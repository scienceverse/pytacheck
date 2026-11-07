"""GitHub repositories (port of ``R/archive-github.R``).

:func:`github_links` finds GitHub links in papers (real hyperlinks and bare
``owner/repo`` mentions near the word "github"); :func:`github_repo` checks a
repository exists; :func:`github_readme`, :func:`github_languages`,
:func:`github_files` and :func:`github_tree_files` query the GitHub API;
:func:`github_info` bundles them.

All requests go through :mod:`metacheck.http`. As in metacheck, a request is
tried once (httr2 without ``req_retry()``), an HTTP error status is returned
rather than raised, and a connection failure raises.
"""

from __future__ import annotations

import base64
import functools
import os
import re
import subprocess
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from metacheck._r import slashed
from metacheck._values import as_int, field, field_or_value, is_missing

if TYPE_CHECKING:
    import httpx

__all__ = [
    "github_files",
    "github_info",
    "github_languages",
    "github_links",
    "github_readme",
    "github_repo",
    "github_tree_files",
]

#: R: ``github_regex`` in github_links()
_GITHUB_REGEX = r"(?:https?://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
#: R: the regexec() pattern in github_repo()
_REPO_REGEX = r"(?<=^|/)([a-z0-9-])+/([a-z0-9\._-])+(?=\.git|/|$)"


# ---------------------------------------------------------------------------
# shared helpers (also used by the GitLab and Zenodo ports)
# ---------------------------------------------------------------------------


