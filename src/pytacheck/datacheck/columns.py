"""data_check helpers, part 2: columns, codebooks and labels.

Port of ``R/data_check_helpers.R`` from ``data_col_type()`` through
``match_column_labels()`` (the section before ``data_check_scale_values()``):

* column typing and summary statistics -- :func:`data_col_type`,
  :func:`data_col_stats`;
* name / label normalisation -- :func:`normalize_varname`,
  :func:`normalize_label`;
* codebook parsing -- :func:`parse_codebook` (structured CSV/TSV/Excel/ODS,
  JSON, markdown pipe tables, haven / JASP / jamovi embedded labels, Qualtrics
  ``.qsf`` via :func:`parse_qsf`, and plain-text extraction of docx / pdf /
  rtf / odt for the LLM tier);
* value labels and missing-value schemes (DDI code lists serialised as JSON);
* OpenScales scale codes and reference-instrument lookups (``.osd_*``,
  ``.scale_*``);
* :func:`match_column_labels` -- attaching codebook definitions to data
  columns (rules only; the LLM tiers live in the codebook_check module).

The private helpers keep their R names with a leading underscore (``.x`` ->
``_x``); every one is importable from this module.
"""

from __future__ import annotations

import functools
import importlib
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r.base import trimws
from pytacheck._r.regex import grepl, gsub, regexec, regextract, sub
from pytacheck.datacheck._columns_codebook import (
    _extract_json_codebook,
    _extract_markdown_codebook,
    _extract_rich_text,
    _extract_spreadsheet_codebook,
    _json_field,
    _json_value_labels,
    _pdf_codebook_lines,
    _strip_rtf,
    parse_codebook,
)
from pytacheck.datacheck._columns_labels import (
    _cb_is_definition_line,
    _chr,
    _chr_vec,
    _decode_value_labels,
    _empty_codebook_vars,
    _encode_missing_values,
    _encode_value_labels,
    _extract_codebook_positional,
    _extract_haven_labels,
    _extract_structured_codebook,
    _find_codebook_cols,
    _find_coding_instruction_col,
    _find_missing_value_col,
    _find_question_col,
    _find_value_label_col,
    _haven_value_labels,
    _looks_like_freetext_labels,
    _looks_like_varnames,
    _looks_like_wording,
    _missing_from_value_labels,
    _na,
    _normalize_header,
    _parse_value_label_text,
    _stem_words,
    _tolower,
    _toupper,
    _vec,
    _vl_is_numeric,
    _vl_split_pairs,
    normalize_label,
    normalize_varname,
)
from pytacheck.datacheck._columns_qsf import (
    _qsf_export_col,
    _qsf_option_display,
    _qsf_strip_html,
    _qsf_value_labels,
    parse_qsf,
)

__all__ = [
    "data_col_stats",
    "data_col_type",
    "match_column_labels",
    "normalize_label",
    "normalize_varname",
    "parse_codebook",
    "parse_qsf",
]

# re-exported private helpers (R internals), importable from this module
_REEXPORTED = (
    _cb_is_definition_line, _decode_value_labels, _empty_codebook_vars, _encode_missing_values,
    _encode_value_labels, _extract_codebook_positional, _extract_haven_labels,
    _extract_json_codebook, _extract_markdown_codebook, _extract_rich_text,
    _extract_spreadsheet_codebook, _extract_structured_codebook, _find_codebook_cols,
    _find_coding_instruction_col, _find_missing_value_col, _find_question_col,
    _find_value_label_col, _haven_value_labels, _json_field, _json_value_labels,
    _looks_like_freetext_labels, _looks_like_varnames, _looks_like_wording,
    _missing_from_value_labels, _normalize_header, _parse_value_label_text,
    _pdf_codebook_lines, _qsf_export_col, _qsf_option_display, _qsf_strip_html,
    _qsf_value_labels, _stem_words, _strip_rtf, _vl_is_numeric, _vl_split_pairs,
)  # fmt: skip

#: R: .data_missing_sentinels -- conventional numeric codes that disguise
#: missingness (used by data_check_scale_values()).
_DATA_MISSING_SENTINELS: tuple[float, ...] = (
    97, 98, 99, 997, 998, 999, 9997, 9998, 9999, 99997, 99998, 99999,
    88, 888, 8888, 88888, 77, 777, 7777, 77777,
    999999, 888888, 777777, 99999999,
    -99, -999,
)  # fmt: skip

# -----------------------------------------------------------------------------
# Column typing
# -----------------------------------------------------------------------------

_ID_PAT = (
    "(?i)("
    "^(participant|subject|subj|respondent|pp|ppt|pid|sub)$"
    "|^id$"
    r"|[_\-\.](id|number|num|nr|no|code)$"
    "|^(subjectid|subjectnumber|responseid|recordid|participantid|"
    "subjectno|subjectnum|subjectcode|participantno|participantnum)$"
    r"|^sub[_\-]\d"
    r"|^(participant|subject|subj|pp|sub)[_\-]?\d+$"
    ")"
)


