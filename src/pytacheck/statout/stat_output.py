"""Serialise extracted result tables: long table, native JSON document, files.

Port of ``R/stat-output.R``. One extraction (the list of tables returned by
:func:`~pytacheck.statout.stat_tables.read_stat_tables` or
:func:`~pytacheck.statout.r_output.read_r_output`) is serialised two ways:

* :func:`stat_results_long` -- one row per statistic cell, STATO-typed;
* :func:`stat_output_json` -- metacheck's native statistical-output document.

:func:`stat_output_validate` checks a document's shape and
:func:`stat_output_write` writes both views to ``<root>/statistical_output/``.
Column typing is done by
:func:`pytacheck.statout.stato_map.stato_type_column`.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character, plural
from pytacheck._r.regex import compile_r, grepl, gsub, regexec, sub
from pytacheck.statout.r_output import _r_as_numeric, _trimws

__all__ = [
    "stat_output_json",
    "stat_output_validate",
    "stat_output_write",
    "stat_results_long",
]

_LONG_COLUMNS = (
    "paper_id",
    "source_file",
    "test_id",
    "result_id",
    "analysis",
    "table_title",
    "row_label",
    "statistic",
    "stato_label",
    "stato_iri",
    "value",
    "model_ref",
)
_STATO_DF = "http://purl.obolibrary.org/obo/STATO_0000069"
_UNTYPED = {"annotationValue": "", "termSource": "", "termAccession": ""}


# ---------------------------------------------------------------------------
# Small R helpers
# ---------------------------------------------------------------------------


def _paste_chr(x: Any) -> str:
    """``paste()``'s view of one value (``NA`` -> ``"NA"``)."""
    if isinstance(x, float) and math.isnan(x):
        return "NA"
    s = as_character(x)
    return "NA" if s is None else s