def _perform(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """``httr2::req_perform()`` with ``req_error(is_error = \\(resp) FALSE)``.

    One try (no ``req_retry()``); any status is returned; a connection-level
    failure raises, as httr2 does.
    """
    from metacheck import http

    kwargs.setdefault("max_tries", 1)
    resp = http.request(method, url, **kwargs)
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: {method} {url}")
    return resp


def _body_json(resp: httpx.Response) -> Any:
    """``httr2::resp_body_json()`` (content type checked, errors raise)."""
    from metacheck.archives.osf_helpers import _resp_body_json

    return _resp_body_json(resp)


def _is_vector(x: Any) -> bool:
    """Is *x* an R vector of several values (not a single string)?"""
    return not isinstance(x, str) and isinstance(x, Sequence | pd.Series | pd.Index)


def _as_list(x: Any) -> list[Any]:
    """An R vector argument as a Python list (``None`` -> empty)."""
    if x is None:
        return []
    if isinstance(x, pd.Series | pd.Index):
        return [None if is_missing(v) else v for v in x.tolist()]
    if _is_vector(x):
        return [None if is_missing(v) else v for v in x]
    return [x]


_INT_MAX = 2**31 - 1


def _r_value_type(v: Any) -> str:
    """The R type jsonlite gives a JSON scalar (``int`` past 32 bits is a double)."""
    if isinstance(v, bool):
        return "logical"
    if isinstance(v, int):
        return "integer" if -_INT_MAX <= v <= _INT_MAX else "double"
    if isinstance(v, float):
        return "double"
    return "character"


_TYPE_ORDER = {"logical": 0, "integer": 1, "double": 2, "character": 3}
_TYPE_DTYPE = {"logical": "boolean", "integer": "Int64", "double": "float64", "character": "string"}


def _r_unlist(values: Sequence[Any]) -> pd.Series:
    """R ``unlist()`` of parsed JSON values: nested lists flattened, ``NULL`` dropped.

    The result has the highest R type present (logical < integer < double <
    character), as ``unlist()`` coerces.
    """
    from metacheck._r import as_character

    flat: list[Any] = []

    def walk(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list | tuple):
            for x in v:
                walk(x)
        else:
            flat.append(v)

    for v in values:
        walk(v)
    if not flat:
        return pd.Series([], dtype="object")
    rtype = max((_r_value_type(v) for v in flat), key=_TYPE_ORDER.__getitem__)
    if rtype == "character":
        out = [("TRUE" if v else "FALSE") if isinstance(v, bool) else as_character(v) for v in flat]
        return pd.Series(out, dtype="string")
    if rtype == "double":
        return pd.Series([float(v) for v in flat], dtype="float64")
    return pd.Series(flat, dtype=_TYPE_DTYPE[rtype])


def _r_data_frame(cols: dict[str, Any]) -> pd.DataFrame:
    """``data.frame(...)`` of vectors: shorter ones recycled, as R does.

    Every length must divide the longest (and none be zero unless all are),
    else R's "arguments imply differing number of rows" error.
    """
    series = {k: v if isinstance(v, pd.Series) else pd.Series(v) for k, v in cols.items()}
    lengths = [len(s) for s in series.values()]
    nr = max(lengths, default=0)
    if any((n == 0 and nr > 0) or (n > 0 and nr % n) for n in lengths):
        raise ValueError(
            "arguments imply differing number of rows: " + ", ".join(str(n) for n in lengths)
        )
    out = {}
    for k, s in series.items():
        s = s.reset_index(drop=True)
        if 0 < len(s) < nr:
            s = pd.concat([s] * (nr // len(s)), ignore_index=True)
        out[k] = s
    return pd.DataFrame(out)


def _r_col_type(col: pd.Series) -> str:
    """The R type of a column: logical/integer/double/character/list (``unspecified`` = all-NA logical)."""
    dtype = col.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "unspecified" if bool(col.isna().all()) else "logical"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "double"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "character"
    if isinstance(dtype, pd.CategoricalDtype):
        return "factor"
    return "list"


def _check_combine(frames: Sequence[pd.DataFrame | None]) -> None:
    """Raise as ``dplyr::bind_rows()`` (vctrs) does when column types cannot be combined.

    Logical, integer and double combine; character only with character; a
    list column only with list columns. An all-``NA`` logical column combines
    with anything. Factors are left to the caller.
    """
    numeric = {"logical", "integer", "double"}
    parts = [f for f in frames if f is not None]
    seen: dict[str, tuple[int, str]] = {}
    for i, f in enumerate(parts, start=1):
        for c in f.columns:
            t = _r_col_type(f[c])
            if t in ("unspecified", "factor"):
                continue
            if c not in seen:
                seen[c] = (i, t)
                continue
            j, prev = seen[c]
            if prev == t or (prev in numeric and t in numeric):
                if _TYPE_ORDER.get(t, -1) > _TYPE_ORDER.get(prev, -1):
                    seen[c] = (j, t)
                continue
            raise TypeError(f"Can't combine `..{j}${c}` <{prev}> and `..{i}${c}` <{t}>.")


def _r_basename(path: str) -> str:
    """R ``basename()`` (trailing separators are dropped first)."""
    stripped = slashed(path).rstrip("/")
    if not stripped:
        return ""
    return stripped.rsplit("/", 1)[-1]


def _file_ext(name: str) -> str:
    """``tools::file_ext()``: the alphanumeric extension after the last dot, or ``""``."""
    from metacheck._r import regextract

    m = regextract(r"\.([[:alnum:]]+)$", name)
    return "" if m is None else m[1:]


def _url_encode(url: str, reserved: bool = False) -> str:
    """``utils::URLencode()``."""
    from metacheck.db._utils import url_encode

    return url_encode(url, reserved=reserved)


@functools.cache
def _file_types_table() -> pd.DataFrame:
    """One row per extension of ``metacheck::file_types``: ``ext`` and ``type``.

    An extension listed under several types (``json`` is ``code`` and
    ``data``) takes the first, in table order: what metacheck's
    ``repo_check`` ends up reporting once its de-duplication drops the
    repeats. metacheck joins the raw table, which repeats such a file once
    per type (U46).
    """
    from metacheck.fileinfo.types import ext_rows

    table = {ext: rows[0][1] for ext, rows in ext_rows().items()}
    return pd.DataFrame(
        {
            "ext": pd.Series(list(table), dtype="string"),
            "type": pd.Series(list(table.values()), dtype="string"),
        }
    )


def _file_types() -> pd.DataFrame:
    """The extension -> type table the file listings are joined with (a copy)."""
    return _file_types_table().copy()


def _add_file_types(files: pd.DataFrame, drop_ext: bool) -> pd.DataFrame:
    """``left_join(files, file_types, by = "ext")``, then fill ``type`` from ``ft``.

    Drops ``ft`` (and ``ext`` when *drop_ext*), as metacheck does after the join.
    """
    from metacheck.utils import left_join

    out = left_join(files, _file_types(), by="ext")
    missing = out["type"].isna()
    out["type"] = out["type"].astype("string").mask(missing, out["ft"].astype("string"))
    drop = ["ft", "ext"] if drop_ext else ["ft"]
    return out.drop(columns=drop)


# ---------------------------------------------------------------------------
# links in papers
# ---------------------------------------------------------------------------


def _plusminus(host: str, texts: list[Any]) -> str:
    """metacheck's "+-10 words around the host name" pattern, as R's TRE applies it.

    R runs the TRE pattern
    ``(?:\\b\\w+\\b\\W+){0,10}\\b<host>(\\.com)?\\b(?:\\W+\\b\\w+\\b){0,10}``
    over every sentence at once. When any sentence it matches is non-ASCII, R
    switches TRE to its wide-character matcher. That matcher ignores the
    ``{0,10}`` upper bounds here (nested ``\\w+``/``\\W+`` inside a bounded
    group), so the match runs to the ends of the sentence, and so does the
    owner/repo search that follows. Checked against R on 3000 random
    sentences: wide mode equals the unbounded pattern in every case. So the
    unbounded pattern is used exactly when R would be in wide mode.
    """
    rep = "*" if _tre_wide(rf"\b{host}(\.com)?\b", texts) else "{0,10}"
    return rf"(?:\b\w+\b\W+){rep}\b{host}(\.com)?\b(?:\W+\b\w+\b){rep}"


def _tre_wide(match_pattern: str, texts: list[Any]) -> bool:
    """Would R's ``gregexpr()`` use TRE's wide-character mode on the matching *texts*?

    ``text_search()`` calls ``gregexpr()`` on the rows that match; R uses the
    wide-character matcher when any of them (NA aside) is not pure ASCII.
    """
    from metacheck._r import grepl

    hits = grepl(match_pattern, texts, ignore_case=True)
    return any(
        bool(h) and isinstance(t, str) and not t.isascii() for t, h in zip(texts, hits, strict=True)
    )


def _host_links(paper: Any, host: str, host_regex: str) -> pd.DataFrame:
    """The shared body of github_links() / gitlab_links()."""
    from metacheck._r import bind_rows, grepl
    from metacheck.archives import _paper_urls
    from metacheck.text.search import text_search

    # strip punctuation off the end of sentences to avoid weird matches
    strip_text = text_search(paper, r".*[^\.$]", return_="match", perl=True)

    # Differs from metacheck (U196): the links are searched in `href`. metacheck
    # passes the url table to text_search(), which searches its first column:
    # `href` in an older-format paper, but the integer `url_id` in a 12.x one, so
    # no link of a 12.x paper (bibr 12.x JSON, or Grobid TEI, which pytacheck
    # reads as 12.x) was found
    found = _paper_urls(paper)
    if "href" in found.columns:
        found = found.loc[grepl(host_regex, found["href"], ignore_case=True, perl=True)]
    else:
        # an empty paper list has a url table without columns: its links are the
        # usual (empty) columns; R's result has no href (UPSTREAM_ISSUES U79),
        # so repo_check() and the modules that call it stop
        found = pd.DataFrame(
            {
                "href": pd.Series([], dtype="string"),
                "text_id": pd.Series([], dtype="Int64"),
                "paper_id": pd.Series([], dtype="string"),
            }
        )
    found = found[["href", "text_id", "paper_id"]]

    # repos referenced only by owner/repo near the host name (+-10 words)
    no_host_regex = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?"
    other = text_search(strip_text, host)
    other = text_search(other, host_regex, exclude=True, perl=True)
    other = text_search(other, f"{host}.io", exclude=True)
    other = text_search(other, _plusminus(host, other["text"].tolist()), return_="match")
    other = text_search(other, no_host_regex, return_="match", perl=True)
    other = other[["text", "text_id", "paper_id"]].rename(columns={"text": "href"})
    return bind_rows([found, other])


def github_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-github.R::github_links(): GitHub links in papers.

    Hyperlinks to ``github.com/owner/repo`` in the paper's ``url`` table, plus
    bare ``owner/repo`` mentions within ten words of "github" in sentences
    that contain no GitHub URL (and no ``github.io``). Returns ``href``,
    ``text_id`` and ``paper_id``.
    """
    from metacheck._r import bind_rows

    found = _host_links(paper, "github", _GITHUB_REGEX)
    # A link to an account ("https://github.com/OneZoom/") names no repository, but is kept
    # so repo_check can report it as an account rather than drop it without a word (#485).
    accounts = _account_links(paper)
    if len(accounts):
        # the hyperlinks come first, then the bare mentions (see _host_links)
        n_href = _n_href_rows(paper)
        found = bind_rows([found.iloc[:n_href], accounts, found.iloc[n_href:]])
    return found


def _account_links(paper: Any) -> pd.DataFrame:
    """The hyperlinks of the paper's ``url`` table that name a GitHub account."""
    from metacheck._r import grepl
    from metacheck.archives import _paper_urls

    urls = _paper_urls(paper)
    empty = pd.DataFrame(
        {
            "href": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
            "paper_id": pd.Series([], dtype="string"),
        }
    )
    if "href" not in urls.columns:
        return empty
    pattern = r"^(?:https?://)?(?:www\.)?github\.com/(?:orgs/)?[A-Za-z0-9-]+/?[.,;:)]*$"
    hit = urls.loc[grepl(pattern, urls["href"], ignore_case=True, perl=True)]
    hit = hit.loc[[_github_account_name(u) is not None for u in hit["href"]]]
    out: pd.DataFrame = hit[["href", "text_id", "paper_id"]]
    return out


def _n_href_rows(paper: Any) -> int:
    """How many rows of ``_host_links(paper, "github", ...)`` come from the url table."""
    from metacheck._r import grepl
    from metacheck.archives import _paper_urls

    urls = _paper_urls(paper)
    if "href" not in urls.columns:
        return 0
    return int(grepl(_GITHUB_REGEX, urls["href"], ignore_case=True, perl=True).sum())


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------


def github_repo(repo: Any) -> Any:
    """Port of R/archive-github.R::github_repo(): the short ``owner/repo`` name.

    Accepts ``owner/repo`` or a GitHub URL (``.git`` and trailing paths are
    ignored) and checks the repository exists with a ``HEAD`` request;
    returns ``None`` when it does not. Several repositories give a list
    aligned with the input (R: a vector named by the input).
    """
    if repo is None:
        return None
    if _is_vector(repo):
        items = _as_list(repo)
        if not items:
            return None
        if len(items) > 1:
            return [github_repo(r) for r in items]
        repo = items[0]
    simple_repo = _github_repo_name(repo)
    if simple_repo is None:
        return None

    resp = _perform("HEAD", f"https://github.com/{simple_repo}")
    if resp.status_code != 200:
        return None
    return simple_repo


def _github_repo_name(repo: Any) -> str | None:
    """The ``owner/repo`` text of a GitHub URL or bare mention, without a request.

    Port of R/archive-github.R::.github_repo_name(). :func:`github_repo` adds a
    ``HEAD`` request to the github.com page; a caller whose next request is to
    the API about the same repository gets the same answer from the API's 404.
    ``None`` when no ``owner/repo`` pattern is found (single values only).
    """
    if _is_vector(repo):
        items = _as_list(repo)
        if len(items) != 1:
            return None
        repo = items[0]
    if repo is None or is_missing(repo):
        return None

    from metacheck._r import as_character, regexec, sub

    text = repo if isinstance(repo, str) else as_character(repo)
    match = regexec(_REPO_REGEX, text, perl=True, ignore_case=True)
    if not match:
        return None
    out = str(sub(r"\.git$", "", match[0]))
    # GitHub does not allow a repository named "." or "..". Such a match is an account
    # address followed by a sentence's full stop ("https://github.com/OneZoom/."), #485.
    if re.search(r"/\.+$", out):
        return None
    return out


#: GitHub's own pages at the depth of an account page are not accounts
_SITE_PAGES = frozenset(
    [
        "about",
        "features",
        "topics",
        "explore",
        "marketplace",
        "pricing",
        "sponsors",
        "settings",
        "login",
        "join",
        "trending",
        "collections",
        "search",
        "enterprise",
        "orgs",
    ]
)
_ACCOUNT_URL = r"^(?:https?://)?(?:www\.)?github\.com/(?:orgs/)?([A-Za-z0-9-]+)/?[.,;:)]*$"


def _github_account_name(url: Any) -> str | None:
    """Port of R/archive-github.R::.github_account_name(): the account named by an address.

    The user or organisation name of a GitHub address that names an account but no
    repository (``https://github.com/OneZoom``, with or without a trailing slash or
    sentence punctuation, or ``github.com/orgs/OneZoom``). Such a page lists an account's
    repositories, but does not say which one holds a paper's files (#485). ``None`` when
    the address is not an account page. Single value only.
    """
    if not isinstance(url, str):
        return None
    m = re.search(_ACCOUNT_URL, url.strip(), re.IGNORECASE)
    if m is None:
        return None
    if m.group(1).lower() in _SITE_PAGES:
        return None
    return m.group(1)


# gitcreds::gitcreds_get() caches the token for the session (in an env var)
_TOKEN_CACHE: dict[str, str | None] = {}


def _github_token() -> str | None:
    """The GitHub token ``gitcreds::gitcreds_get()`` would find, or ``None``.

    ``GITHUB_PAT_GITHUB_COM`` first, then git's credential helpers for
    ``https://github.com`` (never prompting). Looked up once per session.
    """
    if "token" in _TOKEN_CACHE:
        return _TOKEN_CACHE["token"]
    token = os.environ.get("GITHUB_PAT_GITHUB_COM") or None
    if token is None:
        env = dict(os.environ)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": "",
                "SSH_ASKPASS": "",
            }
        )
        try:
            out = subprocess.run(
                ["git", "credential", "fill"],  # noqa: S607 - git on PATH, as gitcreds uses
                input="protocol=https\nhost=github.com\npath=\n\n",
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            out = None
        if out is not None and out.returncode == 0:
            for line in out.stdout.splitlines():
                key, _, value = line.partition("=")
                if key == "password" and value:
                    token = value
    _TOKEN_CACHE["token"] = token
    return token


def _github_config(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Port of R/archive-github.R::.github_config(): GitHub API request headers.

    Adds the v3 ``Accept`` type, metacheck's ``User-Agent``, and an
    ``Authorization`` token when git has GitHub credentials. R modifies an
    httr2 request; here the headers are returned for :func:`metacheck.http.request`.
    """
    out = dict(headers or {})
    out["Accept"] = "application/vnd.github.v3+json"
    out["User-Agent"] = "scienceverse/metacheck"
    from metacheck.archives.api_keys import key_use

    token = key_use("github", check=_github_key_check)
    if token:
        out["Authorization"] = f"token {token}"
    return out


def _github_key_check(key: str) -> bool | None:
    """A key GitHub refuses answers 401 even for a public repository; ``/rate_limit``
    does not count toward the limit."""
    from metacheck.archives.api_keys import key_check_request

    return key_check_request("https://api.github.com/rate_limit", {"Authorization": f"token {key}"})


def _github_limit(resp: httpx.Response) -> str | None:
    """The message for a response refused because GitHub's limit was reached (recorded,
    see ``key_limit_reached``), or ``None`` for any other response.

    GitHub answers 403 or 429 with ``x-ratelimit-remaining`` 0 and the end of the hour in
    ``x-ratelimit-reset``; its secondary limit (too many requests at once) answers the
    same statuses with ``retry-after`` instead.
    """
    import datetime as dt

    from metacheck.archives.api_keys import key_limit_reached

    if resp.status_code not in (403, 429):
        return None
    remaining = resp.headers.get("x-ratelimit-remaining")
    reset = as_int(resp.headers.get("x-ratelimit-reset"))
    wait = as_int(resp.headers.get("retry-after"))
    if remaining != "0" and wait is None:
        return None
    until: Any = None
    if reset is not None:
        until = dt.datetime.fromtimestamp(reset, dt.UTC)
    elif wait is not None:
        until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=wait)
    return key_limit_reached("github", until)


def github_readme(repo: Any) -> Any:
    """Port of R/archive-github.R::github_readme(): a repository's README text.

    ``""`` when the repository or its README cannot be found. Several
    repositories give a list of READMEs aligned with the input.
    """
    if _is_vector(repo) and len(_as_list(repo)) > 1:
        return [github_readme(r) for r in _as_list(repo)]
    clean = github_repo(repo)
    if clean is None:
        return ""
    return _github_readme(clean)


def _github_readme(clean_repo: str) -> str:
    resp = _perform(
        "GET", f"https://api.github.com/repos/{clean_repo}/readme", headers=_github_config()
    )
    if resp.status_code != 200:
        return ""
    content = _body_json(resp)
    raw = base64.b64decode(str(field(content, "content") or ""))
    if b"\x00" in raw:
        # R: rawToChar() refuses a NUL byte
        raise ValueError("embedded nul in string")
    return raw.decode("utf-8", errors="replace")


def github_languages(repo: Any) -> pd.DataFrame | None:
    """Port of R/archive-github.R::github_languages(): languages used in a repository.

    A table of ``repo``, ``language`` and ``bytes`` (one row of missing
    values when GitHub lists none); ``None`` for a repository that does not
    exist or whose languages cannot be retrieved. Several repositories are
    row-bound (missing ones contribute nothing).
    """
    from metacheck._r import bind_rows

    if _is_vector(repo) and len(_as_list(repo)) > 1:
        tables = [github_languages(r) for r in _as_list(repo)]
        _check_combine(tables)
        return bind_rows(tables)
    clean = github_repo(repo)
    if clean is None:
        return None
    return _github_languages(clean)


def _github_languages(clean_repo: str) -> pd.DataFrame | None:
    resp = _perform(
        "GET", f"https://api.github.com/repos/{clean_repo}/languages", headers=_github_config()
    )
    try:
        languages = _body_json(resp)
    except Exception:
        languages = []
    # an error answer's fields ("message", "documentation_url") are no languages (U48)
    if resp.status_code != 200:
        languages = []
    n = 0 if languages is None else len(languages) if isinstance(languages, list | dict) else 1
    if n:
        # names() of a JSON array or scalar is NULL: data.frame() counts it as
        # a zero-row column and stops ("differing number of rows: 1, 0, n")
        names = list(languages) if isinstance(languages, dict) else []
        if isinstance(languages, dict):
            values = list(languages.values())
        else:
            values = languages if isinstance(languages, list) else [languages]
        return _r_data_frame(
            {
                "repo": pd.Series([clean_repo], dtype="string"),
                "language": pd.Series(names, dtype="string"),
                "bytes": _r_unlist(values),
            }
        )
    return pd.DataFrame(
        {
            "repo": pd.Series([clean_repo], dtype="string"),
            "language": pd.Series([None], dtype="string"),
            "bytes": pd.Series([None], dtype="float64"),
        }
    )


# ---------------------------------------------------------------------------
# file listings
# ---------------------------------------------------------------------------


def _ext_from_name(name: str) -> str:
    """``strsplit(name, "\\\\.")``'s last piece when there are two or more, else ``""``."""
    from metacheck._r import strsplit

    parts = strsplit(name, r"\.")
    return parts[-1].lower() if len(parts) >= 2 else ""


def github_files(repo: Any, dir: str = "", recursive: bool = False) -> pd.DataFrame | None:
    """Port of R/archive-github.R::github_files(): list a repository's files.

    One row per file or directory in *dir* (the repository root by default):
    ``repo`` (as given), ``clean_repo``, ``name``, ``path``, ``download_url``,
    ``size``, ``ext`` and ``type`` (from the extension, else ``file``/``dir``),
    sorted by path. With *recursive*, sub-directories' contents follow.
    ``None`` when the repository or directory cannot be listed. Several
    repositories are each listed from *dir* (metacheck lists them from their
    roots: U48) and joined onto the input; ``None`` when none can be listed.
    """
    if _is_vector(repo) and len(_as_list(repo)) > 1:
        from metacheck._r import bind_rows
        from metacheck.utils import left_join

        repos = _as_list(repo)
        unique_repos = [r for r in dict.fromkeys(repos) if r is not None]
        info = bind_rows([github_files(r, dir=dir, recursive=recursive) for r in unique_repos])
        if "repo" not in info.columns:
            return None
        orig = pd.DataFrame({"repo": pd.Series(repos, dtype="string")})
        return left_join(orig, info, by="repo")

    clean_repo = github_repo(repo)
    if clean_repo is None:
        return None
    single = _as_list(repo)[0] if _is_vector(repo) else repo
    return _github_files(single, clean_repo, dir, recursive)


def _github_files(repo: Any, clean_repo: str, dir: str, recursive: bool) -> pd.DataFrame | None:
    """github_files() for one repository already checked by github_repo()."""
    from metacheck._r import as_character, gsub, r_sort_key
    from metacheck.archives import _message

    url = _url_encode(f"https://api.github.com/repos/{clean_repo}/contents/{dir}")
    resp = _perform("GET", url, headers=_github_config())
    try:
        contents = _body_json(resp)
    except Exception:
        contents = []

    if resp.status_code != 200:
        limit = _github_limit(resp)
        if limit is not None:
            _message(limit)
        else:
            _message(dir, ": ", field(contents, "message") or "")
        # NULL rather than an error, so a rate limit at the end of a file
        # list still returns the files up to that point
        return None

    # a file path is listed as one object, an empty directory as []: both are
    # listings (metacheck fails on them: U48)
    if isinstance(contents, dict):
        contents = [contents]
    if not isinstance(contents, list):
        contents = []
    contents = [f for f in contents if isinstance(f, dict)]

    repo_str = as_character(repo) if not isinstance(repo, str) else repo
    names = [f.get("name") for f in contents]
    names = gsub("^/|/$", "", gsub("/+", "/", names))
    paths = [f.get("path") for f in contents]
    sizes = [f.get("size") for f in contents]
    files = pd.DataFrame(
        {
            "repo": pd.Series([repo_str] * len(contents), dtype="string"),
            "clean_repo": pd.Series([clean_repo] * len(contents), dtype="string"),
            "name": pd.Series(names, dtype="string"),
            "path": pd.Series(paths, dtype="string"),
            "download_url": pd.Series([f.get("download_url") for f in contents], dtype="string"),
            "ft": pd.Series([f.get("type") for f in contents], dtype="string"),
            "size": pd.Series(
                sizes, dtype="float64" if any(isinstance(s, float) for s in sizes) else "Int64"
            ),
        }
    )
    # sort_by(files, files$path): a stable order in R's collation
    order = sorted(range(len(files)), key=lambda i: r_sort_key(paths[i]))
    files = files.iloc[order].reset_index(drop=True)
    files["ext"] = pd.Series([_ext_from_name(n) for n in files["name"]], dtype="string")
    files = _add_file_types(files, drop_ext=False)

    if recursive is True:
        is_dir = (files["type"] == "dir").fillna(False).tolist()
        subdirs = [p for p, d in zip(files["path"].tolist(), is_dir, strict=True) if d]
        if subdirs:
            from metacheck._r import bind_rows

            dir_contents = [
                _github_files(repo, clean_repo, subdir, recursive=True) for subdir in subdirs
            ]
            files = bind_rows([files, *dir_contents])
    return files


def github_tree_files(repo: Any) -> dict[str, Any]:
    """Port of R/archive-github.R::github_tree_files(): a repository's whole file tree.

    Two requests (repository metadata, then the recursive Git tree) instead
    of one per directory. Returns a dict with ``gated`` (``True`` only when
    the repository cannot be listed: invalid/inaccessible, or a truncated
    tree), ``reason``, ``files`` (``repo``, ``clean_repo``, ``name``,
    ``path``, ``download_url``, ``size``, ``type``; ``None`` when not
    fetched), ``default_branch`` and ``license`` (the SPDX id GitHub
    detected). The API answers whether the repository exists (a 404 is
    reported as invalid or inaccessible), so no ``HEAD`` request goes to the
    github.com page first. When the metadata or tree request fails otherwise
    (a rate limit, a server error), falls back to the recursive
    :func:`github_files` listing.
    """
    invalid = {
        "gated": True,
        "reason": "invalid or inaccessible GitHub repository",
        "files": None,
        "default_branch": None,
        "license": None,
    }
    # An account page is reachable; it is just not a repository (#485). Checked first,
    # because "github.com/orgs/OneZoom" also has the owner/repo shape.
    if _github_account_name(repo) is not None:
        invalid["reason"] = "links to a GitHub account, not to a specific repository"
        return invalid
    clean_repo = _github_repo_name(repo)
    if clean_repo is None:
        return invalid

    def gated_by(limit: str, default_branch: str | None, license: str | None) -> dict[str, Any]:
        return {
            "gated": True,
            "reason": limit,
            "files": None,
            "default_branch": default_branch,
            "license": license,
        }

    def fallback(default_branch: str, license: str | None) -> dict[str, Any]:
        try:
            files_df = github_files(repo, recursive=True)
        except Exception:
            files_df = None
        from metacheck.archives.api_keys import key_problem, key_problem_message

        problem = key_problem("github")
        if files_df is None and problem is not None and problem["problem"] == "limit":
            # a crawl that GitHub's limit stopped is "not listed", not a repository without files
            reason = "API rate limit exhausted: " + key_problem_message(
                "github", "limit", problem["until"]
            )
            return gated_by(reason, default_branch, license)
        return {
            "gated": False,
            "reason": None,
            "files": files_df,
            "default_branch": default_branch,
            "license": license,
        }

    # 1. repository metadata (default branch + detected licence)
    try:
        meta_resp = _perform(
            "GET", f"https://api.github.com/repos/{clean_repo}", headers=_github_config()
        )
    except Exception:
        meta_resp = None
    # 404: no such repository, or a private one the token cannot see. Any other
    # failure says nothing about the repository, so the contents crawl is tried.
    if meta_resp is not None and meta_resp.status_code == 404:
        return invalid
    # GitHub's limit (60 requests per hour without a key, 5,000 with one) makes the repository
    # "not listed", not empty: the crawl below would be refused too
    limit = None if meta_resp is None else _github_limit(meta_resp)
    if limit is not None:
        return gated_by(limit, None, None)
    if meta_resp is None or meta_resp.status_code != 200:
        return fallback("main", None)

    meta = _body_json(meta_resp)
    default_branch = field(meta, "default_branch")
    if default_branch is None:
        default_branch = "main"
    license_id = _empty_or(field_or_value(field(meta, "license"), "spdx_id"))

    # 2. the Git tree (recursive, one request)
    try:
        tree_resp = _perform(
            "GET",
            f"https://api.github.com/repos/{clean_repo}/git/trees/{default_branch}?recursive=1",
            headers=_github_config(),
        )
    except Exception:
        tree_resp = None
    limit = None if tree_resp is None else _github_limit(tree_resp)
    if limit is not None:
        return gated_by(limit, default_branch, license_id)
    if tree_resp is None or tree_resp.status_code != 200:
        return fallback(default_branch, license_id)

    tree = _body_json(tree_resp)
    if field(tree, "truncated") is True:
        return {
            "gated": True,
            "reason": "GitHub repo tree truncated (>100 000 items); too large to list",
            "files": None,
            "default_branch": default_branch,
            "license": license_id,
        }

    blobs = _filter_blobs(field(tree, "tree") or [])
    paths = [_empty_or(field(x, "path"), "") for x in blobs]
    if not blobs:
        files_df = _empty_tree_files()
    else:
        repo_str = _as_list(repo)[0] if _is_vector(repo) else repo
        raw_base = f"https://raw.githubusercontent.com/{clean_repo}/{default_branch}/"
        names = [_r_basename(p) for p in paths]
        files_df = pd.DataFrame(
            {
                "repo": pd.Series([repo_str] * len(blobs), dtype="string"),
                "clean_repo": pd.Series([clean_repo] * len(blobs), dtype="string"),
                "name": pd.Series(names, dtype="string"),
                "path": pd.Series(paths, dtype="string"),
                "download_url": pd.Series([raw_base + p for p in paths], dtype="string"),
                "size": pd.Series([_vapply_num(field(x, "size")) for x in blobs], dtype="float64"),
                "ft": pd.Series(["file"] * len(blobs), dtype="string"),
            }
        )
        files_df["ext"] = pd.Series([_file_ext(n).lower() for n in names], dtype="string")
        files_df = _add_file_types(files_df, drop_ext=True)

    return {
        "gated": False,
        "reason": None,
        "files": files_df,
        "default_branch": default_branch,
        "license": license_id,
    }


def _filter_blobs(entries: Any) -> list[Any]:
    """``Filter(\\(x) x$type == "blob", entries)`` on parsed JSON tree entries.

    ``Filter()`` keeps ``x[which(unlist(lapply(x, f)))]``: an entry without a
    ``type`` gives ``logical(0)``, which ``unlist()`` drops, so the flags after
    it shift onto earlier entries, as in R.
    """
    if isinstance(entries, dict):
        entries = list(entries.values())
    flags = [t == "blob" for t in (field(x, "type") for x in entries) if t is not None]
    return [entries[i] for i, keep in enumerate(flags) if keep is True]


def _empty_tree_files() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo": pd.Series([], dtype="string"),
            "clean_repo": pd.Series([], dtype="string"),
            "name": pd.Series([], dtype="string"),
            "path": pd.Series([], dtype="string"),
            "download_url": pd.Series([], dtype="string"),
            "size": pd.Series([], dtype="float64"),
            "type": pd.Series([], dtype="string"),
        }
    )


