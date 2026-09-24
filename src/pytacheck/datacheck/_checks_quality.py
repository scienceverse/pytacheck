"""Data-quality checks on single columns (private; see :mod:`pytacheck.datacheck.checks`).

Port of ``R/data_check_helpers.R``: ``data_check_scale_values()`` through
``data_check_colname_collisions()``.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from pytacheck._r.base import plural, r_sort_key, signif
from pytacheck._r.regex import grepl, gsub, regextract_all
from pytacheck.datacheck._checks_rvec import (
    as_numeric_str,
    chr,
    dbl_chr,
    fmt_d,
    fmt_g,
    fmt_pct0,
    num,
    quantile7,
    r_colon,
    rvec,
    tolower,
    trim,
    unique,
)

__all__ = [
    "_DATA_MISSING_SENTINELS",
    "data_check_case_issues",
    "data_check_colname",
    "data_check_colname_collisions",
    "data_check_constant",
    "data_check_design_name",
    "data_check_empty",
    "data_check_numeric_in_text",
    "data_check_outliers",
    "data_check_scale_values",
    "data_check_spss_filter",
    "data_check_whitespace",
]

#: R: .data_missing_sentinels -- numeric codes commonly used for missing data
#: (defined next to data_check_scale_values() in R/data_check_helpers.R).
_DATA_MISSING_SENTINELS: tuple[float, ...] = (
    # 9x-block (don't know / refused / not applicable)
    97, 98, 99,
    997, 998, 999,
    9997, 9998, 9999,
    99997, 99998, 99999,
    # repeated-digit 8- and 7-families
    88, 888, 8888, 88888,
    77, 777, 7777, 77777,
    # extreme repeated placeholders at wide field widths
    999999, 888888, 777777, 99999999,
    # the two attested negative codings
    -99, -999,
)  # fmt: skip


def _scale_typo_of(v: float, lo: float, hi: float) -> Any:
    """``.scale_typo_of()`` (ported in :mod:`pytacheck.datacheck.columns`)."""
    from pytacheck.datacheck.columns import _scale_typo_of as fn

    return fn(v, lo, hi)


def _finite(v: Any) -> bool:
    return v is not None and v == v and not math.isinf(v)


def _mean(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else math.nan


def _seq_int(a: float, b: float) -> list[int]:
    """``seq.int(a, b)`` for whole numbers ``a <= b``."""
    return list(range(int(a), int(b) + 1))


def data_check_scale_values(
    x: Any,
    sentinels: Any = _DATA_MISSING_SENTINELS,
    declared: Any = None,
    valid_values: Any = None,
    valid_range: Any = None,
    n_max: int = 10,
    min_ground_truth_coverage: float = 0.5,
) -> dict[str, Any]:
    """Flag values that fall outside a rating scale's valid range.

    Port of ``R/data_check_helpers.R::data_check_scale_values()``. Returns a
    dict with ``problem``, ``message``, ``values`` (the out-of-scale values),
    ``lower``/``upper`` (the valid range) and ``classes`` (``"missing"``,
    ``"typo:<intended>"`` or ``"unexplained"`` per value). Ground truth
    (*valid_values* / *valid_range*) is used only when it covers at least
    *min_ground_truth_coverage* of the data; otherwise the range is inferred
    with ``.detect_likert_scale()``.
    """
    none: dict[str, Any] = {
        "problem": False,
        "message": "",
        "values": None,
        "lower": math.nan,
        "upper": math.nan,
        "classes": [],
    }
    v = rvec(x)
    if not v.is_numeric:
        return none
    xv = [e for e in v.values if _finite(e)]
    if not xv:
        return none

    valid_set: list[Any] | None
    lo: Any = None
    hi: Any = None
    if valid_values is not None and len(rvec(valid_values)) > 0:
        vv = sorted({f for f in num(valid_values) if f is not None and f == f})
        vv = [f for f in vv if not math.isinf(f)]
        if not vv:
            return none
        vv_int = all(f == round(f) for f in vv)
        if vv_int and len(vv) >= 2 and (vv[-1] - vv[0]) >= 2:
            x_int = sorted({e for e in xv if e == round(e)})
            vv_set = set(vv)
            has_interior = any(vv[0] < e < vv[-1] and e not in vv_set for e in x_int)
            valid_set = _seq_int(vv[0], vv[-1]) if has_interior else vv
        else:
            valid_set = vv
        lo = min(valid_set)
        hi = max(valid_set)
        is_contig_int = (
            all(f == round(f) for f in valid_set)
            and len(valid_set) >= 2
            and all(b - a == 1 for a, b in itertools.pairwise(valid_set))
        )
        if is_contig_int and lo >= 0:
            x_int = sorted({e for e in xv if e == round(e)})
            natural_floor = 0 if 0 in x_int else 1
            if natural_floor < lo <= natural_floor + 2:
                lo = natural_floor
                valid_set = _seq_int(lo, hi)
    elif valid_range is not None and _finite_pair(valid_range):
        vr = [float(f) for f in num(valid_range)]  # type: ignore[arg-type]
        lo = min(vr)
        hi = max(vr)
        valid_set = r_colon(lo, hi)
    else:
        valid_set = None

    if valid_set is not None:
        vs = set(valid_set)
        if _mean([e in vs for e in xv]) < min_ground_truth_coverage:
            valid_set = None

    if valid_set is None:
        from pytacheck.datacheck.files import _detect_likert_scale

        sc = _detect_likert_scale(xv)
        if sc is None:
            return none
        lo = sc["lo"]
        hi = sc["hi"]
        valid_set = r_colon(lo, hi)

    vs = set(valid_set)
    out = sorted(unique(e for e in xv if e not in vs))
    if not out:
        return {
            "problem": False,
            "message": "",
            "values": None,
            "lower": lo,
            "upper": hi,
            "classes": [],
        }

    declared_num = {f for f in num(declared) if f is not None} if declared is not None else set()
    sentinel_set = {f for f in num(sentinels) if f is not None} if sentinels is not None else set()

    def classify(e: Any) -> str:
        if e in declared_num:
            return "missing"
        if e in sentinel_set:
            return "missing"
        typo = _scale_typo_of(e, lo, hi)
        if typo is not None and typo == typo:
            return "typo:" + (str(typo) if isinstance(typo, int) else dbl_chr(typo))
        return "unexplained"

    classes = [classify(e) for e in out]

    def describe(e: Any, cls: str) -> str:
        s = str(e) if isinstance(e, int) else dbl_chr(e)
        if cls == "missing":
            return f"{s} (looks like a missing-data code -> recode to NA)"
        if cls.startswith("typo:"):
            return f"{s} (looks like a typo of {cls[5:]})"
        return f"{s} (outside the scale, cause unclear)"

    n_show = min(len(out), int(n_max))
    parts = [describe(out[i], classes[i]) for i in range(n_show)]
    msg = (
        f"{len(out)} value{plural(len(out))} outside the {fmt_d(lo)}-{fmt_d(hi)} scale: "
        + ", ".join(parts)
        + (", ..." if len(out) > n_max else "")
    )
    return {
        "problem": True,
        "message": msg,
        "values": out,
        "lower": lo,
        "upper": hi,
        "classes": classes,
    }


def _finite_pair(valid_range: Any) -> bool:
    """``length(valid_range) == 2 && all(is.finite(valid_range))``."""
    v = rvec(valid_range)
    if len(v) != 2 or v.kind not in ("double", "integer", "logical"):
        return False
    return all(_finite(f) for f in num(v))


def data_check_outliers(x: Any, k: float = 1.5, n_max: int = 10) -> dict[str, Any]:
    """Flag Tukey (IQR) outliers in a numeric vector.

    Port of ``R/data_check_helpers.R::data_check_outliers()``: values below
    ``Q1 - k * IQR`` or above ``Q3 + k * IQR`` (type-7 quantiles).
    """
    none: dict[str, Any] = {
        "problem": False,
        "message": "",
        "values": None,
        "lower": math.nan,
        "upper": math.nan,
    }
    v = rvec(x)
    if not v.is_numeric:
        return none
    xs = [e for e in v.values if e is not None]
    if len(xs) < 4:
        return none
    q1, q3 = quantile7(xs, (0.25, 0.75))
    iqr = q3 - q1
    if iqr != iqr:
        raise ValueError("missing value where TRUE/FALSE needed")
    if iqr == 0:
        return none
    lower = q1 - k * iqr
    upper = q3 + k * iqr
    out = unique(e for e in xs if e < lower or e > upper)
    if not out:
        return {"problem": False, "message": "", "values": None, "lower": lower, "upper": upper}
    shown = sorted(out)[: int(n_max)]
    shown_txt = ", ".join(_signif4(e) for e in shown)
    msg = (
        f"{len(out)} outlier value{plural(len(out))} outside "
        f"[{fmt_g(lower, 3)}, {fmt_g(upper, 3)}]: {shown_txt}"
        + (", ..." if len(out) > n_max else "")
    )
    return {"problem": True, "message": msg, "values": out, "lower": lower, "upper": upper}


def _signif4(e: float) -> str:
    f = float(e)
    if not math.isfinite(f):
        return dbl_chr(f)
    return dbl_chr(float(signif(f, 4)))


def _table(v: Any) -> tuple[list[str], list[int]]:
    """``table(x)`` of a vector without NA: level labels and counts."""
    if v.kind == "factor":
        labels = list(v.levels or [])
        counts = dict.fromkeys(labels, 0)
        for s in v.values:
            counts[s] += 1
        return labels, [counts[lab] for lab in labels]
    x_chr = chr(v)
    y = unique(zip(v.values, x_chr, strict=True))
    if v.kind == "character":
        y.sort(key=lambda p: r_sort_key(p[0]))
    else:
        y.sort(key=lambda p: p[0])
    labels = unique(p[1] for p in y)
    counts = dict.fromkeys(labels, 0)
    for s in x_chr:
        counts[s] += 1
    return labels, [counts[lab] for lab in labels]


def data_check_constant(x: Any, threshold: float = 0.99) -> dict[str, Any]:
    """Flag a constant or near-constant column.

    Port of ``R/data_check_helpers.R::data_check_constant()``: the column is
    constant when it has one distinct non-missing value, near-constant
    (``near = True``) when the most common value covers at least *threshold*.
    """
    v = rvec(x).drop_na()
    if len(v) == 0:
        return {"problem": False, "message": "", "values": None, "near": False}
    labels, counts = _table(v)
    top = max(range(len(counts)), key=lambda i: (counts[i], -i))
    top_frac = counts[top] / len(v)
    if len(counts) == 1:
        return {
            "problem": True,
            "message": f'Column is constant: every value is "{labels[0]}".',
            "values": labels[0],
            "near": False,
        }
    if top_frac >= threshold:
        return {
            "problem": True,
            "message": f'Near-constant: {fmt_pct0(100 * top_frac)}% of values are "{labels[top]}".',
            "values": labels[top],
            "near": True,
        }
    return {"problem": False, "message": "", "values": None, "near": False}


def data_check_empty(x: Any) -> dict[str, Any]:
    """Flag a column with no observed values (all NA, or blank text).

    Port of ``R/data_check_helpers.R::data_check_empty()``.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    v = rvec(x)
    n = len(v)
    if n == 0:
        return none
    if v.is_numeric:
        filled = any(e is not None for e in v.values)
    else:
        filled = any(s is not None and trim(s) != "" for s in chr(v))
    if filled:
        return none
    return {
        "problem": True,
        "message": f"Column is empty: all {n} value{plural(n)} {'is' if n == 1 else 'are'} missing.",
        "values": None,
    }


