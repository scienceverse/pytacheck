"""bibr export schema 12.x: read and write (port of ``R/import-bibr12.R``).

bibr export schema 12.0 is the schema pytacheck targets. A JSON file with a
root ``schema_version`` is read here; any version other than 12.x stops with
metacheck's error (so bibr 11.x files are refused, as in metacheck), and files
without a root ``schema_version`` (bibr v10.x and older, metacheck's demo and
fixture papers) are read by the older reader exactly as before.

A paper read from a 12.x file keeps metacheck's own names where it has them --
the 12.x ``metadata`` and ``source`` objects make the ``info`` table and
``metadata_match`` is ``info_match`` -- and the 12.x names and meanings
everywhere else: ``xref_id`` is the row's own key and ``target_id`` the row it
cites, and a caption or footnote is a text row with no section that
``figure``, ``table`` or ``footnote`` points at with ``text_id``. The export's
``extraction`` block is kept as ``paper["extraction"]``. Degrees of freedom are
kept in their parentheses (``"(28)"``), as metacheck's statistics code
expects; 12.x writes them bare (``"28"``).

:func:`_paper_to_bibr12` and :func:`_bibr12_json` write a paper back as a 12.0
file, byte for byte as metacheck's ``paper_write(schema_version = "12.0")``
does (``jsonlite::write_json(auto_unbox = TRUE, pretty = TRUE, digits = NA)``),
except that pytacheck names itself, not metacheck, as the ``converter``, and
writes a double that 15 significant digits would change in full (U22).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import pandas as pd

from pytacheck._r.base import as_character, trimws
from pytacheck._r.regex import grepl, is_na, sub
from pytacheck.papers.io import _field
from pytacheck.papers.model import Paper
from pytacheck.papers.schema import (
    SCHEMA_DTYPES,
    coerce_column,
    infer_column,
    load_schema_bibr12,
    records_to_frame,
)

__all__ = [
    "BIBR12_COLS",
    "BIBR12_TABLES",
    "bibr12_json",
    "is_bibr12",
    "paper_to_bibr12",
    "read_bibr12",
    "write_bibr12",
]

# The columns of each 12.0 table, in the order bibr writes them, and their
# types: chr, int, num and lgl are scalars; chr[] and int[] are arrays;
# chr[][] is an array of arrays of strings (table contents); json is kept as
# parsed (the person and funder objects of the match tables).
_MATCH_COLS: dict[str, str] = {
    "service": "chr",
    "service_id": "chr",
    "score": "num",
    "bib_type": "chr",
    "doi": "chr",
    "title": "chr",
    "author": "json",
    "editor": "json",
    "publisher": "chr",
    "year": "int",
    "published_date": "chr",
    "container": "chr",
    "volume": "chr",
    "issue": "chr",
    "first_page": "chr",
    "last_page": "chr",
    "edition": "chr",
    "version": "chr",
    "url": "chr",
    "license_url": "chr",
    "license_spdx": "chr",
    "funder": "json",
}

BIBR12_COLS: dict[str, dict[str, str]] = {
    "metadata": {
        "title": "chr",
        "abstract": "chr",
        "keywords": "chr[]",
        "doi": "chr",
        "pmid": "chr",
        "pmcid": "chr",
        "arxiv": "chr",
        "language": "chr",
        "paper_type": "chr",
        "oecd_l1": "chr",
        "oecd_l2": "chr",
        "journal": "chr",
        "volume": "chr",
        "issue": "chr",
        "first_page": "chr",
        "last_page": "chr",
        "issn": "chr",
        "publisher": "chr",
        "published": "chr",
        "published_date": "chr",
        "license": "chr",
        "license_url": "chr",
        "license_spdx": "chr",
        "funding_statement": "chr",
        "coi_statement": "chr",
        "ethics_statement": "chr",
        "data_availability": "chr",
    },
    "author": {
        "author_id": "int",
        "given": "chr",
        "family": "chr",
        "suffix": "chr",
        "literal": "chr",
        "email": "chr",
        "corresponding": "lgl",
        "orcid": "chr",
        "role": "chr[]",
        "credit_roles": "chr[]",
    },
    "affiliation": {
        "affiliation_id": "int",
        "text": "chr",
        "institution": "chr",
        "department": "chr",
        "city": "chr",
        "country": "chr",
        "author_ids": "int[]",
    },
    "funding": {"funding_id": "int", "funder": "chr", "award_ids": "chr[]"},
    "text": {
        "text": "chr",
        "text_id": "int",
        "paragraph_id": "int",
        "section_id": "int",
        "page_number": "int",
        "formatted": "chr",
    },
    "section": {
        "section_id": "int",
        "header": "chr",
        "level": "int",
        "parent_section_id": "int",
        "section_type": "chr",
    },
    "url": {
        "url_id": "int",
        "href": "chr",
        "link_text": "chr",
        "text_id": "int",
        "start": "int",
        "end": "int",
    },
    "bib": {
        "bib_id": "int",
        "text_id": "int",
        "bib_type": "chr",
        "doi": "chr",
        "title": "chr",
        "authors": "chr",
        "editors": "chr",
        "publisher": "chr",
        "year": "int",
        "year_suffix": "chr",
        "date": "chr",
        "published_date": "chr",
        "container": "chr",
        "volume": "chr",
        "issue": "chr",
        "first_page": "chr",
        "last_page": "chr",
        "edition": "chr",
        "version": "chr",
        "url": "chr",
        "is_in_press": "lgl",
        "arxiv": "chr",
        "pmid": "chr",
        "series": "chr",
        "access_date": "chr",
        "note": "chr",
    },
    "xref": {
        "xref_id": "int",
        "target_id": "int",
        "xref_type": "chr",
        "contents": "chr",
        "text_id": "int",
        "start": "int",
        "end": "int",
    },
    "figure": {
        "figure_id": "int",
        "label": "chr",
        "section_id": "int",
        "text_id": "int",
        "image": "chr",
        "caption": "chr",
        "page_number": "int",
    },
    "table": {
        "table_id": "int",
        "label": "chr",
        "section_id": "int",
        "text_id": "int",
        "html": "chr",
        "contents": "chr[][]",
        "caption": "chr",
        "page_number": "int",
    },
    "footnote": {"footnote_id": "int", "label": "chr", "text_id": "int"},
    "eq": {
        "eq_id": "int",
        "text_id": "int",
        "start": "int",
        "end": "int",
        "grp_id": "int",
        "verbatim": "chr",
        "lhs": "chr",
        "df": "chr",
        "comp": "chr",
        "rhs": "chr",
    },
    "metadata_match": _MATCH_COLS,
    "affiliation_match": {
        "affiliation_id": "int",
        "service": "chr",
        "service_id": "chr",
        "score": "num",
        "name": "chr",
        "country_code": "chr",
    },
    "funding_match": {
        "funding_id": "int",
        "service": "chr",
        "service_id": "chr",
        "score": "num",
        "name": "chr",
        "country_code": "chr",
        "funder_doi": "chr",
    },
    "bib_match": {"bib_id": "int", **_MATCH_COLS},
}

# 12.0 record tables (keys) and the paper table that holds each (values)
BIBR12_TABLES: dict[str, str] = {
    "author": "author",
    "affiliation": "affiliation",
    "funding": "funding",
    "text": "text",
    "section": "section",
    "url": "url",
    "bib": "bib",
    "xref": "xref",
    "figure": "figure",
    "table": "table",
    "footnote": "footnote",
    "eq": "eq",
    "metadata_match": "info_match",
    "affiliation_match": "affiliation_match",
    "funding_match": "funding_match",
    "bib_match": "bib_match",
}

# the extraction keys 12.0 defines; other keys are not written
_EXTRACTION_KEYS = (
    "producer",
    "converter",
    "completed_at",
    "ocr",
    "llm",
    "settings",
    "timings",
    "usage",
    "identity",
    "enrichment",
    "diagnostics",
    "validation",
    "qualification",
    "warnings",
    "pages",
    "float_parts",
    "text_regions",
    "regions",
    "trace",
)

# the order 12.0 writes its record tables in
_WRITE_ORDER = (
    "author",
    "affiliation",
    "funding",
    "text",
    "section",
    "url",
    "bib",
    "xref",
    "figure",
    "table",
    "footnote",
    "eq",
    "metadata_match",
    "affiliation_match",
    "funding_match",
    "bib_match",
)

_SCALAR_SCHEMA = {"chr": "string", "int": "integer", "num": "number", "lgl": "boolean"}
_INT_MAX = 2147483647


def _paper_schema_bibr12() -> dict[str, Any]:
    """Port of ``R/import-bibr12.R::.paper_schema_bibr12()`` (see :mod:`pytacheck.papers.schema`)."""
    return load_schema_bibr12()


# ---------------------------------------------------------------------------
# which papers are 12.x papers
# ---------------------------------------------------------------------------


def is_bibr12(paper: Any) -> bool:
    """Port of ``R/import-bibr12.R::.is_bibr12()``.

    Whether *paper* is in bibr export schema 12.x form: its ``info`` table's
    ``schema_version`` starts with ``12.``.
    """
    info = paper.get("info") if isinstance(paper, Paper) else None
    if not isinstance(info, pd.DataFrame) or "schema_version" not in info.columns or not len(info):
        return False
    version = as_character(info["schema_version"].iloc[0])
    return version is not None and version.startswith("12.")


_is_bibr12 = is_bibr12


def _bibr12_paper_ids(paper: Any) -> list[str]:
    """Port of ``R/import-bibr12.R::.bibr12_paper_ids()``: IDs of the 12.x papers."""
    papers: Any = [paper] if isinstance(paper, Paper) else paper
    if isinstance(papers, Mapping):
        papers = list(papers.values())
    ids = [as_character(p.paper_id) if is_bibr12(p) else None for p in papers]
    return [i for i in ids if i is not None]


# ---------------------------------------------------------------------------
# JSON values as R sees them (jsonlite::read_json(simplifyVector = FALSE))
# ---------------------------------------------------------------------------


def _jnum(v: Any) -> Any:
    """jsonlite reads an integer beyond R's integer range as a double."""
    if type(v) is int and not -_INT_MAX <= v <= _INT_MAX:
        return float(v)
    return v


