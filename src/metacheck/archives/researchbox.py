"""ResearchBox (port of ``R/archive-researchbox.R``).

With an API key (:func:`rbox_token`) :func:`rbox_info`, :func:`rbox_files`,
:func:`rbox_search` and :func:`rbox_file_download` ask ResearchBox's API
(``https://researchbox.org/api_call.php``); without one :func:`rbox_info`
scrapes a box's page for its file names and the "Box public since" / "Box
creators" / ... sections, and :func:`rbox_file_download` asks the site to build
a zip of the box's files (the same POST the page's download button makes). The
zip is unzipped into the repository file cache and every file that arrives is
checked against the checksum ResearchBox lists for it.

The key is only ever sent in the ``Authorization`` header of a request to
ResearchBox. It is not part of an error message, a warning or a cache name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import warnings
from typing import Any

import pandas as pd

from metacheck._values import is_missing
from metacheck.archives._atomic import atomic_write, staged_dir
from metacheck.archives.api_keys import (
    RBOX_ASPREDICTED_LIMIT,
    RBOX_ASPREDICTED_RECORD,
    key_problem,
    key_problem_message,
    key_problem_record,
    key_source,
    rbox_token,
    state_dir,
)

__all__ = [
    "rbox_file_download",
    "rbox_files",
    "rbox_info",
    "rbox_links",
    "rbox_search",
    "rbox_token",
]

_API_URL = "https://researchbox.org/api_call.php"


def _rbox_headers() -> dict[str, str]:
    """Port of R/archive-researchbox.R::.rbox_headers(): browser-like request headers.

    ResearchBox sits behind Cloudflare, which rejects httr2's default
    User-Agent.
    """
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8",
        "User-Agent": " ".join(
            [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "AppleWebKit/537.36 (KHTML, like Gecko)",
                "Chrome/137.0.0.0 Safari/537.36",
                "scienceverse/metacheck",
            ]
        ),
    }


# ---------------------------------------------------------------------------
# links, box ids and passcodes
# ---------------------------------------------------------------------------

_BARE_REGEX = r"(?:https?://)?researchbox\.org/[0-9]+(?:&PEER_REVIEW_passcode=[A-Za-z0-9]+)?/?"


def rbox_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_links(): ResearchBox links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``researchbox.org``, plus bare ``researchbox.org/<id>`` mentions in the
    text. Trailing slashes are stripped, links to a file inside a box
    (``researchbox.org/2257.8``, ``researchbox.org/2257/72``) are cut back to
    the box, and duplicate rows dropped. A peer-review link keeps its
    passcode, and a plain link to the same box in the same paper takes it, so
    the box reaches ``repo_check`` once.
    """
    from metacheck._r import sub
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import _collect_links, _url_rows

    found_href = _url_rows(paper, r"researchbox\.org")
    other = _scan_links(paper, _BARE_REGEX, ["researchbox.org/"])
    links = _collect_links([found_href, other])
    links["href"] = pd.Series(
        sub(
            r"(researchbox\.org/[0-9]+)(\.[0-9]+)?(/.*)?$",
            r"\1",
            links["href"].tolist(),
            ignore_case=True,
        ),
        dtype="string",
        index=links.index,
    )

    # Within one paper, the plain link takes the peer-review link of the same box
    hrefs = [None if is_missing(h) else str(h) for h in links["href"].tolist()]
    paper_key = (
        [str(p) if not is_missing(p) else "NA" for p in links["paper_id"].tolist()]
        if "paper_id" in links.columns
        else [""] * len(links)
    )
    ids = _rbox_id(hrefs)
    codes = _rbox_code(hrefs)
    keys = [f"{p} {'NA' if i is None else i}" for p, i in zip(paper_key, ids, strict=True)]
    coded: dict[str, str] = {}
    for k, h, c in zip(keys, hrefs, codes, strict=True):
        if c is not None and h is not None:
            coded.setdefault(k, h)
    out = [
        coded[k] if (c is None and k in coded) else h
        for k, h, c in zip(keys, hrefs, codes, strict=True)
    ]
    links["href"] = pd.Series(out, dtype="string", index=links.index)
    return links.drop_duplicates().reset_index(drop=True)


def _rbox_id(rb_url: Any) -> Any:
    """Port of R/archive-researchbox.R::.rbox_id(): box ids from URLs or bare ids.

    A list gives a list (``None`` where there is no id); a single value gives one.
    """
    scalar = rb_url is None or isinstance(rb_url, str) or not hasattr(rb_url, "__iter__")
    values = [rb_url] if scalar else list(rb_url)
    out: list[str | None] = []
    for v in values:
        if is_missing(v):
            out.append(None)
            continue
        text = str(v).strip()
        if re.fullmatch(r"[0-9]+", text):
            out.append(text)
            continue
        m = re.search(r"researchbox\.org/([0-9]+)", text, re.IGNORECASE)
        out.append(m.group(1) if m else None)
    return out[0] if scalar else out


def _rbox_code(rb_url: Any) -> Any:
    """Port of R/archive-researchbox.R::.rbox_code(): the peer-review passcode of URLs.

    A box that is not public yet is shared with a link like
    ``researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS``; the API takes the
    passcode as ``code``. A list gives a list (``None`` where there is none).
    """
    scalar = rb_url is None or isinstance(rb_url, str) or not hasattr(rb_url, "__iter__")
    values = [rb_url] if scalar else list(rb_url)
    out: list[str | None] = []
    for v in values:
        m = (
            None
            if is_missing(v)
            else re.search(r"PEER_REVIEW_passcode=([A-Za-z0-9]+)", str(v), re.IGNORECASE)
        )
        out.append(m.group(1) if m else None)
    return out[0] if scalar else out


def _rbox_use_api(rb_url: Any = None) -> bool:  # noqa: ARG001 - as in R, any link
    """The API is used whenever a key is set; a peer-review link sends its passcode
    along, and the web page is still read when the API refuses."""
    return bool(rbox_token())


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------

#: The longest wait :func:`_rbox_api` sits out; a longer one (an hourly or daily
#: limit) is reported instead, with the wait in the message.
_RBOX_MAX_WAIT = 60

#: ResearchBox blocks a key for 10 days after 10 requests within 10 days for boxes
#: that are not public, sent without the passcode, and answers each as a box that
#: does not exist (HTTP 500, empty body). From this many answers on, boxes without
#: a passcode are not sent to the API.
_RBOX_UNFOUND_LIMIT = 8

_UNFOUND_DAYS = 10

#: ResearchBox wants at least 5 s between requests; they are sent 6 s apart
_THROTTLE: Any = None


def _throttle() -> Any:
    global _THROTTLE
    if _THROTTLE is None:
        from metacheck import http

        _THROTTLE = http.Throttle(1, 6)
    return _THROTTLE


def _num_text(x: Any) -> str:
    """A number as R's ``paste0()`` writes it (a whole number without a decimal point)."""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _rbox_retry_after(resp: Any, body: Any = None) -> float | None:
    """Seconds ResearchBox asks to wait before trying again, or ``None`` when the
    response is not a refusal that names a wait.

    A request sent too soon is refused with a 429 that gives the wait in a
    ``Retry-After`` header and as ``retry_after_seconds`` in the body (*body*,
    when the caller has parsed it); the hourly and daily limits answer 429 as
    well, an older reply gave no wait at all.
    """
    if resp.status_code != 429:
        return None
    try:
        return float(resp.headers.get("retry-after", "").strip())
    except ValueError:
        pass
    try:
        if not isinstance(body, dict):
            resp.read()
            body = resp.json()
        wait = body.get("retry_after_seconds")
        return None if wait is None else float(wait)
    except Exception:
        return None


def _unfound_path() -> str:
    return str(state_dir() / "researchbox_unfound_requests.json")


