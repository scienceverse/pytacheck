"""Codebook label helpers (part of :mod:`pytacheck.datacheck.columns`).

Port of the rules-only codebook machinery in ``R/data_check_helpers.R``:
variable/label normalisation, codebook header detection, value-label and
missing-value code lists (serialised as compact JSON exactly as
``jsonlite::toJSON(auto_unbox = TRUE)`` writes them), haven embedded labels,
and the data-frame codebook extractors (named-header, positional).

R vectors are handled as Python lists with ``None`` for ``NA``; data frames
are pandas DataFrames whose character columns use the ``string`` dtype.
Per-column haven attributes (``label``, ``labels``, ``na_values``,
``na_range``) are read from ``df.attrs["col_attrs"][column]`` -- the
convention of :func:`pytacheck.datacheck.files.data_read_head` and
:func:`pytacheck.statout.jasp.import_jasp` -- where ``labels`` maps each label
text to its code (R's named vector ``c(label = code)``).
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character, is_na, trimws
from pytacheck._r.regex import grepl, gsub, regexec, regextract_all, strsplit

# -----------------------------------------------------------------------------
# R-vector utilities
# -----------------------------------------------------------------------------


def _na(x: Any) -> bool:
    """R ``is.na()`` for one value (``None``, ``NaN``, ``pd.NA``, ``NaT``)."""
    if x is None:
        return True
    try:
        return bool(is_na(x)) or (isinstance(x, float) and math.isnan(x))
    except (TypeError, ValueError):
        return False


def _chr(x: Any) -> str | None:
    """R ``as.character()`` of one atomic value (``None`` for ``NA``)."""
    if _na(x):
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, pd.Timestamp):
        if x.hour == x.minute == x.second == 0 and x.microsecond == 0:
            return x.strftime("%Y-%m-%d")
        return x.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(x, dt.datetime):
        return x.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, bytes):
        return x.decode("utf-8", "surrogateescape")
    return as_character(x)


def _vec(x: Any) -> list[Any]:
    """An R vector as a Python list (``NULL`` -> ``[]``, a scalar -> ``[x]``)."""
    if x is None:
        return []
    if isinstance(x, str | bytes) or not isinstance(x, Iterable):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [None if _na(v) else v for v in x.tolist()]
    if isinstance(x, Mapping):
        return list(x.values())
    return list(x)


def _chr_vec(x: Any) -> list[str | None]:
    """``as.character(x)`` of a vector."""
    if isinstance(x, pd.Series) and isinstance(x.dtype, pd.CategoricalDtype):
        return [None if _na(v) else str(v) for v in x.astype(object).tolist()]
    return [_chr(v) for v in _vec(x)]


def _nzchar(s: str | None) -> bool:
    """R ``nzchar()``: ``NA`` counts as non-empty."""
    return s is None or s != ""


def _trim(s: str | None) -> str | None:
    """R ``trimws()`` of one value."""
    return None if s is None else trimws(s)


def _mean(xs: Sequence[bool]) -> float:
    return sum(xs) / len(xs) if xs else math.nan


def _median(xs: Sequence[float]) -> float:
    """``stats::median()`` of a numeric vector without NA."""
    v = sorted(xs)
    n = len(v)
    if n == 0:
        return math.nan
    half = (n + 1) // 2
    if n % 2 == 1:
        return float(v[half - 1])
    return (v[half - 1] + v[half]) / 2


def _col_index(df: pd.DataFrame, name: Any) -> int:
    """Position of the first column called *name* (R's ``df[[name]]``)."""
    for j, c in enumerate(df.columns):
        if c == name or (_na(c) and _na(name)):
            return j
    raise KeyError(name)


def _col(df: pd.DataFrame, name: Any) -> pd.Series:
    return df.iloc[:, _col_index(df, name)]


def _names(df: pd.DataFrame) -> list[str | None]:
    """``names(df)`` as character."""
    return [_chr(c) for c in df.columns]


def chr_frame(columns: dict[str, Any], nrow: int) -> pd.DataFrame:
    """``data.frame()`` of character columns, recycling length-1 values."""
    data: dict[str, pd.Series] = {}
    for name, val in columns.items():
        vals = val if isinstance(val, list) else [val] * nrow
        if len(vals) == 1 and nrow != 1:
            vals = vals * nrow
        data[name] = pd.Series(vals, dtype="string")
    return pd.DataFrame(data, index=range(nrow))


# -----------------------------------------------------------------------------
# Name / label normalisation
# -----------------------------------------------------------------------------


def _tolower(s: str | None) -> str | None:
    """R ``tolower()`` (per-character ``towlower``)."""
    if s is None:
        return None
    low = s.lower()
    if len(low) == len(s):
        return low
    return "".join(c.lower()[0] for c in s)


def _toupper(s: str | None) -> str | None:
    """R ``toupper()`` (per-character ``towupper``)."""
    if s is None:
        return None
    up = s.upper()
    if len(up) == len(s):
        return up
    return "".join(c.upper()[0] if len(c.upper()) == 1 else c for c in s)


def _like(x: Any, out: list[Any]) -> Any:
    """Return *out* in the container kind of *x* (scalar, list or Series)."""
    if isinstance(x, pd.Series):
        return pd.Series(out, index=x.index, dtype="string")
    if isinstance(x, str) or x is None or not isinstance(x, Iterable):
        return out[0]
    return out


def _normalize_varname_one(s: str | None) -> str | None:
    if s is None:
        return None
    s = trimws(_tolower(s))
    s = gsub("[_]+", " ", s)
    s = gsub(r"\s+", " ", s)
    s = gsub("^[.]+|[.]+$", "", s)
    return trimws(s)


def normalize_varname(x: Any) -> Any:
    """Normalise variable/column names for matching.

    Port of ``R/data_check_helpers.R::normalize_varname()``: lowercase,
    underscores -> space, collapse whitespace, strip leading/trailing dots.
    """
    return _like(x, [_normalize_varname_one(v) for v in _chr_vec(x)])


@functools.cache
def _porter() -> Any:
    try:
        import snowballstemmer
    except ImportError:  # pragma: no cover - optional dependency
        return None
    return snowballstemmer.stemmer("porter")


def _stem_words(s: str | None) -> str:
    """Port of ``.stem_words()``: Porter-stem each word (SnowballC semantics).

    Falls back to R's crude trailing-"s" stripper when ``snowballstemmer`` is
    unavailable (as R does without SnowballC).
    """
    words = [s] if s is None else [w for w in s.split(" ") if w != ""]
    if not words:
        return ""
    stemmer = _porter()
    if stemmer is not None:
        return " ".join("NA" if w is None else stemmer.stemWord(w) for w in words)
    return " ".join(
        "NA" if w is None else gsub("^([a-z]{7,})s$", r"\1", w, perl=True) for w in words
    )


def _normalize_label_one(s: str | None) -> str:
    if s is not None:
        s = _tolower(s)
        s = gsub("'s|’s|‘s", "", s, perl=True)
        s = gsub("[^a-z0-9 ]", " ", s)
        s = gsub(r"\s+", " ", trimws(s))
    return _stem_words(s)


def normalize_label(x: Any) -> Any:
    """Normalise a label for semantic-equivalence comparison.

    Port of ``R/data_check_helpers.R::normalize_label()``: strip possessives and
    punctuation, Porter-stem each word, collapse whitespace.
    """
    out = [_normalize_label_one(v) for v in _chr_vec(x)]
    return _like(x, out)


def _normalize_header_one(s: str | None) -> str | None:
    if s is None:
        return None
    s = _tolower(trimws(s))
    s = gsub("[^a-z0-9]+", " ", s)
    return trimws(gsub(r"\s+", " ", s))


def _normalize_header(x: Any) -> Any:
    """Port of ``.normalize_header()``: lowercase, punctuation runs -> one space."""
    return _like(x, [_normalize_header_one(v) for v in _chr_vec(x)])


# -----------------------------------------------------------------------------
# Codebook header detection
# -----------------------------------------------------------------------------

#: R: .cb_var_header_re
_CB_VAR_HEADER_RE = (
    "^(variable|variables|var|vars|varname|varnames|item|items|"
    "field|fields|column|columns|name|names|code|codes|id)$"
    "|^(variable|var|item|field|column|col) (name|names)$"
)
#: R: .cb_lab_header_re
_CB_LAB_HEADER_RE = (
    "^((variable|var|item|question|response|full|general|specific|short|long) )?"
    "(label|labels|description|descriptions|desc|definition|definitions|meaning|"
    "explanation|explanations|text|wording|interpretation|details|content)$"
    "|^(questions?)$"
    "|^((full|long|complete) names?)$"
)


def _find_codebook_cols(col_names: Any) -> dict[str, Any] | None:
    """Port of ``.find_codebook_cols()``: the variable and label header columns.

    Returns ``{"var_col": ..., "lab_col": ...}`` (the original header values)
    or ``None``.
    """
    names = _vec(col_names)
    nm = [_normalize_header_one(v) for v in _chr_vec(names)]
    is_lab = grepl(_CB_LAB_HEADER_RE, nm, perl=True)
    is_var = grepl(_CB_VAR_HEADER_RE, nm, perl=True)
    var_col = next(
        (c for c, v, lab in zip(names, is_var, is_lab, strict=True) if v and not lab), None
    )
    lab_col = next((c for c, lab in zip(names, is_lab, strict=True) if lab), None)
    if _na(var_col) or _na(lab_col):
        return None
    return {"var_col": var_col, "lab_col": lab_col}


def _first_header(re: str, col_names: Any) -> Any:
    names = _vec(col_names)
    hits = grepl(re, [_normalize_header_one(v) for v in _chr_vec(names)], perl=True)
    return next((c for c, h in zip(names, hits, strict=True) if h), None)


_MISSING_COL_RE = (
    "^((assigned|declared|defined) )?(missing|missings|"
    "missing values?|missing codes?|missing data|"
    "na values?|na codes?|missing value codes?)$"
)
_VALUE_LABEL_COL_RE = (
    "^(value labels?|values?|coding|categor(y|ies)|"
    "response options?|response codes?|levels?|value meanings?|"
    "valid values|value codes?|scoring|answers|"
    "variable levels|allowe?d values|allowable values|"
    "possible values|permitted values)$"
)
_QUESTION_COL_RE = (
    "^(question|questions|question text|question wording|"
    "question in survey|item text|item wording|prompt|"
    "survey question|item description)$"
)
_CODING_COL_RE = (
    "^(coding instructions?|coding instruction|instructions?|"
    "transformations?|recodings?|recoding|calculations?|"
    "calculation recoding|derivations?|derived|derivation description|"
    "computations?|computed|how computed|scoring|scoring instructions?)$"
)


def _find_missing_value_col(col_names: Any) -> Any:
    """Port of ``.find_missing_value_col()`` (``None`` when absent)."""
    return _first_header(_MISSING_COL_RE, col_names)


def _find_value_label_col(col_names: Any) -> Any:
    """Port of ``.find_value_label_col()`` (``None`` when absent)."""
    return _first_header(_VALUE_LABEL_COL_RE, col_names)


def _find_question_col(col_names: Any) -> Any:
    """Port of ``.find_question_col()`` (``None`` when absent)."""
    return _first_header(_QUESTION_COL_RE, col_names)


def _find_coding_instruction_col(col_names: Any) -> Any:
    """Port of ``.find_coding_instruction_col()`` (``None`` when absent)."""
    return _first_header(_CODING_COL_RE, col_names)


def _empty_codebook_vars() -> pd.DataFrame:
    """Port of ``.empty_codebook_vars()``: the zero-row codebook-variable table."""
    return chr_frame(
        {
            "codebook_variable": [],
            "label": [],
            "codebook_source": [],
            "group": [],
            "value_labels": [],
            "missing_values": [],
            "question": [],
            "coding_instructions": [],
            "parse_method": [],
            "paper_id": [],
        },
        0,
    )


# -----------------------------------------------------------------------------
# JSON as jsonlite writes and reads it
# -----------------------------------------------------------------------------

_JSON_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t",
                 "\b": "\\b", "\f": "\\f"}  # fmt: skip