def _is_numeric_vector(values: Any) -> bool:
    """R ``is.numeric()`` of a column (integer / double, not logical / factor)."""
    if isinstance(values, pd.Series | pd.Index | np.ndarray):
        dtype = values.dtype
        if isinstance(dtype, pd.CategoricalDtype) or pd.api.types.is_bool_dtype(dtype):
            return False
        if pd.api.types.is_numeric_dtype(dtype):
            return True
        vals = [v for v in values.tolist() if not _na(v)]
    else:
        vals = [v for v in _vec(values) if not _na(v)]
        if not vals:
            # an all-NA list: R's NA is logical
            return any(isinstance(v, float) for v in _vec(values))
    return bool(vals) and all(
        isinstance(v, int | float | np.integer | np.floating) and not isinstance(v, bool | np.bool_)
        for v in vals
    )


def _values_list(values: Any) -> list[Any]:
    if isinstance(values, pd.Series) and isinstance(values.dtype, pd.CategoricalDtype):
        return [None if _na(v) else str(v) for v in values.astype(object).tolist()]
    if isinstance(values, np.ndarray):
        return [None if _na(v) else v for v in values.tolist()]
    return _vec(values)


def _unique_key(v: Any) -> Any:
    if isinstance(v, bool | np.bool_):
        return ("lgl", bool(v))
    if isinstance(v, int | float | np.integer | np.floating):
        return float(v)
    return v


def _n_unique(vals: Sequence[Any]) -> int:
    return len({_unique_key(v) for v in vals})


def _unique(vals: Sequence[Any]) -> list[Any]:
    seen: dict[Any, Any] = {}
    for v in vals:
        seen.setdefault(_unique_key(v), v)
    return list(seen.values())


_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _strptime_ymd(s: str, sep: str) -> bool:
    """Does R's ``strptime(s, "%Y<sep>%m<sep>%d")`` give a valid date?"""
    n = len(s)
    i = 0

    def number(lo: int, hi: int, width: int) -> int | None:
        nonlocal i
        while i < n and s[i] == " ":
            i += 1
        if i >= n or not ("0" <= s[i] <= "9"):
            return None
        val = 0
        k = 0
        while k < width and i < n and "0" <= s[i] <= "9":
            val = val * 10 + ord(s[i]) - 48
            i += 1
            k += 1
        return val if lo <= val <= hi else None

    y = number(0, 9999, 4)
    if y is None or i >= n or s[i] != sep:
        return False
    i += 1
    m = number(1, 12, 2)
    if m is None or i >= n or s[i] != sep:
        return False
    i += 1
    d = number(1, 31, 2)
    if d is None:
        return False
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    return d <= (29 if m == 2 and leap else _DAYS[m - 1])


def _r_is_date(s: str | None) -> bool:
    """``!is.na(as.Date(s))`` for one string (errors / warnings -> ``False``)."""
    if s is None:
        return False
    return _strptime_ymd(s, "-") or _strptime_ymd(s, "/")


@functools.cache
def _strtod_fns() -> tuple[Any, Any]:
    from pytacheck.datacheck._files_readtable import _is_blank_string, _strtod

    return _strtod, _is_blank_string


def _as_numeric_str(s: str | None) -> float | None:
    """R ``as.numeric()`` of one string (``None`` for NA)."""
    if s is None:
        return None
    strtod, is_blank = _strtod_fns()
    b = s.encode("utf-8", "surrogateescape")
    try:
        if is_blank(b):
            return None
        x, end = strtod(b, False)
        if x is None or not is_blank(b[end:]):
            return None
    except ValueError:
        return None
    return x  # type: ignore[no-any-return]


def _as_numeric(x: Any) -> float | None:
    """R ``as.numeric()`` of one atomic value."""
    if _na(x):
        return None
    if isinstance(x, bool | np.bool_):
        return 1.0 if x else 0.0
    if isinstance(x, int | float | np.integer | np.floating):
        return float(x)
    return _as_numeric_str(_chr(x))


def _fix_name(col_name: Any) -> Any:
    """``iconv(col_name, "latin1", "UTF-8", sub = "")`` for an invalid-UTF-8 name."""
    if isinstance(col_name, bytes):
        try:
            return col_name.decode("utf-8")
        except UnicodeDecodeError:
            return col_name.decode("latin-1")
    if isinstance(col_name, str):
        try:
            col_name.encode("utf-8")
        except UnicodeEncodeError:
            return col_name.encode("utf-8", "surrogateescape").decode("latin-1")
    return col_name