def _rbox_unfound_times() -> list[float]:
    """The times (epoch seconds) of the recorded answers of the last 10 days."""
    try:
        with open(_unfound_path(), encoding="utf-8") as fh:
            times = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(times, list):
        return []
    now = time.time()
    return [
        float(t)
        for t in times
        if isinstance(t, int | float)
        and not isinstance(t, bool)
        and now - t < _UNFOUND_DAYS * 86400
    ]


def _rbox_unfound_add() -> None:
    """Record one answer of ``unfound`` (the folder is made only now)."""
    path = _unfound_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with atomic_write(path) as fh:
            fh.write(json.dumps([*_rbox_unfound_times(), time.time()]).encode("utf-8"))
    except OSError:
        pass


def _form_body(fields: list[tuple[str, Any]]) -> str:
    """``application/x-www-form-urlencoded`` as httr2's ``req_body_form()`` writes it."""
    from urllib.parse import quote

    return "&".join(f"{quote(k, safe='')}={quote(_num_text(v), safe='')}" for k, v in fields)


def _rbox_api(
    action: str, *, code: str | None = None, path: str | None = None, **fields: Any
) -> dict[str, Any]:
    """Port of R/archive-researchbox.R::.rbox_api(): send a request to the ResearchBox API.

    Every API call goes through here: it adds the key, waits as long as
    ResearchBox asks when a request comes too soon, and turns every failure
    into an error message instead of an exception. Returns ``{"ok", "error",
    "body"}``: ``error`` is ``None`` when ``ok`` and ``body`` the parsed JSON
    reply (``None`` when *path*, a file to write the reply to, is given).

    A refusal that cannot be waited out (a wait longer than a minute, which the
    hourly and daily limits ask for, or a wait that kept being refused) is
    reported with the prefix ``"API rate limit exhausted: "``, the same text
    ``download_repo_files()`` uses. ``"get_aspredicted"`` has limits of its own,
    recorded apart from the limit for boxes; a key ResearchBox refuses stops
    both. The key is not part of any message.
    """
    from metacheck import http

    token = rbox_token()
    if not token:
        return {
            "ok": False,
            "error": "no ResearchBox API key is set (see ?rbox_token)",
            "body": None,
        }

    aspredicted = action == "get_aspredicted"
    limit_record = RBOX_ASPREDICTED_RECORD if aspredicted else "researchbox"
    source = key_source("researchbox")
    problem = key_problem("researchbox")
    if aspredicted:
        if problem is None or problem["problem"] != "rejected":
            problem = None
        problem = problem or key_problem(limit_record, source=source)
    limit_name = RBOX_ASPREDICTED_LIMIT if aspredicted else None
    if problem is not None:
        prefix = "API rate limit exhausted: " if problem["problem"] == "limit" else ""
        return {
            "ok": False,
            "body": None,
            "error": prefix
            + key_problem_message(
                "researchbox", problem["problem"], problem["until"], source=source, limit=limit_name
            ),
        }

    form: list[tuple[str, Any]] = [("version", "1.0"), ("action", action)]
    form += [(k, v) for k, v in fields.items() if v is not None]
    if code is not None:
        form.append(("code", code))
    about_box = fields.get("box_id") is not None and code is None
    n_unfound = len(_rbox_unfound_times()) if about_box else 0
    if n_unfound >= _RBOX_UNFOUND_LIMIT:
        return {
            "ok": False,
            "body": None,
            "error": f"not sent: ResearchBox could not find {n_unfound} boxes asked for in the "
            "last 10 days, which may be boxes that are not public; it blocks the key for 10 "
            "days after 10 requests for those, so a box without a peer-review passcode is not "
            "sent to the API until then",
        }

    def transient(resp: Any) -> bool:
        wait = _rbox_retry_after(resp)
        return wait is not None and wait <= _RBOX_MAX_WAIT

    def sink(resp: Any) -> None:
        assert path is not None
        with atomic_write(path) as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)

    try:
        import httpx

        resp = http.request(
            "POST",
            _API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=_form_body(form).encode("utf-8"),
            max_tries=5,
            is_transient=transient,
            retry_on_failure=True,
            throttle=_throttle(),
            sink=sink if path is not None else None,
            raise_errors=True,
            timeout=httpx.Timeout(300.0, connect=20.0),
        )
    except Exception as e:
        # (the exception's type only: nothing that was sent is repeated)
        if path is not None:
            _unlink(path)
        return {
            "ok": False,
            "error": f"Failed to perform HTTP request to researchbox.org ({type(e).__name__})",
            "body": None,
        }
    assert resp is not None

    status = int(resp.status_code)
    media = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    is_json = media == "application/json"
    if path is not None:
        try:
            raw = open(path, "rb").read() if os.path.exists(path) else b""  # noqa: SIM115
        except OSError:
            raw = b""
        has_body = bool(raw) or (os.path.exists(path) and os.path.getsize(path) > 0)
    else:
        raw = resp.content
        has_body = bool(raw)
    body: Any = None
    if is_json and has_body:
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
    fields_of = body if isinstance(body, dict) else {}

    if status == 200 and (path is not None or fields_of.get("ok") is True):
        return {"ok": True, "error": None, "body": None if path is not None else body}

    if path is not None:
        _unlink(path)
    reason = fields_of.get("error")
    # A box that does not exist, or is not public and was asked for without its
    # passcode, answers 500 with an empty body
    if status == 500 and not has_body:
        if about_box:
            _rbox_unfound_add()
        error = "unfound"
    elif aspredicted and status == 404:
        # "not found or not public", also for a link form the API does not take
        error = "unfound"
    elif status == 429:
        import datetime as dt

        wait = _rbox_retry_after(resp, fields_of)
        # The key's hourly or daily limit holds for every box, so it is recorded for
        # the other jobs (see api_key_status()). Not a wait under a minute (a request
        # sent too soon), nor a limit for one box or file, which other boxes are not
        # held to.
        key_wide = (
            wait is not None
            and wait > _RBOX_MAX_WAIT
            and not re.search(r"same[- ](box|file)", str(reason or ""), re.IGNORECASE)
        )
        until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=wait) if wait is not None else None
        if key_wide:
            key_problem_record(limit_record, "limit", until, source=source)
        error = "API rate limit exhausted: " + (str(reason) if reason is not None else "HTTP 429")
        if wait is not None:
            error += f" (ResearchBox asks to wait {_num_text(wait)} seconds)"
        if key_wide:
            error += ". " + key_problem_message(
                "researchbox", "limit", until, source=source, limit=limit_name
            )
    elif status in (401, 403):
        # a key ResearchBox does not know: 401, "token invalid"
        key_problem_record("researchbox", "rejected")
        error = f"HTTP {status}" + (f": {reason}" if reason is not None else "")
        error += ". " + key_problem_message(
            "researchbox", "rejected", source=key_source("researchbox")
        )
    else:
        error = f"HTTP {status}" + (f": {reason}" if reason is not None else "")
    return {"ok": False, "error": error, "body": body}


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# rbox_info()
# ---------------------------------------------------------------------------


