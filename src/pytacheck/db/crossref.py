"""Crossref, DataCite and OpenAlex lookups (port of ``R/db-crossref.R``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pytacheck._r.base import as_character
from pytacheck._r.regex import is_na
from pytacheck.db import _utils
from pytacheck.db._utils import (
    as_vector,
    default_email,
    paste_unlist,
    r_dollar,
    r_list_set,
    records_frame,
    resp_body_json,
    resp_content_type,
    unlist,
    url_encode,
)

if TYPE_CHECKING:
    import httpx
    import pandas as pd

__all__ = [
    "add_bib_match",
    "crossref_doi",
    "crossref_query",
    "datacite_doi",
    "openalex_doi",
    "openalex_query",
]

CROSSREF_DOI_SELECT = (
    "DOI",
    "type",
    "title",
    "author",
    "container-title",
    "volume",
    "issue",
    "page",
    "URL",
    "abstract",
    "year",
    "error",
)
CROSSREF_QUERY_SELECT = (
    "DOI",
    "score",
    "type",
    "title",
    "author",
    "editor",
    "publisher",
    "container-title",
    "year",
    "volume",
    "issue",
    "page",
    "URL",
)

_BIBTYPES = {
    "journal-article": "article",
    "book": "book",
    "book-chapter": "incollection",
    "book-part": "inbook",
    "book-section": "inbook",
    "book-series": "book",
    "edited-book": "book",
    "reference-book": "book",
    "monograph": "book",
    "report": "techreport",
    "proceedings-article": "inproceedings",
    "proceedings": "proceedings",
    "conference-paper": "inproceedings",
    "conference-proceeding": "proceedings",
    "posted-content": "misc",
    "dissertation": "phdthesis",
    "thesis": "phdthesis",
    "dataset": "misc",
    "standard": "misc",
    "reference-entry": "incollection",
    "reference-work": "book",
    "report-series": "techreport",
    "other": "misc",
}


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _r_length(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, list | tuple | Mapping):
        return len(x)
    return 1


def _first(x: Any) -> Any:
    """``x[[1]]`` (a scalar is its own first element; an empty list errors)."""
    if isinstance(x, list | tuple):
        if not x:
            raise IndexError("subscript out of bounds")
        return x[0]
    if isinstance(x, Mapping):
        if not x:
            raise IndexError("subscript out of bounds")
        return next(iter(x.values()))
    return x


def _unlist_first(x: Any) -> Any:
    """``unlist(x)[[1]]`` (``NULL`` when there is nothing)."""
    values = unlist(x)
    if not values:
        return None
    if any(isinstance(v, str) for v in values):
        return as_character(values[0])
    if any(isinstance(v, float) for v in values):
        return float(values[0])
    return values[0]


def _info_value(x: Any) -> Any:
    """``if (length(i) == 1 & is.atomic(i)) i[[1]] else unlist(i) |> paste(collapse = "; ")``."""
    if x is None or isinstance(x, str | int | float | bool):
        return x
    return paste_unlist(x, collapse="; ")


def _is_paperish(x: Any) -> bool:
    from pytacheck.papers.model import Paper, PaperList

    return isinstance(x, Paper | PaperList) or (
        isinstance(x, list | tuple) and len(x) > 0 and all(isinstance(p, Paper) for p in x)
    )


def _paper_dois(papers: Any) -> list[Any]:
    from pytacheck.papers.tables import paper_table

    info = paper_table(papers, "info", ["doi"])
    if "doi" not in info.columns:
        return []
    return [None if is_na(v) else v for v in info["doi"].tolist()]


def _deparse(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, list | tuple):
        return "list(" + ", ".join(_deparse(e) for e in v) + ")"
    s = as_character(v)
    return "NA" if s is None else s


def _df_dollar(df: pd.DataFrame, name: str) -> Any:
    """``df$name`` on a data frame (exact, else unique partial match), as a list."""
    cols = [str(c) for c in df.columns]
    if name in cols:
        col = name
    else:
        hits = [c for c in cols if c.startswith(name)]
        if len(hits) != 1:
            return None
        col = hits[0]
    return [None if (not isinstance(v, list | tuple | Mapping) and is_na(v)) else v for v in df[col].tolist()]


def _encode_query(value: Any) -> str:
    """``utils::URLencode(x, reserved = TRUE) |> gsub("%28", "(") |> gsub("%29", ")")``."""
    if isinstance(value, list | tuple):
        value = unlist(value)[0] if len(unlist(value)) == 1 else value
    return url_encode(value, reserved=True).replace("%28", "(").replace("%29", ")")


def _nzchar(s: str) -> bool:
    return len(s) > 0


# ---------------------------------------------------------------------------
# DataCite
# ---------------------------------------------------------------------------


def _datacite_row(bd: Any) -> dict[str, Any]:
    att = r_dollar(r_dollar(bd, "data"), "attributes")
    authors: list[dict[str, Any]] = []
    creators = r_dollar(att, "creators")
    for a in creators.values() if isinstance(creators, Mapping) else creators or []:
        if a is None or _r_length(a) == 0:
            continue
        authors.append(
            {"given": r_dollar(a, "givenName"), "family": r_dollar(a, "familyName")}
        )

    info = {
        "service": "datacite",
        "service_id": r_dollar(r_dollar(bd, "data"), "id"),
        "score": None,
        "doi": r_dollar(att, "doi"),
        "bib_type": r_dollar(r_dollar(att, "types"), "bibtex"),
        "title": _unlist_first(r_dollar(att, "titles")),
        "authors": None,
        "container": _unlist_first(r_dollar(att, "container")),
        "publisher": att.get("publisher") if isinstance(att, Mapping) else None,
        "year": r_dollar(att, "publicationYear"),
        "date": _unlist_first(r_dollar(att, "dates")),
        "url": att.get("url") if isinstance(att, Mapping) else None,
        "version": att.get("version") if isinstance(att, Mapping) else None,
    }
    row = {k: _info_value(v) for k, v in info.items()}
    row["score"] = float("nan")
    row["authors"] = authors
    return row


def datacite_doi(doi: Any) -> pd.DataFrame | None:
    """DOI info from DataCite (port of ``datacite_doi()``).

    Returns a ``bib_match``-style table (``service`` = ``"datacite"``) with
    one row per DOI. As in metacheck, an HTTP error status makes the whole
    call return ``None``, and a response that is not ``application/json``
    removes that DOI's slot from the result list.
    """
    import pandas as pd

    from pytacheck import http

    values = as_vector(doi)
    if len(values) == 0:
        return pd.DataFrame(
            {
                "service": pd.Series([], dtype="string"),
                "title": pd.Series([], dtype="string"),
                "authors": pd.Series([], dtype=object),
                "doi": pd.Series([], dtype="string"),
            }
        )

    from pytacheck.db.doi import doi_clean

    cleaned = doi_clean(list(values))
    is_valid = [not is_na(v) for v in values]
    valid_idx = [i for i, ok in enumerate(is_valid) if ok]
    urls = ["https://api.datacite.org/dois/" + str(cleaned[i]) for i in valid_idx]
    resps = http.batch_query(urls, msg="Querying DataCite") if urls else []

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
                return None  # `return(NULL)` inside tryCatch() leaves datacite_doi()
            value = resp_body_json(resp) if resp_content_type(resp) == "application/json" else None
        except (TypeError, ValueError):
            value = None
        r_list_set(bibdata, i + 1, value)

    if not bibdata:
        return pd.DataFrame()
    return records_frame([_datacite_row(bd) for bd in bibdata])


# ---------------------------------------------------------------------------
# Bibtex types
# ---------------------------------------------------------------------------


def _bibtype_convert(type: Any) -> Any:  # noqa: A002 - R argument name
    """Convert Crossref/DOI types to BibTeX types (port of ``.bibtype_convert()``).

    Unknown types are returned unchanged and ``NA`` stays ``NA``.
    """
    if type is None:
        return None
    if isinstance(type, str):
        return _BIBTYPES.get(type, type)
    try:
        import pandas as pd

        if isinstance(type, pd.Series):
            return pd.Series(
                [None if is_na(t) else _BIBTYPES.get(t, t) for t in type.tolist()],
                index=type.index,
                dtype="string",
            )
    except ImportError:  # pragma: no cover
        pass
    return [None if is_na(t) else _BIBTYPES.get(t, t) for t in type]


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------


def _date_year(parts: Any) -> tuple[bool, Any]:
    """``length(dp) & length(dp[[1]])`` and ``dp[[1]][[1]]`` for ``date-parts``."""
    n = _r_length(parts)
    inner = None if n == 0 else _first(parts)
    if n == 0:
        return False, None
    if _r_length(inner) == 0:
        return False, None
    return True, _first(inner)


def _author_records(authors: Any) -> list[dict[str, Any]]:
    """``lapply(item$author, \\(a) a[c("given", "family", "ORCID")]) |> bind_rows()``."""
    cols = ("given", "family", "ORCID")
    rows: list[dict[str, Any]] = []
    items = authors.values() if isinstance(authors, Mapping) else (authors or [])
    for a in items:
        if not isinstance(a, Mapping):
            continue
        rec = {c: a.get(c) for c in cols if a.get(c) is not None}
        if rec:  # a record with no values adds no row
            rows.append(rec)
    names: dict[str, None] = {}
    for r in rows:
        for k in r:
            names.setdefault(k, None)
    return [{k: r.get(k) for k in names} for r in rows]


def _flatten_df_value(name: str, value: Any) -> list[tuple[str, Any]]:
    """The columns ``data.frame(list(name = value))`` makes of one element."""
    if value is None or (isinstance(value, list | tuple | Mapping) and len(value) == 0):
        raise ValueError("arguments imply differing number of rows: 1, 0")
    if isinstance(value, Mapping):
        out: list[tuple[str, Any]] = []
        for k, v in value.items():
            if isinstance(v, Mapping):
                out.extend(_flatten_df_value(name, v))
            else:
                out.extend(
                    (f"{name}.{k}" if n == k else n, x) for n, x in _flatten_df_value(str(k), v)
                )
        return out
    if isinstance(value, list | tuple):
        out = []
        for v in value:
            if isinstance(v, Mapping):
                out.extend(_flatten_df_value(name, v))
            elif len(value) == 1:
                out.append((_deparse(v), v))
            else:
                out.append((f"{name}.{_deparse(v)}", v))
        return out
    return [(name, value)]


def _parse_item_record(item: Mapping[str, Any], select: Sequence[str]) -> dict[str, Any]:
    """``.crossref_parse_item()`` as one record (column -> value)."""
    item = dict(item)

    title = r_dollar(item, "title")
    if _r_length(title):
        item["title"] = _first(title)
    else:
        item.pop("title", None)

    container = r_dollar(item, "container-title")
    if _r_length(container):
        item["container-title"] = _first(container)
    else:
        item.pop("container-title", None)

    ok, year = _date_year(
        r_dollar(r_dollar(r_dollar(item, "journal-issue"), "published-print"), "date-parts")
    )
    if not ok:
        ok, year = _date_year(r_dollar(r_dollar(item, "published"), "date-parts"))
    if ok:
        if year is None:
            item.pop("year", None)  # `item$year <- NULL`
        else:
            item["year"] = year
    item.pop("published", None)

    authors = _author_records(r_dollar(item, "author"))
    item.pop("author", None)

    to_select = [s for s in dict.fromkeys(select) if s in item]
    record: dict[str, Any] = {}
    for name in to_select:
        for col, value in _flatten_df_value(name, item[name]):
            if col in record:
                raise ValueError("Names must be unique.")
            record[col] = value
    if "author" in select and authors:
        if not record:
            raise ValueError("replacement has 1 row, data has 0")
        record["author"] = authors
    return record


def _crossref_parse_item(item: Mapping[str, Any], select: Sequence[str] = CROSSREF_DOI_SELECT) -> pd.DataFrame:
    """Parse a Crossref work into a one-row data frame (port of ``.crossref_parse_item()``).

    Titles and container titles are reduced to their first element, ``year``
    comes from the print issue date (else the published date), and authors
    become a table (``given``, ``family``, ``ORCID``) in an ``author`` cell.
    """
    return records_frame([_parse_item_record(item, list(select))])


def _query_parse_records(
    items: Any, min_score: float, select: Sequence[str]
) -> list[dict[str, Any]]:
    items = list(items.values()) if isinstance(items, Mapping) else list(items or [])
    scores = [r_dollar(i, "score") if isinstance(i, Mapping) else None for i in items]
    if len(items) == 0 or all(s is not None and s < min_score for s in scores):
        return [{"DOI": None}]
    kept = [i for i, s in zip(items, scores, strict=True) if s is None or s >= min_score]
    records = [_parse_item_record(i, list(select)) for i in kept]
    names: dict[str, None] = {}
    for r in records:
        for k in r:
            names.setdefault(k, None)
    cols = [c for c in dict.fromkeys(select) if c in names]
    return [{c: r.get(c) for c in cols} for r in records]


def _crossref_query_parse(items: Any, min_score: float, select: Sequence[str]) -> pd.DataFrame:
    """Parse Crossref query items into a table (port of ``.crossref_query_parse()``).

    Items scoring below *min_score* are dropped; with none left the result is
    a single row with ``DOI`` = ``NA``.
    """
    return records_frame(_query_parse_records(items, min_score, select))


def _crossref_doi_one(doi: Any, resp: httpx.Response | None, select: Sequence[str]) -> dict[str, Any]:
    try:
        if resp is None:
            raise TypeError("`resp` must be an HTTP response object, not `NULL`.")
        if resp.status_code >= 400:
            return {"DOI": doi, "error": f"HTTP {resp.status_code}"}
        item = resp_body_json(resp)
        status = r_dollar(item, "status")
        if status is None or isinstance(status, list | Mapping):
            raise ValueError("argument is of length zero")
        if status != "ok":
            err = r_dollar(r_dollar(item, "body"), "message-type")
            return {"DOI": doi, "error": "unknown" if err is None else err}
        return _parse_item_record(r_dollar(item, "message"), select)
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        return {"DOI": doi, "error": str(exc)}


def crossref_doi(doi: Any, select: Sequence[str] = CROSSREF_DOI_SELECT) -> pd.DataFrame:
    """Crossref info from DOIs (port of ``crossref_doi()``).

    *doi* may be a DOI, a list of DOIs, or a paper / paper list (their
    ``info$doi``). Queries ``api.labs.crossref.org/works/<doi>`` politely
    (10 requests/s) and returns one row per DOI with the *select* fields;
    malformed DOIs get ``error = "malformed"``, failed lookups an ``error``
    message (e.g. ``"Not Found"`` or ``"HTTP 404"``).
    """
    import pandas as pd

    from pytacheck import http

    if _is_paperish(doi):
        values = _paper_dois(doi)
        if not values:
            return pd.DataFrame()
    else:
        values = as_vector(doi)
        if len(values) == 0:
            return pd.DataFrame()
        if all(is_na(v) for v in values):
            return records_frame([{"DOI": None} for _ in values])

    select = list(select)
    if not _utils.online("api.crossref.org"):
        return records_frame([{"DOI": v, "error": "offline"} for v in values])

    from pytacheck.db.doi import doi_clean, doi_valid_format

    cleaned = doi_clean(list(values))
    valid = doi_valid_format(cleaned)
    valid_idx = [i for i, ok in enumerate(valid) if ok]

    results: list[dict[str, Any]] = [{"DOI": v, "error": "malformed"} for v in values]
    if valid_idx:
        email = default_email()
        urls = [
            f"https://api.labs.crossref.org/works/{url_encode(cleaned[i], reserved=True)}?mailto={email}"
            for i in valid_idx
        ]
        # api.labs.crossref.org single-record lookup: polite pool allows 10 req/s
        resps = http.batch_query(
            urls,
            msg="Querying CrossRef by DOI",
            throttle_capacity=10,
            throttle_fill_time_s=1,
        )
        for j, i in enumerate(valid_idx):
            results[i] = _crossref_doi_one(values[i], resps[j], select)
    for i, v in enumerate(values):
        if is_na(v):
            results[i] = {"DOI": None}
    return records_frame(results)


def _ref_text(row: Mapping[str, Any]) -> str:
    """The ``ref`` text crossref_query() records for a data-frame reference."""
    nonblank = [
        (k, v)
        for k, v in row.items()
        if isinstance(v, list | tuple | Mapping) or not (is_na(v) or v == "")
    ]
    if not nonblank:
        return ""
    if len(nonblank) == 1:
        v = nonblank[0][1]
        vals = unlist(v) if isinstance(v, list | tuple | Mapping) else [v]
        return "; \\n".join("NA" if (s := as_character(x)) is None else s for x in vals)
    parts = []
    for _, v in nonblank:
        if isinstance(v, list | tuple | Mapping):
            parts.append(_deparse(list(v.values()) if isinstance(v, Mapping) else list(v)))
        else:
            s = as_character(v)
            parts.append("NA" if s is None else s)
    return "; \\n".join(parts)


def _query_url(ref: Any, rows: int, email: str) -> str:
    base = f"https://api.crossref.org/works?mailto={email}&rows={int(rows):d}&sort=score"
    if isinstance(ref, Mapping):
        title = _encode_query(ref.get("title"))
        author = _encode_query(ref.get("author"))
        container = _encode_query(ref.get("container"))
        url = base
        if _nzchar(title):
            url = f"{url}&query.title={title}"
        if _nzchar(author):
            url = f"{url}&query.author={author}"
        if _nzchar(container):
            url = f"{url}&query.container-title={container}"
        return url
    return f"{base}&query.bibliographic={_encode_query(ref)}"


def crossref_query(
    ref: Any,
    min_score: float = 50,
    rows: int = 1,
    select: Sequence[str] = CROSSREF_QUERY_SELECT,
) -> pd.DataFrame:
    """Look up references in Crossref (port of ``crossref_query()``).

    *ref* can be a text reference (or a list of them), a data frame of
    references with ``title``, ``authors``/``author`` and
    ``container``/``journal``/``booktitle`` columns (the structured
    ``query.title`` / ``query.author`` / ``query.container-title`` search is
    used), or a paper (its ``bib`` table). Matches scoring below *min_score*
    are dropped; unmatched references get ``DOI`` = ``NA``. Search queries
    are throttled to 3 requests/s (Crossref's polite pool).
    """
    import pandas as pd

    from pytacheck import http
    from pytacheck.papers.model import Paper

    if isinstance(ref, Paper):
        ref = ref.bib
    if ref is None:
        return pd.DataFrame()

    refs: list[Any]
    if isinstance(ref, pd.DataFrame):
        if ref.shape[1] == 0:
            return pd.DataFrame()
        title = _df_dollar(ref, "title")
        author = _df_dollar(ref, "authors")
        if author is None:
            author = _df_dollar(ref, "author")
        container = _df_dollar(ref, "container")
        if container is None:
            container = _df_dollar(ref, "journal")
        if container is None:
            container = _df_dollar(ref, "booktitle")
        cols = {"title": title, "author": author, "container": container}
        cols = {k: v for k, v in cols.items() if v is not None}
        if "title" not in cols:
            return pd.DataFrame()
        refs = [{k: v[i] for k, v in cols.items()} for i in range(len(ref))]
    else:
        refs = as_vector(ref)
        if len(refs) == 0:
            return pd.DataFrame()

    texts = [_ref_text(r) if isinstance(r, Mapping) else r for r in refs]
    if not _utils.online("api.crossref.org"):
        return records_frame([{"bib_text": t, "DOI": None, "error": "offline"} for t in texts])

    email = default_email()
    urls = [_query_url(r, rows, email) for r in refs]
    # api.crossref.org list/search (query.*) endpoint: polite pool allows only 3 req/s
    resps = http.batch_query(
        urls, msg="Querying CrossRef", throttle_capacity=3, throttle_fill_time_s=1
    )

    records: list[dict[str, Any]] = []
    for text, resp in zip(texts, resps, strict=True):
        base = {"ref": None if is_na(text) else as_character(text)}
        try:
            if resp is None or resp.status_code >= 400:
                records.append({**base, "DOI": None, "error": "request failed"})
                continue
            j = resp_body_json(resp)
            status = r_dollar(j, "status")
            if status is None or isinstance(status, list | Mapping):
                raise ValueError("argument is of length zero")
            if status != "ok":
                msg = r_dollar(r_dollar(j, "body"), "message")
                records.append({**base, "DOI": None, "error": "unknown" if msg is None else msg})
                continue
            parsed = _query_parse_records(
                r_dollar(r_dollar(j, "message"), "items"), min_score, list(select)
            )
            records.extend({**p, "ref": base["ref"]} for p in parsed)
        except (TypeError, ValueError, IndexError, AttributeError) as exc:
            records.append({**base, "DOI": None, "error": str(exc)})
    return records_frame(records)


# ---------------------------------------------------------------------------
# add_bib_match
# ---------------------------------------------------------------------------


def _separate_page(page: Any) -> tuple[Any, Any]:
    """``tidyr::separate(page, c("first_page", "last_page"), sep = "-", extra = "merge")``."""
    if page is None or is_na(page):
        return None, None
    s = as_character(page) or ""
    first, sep, rest = s.partition("-")
    return first, (rest if sep else None)


def add_bib_match(paper: Any, min_score: float = 50) -> Any:
    """Add a ``bib_match`` table from Crossref (port of ``add_bib_match()``).

    References with a valid DOI are looked up with :func:`crossref_doi`, the
    others with :func:`crossref_query`; unmatched references are left out.
    Returns a new paper / paper list with the ``bib_match`` table (the input
    is not modified).
    """
    import pandas as pd

    from pytacheck.db.doi import doi_valid_format
    from pytacheck.log import logger
    from pytacheck.papers.model import Paper, PaperList, is_paper_list
    from pytacheck.papers.tables import paper_table

    bib = paper_table(paper, "bib")
    if len(bib) == 0:
        return paper

    def col(name: str) -> list[Any]:
        if name not in bib.columns:
            return [None] * len(bib)
        return [None if (not isinstance(v, list | tuple) and is_na(v)) else v for v in bib[name].tolist()]

    dois = col("doi")
    has_doi = [v is True for v in doi_valid_format(dois)]
    paper_ids = col("paper_id")
    bib_ids = col("bib_id")

    parts: list[pd.DataFrame] = []
    if any(has_doi):
        cr_doi = crossref_doi([d for d, h in zip(dois, has_doi, strict=True) if h])
        cr_doi = cr_doi.copy()
        cr_doi["paper_id"] = [p for p, h in zip(paper_ids, has_doi, strict=True) if h]
        cr_doi["bib_id"] = [b for b, h in zip(bib_ids, has_doi, strict=True) if h]
        parts.append(cr_doi)
    if not all(has_doi):
        no = [not h for h in has_doi]
        cr_no = crossref_query(bib.loc[no].reset_index(drop=True), min_score=min_score)
        cr_no = cr_no.drop(columns=["ref"], errors="ignore").copy()
        cr_no["paper_id"] = [p for p, n in zip(paper_ids, no, strict=True) if n]
        cr_no["bib_id"] = [b for b, n in zip(bib_ids, no, strict=True) if n]
        parts.append(cr_no)

    from pytacheck._r.frames import bind_rows

    cr_data = bind_rows(parts)
    n = len(cr_data)

    def get(name: str) -> list[Any]:
        if name not in cr_data.columns:
            return [None] * n
        return [
            None if (not isinstance(v, list | tuple) and is_na(v)) else v
            for v in cr_data[name].tolist()
        ]

    if "error" in cr_data.columns:
        errors = get("error")
        from pytacheck._r.regex import grepl

        net = grepl(
            "offline|connection|timeout|failed|http 5",
            [None if e is None else str(e).lower() for e in errors],
        )
        n_net = sum(bool(x) for x in net)
        if n_net > 0:
            msg = (
                f"{n_net} of {n} reference lookups did not complete (network error); "
                "the bib_match table may be incomplete. Re-run add_bib_match() with a "
                "stable connection for full data."
            )
            import warnings

            warnings.warn(msg, stacklevel=2)
            logger("add_bib_match", {"network_errors": n_net, "total": n, "msg": msg})

    if "page" in cr_data.columns:
        pages = [_separate_page(p) for p in get("page")]
        first_page = [p[0] for p in pages]
        last_page = [p[1] for p in pages]
    else:
        first_page = get("first_page")
        last_page = get("last_page")

    authors: list[list[dict[str, Any]]] = []
    for a in get("author"):
        if not a:
            authors.append([])
            continue
        recs = list(a) if isinstance(a, list | tuple) else a.to_dict("records")
        authors.append([{"given": r.get("given"), "family": r.get("family")} for r in recs])

    types = get("type")
    years = get("year")
    scores = get("score")
    bib_match = pd.DataFrame(
        {
            "paper_id": pd.array(get("paper_id"), dtype="string"),
            "bib_id": pd.array(get("bib_id"), dtype="Int64"),
            "service": pd.array(["crossref"] * n, dtype="string"),
            "service_id": pd.array([None] * n, dtype="string"),
            "score": pd.array([float("nan") if s is None else float(s) for s in scores], dtype="float64"),
            "bib_type": pd.array(_bibtype_convert(types), dtype="string"),
            "doi": pd.array(_str_list(get("DOI")), dtype="string"),
            "title": pd.array(_str_list(get("title")), dtype="string"),
            "authors": pd.Series(authors, dtype=object).array,
            "editors": pd.Series([[] for _ in range(n)], dtype=object).array,
            "publisher": pd.array(_str_list(get("publisher")), dtype="string"),
            "year": pd.array([None if y is None else int(y) for y in years], dtype="Int64"),
            "date": pd.array([None] * n, dtype="string"),
            "container": pd.array(_str_list(get("container-title")), dtype="string"),
            "volume": pd.array(_str_list(get("volume")), dtype="string"),
            "issue": pd.array(_str_list(get("issue")), dtype="string"),
            "first_page": pd.array(_str_list(first_page), dtype="string"),
            "last_page": pd.array(_str_list(last_page), dtype="string"),
            "edition": pd.array([None] * n, dtype="string"),
            "version": pd.array([None] * n, dtype="string"),
            "url": pd.array(_str_list(get("URL")), dtype="string"),
        },
        index=pd.RangeIndex(n),
    )

    # remove unfound, arrange(paper_id, bib_id)
    table = bib_match[bib_match["title"].notna()]
    table = table.sort_values(
        ["paper_id", "bib_id"], kind="stable", na_position="last"
    ).reset_index(drop=True)

    if isinstance(paper, Paper):
        out = paper.copy(deep=False)
        out.bib_match = table.drop(columns=["paper_id"])
        return out
    if is_paper_list(paper):
        papers = paper.values() if isinstance(paper, Mapping) else paper
        new = []
        for p in papers:
            sub = table[table["paper_id"] == p.paper_id].drop(columns=["paper_id"])
            q = p.copy(deep=False)
            q.bib_match = sub.reset_index(drop=True)
            new.append(q)
        return PaperList(new)
    return paper


def _str_list(values: Sequence[Any]) -> list[str | None]:
    return [None if v is None else (v if isinstance(v, str) else as_character(v)) for v in values]


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def _openalex_add_abstract(info: Any) -> Any:
    """Add ``abstract`` from ``abstract_inverted_index`` (port of ``.openalex_add_abstract()``)."""
    aii = r_dollar(info, "abstract_inverted_index")
    if aii is None:
        return info
    pairs: list[tuple[Any, str]] = []
    if isinstance(aii, Mapping):
        for word, positions in aii.items():
            for pos in unlist(positions):
                pairs.append((pos, str(word)))
    pairs.sort(key=lambda p: p[0])
    out = dict(info)
    out["abstract"] = " ".join(w for _, w in pairs)
    return out


def openalex_doi(doi: Any, select: Sequence[str] | None = None) -> Any:
    """OpenAlex info from DOIs (port of ``openalex_doi()``).

    Returns a list with one entry (the OpenAlex work record, with an
    ``abstract`` rebuilt from the inverted index) per DOI; missing DOIs give
    ``{"DOI": NA}`` and malformed ones ``{"DOI": doi, "error": "malformed"}``.
    As in metacheck, *select* is accepted but not used, and a DOI that
    OpenAlex does not know (HTTP error) makes the call return just
    ``{"DOI": doi, "error": "not found"}`` for that DOI.
    """
    from pytacheck import http

    del select  # documented by metacheck but never applied
    if _is_paperish(doi):
        values = _paper_dois(doi)
        if not values:
            return []
    else:
        values = as_vector(doi)
        if len(values) == 0:
            return []
        if all(is_na(v) for v in values):
            return {"DOI": values if len(values) != 1 else values[0]}

    if not _utils.online("api.openalex.org"):
        return {"DOI": values if len(values) != 1 else values[0], "error": "offline"}

    from pytacheck.db.doi import doi_clean, doi_valid_format

    cleaned = doi_clean(list(values))
    fmt = doi_valid_format(cleaned)
    valid = [not is_na(v) and ok for v, ok in zip(values, fmt, strict=True)]
    valid_idx = [i for i, ok in enumerate(valid) if ok]
    resps: list[Any] = []
    if valid_idx:
        email = default_email()
        urls = [
            "https://api.openalex.org/works/https://doi.org/"
            f"{url_encode(cleaned[i], reserved=True)}?mailto={email}"
            for i in valid_idx
        ]
        resps = http.batch_query(urls, msg="Querying OpenAlex by DOI")

    oa: list[Any] = [None] * len(values)
    for i, v in enumerate(values):
        if is_na(v):
            oa[i] = {"DOI": None}
        elif not valid[i]:
            oa[i] = {"DOI": v, "error": "malformed"}
    for j, i in enumerate(valid_idx):
        resp = resps[j]
        try:
            if resp is None:
                raise TypeError("`resp` must be an HTTP response object, not `NULL`.")
            if resp.status_code >= 400:
                # `return(...)` inside tryCatch() leaves openalex_doi()
                return {"DOI": values[i], "error": "not found"}
            value = _openalex_add_abstract(resp_body_json(resp))
        except (TypeError, ValueError):
            value = {"DOI": values[i], "error": "not found"}
        r_list_set(oa, i + 1, value)
    return oa


def _unlist_named(x: Mapping[str, Any]) -> dict[str, str | None]:
    """``unlist(x)`` of a named list as ``{name: value}``, values coerced like R."""
    leaves: list[tuple[str, Any]] = []

    def walk(name: str, v: Any) -> None:
        if v is None:
            return
        if isinstance(v, Mapping):
            for k, sub in v.items():
                walk(f"{name}.{k}" if name else str(k), sub)
        elif isinstance(v, list | tuple):
            flat = [e for e in v if e is not None]
            if len(flat) == 1 and not isinstance(flat[0], list | tuple | Mapping):
                walk(name, flat[0])
            else:
                for idx, e in enumerate(flat, start=1):
                    walk(name if isinstance(e, Mapping) else f"{name}{idx}", e)
        else:
            leaves.append((name, v))

    for k, v in x.items():
        walk(str(k), v)
    out: dict[str, str | None] = {}
    for name, v in leaves:
        out.setdefault(name, as_character(v))
    return out


def _openalex_request(url: str) -> Any:
    from pytacheck import http

    resp = http.request("GET", url, headers={"Accept": "application/json"})
    if resp is None:
        return "offline"
    try:
        return resp_body_json(resp)
    except (TypeError, ValueError):
        return "error"


def openalex_query(
    title: str, source: Any = None, authors: Any = None, strict: bool = True
) -> pd.DataFrame | None:
    """Look up a reference in OpenAlex by title (port of ``openalex_query()``).

    Returns the single work whose title (and *source*, the journal or book)
    match exactly, as a one-row data frame; ``None`` when nothing matches or
    the match is ambiguous and *strict* is true (with ``strict=False`` the
    best candidate is returned instead). Titles with a colon are retried
    with the part before the colon when the full title finds nothing.
    """
    import pandas as pd

    del authors  # TODO upstream: fuzzy match authors
    fields = ",".join(
        [
            "id",
            "doi",
            "relevance_score",
            "display_name",
            "publication_year",
            "primary_location",
            "authorships",
            "type",
            "biblio",
        ]
    )
    url = (
        "https://api.openalex.org/works?filter=title.search:"
        + url_encode(title.replace(",", ""))
        + "&mailto="
        + default_email()
        + "&select="
        + fields
    )
    j = _openalex_request(url)
    if isinstance(j, str):
        return None

    results = r_dollar(j, "results")
    if results is None or len(results) == 0:
        if ":" in title:
            maintitle = title.split(":", 1)[0]
            return openalex_query(maintitle, source, None, strict)
        return None

    rows: list[dict[str, str | None]] = []
    for res in results:
        res = dict(res)
        display = r_dollar(r_dollar(r_dollar(res, "primary_location"), "source"), "display_name")
        if display is not None:
            res["source"] = display
        res.pop("primary_location", None)
        authorships = r_dollar(res, "authorships")
        names = [r_dollar(a, "raw_author_name") for a in (authorships or [])]
        if all(isinstance(nm, str | int | float | bool) for nm in names):
            res["authors"] = "; ".join(as_character(nm) or "NA" for nm in names)
        else:  # sapply() gave a list: paste() deparses NULLs as "NULL"
            res["authors"] = "; ".join(
                "NULL" if nm is None else (as_character(nm) or "NA") for nm in names
            )
        res.pop("authorships", None)
        rows.append(_unlist_named(res))

    info = records_frame(rows)
    if "relevance_score" in info.columns:
        # arrange(desc(relevance_score)) on the character column unlist() made:
        # string order, NA last, ties stable
        scores = info["relevance_score"].tolist()
        present = [i for i in range(len(info)) if not is_na(scores[i])]
        missing = [i for i in range(len(info)) if is_na(scores[i])]
        present = sorted(present, key=lambda i: scores[i], reverse=True)
        info = info.iloc[present + missing].reset_index(drop=True)

    for rq in ("display_name", "source"):
        if rq not in info.columns:
            info[rq] = pd.array([""] * len(info), dtype="string")

    def lower(values: Sequence[Any]) -> list[str | None]:
        return [None if is_na(v) else str(v).lower() for v in values]

    t = title.lower()
    s = None if source is None or is_na(source) else str(source).lower()
    title_match = [None if v is None else v == t for v in lower(info["display_name"].tolist())]
    source_match = [
        None if (v is None or s is None) else v == s for v in lower(info["source"].tolist())
    ]
    info["title_match"] = pd.array(title_match, dtype="boolean")
    info["source_match"] = pd.array(source_match, dtype="boolean")

    both = [bool(a) and bool(b) for a, b in zip(title_match, source_match, strict=True)]
    matches = info[both].reset_index(drop=True)
    if len(matches) == 1:
        return matches
    if len(matches) > 1:
        return None if strict else matches.iloc[:1]

    only_title = [a is True for a in title_match]
    matches = info[only_title].reset_index(drop=True)
    if len(matches) >= 1:
        return None if strict else matches.iloc[:1]
    return None if strict else info.iloc[:1]