_DESIGN_NAME_RE = (
    r"(?i)(^|[._ -])(cond(ition)?|grp|group|treat(ment)?|arm|dose|manip(ulation)?"
    r"|intervention)([._ -]|[0-9]|$)"
)


def data_check_design_name(col: Any) -> Any:
    """Does a column name look like an experimental design variable?

    Port of ``R/data_check_helpers.R::data_check_design_name()``
    (vectorised like ``grepl()``: a string gives a bool, a list a list).
    """
    return grepl(_DESIGN_NAME_RE, col, perl=True)


_SPSS_ALL = (
    'SPSS "Select Cases" filter variable: every row is selected (value 1), so the file '
    "appears to have been saved after deleting unselected cases -- the shared data are a "
    "pre-filtered subset of what was collected."
)


def data_check_spss_filter(col: Any, x: Any) -> dict[str, Any]:
    """Flag an SPSS "Select Cases" filter variable (``filter_$``).

    Port of ``R/data_check_helpers.R::data_check_spss_filter()``; ``values``
    is ``{"selected": n, "total": n}``.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    if col is None:  # grepl(p, NA) is FALSE
        return none
    cols = chr(col)
    if not cols:
        raise ValueError("argument is of length zero")
    if len(cols) > 1:
        raise ValueError("the condition has length > 1")
    if not grepl(r"(?i)^filter_[$._]?$", cols[0], perl=True):
        return none
    v = [f for f in num(x) if f is not None and f == f]
    if not v:
        return none
    n_sel = sum(1 for f in v if f == 1)
    if n_sel == len(v):
        msg = _SPSS_ALL
    else:
        msg = (
            f'SPSS "Select Cases" filter variable: {n_sel} of {len(v)} rows are selected '
            "(value 1). The reported analyses likely used only the selected rows; re-apply "
            "this filter to reproduce them."
        )
    return {"problem": True, "message": msg, "values": {"selected": n_sel, "total": len(v)}}


def data_check_case_issues(x: Any) -> dict[str, Any]:
    """Flag categorical levels that differ only by letter case ("Male"/"male").

    Port of ``R/data_check_helpers.R::data_check_case_issues()``.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    v = rvec(x)
    if v.is_numeric:
        return none
    xs = unique(s for s in chr(v) if s is not None and trim(s) != "")
    if not xs:
        return none
    lower = [tolower(s) for s in xs]
    seen: set[str | None] = set()
    dup: list[str | None] = []
    for low in lower:
        if low in seen:
            dup.append(low)
        else:
            seen.add(low)
    if not dup:
        return none
    groups = [
        "/".join(s for s, low in zip(xs, lower, strict=True) if low == d) for d in unique(dup)
    ]
    return {
        "problem": True,
        "message": "Categories differing only by case: " + "; ".join(groups),
        "values": groups,
    }