def rbox_info(
    rb_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_info(): look up ResearchBox boxes.

    *rb_url* is a box URL, a sequence of them, or a table whose *id_col*
    (1-based position or name) holds them (e.g. from :func:`rbox_links`).
    Every distinct URL is looked up; the input rows are returned with ``files``,
    ``box_id``, ``reference``, the ``RB_*`` columns (or ``error``) added.

    With an API key (:func:`rbox_token`) each box costs two API requests, its
    details and its file list; a peer-review link's passcode is sent along.
    Without a key, or when the API refuses, the box's web page is read instead,
    which does not give ``RB_title``, ``RB_status``, ``RB_paper_doi``,
    ``RB_zenodo_doi``, ``RB_paper_authors``, ``RB_license_url`` and
    ``RB_last_modified`` (``NA``). ``RB_license`` is the licence's SPDX
    identifier and ``RB_public`` the ISO date the box became public, from
    either route. With *cache* a box looked up before (see
    ``repo_info_cache_clear()``) is not asked for again.
    """
    from metacheck._r import as_character, bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _as_values
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.archives.psycharchives import _column, _vector
    from metacheck.utils import left_join, online

    if not online("researchbox.org"):
        raise ConnectionError("ResearchBox.org seems to be offline")

    label = "ResearchBox"
    with _spinner(pb, f"{label} Retrieve") as bar:
        if isinstance(rb_url, pd.DataFrame):
            table = rb_url
            id_col_name = _column(table, id_col)
            raw = table[id_col_name].tolist()
        elif rb_url is None:
            _tick(bar, f"No valid {label} links")
            return pd.DataFrame(index=pd.RangeIndex(0))
        else:
            id_col_name = "rb_url"
            vals, _ = _as_values(rb_url)
            raw = [
                v
                for v in dict.fromkeys(None if is_missing(v) else v for v in vals)
                if v is not None
            ]
            table = pd.DataFrame({"rb_url": _vector(raw)})

        present = [v for v in raw if not is_missing(v)]
        ids = pd.DataFrame({"rb_url": _vector(present)}).drop_duplicates().reset_index(drop=True)
        valid = list(dict.fromkeys(present))
        if not valid:
            _tick(bar, f"No valid {label} links")
            return table

        nv = len(valid)
        _tick(bar, f"Starting {label} retrieval for {nv} file{'' if nv == 1 else 's'}...")
        # Every box is looked up: this loop used to stop at the first box that failed
        id_info = []
        for v in valid:
            u = v if isinstance(v, str) else as_character(v)
            rb_id = _rbox_id(u)
            cached = _repo_info_cache_get("researchbox", rb_id) if cache is True and rb_id else None
            if cached is not None:
                # cached by box id, so a different link to the same box gets its own url
                info1 = cached.copy()
                info1["rb_url"] = pd.Series([u], dtype="string", index=info1.index)
            else:
                info1 = _rbox_info(u, pb=bar)
                if cache is True and rb_id and _repo_info_ok(info1):
                    _repo_info_cache_put("researchbox", rb_id, info1)
            id_info.append(info1)

        info = left_join(bind_rows(id_info), ids, by="rb_url")
        data = left_join(table, info, by={id_col_name: "rb_url"}, suffix=("", ".rb"))
        _tick(bar, "...ResearchBox retrieval complete!")
        return data


def _rbox_info(rb_url: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::.rbox_info(): one ResearchBox box.

    Uses the API when a key is set and reads the box's web page otherwise. When
    the API fails (most often ResearchBox's hourly limit for one box) the web
    page is read instead. The API answers alike for a box that does not exist
    and one that is not public; the web page tells them apart, so its answer is
    the one reported. When both fail the API's reason comes first, so a refusal
    that a later run will get past keeps its ``"API rate limit exhausted: "``
    start.
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _paste

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from {_paste(rb_url)}...")
        api_error: str | None = None
        if _rbox_use_api(rb_url):
            info = _rbox_info_api(rb_url)
            if "error" not in info.columns:
                return info
            api_error = str(info["error"].iloc[0])
            _tick(bar, f"- ResearchBox API: {api_error}; reading the web page instead")

        info = _rbox_info_page(rb_url)
        if api_error is not None and api_error != "unfound" and "error" in info.columns:
            info["error"] = pd.Series(
                [f"{api_error}; web page: {info['error'].iloc[0]}"],
                dtype="string",
                index=info.index,
            )
        return info


#: Columns that only the API fills, added as NA by the web page route so both
#: return the same columns.
_API_ONLY = (
    "RB_title",
    "RB_status",
    "RB_paper_doi",
    "RB_zenodo_doi",
    "RB_paper_authors",
    "RB_license_url",
    "RB_last_modified",
)


def _field(x: Any) -> str | None:
    """The API's value for a field as text: ``None`` where the box has none (``""``)."""
    if x is None or isinstance(x, list | dict) or x == "":
        return None
    return str(x)


def _rbox_info_api(rb_url: Any) -> pd.DataFrame:
    """A box's details and file list from the API (two requests).

    Returns the same columns as :func:`_rbox_info_page`; on failure only
    ``rb_url`` and ``error``.
    """
    from metacheck.archives.dataverse import _cell
    from metacheck.archives.psycharchives import _obj_cell

    obj: dict[str, pd.Series] = {"rb_url": _cell(rb_url)}
    rb_id = _rbox_id(rb_url)
    if rb_id is None:
        obj["error"] = _cell("unfound")
        return pd.DataFrame(obj)
    code = _rbox_code(rb_url)

    meta = _rbox_api("box_meta", box_id=rb_id, code=code)
    if not meta["ok"]:
        obj["error"] = _cell(meta["error"])
        return pd.DataFrame(obj)
    listing = _rbox_api("list_files", box_id=rb_id, code=code)
    if not listing["ok"]:
        obj["error"] = _cell(listing["error"])
        return pd.DataFrame(obj)

    m = meta["body"] if isinstance(meta["body"], dict) else {}
    lst = listing["body"] if isinstance(listing["body"], dict) else {}
    obj["files"] = _obj_cell(_rbox_file_table(lst.get("files")))
    obj["box_id"] = _cell(rb_id)
    obj["reference"] = _cell(None)  # the web page's download token; the API needs none
    # The reference comes with HTML markup, which the web page shows as plain text
    target = _field(m.get("paper_reference"))
    obj["RB_target"] = _cell(None if target is None else _rbox_plain_text(target))
    obj["RB_license"] = _cell(_field(m.get("license_id")))
    obj["RB_public"] = _cell(_field(m.get("public_since")))
    # author1, author2, ... each with first and last name
    authors = []
    for key, a in m.items():
        if re.fullmatch(r"author[0-9]+", key) and isinstance(a, dict):
            authors.append(f"{a.get('first') or ''} {a.get('last') or ''}".strip())
    obj["RB_authors"] = _cell(", ".join(authors) if authors else None)
    obj["RB_abstract"] = _cell(_field(m.get("abstract")))
    obj["RB_title"] = _cell(_field(m.get("box_title")))
    obj["RB_status"] = _cell(_field(m.get("status")))
    obj["RB_paper_doi"] = _cell(_field(m.get("paper_doi")))
    obj["RB_zenodo_doi"] = _cell(_field(m.get("zenodo_doi")))
    pa = _field(m.get("paper_authors"))
    obj["RB_paper_authors"] = _cell(None if pa is None else pa.strip())
    obj["RB_license_url"] = _cell(_field(m.get("license_url")))
    obj["RB_last_modified"] = _cell(_field(m.get("last_modified")))
    return pd.DataFrame(obj)


def _rbox_box_meta(rb_url: Any, cache: bool = False) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::.rbox_box_meta(): a box's licence and status.

    One row with ``RB_license`` (SPDX id, ``"CC-BY-4.0"``) and ``RB_status``
    (``"public"`` or ``"private"``) for ``repo_check``, which gets the files from
    the download: one ``box_meta`` request, where :func:`rbox_info` would also ask
    for the file list. Without a key, or when the API fails, the web page is read
    for the licence; the page does not give the status. ``None`` where neither
    gives it. With *cache* a licence from an earlier :func:`rbox_info` or this
    function is reused, with the status saved with it (``None`` when none was).
    """
    from metacheck.archives.info_cache import _repo_info_cache_get, _repo_info_cache_put

    license_: str | None = None
    status: str | None = None

    def out() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "RB_license": pd.Series([license_], dtype="string"),
                "RB_status": pd.Series([status], dtype="string"),
            }
        )

    rb_id = _rbox_id(rb_url)
    if rb_id is None:
        return out()
    if cache is True:
        for host in ("researchbox", "researchbox_license"):
            cached = _repo_info_cache_get(host, rb_id)
            if isinstance(cached, pd.DataFrame) and "RB_license" in cached.columns and len(cached):
                value = cached["RB_license"].iloc[0]
                if not is_missing(value):
                    license_ = str(value)
                    if "RB_status" in cached.columns and not is_missing(
                        cached["RB_status"].iloc[0]
                    ):
                        status = str(cached["RB_status"].iloc[0])
                    return out()

    if _rbox_use_api(rb_url):
        meta = _rbox_api("box_meta", box_id=rb_id, code=_rbox_code(rb_url))
        if meta["ok"]:
            body = meta["body"] if isinstance(meta["body"], dict) else {}
            license_ = (
                _field(body.get("license_id")) if isinstance(body.get("license_id"), str) else None
            )
            status = _field(body.get("status")) if isinstance(body.get("status"), str) else None
    if license_ is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                page = _rbox_info_page(rb_url)
        except Exception:
            page = None
        if isinstance(page, pd.DataFrame) and "error" not in page.columns:
            value = page["RB_license"].iloc[0] if "RB_license" in page.columns else None
            license_ = None if is_missing(value) else str(value)
    if cache is True and license_ is not None:
        _repo_info_cache_put("researchbox_license", rb_id, out())
    return out()


def _rbox_license(rb_url: Any, cache: bool = False) -> str | None:
    """Port of R/archive-researchbox.R::.rbox_license(): a box's licence only."""
    value = _rbox_box_meta(rb_url, cache=cache)["RB_license"].iloc[0]
    return None if is_missing(value) else str(value)


def _rbox_plain_text(x: str | None) -> str | None:
    """HTML markup as plain text (``None`` stays ``None``)."""
    if x is None:
        return None
    from metacheck.archives.aspredicted import _html_text2

    try:
        text = _html_text2(_read_html(f"<p>{x}</p>"))
        return re.sub(r"\s+", " ", str(text)).strip()
    except Exception:
        return x


def _rbox_file_table(files: Any) -> pd.DataFrame:
    """The API's file list as a table, with ``name`` and ``file_id`` first as in the
    table read from the web page (``list_files`` gives ``basename`` for the name)."""
    if not isinstance(files, list) or not files:
        return pd.DataFrame(
            {"name": pd.Series([], dtype="string"), "file_id": pd.Series([], dtype="string")}
        )
    rows = [f if isinstance(f, dict) else {} for f in files]

    def col(key: str) -> pd.Series:
        return pd.Series(
            [None if r.get(key) is None else str(r[key]) for r in rows], dtype="string"
        )

    def num(key: str) -> pd.Series:
        vals = []
        for r in rows:
            try:
                vals.append(float(r[key]))
            except (KeyError, TypeError, ValueError):
                vals.append(float("nan"))
        return pd.Series(vals, dtype="float64")

    return pd.DataFrame(
        {
            "name": col("basename"),
            "file_id": col("file_id"),
            "section": col("section"),
            "type": col("type"),
            "description": col("description"),
            "extension": col("extension"),
            "size": num("size"),
            "md5": col("md5"),
        }
    )


# ---------------------------------------------------------------------------
# the web page route
# ---------------------------------------------------------------------------

#: The page's section headings, in order (R: ``sections``).
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("RB_target", "SUPPLEMENTARY FILES FOR"),
    ("RB_license", "LICENSE FOR USE"),
    ("RB_public", "BOX PUBLIC SINCE"),
    ("RB_authors", "BOX CREATORS"),
    ("RB_abstract", "ABSTRACT"),
    ("done", "$('.file_number')"),
)