def _json_str(s: str) -> str:
    """A JSON string literal as jsonlite writes it (UTF-8 kept, controls escaped)."""
    out = []
    for ch in s:
        esc = _JSON_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _make_unique(names: list[str], sep: str = ".") -> list[str]:
    """R ``make.unique()``."""
    seen = set(names)
    first: set[str] = set()
    counts: dict[str, int] = {}
    out = list(names)
    for i, nm in enumerate(names):
        if nm not in first:
            first.add(nm)
            continue
        cnt = counts.get(nm, 1)
        while f"{nm}{sep}{cnt}" in seen:
            cnt += 1
        new = f"{nm}{sep}{cnt}"
        out[i] = new
        seen.add(new)
        counts[nm] = cnt + 1
    return out


def _json_object(names: list[str | None], values: list[str | None]) -> str:
    """``jsonlite::toJSON(<named list of strings>, auto_unbox = TRUE)``."""
    clean = [str(i + 1) if nm is None or nm == "" else nm for i, nm in enumerate(names)]
    clean = _make_unique(clean)
    items = [
        f"{_json_str(k)}:{'null' if v is None else _json_str(v)}"
        for k, v in zip(clean, values, strict=True)
    ]
    return "{" + ",".join(items) + "}"


def _json_array(values: list[str | None]) -> str:
    """``jsonlite::toJSON(<character vector>)``."""
    return "[" + ",".join("null" if v is None else _json_str(v) for v in values) + "]"


