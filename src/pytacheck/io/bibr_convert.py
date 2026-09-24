"""Remote bibr conversion client (port of ``R/import-bibr.R``).

``convert_bibr()`` sends documents (PDF, DOC, DOCX) to a bibr extraction
server -- the Scienceverse platform (job queue, API key) or a self-hosted
bibr instance -- and saves the returned bibr JSON. The in-process bibr
integration (no server) lives in :mod:`pytacheck.io.bibr`.

Also here: the author helpers ``format_bib_authors()``,
``.coerce_bib_authors()`` and ``.parse_author_string()``.
"""

from __future__ import annotations

import math
import os
import warnings
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character, trimws
from pytacheck._r.regex import grepl, gsub, strsplit
from pytacheck.log import logger

__all__ = ["convert_bibr", "format_bib_authors"]

PathLikeStr = str | PathLike[str]

_BACKENDS = ("auto", "scivrs", "selfhosted")


def _na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and math.isnan(x)


def _match_arg(value: Any, choices: Sequence[str]) -> str:
    """R's ``match.arg()`` (exact or unique partial match)."""
    from pytacheck.utils import match_arg

    return match_arg(value, choices)


# httr2's status descriptions (``httr2:::http_statuses``), for resp_status_desc()
_HTTR2_STATUSES: dict[int, str] = {
    100: 'Continue',
    101: 'Switching Protocols',
    102: 'Processing',
    103: 'Early Hints',
    200: 'OK',
    201: 'Created',
    202: 'Accepted',
    203: 'Non-Authoritative Information',
    204: 'No Content',
    205: 'Reset Content',
    206: 'Partial Content',
    207: 'Multi-Status',
    208: 'Already Reported',
    226: 'IM Used',
    300: 'Multiple Choice',
    301: 'Moved Permanently',
    302: 'Found',
    303: 'See Other',
    304: 'Not Modified',
    305: 'Use Proxy',
    307: 'Temporary Redirect',
    308: 'Permanent Redirect',
    400: 'Bad Request',
    401: 'Unauthorized',
    402: 'Payment Required',
    403: 'Forbidden',
    404: 'Not Found',
    405: 'Method Not Allowed',
    406: 'Not Acceptable',
    407: 'Proxy Authentication Required',
    408: 'Request Timeout',
    409: 'Conflict',
    410: 'Gone',
    411: 'Length Required',
    412: 'Precondition Failed',
    413: 'Payload Too Large',
    414: 'URI Too Long',
    415: 'Unsupported Media Type',
    416: 'Range Not Satisfiable',
    417: 'Expectation Failed',
    418: "I'm a teapot",
    421: 'Misdirected Request',
    422: 'Unprocessable Entity',
    423: 'Locked',
    424: 'Failed Dependency',
    425: 'Too Early',
    426: 'Upgrade Required',
    428: 'Precondition Required',
    429: 'Too Many Requests',
    451: 'Unavailable For Legal Reasons',
    500: 'Internal Server Error',
    501: 'Not Implemented',
    502: 'Bad Gateway',
    503: 'Service Unavailable',
    504: 'Gateway Timeout',
    505: 'HTTP Version Not Supported',
    506: 'Variant Also Negotiates',
    507: 'Insufficient Storage',
    508: 'Loop Detected',
    510: 'Not Extended',
    511: 'Network Authentication Required',
}


def _status_desc(status: int) -> str | None:
    """``httr2::resp_status_desc()``: the reason phrase, ``None`` (``NA``) if unknown."""
    return _HTTR2_STATUSES.get(int(status))


def _http_error(resp: Any) -> RuntimeError:
    """httr2's error for an HTTP error status (``req_perform()`` default)."""
    desc = _status_desc(resp.status_code)
    return RuntimeError(f"HTTP {resp.status_code} {desc}." if desc else f"HTTP {resp.status_code}.")


def _url_append(api_url: str, *parts: str) -> str:
    """``httr2::req_url_path_append()``."""
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(api_url)
    path = u.path.rstrip("/") + "/" + "/".join(p.strip("/") for p in parts)
    return urlunsplit((u.scheme, u.netloc, path, u.query, u.fragment))


# ---------------------------------------------------------------------------
# convert_bibr()
# ---------------------------------------------------------------------------


