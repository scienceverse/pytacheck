"""Uploading folders to Zenodo (port of ``R/archive-zenodo-upload.R``).

Everything here WRITES to a Zenodo account, so the defaults are cautious:
uploads go to the sandbox (sandbox.zenodo.org) unless ``sandbox=False``,
depositions are left as unpublished drafts unless ``publish=True``, and an
interactive session is asked to confirm before anything is sent. Publishing
on the real zenodo.org mints a permanent DOI and cannot be undone.

:func:`zenodo_upload` takes the result of
:func:`~pytacheck.archives.osf.osf_file_download` (or folder paths) and
creates one deposition per folder, with metadata taken from the OSF.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import is_na

if TYPE_CHECKING:
    import httpx

__all__ = ["ZenodoMetadata", "zenodo_pat", "zenodo_upload"]

_OSF_META_DIR = "_osf_metadata"
_TRANSIENT = (429, 500, 502, 503, 504)

#: OSF licence names (lowercased, punctuation removed) -> Zenodo licence ids
_LICENSE_MAP = {
    "ccbyattribution40international": "cc-by-4.0",
    "ccbyattributionnoncommercial40international": "cc-by-nc-4.0",
    "ccbyattributionnoderivatives40international": "cc-by-nd-4.0",
    "ccbyattributionnoncommercialsharealike40international": "cc-by-nc-sa-4.0",
    "cc010universal": "cc0-1.0",
    "mitlicense": "mit",
    "bsd2clausesimplifiedlicense": "bsd-2-clause",
    "bsd3clausenewrevisedlicense": "bsd-3-clause",
    "apachelicense20": "apache-2.0",
    "artisticlicense20": "artistic-2.0",
    "academicfreelicenseafl30": "afl-3.0",
    "eclipsepubliclicense10": "epl-1.0",
    "mozillapubliclicense20": "mpl-2.0",
    "gnugeneralpubliclicensegpl20": "gpl-2.0-or-later",
    "gnugeneralpubliclicensegpl30": "gpl-3.0-or-later",
    "gnulessergeneralpubliclicenselgpl21": "lgpl-2.1-or-later",
    "gnulessergeneralpubliclicenselgpl30": "lgpl-3.0-or-later",
}


class ZenodoMetadata(dict):  # type: ignore[type-arg]
    """A deposition's metadata (a dict), remembering whether the licence was assumed.

    ``license_was_default`` is R's ``attr(md, "license_was_default")``: ``True``
    when the OSF project had no licence Zenodo recognises and the ``license``
    argument was used instead.
    """

    license_was_default: bool = False


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _pb_say(pb: Any, text: str) -> None:
    """Port of R/archive-zenodo-upload.R::.pb_say(): a padded progress-bar line.

    Truncated to the console width (less 12) with ``"..."`` and padded with
    spaces, so a shorter message fully overwrites a longer one.
    """
    from pytacheck.archives import _tick
    from pytacheck.utils import get_option

    width = max(int(get_option("width", 80)) - 12, 40)
    if len(text) > width:
        text = text[: width - 1] + "..."
    _tick(pb, text.ljust(width))


def _zenodo_api(sandbox: bool = True) -> str:
    """Port of R/archive-zenodo-upload.R::.zenodo_api(): the API base URL."""
    return "https://sandbox.zenodo.org/api" if sandbox is True else "https://zenodo.org/api"


def zenodo_pat(pat: Any = None, sandbox: bool = True) -> Any:
    """Port of R/archive-zenodo-upload.R::zenodo_pat(): set or get the Zenodo token.

    The sandbox and the real Zenodo are separate services with separate
    accounts and tokens, kept apart here: with no *pat*, returns the token
    for the chosen server (the ``metacheck.zenodo.pat.sandbox`` /
    ``metacheck.zenodo.pat`` option, else the ``ZENODO_SANDBOX_PAT`` /
    ``ZENODO_PAT`` environment variable; ``""`` when unset). With a string,
    sets it for the session. Create a token (scopes ``deposit:write`` and
    ``deposit:actions``) under Applications > Personal access tokens.
    """
    from pytacheck.utils import get_option, options

    opt = "metacheck.zenodo.pat.sandbox" if sandbox is True else "metacheck.zenodo.pat"
    env = "ZENODO_SANDBOX_PAT" if sandbox is True else "ZENODO_PAT"
    if pat is None:
        value = get_option(opt)
        return os.environ.get(env, "") if value is None else value
    if not isinstance(pat, str):
        # R accepts any character vector of length one
        from pytacheck.archives.github import _as_list, _is_vector

        values = _as_list(pat) if _is_vector(pat) else []
        if len(values) == 1 and isinstance(values[0], str):
            pat = values[0]
    if not isinstance(pat, str):
        raise ValueError("Set zenodo_pat with a single string containing your Zenodo token")
    options({opt: pat})
    return pat


_get_set_pat = zenodo_pat  # zenodo_upload()'s `zenodo_pat` argument shadows the function


def _zenodo_auth(token: str) -> dict[str, Any]:
    """Port of R/archive-zenodo-upload.R::.zenodo_auth(): authenticated request options.

    Headers (``User-Agent``, ``Authorization: Bearer``) and the retry policy
    (3 tries on 429/500/502/503/504 and connection failures), as keyword
    arguments for :func:`pytacheck.http.request`. HTTP error statuses are
    returned, not raised.
    """
    return {
        "headers": {"User-Agent": "metacheck", "Authorization": f"Bearer {token}"},
        "max_tries": 3,
        "retry_statuses": _TRANSIENT,
    }


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """``req_perform()``: the response, or an error after a connection failure."""
    from pytacheck import http

    resp = http.request(method, url, **kwargs)
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: {method} {url}")
    return resp


def _zenodo_check_token(api: str, token: str, sandbox: bool = True) -> bool:
    """Port of R/archive-zenodo-upload.R::.zenodo_check_token(): is the token accepted?

    One cheap authenticated request before anything is uploaded (Zenodo
    answers a bad token with HTTP 500, which would otherwise be retried for
    minutes). Raises with an explanation when the token is refused.
    """
    try:
        resp = _request(
            "GET",
            f"{api}/deposit/depositions?size=1",
            headers={"User-Agent": "metacheck", "Authorization": f"Bearer {token}"},
            max_tries=1,
        )
    except Exception as e:
        raise RuntimeError(f"Could not reach {api}: {e}") from e
    status = resp.status_code
    if status < 400:
        return True
    raise RuntimeError(
        f"Zenodo did not accept the token (HTTP {status}). Check that it is a "
        f"{'sandbox.zenodo.org' if sandbox is True else 'zenodo.org'} token with the "
        f"deposit:write scope; a {'zenodo.org' if sandbox is True else 'sandbox.zenodo.org'} "
        "token will not work here. See ?zenodo_pat"
    )


def _zenodo_check_resp(resp: httpx.Response, what: str) -> Any:
    """Port of R/archive-zenodo-upload.R::.zenodo_check_resp(): parse, or explain a failure.

    Returns the parsed body for a success status; otherwise raises with
    Zenodo's reason, including its per-field ``errors``.
    """
    from pytacheck._r import as_character
    from pytacheck.archives.github import _body_json
    from pytacheck.archives.github import _dollar as r_dollar

    status = resp.status_code
    try:
        body = _body_json(resp)
    except Exception:
        body = None
    if status < 400:
        return body

    message = r_dollar(body, "message")
    detail = _resp_status_desc(resp) if message is None else as_character(message)
    fields = ""
    errors = r_dollar(body, "errors")
    if errors:
        parts = []
        for e in errors:
            if not isinstance(e, Mapping):
                raise TypeError("$ operator is invalid for atomic vectors")
            field = r_dollar(e, "field")
            msg = r_dollar(e, "message")
            parts.append(
                f"{'?' if field is None else as_character(field)}: "
                f"{'invalid' if msg is None else as_character(msg)}"
            )
        fields = f" ({'; '.join(parts)})"
    if status in (401, 403):
        raise RuntimeError(
            f"{what} failed: Zenodo rejected the token (HTTP {status}). Check the token has "
            "the deposit:write scope, and that a sandbox token is used for the sandbox. "
            "See ?zenodo_pat"
        )
    raise RuntimeError(f"{what} failed (HTTP {status}): {detail}{fields}")


#: httr2's status descriptions where they differ from Python's (None: R's NA)
_HTTR2_STATUS_DESC = {
    300: "Multiple Choice",
    413: "Payload Too Large",
    414: "URI Too Long",
    416: "Range Not Satisfiable",
    431: None,
}


def _resp_status_desc(resp: httpx.Response) -> str:
    """``httr2::resp_status_desc()`` as ``sprintf("%s")`` shows it (``"NA"`` when unknown).

    From the status code alone (a static table, as httr2 does), never from
    the reason phrase the server sent.
    """
    import httpx

    code = resp.status_code
    if code in _HTTR2_STATUS_DESC:
        desc = _HTTR2_STATUS_DESC[code]
    else:
        desc = httpx.codes.get_reason_phrase(code)
    return desc or "NA"


def _zenodo_license_id(osf_license: Any) -> str | None:
    """Port of R/archive-zenodo-upload.R::.zenodo_license_id(): OSF licence -> Zenodo id.

    Covers the licences the OSF offers (matched ignoring case, spacing and
    punctuation); ``None`` for anything else, including "No license" and
    "Other", so the caller's default licence is used.
    """
    from pytacheck._r import gsub

    if osf_license is None or (not isinstance(osf_license, str) and is_na(osf_license)):
        return None
    if not isinstance(osf_license, str):
        values = list(osf_license)
        if not values:
            return None
        if len(values) > 1:
            # R: `is.na(osf_license) || ...` on a longer vector
            raise ValueError(f"'length = {len(values)}' in coercion to 'logical(1)'")
        osf_license = values[0]
    if is_na(osf_license) or osf_license == "":
        return None
    key = gsub("[^a-z0-9]", "", str(osf_license).lower())
    return _LICENSE_MAP.get(key)


def _osf_zenodo_metadata(osf_id: Any, pb: Any = None) -> dict[str, dict[str, Any]]:  # noqa: ARG001
    """Port of R/archive-zenodo-upload.R::.osf_zenodo_metadata(): OSF metadata for Zenodo.

    One request per project (``?embed=license&embed=bibliographic_contributors``)
    for the title, description, tags, licence name and contributors (``"Family,
    Given"`` plus ORCID). Returns a dict keyed by OSF id; projects that could
    not be read are left out.
    """
    from pytacheck import http
    from pytacheck.archives.github import _as_list
    from pytacheck.archives.osf_helpers import _osf_headers
    from pytacheck.utils import get_option

    ids = [str(i) for i in dict.fromkeys(_as_list(osf_id)) if i is not None]
    if not ids:
        return {}
    osf_api = get_option("metacheck.osf.api")
    urls = [
        f"{osf_api}/nodes/{i}/?embed=license&embed=bibliographic_contributors&page[size]=100"
        for i in ids
    ]
    resps = http.batch_query(urls, msg="OSF Metadata", req_func=_osf_headers)
    out: dict[str, dict[str, Any]] = {}
    for oid, resp in zip(ids, resps, strict=True):
        if resp is None or resp.status_code != 200:
            continue
        meta = _osf_node_for_zenodo(oid, resp)
        if meta is not None:
            out[oid] = meta
    return out


def _dig(x: Any, *path: str) -> Any:
    from pytacheck.archives.github import _dollar as r_dollar

    for key in path:
        x = r_dollar(x, key)
        if x is None:
            return None
    return x


def _osf_node_for_zenodo(osf_id: str, resp: httpx.Response) -> dict[str, Any] | None:
    """One project's metadata from its (embedded) node response."""
    from pytacheck.archives.github import _body_json

    try:
        content = _body_json(resp)
    except Exception:
        return None
    if content is None:
        return None
    att = _dig(content, "data", "attributes") or {}
    contributors = _dig(content, "data", "embeds", "bibliographic_contributors", "data")
    creators: list[dict[str, Any]] = []
    if isinstance(contributors, list) and contributors:
        # R reads this with simplifyVector = TRUE: the users become a data
        # frame, one row per contributor. A field no user has is NULL (so
        # `%||% ""` applies); a field only some users have is NA for the rest
        # (and nzchar(NA) is TRUE).
        users = [_dig(c, "embeds", "users", "data", "attributes") for c in contributors]
        users = [u if isinstance(u, Mapping) else {} for u in users]

        def column(*path: str) -> list[Any] | None:
            present = [_has_path(u, path) for u in users]
            if not any(present):
                return None
            return [
                _na_chr(_dig(u, *path)) if p else None for u, p in zip(users, present, strict=True)
            ]

        full = column("full_name")
        if full:
            family_col = column("family_name")
            given_col = column("given_name")
            orcid_col = column("social", "orcid")
            for j, full_name in enumerate(full):
                family = "" if family_col is None else family_col[j]
                given = "" if given_col is None else given_col[j]
                if (family is None or family != "") and (given is None or given != ""):
                    name: Any = f"{_na(family)}, {_na(given)}"
                else:
                    name = full_name
                cr: dict[str, Any] = {"name": name}
                orcid = None if orcid_col is None else orcid_col[j]
                if orcid is not None and orcid != "":
                    cr["orcid"] = orcid
                creators.append(cr)
    tags = att.get("tags") if isinstance(att, Mapping) else None
    return {
        "osf_id": osf_id,
        "title": _na_chr(_dig(att, "title")),
        "description": _na_chr(_dig(att, "description")),
        "tags": list(tags) if isinstance(tags, list) else [],
        "license": _na_chr(
            _dig(content, "data", "embeds", "license", "data", "attributes", "name")
        ),
        "creators": creators,
        "date_created": _na_chr(_dig(att, "date_created")),
    }


def _has_path(x: Any, path: tuple[str, ...]) -> bool:
    for key in path:
        if not isinstance(x, Mapping) or key not in x:
            return False
        x = x[key]
    return True


def _na_chr(x: Any) -> Any:
    return None if x is None or is_na(x) else x


def _na(x: Any) -> str:
    return "NA" if x is None or is_na(x) else str(x)


def _zenodo_classify(files: list[str]) -> list[str]:
    """Port of R/archive-zenodo-upload.R::.zenodo_classify(): the check modules' file categories.

    ``data``, ``code``, ``materials``, ``documentation``, ``output`` or
    ``unknown``, from names and paths only (via
    :func:`~pytacheck.datacheck.files.data_classify_files`).
    """
    from pytacheck.archives.github import _r_basename
    from pytacheck.datacheck.files import data_classify_files

    if not files:
        return []
    return data_classify_files([_r_basename(f) for f in files], file_path=list(files))


def _zenodo_regex_escape(x: Any) -> Any:
    """Port of R/archive-zenodo-upload.R::.zenodo_regex_escape(): escape regex metacharacters."""
    from pytacheck._r import gsub

    return gsub(r"([.\\|()\[\]{}^$*+?])", r"\\\1", x, perl=True)


def _duplicated(values: list[Any]) -> list[bool]:
    seen: set[Any] = set()
    out = []
    for v in values:
        out.append(v in seen)
        seen.add(v)
    return out


def _zenodo_flat_names(files: list[str], folder: str) -> list[str]:
    """Port of R/archive-zenodo-upload.R::.zenodo_flat_names(): the name each file gets on Zenodo.

    Zenodo has no folders, so names must be flat. A file keeps its bare name
    unless another file shares it; those take as many parent folders
    (joined with ``__``) as it takes to be unique. Anything still clashing
    gets its position appended.
    """
    from pytacheck._r import gsub, strsplit, sub
    from pytacheck.archives.github import _r_basename

    if not files:
        return []
    rel = sub(f"^{_zenodo_regex_escape(folder)}[/\\\\]*", "", list(files))
    rel = gsub(r"\\", "/", rel)
    base = [_r_basename(r) for r in rel]
    out = list(base)

    dup_names = [nm for nm, d in zip(base, _duplicated(base), strict=True) if d]
    for nm in dict.fromkeys(dup_names):
        idx = [i for i, b in enumerate(base) if b == nm]
        parts = [strsplit(rel[i], "/", fixed=True) for i in idx]
        cand: list[str] = []
        for depth in range(1, max(len(p) for p in parts) + 1):
            cand = ["__".join(p[-(depth + 1) :]) for p in parts]
            if not any(_duplicated(cand)):
                break
        for i, c in zip(idx, cand, strict=True):
            out[i] = c

    dup = _duplicated(out)
    for i, d in enumerate(dup):
        if not d:
            continue
        ext = sub(r".*(\.[^.]+)$", r"\1", out[i])
        stem = sub(r"\.[^.]+$", "", out[i])
        out[i] = f"{stem}_{i + 1}{'' if ext == out[i] else ext}"
    return out


def _zenodo_meta_from_folder(folder: str) -> dict[str, Any] | None:
    """Port of R/archive-zenodo-upload.R::.zenodo_meta_from_folder(): saved OSF metadata.

    Reads ``_osf_metadata/metadata.json`` (written by
    ``osf_file_download(metadata=True)``) into the shape
    :func:`_osf_zenodo_metadata` returns, so a folder uploads with its real
    title, licence and authors without asking the OSF again. ``None`` when
    there is no such file (or it has no ``osf_id``).
    """
    from pytacheck.archives.github import _as_list

    path = os.path.join(folder, _OSF_META_DIR, "metadata.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(m, Mapping) or m.get("osf_id") is None:
        return None

    from pytacheck.archives.github import _dollar as r_dollar

    creators = []
    for c in r_dollar(m, "contributors") or []:
        family = r_dollar(c, "family_name")
        given = r_dollar(c, "given_name")
        family = "" if family is None else family
        given = "" if given is None else given
        if family != "" and given != "":
            nm = f"{family}, {given}"
        else:
            nm = r_dollar(c, "name")
            nm = "" if nm is None else nm
        entry: dict[str, Any] = {"name": nm}
        orcid = r_dollar(c, "orcid")
        if orcid is not None and not is_na(orcid) and orcid != "":
            entry["orcid"] = orcid
        creators.append(entry)
    creators = [c for c in creators if c["name"] != ""]

    tags = [t for t in _as_list(r_dollar(m, "tags")) if t is not None]
    return {
        "osf_id": r_dollar(m, "osf_id"),
        "title": r_dollar(m, "title"),
        "description": r_dollar(m, "description"),
        "tags": tags,
        "license": r_dollar(m, "license"),
        "creators": creators,
    }


def _zenodo_build_metadata(
    meta: Mapping[str, Any] | None,
    folder: str,
    license: str = "cc-by-4.0",
    upload_type: str = "dataset",
) -> ZenodoMetadata:
    """Port of R/archive-zenodo-upload.R::.zenodo_build_metadata(): a deposition's metadata.

    Title and description fall back to the folder name and a sentence naming
    where the files came from (Zenodo requires both); creators to
    ``"Unknown"``; the OSF licence is mapped to a Zenodo id, else *license*
    is used (flagged by ``license_was_default``). OSF projects get keywords
    from their tags and an ``isIdenticalTo`` link back to the project.
    """
    from pytacheck._r import as_character
    from pytacheck.archives.github import _as_list, _r_basename
    from pytacheck.archives.github import _dollar as r_dollar

    title = r_dollar(meta, "title")
    if title is None or is_na(title) or title == "":
        title = _r_basename(folder)

    osf_id = r_dollar(meta, "osf_id")
    description = r_dollar(meta, "description")
    if description is None or is_na(description) or description == "":
        if osf_id is not None:
            description = (
                f"Files archived from the OSF project https://osf.io/{_na(as_character(osf_id))}/"
            )
        else:
            description = f"Files archived from {_r_basename(folder)}"

    creators = r_dollar(meta, "creators")
    if creators is None or len(creators) == 0:
        creators = [{"name": "Unknown"}]

    osf_license = _zenodo_license_id(r_dollar(meta, "license"))
    md = ZenodoMetadata(
        title=title,
        upload_type=upload_type,
        description=description,
        creators=creators,
        license=license if osf_license is None else osf_license,
    )
    tags = [t for t in _as_list(r_dollar(meta, "tags")) if t is not None]
    if tags:
        md["keywords"] = tags
    if osf_id is not None:
        md["related_identifiers"] = [
            {
                "identifier": f"https://osf.io/{_na(as_character(osf_id))}/",
                "relation": "isIdenticalTo",
                "scheme": "url",
            }
        ]
    md.license_was_default = osf_license is None
    return md


# ---------------------------------------------------------------------------
# the upload
# ---------------------------------------------------------------------------


def _list_files(path: str) -> list[str]:
    """``list.files(path, recursive = TRUE, full.names = TRUE)`` without directories.

    Hidden files and folders are skipped; names are sorted as R sorts them,
    and joined to *path* with ``/`` as R does (``dir/`` gives ``dir//file``).
    """
    return [f"{path}/{rel}" for rel in _list_rel(path)]


def _list_rel(path: str) -> list[str]:
    """``list.files(path, recursive = TRUE)``: relative paths, R-sorted."""
    from pytacheck._r import r_sorted

    found: list[str] = []
    for dirpath, dirs, names in os.walk(os.path.expanduser(path)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = os.path.relpath(dirpath, os.path.expanduser(path))
        found.extend(
            n if rel == "." else f"{rel}/{n}".replace("\\", "/")
            for n in names
            if not n.startswith(".")
        )
    return r_sorted(found)


def _interactive() -> bool:
    """R ``interactive()``: a Python REPL, ``python -i``, or an IPython/Jupyter session."""
    if hasattr(sys, "ps1") or sys.flags.interactive:
        return True
    ipython = sys.modules.get("IPython")
    get_ipython = getattr(ipython, "get_ipython", None)
    return bool(get_ipython is not None and get_ipython() is not None)


def _menu(choices: list[str], title: str) -> int:
    """``utils::menu()``: the number chosen, 0 for none."""
    print(title)
    for i, c in enumerate(choices, start=1):
        print(f"{i}: {c}")
    try:
        answer = input("\nSelection: ").strip()
    except EOFError:
        return 0
    return int(answer) if answer.isdigit() and 1 <= int(answer) <= len(choices) else 0


def _upload_file(url: str, path: str, token: str) -> httpx.Response:
    """PUT a file's bytes to a bucket URL, streamed, with :func:`_zenodo_auth`'s retries."""
    from pytacheck import http

    auth = _zenodo_auth(token)
    resp: httpx.Response | None = None
    for attempt in range(1, auth["max_tries"] + 1):
        with open(path, "rb") as fh:
            resp = http.request("PUT", url, content=fh, headers=auth["headers"], max_tries=1)
        if resp is not None and resp.status_code not in _TRANSIENT:
            return resp
        if attempt < auth["max_tries"]:
            http.sleep(min(2**attempt, 60))
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: PUT {url}")
    return resp


def _upload_form(url: str, path: str, name: str, token: str) -> httpx.Response:
    """POST a file to the deposition's form endpoint (``name`` + ``file`` fields)."""
    from pytacheck import http

    auth = _zenodo_auth(token)
    resp: httpx.Response | None = None
    for attempt in range(1, auth["max_tries"] + 1):
        with open(path, "rb") as fh:
            resp = http.request(
                "POST",
                url,
                data={"name": name},
                files={"file": (os.path.basename(path), fh)},
                headers=auth["headers"],
                max_tries=1,
            )
        if resp is not None and resp.status_code not in _TRANSIENT:
            return resp
        if attempt < auth["max_tries"]:
            http.sleep(min(2**attempt, 60))
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: POST {url}")
    return resp


def _json_body(x: Any) -> bytes:
    """``httr2::req_body_json()``'s body (jsonlite, ``auto_unbox = TRUE``)."""
    return json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _zip_name(zip_dir: str, stem: str, suffix: str) -> str:
    return os.path.join(zip_dir, f"{stem}{suffix}.zip")


def _make_zip(zip_path: str, parent: str, rels: list[str]) -> str | None:
    """``utils::zip(zip_path, rels)`` run from *parent*: the archive, or ``None``."""
    import zipfile

    with contextlib.suppress(OSError):
        os.remove(zip_path)
    if not rels:
        return None
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel in rels:
                zf.write(os.path.join(parent, rel), arcname=rel)
    except (OSError, zipfile.BadZipFile):
        return None
    if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
        return zip_path
    return None


def _result_row(
    folder: str,
    osf_id: Any,
    deposition_id: Any,
    doi: Any,
    url: Any,
    uploaded: int,
    skipped: int,
    published: bool,
    error: Any,
) -> pd.DataFrame:
    def chr1(v: Any) -> pd.Series:
        return pd.Series([None if v is None or is_na(v) else str(v)], dtype="string")

    return pd.DataFrame(
        {
            "folder": chr1(folder),
            "osf_project": chr1(osf_id),
            "deposition_id": chr1(deposition_id),
            "doi": chr1(doi),
            "url": chr1(url),
            "files_uploaded": pd.Series([uploaded], dtype="Int64"),
            "files_skipped": pd.Series([skipped], dtype="Int64"),
            "published": pd.Series([published], dtype="boolean"),
            "error": chr1(error),
        }
    )


def zenodo_upload(
    folders: Any,
    sandbox: bool = True,
    zenodo_pat: str | None = None,
    publish: bool = False,
    license: str = "cc-by-4.0",
    upload_type: str = "dataset",
    metadata: Mapping[str, Any] | None = None,
    as_zip: bool = True,
    split_materials: Any = "materials",
    upload_osf_metadata: bool = True,
    max_file_size: float | None = None,
    ask: bool = True,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-zenodo-upload.R::zenodo_upload(): upload folders to Zenodo.

    Creates one deposition per folder, uploads its files and attaches
    metadata (title, description, keywords, licence and creators from the
    OSF project, read from the folder's ``_osf_metadata/metadata.json`` when
    present, else fetched). *folders* is the table
    :func:`~pytacheck.archives.osf.osf_file_download` returns (one row per
    file, with ``download_path`` and ``osf_project``) or folder paths.

    Safety: *sandbox* (default) uploads to sandbox.zenodo.org, whose DOIs are
    not real; depositions stay drafts unless *publish*; an interactive
    session confirms first when *ask*. With *as_zip* (default) each folder
    is uploaded as a ZIP keeping its structure, with the file categories in
    *split_materials* (as the check modules classify them) in separate
    archives; otherwise files are uploaded one by one under flat names.
    *metadata* overrides fields of every deposition; *max_file_size* (MB)
    skips larger files; *upload_osf_metadata* controls whether the
    ``_osf_metadata`` folder itself is uploaded.

    Returns one row per folder (``folder``, ``osf_project``,
    ``deposition_id``, ``doi``, ``url``, ``files_uploaded``,
    ``files_skipped``, ``published``, ``error``), or ``None`` when there was
    nothing to upload or the upload was cancelled.
    """
    from pytacheck._r import as_character, bind_rows, format_num, grepl, plural
    from pytacheck.archives import _message, _spinner
    from pytacheck.archives.github import _as_list, _r_basename
    from pytacheck.archives.osf import _r_dirname
    from pytacheck.llm.cap_prompt import _cap_size_str
    from pytacheck.log import logger
    from pytacheck.utils import path_sanitize

    # which folders, and which OSF project each came from ----
    osf_ids: list[Any] | None = None
    if isinstance(folders, pd.DataFrame):
        if "download_path" not in folders.columns:
            raise ValueError(
                "`folders` is a data frame without a `download_path` column. "
                "Pass the result of osf_file_download(), or a vector of folder paths."
            )
        dl_paths = folders["download_path"].tolist()
        paths = [str(p) for p in dict.fromkeys(p for p in dl_paths if not is_na(p))]
        if "osf_project" in folders.columns:
            first: dict[str, Any] = {}
            for p, o in zip(dl_paths, folders["osf_project"].tolist(), strict=True):
                if not is_na(p) and str(p) not in first:
                    first[str(p)] = None if is_na(o) else o
            osf_ids = [first.get(p) for p in paths]
    else:
        paths = [as_character(p) for p in _as_list(folders)]

    keep_idx = [i for i, p in enumerate(paths) if p is not None]
    paths = [paths[i] for i in keep_idx]
    if osf_ids is not None:
        osf_ids = [osf_ids[i] for i in keep_idx]
    exists = [os.path.isdir(os.path.expanduser(p)) for p in paths]
    if not all(exists):
        missing = [p for p, e in zip(paths, exists, strict=True) if not e]
        warnings.warn(
            f"{len(missing)} folder{plural(len(missing))} could not be found and will be "
            f"skipped: {', '.join(missing[:3])}",
            stacklevel=2,
        )
        if osf_ids is not None:
            osf_ids = [o for o, e in zip(osf_ids, exists, strict=True) if e]
        paths = [p for p, e in zip(paths, exists, strict=True) if e]
    if not paths:
        _message("No folders to upload")
        return None

    # token ----
    if zenodo_pat is not None:
        _get_set_pat(zenodo_pat, sandbox=sandbox)
    token = _get_set_pat(sandbox=sandbox)
    if not token:
        raise ValueError(
            f"No token found for {'the Zenodo sandbox' if sandbox is True else 'Zenodo'}. "
            f'Set one with zenodo_pat("your-token", sandbox = '
            f"{'TRUE' if sandbox is True else 'FALSE'}), or put "
            f"{'ZENODO_SANDBOX_PAT' if sandbox is True else 'ZENODO_PAT'} in your .Renviron. "
            "See ?zenodo_pat for how to create one."
        )

    with _spinner(pb, "Zenodo Upload") as bar:
        # the files in each folder ----
        mb = 1024 * 1024
        meta_rx = f"(^|[/\\\\]){_OSF_META_DIR}([/\\\\]|$)"
        file_lists: list[list[str]] = []
        for p in paths:
            f = _list_files(p)
            if upload_osf_metadata is not True and f:
                f = [x for x, hit in zip(f, grepl(meta_rx, f), strict=True) if not hit]
            file_lists.append(f)
        sizes = [[float(os.path.getsize(x)) for x in f] for f in file_lists]

        skipped = [0] * len(paths)
        if max_file_size is not None and not is_na(max_file_size) and max_file_size > 0:
            import math

            if math.isfinite(max_file_size):
                for i in range(len(file_lists)):
                    too_big = [j for j, s in enumerate(sizes[i]) if s > max_file_size * mb]
                    if too_big:
                        largest = max(too_big, key=lambda j: (sizes[i][j], -j))
                        _message(
                            f"{len(too_big)} file{plural(len(too_big))} in "
                            f"{_r_basename(paths[i])} exceed the {format_num(max_file_size)} MB "
                            "limit and will not be uploaded (largest: "
                            f"{_r_basename(file_lists[i][largest])})"
                        )
                        drop = set(too_big)
                        file_lists[i] = [x for j, x in enumerate(file_lists[i]) if j not in drop]
                        sizes[i] = [x for j, x in enumerate(sizes[i]) if j not in drop]
                        skipped[i] = len(too_big)

        n_files = [len(f) for f in file_lists]
        total_size = sum(s for group in sizes for s in group)
        if sum(n_files) == 0:
            _message(f"No files to upload in {len(paths)} folder{plural(len(paths))}")
            return None

        # check the token before asking or sending anything ----
        api = _zenodo_api(sandbox)
        _zenodo_check_token(api, token, sandbox=sandbox)

        # confirm ----
        server = (
            "the Zenodo SANDBOX (sandbox.zenodo.org)"
            if sandbox is True
            else "the REAL Zenodo (zenodo.org)"
        )
        split_list = [] if split_materials is None or split_materials is False else split_materials
        split_list = _as_list(split_list)
        if as_zip is True:
            if split_list:
                how = (
                    "\nFiles will be packed into ZIP archives, keeping their folders, with "
                    f"{' and '.join(str(s) for s in split_list)} in separate archives."
                )
            else:
                how = (
                    "\nFiles will be packed into one ZIP archive per folder, keeping their folders."
                )
        else:
            how = "\nFiles will be uploaded individually; their folder structure will be lost."
        total_n = sum(n_files)
        _message(
            f"\nAbout to upload {len(paths)} folder{plural(len(paths))} ({total_n} "
            f"file{plural(total_n)}, {_cap_size_str(total_size)}) to {server}.{how}\n"
            "Depositions will be "
            f"{'PUBLISHED immediately' if publish is True else 'left as unpublished drafts'}."
        )
        if publish is True and sandbox is not True:
            _message(
                "Publishing on the real Zenodo cannot be undone: each record and its DOI "
                "become permanent and cannot be deleted.\n"
            )
        if ask is True and _interactive():
            choice = _menu(["Yes, upload", "No, cancel"], title="\nContinue?")
            if choice != 1:
                _message("Cancelled; nothing was uploaded")
                return None

        # metadata for each folder ----
        meta_by_id: dict[str, dict[str, Any]] = {}
        if osf_ids is not None and any(o is not None for o in osf_ids):
            meta_by_id = _osf_zenodo_metadata(osf_ids, pb=bar)
        meta_by_folder = {p: _zenodo_meta_from_folder(p) for p in paths}
        n_local = sum(1 for v in meta_by_folder.values() if v is not None)
        if n_local > 0:
            _message(f"Read the OSF metadata already saved in {n_local} folder{plural(n_local)}.")

        if osf_ids is not None:
            no_meta = [
                str(o)
                for o in dict.fromkeys(o for o in osf_ids if o is not None)
                if str(o) not in meta_by_id
            ]
            if no_meta:
                k = len(no_meta)
                warnings.warn(
                    f"No OSF metadata could be retrieved for {k} project{plural(k)} "
                    f"({', '.join(no_meta[:5])}), so the deposition{plural(k)} will be titled "
                    'after the folder with "Unknown" as the creator. Check and correct '
                    f"{'it' if k == 1 else 'them'} before publishing.",
                    stacklevel=2,
                )

        results: list[pd.DataFrame] = []
        default_license_used: list[str] = []
        auth = _zenodo_auth(token)

        for i, folder in enumerate(paths):
            _pb_say(bar, f"Uploading {_r_basename(folder)} ({i + 1} of {len(paths)})")
            osf_id = osf_ids[i] if osf_ids is not None else None
            meta = meta_by_folder.get(folder)
            if meta is None and osf_id is not None:
                meta = meta_by_id.get(str(osf_id))
            md = _zenodo_build_metadata(meta, folder, license=license, upload_type=upload_type)
            if md.license_was_default:
                default_license_used.append(_r_basename(folder))
            if metadata is not None:
                md.update(metadata)

            # an empty deposition ("{}" -- an empty JSON array gets HTTP 500) ----
            try:
                resp = _request(
                    "POST",
                    f"{api}/deposit/depositions",
                    content=b"{}",
                    headers={**auth["headers"], "Content-Type": "application/json"},
                    max_tries=auth["max_tries"],
                    retry_statuses=auth["retry_statuses"],
                )
                dep = _zenodo_check_resp(resp, f"Creating a deposition for {_r_basename(folder)}")
            except Exception as e:
                warnings.warn(str(e), stacklevel=2)
                results.append(
                    _result_row(folder, osf_id, None, None, None, 0, skipped[i], False, str(e))
                )
                continue

            from pytacheck.archives.github import _dollar as r_dollar

            dep_id = r_dollar(dep, "id")
            bucket = _dig(dep, "links", "bucket")

            if as_zip is True:
                stem = path_sanitize(_r_basename(folder), keep_sep=False)
                root = _r_basename(folder)
                parent = _r_dirname(folder)
                rels = [f"{root}/{r}" for r in _list_rel(folder)]
                abs_paths = [f"{parent}/{r}" for r in rels]
                cats = _zenodo_classify(abs_paths) if split_list else [None] * len(rels)
                is_mat = [c is not None and c in split_list for c in cats]
                zip_dir = os.path.join(tempfile.gettempdir(), f"metacheck_zip_{i + 1}")
                os.makedirs(zip_dir, exist_ok=True)

                built: list[str] = []
                if any(is_mat) and not all(is_mat):
                    main = _make_zip(
                        _zip_name(zip_dir, stem, ""),
                        parent,
                        [r for r, m in zip(rels, is_mat, strict=True) if not m],
                    )
                    if main is not None:
                        built.append(main)
                    parts_msg: list[str] = []
                    present = list(dict.fromkeys(c for c, m in zip(cats, is_mat, strict=True) if m))
                    for ct in [s for s in dict.fromkeys(split_list) if s in present]:
                        zf = _make_zip(
                            _zip_name(zip_dir, stem, f"_{ct}"),
                            parent,
                            [r for r, c in zip(rels, cats, strict=True) if c == ct],
                        )
                        if zf is not None:
                            built.append(zf)
                            n_ct = sum(1 for c in cats if c == ct)
                            parts_msg.append(
                                f"{os.path.basename(zf)} ({n_ct} {ct} file{plural(n_ct)})"
                            )
                    n_main = sum(1 for m in is_mat if not m)
                    _message(
                        f"{_r_basename(folder)}: split into "
                        f"{os.path.basename(built[0]) if built else 'NA'} ({n_main} "
                        f"file{plural(n_main)}) and {', '.join(parts_msg)}. Unzipping all of "
                        "them rebuilds the original folders; unzipping only the first gives "
                        "everything except those categories."
                    )
                else:
                    main = _make_zip(_zip_name(zip_dir, stem, ""), parent, rels)
                    if main is not None:
                        built.append(main)

                if built:
                    file_lists[i] = built
                else:
                    _message(
                        f"Could not build a zip for {_r_basename(folder)}; its files are "
                        "uploaded individually."
                    )

            # upload each file ----
            uploaded = 0
            upload_err: str | None = None
            flat_names = _zenodo_flat_names(file_lists[i], folder)
            n_up = len(file_lists[i])
            for fp, rel in zip(file_lists[i], flat_names, strict=True):
                try:
                    if bucket is not None:
                        from urllib.parse import quote

                        resp = _upload_file(f"{bucket}/{quote(rel, safe='')}", fp, token)
                    else:
                        resp = _upload_form(
                            f"{api}/deposit/depositions/{_na(as_character(dep_id))}/files",
                            fp,
                            rel,
                            token,
                        )
                    _zenodo_check_resp(resp, f"Uploading {rel}")
                    uploaded += 1
                except Exception as e:
                    logger("zenodo_upload", {"error": str(e), "file": fp})
                    upload_err = str(e)
                _pb_say(bar, f"{_r_basename(folder)}: uploaded {uploaded} of {n_up} files")

            if uploaded < n_up:
                warnings.warn(
                    f"{n_up - uploaded} of {n_up} file{plural(n_up)} in {_r_basename(folder)} "
                    f"failed to upload (e.g. {_na(upload_err)})",
                    stacklevel=2,
                )

            # attach metadata ----
            dep_path = f"{api}/deposit/depositions/{_na(as_character(dep_id))}"
            try:
                resp = _request(
                    "PUT",
                    dep_path,
                    content=_json_body({"metadata": dict(md)}),
                    headers={**auth["headers"], "Content-Type": "application/json"},
                    max_tries=auth["max_tries"],
                    retry_statuses=auth["retry_statuses"],
                )
                _zenodo_check_resp(resp, f"Adding metadata to {_r_basename(folder)}")
                meta_ok = True
            except Exception as e:
                warnings.warn(str(e), stacklevel=2)
                meta_ok = False

            # publish, only when asked ----
            doi = _dig(dep, "metadata", "prereserve_doi", "doi")
            published = False
            if publish is True and meta_ok:
                try:
                    resp = _request(
                        "POST",
                        f"{dep_path}/actions/publish",
                        headers=auth["headers"],
                        max_tries=auth["max_tries"],
                        retry_statuses=auth["retry_statuses"],
                    )
                    pub = _zenodo_check_resp(resp, f"Publishing {_r_basename(folder)}")
                    published = True
                    new_doi = r_dollar(pub, "doi")
                    if new_doi is not None:
                        doi = new_doi
                except Exception as e:
                    warnings.warn(str(e), stacklevel=2)

            results.append(
                _result_row(
                    folder,
                    osf_id,
                    as_character(dep_id) if dep_id is not None else None,
                    doi,
                    _dig(dep, "links", "html"),
                    uploaded,
                    skipped[i],
                    published,
                    None,
                )
            )

        out = bind_rows(results)

        if default_license_used:
            n_lic = len(default_license_used)
            shown = default_license_used[:5]
            quoted = ", ".join(f'"{s}"' for s in shown)
            if n_lic == 1:
                opening = (
                    f"The folder {quoted} comes from an OSF project without specified license "
                    "information."
                )
            else:
                opening = (
                    f"{n_lic} folders come from OSF projects without specified license "
                    f"information: {quoted}{', and others' if n_lic > len(shown) else ''}."
                )
            warnings.warn(
                f"{opening} The license `{license}` was added to Zenodo. You can change it on "
                "Zenodo if needed, or choose a different one when uploading:\n"
                '    zenodo_upload(result, license = "cc0-1.0")   # public domain\n'
                '    zenodo_upload(result, license = "cc-by-4.0")  # attribution\n'
                "Other identifiers are listed at https://zenodo.org/api/vocabularies/licenses",
                stacklevel=2,
            )

        dep_ids = out["deposition_id"].tolist()
        n_ok = sum(1 for d in dep_ids if not is_na(d))
        n_uploaded = int(sum(int(v) for v in out["files_uploaded"].tolist()))
        _message(
            f"\nCreated {n_ok} deposition{plural(n_ok)} on "
            f"{'the Zenodo sandbox' if sandbox is True else 'Zenodo'} with {n_uploaded} "
            f"file{plural(n_uploaded)}."
        )
        urls = [
            (u, f)
            for u, f in zip(out["url"].tolist(), out["folder"].tolist(), strict=True)
            if not is_na(u)
        ]
        if urls:
            if publish is True:
                _message("Published. The records are at:")
            else:
                _message(
                    "They are unpublished drafts. Check each one, then press Publish on Zenodo:"
                )
            for u, f in urls:
                _message(f"  {u}  ({_r_basename(str(f))})")
            if _interactive():
                import webbrowser

                for u, _f in urls:
                    webbrowser.open(str(u))

        return out
