"""Data-quality checks on single columns (private; see :mod:`metacheck.datacheck.checks`).

Port of ``R/data_check_helpers.R``: ``data_check_scale_values()`` through
``data_check_colname_collisions()``.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Callable
from typing import Any

from metacheck._r.base import plural, r_sort_key, signif
from metacheck._r.regex import grepl, gsub, regextract_all
from metacheck.datacheck._checks_rvec import (
    RVec,
    as_numeric_str,
    chr,
    chr_counts,
    dbl_chr,
    fmt_d,
    fmt_g,
    fmt_pct0,
    num,
    numeric_array,
    quantile7,
    r_colon,
    rvec,
    tolower,
    trim,
    trimmed_counts,
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
    interval = False  # a continuous [lo, hi] range rather than a set of levels
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
        # A range with whole-number bounds is a rating scale (its integer
        # levels); fractional bounds describe a continuous interval. (R builds
        # lo:hi either way -- 1.5:3.5 is {1.5, 2.5, 3.5}, so 2.0 would be "out"
        # -- and then fails formatting the bounds with %d.)
        interval = lo != round(lo) or hi != round(hi)
        valid_set = None if interval else r_colon(lo, hi)
    else:
        valid_set = None

    if valid_set is not None or interval:
        inside = _inside(xv, valid_set, lo, hi)
        if _mean(inside) < min_ground_truth_coverage:
            valid_set = None
            interval = False

    if valid_set is None and not interval:
        sc = _likert_scale(xv)
        if sc is None:
            return none
        lo = sc["lo"]
        hi = sc["hi"]
        valid_set = r_colon(lo, hi)

    inside = _inside(xv, valid_set, lo, hi)
    out = sorted(unique(e for e, ok in zip(xv, inside, strict=True) if not ok))
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

    # .scale_typo_of(): an int e (integer column) takes R's integer path there
    from metacheck.datacheck.columns import _scale_typo_of

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
        f"{len(out)} value{plural(len(out))} outside the {_bound(lo)}-{_bound(hi)} scale: "
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


def _inside(xv: list[Any], valid_set: list[Any] | None, lo: Any, hi: Any) -> list[bool]:
    """Which values are valid: members of *valid_set*, or within ``[lo, hi]``
    when there is no set (a continuous range)."""
    if valid_set is None:
        return [lo <= e <= hi for e in xv]
    vs = set(valid_set)
    return [e in vs for e in xv]


def _bound(v: Any) -> str:
    """A scale bound in a message: ``%d`` for a whole number, else its decimal
    form (R's ``sprintf("%d")`` fails on a fractional ground-truth bound)."""
    if isinstance(v, int) or (v == v and not math.isinf(v) and v == round(v)):
        return fmt_d(v)
    return dbl_chr(v)


def _likert_scale(xv: list[Any]) -> Any:
    """``.detect_likert_scale(xv)`` for finite *xv*, skipping the call when its
    own opening checks already return ``NULL``: fewer than 20 values, a
    non-integer value, or fewer than 2 / more than 23 distinct levels (counted
    only when every value is in R's integer range, as ``as.integer()`` turns
    the others into ``NA``, which ``sort(unique())`` then drops)."""
    if len(xv) < 20:
        return None
    u = set(xv)
    if any(e != round(e) for e in u):
        return None
    if all(abs(e) <= 2147483647 for e in u) and not 2 <= len(u) <= 23:
        return None
    from metacheck.datacheck.files import _detect_likert_scale

    return _detect_likert_scale(xv)


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
    import numpy as np

    got = numeric_array(x)
    if got is None:
        return none
    a = got[0][~np.isnan(got[0])]
    if a.size < 4:
        return none
    q1, q3 = quantile7(np.sort(a), (0.25, 0.75), is_sorted=True)
    iqr = q3 - q1
    # Both quartiles the same infinity (c(1, Inf, Inf, Inf)): Inf - Inf is NaN,
    # which is "no spread" just like iqr == 0 (R fails on it in `if`).
    if iqr != iqr or iqr == 0:
        return none
    lower = q1 - k * iqr
    upper = q3 + k * iqr
    out: list[Any] = list(dict.fromkeys(a[(a < lower) | (a > upper)].tolist()))  # unique()
    if got[1]:
        out = [int(e) for e in out]
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


def _table(v: Any) -> tuple[dict[Any, int], Callable[[Any], Any], Callable[[Any], str]]:
    """``table(x)`` of a vector without NA, unsorted.

    Returns the counts per level, a sort key giving each level's position in
    ``levels(factor(x))`` and a function giving a level's label (its
    ``as.character()``). Sorting is left to the caller, which only ever needs
    the first of a few tied levels. ``table()``'s default ``exclude = c(NA,
    NaN)`` is coerced to the type of a non-factor *x*, so for a character
    vector it drops the string ``"NaN"`` (a factor keeps a ``"NaN"`` level).
    Distinct doubles that print alike (``as.character()`` keeps 15 significant
    digits) share one level.
    """
    if v.kind == "factor":
        levels = list(v.levels or [])
        counts: dict[Any, int] = dict.fromkeys(levels, 0)
        counts.update(Counter(v.values))
        pos = {lab: i for i, lab in enumerate(levels)}
        return counts, pos.__getitem__, str
    raw = Counter(v.values)
    if v.kind == "character":
        raw.pop("NaN", None)
        return dict(raw), r_sort_key, str
    if v.kind == "double" and _doubles_may_collide(list(raw)):
        labels = chr(RVec("double", list(raw)))
        counts = {}
        first: dict[Any, Any] = {}
        for val, lab in zip(raw, labels, strict=True):
            counts[lab] = counts.get(lab, 0) + raw[val]
            first[lab] = val if lab not in first else min(first[lab], val)
        return counts, first.__getitem__, str
    # as.character() is one-to-one here: count raw values, label them on demand
    kind = v.kind
    return dict(raw), lambda val: val, lambda val: _label(kind, val)


def _label(kind: str, val: Any) -> str:
    """``as.character()`` of one value of an R vector of type *kind*."""
    return chr(RVec(kind, [val]))[0]  # type: ignore[return-value]


def _doubles_may_collide(values: list[float]) -> bool:
    """Could two of these distinct doubles print alike under ``as.character()``?

    Only values within a relative 1e-14 of each other can share 15 significant
    digits, so the (vectorised) check is exact in the "no" direction.
    """
    import numpy as np

    u = np.sort(np.asarray(values, dtype="float64"))
    u = u[np.isfinite(u)]
    if u.size < 2:
        return False
    d = np.diff(u)
    scale = np.maximum(np.abs(u[:-1]), np.abs(u[1:]))
    return bool(np.any(d <= 1e-14 * scale))


def data_check_constant(x: Any, threshold: float = 0.99) -> dict[str, Any]:
    """Flag a constant or near-constant column.

    Port of ``R/data_check_helpers.R::data_check_constant()``: the column is
    constant when it has one distinct non-missing value, near-constant
    (``near = True``) when the most common value covers at least *threshold*.
    """
    v = rvec(x).drop_na()
    if len(v) == 0:
        return {"problem": False, "message": "", "values": None, "near": False}
    counts, key, label_of = _table(v)
    if not counts:  # every value was the string "NaN": `tab[[1]]` on an empty table
        raise IndexError("subscript out of bounds")
    top_count = max(counts.values())
    top_frac = top_count / len(v)
    if len(counts) == 1:
        label = label_of(next(iter(counts)))
        return {
            "problem": True,
            "message": f'Column is constant: every value is "{label}".',
            "values": label,
            "near": False,
        }
    if top_frac >= threshold:
        # sort(table(x), decreasing = TRUE) is stable: the first tied level wins
        label = label_of(min((lv for lv, c in counts.items() if c == top_count), key=key))
        return {
            "problem": True,
            "message": f'Near-constant: {fmt_pct0(100 * top_frac)}% of values are "{label}".',
            "values": label,
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
        filled = any(s is not None and trim(s) != "" for s in chr_counts(v))
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
    xs = [s for s in chr_counts(v) if s is not None and trim(s) != ""]  # unique(), data order
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
    padded = [s for s in chr_counts(v) if s is not None and (t := trim(s)) != s and t != ""]
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
    counts = trimmed_counts(v)
    n_total = sum(counts.values())
    if n_total < 5:
        return none
    bad = [s for s in counts if (f := as_numeric_str(s.replace(",", "."))) is None or f != f]
    frac_num = (n_total - sum(counts[s] for s in bad)) / n_total
    if frac_num < threshold or frac_num >= 1:
        return none
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

    Blank (``""``) names collide like any other and get an entry (R's
    ``out[[""]] <- msg`` appends unnamed elements that no lookup by name can
    retrieve, so there a blank column's collision is never reported).
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