def data_check_whitespace(x: Any) -> dict[str, Any]:
    """Flag values with leading or trailing whitespace.

    Port of ``R/data_check_helpers.R::data_check_whitespace()``.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    v = rvec(x)
    if v.is_numeric:
        return none
    padded = unique(s for s in chr(v) if s is not None and (t := trim(s)) != s and t != "")
    if not padded:
        return none
    shown = ", ".join(f'"{s}"' for s in padded[:10])
    return {
        "problem": True,
        "message": f"{len(padded)} value{plural(len(padded))} with leading/trailing whitespace: {shown}",
        "values": padded,
    }


def data_check_numeric_in_text(x: Any, threshold: float = 0.8, n_max: int = 10) -> dict[str, Any]:
    """Flag a mostly-numeric column stored as text.

    Port of ``R/data_check_helpers.R::data_check_numeric_in_text()``: at least
    *threshold* (but not all) of the non-empty values parse as numbers, with
    comma decimals accepted.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    v = rvec(x)
    if v.is_numeric:
        return none
    xs = [t for s in chr(v) if s is not None and (t := trim(s)) != ""]  # type: ignore[misc]
    if len(xs) < 5:
        return none
    ok = [(f := as_numeric_str(s.replace(",", "."))) is not None and f == f for s in xs]
    frac_num = sum(ok) / len(ok)
    if frac_num < threshold or frac_num >= 1:
        return none
    bad = unique(s for s, good in zip(xs, ok, strict=True) if not good)
    shown = ", ".join(f'"{s}"' for s in bad[: int(n_max)])
    return {
        "problem": True,
        "message": (
            f"Column is {fmt_pct0(100 * frac_num)}% numeric but {len(bad)} "
            f"value{plural(len(bad))} cannot be parsed: {shown}"
        ),
        "values": bad,
    }