def data_col_type(col_name: Any, values: Any) -> dict[str, Any]:
    """Classify a single data column by rule.

    Port of ``R/data_check_helpers.R::data_col_type()``. Rule order: all-NA ->
    empty; ID name pattern -> id; 1 unique -> constant; 2 unique -> binary;
    date-parseable -> date; long strings -> text; numeric -> continuous (or an
    ambiguous integer column); comma-decimal -> continuous variants.

    Returns a dict with ``col_type`` (``None`` when only the LLM could decide),
    ``ambiguous``, ``numeric_values`` (the numeric values for stats, or
    ``None``), ``n_coerced`` and ``is_numeric``.
    """
    col_name = _fix_name(col_name)
    is_num = _is_numeric_vector(values)
    vals = _values_list(values)
    x_nona = [v for v in vals if not _na(v)]
    n_nona = len(x_nona)

    def out(col_type: str | None, ambiguous: bool = False, numeric_values: Any = None,
            n_coerced: int | None = None, is_numeric: bool = False) -> dict[str, Any]:  # fmt: skip
        return {"col_type": col_type, "ambiguous": ambiguous, "numeric_values": numeric_values,
                "n_coerced": n_coerced, "is_numeric": is_numeric}  # fmt: skip

    if n_nona == 0:
        return out("empty")
    uniq = _unique(x_nona)
    n_unique = len(uniq)
    name = _chr(col_name) if not isinstance(col_name, list | tuple) else _chr(col_name[0])
    if grepl(_ID_PAT, name, perl=True):
        return out("id")
    if n_unique == 1:
        return out("constant")
    if n_unique == 2:
        return out("binary")
    char_sample = [_chr(v) for v in uniq[: min(20, n_unique)]]
    n_date_ok = sum(_r_is_date(v) for v in char_sample)
    if n_date_ok / len(char_sample) >= 0.70:
        return out("date")
    nch = [len(_chr(v) or "") for v in x_nona]
    from pytacheck.datacheck._columns_labels import _median

    if _median(nch) > 40:
        return out("text")
    if is_num:
        nums = [float(v) for v in x_nona]
        if any(v != math.floor(v) for v in nums if not math.isinf(v)) or n_unique > 20:
            return out("continuous", numeric_values=values)
        return out(None, ambiguous=True, numeric_values=values, is_numeric=True)
    x_sub = [_as_numeric_str(_chr(v).replace(",", ".")) for v in x_nona]  # type: ignore[union-attr]
    n_ok = sum(v is not None and not math.isnan(v) for v in x_sub)
    pct_ok = n_ok / n_nona
    if pct_ok >= 0.80:
        num_vec = [
            None if _na(v) else _as_numeric_str(_chr(v).replace(",", "."))  # type: ignore[union-attr]
            for v in vals
        ]
        num_vec_f = [math.nan if v is None else v for v in num_vec]
        col_type = "continuous_comma_decimal" if pct_ok >= 0.95 else "continuous_outliers_excluded"
        n_coerced = sum(v is None or math.isnan(v) for v in x_sub)
        return out(col_type, numeric_values=num_vec_f, n_coerced=n_coerced)
    return out(None, ambiguous=True)


# -----------------------------------------------------------------------------
# Column statistics
# -----------------------------------------------------------------------------

_STAT_COLS = ("mean", "sd", "se", "median", "min", "max", "range", "p25", "p75", "iqr",
              "skewness", "kurtosis")  # fmt: skip


def _stats_frame(n: Any, n_missing: Any, n_unique: Any, **stats: float) -> pd.DataFrame:
    data: dict[str, pd.Series] = {
        "n": pd.Series([n], dtype="Int64"),
        "n_missing": pd.Series([n_missing], dtype="Int64"),
        "n_unique": pd.Series([n_unique], dtype="Int64"),
    }
    for k in _STAT_COLS:
        data[k] = pd.Series([stats.get(k, math.nan)], dtype="float64")
    return pd.DataFrame(data)


def _r_mean(x: Sequence[float]) -> float:
    """R's ``mean()`` of doubles (accurate sum, then a correction pass)."""
    n = len(x)
    if any(math.isnan(v) for v in x):
        return math.nan
    if any(math.isinf(v) for v in x):
        return sum(x) / n
    s = math.fsum(x) / n
    if math.isfinite(s):
        s += math.fsum(v - s for v in x) / n
    return s


def _r_quantile7(x: Sequence[float], prob: float) -> float:
    """``stats::quantile(x, prob, type = 7)`` of a sorted vector without NA."""
    n = len(x)
    index = 1 + max(n - 1, 0) * prob
    lo = math.floor(index)
    hi = math.ceil(index)
    qs = x[lo - 1]
    if index > lo and x[hi - 1] != qs:
        h = index - lo
        qs = (1 - h) * qs + h * x[hi - 1]
    return qs


def _is_non_atomic(x: Any) -> bool:
    if isinstance(x, pd.DataFrame):
        return True
    if isinstance(x, np.ndarray):
        return x.ndim > 1
    if isinstance(x, pd.Series):
        if x.dtype != object:
            return False
        return any(
            isinstance(v, list | tuple | dict | np.ndarray | pd.DataFrame) for v in x.tolist()
        )
    if isinstance(x, list | tuple):
        return any(isinstance(v, list | tuple | dict | np.ndarray | pd.DataFrame) for v in x)
    return isinstance(x, dict)


def _numeric_values(x: Any) -> list[float | None]:
    """``as.numeric(x)`` of a vector (factor -> codes, strings parsed)."""
    if isinstance(x, pd.Series) and isinstance(x.dtype, pd.CategoricalDtype):
        return [None if c < 0 else float(c + 1) for c in x.cat.codes.tolist()]
    return [_as_numeric(v) for v in _vec(x)]


