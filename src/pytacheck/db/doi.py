"""DOI utilities (port of ``R/doi.R``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pytacheck._r.base import as_character, trimws
from pytacheck._r.regex import compile_r, is_na, sub
from pytacheck.db._utils import (
    as_vector,
    paste_unlist,
    r_dollar,
    r_list_set,
    records_frame,
    resp_body_json,
    unlist,
    url_encode,
)

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["doi_clean", "doi_lookup", "doi_resolves", "doi_valid_format"]

# doi_clean() substitutions, in order (pattern, replacement, ignore.case, perl)
_CLEAN_STEPS = (
    (r"^https?://(dx\.)?doi\.org/", "", True, False),
    (r"^doi\s*:\s*", "", True, False),
    # journal-specific "doi" like
    # http://journals.plos.org/plosone/article?id=10.1371/journal.pone.0004153
    (r"^.*?(10\.\d{3,}.*)$", r"\1", False, True),
    (r"#.*$", "", False, False),  # section markers
    (r"/full$", "", False, False),  # /full at the end
)

_VALID_DOI = (
    r"^10\.\d{3,9}\/"  # 10.
    r"[-._;()/:<>A-Za-z0-9]*"  # valid characters
    r"[A-Za-z0-9]$"  # must end in a number/letter
)


def _is_scalar(x: Any) -> bool:
    return x is None or isinstance(x, str) or not hasattr(x, "__iter__")


def _like(x: Any, values: list[Any]) -> Any:
    """Return *values* shaped like the input *x* (scalar, Series or list)."""
    if _is_scalar(x) and len(values) == 1:
        return values[0]
    try:
        import pandas as pd

        if isinstance(x, pd.Series) and len(x) == len(values):
            return pd.Series(values, index=x.index, dtype=object, name=x.name)
    except ImportError:  # pragma: no cover
        pass
    return values


def _chr_values(x: Any) -> list[str | None]:
    """``x |> unlist() |> as.character()``."""
    if x is None:
        return [None]
    if _is_scalar(x):
        return [as_character(x)]
    if hasattr(x, "tolist"):
        x = x.tolist()
    out: list[str | None] = []

    def walk(v: Any) -> None:
        # a Python None inside a vector is NA (kept), unlike R's NULL
        if isinstance(v, list | tuple):
            for e in v:
                walk(e)
        elif isinstance(v, Mapping):
            for e in v.values():
                walk(e)
        else:
            out.append(None if is_na(v) else as_character(v))

    walk(list(x))
    return out


def _clean(values: list[str | None]) -> list[str | None]:
    out: list[str | None] = trimws(values)
    for pattern, repl, ignore_case, perl in _CLEAN_STEPS:
        out = sub(pattern, repl, out, ignore_case=ignore_case, perl=perl)
    return trimws(out)  # type: ignore[no-any-return]


def doi_clean(doi: Any) -> Any:
    """Clean DOIs (port of ``doi_clean()``).

    Removes ``https://doi.org/`` / ``doi:`` prefixes, anything before the
    first ``10.NNN`` (journal-specific URLs), ``#`` section markers and a
    trailing ``/full``. Nested lists are flattened as by ``unlist()``.

    >>> doi_clean("https://doi.org/10.1038/nphys1170")
    '10.1038/nphys1170'
    """
    return _like(doi, _clean(_chr_values(doi)))


def _valid_values(values: list[Any]) -> list[bool]:
    rx = compile_r(_VALID_DOI, False, True, False)
    out = []
    for v in values:
        s = None if is_na(v) else (v if isinstance(v, str) else as_character(v))
        out.append(s is not None and rx.search(s) is not None)
    return out


def doi_valid_format(doi: Any) -> Any:
    """Validate DOI format (port of ``doi_valid_format()``); ``NA`` is ``False``."""
    if _is_scalar(doi):
        return _valid_values([doi])[0]
    values = doi.tolist() if hasattr(doi, "tolist") else list(doi)
    return _like(doi, _valid_values(values))


def _resolves(resp: Any) -> bool | None:
    # metacheck checks inherits(resp, "error"), but .batch_query() returns
    # NULL for connection failures, which then crashes (`NULL$status_code`);
    # the documented intent is NA ("the check failed").
    if resp is None:
        return None
    if resp.status_code != 200:
        return False
    try:
        body = resp_body_json(resp)
    except (TypeError, ValueError):
        body = None
    code = r_dollar(body, "responseCode")
    if code is None or isinstance(code, list | dict):
        return None
    if code == 1:
        return True
    if code == 100:
        return False
    if code == 2:
        return None
    if code == 200:
        return False
    return None


def doi_resolves(doi: Any, timeout: float = 10) -> Any:
    """Check whether DOIs resolve (port of ``doi_resolves()``).

    Queries ``https://doi.org/api/handles/<doi>?type=URL``: ``True`` if the
    DOI is registered with a URL, ``False`` if it does not exist or has no
    URL (or is clearly invalid, i.e. not of the form ``10.xxx/...``, which is
    decided without a request), and ``None`` (NA) if the check failed or the
    DOI is missing/empty.
    """
    from pytacheck import http

    cleaned = _clean(_chr_values(doi))
    valid = _valid_values(cleaned)
    na_or_empty = [v is None or v == "" for v in cleaned]
    res: list[bool | None] = [
        None if na else (None if ok else False) for na, ok in zip(na_or_empty, valid, strict=True)
    ]
    needs = [i for i, (na, ok) in enumerate(zip(na_or_empty, valid, strict=True)) if not na and ok]
    if needs:
        urls = [
            "https://doi.org/api/handles/" + url_encode(cleaned[i], reserved=True) + "?type=URL"
            for i in needs
        ]
        batch_size = 500
        msg = "Checking DOIs" if len(urls) > batch_size else None
        resps = http.batch_query(urls, batch_size=batch_size, msg=msg, delay=0, timeout_s=timeout)
        for i, resp in zip(needs, resps, strict=True):
            res[i] = _resolves(resp)
    return _like(doi, res)


# ---------------------------------------------------------------------------
# doi_lookup
# ---------------------------------------------------------------------------


def _info_value(x: Any) -> Any:
    """``if (length(i) == 1 & is.atomic(i)) i[[1]] else unlist(i) |> paste(collapse = "; ")``."""
    if x is None or isinstance(x, str | int | float | bool):
        return x
    return paste_unlist(x, collapse="; ")


def _paste_author(a: Any) -> list[str]:
    """``paste(a$family, a$given, sep = ", ")`` (zero-length arguments dropped)."""
    parts = []
    for key in ("family", "given"):
        v = r_dollar(a, key)
        if v is None:
            continue
        vals = unlist(v) if isinstance(v, list | dict) else [v]
        if not vals:
            continue
        parts.append(vals)
    if not parts:
        return []
    n = max(len(p) for p in parts)
    return [
        ", ".join("NA" if (s := as_character(p[k % len(p)])) is None else s for p in parts)
        for k in range(n)
    ]


def _lookup_row(bd: Any) -> dict[str, Any]:
    first_page: Any = None
    last_page: Any = None
    page = r_dollar(bd, "page")
    if page is not None:
        from pytacheck._r.regex import strsplit

        pages = strsplit(as_character(unlist(page)[0]) if isinstance(page, list) else page, "-")
        pages = pages[0] if pages and isinstance(pages[0], list) else pages
        if not pages:
            raise IndexError("subscript out of bounds")
        first_page = pages[0]
        if len(pages) > 1:
            last_page = pages[1]

    author_list = r_dollar(bd, "author")
    if author_list is None:
        authors = ""
    else:
        items = list(author_list.values()) if isinstance(author_list, Mapping) else author_list
        pasted = [_paste_author(a) for a in (items if isinstance(items, list) else [items])]
        if all(len(p) == 1 for p in pasted):
            authors = "; ".join(p[0] for p in pasted)
        else:  # sapply() returns a list; paste() deparses its elements
            authors = "; ".join(
                p[0] if len(p) == 1 else ("character(0)" if not p else _deparse_chr(p))
                for p in pasted
            )

    def get(key: str) -> Any:
        return bd.get(key) if isinstance(bd, Mapping) else None

    published = get("published")
    year: Any = None
    date_parts = r_dollar(published, "date-parts")
    if date_parts is not None:
        first = date_parts[0] if isinstance(date_parts, list) and date_parts else None
        if isinstance(date_parts, list) and not date_parts:
            raise IndexError("subscript out of bounds")
        if isinstance(first, list):
            if not first:
                raise IndexError("subscript out of bounds")
            year = first[0]
        else:
            year = first

    info = {
        "doi": get("DOI"),
        "type": get("type"),
        "title": get("title"),
        "container": get("container-title"),
        "year": year,
        "author": authors,
        "volume": get("volume"),
        "issue": get("issue"),
        "first_page": first_page,
        "last_page": last_page,
        "editor": get("editor"),
        "publisher": get("publisher"),
        "url": get("URL"),
    }
    return {k: _info_value(v) for k, v in info.items()}


def _deparse_chr(values: list[str]) -> str:
    inner = ", ".join('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values)
    return f"c({inner})"


def doi_lookup(doi: Any) -> pd.DataFrame | None:
    """Doi.org info from DOIs (port of ``doi_lookup()``).

    Retrieves citation metadata (CSL JSON via content negotiation at
    https://doi.org) and returns one row per DOI with columns ``doi``,
    ``type``, ``title``, ``container``, ``year``, ``author``, ``volume``,
    ``issue``, ``first_page``, ``last_page``, ``editor``, ``publisher`` and
    ``url``. Missing DOIs give an all-``NA`` row.

    As in metacheck, an HTTP error status for any DOI makes the whole lookup
    return ``None`` (a ``return()`` inside ``tryCatch()``), and a response
    that is not JSON removes that DOI's slot from the result list (assigning
    ``NULL`` to a list element), shifting later rows.
    """
    import pandas as pd

    from pytacheck import http

    values = as_vector(doi)
    if len(values) == 0:
        return pd.DataFrame({"doi": pd.Series([], dtype="string")})

    cleaned = _clean(_chr_values(values))
    is_valid = [not is_na(v) for v in values]
    valid_idx = [i for i, ok in enumerate(is_valid) if ok]
    urls = ["https://doi.org/" + url_encode(cleaned[i], reserved=True) for i in valid_idx]
    # small batch size because most lookups use crossref and email isn't supplied
    resps = http.batch_query(urls, batch_size=3, msg="DOI Lookup")

    bibdata: list[Any] = [None] * len(values)
    for i, ok in enumerate(is_valid):
        if not ok:
            bibdata[i] = {"doi": None}
    for j, i in enumerate(valid_idx):
        resp = resps[j]
        try:
            if resp is None:
                raise TypeError("`resp` must be an HTTP response object, not `NULL`.")
            if resp.status_code >= 400:
                # `return(NULL)` inside tryCatch() returns from doi_lookup()
                return None
            value = resp_body_json(resp)
        except (TypeError, ValueError):
            value = None
        r_list_set(bibdata, i + 1, value)

    if not bibdata:
        return pd.DataFrame()
    return records_frame([_lookup_row(bd) for bd in bibdata])