def _first(e: Any) -> Any:
    """``if (length(e) == 0) NA else e[[1]]`` for one parsed JSON value."""
    if isinstance(e, list):
        e = e[0] if e else None
    elif isinstance(e, dict):
        e = next(iter(e.values())) if e else None
    return _jnum(e)


def _flatten(e: Any, out: list[Any]) -> None:
    """``unlist()``: the scalars of a nested value, dropping ``NULL``."""
    if e is None:
        return
    if isinstance(e, list | tuple):
        for v in e:
            _flatten(v, out)
    elif isinstance(e, dict):
        for v in e.values():
            _flatten(v, out)
    elif isinstance(e, np.ndarray):
        for v in e.tolist():
            _flatten(v, out)
    elif isinstance(e, pd.Series):
        for v in e.tolist():
            _flatten(None if is_na(v) else v, out)
    else:
        out.append(_jnum(e))


def _unlist_as(e: Any, schema_type: str) -> list[Any]:
    """``as.character(unlist(e))`` / ``as.integer(unlist(e))`` as a Python list."""
    vals: list[Any] = []
    _flatten(e, vals)
    if schema_type == "string" and all(type(v) is str for v in vals):
        return vals
    if schema_type == "integer" and all(type(v) is int for v in vals):
        return vals
    series = coerce_column(infer_column(vals), schema_type)
    return [None if is_na(v) else v for v in series.tolist()]


def _cell(e: Any, typ: str) -> Any:
    """One row's value of a column of type *typ*, normalised as ``.bibr12_df()`` does.

    Scalar columns keep the first value (their type is set per column, as
    ``unlist()`` and ``as.character()`` etc. do); array columns hold Python
    lists; ``json`` values are kept as parsed.
    """
    if typ in _SCALAR_SCHEMA:
        return _first(e)
    if typ == "chr[]":
        return _unlist_as(e, "string")
    if typ == "int[]":
        return _unlist_as(e, "integer")
    if typ == "chr[][]":
        if e is None:
            return []
        if isinstance(e, dict):
            # lapply() over a named list keeps its names: written back as an object
            return {k: _unlist_as(r, "string") for k, r in e.items()}
        rows = e if isinstance(e, list | tuple) else [e]
        return [_unlist_as(r, "string") for r in rows]
    return e  # json