def data_col_stats(x_for_stats: Any, x_raw: Any) -> pd.DataFrame:
    """Summary statistics for a numeric column.

    Port of ``R/data_check_helpers.R::data_col_stats()``: a one-row data frame
    of ``n``, ``n_missing``, ``n_unique``, mean, sd (n - 1), se, median,
    min / max / range, the type-7 quartiles, IQR, skewness and excess kurtosis.
    A nested (data-frame / matrix / list) column gets counts only.
    """
    if _is_non_atomic(x_raw):
        n_val = x_raw.shape[0] if isinstance(x_raw, np.ndarray) else len(x_raw)
        return _stats_frame(n_val, 0, None)
    raw = _values_list(x_raw)
    n_unique_val = _n_unique([v for v in raw if not _na(v)])
    if x_for_stats is None:
        n_miss = sum(_na(v) for v in raw)
        return _stats_frame(len(raw) - n_miss, n_miss, n_unique_val)
    nums = _numeric_values(x_for_stats)
    x = sorted(v for v in nums if v is not None and not math.isnan(v))
    n = len(x)
    n_miss = sum(_na(v) for v in _values_list(x_for_stats))
    if n == 0:
        return _stats_frame(0, n_miss, n_unique_val)
    mn = _r_mean(x)
    s = math.nan
    if n > 1 and math.isfinite(mn):
        s = math.sqrt(math.fsum((v - mn) ** 2 for v in x) / (n - 1))
    p25 = _r_quantile7(x, 0.25)
    p75 = _r_quantile7(x, 0.75)
    half = (n + 1) // 2
    median = x[half - 1] if n % 2 == 1 else (x[half - 1] + x[half]) / 2
    stats: dict[str, float] = {
        "mean": mn,
        "sd": s,
        "se": s / math.sqrt(n) if not math.isnan(s) else math.nan,
        "median": median,
        "min": x[0],
        "max": x[-1],
        "range": x[-1] - x[0],
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "skewness": math.nan,
        "kurtosis": math.nan,
    }
    if n > 2 and not math.isnan(s) and s > 0:
        stats["skewness"] = _r_mean([(v - mn) ** 3 for v in x]) / s**3
    if n > 3 and not math.isnan(s) and s > 0:
        stats["kurtosis"] = _r_mean([(v - mn) ** 4 for v in x]) / s**4 - 3
    return _stats_frame(n, n_miss, n_unique_val, **stats)


# -----------------------------------------------------------------------------
# OpenScales codes and reference instruments
# -----------------------------------------------------------------------------


def _osd_slug(name: Any = None, prefix: Any = None, max_chars: int = 60) -> str | None:
    """Port of ``.osd_slug()``: a lowercase, underscore-joined slug (file name + code)."""
    if name is not None and not _na(name) and _chr(name) != "":
        x = name
    else:
        x = "" if prefix is None else prefix
    s = _chr(x)
    if s is None:
        return None
    s = _tolower(gsub("[^A-Za-z0-9]+", "_", s))
    s = gsub("^_+|_+$", "", s)
    if s == "":
        return "scale"
    if len(s) > max_chars:
        trunc = s[:max_chars]
        at = _first_pos("_[^_]*$", trunc)
        if at > 1:
            trunc = trunc[: at - 1]
        s = gsub("_+$", "", trunc)
    return "scale" if s == "" else s


def _first_pos(pattern: str, s: str) -> int:
    """1-based start of R ``regexpr(pattern, s)`` (``-1`` when no match)."""
    from pytacheck._r.regex import gregexpr_all

    spans = gregexpr_all(pattern, s)
    return spans[0][0] if spans else -1


def _osd_safe_code(x: Any, max_chars: int = 40) -> str | None:
    """Port of ``.osd_safe_code()``: an OSD-valid code (A-Z, 0-9 and hyphens)."""
    s = _chr("" if x is None else x)
    if s is None:
        return None
    s = _toupper(gsub("[^A-Za-z0-9]+", "-", s))
    s = gsub("^-+|-+$", "", s)
    if s == "":
        return "SCALE"
    if len(s) > max_chars:
        trunc = s[:max_chars]
        at = _first_pos("-[^-]*$", trunc)
        if at > 1:
            trunc = trunc[: at - 1]
        s = gsub("-+$", "", trunc)
    return "SCALE" if s == "" else s


_PROVENANCE = {
    "dictionary": "Matched a known instrument in metacheck's scale dictionary (OpenScales-derived or curated).",
    "self_generated": "This label was GENERATED BY metacheck from the item wording. It is NOT a recognised named instrument, only metacheck's inference of what the items measure.",
    "unnamed_block": "A coherent block of same-prefix rating columns detected in the data, but NOT named: neither a known instrument nor a construct metacheck could infer from the available text. Recorded for its structure (items + response scale) only.",
    "manuscript": "A named instrument identified from the manuscript text but not present in the OpenScales registry.",
}  # fmt: skip


def _dict_rows(dict_: Any, scale: Any) -> list[int]:
    """``which(tolower(dict$name) == tolower(scale))`` (0-based)."""
    if dict_ is None:
        return []
    names = _chr_vec(dict_["name"]) if "name" in dict_ else []
    target = _tolower(_chr(scale))
    return [i for i, nm in enumerate(names) if nm is not None and _tolower(nm) == target]


def _osd_code_and_provenance(scale: Any, prefix: Any, scale_source: Any, dict: Any) -> Any:
    """Port of ``.osd_code_and_provenance()``: OSD code, reference code and provenance.

    *dict* is the scale dictionary (a data frame with ``name`` and ``code``).
    Returns ``{"code", "ref_code", "source", "provenance"}``.
    """
    src = "" if scale_source is None else scale_source
    in_dict = False
    if not _na(scale) and _chr(scale) != "":
        in_dict = bool(_dict_rows(dict, scale))
    code = _osd_slug(name=scale, prefix=prefix)
    ref_code = _scale_ref_code(scale, dict)
    if in_dict:
        source = "dictionary"
    elif src == "self_generated":
        source, ref_code = "self_generated", None
    elif src == "unnamed_block":
        source, ref_code = "unnamed_block", None
    else:
        source = "manuscript"
    return {"code": code, "ref_code": ref_code, "source": source, "provenance": _PROVENANCE[source]}