_REDIRECT = r"(?<=window\.location\.replace\(')https://researchbox.org/\d+(?='\))"


def _curl_url(url: str) -> str:
    """The URL libcurl requests for *url*: one without a scheme gets ``http://``.

    ``rbox_links()`` returns bare text mentions such as ``researchbox.org/801``,
    which httr2 hands to libcurl as they are; libcurl guesses the scheme.
    """
    from metacheck._r import grepl

    if grepl("^[A-Za-z][A-Za-z0-9+.-]*://", url):
        return url
    return "http://" + url


def _get(url: str) -> Any:
    """``httr2::request(url) |> req_headers(<rbox headers>) |> req_error(FALSE) |> req_perform()``.

    A request that cannot be sent is an error, as in R.
    """
    from metacheck import http

    resp = http.request("GET", _curl_url(url), headers=_rbox_headers(), max_tries=1)
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: {url}")
    return resp


def _body_string(resp: Any) -> str | None:
    """``httr2::resp_body_string()`` (see :func:`metacheck.archives.dataone._resp_body_string`)."""
    from metacheck.archives.dataone import _resp_body_string

    return _resp_body_string(resp)


def _read_html(text: str | None) -> Any:
    """``xml2::read_html(text, encoding = "UTF-8")`` of a page body.

    libxml2's HTML parser with xml2's options (``RECOVER``, ``NOERROR``,
    ``NOBLANKS``, ``HUGE``), reading the string as UTF-8. ``NA`` is an error,
    and a string without ``<`` or ``>`` is taken as a file path by xml2: an
    error here.
    """
    from lxml import etree
    from lxml import html as lxml_html

    if text is None:
        raise ValueError("`x` must be a single string, not a character `NA`.")
    if "<" not in text and ">" not in text:
        raise FileNotFoundError(f"'{text}' does not exist in current working directory.")
    parser = lxml_html.HTMLParser(
        encoding="utf-8", recover=True, remove_blank_text=True, huge_tree=True, no_network=True
    )
    root = etree.fromstring(text.encode("utf-8", "surrogateescape"), parser)
    if root is None:
        raise ValueError("Document is empty")
    return root


_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _rbox_iso_date(x: str | None) -> str | None:
    """``"June 09, 2023"`` as ``"2023-06-09"``; anything else is returned unchanged
    (English month names in every locale)."""
    if x is None:
        return None
    m = re.fullmatch(r"([A-Za-z]+) ([0-9]{1,2}), ([0-9]{4})", x.strip())
    if m is None or m.group(1).lower() not in _MONTHS:
        return x
    return f"{m.group(3)}-{_MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"