# ---------------------------------------------------------------------------
# building 12.x tables
# ---------------------------------------------------------------------------


def _bibr12_rows(rows: Any, cols: Mapping[str, str]) -> dict[str, list[Any]]:
    """Port of ``R/import-bibr12.R::.bibr12_rows()``.

    The columns of a JSON array of objects: one list per column, ``None`` for
    a missing value.
    """
    records = _as_rows(rows)
    return {col: [_r_index(r, col) for r in records] for col in cols}


def _as_rows(rows: Any) -> list[Any]:
    """The elements ``lapply(rows, ...)`` runs over: an array's, an object's values, a scalar."""
    if rows is None:
        return []
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        return list(rows.values())
    return [rows]


def _r_index(r: Any, col: str) -> Any:
    """``r[[col]]`` on one parsed JSON row: NULL for null, an array or a missing key.

    A scalar row (a string, number or boolean) stops, as in R.
    """
    if isinstance(r, Mapping):
        return r.get(col)
    if r is None or isinstance(r, list):
        return None
    raise ValueError("subscript out of bounds")


def _flat_first(e: Any) -> list[Any]:
    """What ``unlist(lapply(v, \\(e) if (length(e) == 0) NA else e[[1]]))`` keeps of one value.

    One value for a scalar, ``null``, ``[]`` or ``{}`` (NA), or an array whose
    first element is a scalar; none when that element is null; every scalar
    of it when it is an array or object (``unlist()`` flattens it).
    """
    if isinstance(e, list):
        if not e:
            return [None]
        f = e[0]
    elif isinstance(e, dict):
        if not e:
            return [None]
        f = next(iter(e.values()))
    else:
        return [_jnum(e)]
    if f is None:
        return []
    if isinstance(f, list | dict):
        out: list[Any] = []
        _flatten(f, out)
        return out
    return [_jnum(f)]


def _as_character_list(v: Any) -> list[str | None]:
    """``as.character(v)`` of a parsed JSON value (``character(0)`` for NULL)."""
    if v is None:
        return []
    if isinstance(v, list | dict):
        out: list[str | None] = []
        for e in v.values() if isinstance(v, dict) else v:
            if e is None:
                out.append("NULL")
            elif isinstance(e, list | dict):
                out.append(orjson.dumps(e).decode())  # R deparses it; not a value bibr writes
            else:
                out.append(as_character(_jnum(e)))
        return out
    return [as_character(_jnum(v))]


def _dollar_atomic(x: Any, name: str) -> Any:
    """``x$name``, which stops for a scalar (an atomic vector in R)."""
    if x is not None and not isinstance(x, list | Mapping):
        raise ValueError("$ operator is invalid for atomic vectors")
    return _field(x, name)


def _is_list_column(v: Any) -> bool:
    """R ``is.list()`` of a column: a list, or an object Series of list-like cells."""
    if isinstance(v, list):
        return True
    if isinstance(v, pd.Series) and v.dtype == object:
        return any(isinstance(e, list | tuple | dict | np.ndarray | pd.DataFrame) for e in v)
    return False


def _length(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, pd.Series | list | tuple | np.ndarray):
        return len(v)
    return 1


def _column_values(v: Any) -> list[Any]:
    """The cells of a column, with missing scalars as ``None``."""
    if isinstance(v, pd.Series | np.ndarray):
        v = v.tolist()
    elif not isinstance(v, list | tuple):
        v = [v]
    return [None if _scalar_na(e) else e for e in v]


def _bibr12_columns(
    columns: Mapping[str, Any], cols: Mapping[str, str], n: int
) -> dict[str, list[Any]]:
    """The typed columns of ``.bibr12_df()``, as lists (``None`` for NA).

    Scalar columns hold values of the column's type (converted as ``unlist()``
    and ``as.character()`` etc. do); array columns hold lists; ``json``
    columns the values as they are.
    """
    out: dict[str, list[Any]] = {}
    for col, typ in cols.items():
        v = columns.get(col) if col in columns else None
        if typ in _SCALAR_SCHEMA:
            schema_type = _SCALAR_SCHEMA[typ]
            if isinstance(v, pd.Series) and str(v.dtype) == str(SCHEMA_DTYPES[schema_type]):
                vals: list[Any] = v.tolist()  # already the column's type
            else:
                if v is None:
                    vals = [None] * n
                elif _is_list_column(v):
                    vals = [_first(e) if isinstance(e, list | dict) else _first_any(e) for e in v]
                else:
                    vals = _column_values(v)
                vals = coerce_column(infer_column(vals), schema_type).tolist()
            out[col] = [None if e is None or e is pd.NA or e != e else e for e in vals]
            continue
        if typ in ("chr[]", "int[]", "chr[][]"):
            out[col] = _array_cells(v, n, typ)
            continue
        cells: list[Any] = [None] * n if v is None else _column_values(v)
        out[col] = [None if _scalar_na(e) else e for e in cells]
    return out


def _array_cells(v: Any, n: int, typ: str) -> list[Any]:
    """The cells of an array column, as ``lapply(v, \\(e) as.character(unlist(e)))`` makes them.

    ``unlist()`` drops ``NULL`` but keeps ``NA``. A Series without list cells
    is an atomic vector, so its missing cells are ``[NA]`` (``[[NA]]`` for
    ``chr[][]``); in a list column (a Series with list cells, or a plain
    Python list of cells) ``None`` is ``NULL`` (``[]``) and ``pd.NA`` / NaN
    (what pandas fills in when rows are added) is ``NA``, as in metacheck.
    """
    if v is None:
        return [_cell(None, typ) for _ in range(n)]
    atomic = isinstance(v, pd.Series | np.ndarray) and (
        v.dtype != object or not _is_list_column(pd.Series(v) if isinstance(v, np.ndarray) else v)
    )
    if isinstance(v, pd.Series | np.ndarray):
        values = v.tolist()
    elif isinstance(v, list | tuple):
        values = list(v)
    else:
        values = [v]
    out = []
    for e in values:
        if not isinstance(e, list | tuple | dict | np.ndarray | pd.DataFrame | pd.Series) and (
            (atomic and is_na(e)) or (e is not None and is_na(e))
        ):
            out.append([[None]] if typ == "chr[][]" else [None])
        else:
            out.append(_cell_na(_to_json_value(e), typ))
    return out