@functools.cache
def _scale_ref_data() -> dict[str, Any]:
    """Port of ``.scale_ref_data()``: the OpenScales item-level reference datasets.

    ``scale_meta`` / ``scale_items`` / ``scale_scoring`` are looked up in
    :mod:`pytacheck.resources` (as R looks them up in the package namespace);
    metacheck at the pinned commit ships none of them, so each is ``None``.
    """

    def get_ds(nm: str) -> Any:
        try:
            mod = importlib.import_module("pytacheck.resources")
        except ImportError:  # pragma: no cover
            return None
        obj = getattr(mod, nm, None)
        return obj() if callable(obj) else obj

    return {
        "meta": get_ds("scale_meta"),
        "items": get_ds("scale_items"),
        "scoring": get_ds("scale_scoring"),
    }


def _scale_ref_code(scale: Any, dict: Any) -> str | None:
    """Port of ``.scale_ref_code()``: the OpenScales code of a dictionary scale, or ``None``."""
    if scale is None or isinstance(scale, list | tuple) or _na(scale) or _chr(scale) == "":
        return None
    rows = _dict_rows(dict, scale)
    if not rows:
        return None
    code = _chr(_chr_vec(dict["code"])[rows[0]])
    if code is None or code == "":
        return None
    ref = _scale_ref_data()["meta"]
    if ref is None or code not in set(_chr_vec(ref["code"])):
        return None
    return code


def _scale_reference(code: Any) -> dict[str, Any] | None:
    """Port of ``.scale_reference()``: registry record, items and scoring of a code."""
    if code is None or isinstance(code, list | tuple) or _na(code) or _chr(code) == "":
        return None
    d = _scale_ref_data()
    if d["meta"] is None or d["items"] is None:
        return None
    meta = d["meta"]
    m = meta[[c == code for c in _chr_vec(meta["code"])]]
    if len(m) == 0:
        return None
    items = d["items"][[c == code for c in _chr_vec(d["items"]["code"])]]
    if len(items) == 0:
        return None
    scoring = d["scoring"]
    if scoring is not None:
        scoring = scoring[[c == code for c in _chr_vec(scoring["code"])]]
    return {"meta": m.iloc[:1], "items": items, "scoring": scoring}


def _item_text_key(x: Any) -> Any:
    """Port of ``.item_text_key()``: normalised item wording for comparison."""
    vals = _chr_vec("" if x is None else x)
    out = []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        v = gsub("<[^>]*>", " ", v)
        v = _tolower(v)
        v = gsub(r"n't\b", " not", v)
        v = gsub("[^a-z0-9]+", " ", v)
        out.append(trimws(gsub(r"\s+", " ", v)))
    if isinstance(x, str) or x is None:
        return out[0]
    return out


def _scale_match_items(wording: Any, reference: Any) -> pd.DataFrame | None:
    """Match a block's item wording against a reference instrument.

    Port of ``.scale_match_items()``. *wording* maps column name -> item text
    (R's named character vector); *reference* is a
    :func:`_scale_reference` result. Unmatched columns get ``ref_reverse`` NA.
    """
    if reference is None or wording is None or not len(wording):
        return None
    pairs = [(k, _chr(v)) for k, v in dict(wording).items()]
    pairs = [(k, v) for k, v in pairs if v is not None and v != ""]
    if not pairs:
        return None
    items = reference["items"]
    ref_key = _item_text_key(_chr_vec(items["text"]))
    counts: dict[Any, int] = {}
    for k in ref_key:
        counts[k] = counts.get(k, 0) + 1
    ref_key = [None if counts[k] > 1 else k for k in ref_key]
    pos: dict[str, int] = {}
    for i, k in enumerate(ref_key):
        if k is not None:
            pos.setdefault(k, i)
    idx = [pos.get(k) for k in _item_text_key([v for _, v in pairs])]

    def pick(col: str) -> pd.Series:
        s = items[col].reset_index(drop=True)
        vals = [None if i is None else s.iloc[i] for i in idx]
        dtype = s.dtype if not pd.api.types.is_integer_dtype(s.dtype) else "Int64"
        if pd.api.types.is_bool_dtype(s.dtype):
            dtype = "boolean"
        try:
            return pd.Series(vals, dtype=dtype)
        except (TypeError, ValueError):
            return pd.Series(vals, dtype=object)

    return pd.DataFrame(
        {
            "column_name": pd.Series([k for k, _ in pairs], dtype="string"),
            "ref_item_id": pick("item_id"),
            "ref_text": pick("text"),
            "ref_reverse": pick("reverse"),
            "ref_dimension": pick("dimension"),
        }
    )


# -----------------------------------------------------------------------------
# Experiment groups and column-label matching
# -----------------------------------------------------------------------------


def _infer_group_one(s: Any) -> str | None:
    if _na(s):
        return None
    t = _chr(s)
    if t is None or trimws(t) == "":
        return None
    t = trimws(t)
    m = regextract(r"(?i)pilot\s*(\d+[a-z]?)", t, perl=True)
    if m is not None and m != "":
        return "pilot" + _tolower(sub(r"(?i)pilot\s*", "", m, perl=True))
    m = regextract(r"(?i)(experiment|study)\s*(\d+[a-z]?)", t, perl=True)
    if m is not None and m != "":
        return "ex" + _tolower(sub(r"(?i)(experiment|study)\s*", "", m, perl=True))
    return None


def _infer_group(context_str: Any) -> list[str | None]:
    """Port of ``.infer_group()``: "Experiment 1" -> "ex1", "Pilot 2" -> "pilot2"."""
    return [_infer_group_one(v) for v in _vec(context_str)]