_ILLEGAL_RE = r'[<>:"/\\|?*]'


def data_check_colname(col_name: Any, max_chars: int = 64) -> dict[str, Any]:
    """Flag a problematic column name (file-illegal/control characters, padding, length).

    Port of ``R/data_check_helpers.R::data_check_colname()``.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    nms = chr(col_name)
    if len(nms) != 1 or nms[0] is None:
        return none
    nm = nms[0]
    illegal = regextract_all(_ILLEGAL_RE, nm)
    ctrl = regextract_all("[[:cntrl:]]", nm)
    issues: list[str] = []
    if illegal:
        quoted = ", ".join(unique(f'"{c}"' for c in illegal))
        issues.append(f"characters not allowed in file names ({quoted})")
    if ctrl:
        issues.append(f"{len(ctrl)} control character{plural(len(ctrl))} (tab/newline)")
    if nm != trim(nm):
        issues.append("leading/trailing whitespace")
    if len(nm) > max_chars:
        issues.append(
            f"a length of {len(nm)} characters (SPSS allows at most 64, SAS and Stata 32, "
            "so this name cannot be imported there without renaming)"
        )
    if not issues:
        return none
    bad_chars = unique([*illegal, *ctrl])
    return {
        "problem": True,
        "message": (
            f"Column name has {'; '.join(issues)}. Such names break when reused as file names "
            "or in code; prefer short names of letters, digits and underscores."
            + (
                " A name like this can also mean the file's header was not parsed as intended."
                if bad_chars
                else ""
            )
        ),
        "values": bad_chars if bad_chars else None,
    }


def data_check_colname_collisions(col_names: Any) -> dict[str, str]:
    """Flag column names that collide once special characters are replaced by ``_``.

    Port of ``R/data_check_helpers.R::data_check_colname_collisions()``:
    returns a dict mapping each colliding column name to a message (empty
    when all names stay distinct).

    Empty (``""``) column names get no entry: R's ``out[[""]] <- msg``
    appends an unnamed element that ``out[[""]]`` can never retrieve, so a
    lookup by name finds nothing in either language (the unreachable
    elements themselves cannot be held in a dict).
    """
    nms = chr(col_names)
    keys = gsub(r"[^\p{L}\p{N}]", "_", nms, perl=True)
    seen: set[str | None] = set()
    dups: list[str | None] = []
    for k in keys:
        if k in seen:
            dups.append(k)
        else:
            seen.add(k)
    out: dict[str, str] = {}
    for k in unique(dups):
        if k is None:
            continue
        idx = [i for i, kk in enumerate(keys) if kk == k]
        members = [nms[i] for i in idx]
        for i in idx:
            me = nms[i]
            if me == "":
                continue
            others = unique(m for m in members if m != me)
            if not others:
                n_same = sum(1 for m in members if m == me) - 1
                desc = f"{n_same} other identically named column{plural(n_same)}"
            else:
                desc = ", ".join(f'"{o}"' for o in others[:5])
            out[me] = (  # type: ignore[index]
                f'Column name becomes "{k}" when special characters are replaced, the same as '
                f"{desc}: tools that sanitize names (R's make.names(), SPSS/SAS/Stata import, "
                "codebook section links) cannot tell these columns apart."
            )
    return out