def convert_bibr(
    file_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr = ".",
    backend: str = "auto",
    api_key: str | None = None,
    api_url: str | None = None,
    include_figures: bool = False,
    start_page: float = 1,
    end_page: float = math.inf,
    poll_interval: float = 2,
    timeout: float = 600,
) -> Any:
    """Port of ``R/import-bibr.R::convert_bibr()``: convert documents with a bibr server.

    ``backend="auto"`` uses the Scienceverse platform (``"scivrs"``) when an
    API key is given or ``SCIVRS_API_KEY`` is set, else a self-hosted bibr
    (``"selfhosted"``, default ``http://localhost:8000``). Pages are 1-based;
    ``end_page=math.inf`` means all pages. Returns the saved JSON path, or
    a list of paths (``None`` for failures) for several files or a directory.
    """
    backend = _match_arg(backend, _BACKENDS)
    env_key = os.environ.get("SCIVRS_API_KEY", "")
    if backend == "auto":
        backend = "scivrs" if api_key is not None or env_key else "selfhosted"

    if backend == "scivrs" and api_key is None:
        api_key = env_key
        if not api_key:
            raise ValueError(
                "API key not set. Set the SCIVRS_API_KEY environment variable or pass "
                "api_key directly."
            )

    if api_url is None:
        api_url = (
            "https://platform.metacheck.app" if backend == "scivrs" else "http://localhost:8000"
        )

    paths: list[str] = (
        [os.fspath(file_path)]
        if isinstance(file_path, str | PathLike)
        else [os.fspath(f) for f in file_path]
    )
    if len(paths) == 1 and Path(paths[0]).is_dir():
        from pytacheck.io.grobid import _list_files

        paths = [
            p
            for p in _list_files(paths[0], r"\.(docx?|pdf)$")
            if grepl(r"\.(docx?|pdf)$", Path(p).name)
        ]

    if len(paths) > 1:
        out: list[Any] = []
        for fp in paths:
            try:
                out.append(
                    convert_bibr(
                        fp,
                        save_path=save_path,
                        backend=backend,
                        api_key=api_key,
                        api_url=api_url,
                        include_figures=include_figures,
                        start_page=start_page,
                        end_page=end_page,
                        poll_interval=poll_interval,
                        timeout=timeout,
                    )
                )
            except Exception as exc:
                logger("convert_bibr", str(exc))
                out.append(None)
        return out

    # zero-based page values (None = omit from the request)
    zb_start: float | None = start_page - 1 if start_page > 1 else None
    zb_end: float | None = end_page - 1 if math.isfinite(end_page) else None

    if backend == "scivrs":
        contents = _bibr_request_scivrs(
            paths[0],
            api_url,
            str(api_key),
            include_figures,
            zb_start,
            zb_end,
            poll_interval,
            timeout,
        )
    else:
        contents = _bibr_request_selfhosted(paths[0], api_url, include_figures, zb_start, zb_end)
    return _bibr_save_result(contents, paths[0], save_path)


def _form(
    file_path: str, include_figures: bool, start_page: float | None, end_page: float | None
) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = [
        ("file", (Path(file_path).name, Path(file_path).read_bytes())),
        ("include_figures", (None, str(bool(include_figures)).lower())),
    ]
    if start_page is not None:
        fields.append(("start_page", (None, as_character(start_page))))
    if end_page is not None:
        fields.append(("end_page", (None, as_character(end_page))))
    return fields


def _bibr_request_scivrs(
    file_path: str,
    api_url: str,
    api_key: str,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
    poll_interval: float,
    timeout: float,
) -> bytes:
    """Port of ``R/import-bibr.R::.bibr_request_scivrs()``: submit a job and poll it."""
    from pytacheck import http

    auth = {"Authorization": f"Bearer {api_key}"}
    resp = http.request(
        "POST",
        _url_append(api_url, "jobs"),
        max_tries=1,
        headers=auth,
        files=_form(file_path, include_figures, start_page, end_page),
        timeout=60,
    )
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if resp.status_code >= 400:
        raise _http_error(resp)
    if resp.status_code != 200:
        logger("convert_bibr", "submission failed")
        raise RuntimeError(f"Job submission failed (HTTP {resp.status_code}): {resp.text}")
    job_id = resp.json().get("job_id")

    status_url = f"{api_url}/jobs/{job_id}"
    elapsed = 0.0
    while True:
        http.sleep(poll_interval)
        elapsed += poll_interval
        sresp = http.request("GET", status_url, max_tries=1, headers=auth, timeout=30)
        if sresp is None:
            raise ConnectionError(f"Failed to perform HTTP request to {status_url}")
        if sresp.status_code >= 400:
            raise _http_error(sresp)
        status = sresp.json()
        state = status.get("status")
        if state == "complete":
            break
        if state == "failed":
            err = status.get("stage")
            if err is None:  # status$stage %||% "unknown error"
                err = "unknown error"
            logger("convert_bibr", {"job_id": job_id, "error": err})
            raise RuntimeError(f"Job {job_id} failed: {err}")
        if elapsed >= timeout:
            logger("convert_bibr", {"job_id": job_id, "error": "timeout"})
            raise TimeoutError(
                f"Job {job_id} timed out after {as_character(timeout)}s "
                f"(last status: {as_character(state) if state is not None else ''})"
            )

    rresp = http.request(
        "GET",
        _url_append(api_url, "jobs", str(job_id), "result"),
        max_tries=1,
        headers=auth,
        timeout=120,
    )
    if rresp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if rresp.status_code >= 400:
        raise _http_error(rresp)
    if rresp.status_code != 200:
        logger("convert_bibr", "download failed")
        raise RuntimeError(f"Result download failed (HTTP {rresp.status_code})")
    return rresp.content