def _column(df: pd.DataFrame, name: str) -> list[Any] | None:
    """``df$name`` as a list (``None`` when the column is absent)."""
    if name not in df.columns:
        return None
    from pytacheck.datacheck._columns_labels import _col

    return [None if _na(v) else v for v in _col(df, name).tolist()]


def _check_df_rows(sizes: list[int]) -> None:
    """R ``data.frame()``'s recycling check over its arguments' row counts.

    A shorter argument is recycled only when it is non-empty and divides the
    longest; otherwise R stops with ``unique(nrows)`` in argument order.
    """
    nr = max(sizes)
    if any(s < nr and not (s > 0 and nr % s == 0) for s in sizes):
        shown = ", ".join(str(s) for s in dict.fromkeys(sizes))
        raise ValueError(f"arguments imply differing number of rows: {shown}")


def _paste_unique(values: list[Any] | None) -> str:
    """``paste(unique(x), collapse = " | ")`` (``NA`` -> ``"NA"``)."""
    if values is None:
        return ""
    return " | ".join("NA" if v is None else _chr(v) for v in _unique(values))  # type: ignore[misc]


def _which_max_nchar(labels: list[Any]) -> int | None:
    best: int | None = None
    best_n = -1
    for i, v in enumerate(labels):
        if v is None:
            continue
        n = len(_chr(v))  # type: ignore[arg-type]
        if n > best_n:
            best, best_n = i, n
    return best


_RANGE_PAT = r"^([A-Za-z]*)\s*(\d+)\s*[-" + "–" + r"]\s*(\d+)$"
_QSF_PARADATA_RE = r"_(First\.Click|Last\.Click|Page\.Submit|Click\.Count)$"


def _as_int_str(s: str) -> int | None:
    v = _as_numeric_str(s)
    if v is None or math.isnan(v) or v >= 2147483648 or v <= -2147483649:
        return None
    return int(v)


def _expand_ranges(cb: pd.DataFrame) -> pd.DataFrame:
    """Expand range notation ("V1-V10") into one codebook row per variable."""
    var = _chr_vec(cb["codebook_variable"]) if "codebook_variable" in cb.columns else []
    hits = grepl(_RANGE_PAT, var, perl=True)
    range_rows = [i for i, h in enumerate(hits) if h]
    if not range_rows:
        return cb
    expanded: list[pd.DataFrame] = []
    for i in range_rows:
        parts = regexec(_RANGE_PAT, var[i], perl=True)
        prefix, start, end = parts[1], _as_int_str(parts[2]), _as_int_str(parts[3])
        if start is None or end is None or start > end:
            continue
        row = cb.iloc[[i] * (end - start + 1)].copy()
        new = [f"{prefix}{nn}" for nn in range(start, end + 1)]
        row["codebook_variable"] = pd.Series(
            new, index=row.index, dtype=cb["codebook_variable"].dtype
        )
        expanded.append(row)
    if not expanded:
        return cb
    keep = cb.drop(index=cb.index[range_rows])
    return pd.concat([keep, *expanded], ignore_index=True)