class _JsonObject(list):  # type: ignore[type-arg]
    """A parsed JSON object as ``(key, value)`` pairs (R named list: keeps duplicates)."""

    def names(self) -> list[str]:
        return [k for k, _ in self]

    def values_(self) -> list[Any]:
        return [v for _, v in self]


def _json_loads(text: str) -> Any:
    """``jsonlite::fromJSON(simplifyVector = FALSE)``-shaped parse.

    Objects become :class:`_JsonObject` (ordered pairs, duplicates kept),
    arrays lists, ``null`` ``None``. Raises ``ValueError`` on invalid JSON.
    """
    return json.loads(text, object_pairs_hook=_JsonObject)


def _json_scalar_chr(v: Any) -> str | None:
    """``as.character()`` of a jsonlite scalar (integers beyond int32 are doubles)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v) if -2147483647 <= v <= 2147483647 else as_character(float(v))
    return _chr(v)


def _unlist_scalars(values: list[Any]) -> list[Any]:
    """``unlist()`` of a list of JSON scalars: NULLs dropped, common type."""
    vals = [v for v in values if v is not None]
    if any(isinstance(v, str) for v in vals):
        return [_json_scalar_chr(v) for v in vals]
    if vals and all(isinstance(v, bool) for v in vals):
        return vals
    return [float(v) if isinstance(v, bool) else v for v in vals]


# -----------------------------------------------------------------------------
# Value labels / missing values
# -----------------------------------------------------------------------------


def _encode_value_labels(codes: Any, labels: Any) -> str | None:
    """Encode a code -> label mapping as a JSON string (``None`` when empty).

    Port of ``.encode_value_labels()``, including R's recycling when *codes*
    and *labels* differ in length and jsonlite's name cleaning (blank names
    become their position, duplicates are made unique).
    """
    cv, lv = _vec(codes), _vec(labels)
    lc, ll = len(cv), len(lv)
    n = max(lc, ll) if lc and ll else 0
    keep = []
    for i in range(n):
        lab = lv[i % ll]
        lab_s = _chr(lab)
        keep.append(not _na(cv[i % lc]) and not _na(lab) and _trim(lab_s) != "")
    if not any(keep):
        return None
    idx = [i for i in range(n) if keep[i]]
    values = [_chr(lv[i]) if i < ll else None for i in idx]
    names = [_chr(cv[i]) if i < lc else None for i in idx]
    return _json_object(names, values)


def _decode_value_labels(s: Any) -> Any:
    """Decode a value-labels JSON string (``None`` on failure / NA).

    Port of ``.decode_value_labels()``: a JSON object gives a ``dict`` code ->
    label (R's named vector), an array a list; NA values are dropped.
    """
    if s is None or isinstance(s, list | tuple) or _na(s) or s == "":
        return None
    try:
        parsed = _json_loads(s)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, _JsonObject):
        if not parsed:
            return None
        pairs = [(k, v) for k, v in parsed if v is not None]
        if any(isinstance(v, list) for _, v in pairs):
            return None
        vals = _unlist_scalars([v for _, v in pairs])
        return {k: v for (k, _), v in zip(pairs, vals, strict=True) if not _na(v)}
    if isinstance(parsed, list):
        if not parsed or any(isinstance(v, list) for v in parsed):
            return None
        return [v for v in _unlist_scalars(parsed) if not _na(v)]
    if parsed is None:
        return None
    return [v for v in _unlist_scalars([parsed]) if not _na(v)]


def _encode_missing_values(codes: Any, reasons: Any = None) -> str | None:
    """Port of ``.encode_missing_values()``: missing codes (with reasons) as JSON."""
    cv = [c for c in _vec(codes) if not _na(c)]
    if not cv:
        return None
    if reasons is None:
        return _json_array([_chr(c) for c in cv])
    return _encode_value_labels(cv, reasons)


#: R: .missing_na_re
_MISSING_NA_RE = r"(?i)^\s*n/?a\.?\s*$|(?i)[-:]\s*n/?a\s*[.)]?\s*$"
#: R: .missing_label_re
_MISSING_LABEL_RE = (
    r"(?i)\b("
    r"missing|"
    r"refus(ed|al)?|"
    r"declin(ed|e)?|"
    r"no\s*(answer|response|data)|"
    r"did\s*not\s*respond|"
    r"not\s*(applicable|asked|reported|answered)|"
    r"prefer\s*not\s*to\s*(answer|say|respond)|"
    r"don'?t\s*know|"
    r"skip(ped)?|"
    r"unanswered|"
    r"(left\s*)?blank|"
    r"withheld|"
    r"system\s*missing"
    r")\b"
)


def _is_missing_label(labels: list[Any]) -> list[bool]:
    txt = [_chr(v) for v in labels]
    a = grepl(_MISSING_LABEL_RE, txt, perl=True)
    b = grepl(_MISSING_NA_RE, txt, perl=True)
    return [x or y for x, y in zip(a, b, strict=True)]


def _looks_like_freetext_labels(labs: Any) -> bool | None:
    """Port of ``.looks_like_freetext_labels()``: are these labels free-text prose?

    ``None`` is R's ``NA`` (a missing label).
    """
    txt = [_trim(v) for v in _chr_vec(labs)]
    txt = [t for t in txt if _nzchar(t)]
    if len(txt) < 5:
        return False
    if any(t is None for t in txt):
        return None
    return _mean([len(t) > 40 for t in txt if t is not None]) > 0.2


def _label_pairs(labs: Any) -> list[tuple[Any, Any]]:
    """haven ``labels`` as ``(label text, code)`` pairs."""
    if labs is None:
        return []
    if isinstance(labs, Mapping):
        return list(labs.items())
    if isinstance(labs, pd.Series):
        return list(zip(labs.index.tolist(), labs.tolist(), strict=True))
    return [tuple(p) for p in labs]  # type: ignore[misc]


def _column_attrs(col: Any, attrs: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    if attrs is not None:
        return attrs
    a = getattr(col, "attrs", None)
    return a if isinstance(a, Mapping) else {}


def _dup_key(v: Any) -> Any:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float):
        return float(v)
    return v


def _haven_value_labels(col: Any, attrs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Value labels + declared missing values of one haven column.

    Port of ``.haven_value_labels()``. *attrs* holds the column's R attributes
    (``labels``, ``na_values``, ``na_range``); by default ``col.attrs``.
    Returns ``{"value_labels": <json|None>, "missing_values": <json|None>}``.
    """
    a = _column_attrs(col, attrs)
    pairs = _label_pairs(a.get("labels"))
    na_values = a.get("na_values")
    na_range = a.get("na_range")
    vl: str | None = None
    miss_codes: list[Any] = []
    miss_reasons: list[str | None] = []
    if pairs:
        freetext = _looks_like_freetext_labels([p[0] for p in pairs])
        if freetext is None:  # R: `&& NA` inside if()
            raise ValueError("missing value where TRUE/FALSE needed")
        if freetext:
            pairs = []
    if pairs:
        codes = [p[1] for p in pairs]
        reasons = [p[0] for p in pairs]
        vl = _encode_value_labels(codes, reasons)
        is_miss = _is_missing_label(reasons)
        miss_codes += [c for c, m in zip(codes, is_miss, strict=True) if m]
        miss_reasons += [r for r, m in zip(reasons, is_miss, strict=True) if m]
    if na_values is not None:
        nv = _vec(na_values)
        miss_codes += nv
        miss_reasons += [None] * len(nv)
    if na_range is not None:
        nr = _vec(na_range)
        if len(nr) == 2 and all(
            not _na(v) and isinstance(v, int | float) and math.isfinite(v) for v in nr
        ):
            miss_codes += nr
            miss_reasons += ["range", "range"]
    mv: str | None = None
    if miss_codes:
        seen: set[Any] = set()
        keep = []
        for c in miss_codes:
            k = _dup_key(c)
            keep.append(k not in seen)
            seen.add(k)
        kc = [c for c, k in zip(miss_codes, keep, strict=True) if k]
        kr = [r for r, k in zip(miss_reasons, keep, strict=True) if k]
        mv = (
            _encode_missing_values(kc)
            if all(r is None for r in kr)
            else _encode_value_labels(kc, kr)
        )
    return {"value_labels": vl, "missing_values": mv}


_RX_VL_NUMERIC = r"^-?\d+(\.\d+)?$"


def _vl_is_numeric(x: Any) -> Any:
    """Port of ``.vl_is_numeric()``: is each string a bare number?"""
    return grepl(_RX_VL_NUMERIC, trimws(x))


_RX_ANCHORS = r"(-?\d+(?:\.\d+)?)\s*\(([^)]{1,60})\)"
_RX_ANCHOR = r"^(-?\d+(?:\.\d+)?)\s*\(([^)]{1,60})\)$"
_RX_SPLIT = (
    r"\s*[;|\n]\s*"
    r"|\s*,\s*(?=\s*-?\d+(\.\d+)?\s*[:=])"
    r"|\s*,\s*(?=[^,;|=:]{1,40}[:=]\s*-?\d+(\.\d+)?\s*(,|;|\||$))"
)
_RX_PAIR = r"^\s*(.+?)\s*[:=]\s*(.+?)\s*$"
_RX_CODE_LABEL = r"^\s*(-?\d+(?:\.\d+)?)\s*(?:[-" + "–—" + r").]\s*|\s+)([A-Za-z].*?)\s*$"


def _vl_split_pairs(s: str) -> dict[str, list[str]] | None:
    """Split a codebook "values" cell into raw left/right halves of each entry.

    Port of ``.vl_split_pairs()``: returns ``{"lhs": [...], "rhs": [...]}`` or
    ``None``. Direction is decided by :func:`_parse_value_label_text`.
    """
    anchors = regextract_all(_RX_ANCHORS, s, perl=True)
    if len(anchors) >= 2:
        am = regexec(_RX_ANCHOR, anchors, perl=True)
        good = [m for m in am if len(m) == 3]
        if len(good) >= 2:
            return {"lhs": [m[1] for m in good], "rhs": [trimws(m[2]) for m in good]}
    parts = [p for p in strsplit(s, _RX_SPLIT, perl=True) if p is not None]
    parts = [trimws(p) for p in parts if trimws(p) != ""]
    if not parts:
        return None
    m = regexec(_RX_PAIR, parts, perl=True)
    ok = [z for z in m if len(z) == 3]
    if not ok:
        m = regexec(_RX_CODE_LABEL, parts, perl=True)
        ok = [z for z in m if len(z) == 3]
        if not ok:
            return None
    return {"lhs": [z[1] for z in ok], "rhs": [z[2] for z in ok]}


def _parse_value_label_text(s: Any, observed: Any = None) -> str | None:
    """Parse a codebook "values" cell into value-labels JSON (``None`` = NA).

    Port of ``.parse_value_label_text()``: the numeric side is the code; when
    neither side is numeric, *observed* data values decide the direction.
    """
    if s is None or _na(s) or trimws(_chr(s)) == "":
        return None
    pr = _vl_split_pairs(_chr(s))  # type: ignore[arg-type]
    if pr is None or len(pr["lhs"]) < 2:
        return None
    l_num = _mean(_vl_is_numeric(pr["lhs"]))
    r_num = _mean(_vl_is_numeric(pr["rhs"]))
    codes: list[str] | None = None
    labels: list[str] | None = None
    if l_num >= 0.8 and r_num < 0.8:
        codes, labels = pr["lhs"], pr["rhs"]
    elif r_num >= 0.8 and l_num < 0.8:
        codes, labels = pr["rhs"], pr["lhs"]
    elif observed is not None and len(_vec(observed)):
        ov_all = [_trim(v) for v in _chr_vec(observed)]
        ov = {v for v in dict.fromkeys(ov_all) if v is not None and v != ""}
        hit_l = _mean([trimws(v) in ov for v in pr["lhs"]])
        hit_r = _mean([trimws(v) in ov for v in pr["rhs"]])
        if hit_l > hit_r:
            codes, labels = pr["lhs"], pr["rhs"]
        elif hit_r > hit_l:
            codes, labels = pr["rhs"], pr["lhs"]
    if codes is None:
        return None
    return _encode_value_labels(codes, labels)


def _missing_from_value_labels(vl_json: Any) -> str | None:
    """Port of ``.missing_from_value_labels()``: codes whose label reads as missing."""
    vl = _decode_value_labels(vl_json)
    if not vl:
        return None
    if isinstance(vl, dict):
        names: list[Any] = list(vl.keys())
        vals = list(vl.values())
    else:
        names, vals = [], list(vl)
    is_miss = _is_missing_label(vals)
    if not any(is_miss):
        return None
    return _encode_value_labels(
        [n for n, m in zip(names, is_miss, strict=False) if m],
        [v for v, m in zip(vals, is_miss, strict=True) if m],
    )


# -----------------------------------------------------------------------------
# Positional (headerless) codebooks
# -----------------------------------------------------------------------------

_RX_VARNAME = "^[A-Za-z][A-Za-z0-9_.]{0,30}$"


def _clean_values(x: Any) -> list[str]:
    vals = [_trim(v) for v in _chr_vec(x)]
    return [v for v in vals if v is not None and v != ""]


def _looks_like_varnames(x: Any) -> bool:
    """Port of ``.looks_like_varnames()``: short, mostly unique identifiers?"""
    v = _clean_values(x)
    if len(v) < 3:
        return False
    a = grepl(_RX_VARNAME, v)
    b = grepl(r"\s", v)
    ok = [p and not q for p, q in zip(a, b, strict=True)]
    return _mean(ok) >= 0.8 and len(set(v)) >= 0.8 * len(v)


def _looks_like_wording(x: Any) -> bool:
    """Port of ``.looks_like_wording()``: sentence-like item wording?"""
    v = _clean_values(x)
    if len(v) < 3:
        return False
    return _mean(grepl(r"\s", v)) >= 0.6 and _median([len(s) for s in v]) >= 8


_RX_ANCHOR_CELL = r"^[0-9]+\s*[-=:.)]"
_RX_ANCHOR_PAIR = r"^([0-9]+)\s*[-=:.)]\s*(.+)$"


def _extract_codebook_positional(df: pd.DataFrame | None, src: str) -> pd.DataFrame | None:
    """Positional codebook extractor for sheets with no usable header row.

    Port of ``.extract_codebook_positional()``: a variable-name column next to
    an item-wording column; later "N - Label" anchor columns become a code list.
    """
    if df is None or len(df) < 3 or df.shape[1] < 2:
        return None
    raw = [_chr_vec(df.iloc[:, j]) for j in range(df.shape[1])]
    p = len(raw)
    var_j = lab_j = -1
    for j in range(p - 1):
        if _looks_like_varnames(raw[j]) and _looks_like_wording(raw[j + 1]):
            var_j, lab_j = j, j + 1
            break
    if var_j < 0:
        return None
    keep = [v is not None and trimws(v) != "" for v in raw[var_j]]
    rows = [[col[i] for i in range(len(col)) if keep[i]] for col in raw]
    nrow = sum(keep)
    if nrow < 3:
        return None
    anchor_js = []
    for j in range(lab_j + 1, p):
        vals = _clean_values(rows[j])
        if vals and _mean(grepl(_RX_ANCHOR_CELL, vals)) >= 0.6:
            anchor_js.append(j)
    value_labels: list[str | None] = [None] * nrow
    if anchor_js:
        for i in range(nrow):
            cells = [_trim(rows[j][i]) for j in anchor_js]
            cells = [c for c in cells if c is not None and c != ""]
            m = regexec(_RX_ANCHOR_PAIR, cells)
            codes = [z[1] if len(z) == 3 else None for z in m]
            labs = [trimws(z[2]) if len(z) == 3 else None for z in m]
            ok = [c is not None for c in codes]
            if any(ok):
                value_labels[i] = _encode_value_labels(
                    [c for c, k in zip(codes, ok, strict=True) if k],
                    [lb for lb, k in zip(labs, ok, strict=True) if k],
                )
    return chr_frame(
        {
            "codebook_variable": [_trim(v) for v in rows[var_j]],
            "label": [_trim(v) for v in rows[lab_j]],
            "codebook_source": src,
            "group": None,
            "value_labels": value_labels,
            "missing_values": [_missing_from_value_labels(v) for v in value_labels],
            "question": None,
            "coding_instructions": None,
        },
        nrow,
    )


# -----------------------------------------------------------------------------
# Named-header (structured) codebooks
# -----------------------------------------------------------------------------


def _duplicated(keys: list[Any]) -> list[bool]:
    seen: set[Any] = set()
    out = []
    for k in keys:
        out.append(k in seen)
        seen.add(k)
    return out


def _observed_for(observed: Any, name: str | None) -> Any:
    if not observed or name is None:
        return None
    if isinstance(observed, Mapping):
        return observed.get(name)
    return None


def _declared_missing(x: str | None) -> str | None:
    if x is None or trimws(x) == "":
        return None
    pr = _vl_split_pairs(x)
    if pr is not None and pr["lhs"]:
        if _mean(_vl_is_numeric(pr["lhs"])) >= 0.8:
            return _encode_value_labels(pr["lhs"], pr["rhs"])
        if _mean(_vl_is_numeric(pr["rhs"])) >= 0.8:
            return _encode_value_labels(pr["rhs"], pr["lhs"])
    codes = [trimws(c) for c in strsplit(x, r"\s*[;,|]\s*") if c is not None]
    codes = [c for c, num in zip(codes, _vl_is_numeric(codes), strict=True) if c != "" and num]
    if not codes:
        return None
    return _encode_missing_values(codes)


def _na_str(x: list[str | None]) -> list[str | None]:
    out = []
    for v in x:
        t = _trim(v)
        out.append(t if t is None or t != "" else None)
    return out


def _extract_structured_codebook(
    df: pd.DataFrame | None, src: str, observed: Any = None
) -> pd.DataFrame | None:
    """Extract variable-label pairs from a codebook table with named headers.

    Port of ``.extract_structured_codebook()``: finds the variable / label
    columns, drops content-free repeats, and parses the optional DDI columns
    (value labels, declared missing values, question text, coding
    instructions). *observed* maps a variable name to a sample of its data
    values (resolves text-coded value labels). ``None`` when no header matches.
    """
    if df is None or len(df) == 0 or df.shape[1] < 2:
        return None
    names = list(df.columns)
    cols = _find_codebook_cols(names)
    if cols is None:
        return None
    var_all = _chr_vec(_col(df, cols["var_col"]))
    lab_all = _chr_vec(_col(df, cols["lab_col"]))
    sel = [i for i, v in enumerate(var_all) if _nzchar(_trim(v))]
    if not sel:
        return None
    name_key = [_trim(var_all[i]) for i in sel]
    lab_blank = [not _nzchar(_trim(lab_all[i])) for i in sel]
    dup = _duplicated(name_key)
    sel = [i for i, d, b in zip(sel, dup, lab_blank, strict=True) if not (d and b)]
    if not sel:
        return None
    n = len(sel)
    val_col = _find_value_label_col(names)
    q_col = _find_question_col(names)
    ci_col = _find_coding_instruction_col(names)
    if val_col is not None:
        vals = _chr_vec(_col(df, val_col))
        value_labels = [
            _parse_value_label_text(vals[i], _observed_for(observed, _trim(var_all[i])))
            for i in sel
        ]
    else:
        value_labels = [None] * n
    missing_values = [_missing_from_value_labels(v) for v in value_labels]
    m_col = _find_missing_value_col(names)
    if m_col is not None:
        mvals = _chr_vec(_col(df, m_col))
        declared = [_declared_missing(mvals[i]) for i in sel]
        missing_values = [
            m if d is None else d for m, d in zip(missing_values, declared, strict=True)
        ]
    question: Any = None
    if q_col is not None:
        qv = _chr_vec(_col(df, q_col))
        question = _na_str([qv[i] for i in sel])
    coding: Any = None
    if ci_col is not None:
        cv = _chr_vec(_col(df, ci_col))
        coding = _na_str([cv[i] for i in sel])
    return chr_frame(
        {
            "codebook_variable": [var_all[i] for i in sel],
            "label": [lab_all[i] for i in sel],
            "codebook_source": src,
            "group": None,
            "value_labels": value_labels,
            "missing_values": missing_values,
            "question": question,
            "coding_instructions": coding,
        },
        n,
    )


def _attr(attrs: Mapping[str, Any], which: str) -> Any:
    """R ``attr(x, which)``: exact name first, else a unique partial match.

    So ``attr(x, "label")`` of a column with value ``labels`` but no variable
    label returns the value labels (metacheck then uses the first code).
    """
    if which in attrs:
        return attrs[which]
    hits = [k for k in attrs if isinstance(k, str) and k.startswith(which)]
    return attrs[hits[0]] if len(hits) == 1 else None


def _col_attrs(df: pd.DataFrame, j: int) -> Mapping[str, Any]:
    """The R attributes of column *j* (``df.attrs["col_attrs"]`` or ``Series.attrs``)."""
    name = df.columns[j]
    ca = df.attrs.get("col_attrs") if isinstance(df.attrs, Mapping) else None
    if isinstance(ca, Mapping) and name in ca:
        return ca[name] or {}
    a = getattr(df.iloc[:, j], "attrs", None)
    return a if isinstance(a, Mapping) else {}


def _extract_haven_labels(
    df: pd.DataFrame, src: str, group: str | None = None
) -> pd.DataFrame | None:
    """Embedded variable / value labels of a haven-read data frame.

    Port of ``.extract_haven_labels()``; column attributes come from
    ``df.attrs["col_attrs"]`` (see the module docstring). ``None`` when no
    column carries a label or a code list.
    """
    names = list(df.columns)
    first_j = {}
    for j, nm in enumerate(names):
        first_j.setdefault(nm, j)
    labels: list[str | None] = []
    vls: list[str | None] = []
    mvs: list[str | None] = []
    for nm in names:
        a = _col_attrs(df, first_j[nm])
        lbl = _attr(a, "label")
        lv = [p[1] for p in _label_pairs(lbl)] if isinstance(lbl, Mapping) else _vec(lbl)
        labels.append(None if lbl is None or not lv else _trim(_chr(lv[0])))
        res = _haven_value_labels(None, a)
        vls.append(res["value_labels"])
        mvs.append(res["missing_values"])
    keep = [
        (lab is not None and lab != "") or vl is not None
        for lab, vl in zip(labels, vls, strict=True)
    ]
    if not any(keep):
        return None
    idx = [i for i, k in enumerate(keep) if k]
    return chr_frame(
        {
            "codebook_variable": [_chr(names[i]) for i in idx],
            "label": [labels[i] for i in idx],
            "codebook_source": src,
            "group": group,
            "value_labels": [vls[i] for i in idx],
            "missing_values": [mvs[i] for i in idx],
            "question": None,
            "coding_instructions": None,
        },
        len(idx),
    )


def _cb_is_definition_line(x: Any) -> Any:
    """Port of ``.cb_is_definition_line()``: does a line look like a definition?"""
    return grepl(
        "^[ ]{0,8}[A-Za-z_][A-Za-z0-9_.$#-]{0,40}[ ]*([:=]|[ ]{2,}|\t)[ ]*[^ ].{3,}$",
        x,
        perl=True,
    )