def _bibr_request_selfhosted(
    file_path: str,
    api_url: str,
    include_figures: bool,
    start_page: float | None,
    end_page: float | None,
) -> bytes:
    """Port of ``R/import-bibr.R::.bibr_request_selfhosted()``: one direct extraction."""
    from pytacheck import http

    resp = http.request(
        "POST",
        _url_append(api_url, "papers", "extract"),
        max_tries=1,
        files=_form(file_path, include_figures, start_page, end_page),
        timeout=300,
    )
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if resp.status_code >= 400:
        raise _http_error(resp)
    if resp.status_code != 200:
        msg = _status_desc(resp.status_code) or "NA"
        raise RuntimeError(f"Bibr request failed with status code: {resp.status_code}\n{msg}")
    return resp.content


def _bibr_save_result(contents: bytes, file_path: PathLikeStr, save_path: PathLikeStr) -> str:
    """Port of ``R/import-bibr.R::.bibr_save_result()``: write the JSON, return its path."""
    Path(save_path).mkdir(parents=True, exist_ok=True)
    name = gsub(r"\..{1,4}$", r"\.json", Path(file_path).name)
    json_path = f"{os.fspath(save_path)}/{name}"
    Path(json_path).write_bytes(contents)
    return json_path


def _bibr_isalive(
    api_url: str,
    api_key: str | None = "__env__",
    error: bool = True,
) -> bool:
    """Port of ``R/import-bibr.R::.bibr_isalive()``: is a bibr server up and ready?

    ``api_key`` defaults to the ``SCIVRS_API_KEY`` environment variable (an
    empty key still sends an ``Authorization`` header, as in R); ``None``
    sends none.
    """
    from pytacheck import http

    if api_key == "__env__":
        api_key = os.environ.get("SCIVRS_API_KEY", "")
    headers = {} if api_key is None else {"Authorization": f"Bearer {api_key}"}
    failure = None
    try:
        resp = http.request(
            "GET",
            _url_append(api_url, "ready"),
            max_tries=3,
            retry_statuses=(429, 503),
            headers=headers,
        )
    except Exception as exc:
        resp, failure = None, str(exc)
    if resp is None:
        if error:
            raise ConnectionError(
                "Connection to the BIBR server failed. Please check your connection or the "
                f"URL: {api_url} ({failure or 'Could not connect to server'})"
            )
        return False

    status = resp.status_code
    if status != 200:
        if error:
            raise RuntimeError(
                f"The BIBR server does not appear up and running on the URL {api_url}. "
                f"Status: {status}"
            )
        return False

    ctype = resp.headers.get("content-type")
    if ctype is None:
        raise ValueError("argument is of length zero")
    if not grepl("json", ctype):
        if error:
            raise RuntimeError("The server is running, but the API key is not valid")
        return False

    body = resp.json()
    checks = body.get("checks") or {}
    if body.get("status") != "ready" or checks.get("bibr") != "ok":
        if error:
            raise RuntimeError(f"The server is running, but BIBR is not ready{status}")
        return False
    return True


# ---------------------------------------------------------------------------
# authors
# ---------------------------------------------------------------------------


def _is_character(x: Any) -> bool:
    if isinstance(x, str):
        return True
    if isinstance(x, pd.Series):
        return pd.api.types.is_string_dtype(x.dtype) or all(isinstance(v, str) or _na(v) for v in x)
    if isinstance(x, list | tuple):
        return (
            len(x) > 0
            and all(isinstance(v, str) or _na(v) for v in x)
            and any(isinstance(v, str) for v in x)
        )
    return False


def _paste_na(v: Any) -> str:
    """``as.character()`` of one value as ``paste()`` writes it (``NA`` -> ``"NA"``)."""
    if _na(v):
        return "NA"
    out = as_character(v)
    return "NA" if out is None else str(out)