def match_column_labels(
    columns_df: pd.DataFrame | None, codebook_vars_df: pd.DataFrame | None
) -> pd.DataFrame:
    """Match data columns against codebook variable definitions (rules only).

    Port of ``R/data_check_helpers.R::match_column_labels()``. For each column
    of *columns_df* (``paper_id``, ``source_file``, ``column_name``, optional
    ``group`` / ``experiment_group``), finds the codebook variables of the same
    paper whose normalised name matches, honouring experiment-group scoping;
    several definitions are resolved by haven priority, then rule-based label
    equivalence (:func:`normalize_label`), else flagged
    ``conflicting_definition``. Unlabelled columns then get the question-level
    label of a Qualtrics ``.qsf`` question whose export tag prefixes the
    column name (paradata timing columns excluded).

    As in R, a column whose group is ``NA`` compared against a group-scoped
    codebook row picks up an all-``NA`` row (R's ``df[NA, ]``), so it is
    labelled with ``NA`` or gets an ``" | NA"`` conflict.
    """
    if columns_df is None:
        raise ValueError("invalid 'times' argument")
    n = len(columns_df)
    group_col = (
        _column(columns_df, "group")
        if "group" in columns_df.columns
        else _column(columns_df, "experiment_group")
        if "experiment_group" in columns_df.columns
        else [None] * n
    )
    col_group: list[Any] = group_col if group_col is not None else [None] * n
    paper = _column(columns_df, "paper_id")
    source = _column(columns_df, "source_file")
    colname = _column(columns_df, "column_name")

    def frame(status: list[str], label: list[Any], cbk: list[Any], src: list[Any],
              method: list[Any], vl: list[Any], mv: list[Any], q: list[Any], ci: list[Any],
              sg: list[Any], empty: bool = False) -> pd.DataFrame:  # fmt: skip
        # R's data.frame(): an absent column (NULL) has 0 rows; make_empty() passes
        # length-1 NA / status values that recycle to n.
        sizes = [0 if v is None else n for v in (paper, source, colname)] + [n]
        sizes.append(1 if empty else n)
        _check_df_rows(sizes)
        data: dict[str, pd.Series] = {}
        for nm, vals in (("paper_id", paper), ("source_file", source), ("column_name", colname)):
            if vals is not None:
                data[nm] = (
                    columns_df[nm].reset_index(drop=True)
                    if nm in columns_df.columns
                    else pd.Series(vals)
                )
        gser = (
            columns_df["group"] if "group" in columns_df.columns
            else columns_df["experiment_group"] if "experiment_group" in columns_df.columns
            else None
        )  # fmt: skip
        data["group"] = (
            gser.reset_index(drop=True)
            if gser is not None
            else pd.Series([None] * n, dtype="string")
        )
        for nm, vals in (("label", label), ("codebook_variable", cbk), ("label_source", src),
                         ("label_status", status), ("label_method", method),
                         ("value_labels", vl), ("missing_values", mv), ("question", q),
                         ("coding_instructions", ci), ("scale_group", sg)):  # fmt: skip
            data[nm] = pd.Series([_chr(v) for v in vals], dtype="string")
        return pd.DataFrame(data)

    na_n: list[Any] = [None] * n
    if n == 0 or codebook_vars_df is None or len(codebook_vars_df) == 0:
        return frame(["unlabelled"] * n, na_n, na_n, na_n, na_n, na_n, na_n, na_n, na_n, na_n,
                     empty=True)  # fmt: skip

    cb = _expand_ranges(codebook_vars_df)
    cb = cb.reset_index(drop=True)
    norm_col = normalize_varname([_chr(v) for v in (colname or [None] * n)])
    norm_var = (
        normalize_varname(_chr_vec(cb["codebook_variable"]))
        if "codebook_variable" in cb.columns
        else []
    )
    ncb = len(cb)

    def cbcol(name: str) -> list[Any] | None:
        return _column(cb, name)

    cb_group = cbcol("group")
    cb_label = cbcol("label") or [None] * ncb
    cb_var = cbcol("codebook_variable") or [None] * ncb
    cb_src = cbcol("codebook_source")
    cb_method = cbcol("parse_method")
    label_out: list[Any] = [None] * n
    cbk_out: list[Any] = [None] * n
    src_out: list[Any] = [None] * n
    method_out: list[Any] = [None] * n
    status_out = ["unlabelled"] * n
    vl_out: list[Any] = [None] * n
    mv_out: list[Any] = [None] * n
    q_out: list[Any] = [None] * n
    ci_out: list[Any] = [None] * n
    sg_out: list[Any] = [None] * n
    carry = {nm: cbcol(nm) for nm in ("value_labels", "missing_values", "question",
                                      "coding_instructions", "scale_group")}  # fmt: skip

    col_paper = paper if paper is not None else [None] * n
    var_paper = cbcol("paper_id") if "paper_id" in cb.columns else None
    by_name: dict[Any, list[int]] = {}
    for k, nv in enumerate(norm_var):
        if nv is None:
            continue
        key = (nv, _chr(var_paper[k])) if var_paper is not None else nv
        if var_paper is not None and var_paper[k] is None:
            continue
        by_name.setdefault(key, []).append(k)

    def first_present(rows: list[int | None], name: str) -> Any:
        vals = carry.get(name)
        if vals is None:
            return None
        for r in rows:
            v = None if r is None else vals[r]
            if v is not None and _chr(v) != "":
                return _chr(v)
        return None

    for i in range(n):
        if norm_col[i] is None:
            continue
        if var_paper is not None:
            if col_paper[i] is None:
                continue
            name_idx = by_name.get((norm_col[i], _chr(col_paper[i])), [])
        else:
            name_idx = by_name.get(norm_col[i], [])
        if not name_idx:
            continue
        cg = col_group[i]
        if cb_group is None:
            continue
        scoped = [k for k in name_idx if cb_group[k] is not None]
        unscoped = [k for k in name_idx if cb_group[k] is None]
        # R: scoped[!is.na(group) & group == cg, ] -- an NA cg gives NA rows
        if cg is None:
            same_group: list[int | None] = [None] * len(scoped)
            other_scoped: list[int | None] = [None] * len(scoped)
        else:
            cgs = _chr(cg)
            same_group = [k for k in scoped if _chr(cb_group[k]) == cgs]
            other_scoped = [k for k in scoped if _chr(cb_group[k]) != cgs]
        applicable: list[int | None] = [*unscoped, *same_group]

        def get(vals: list[Any] | None, rows: list[int | None]) -> list[Any]:
            return [None if r is None or vals is None else vals[r] for r in rows]

        if not applicable:
            if other_scoped:
                status_out[i] = "ambiguous_experiment"
                label_out[i] = _paste_unique(get(cb_label, other_scoped))
                cbk_out[i] = _paste_unique(get(cb_var, other_scoped))
                src_out[i] = _paste_unique(
                    get(cb_src, other_scoped) if cb_src is not None else None
                )
            continue
        labels = get(cb_label, applicable)
        distinct = _unique(labels)
        srcs = get(cb_src, applicable) if cb_src is not None else None
        if len(distinct) > 1:
            haven_rows = (
                [r for r in applicable if r is not None and cb_method is not None
                 and cb_method[r] is not None and _chr(cb_method[r]) == "haven"]
                if cb_method is not None else []
            )  # fmt: skip
            norm_labels = normalize_label([_chr(v) for v in distinct])
            if haven_rows:
                hl = get(cb_label, haven_rows)  # type: ignore[arg-type]
                w = _which_max_nchar(hl)
                if w is None:
                    raise ValueError("replacement has length zero")
                status_out[i] = "labelled"
                label_out[i] = hl[w]
                cbk_out[i] = get(cb_var, haven_rows[:1])[0]  # type: ignore[arg-type]
                src_out[i] = _paste_unique(
                    get(cb_src, haven_rows) if cb_src is not None else None  # type: ignore[arg-type]
                )
                method_out[i] = "haven_priority"
            elif len(set(norm_labels)) == 1:
                w = _which_max_nchar(distinct)
                if w is None:
                    raise ValueError("replacement has length zero")
                status_out[i] = "labelled"
                label_out[i] = distinct[w]
                cbk_out[i] = get(cb_var, applicable[:1])[0]
                src_out[i] = _paste_unique(srcs)
                method_out[i] = "merged_rules"
            else:
                status_out[i] = "conflicting_definition"
                label_out[i] = " | ".join("NA" if v is None else _chr(v) for v in distinct)  # type: ignore[misc]
                cbk_out[i] = _paste_unique(get(cb_var, applicable))
                src_out[i] = _paste_unique(srcs)
        else:
            status_out[i] = "labelled"
            label_out[i] = distinct[0]
            cbk_out[i] = get(cb_var, applicable[:1])[0]
            src_out[i] = _paste_unique(srcs)
        vl_out[i] = first_present(applicable, "value_labels")
        mv_out[i] = first_present(applicable, "missing_values")
        q_out[i] = first_present(applicable, "question")
        ci_out[i] = first_present(applicable, "coding_instructions")
        sg_out[i] = first_present(applicable, "scale_group")

    # -- deterministic .qsf question-tag fallback ------------------------------
    qsf_tags: list[str] = []
    sg_all = carry["scale_group"]
    if sg_all is not None and "question" in cb.columns:
        keep = (
            [m is not None and _chr(m) == "qsf" for m in cb_method]
            if cb_method is not None else [True] * ncb
        )  # fmt: skip
        for k, g in zip(keep, sg_all, strict=True):
            gs = _chr(g)
            if k and gs is not None and gs != "" and gs not in qsf_tags:
                qsf_tags.append(gs)
    if qsf_tags:
        sg_chr = [_chr(g) for g in sg_all]  # type: ignore[union-attr]
        tag_row = [sg_chr.index(t) for t in qsf_tags]
        ord_ = sorted(range(len(qsf_tags)), key=lambda t: -len(qsf_tags[t]))
        cb_q = carry["question"] or [None] * ncb
        cb_vl = carry["value_labels"]
        for i in [k for k in range(n) if status_out[k] == "unlabelled"]:
            if colname is None:
                # R: if (grepl(re, NULL)) -- a zero-length condition
                raise ValueError("argument is of length zero")
            cn = _chr(colname[i])
            if grepl(_QSF_PARADATA_RE, cn, perl=True, ignore_case=True):
                continue
            if cn is None:
                raise ValueError("missing value where TRUE/FALSE needed")
            hit = next(
                (t for t in ord_ if cn.startswith(qsf_tags[t] + "_") or cn == qsf_tags[t]), None
            )
            if hit is None:
                continue
            r = tag_row[hit]
            if cb_vl is None or cb_src is None:
                raise ValueError("replacement has length zero")
            status_out[i] = "labelled"
            label_out[i] = cb_q[r]
            cbk_out[i] = qsf_tags[hit]
            src_out[i] = cb_src[r]
            method_out[i] = "qsf_question_tag"
            vl_out[i] = cb_vl[r]
            q_out[i] = cb_q[r]
            sg_out[i] = qsf_tags[hit]

    method_out = [
        "rules" if s == "labelled" and m is None else m
        for s, m in zip(status_out, method_out, strict=True)
    ]
    return frame(
        status_out, label_out, cbk_out, src_out, method_out, vl_out, mv_out, q_out, ci_out, sg_out
    )