def _empty_or(x: Any, y: Any = None) -> Any:
    """metacheck's ``x %empty_or% y``: *y* when *x* is ``NULL`` or zero-length."""
    if x is None or (isinstance(x, list | tuple | dict) and len(x) == 0):
        return y
    return x


def _vapply_num(x: Any) -> float | None:
    """``vapply(..., \\(x) x %empty_or% NA_real_, numeric(1))`` for one value."""
    x = _empty_or(x)
    if x is None:
        return None
    if isinstance(x, list | dict):
        raise ValueError("values must be length 1")
    if isinstance(x, str):
        raise TypeError("values must be type 'double', but FUN(X[[1]]) result is type 'character'")
    return float(x)


def github_info(repo: Any, recursive: bool = False) -> dict[str, Any] | None:
    """Port of R/archive-github.R::github_info(): everything about a repository.

    A dict with ``repo`` (the short name), ``readme``, ``files`` (see
    :func:`github_files`) and ``languages`` (see :func:`github_languages`);
    ``None`` when the repository does not exist.
    """
    clean = github_repo(repo)
    if clean is None:
        return None
    if isinstance(clean, list):
        # several repositories: R passes the vector on to each function. With
        # a missing one it is a list holding NULL, and github_files()'s
        # data.frame(repo = <that list>) stops
        if any(c is None for c in clean):
            lengths = ", ".join("0" if c is None else "1" for c in clean)
            raise ValueError(f"arguments imply differing number of rows: {lengths}")
        return {
            "repo": clean,
            "readme": github_readme(clean),
            "files": github_files(clean, recursive=recursive),
            "languages": github_languages(clean),
        }
    # `clean` was just checked, so the per-function existence checks
    # (a HEAD request each in metacheck) are skipped
    return {
        "repo": clean,
        "readme": _github_readme(clean),
        "files": _github_files(clean, clean, "", recursive),
        "languages": _github_languages(clean),
    }