def _flatten_na(e: Any, out: list[Any]) -> None:
    """``unlist()`` of a paper's array cell: its scalars, missing values kept as NA.

    A cell read from JSON is already normalised (its nulls dropped), so a
    ``None`` left in it is an NA, e.g. ``as.integer("x")``.
    """
    if isinstance(e, list | tuple):
        for v in e:
            _flatten_na(v, out)
    elif isinstance(e, dict):
        for v in e.values():
            _flatten_na(v, out)
    else:
        out.append(None if _scalar_na(e) else _jnum(e))


def _unlist_na(e: Any, schema_type: str) -> list[Any]:
    vals: list[Any] = []
    _flatten_na(e, vals)
    if schema_type == "string" and all(type(v) is str for v in vals):
        return vals
    if schema_type == "integer" and all(type(v) is int for v in vals):
        return vals
    series = coerce_column(infer_column(vals), schema_type)
    return [None if is_na(v) else v for v in series.tolist()]


def _cell_na(e: Any, typ: str) -> Any:
    """An array cell of a paper's table as ``.bibr12_df()`` makes it (see :func:`_flatten_na`)."""
    if e is None:
        return [] if typ != "json" else None
    if typ == "chr[]":
        return _unlist_na(e, "string")
    if typ == "int[]":
        return _unlist_na(e, "integer")
    if isinstance(e, dict):
        return {k: _unlist_na(r, "string") for k, r in e.items()}
    rows = e if isinstance(e, list | tuple) else [e]
    return [_unlist_na(r, "string") for r in rows]


def _scalar_na(e: Any) -> bool:
    return not isinstance(e, list | tuple | dict | np.ndarray | pd.DataFrame) and is_na(e)


def _first_any(e: Any) -> Any:
    """``e[[1]]`` for a list-column cell that may be a tuple, array or data frame."""
    if isinstance(e, tuple | np.ndarray):
        return _jnum(e[0]) if len(e) else None
    if isinstance(e, pd.DataFrame):
        return _jnum(e.iloc[0, 0]) if e.shape[1] and len(e) else None
    return None if _scalar_na(e) else _jnum(e)


def _to_json_value(e: Any) -> Any:
    """A cell as parsed JSON (tuples, arrays and data frames as lists)."""
    if isinstance(e, tuple | np.ndarray):
        return [_to_json_value(v) for v in list(e)]
    if isinstance(e, pd.Series):
        return [None if is_na(v) else v for v in e.tolist()]
    if isinstance(e, pd.DataFrame):
        return [
            {k: (None if _scalar_na(v) else v) for k, v in r.items()} for r in e.to_dict("records")
        ]
    if _scalar_na(e):
        return None
    return e


def _bibr12_df(
    columns: Mapping[str, Any], cols: Mapping[str, str], n: int | None = None
) -> pd.DataFrame:
    """Port of ``R/import-bibr12.R::.bibr12_df()``: a typed data frame for one 12.0 table.

    *columns* holds vectors (Series or lists), or lists with one element per
    row (``None`` for a missing value); a missing column is all NA. The result
    has the columns of *cols*, in order.
    """
    if n is None:
        n = max([0, *(_length(v) for v in columns.values())])
    data = _bibr12_columns(columns, cols, n)
    frame: dict[str, Any] = {}
    for col, typ in cols.items():
        dtype = SCHEMA_DTYPES[_SCALAR_SCHEMA[typ]] if typ in _SCALAR_SCHEMA else object
        frame[col] = pd.Series(data[col], dtype=dtype)
    if not frame:
        return pd.DataFrame(index=range(n))
    return pd.DataFrame(frame)