def _is_na(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _cell(x: Any) -> str | None:
    """``as.character()`` of one data-frame cell (``None`` for ``NA``)."""
    if _is_na(x):
        return None
    return as_character(x)


def _typ_get(typ: Any, key: str) -> str:
    if typ is None:
        return ""
    val = typ.get(key, "") if isinstance(typ, Mapping) else getattr(typ, key, "")
    return "" if val is None else str(val)


def _stato_type_column(header: Any, call_fn: Any = None) -> Any:
    from pytacheck.statout.stato_map import stato_type_column  # type: ignore[import-not-found]

    return stato_type_column(header, call_fn)


def _ave_unique(ids: Sequence[str]) -> list[str]:
    """``stats::ave(ids, ids, FUN = ...)``: suffix ``_1, _2`` on repeated ids."""
    counts: dict[str, int] = {}
    for x in ids:
        counts[x] = counts.get(x, 0) + 1
    seen: dict[str, int] = {}
    out = []
    for x in ids:
        if counts[x] == 1:
            out.append(x)
        else:
            seen[x] = seen.get(x, 0) + 1
            out.append(f"{x}_{seen[x]}")
    return out


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def _stat_sanitize_id(x: Any) -> str | None:
    """Reduce text to one safe token (lower case, runs of ``[^a-z0-9]`` -> ``_``).

    Port of ``R/stat-output.R::.stat_sanitize_id()``; only one leading *or*
    trailing underscore is removed (R's ``sub("^_|_$", "", x)``).
    """
    s = _cell(x)
    if s is None:
        return None
    s = (_trimws(s) or "").lower()
    s = gsub("[^a-z0-9]+", "_", s)
    return sub("^_|_$", "", s)


def _num_or_na(x: Any) -> bool:
    return not _is_na(x)


def _stat_result_ids(
    tables: Sequence[Mapping[str, Any]], source_file: str | None = None
) -> list[str]:
    """One base (per-table) result id per table.

    Port of ``R/stat-output.R::.stat_result_ids()``:
    ``<source>_l<line>_<line_seq>``, ``<source>_t<table_index>``,
    ``<source>_<analysis>`` or ``<source>_result``; repeats get ``_1, _2``.
    """
    # R's default source_file is NA_character_ (``None`` here), which the
    # ``%||% "result"`` fallback does not replace: ids then start "NA_".
    src = _paste_chr(_stat_sanitize_id(source_file))
    locators = []
    for tb in tables:
        line = tb.get("line")
        ti = tb.get("table_index")
        analysis = tb.get("analysis")
        if "line" in tb and _num_or_na(line):
            seq_n = tb.get("line_seq", 1)
            locators.append(f"l{_paste_chr(line)}_{_paste_chr(seq_n)}")
        elif "table_index" in tb and _num_or_na(ti):
            locators.append(f"t{_paste_chr(ti)}")
        elif analysis is not None and not _is_na(analysis) and str(analysis) != "":
            locators.append(_paste_chr(analysis))
        else:
            locators.append("result")
    return _ave_unique([f"{src}_{loc}" for loc in locators])


def _stat_test_id(
    tb: Mapping[str, Any], source_file: str | None, base_id: str, row_label: str = ""
) -> str | None:
    """The test-level grouping key of one result row.

    Port of ``R/stat-output.R::.stat_test_id()``: anchored on the format's
    analysis id, else the source line (+ ``line_seq``), else the table's
    base id; the row label is appended.
    """
    aid = tb.get("analysis_id")
    if isinstance(aid, list):
        aid = aid[0] if len(aid) == 1 else None
    if aid is not None and not _is_na(aid) and _paste_chr(aid) != "":
        anchor = "a" + _paste_chr(aid)
    elif "line" in tb and _num_or_na(tb.get("line")):
        anchor = f"l{_paste_chr(tb['line'])}_{_paste_chr(tb.get('line_seq', 1))}"
    else:
        anchor = sub("_r[0-9]+$", "", base_id)
    src = _paste_chr(_stat_sanitize_id(source_file))
    return _stat_sanitize_id(f"{src}_{anchor}_{_paste_chr(row_label)}")


# ---------------------------------------------------------------------------
# Column roles
# ---------------------------------------------------------------------------

_STAT_PLACEHOLDERS = (".", "-", "—", "–", "−", "na", "nan", "null", "n/a")


def _stat_is_placeholder(x: Any) -> bool:
    """Is this cell a placeholder (``.``, a dash, ``NA`` text) rather than a value?

    Port of ``R/stat-output.R::.stat_is_placeholder()``.
    """
    s = _cell(x)
    if s is None:  # NA is not a placeholder (``!nzchar(NA)`` is FALSE)
        return False
    s = _trimws(s) or ""
    return s == "" or s.lower() in _STAT_PLACEHOLDERS


_NUMLIKE = "^[<>=]?\\s*[-+]?[0-9.]+([eE][-+]?[0-9]+)?$"


def _stat_is_label_col(header: Any, values: Any, role: Mapping[str, Any] | None = None) -> bool:
    """Is this result-table column a row label (vs a statistic)?

    Port of ``R/stat-output.R::.stat_is_label_col()``: a declared role wins;
    a header that types to a known statistic is a statistic; otherwise a
    column is a label when fewer than half its non-placeholder cells look
    numeric.
    """
    if role is not None:
        ty = (_trimws(_paste_chr(role.get("type") or "")) or "").lower()
        fm = (_trimws(_paste_chr(role.get("format") or "")) or "").lower()
        if fm:
            return False
        if ty in ("number", "integer"):
            return False
        if ty == "text":
            return True
    h = (_trimws("" if header is None else _paste_chr(header)) or "").lower()
    if values is None:
        vals: list[str | None] = []
    else:
        seq = values.tolist() if isinstance(values, pd.Series) else list(values)
        vals = [None if (c := _cell(v)) is None else _trimws(c) for v in seq]
    vals = [v for v in vals if not _stat_is_placeholder(v)]
    if h and _typ_get(_stato_type_column(h), "termSource") != "":
        return False
    if not vals:
        return True
    r1 = compile_r(_NUMLIKE)
    r2 = compile_r("(?i)^[-+]?inf$", perl=True)
    r3 = compile_r("^[<>]\\s*[.0-9]")
    numlike = [v is not None and bool(r1.search(v) or r2.search(v) or r3.search(v)) for v in vals]
    return sum(numlike) / len(numlike) < 0.5


def _split_combined_df(val: Any, typ: Any) -> list[dict[str, Any]] | None:
    """Split a combined ``"2, 560"`` df cell into df1/df2.

    Port of ``R/stat-output.R::.split_combined_df()``; ``None`` unless the
    column typed to plain df and both halves are numbers.
    """
    if typ is None or _typ_get(typ, "termAccession") != _STATO_DF:
        return None
    if val is None:
        return None
    m = regexec("^([0-9.]+)\\s*,\\s*([0-9.]+)$", val)
    if len(m) != 3:
        return None
    if _r_as_numeric(m[1]) is None or _r_as_numeric(m[2]) is None:
        return None
    return [
        {"name": "df1", "value": m[1], "typ": _stato_type_column("df1")},
        {"name": "df2", "value": m[2], "typ": _stato_type_column("df2")},
    ]


def _col_roles(df: pd.DataFrame) -> Mapping[str, Any]:
    roles = df.attrs.get("col_roles") if hasattr(df, "attrs") else None
    return roles if isinstance(roles, Mapping) else {}


def _label_flags(df: pd.DataFrame) -> list[bool]:
    headers = [str(c) for c in df.columns]
    roles = _col_roles(df)
    return [
        _stat_is_label_col(h, df.iloc[:, c], roles.get(h) if h != "" else None)
        for c, h in enumerate(headers)
    ]


def _frame_column(values: list[Any]) -> pd.Series:
    if all(v is None or isinstance(v, str) for v in values):
        return pd.Series(values, dtype="string")
    return pd.Series(values, dtype=object)


# ---------------------------------------------------------------------------
# stat_results_long()
# ---------------------------------------------------------------------------


def stat_results_long(
    tables: Sequence[Mapping[str, Any]] | None,
    paper_id: Any = None,
    source_file: str | None = None,
) -> pd.DataFrame:
    """Flatten extracted result tables into one long data frame.

    Port of ``R/stat-output.R::stat_results_long()``: one row per
    (analysis, table, result row, statistic) cell with columns
    ``paper_id``, ``source_file``, ``test_id``, ``result_id``, ``analysis``,
    ``table_title``, ``row_label``, ``statistic``, ``stato_label``,
    ``stato_iri``, ``value`` and ``model_ref``. Empty frame (same columns)
    when there is nothing to flatten.
    """
    rows: dict[str, list[Any]] = {c: [] for c in _LONG_COLUMNS}
    if tables:
        tables = list(tables)
        base_ids = _stat_result_ids(tables, source_file)
        for ti, tb in enumerate(tables):
            _long_rows(tb, base_ids[ti], paper_id, source_file, rows)
    return pd.DataFrame({c: _frame_column(v) for c, v in rows.items()})


def _long_rows(
    tb: Mapping[str, Any],
    base_id: str,
    paper_id: Any,
    source_file: str | None,
    rows: dict[str, list[Any]],
) -> None:
    df = tb.get("data")
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0 or df.shape[1] == 0:
        return
    if tb.get("is_chart") is True:
        return
    headers = [str(c) for c in df.columns]
    is_label = _label_flags(df)
    label_cols = [i for i, lab in enumerate(is_label) if lab]
    stat_cols = [i for i, lab in enumerate(is_label) if not lab]
    if not stat_cols:
        return

    is_spv = "syntax" in tb
    stats_col: int | None = None
    if is_spv:
        exact = [c for c in label_cols if (_trimws(headers[c]) or "").lower() == "statistics"]
        stats_col = exact[0] if exact else None
    row_label_cols = (
        [c for c in label_cols if c != stats_col]
        if is_spv and stats_col is not None
        else label_cols
    )

    analysis = tb.get("analysis")
    title = tb.get("title")
    model_ref = tb.get("model_ref")
    call_fn = tb.get("call_fn")
    columns = [df.iloc[:, c].tolist() for c in range(df.shape[1])]
    stat_slugs = _ave_unique([_paste_chr(_stat_sanitize_id(headers[c])) for c in stat_cols])

    typ_cache: dict[str, Any] = {}

    def header_typ(h: str) -> Any:
        if h not in typ_cache:
            typ_cache[h] = _stato_type_column(h, call_fn)
        return typ_cache[h]

    def add(
        test_id: Any, result_id: Any, row_label: str, statistic: Any, typ: Any, value: Any
    ) -> None:
        rows["paper_id"].append(paper_id)
        rows["source_file"].append(source_file)
        rows["test_id"].append(test_id)
        rows["result_id"].append(result_id)
        rows["analysis"].append(analysis)
        rows["table_title"].append(title)
        rows["row_label"].append(row_label)
        rows["statistic"].append(statistic)
        rows["stato_label"].append(_typ_get(typ, "annotationValue"))
        rows["stato_iri"].append(_typ_get(typ, "termAccession"))
        rows["value"].append(value)
        rows["model_ref"].append(model_ref)

    ws = compile_r("\\s+")
    for ri in range(len(df)):
        parts = [_paste_chr(_trimws(_cell(columns[c][ri]))) for c in row_label_cols]
        row_label = _trimws(ws.sub(" ", " ".join(parts))) or ""
        row_id = f"{base_id}_r{ri + 1}"
        test_id: Any = None
        test_id_done = False
        for si, ci in enumerate(stat_cols):
            c = _cell(columns[ci][ri])
            val = None if c is None else _trimws(c)
            if _stat_is_placeholder(val):
                continue
            if is_spv:
                stat_name: Any = (
                    _trimws(_cell(columns[stats_col][ri])) if stats_col is not None else headers[ci]
                )
                if stats_col is not None:
                    from pytacheck.statout.stato_map import (  # type: ignore[import-not-found]
                        _spv_stato_type_label,
                    )

                    typ = _spv_stato_type_label(stat_name)
                else:
                    typ = _UNTYPED
            else:
                stat_name = headers[ci]
                typ = header_typ(headers[ci])
            if (
                _typ_get(typ, "termSource") == ""
                and ("" if analysis is None else analysis) == "Correlation of Fixed Effects"
            ):
                typ = {
                    "annotationValue": "Pearson's correlation coefficient",
                    "termSource": "STATO",
                    "termAccession": "http://purl.obolibrary.org/obo/STATO_0000280",
                }
            if not test_id_done:
                test_id = _stat_test_id(tb, source_file, base_id, row_label)
                test_id_done = True
            combined = _split_combined_df(val, typ)
            if combined is not None:
                for cd in combined:
                    add(
                        test_id,
                        _stat_sanitize_id(f"{row_id}_{stat_slugs[si]}_{cd['name']}"),
                        row_label,
                        cd["name"],
                        cd["typ"],
                        cd["value"],
                    )
                continue
            add(
                test_id,
                _stat_sanitize_id(f"{row_id}_{stat_slugs[si]}"),
                row_label,
                stat_name,
                typ,
                val,
            )


# ---------------------------------------------------------------------------
# stat_output_json()
# ---------------------------------------------------------------------------


def _source_format(source_file: str | None) -> str:
    sf = "" if source_file is None else source_file
    for pat, fmt in (
        ("\\.omv$", "jamovi"),
        ("\\.jasp$", "JASP"),
        ("\\.spv$", "SPSS"),
        ("\\.smcl$", "Stata"),
        ("\\.out$", "Mplus"),
        ("\\.[rR]$", "R"),
    ):
        if source_file is not None and grepl(pat, sf, ignore_case=True):
            return fmt
    return "unknown"


def stat_output_json(
    tables: Sequence[Mapping[str, Any]] | None,
    paper_id: Any = "metacheck",
    source_file: str | None = None,
) -> dict[str, Any] | None:
    """Serialise result tables as a structured statistical-output document.

    Port of ``R/stat-output.R::stat_output_json()``. Returns a dict with
    ``schema``, ``schema_version``, ``paper_id``, ``source_file``,
    ``source_format`` and ``analyses`` (each ``{"analysis", "results"}``, a
    result being ``{"result_id", "test_id", "row_label", "values"}``), or
    ``None`` when no table yields a value.
    """
    if not tables:
        return None
    tables = list(tables)
    source_format = _source_format(source_file)
    base_ids = _stat_result_ids(tables, source_file)
    ws = compile_r("\\s+")

    analyses: list[dict[str, Any]] = []
    for ti, tb in enumerate(tables):
        df = tb.get("data")
        if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0 or df.shape[1] == 0:
            continue
        if tb.get("is_chart") is True:
            continue
        headers = [str(c) for c in df.columns]
        is_label = _label_flags(df)
        stat_cols = [i for i, lab in enumerate(is_label) if not lab]
        label_cols = [i for i, lab in enumerate(is_label) if lab]
        if not stat_cols:
            continue
        columns = [df.iloc[:, c].tolist() for c in range(df.shape[1])]
        call_fn = tb.get("call_fn")
        typ_cache: dict[str, Any] = {}

        results: list[dict[str, Any]] = []
        for ri in range(len(df)):
            values: dict[str, Any] = {}
            for ci in stat_cols:
                c = _cell(columns[ci][ri])
                val = None if c is None else _trimws(c)
                if _stat_is_placeholder(val):
                    continue
                h = headers[ci]
                if h not in typ_cache:
                    typ_cache[h] = _stato_type_column(h, call_fn)
                typ = typ_cache[h]
                num = _r_as_numeric(val)
                entry: dict[str, Any] = {
                    "value": val if num is None or not math.isfinite(num) else num
                }
                if _typ_get(typ, "termAccession"):
                    entry["stato_label"] = _typ_get(typ, "annotationValue")
                    entry["stato_iri"] = _typ_get(typ, "termAccession")
                key = _stat_sanitize_id(h) or ""
                if not key:
                    key = f"v{ci + 1}"
                values[key] = entry
            if not values:
                continue
            parts = [_paste_chr(_cell(columns[c][ri])) for c in label_cols]
            row_label = _trimws(ws.sub(" ", " ".join(parts))) or ""
            results.append(
                {
                    "result_id": _stat_sanitize_id(f"{base_ids[ti]}_r{ri + 1}"),
                    "test_id": _stat_test_id(tb, source_file, base_ids[ti], row_label),
                    "row_label": row_label,
                    "values": values,
                }
            )
        if not results:
            continue
        analyses.append({"analysis": tb.get("analysis"), "results": results})
    if not analyses:
        return None
    return {
        "schema": "metacheck-statistical-output",
        "schema_version": "1.0",
        "paper_id": paper_id,
        "source_file": source_file,
        "source_format": source_format,
        "analyses": analyses,
    }


# ---------------------------------------------------------------------------
# stat_output_validate()
# ---------------------------------------------------------------------------


def _r_names(x: Any) -> list[str]:
    return [str(k) for k in x] if isinstance(x, Mapping) else []


def _r_elt(x: Any, key: str) -> Any:
    """``x$key`` (``None`` when absent; errors on atomic values like R)."""
    if x is None or isinstance(x, list):
        return None
    if isinstance(x, Mapping):
        return x.get(key)
    raise TypeError("$ operator is invalid for atomic vectors")


def _is_list(x: Any) -> bool:
    return isinstance(x, list | Mapping)


def _r_length(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, list | Mapping | str):
        return len(x) if not isinstance(x, str) else 1
    return 1


def _iter_elems(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, Mapping):
        return list(x.values())
    if isinstance(x, list):
        return list(x)
    return [x]


def _sprintf_s(x: Any) -> str:
    if isinstance(x, list):
        x = x[0] if x else ""
    return _paste_chr(x)


def _parse_json_doc(doc: str) -> Any:
    """``jsonlite::fromJSON(if (file.exists(doc)) doc else textConnection(doc))``.

    jsonlite only reads *binary* connections, so metacheck's text-connection
    branch always fails: a JSON string that is not a file path is reported as
    invalid JSON. That upstream behaviour is reproduced.
    """

    def reject(const: str) -> Any:
        raise ValueError(const)

    if not os.path.exists(doc):
        raise ValueError("can only read from a binary connection")
    src = Path(doc).read_text(encoding="utf-8")
    return json.loads(src, parse_constant=reject)


def stat_output_validate(doc: Any) -> dict[str, Any]:
    """Validate a statistical-output document's native shape.

    Port of ``R/stat-output.R::stat_output_validate()``. *doc* is a document
    (as from :func:`stat_output_json`) or a path to a JSON file. (A JSON
    *string* is reported as invalid JSON, as in metacheck, whose text-connection
    branch cannot be read by jsonlite.) Returns
    ``{"valid", "issues", "summary": {"n_errors", "n_analyses", "n_results"}}``.
    """
    if isinstance(doc, str):
        try:
            doc = _parse_json_doc(doc)
        except (ValueError, OSError, UnicodeDecodeError):
            return {
                "valid": False,
                "issues": ["Input is not valid JSON."],
                "summary": {"n_errors": 1, "n_analyses": 0, "n_results": 0},
            }

    issues: list[str] = []
    required_top = [
        "schema",
        "schema_version",
        "paper_id",
        "source_file",
        "source_format",
        "analyses",
    ]
    names = _r_names(doc)
    missing_top = [k for k in required_top if k not in names]
    if missing_top:
        issues.append(
            f"Document missing top-level field{plural(len(missing_top))}: {', '.join(missing_top)}."
        )

    analyses = _r_elt(doc, "analyses")
    if analyses is None:
        analyses = []
    if not _is_list(analyses):
        issues.append("`analyses` must be a list.")

    n_results = 0
    for a in _iter_elems(analyses):
        if _r_elt(a, "analysis") is None:
            issues.append("An analysis entry is missing `analysis`.")
        results = _r_elt(a, "results")
        if results is None:
            results = []
        if not _is_list(results) or not _r_length(results):
            issues.append("An analysis entry has no `results`.")
            continue
        for r in _iter_elems(results):
            n_results += 1
            rid = _r_elt(r, "result_id")
            if rid is None or _sprintf_s(rid) == "":
                issues.append("A result is missing `result_id`.")
            values = _r_elt(r, "values")
            if values is None:
                values = []
            rid_s = "?" if rid is None else _sprintf_s(rid)
            if not _is_list(values) or not _r_length(values):
                issues.append(f'Result "{rid_s}" has no `values`.')
                continue
            for vn in _r_names(values):
                entry = None if vn == "" else values[vn]
                if _r_elt(entry, "value") is None:
                    issues.append(f'Result "{rid_s}": value "{vn}" is missing `value`.')

    return {
        "valid": not issues,
        "issues": issues,
        "summary": {
            "n_errors": len(issues),
            "n_analyses": _r_length(analyses),
            "n_results": n_results,
        },
    }


# ---------------------------------------------------------------------------
# stat_output_write()
# ---------------------------------------------------------------------------


def _json_num(x: float) -> str:
    """jsonlite's ``digits = NA`` number format (``%.15g``)."""
    if math.isnan(x):
        return '"NaN"'
    if math.isinf(x):
        return '"Inf"' if x > 0 else '"-Inf"'
    if x == 0:
        return "0"
    return f"{x:.15g}"


def _json_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _to_json_pretty(x: Any, indent: int = 0) -> str:
    """``jsonlite::toJSON(x, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA)``."""
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if x is None or x is pd.NA:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return '"NA"' if math.isnan(x) else _json_num(x)
    if isinstance(x, str):
        return _json_str(x)
    if isinstance(x, Mapping):
        if not x:
            return "{}"
        items = [
            f"{inner}{_json_str(str(k))}: {_to_json_pretty(v, indent + 1)}" for k, v in x.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(x, list | tuple):
        if not x:
            return "[]"
        if all(v is None or isinstance(v, str | int | float | bool) for v in x):
            return "[" + ", ".join(_to_json_pretty(v, indent) for v in x) + "]"
        items = [f"{inner}{_to_json_pretty(v, indent + 1)}" for v in x]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    return _json_str(str(x))


def _csv_field(x: Any, quote: bool) -> str:
    if _is_na(x):
        return ""
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if not quote:
        return as_character(x) or ""
    s = str(x)
    return '"' + s.replace('"', '""') + '"'


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    """``utils::write.csv(df, path, row.names = FALSE, na = "")``."""
    quoted = []
    for c in range(df.shape[1]):
        col = df.iloc[:, c]
        numeric = pd.api.types.is_numeric_dtype(col.dtype) and not pd.api.types.is_bool_dtype(
            col.dtype
        )
        is_bool = pd.api.types.is_bool_dtype(col.dtype)
        quoted.append(not (numeric or is_bool))
    lines = [",".join('"' + str(c).replace('"', '""') + '"' for c in df.columns)]
    cols = [df.iloc[:, c].tolist() for c in range(df.shape[1])]
    for i in range(len(df)):
        lines.append(",".join(_csv_field(cols[c][i], quoted[c]) for c in range(len(cols))))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stat_output_write(
    stat_output: Sequence[Mapping[str, Any]] | None, root: str | os.PathLike[str]
) -> str | None:
    """Write extracted statistical output to ``<root>/statistical_output/``.

    Port of ``R/stat-output.R::stat_output_write()``: one combined
    ``results_long.csv`` (every element's ``long`` table stacked) and one
    ``<codename>.statistical_output.json`` per element with a ``json``
    document. Returns the folder path, or ``None`` when nothing is written.
    """
    from pytacheck._r.frames import bind_rows

    if not stat_output:
        return None
    longs = [
        s for s in stat_output if isinstance(s.get("long"), pd.DataFrame) and len(s["long"]) > 0
    ]
    jsons = [s for s in stat_output if s.get("json") is not None]
    if not longs and not jsons:
        return None

    out_dir = Path(root) / "statistical_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if longs:
        combined = bind_rows([s["long"] for s in longs])
        _write_csv(combined, out_dir / "results_long.csv")

    for s in jsons:
        file = s.get("file")
        base = os.path.basename("result" if file is None else str(file))
        fn = sub("[.][^.]+$", "", base)
        json_path = out_dir / f"{fn}.statistical_output.json"
        json_path.write_text(_to_json_pretty(s["json"]) + "\n", encoding="utf-8")

    return str(out_dir)