def _df_column(df: pd.DataFrame, name: str) -> list[Any]:
    """``df$name``: exact column, else a unique partial match (with R's warning)."""
    if name in df.columns:
        return list(df[name])
    hits = [c for c in df.columns if isinstance(c, str) and c.startswith(name)]
    if len(hits) == 1:
        warnings.warn(f"Partial match of '{name}' to '{hits[0]}' in data frame", stacklevel=3)
        return list(df[hits[0]])
    return []


def format_bib_authors(authors: Any) -> Any:
    """Port of ``R/import-bibr.R::format_bib_authors()``: authors as ``"Family, Given; ..."``.

    ``authors`` is a data frame with ``given``/``family`` columns, a
    character vector (joined with ``"; "``), or a list of those (formatted
    one by one). An empty table or ``None`` gives ``None`` (``NA``).
    """
    if isinstance(authors, list | tuple) and not _is_character(authors):
        return [format_bib_authors(a) for a in authors]
    if authors is None or (isinstance(authors, pd.DataFrame) and len(authors) == 0):
        return None
    if _is_character(authors):
        values = [authors] if isinstance(authors, str) else list(authors)
        return "; ".join(_paste_na(v) for v in values)
    if isinstance(authors, dict):
        authors = pd.DataFrame(authors)
    if not isinstance(authors, pd.DataFrame):
        raise TypeError("$ operator is invalid for atomic vectors")
    # paste(family, given, sep = ", ", collapse = "; "): a missing column is
    # a zero-length vector, which paste() recycles as ""
    family = _df_column(authors, "family")
    given = _df_column(authors, "given")
    n = max(len(family), len(given))
    if n == 0:
        return ""
    fam = [_paste_na(v) for v in family] or [""]
    giv = [_paste_na(v) for v in given] or [""]
    return "; ".join(f"{fam[i % len(fam)]}, {giv[i % len(giv)]}" for i in range(n))


def _authors_frame(given: list[str], family: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "given": pd.Series(given, dtype="string"),
            "family": pd.Series(family, dtype="string"),
        }
    )


def _parse_author_string(s: Any) -> pd.DataFrame:
    """Port of ``R/import-bibr.R::.parse_author_string()``.

    Parses ``"Family, Given; Family2, Given2"`` or ``"Family, Given, and
    Given2 Family2"`` into a ``given``/``family`` table.
    """
    if _na(s) or len(trimws(str(s))) == 0:
        return _authors_frame([], [])
    s = str(s)
    given: list[str] = []
    family: list[str] = []
    if grepl(";", s):
        for part in trimws(strsplit(s, ";")):
            fg = trimws(strsplit(part, ","))
            if not fg:
                raise IndexError("subscript out of bounds")
            if len(fg) >= 2:
                given.append(fg[1])
                family.append(fg[0])
            else:
                given.append("")
                family.append(fg[0])
        return _authors_frame(given, family)

    for part in trimws(strsplit(s, r"\band\b|&")):
        fg = trimws(strsplit(part, ","))
        if len(fg) >= 2:
            given.append(trimws(fg[1]))
            family.append(trimws(fg[0]))
            continue
        words = trimws(strsplit(trimws(part), r"\s+"))
        if len(words) >= 2:
            given.append(" ".join(words[:-1]))
            family.append(words[-1])
        elif len(words) == 1:
            given.append("")
            family.append(words[0])
    return _authors_frame(given, family)


def _coerce_author_cell(x: Any) -> pd.DataFrame:
    if x is None or (not isinstance(x, list | tuple | pd.DataFrame | pd.Series) and _na(x)):
        return _authors_frame([], [])
    if isinstance(x, pd.DataFrame):
        return x.loc[:, [c for c in ("given", "family") if c in x.columns]]
    if hasattr(x, "ndim") and getattr(x, "ndim", 1) == 2:  # legacy matrix [given, family]
        return _authors_frame(
            [as_character(v) for v in x[:, 0]], [as_character(v) for v in x[:, 1]]
        )
    if isinstance(x, list | tuple) and len(x) == 1 and isinstance(x[0], str):
        x = x[0]
    if isinstance(x, str):
        return _parse_author_string(x)
    return _authors_frame([], [])


def _coerce_bib_authors(col: Any) -> Any:
    """Port of ``R/import-bibr.R::.coerce_bib_authors()``.

    Turns an authors column (structured ``[{given, family}]`` tables, legacy
    matrices or ``"Family, Given; ..."`` strings) into a list of
    ``given``/``family`` data frames.
    """
    if col is None:
        return None
    if isinstance(col, pd.DataFrame):
        if col.shape[1] == 0:
            cells: list[Any] = [None] * len(col)
        else:
            cells = [list(col[c]) for c in col.columns]
    else:
        cells = list(col)
    return [_coerce_author_cell(x) for x in cells]