def _bibr12_records(
    rows: Any, cols: Mapping[str, str], drop: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """The rows of one 12.0 table as normalised records (for lazy materialisation).

    ``records_to_frame()`` builds from these records the data frame
    ``.bibr12_df(.bibr12_rows(rows, cols), cols)`` makes, coerced by
    ``.paper_coerce()``: every 12.0 column is in the merged paper schema with
    the same type. Columns in *drop* are all NA. A row whose values need no
    normalisation is kept as parsed (keys 12.0 does not define are never read).

    A scalar column holding an array or object (which bibr does not write)
    keeps its first value, NA for ``null``, ``[]`` or ``[null]``, row by row.
    metacheck ``unlist()``s the column, so such a value moves the values of
    the rows after it to the rows before them, is recycled across the table,
    or fails the paper ("replacement has 2 rows, data has 3"; U24).
    """
    records = _as_rows(rows)
    for row in records:
        _r_index(row, "")  # a scalar row stops, as .bibr12_rows() does
    scalar = [col for col, typ in cols.items() if typ in _SCALAR_SCHEMA and col not in drop]
    special = [(col, typ) for col, typ in cols.items() if typ in ("chr[]", "int[]", "chr[][]")]
    out = []
    for row in records:
        r: dict[str, Any] = row if isinstance(row, dict) else {}
        rec: dict[str, Any] | None = None
        for col in scalar:
            v: Any = r.get(col)
            t = type(v)
            if v is None or t is str or t is float or t is bool:
                continue
            if t is int and -_INT_MAX <= v <= _INT_MAX:
                continue
            if rec is None:
                rec = dict(r)
            rec[col] = _first_value(v)
        for col, typ in special:
            if rec is None:
                rec = dict(r)
            rec[col] = _cell(r.get(col), typ)
        for col in drop:
            if rec is None:
                rec = dict(r)
            rec[col] = None
        out.append(r if rec is None else rec)
    return out


def _first_value(e: Any) -> Any:
    """A scalar field's value: the first value of an array or object (``None`` for none).

    What ``unlist(if (length(e) == 0) NA else e[[1]])`` gives when that is one
    value (``[{"given": "A"}]`` is ``"A"``); an array or object as first
    element gives its first value, and ``[null]`` gives ``None`` (see
    :func:`_bibr12_records`).
    """
    flat = _flat_first(e)
    return flat[0] if flat else None


def _chr_first(v: Any) -> str | None:
    """``as.character()`` of a field's value (its first value; ``None`` for none)."""
    e = _first_value(v)
    return None if e is None else as_character(e)


def _bibr12_info(metadata: Any, source: Any, schema_version: str, extraction: Any) -> pd.DataFrame:
    """Port of ``R/import-bibr12.R::.bibr12_info()``: the one-row ``info`` table of a 12.x paper.

    *extraction* is the export's extraction block (R reads ``$producer`` of
    it lazily, after the source). A field holding an array keeps its first
    value (metacheck fails the paper; U24, see :func:`_bibr12_records`).
    """
    meta = metadata if isinstance(metadata, Mapping) else {}
    row: dict[str, Any] = {}
    for col, typ in BIBR12_COLS["metadata"].items():
        if typ in _SCALAR_SCHEMA:
            row[col] = _first_value(meta.get(col))
        else:
            row[col] = _cell(meta.get(col), typ)
    for col in ("file_name", "sha256", "input_format"):
        # info$file_name <- as.character(source$file_name %||% NA)
        row[col] = _chr_first(_dollar_atomic(source, col))
    row["schema_version"] = schema_version
    # the older info columns paper.json requires
    sha = row["sha256"]
    row["file_hash"] = None if sha is None else str(sha)[:16]
    row["bibr_version"] = None
    producer = _dollar_atomic(extraction, "producer")
    if _dollar_atomic(producer, "name") == "bibr":
        row["bibr_version"] = _chr_first(_field(producer, "version"))
    frame = records_to_frame("info", [row], list(row))
    frame["keywords"] = pd.Series([row["keywords"]], dtype=object)
    return frame


def _chr1(v: Any) -> str | None:
    if isinstance(v, list | dict):
        return None
    return as_character(v)


def _names_records(persons: Any) -> Any:
    """The given/family names of a list of person objects (``names_df()``).

    A data frame in R: pytacheck holds a non-empty one as a list of records,
    as it does for the data frames jsonlite makes from JSON arrays of objects,
    and an empty one as a zero-row data frame.
    """
    # vapply() runs over the elements of a JSON array or object; a scalar is
    # a length-1 vector
    if persons is None:
        people: list[Any] = []
    elif isinstance(persons, list):
        people = persons
    elif isinstance(persons, Mapping):
        people = list(persons.values())
    else:
        people = [persons]
    if not people:
        return _EMPTY_NAMES.copy(deep=False)  # zero rows: nothing to share
    out = []
    for p in people:
        if p is not None and not isinstance(p, list | Mapping):
            raise ValueError("$ operator is invalid for atomic vectors")
        # NULL$given and an unnamed list's $given are NULL: NA
        person = p if isinstance(p, Mapping) else {}
        out.append(
            {
                "given": _person_name(person.get("given")),
                "family": _person_name(person.get("family")),
            }
        )
    return out


def _person_name(v: Any) -> str | None:
    """``as.character(p$given %||% NA)``: the name, its first value when it is an array.

    (metacheck's ``vapply()`` fails the paper when a name is an array of
    other than one value, and reads ``[null]`` as ``"NULL"``; U24.)
    """
    return _chr_first(v)


_EMPTY_NAMES = pd.DataFrame(
    {"given": pd.Series([], dtype="string"), "family": pd.Series([], dtype="string")}
)


def _bibr12_paper(
    paper_id: Any,
    info: pd.DataFrame,
    tables: Mapping[str, list[dict[str, Any]]],
    extraction: Any,
) -> Paper:
    """Port of ``R/import-bibr12.R::.bibr12_paper()``: assemble a paper from 12.x parts.

    *tables* maps 12.0 table names to their normalised records (see
    :func:`_bibr12_records`); they are materialised as data frames on first
    use, like every table read from JSON.
    """
    # paper(id) then as.character(paper_id): an array id [null] is "NULL"
    pid: Any = paper_id
    if isinstance(pid, list | dict):
        ids = _as_character_list(pid)
        pid = ids[0] if ids else None  # R keeps a character(0) or longer vector
    p = Paper(None if pid is None else _chr1(_jnum(pid)))
    p.info = info
    # the older given/family columns metacheck's modules read (ref_accuracy),
    # made in R's order: bib_match, then info_match; authors, then editors
    names: dict[str, dict[str, list[Any]]] = {}
    for tbl in ("bib_match", "metadata_match"):
        records = list(tables.get(tbl) or [])
        names[tbl] = {
            "authors": [_names_records(r.get("author")) for r in records],
            "editors": [_names_records(r.get("editor")) for r in records],
        }
    for tbl, name in BIBR12_TABLES.items():
        records = list(tables.get(tbl) or [])
        columns = list(BIBR12_COLS[tbl])
        if tbl in names:
            records = [
                {**r, "authors": a, "editors": e}
                for r, a, e in zip(
                    records, names[tbl]["authors"], names[tbl]["editors"], strict=True
                )
            ]
            columns += ["authors", "editors"]
        p._set_raw(name, records, columns)
    if extraction is not None:
        p["extraction"] = extraction
    return p


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _schema_version(x: Mapping[str, Any]) -> str:
    """``as.character(x$schema_version[[1]])``."""
    v = _field(x, "schema_version")
    if isinstance(v, list):
        if not v:
            raise ValueError("subscript out of bounds")
        v = v[0]
    elif isinstance(v, dict):
        if not v:
            raise ValueError("subscript out of bounds")
        v = next(iter(v.values()))
    version = as_character(_jnum(v)) if not isinstance(v, list | dict) else None
    if version is None:
        raise ValueError("argument is of length zero")
    return version


def _bibr12_from_json(x: Mapping[str, Any], include_images: bool, file_name: str) -> Paper:
    """``.read_bibr12()`` on already parsed JSON (``file_name`` names it in errors).

    Malformed input stops where and as metacheck stops: a scalar table row
    (``.bibr12_rows()``), a scalar ``source``/``extraction``/``producer``
    (``$``), a scalar person (``names_df()``). A scalar field holding an
    array keeps its first value (see :func:`_bibr12_records`).
    """
    version = _schema_version(x)
    if not grepl(r"^12\.", version):
        raise ValueError(
            f"bibr export schema {version} is not supported: metacheck reads schema 12.x "
            f"and the older files without a root schema_version ({file_name})"
        )

    raw = {tbl: _as_rows(x.get(tbl)) for tbl in BIBR12_TABLES}
    for rows in raw.values():
        for row in rows:
            _r_index(row, "")  # .bibr12_rows(): a scalar row stops

    # metacheck keeps degrees of freedom in parentheses, as printed: "(28)"
    raw["eq"] = [
        {**r, "df": _paren_df(r.get("df"))} if isinstance(r, dict) else r for r in raw["eq"]
    ]

    # exact keys (metacheck's `$` also matches a prefix: metadata_match stands
    # in for a missing metadata object; U25)
    extraction = _field(x, "extraction")
    info = _bibr12_info(_field(x, "metadata"), _field(x, "source"), version, extraction)

    tables: dict[str, list[dict[str, Any]]] = {}
    for tbl in BIBR12_TABLES:
        drop = ("image",) if tbl == "figure" and not include_images else ()
        tables[tbl] = _bibr12_records(raw[tbl], BIBR12_COLS[tbl], drop)
    return _bibr12_paper(_field(x, "paper_id"), info, tables, extraction)


def _paren_df(df: Any) -> str | None:
    """Degrees of freedom in parentheses: ``"28"`` is ``"(28)"``, ``"(28)"`` stays.

    An empty ``df`` is no df (``None``; metacheck makes it ``"()"``), and an
    array holds the degrees of freedom (``[1, 27]`` is ``"(1, 27)"``; metacheck
    fails the paper for an empty or longer one; U24).
    """
    if isinstance(df, list | dict):
        values: list[Any] = []
        _flatten(df, values)
        parts = [str(sub(r"^\((.*)\)$", r"\1", as_character(_jnum(v)))) for v in values]
        # (blank values are no degrees of freedom: ["", ""] is no df, not "(, )")
        parts = [p for p in parts if p.strip()]
        s = ", ".join(parts) if parts else None
    else:
        s = _chr_first(df)
    if s is None or s.strip() == "":
        return None
    if grepl(r"^\(.*\)$", s):
        return s
    return f"({s})"


def read_bibr12(file_path: str | PathLike[str], include_images: bool = False) -> Paper:
    """Port of ``R/import-bibr12.R::.read_bibr12()``: read a bibr export schema 12.x file.

    Called by :func:`pytacheck.papers.read_bibr` for a file with a root
    ``schema_version``; any version other than 12.x raises metacheck's error.
    Figure images are dropped unless *include_images* is true.
    """
    x = orjson.loads(Path(file_path).read_bytes())
    if not isinstance(x, Mapping):
        raise ValueError(f"{os.path.basename(file_path)} is not a bibr export (a JSON object)")
    return _bibr12_from_json(x, include_images, os.path.basename(file_path))


_read_bibr12 = read_bibr12


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _bibr12_doi(x: Sequence[Any]) -> list[str | None]:
    """Port of ``R/import-bibr12.R::.bibr12_doi()``: bare lowercase DOIs, NA when not a DOI."""
    out: list[str | None] = []
    for v in x:
        s = as_character(v)
        if s is None:
            out.append(None)
            continue
        s = str(trimws(s)).lower()
        s = sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", s)
        out.append(s if grepl(r"^10\.[0-9]{4,9}/\S+$", s) else None)
    return out


_BIB_TYPES = (
    "journal_article",
    "book",
    "book_chapter",
    "dataset",
    "software",
    "preprint",
    "conference_paper",
    "report",
    "thesis",
    "other",
)
_BIB_TYPE_MAP = {
    "article": "journal_article",
    "journal-article": "journal_article",
    "incollection": "book_chapter",
    "inbook": "book_chapter",
    "book-chapter": "book_chapter",
    "inproceedings": "conference_paper",
    "conference": "conference_paper",
    "proceedings-article": "conference_paper",
    "techreport": "report",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "dissertation": "thesis",
    "posted-content": "preprint",
}


def _bibr12_bib_type(x: Sequence[Any]) -> list[str | None]:
    """Port of ``R/import-bibr12.R::.bibr12_bib_type()``: reference types in the 12.x vocabulary."""
    out: list[str | None] = []
    for v in x:
        s = as_character(v)
        if s is None:
            out.append(None)
            continue
        s = _BIB_TYPE_MAP.get(s, s)
        out.append(s if s in _BIB_TYPES else "other")
    return out


def _bibr12_sha256(path: str | PathLike[str]) -> str | None:
    """Port of ``R/import-bibr12.R::.bibr12_sha256()``: the SHA-256 digest of a file (NA if unreadable)."""
    try:
        with open(path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError:
        return None


class _Array(tuple):  # type: ignore[type-arg]
    """An R atomic vector wrapped in ``I()``: jsonlite writes it inline, ``["a", "b"]``."""

    __slots__ = ()


def _persons_from_names(p: Any) -> list[dict[str, Any]] | None:
    """The 12.0 person objects of an older given/family name table (``add_bib_match()``)."""
    if isinstance(p, pd.DataFrame):
        if not len(p):
            return None
        cols = set(p.columns)
        records = p.to_dict("records")
    elif isinstance(p, list) and p and all(isinstance(r, Mapping) for r in p):
        cols = {k for r in p for k in r}
        records = p
    else:
        return None
    out = []
    for r in records:
        person: dict[str, Any] = {}
        for key in ("given", "family"):
            value = r.get(key) if key in cols else None
            if key in cols and _scalar_na(value):
                continue  # person[!is.na(person)]
            person[key] = value
        out.append(person)
    return out


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _converter() -> dict[str, Any]:
    """The ``extraction.converter`` pytacheck writes (metacheck names itself)."""
    from pytacheck._version import __version__

    return {"name": "pytacheck", "version": __version__, "build_sha": None}


def paper_to_bibr12(paper: Paper) -> dict[str, Any]:
    """Port of ``R/import-bibr12.R::.paper_to_bibr12()``: a paper as a bibr 12.0 export.

    Called by ``paper_write(schema_version="12.0")``. Keeps the extraction
    block of the paper (a bibr export keeps bibr as its producer and the time
    bibr extracted it) and names the converter. A paper read from a later 12.x
    file is not rewritten, since the rewrite would have to keep keys it does
    not know. Match rows made by ``add_bib_match()`` are converted to the 12.0
    columns; a score off the 0-1 scale is written as null with a
    ``METACHECK_MATCH_SCORE_NOT_0_1`` warning.

    Deliberate difference: ``extraction.converter`` names pytacheck and its
    version (metacheck writes ``metacheck`` and its own version).

    Returns the JSON structure :func:`bibr12_json` writes (inline arrays are
    ``_Array`` tuples).
    """
    pid = _chr1(paper.paper_id)
    if not is_bibr12(paper):
        raise ValueError(
            'paper_write(schema_version = "12.0") writes papers read from a bibr 12.x '
            'export, or converted from Grobid TEI with grobid_to_bibr(schema_version = "12.0"); '
            f"'{'NA' if pid is None else pid}' is in metacheck's older format"
        )
    version = paper.info["schema_version"].iloc[0]
    if version != "12.0":
        raise ValueError(
            f"paper_write(schema_version = \"12.0\") cannot rewrite '{pid}': it was read from "
            f"bibr export schema {version}, and a rewrite keeps every key, including those "
            "metacheck does not know"
        )

    ext_in = paper.get("extraction")
    ext_in = ext_in if isinstance(ext_in, Mapping) else {}
    warns = ext_in.get("warnings")
    warnings_out: list[Any] = (
        list(warns) if isinstance(warns, list) else [] if warns is None else [warns]
    )

    tables: dict[str, dict[str, Any]] = {}
    for tbl, name in BIBR12_TABLES.items():
        df = paper.get(name)
        columns: dict[str, Any] = {}
        n = 0
        if isinstance(df, pd.DataFrame):
            columns = {c: df[c] for c in BIBR12_COLS[tbl] if c in df.columns}
            n = len(df)
        tables[tbl] = _bibr12_columns(columns, BIBR12_COLS[tbl], n)

    # 12.x writes degrees of freedom bare: "28"
    tables["eq"]["df"] = list(sub(r"^\((.*)\)$", r"\1", tables["eq"]["df"]))

    # match rows made by metacheck's add_bib_match() have the older columns
    services = (
        "crossref",
        "openalex",
        "datacite",
        "doi.org",
        "openlibrary",
        "ror",
        "manual",
        "other",
    )
    for tbl in ("bib_match", "metadata_match"):
        df = paper.get(BIBR12_TABLES[tbl])
        rows = tables[tbl]
        if not isinstance(df, pd.DataFrame) or not len(df):
            continue
        for who in ("author", "editor"):
            older = df[f"{who}s"] if f"{who}s" in df.columns else None
            if who not in df.columns and older is not None and _is_list_column(older):
                rows[who] = [_persons_from_names(p) for p in older.tolist()]

        if "published_date" not in df.columns and "date" in df.columns:
            dates = [as_character(v) for v in df["date"].tolist()]
            iso = grepl(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$", dates)
            rows["published_date"] = [d if ok else None for d, ok in zip(dates, iso, strict=True)]

        scores = rows["score"]
        off = [s is not None and (s < 0 or s > 1) for s in scores]
        if any(off):
            rows["score"] = [None if o else s for s, o in zip(scores, off, strict=True)]
            warnings_out.append(
                {
                    "code": "METACHECK_MATCH_SCORE_NOT_0_1",
                    "message": (
                        f"{sum(off):d} {tbl} score(s) were not on the 0-1 scale (e.g. CrossRef "
                        "relevance scores) and were written as null"
                    ),
                }
            )

        rows["service"] = [s if s in services else "other" for s in rows["service"]]
        rows["bib_type"] = _bibr12_bib_type(rows["bib_type"])
        rows["doi"] = _bibr12_doi(rows["doi"])

    out_tables: dict[str, list[dict[str, Any]]] = {}
    for tbl in _WRITE_ORDER:
        out_tables[tbl] = _rows_out(tables[tbl], BIBR12_COLS[tbl])

    info = paper.info
    meta_cols = _bibr12_columns(
        {c: info[c] for c in BIBR12_COLS["metadata"] if c in info.columns},
        BIBR12_COLS["metadata"],
        1,
    )
    metadata: dict[str, Any] = {}
    for col, typ in BIBR12_COLS["metadata"].items():
        v = meta_cols[col]
        if typ in _SCALAR_SCHEMA:
            metadata[col] = _json_scalar(v[0]) if v else None
        else:
            metadata[col] = _Array(v[0]) if v else _Array()

    source = {
        "file_name": _info_first(info, "file_name"),
        "sha256": _info_first(info, "sha256", na_if_missing=True),
        "input_format": _info_first(info, "input_format"),
    }

    extraction = {k: v for k, v in ext_in.items() if k in _EXTRACTION_KEYS}
    extraction["converter"] = _converter()
    # when the producer extracted the content, which a converter keeps
    completed = extraction.get("completed_at")
    extraction["completed_at"] = completed if completed is not None else _now_utc()
    for key in ("ocr", "llm"):
        if key not in extraction:
            extraction[key] = None
    extraction["warnings"] = warnings_out
    first = [k for k in ("producer", "converter", "completed_at", "ocr", "llm") if k in extraction]
    extraction = {
        **{k: extraction[k] for k in first},
        **{k: v for k, v in extraction.items() if k not in first},
    }

    return {
        "paper_id": pid,
        "schema_version": "12.0",
        "source": source,
        "metadata": metadata,
        **out_tables,
        "extraction": extraction,
    }


_paper_to_bibr12 = paper_to_bibr12


def _info_first(info: pd.DataFrame, col: str, na_if_missing: bool = False) -> Any:
    """``as.character(info$col[[1]])`` (``character(0)`` for a missing column)."""
    if col not in info.columns or not len(info):
        return None if na_if_missing else _Array()
    return _chr1(_json_scalar(info[col].iloc[0]))


def _json_scalar(v: Any) -> Any:
    if isinstance(v, np.generic):
        v = v.item()
    if v is pd.NA or v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _rows_out(columns: Mapping[str, list[Any]], cols: Mapping[str, str]) -> list[dict[str, Any]]:
    """A table's typed columns as the row objects jsonlite writes (arrays wrapped in ``I()``)."""
    lists: list[list[Any]] = []
    for col, typ in cols.items():
        v = columns[col]
        if typ in _SCALAR_SCHEMA:
            lists.append([_json_scalar(e) for e in v])
        elif typ in ("chr[]", "int[]"):
            lists.append([_Array(e) for e in v])
        elif typ == "chr[][]":
            lists.append(
                [
                    {k: _Array(r) for k, r in e.items()}
                    if isinstance(e, dict)
                    else [_Array(r) for r in e]
                    for e in v
                ]
            )
        else:
            lists.append(list(v))
    names = list(cols)
    return [dict(zip(names, row, strict=True)) for row in zip(*lists, strict=True)]


# jsonlite's string escapes: quote, backslash and the control characters (as
# \b \f \n \r \t or \u00xx), which is exactly what the json module's
# C-accelerated encoder escapes with ensure_ascii=False; "</" is written "<\/"
_encode_basestring = json.encoder.encode_basestring


def _json_double(x: float) -> str:
    """A double as jsonlite writes it (15 significant digits), or with all the
    digits it needs when 15 do not give it back.

    (jsonlite's ``digits = NA`` rounds to 15 significant digits, so rewriting a
    bibr export changed its values: 0.9411764705882353 became
    0.941176470588235; U22.)
    """
    if math.isnan(x) or math.isinf(x):
        return "null"
    s = f"{x:.15g}"
    return s if float(s) == x else repr(x)


def _json_atom(x: Any) -> str:
    """A scalar as jsonlite writes it (``na = "null"``), doubles as :func:`_json_double`."""
    tx = type(x)
    if tx is str:
        return _encode_basestring(x).replace("</", "<\\/")
    if x is None or x is pd.NA:
        return "null"
    if tx is bool:
        return "true" if x else "false"
    if tx is int:
        return str(x) if -_INT_MAX <= x <= _INT_MAX else _json_double(float(x))
    if tx is float:
        return _json_double(x)
    if isinstance(x, str):
        return _json_string(x)
    if isinstance(x, bool | np.bool_):
        return "true" if x else "false"
    if isinstance(x, int | np.integer):
        v = int(x)
        return str(v) if -_INT_MAX <= v <= _INT_MAX else _json_double(float(v))
    if isinstance(x, float | np.floating):
        return _json_double(float(x))
    return _json_string(str(x))


def _json_string(s: str) -> str:
    """A JSON string as jsonlite escapes it (``</`` too, as ``<\\/``)."""
    return _encode_basestring(s).replace("</", "<\\/")


def _json_pretty(x: Any, indent: str, out: list[str]) -> None:
    # hot path: exact-type checks first (the ABC Mapping check is slow)
    tx = type(x)
    if tx is str:
        out.append(_encode_basestring(x).replace("</", "<\\/"))
    elif tx is dict or (tx is not _Array and isinstance(x, Mapping)):
        if not x:
            out.append("{}")
            return
        inner = indent + "  "
        sep = "{\n"
        for k, v in x.items():
            out.append(sep + inner + _json_string(str(k)) + ": ")
            sep = ",\n"
            if type(v) is str:
                out.append(_encode_basestring(v).replace("</", "<\\/"))
            else:
                _json_pretty(v, inner, out)
        out.append("\n" + indent + "}")
    elif tx is _Array:
        out.append("[" + ", ".join([_json_atom(v) for v in x]) + "]")
    elif tx is list or isinstance(x, list | tuple):
        if isinstance(x, _Array):
            out.append("[" + ", ".join([_json_atom(v) for v in x]) + "]")
            return
        if not x:
            out.append("[]")
            return
        inner = indent + "  "
        sep = "[\n"
        for v in x:
            out.append(sep + inner)
            sep = ",\n"
            _json_pretty(v, inner, out)
        out.append("\n" + indent + "]")
    elif isinstance(x, pd.DataFrame):
        _json_pretty(
            [{k: _json_scalar(v) for k, v in r.items()} for r in x.to_dict("records")],
            indent,
            out,
        )
    else:
        out.append(_json_atom(x))


def bibr12_json(x: Any) -> str:
    """JSON text as ``jsonlite::toJSON(x, auto_unbox = TRUE, pretty = TRUE, digits = NA,
    na = "null", null = "null")`` writes it.

    Objects (dicts) and lists are written over several lines with two-space
    indentation, an ``_Array`` (an R vector in ``I()``) inline; doubles use 15
    significant digits (``%.15g``) unless that changes their value, when they
    are written in full (a difference from jsonlite, which rounds them; U22);
    integers beyond R's integer range are doubles, and NaN/Inf are null. Only
    ``"``, ``\\`` and control characters are escaped.
    """
    out: list[str] = []
    _json_pretty(x, "", out)
    return "".join(out)


_bibr12_json = bibr12_json


def write_bibr12(paper: Paper, json_path: str | PathLike[str]) -> Path:
    """Write *paper* as a bibr 12.0 file (``write_json()`` of ``.paper_to_bibr12()``)."""
    path = Path(json_path)
    path.write_bytes((bibr12_json(paper_to_bibr12(paper)) + "\n").encode("utf-8"))
    return path