# -----------------------------------------------------------------------------
# Scale typos (used by data_check_scale_values)
# -----------------------------------------------------------------------------


def _r_colon(lo: float, hi: float) -> list[float]:
    """R's ``lo:hi``."""
    n = math.floor(abs(hi - lo) + 1e-10)
    step = 1 if hi >= lo else -1
    return [lo + step * k for k in range(n + 1)]


def _scale_typo_of(v: Any, lo: float, hi: float) -> float | None:
    """Could an out-of-scale value be a keying typo of an in-scale value?

    Port of ``.scale_typo_of()``: the most plausible intended value inside
    ``[lo, hi]`` (a repeated / doubled digit, a dropped or added minus, an
    extra leading digit), or ``None``.
    """
    if _na(v):
        return None
    v = float(v)
    if v in _r_colon(lo, hi):
        return None
    cand: list[float | None] = []
    av = abs(v)
    if v == _r_round0(v):
        s = _chr(av) or ""
        if len(s) >= 2:
            cand += [_as_int_str(c) for c in s]
            cand += [_as_int_str(s[1:]), _as_int_str(s[:-1])]
    cand.append(-v)
    uniq: list[float] = []
    for c in cand:
        if c is not None and not _na(c) and c not in uniq:
            uniq.append(float(c))
    inside = [c for c in uniq if lo <= c <= hi]
    if not inside:
        return None
    target = math.fmod(av, 10)
    best = min(range(len(inside)), key=lambda k: (abs(inside[k] - target), k))
    return inside[best]


def _r_round0(x: float) -> float:
    from pytacheck._r.base import r_round

    return float(r_round(x, 0))