def _rbox_info_page(rb_url: Any) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::.rbox_info_page(): a box read from its web page.

    Follows the page's JavaScript redirect (peer-review links), then returns
    a one-row table: ``rb_url`` and either ``error`` (with a warning) or
    ``files`` (``name``, ``file_id``), ``box_id``, ``reference`` (what the zip
    download needs) and the ``RB_*`` sections of the page.

    ``error`` is ``"unfound"`` for a box that does not exist (also an empty
    page), ``"HTTP <status>"`` for a refusal or server error, and ``"not
    public: ..."`` for a box that sends the browser to a captcha.
    """
    from metacheck._r import strsplit, trimws
    from metacheck.archives.aspredicted import _html_text2
    from metacheck.archives.dataverse import _cell, _paste
    from metacheck.archives.psycharchives import _obj_cell

    obj: dict[str, pd.Series] = {"rb_url": _cell(rb_url)}
    from metacheck._r import regextract_all

    resp = _get(_paste(rb_url))
    # an empty body is no page (it stopped metacheck before the status was checked: U45)
    try:
        body_text = _body_string(resp)
    except ValueError:
        body_text = None
    # R: grepl(pattern, NA) is FALSE
    redirect = [] if body_text is None else regextract_all(_REDIRECT, body_text, perl=True)
    if redirect:
        if len(redirect) > 1:
            raise ValueError("`base_url` must be a single string, not a character vector.")
        resp = _get(redirect[0])

    # A box that does not exist still answers 200, so any other status is a refusal or
    # a server error, not a missing box; the status is named.
    status = int(resp.status_code)
    if status != 200:
        if status == 404:
            warnings.warn(f"{_paste(rb_url)} could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
        else:
            warnings.warn(
                f"ResearchBox did not serve {_paste(rb_url)} (HTTP {status})", stacklevel=2
            )
            obj["error"] = _cell(f"HTTP {status}")
        return pd.DataFrame(obj)

    # A 200 page without content is no box (U45)
    if not resp.content:
        warnings.warn(f"{_paste(rb_url)} could not be found", stacklevel=2)
        obj["error"] = _cell("unfound")
        return pd.DataFrame(obj)

    # A box that does not exist gets a page saying "Problem. There is no ResearchBox
    # #99,999,999"; one that is not public (without its passcode) a page that sends
    # the browser to a captcha.
    page_text = _body_string(resp)
    if page_text is not None and "There is no ResearchBox" in page_text:
        warnings.warn(f"{_paste(rb_url)} could not be found", stacklevel=2)
        obj["error"] = _cell("unfound")
        return pd.DataFrame(obj)
    if page_text is not None and "captcha_wall" in page_text:
        warnings.warn(
            f"ResearchBox did not show {_paste(rb_url)}: the box is probably not public yet",
            stacklevel=2,
        )
        obj["error"] = _cell(
            "not public: ResearchBox asks for a captcha instead of showing the box; "
            "a peer-review link with its passcode is needed"
        )
        return pd.DataFrame(obj)

    doc = _read_html(page_text)

    file_names = [str(p.xpath("string()")) for p in doc.xpath("//p [@class='file_name']")]
    # Each downloadable file has a checkbox <input type="checkbox" name="fileN"
    # value="<file_id>"> in the same document order as the file_name <p> tags, so
    # they are paired positionally.
    file_ids = [
        el.get("value") for el in doc.xpath("//input[@type='checkbox'][starts-with(@name, 'file')]")
    ]
    n = len(file_names)
    if len(file_ids) == n:
        ids: list[str | None] = file_ids
    elif n == 0:
        raise ValueError("arguments imply differing number of rows: 0, 1")
    else:
        ids = [None] * n
    obj["files"] = _obj_cell(
        pd.DataFrame(
            {
                "name": pd.Series(file_names, dtype="string"),
                "file_id": pd.Series(ids, dtype="string"),
            }
        )
    )

    def first_value(xpath: str) -> str | None:
        hits = doc.xpath(xpath)
        return hits[0].get("value") if hits else None

    obj["box_id"] = _cell(first_value("//input[@id='box_id']"))
    obj["reference"] = _cell(first_value("//input[@id='reference']"))

    bodies = [_html_text2(b) for b in doc.xpath("//body")]
    values: dict[str, str | None] = {}
    for i in range(5):
        name, start = _SECTIONS[i]
        value: str | None = None
        if bodies:
            pieces = strsplit(bodies[0], start, fixed=True)
            if len(pieces) >= 2:
                after: str | None = pieces[1]
                # Cut at whichever later heading comes first: a box without an abstract
                # has no "ABSTRACT" (box 10485)
                for _, later in _SECTIONS[i + 1 :]:
                    parts = strsplit(after, later, fixed=True)
                    if not parts:  # R: [[1]] of an empty split is an error, caught: NA
                        after = None
                        break
                    after = parts[0]
                value = None if after is None else trimws(after)
        values[name] = value
    # the same form as from the API: the page names the licence in a sentence ("All
    # content posted to ResearchBox is under a CC By 4.0 License ...") and writes the
    # date as "June 09, 2023"
    lic = values["RB_license"]
    if lic is not None and re.search("CC By 4.0", lic, re.IGNORECASE):
        values["RB_license"] = "CC-BY-4.0"
    values["RB_public"] = _rbox_iso_date(values["RB_public"])
    for name, v in values.items():
        obj[name] = _cell(v)
    for name in _API_ONLY:
        obj[name] = _cell(None)
    return pd.DataFrame(obj)


# ---------------------------------------------------------------------------
# rbox_files() and rbox_search()
# ---------------------------------------------------------------------------


def rbox_files(rb_url: Any, pb: Any = None, cache: bool = False) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_files(): list the files in ResearchBox boxes.

    Lists every file in one or more boxes without downloading them, using one
    API request per box (a peer-review link's passcode is sent along). Needs an
    API key (:func:`rbox_token`). One row per file: ``rb_url``, ``name``,
    ``file_id``, ``section``, ``type``, ``description``, ``extension``, ``size``
    (bytes) and ``md5``, plus an ``error`` column when a box could not be
    listed. ResearchBox limits how often the same box's file list can be
    requested per hour; a box it refuses has an ``error`` starting ``"API rate
    limit exhausted: "``, so it can be requested again later. With *cache* a
    file list obtained before is reused (see ``repo_info_cache_clear()``).
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.info_cache import _repo_info_cache_put
    from metacheck.archives.psycharchives import _chr_list

    if not rbox_token():
        raise ValueError("rbox_files() needs a ResearchBox API key; see ?rbox_token")

    with _spinner(pb) as bar:
        urls = [u for u in dict.fromkeys(_chr_list(rb_url)) if u is not None]
        tables: list[pd.DataFrame] = []
        for u in urls:
            _tick(bar, f"* Listing files in {u}...")
            rb_id = _rbox_id(u)
            if rb_id is None:
                warnings.warn(f"{u} is not a ResearchBox link", stacklevel=2)
                tables.append(
                    pd.DataFrame(
                        {
                            "rb_url": pd.Series([u], dtype="string"),
                            "error": pd.Series(["unfound"], dtype="string"),
                        }
                    )
                )
                continue
            files = _rbox_cached_files(rb_id) if cache is True else None
            if files is None:
                listing = _rbox_api("list_files", box_id=rb_id, code=_rbox_code(u))
                if not listing["ok"]:
                    warnings.warn(f"ResearchBox did not list {u}: {listing['error']}", stacklevel=2)
                    tables.append(
                        pd.DataFrame(
                            {
                                "rb_url": pd.Series([u], dtype="string"),
                                "error": pd.Series([listing["error"]], dtype="string"),
                            }
                        )
                    )
                    continue
                body = listing["body"] if isinstance(listing["body"], dict) else {}
                files = _rbox_file_table(body.get("files"))
                if cache is True:
                    _repo_info_cache_put("researchbox_files", rb_id, files)
            if len(files) == 0:
                continue
            frame = files.copy()
            frame.insert(
                0, "rb_url", pd.Series([u] * len(frame), dtype="string", index=frame.index)
            )
            tables.append(frame)
        if not tables:
            return pd.DataFrame()
        return bind_rows(tables)


def _rbox_cached_files(rb_id: str) -> pd.DataFrame | None:
    """A box's file list from the cache: :func:`rbox_files`' own, or the ``files``
    column of an :func:`rbox_info` result that came from the API (the web page's
    file table has no sizes or checksums). ``None`` when neither is cached."""
    from metacheck.archives.info_cache import _repo_info_cache_get

    files = _repo_info_cache_get("researchbox_files", rb_id)
    if files is not None:
        return files  # type: ignore[no-any-return]
    info = _repo_info_cache_get("researchbox", rb_id)
    if isinstance(info, pd.DataFrame) and "files" in info.columns and len(info):
        files = info["files"].iloc[0]
        if isinstance(files, pd.DataFrame) and "md5" in files.columns:
            return files
    return None


def rbox_search(
    q: str,
    type: str = "data",
    limit: float = 100,
    offset: int = 0,
) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_search(): search ResearchBox.

    Searches the data, code or papers in ResearchBox's public boxes. A data
    search returns one row per matching variable, a code search one row per
    matching file, a paper search one row per matching box. Needs an API key
    (:func:`rbox_token`). ResearchBox returns at most 100 hits per request, so
    each 100 hits cost one API request; a ``*`` matches any ending of a word
    (``"happi*"``). *limit* is the most hits to return (``inf`` for all of
    them), *offset* the number of hits to skip, to continue an earlier search.

    One row per hit: ``rb_url`` and the columns ResearchBox gives for that type.
    ``out.attrs["total"]`` is the number of hits ResearchBox found, which can be
    more than were returned.
    """
    import math

    from metacheck._r import bind_rows

    if type not in ("data", "code", "paper"):
        raise ValueError("'arg' should be one of 'data', 'code', 'paper'")
    if not isinstance(q, str):
        raise ValueError("q must be a single string")
    if isinstance(limit, bool) or not isinstance(limit, int | float) or limit != limit or limit < 1:
        raise ValueError("limit must be a number of at least 1 (or Inf)")
    if not rbox_token():
        raise ValueError("rbox_search() needs a ResearchBox API key; see ?rbox_token")

    pages: list[pd.DataFrame] = []
    total: float = math.inf
    got = 0
    while got < limit and offset < total:
        res = _rbox_api("search", type=type, q=q, offset=offset, limit=int(min(100, limit - got)))
        if not res["ok"]:
            if not pages:
                raise RuntimeError(f"ResearchBox search failed: {res['error']}")
            warnings.warn(
                f"ResearchBox search stopped after {got} hits: {res['error']}; "
                f"use offset = {offset} to continue",
                stacklevel=2,
            )
            break
        body = res["body"] if isinstance(res["body"], dict) else {}
        total = body.get("total", math.inf)
        hits = body.get("hits")
        # no hits come as an empty list
        if not isinstance(hits, list) or not hits:
            break
        page = pd.DataFrame(hits)
        pages.append(page)
        got += len(page)
        offset += len(page)

    hits_df = bind_rows(pages) if pages else pd.DataFrame()
    if len(hits_df) and "box_id" in hits_df.columns:
        rb_url = pd.Series(
            [f"https://researchbox.org/{_num_text(b)}" for b in hits_df["box_id"]], dtype="string"
        )
    else:
        rb_url = pd.Series([], dtype="string")
    out = pd.concat([pd.DataFrame({"rb_url": rb_url}), hits_df.reset_index(drop=True)], axis=1)
    if "upload_time" in out.columns:
        out["upload_time"] = pd.to_datetime(out["upload_time"], unit="s", utc=True)
    out.attrs["total"] = int(total) if math.isfinite(total) else None
    return out


# ---------------------------------------------------------------------------
# rbox_file_download()
# ---------------------------------------------------------------------------


def _cache_subdir(rb_url: str, cache: bool = False) -> str:
    """``.repo_cache_subdir(rb_url, cache = cache)`` (R/repo-download.R)."""
    from metacheck.archives.download import _repo_cache_subdir

    return str(_repo_cache_subdir(rb_url, cache is True))


def _list_files(root: str) -> list[str]:
    """``list.files(root, recursive = TRUE)``: relative paths of files, sorted as R does
    (locale collation, which R does with ICU).

    As in R (``all.files = FALSE``), files and directories whose names start
    with ``.`` are left out (a zip made on a Mac holds ``.DS_Store`` and
    ``__MACOSX/._*`` entries).
    """
    from metacheck._r import r_sorted

    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
            out.append(rel)
    return list(r_sorted(out))


#: The API's zip holds "Table of Contents Box #<box id>.json" and a .csv copy, next
#: to the folders of files, listing each file's name, file_id, type, section,
#: description, size and MD5. A zip from the web page had none.
_TOC_REGEX = re.compile(r"Table of Contents Box #[0-9]+\.(json|csv)")


def _failed_frame(rows: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """The ``"failed"`` table: ``rb_url``, ``name`` (``NA`` when the whole box failed), ``error``."""
    rows = rows or []
    return pd.DataFrame(
        {
            "rb_url": pd.Series([r.get("rb_url") for r in rows], dtype="string"),
            "name": pd.Series([r.get("name") for r in rows], dtype="string"),
            "error": pd.Series([r.get("error") for r in rows], dtype="string"),
        }
    )


def _rbox_no_files(failed: pd.DataFrame) -> pd.DataFrame:
    """The table returned when no file could be retrieved: no rows, the usual columns,
    and the reason in the ``"failed"`` attribute."""
    out = pd.DataFrame(
        {
            "rb_url": pd.Series([], dtype="string"),
            "name": pd.Series([], dtype="string"),
            "file_location": pd.Series([], dtype="string"),
            "size": pd.Series([], dtype="float64"),
            "isdir": pd.Series([], dtype="boolean"),
            "checksum_ok": pd.Series([], dtype="boolean"),
        }
    )
    out.attrs["failed"] = failed
    return out


def _id_text(x: Any) -> str:
    """``as.character()`` of a file id (a whole number without a decimal point)."""
    return _num_text(x) if isinstance(x, int | float) else str(x)


def rbox_file_download(
    rb_url: Any,
    pb: Any = None,
    file_ids: Any = "all",
    subfolders: str = "type",
    cache: bool = False,
) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_file_download(): download a box's files.

    Downloads the boxes as zips and unpacks them into the repository cache (see
    ``repo_cache_dir()``), so a later call for the same box reuses the files
    without asking ResearchBox again. With an API key (:func:`rbox_token`) each
    box costs one API request, a peer-review link's passcode is sent along, and
    the zip's table of contents (not returned as one of the box's files) gives
    each file's MD5 checksum. Without a key, or when the API cannot download the
    box, the download is made through the box's web page; that zip has no table
    of contents, so with a key the box's file list is asked for to check the
    files (without one ``checksum_ok`` is ``NA``).

    Returns one row per file (``rb_url``, ``name``, ``file_location``, ``size``,
    ``isdir``, ``checksum_ok``, plus the file type columns), with the table
    ``attrs["failed"]`` (``rb_url``, ``name`` and ``error`` for each box or file
    that could not be retrieved; an error starting ``"API rate limit exhausted:
    "`` is expected to succeed when run again later). A file that is listed but
    does not arrive, or does not match its checksum, is recorded there too, and
    that box is downloaded again on the next call.

    *file_ids* chooses files (the ``file_id`` column of :func:`rbox_files`;
    ResearchBox adds a data file's codebook), *subfolders* the folders (``"type"``
    or ``"section"``); both need an API key. With *cache* the files are kept in
    the lasting on-disk cache; otherwise in a temporary folder removed when the
    process ends.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner
    from metacheck.archives.psycharchives import _url_values
    from metacheck.utils import left_join

    if subfolders not in ("type", "section"):
        raise ValueError("'arg' should be one of 'type', 'section'")
    urls = _url_values(rb_url)
    with _spinner(pb) as bar:
        if len(urls) > 1:
            unique_rb = [u for u in dict.fromkeys(urls) if u is not None]
            # Several links to the same box (a peer-review link and a plain link to the
            # box) are downloaded once, through a link with the passcode when one has
            # it, and every link gets that box's files or failure.
            box = [_rbox_id(u) or u for u in unique_rb]
            has_code = [_rbox_code(u) is not None for u in unique_rb]
            boxes = list(dict.fromkeys(box))
            pick = []
            for b in boxes:
                idx = [i for i, x in enumerate(box) if x == b]
                best = max(idx, key=lambda i: (has_code[i], -i))
                pick.append(unique_rb[best])
            box_lists = [
                rbox_file_download(u, bar, file_ids=file_ids, subfolders=subfolders, cache=cache)
                for u in pick
            ]
            file_lists = []
            failed_parts = []
            for k, u in enumerate(unique_rb):
                tbl = box_lists[boxes.index(box[k])].copy()
                failed = box_lists[boxes.index(box[k])].attrs.get("failed", _failed_frame()).copy()
                # (a table's attributes are not combined: the failed table is one of them,
                # and pandas compares attributes when it concatenates)
                tbl.attrs = {}
                failed.attrs = {}
                tbl["rb_url"] = pd.Series([u] * len(tbl), dtype="string", index=tbl.index)
                if len(failed) > 0:
                    failed["rb_url"] = pd.Series(
                        [u] * len(failed), dtype="string", index=failed.index
                    )
                file_lists.append(tbl)
                failed_parts.append(failed)
            failed_all = bind_rows(failed_parts) if failed_parts else _failed_frame()
            info = bind_rows(file_lists) if file_lists else pd.DataFrame()
            # every link failed: no rows, and each box's reason in the "failed" attribute
            if len(info) == 0:
                return _rbox_no_files(failed_all)
            orig = pd.DataFrame({"rb_url": pd.Series(urls, dtype="string")})
            df = left_join(orig, info, by="rb_url")
            df.attrs["failed"] = failed_all
            return df

        if not urls:
            raise ValueError("argument is of length zero")
        return _rbox_download_one(urls[0], bar, file_ids, subfolders, cache)


def _rbox_download_one(
    url: str | None, bar: Any, file_ids: Any, subfolders: str, cache: bool
) -> pd.DataFrame:
    from metacheck.archives import _tick
    from metacheck.archives.dataverse import _paste
    from metacheck.archives.psycharchives import _add_ext_type

    shown = _paste(url)
    _tick(bar, f"* Retrieving files from {shown}...")

    # Download the ZIP into a cache folder keyed by the box, so re-runs reuse the
    # already-downloaded and unzipped files. A download of only some files, or in
    # folders by section, is a different set of files and is cached separately.
    ids_list = list(file_ids) if isinstance(file_ids, list | tuple) else [file_ids]
    all_files = [_id_text(i) for i in ids_list] == ["all"]
    rb_id = _rbox_id(url)
    cache_key = shown if rb_id is None else f"https://researchbox.org/{rb_id}"
    if not all_files or subfolders != "type":
        numeric = all(isinstance(i, int | float) and not isinstance(i, bool) for i in ids_list)
        ordered = sorted(ids_list) if numeric else sorted(_id_text(i) for i in ids_list)
        cache_key = f"{cache_key} {subfolders} {'-'.join(_id_text(i) for i in ordered)}"
    tmp_dir = _cache_subdir(cache_key, cache)
    os.makedirs(tmp_dir, exist_ok=True)

    zip_path = f"{tmp_dir}/archive.zip"
    out_dir = f"{tmp_dir}/unzipped"
    # the checksum results of the download, so a reused box keeps them, and a box with
    # a missing or damaged file is downloaded again
    check_path = f"{tmp_dir}/checksums.json"
    checks = _read_checks(check_path)

    already = (
        os.path.isdir(out_dir)
        and len(_list_files(out_dir)) > 0
        and not (checks is not None and len(checks["failed"]) > 0)
    )

    if not already:
        use_api = _rbox_use_api(url)
        if (not all_files or subfolders != "type") and not use_api:
            return _rbox_no_files(
                _failed_frame(
                    [
                        {
                            "rb_url": url,
                            "error": "choosing files or folders needs the ResearchBox API: "
                            "a key (see ?rbox_token)",
                        }
                    ]
                )
            )
        code = _rbox_code(url)
        _tick(bar, f"Downloading to: {zip_path}")

        dl: dict[str, Any] = {"ok": False, "error": None}
        if use_api:
            dl = _rbox_api(
                "download_files",
                box_id=rb_id,
                file_ids="all" if all_files else ",".join(_id_text(i) for i in ids_list),
                subfolders=subfolders,
                code=code,
                path=zip_path,
            )
        # The web page can only download the whole box, in folders by type. It is also
        # read for "unfound", which may be a box that is not public: the page tells which.
        page_read = (not use_api) or (not dl["ok"] and all_files and subfolders == "type")
        if page_read:
            api_error = dl["error"] if use_api else None
            if use_api:
                _tick(
                    bar,
                    f"- ResearchBox API: {dl['error']}; downloading through the web page instead",
                )
            dl = _rbox_download_page(url, zip_path)
            # both failed: the API's reason first, so a refusal a later run will get
            # past keeps its "API rate limit exhausted: " start
            if not dl["ok"] and api_error is not None and api_error != "unfound":
                dl["error"] = f"{api_error}; web page: {dl['error']}"

        if not dl["ok"]:
            if dl["error"] != "unfound":
                warnings.warn(f"Could not download {shown}: {dl['error']}", stacklevel=3)
            elif not page_read:
                # (the web page warns itself)
                warnings.warn(f"{shown} could not be found", stacklevel=3)
            return _rbox_no_files(_failed_frame([{"rb_url": url, "error": dl["error"]}]))

        # Unzipped into a folder of its own and renamed when done: an unzip that was
        # stopped left some files in "unzipped", which the next run reused as the whole
        # box (U203).
        _tick(bar, f"Unzipping into: {out_dir}")
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
        # a box with one file may get that file itself, not a zip
        with open(zip_path, "rb") as fh:
            is_zip = fh.read(2) == b"PK"
        listed: pd.DataFrame | None
        with staged_dir(out_dir) as stage:
            single = dl.get("single_name")
            if not is_zip and single:
                shutil.copyfile(zip_path, os.path.join(stage, os.path.basename(single)))
            else:
                _unzip(zip_path, stage)
            # the checksums from the zip's table of contents, which is not one of the
            # box's files and is moved next to the zip
            listed = _rbox_take_toc(stage, tmp_dir)

        # No table of contents (a download through the web page): the API's file list
        # gives the checksums instead
        if listed is None and use_api:
            from metacheck.archives.info_cache import _repo_info_cache_put

            listed = _rbox_cached_files(rb_id) if (cache is True and rb_id) else None
            if listed is None:
                listing = _rbox_api("list_files", box_id=rb_id, code=code)
                if listing["ok"]:
                    body = listing["body"] if isinstance(listing["body"], dict) else {}
                    listed = _rbox_file_table(body.get("files"))
                    if cache is True and rb_id:
                        _repo_info_cache_put("researchbox_files", rb_id, listed)
                else:
                    _tick(
                        bar,
                        f"- ResearchBox did not list the files ({listing['error']}); "
                        "the files are not checked",
                    )

        checks = _rbox_verify_downloads(url, out_dir, listed, file_ids)
        _write_checks(check_path, checks)

    # list files (recursively) and return; a table of contents left in a box downloaded
    # before it was moved out is not one of the box's files
    files = [f for f in _list_files(out_dir) if not _TOC_REGEX.fullmatch(os.path.basename(f))]
    if not files:
        warnings.warn("Unzip produced no files. The archive might be corrupt.", stacklevel=3)
        return _rbox_no_files(
            _failed_frame([{"rb_url": url, "error": "the download contained no files"}])
        )

    locations = [f"{out_dir}/{f}" for f in files]
    ok_map = checks["checksum_ok"] if checks is not None else {}
    df = pd.DataFrame(
        {
            "rb_url": pd.Series([url] * len(files), dtype="string"),
            "name": pd.Series(files, dtype="string"),
            "file_location": pd.Series(locations, dtype="string"),
            "size": pd.Series([float(os.path.getsize(p)) for p in locations], dtype="float64"),
            "isdir": pd.Series([os.path.isdir(p) for p in locations], dtype="boolean"),
            # NA for a box downloaded before checksums were checked, or without them
            "checksum_ok": pd.Series([ok_map.get(f) for f in files], dtype="boolean"),
        }
    )
    df = _add_ext_type(df)

    failed = _failed_frame(checks["failed"] if checks is not None else [])
    if len(failed) > 0:
        n = len(failed)
        first = ", ".join(failed["name"].astype(object).map(str).head(3).tolist())
        warnings.warn(
            f"{n} file{'' if n == 1 else 's'} from {shown} did not arrive intact "
            f'(e.g. {first}). The "failed" attribute lists {"it" if n == 1 else "them"}; '
            "the box is downloaded again on the next run.",
            stacklevel=3,
        )
    df.attrs["failed"] = failed
    return df


def _read_checks(path: str) -> dict[str, Any] | None:
    """The checksum results saved with a download (``None`` when there are none)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ok = data.get("checksum_ok")
    failed = data.get("failed")
    return {
        "checksum_ok": ok if isinstance(ok, dict) else {},
        "failed": failed if isinstance(failed, list) else [],
    }


def _write_checks(path: str, checks: dict[str, Any]) -> None:
    try:
        with atomic_write(path) as fh:
            fh.write(json.dumps(checks).encode("utf-8"))
    except OSError:
        pass


def _unzip(zip_path: str, exdir: str) -> None:
    """``utils::unzip(zip_path, exdir = exdir)``, refusing members that would escape *exdir*."""
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as zf:
            base = os.path.realpath(exdir)
            for member in zf.infolist():
                target = os.path.realpath(os.path.join(exdir, member.filename))
                if target != base and not target.startswith(base + os.sep):
                    continue  # divergence: R would write outside exdir
                zf.extract(member, exdir)
    except zipfile.BadZipFile:
        warnings.warn("error 1 in extracting from zip file", stacklevel=3)


def _file_ext(name: str) -> str:
    """``tools::file_ext()``."""
    m = re.search(r"\.([A-Za-z0-9]+)$", name)
    return m.group(1) if m else ""


def _rbox_take_toc(directory: str, to: str) -> pd.DataFrame | None:
    """Move the table of contents out of the unzipped box in *directory* into *to*, and
    return its JSON as a file table like :func:`_rbox_file_table`'s: ``None`` when there
    is none or it cannot be read."""
    paths = _list_files(directory)
    toc = [p for p in paths if _TOC_REGEX.fullmatch(os.path.basename(p))]
    if not toc:
        return None
    moved = []
    for p in toc:
        dest = os.path.join(to, os.path.basename(p))
        _unlink(dest)
        try:
            shutil.move(os.path.join(directory, p), dest)
        except OSError:
            continue
        moved.append(dest)
    json_files = [m for m in moved if m.endswith(".json")]
    if len(json_files) != 1:
        return None
    try:
        with open(json_files[0], encoding="utf-8") as fh:
            files = json.load(fh).get("files")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(files, list) or not files:
        return None
    if not all(isinstance(f, dict) and {"file_name", "file_id", "md5"} <= set(f) for f in files):
        return None
    return _rbox_file_table(
        [
            {
                "basename": f["file_name"],
                "file_id": f["file_id"],
                "section": f.get("section"),
                "type": f.get("type"),
                "description": f.get("description"),
                "extension": _file_ext(str(f["file_name"])),
                "size": f.get("size"),
                "md5": f["md5"],
            }
            for f in files
        ]
    )


def _rbox_download_page(rb_url: Any, zip_path: str) -> dict[str, Any]:
    """Download a whole box through its web page, as ResearchBox's own "download all"
    button does. Returns ``{"ok", "error", "single_name"}``, where ``single_name`` is the
    name of the box's only file (the page may send that file itself rather than a zip).

    ResearchBox generates the zip on request from a POST to ``download_files.php``, given
    the box id, the reference token and the ids of the files to include, all of which
    are read off the box's own page by :func:`_rbox_info_page`.
    """
    import httpx

    from metacheck import http

    info = _rbox_info_page(rb_url)
    if "error" in info.columns:
        # _rbox_info_page() already warns for this case
        return {"ok": False, "error": str(info["error"].iloc[0])}

    files = info["files"].iloc[0]
    file_ids = [v for v in files["file_id"].tolist() if not is_missing(v)]
    box_id = info["box_id"].iloc[0]
    reference = info["reference"].iloc[0]
    if not file_ids or is_missing(box_id) or is_missing(reference):
        return {"ok": False, "error": "could not find downloadable files"}

    # ResearchBox's file ids are tokens now, sent as text, as its own download_files.js
    # does; numeric ids (older pages) are still sent as numbers. A JSON array even for
    # one file.
    ids: list[Any] = (
        [int(i) for i in file_ids]
        if all(re.fullmatch(r"[0-9]+", str(i)) for i in file_ids)
        else [str(i) for i in file_ids]
    )
    body = {"files": ids, "box_id": str(box_id), "reference": str(reference)}
    headers = dict(_rbox_headers())
    headers["Content-Type"] = "application/json"

    def sink(resp: Any) -> None:
        with atomic_write(zip_path) as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)

    status: int | None
    try:
        resp = http.request(
            "POST",
            "https://researchbox.org/download_files.php",
            headers=headers,
            content=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            max_tries=1,
            retry_on_failure=False,
            sink=sink,
            timeout=httpx.Timeout(300.0, connect=20.0),
        )
        status = None if resp is None else int(resp.status_code)
    except (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL, OSError):
        status = None

    # After several downloads the page asks for a captcha (a 429 whose JSON body has action
    # "captcha"); waiting a few seconds does not lift it, so it is reported as a limit a
    # later run may get past
    if status == 429:
        try:
            with open(zip_path, "rb") as fh:
                refusal = json.loads(fh.read().decode("utf-8"))
        except (OSError, ValueError):
            refusal = None
        _unlink(zip_path)
        if isinstance(refusal, dict) and refusal.get("action") == "captcha":
            return {
                "ok": False,
                "error": "API rate limit exhausted: ResearchBox asks for a captcha before more "
                "downloads from its web page; an API key (see ?rbox_token) avoids this",
            }

    if status != 200 or not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
        return {
            "ok": False,
            "error": "download failed or resulted in an empty file"
            + ("" if status is None else f" (HTTP {status})"),
        }
    return {
        "ok": True,
        "error": None,
        "single_name": str(files["name"].iloc[0]) if len(file_ids) == 1 else None,
    }


def _rbox_verify_downloads(
    rb_url: Any, out_dir: str, listed: pd.DataFrame | None, file_ids: Any = "all"
) -> dict[str, Any]:
    """Port of R/archive-researchbox.R::.rbox_verify_downloads(): check downloaded files
    against their checksums.

    Compares every unzipped file with the MD5 checksum ResearchBox lists for a file of
    the same name, and records every listed file that did not arrive or does not match.
    Files are matched by name, not by folder: the zip puts them in folders by type or by
    section, but the folder names are ResearchBox's own and could change.

    *listed* is the box's file table (from the zip's table of contents or the API);
    ``None`` when there is none (then nothing can be checked). *file_ids* are the ids
    asked for, or ``"all"``: only these must arrive, but every file that does arrive is
    checked, including a codebook ResearchBox added.

    Returns ``{"checksum_ok", "failed"}``: ``checksum_ok`` maps each unzipped file's path
    in *out_dir* to ``True``/``False`` (``None`` when no listed file has that name),
    ``failed`` is a list of ``{rb_url, name, error}``.
    """
    paths = _list_files(out_dir)
    checksum_ok: dict[str, bool | None] = dict.fromkeys(paths)
    failed: list[dict[str, Any]] = []
    if listed is None or len(listed) == 0 or "md5" not in listed.columns:
        return {"checksum_ok": checksum_ok, "failed": failed}

    def md5_of(path: str) -> str:
        h = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    md5 = [md5_of(os.path.join(out_dir, p)) for p in paths]
    names = [None if is_missing(v) else str(v) for v in listed["name"].tolist()]
    sums = [None if is_missing(v) else str(v).lower() for v in listed["md5"].tolist()]
    ids = [None if is_missing(v) else str(v) for v in listed["file_id"].tolist()]
    for p, m in zip(paths, md5, strict=True):
        expected = [s for n, s in zip(names, sums, strict=True) if n == os.path.basename(p)]
        if expected:
            checksum_ok[p] = m in expected

    # every file asked for must have arrived with a matching checksum
    ask = [_id_text(i) for i in (file_ids if isinstance(file_ids, list | tuple) else [file_ids])]
    every = ask == ["all"]
    wanted = [i for i in range(len(listed)) if every or ids[i] in ask]
    for i in wanted:
        same = [m for p, m in zip(paths, md5, strict=True) if os.path.basename(p) == names[i]]
        if not same:
            error = "not in the download"
        elif sums[i] not in same:
            error = "checksum does not match"
        else:
            continue
        failed.append({"rb_url": rb_url, "name": names[i], "error": error})
    if not every:
        listed_ids = set(ids)
        failed.extend(
            {"rb_url": rb_url, "name": f"file_id {missing}", "error": "not in the download"}
            for missing in dict.fromkeys(a for a in ask if a not in listed_ids)
        )
    return {"checksum_ok": checksum_ok, "failed": failed}
