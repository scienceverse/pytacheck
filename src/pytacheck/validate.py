"""Validating modules against hand-coded ground truth (port of ``R/validate.R``)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

__all__ = ["AccuracyMeasures", "accuracy", "validate"]


class AccuracyMeasures(dict[str, Any]):
    """Signal-detection measures (R class ``metacheck_accuracy_measures``).

    A dict (``hits``, ``misses``, ``false_alarms``, ``correct_rejections``,
    ``accuracy``, ``sensitivity``, ``specificity``, ``d_prime``, ``beta``)
    whose values can also be read as attributes (``a.d_prime``).
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _logical(x: Any) -> pd.Series:
    if isinstance(x, pd.Series):
        s = x.reset_index(drop=True)
    elif isinstance(x, bool) or x is None:
        s = pd.Series([x])
    else:
        s = pd.Series(list(x), dtype=object)
    return s.astype("boolean")


def _sum(x: pd.Series) -> int | None:
    """R ``sum()`` of a logical vector: ``NA`` if any element is ``NA``."""
    if x.isna().any():
        return None
    return int(x.sum())


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    if b == 0:
        return math.nan if a == 0 else math.copysign(math.inf, a)
    return a / b


def _adjust(rate: float | None, n: int | None) -> float | None:
    """The two ``ifelse()`` corrections for rates of exactly 1 or 0."""
    if rate is None or math.isnan(rate) or n is None:
        return None  # ifelse(NA/NaN test, ...) gives NA
    if rate == 1:
        rate = 1 - 0.5 / n
    if rate == 0:
        rate = 0.5 / n
    return rate


def _qnorm(p: float | None) -> float | None:
    if p is None:
        return None
    from scipy.stats import norm

    return float(norm.ppf(p))


def accuracy(expected: Any, observed: Any) -> AccuracyMeasures:
    """Port of ``accuracy()``: signal-detection values for a classifying module.

    ``expected`` and ``observed`` are logical vectors (ground truth and the
    module's classification). Rates of exactly 0 or 1 are adjusted by half
    an observation so ``d_prime`` and ``beta`` stay finite.
    """
    e, o = _logical(expected), _logical(observed)
    n = max(len(e), len(o))
    if len(e) != len(o) and n:
        # R recycles the shorter vector
        e = pd.Series([e.iloc[i % len(e)] for i in range(n)], dtype="boolean") if len(e) else e
        o = pd.Series([o.iloc[i % len(o)] for i in range(n)], dtype="boolean") if len(o) else o
    hit = _sum(e & o)
    miss = _sum(e & ~o)
    fa = _sum(~e & o)
    cr = _sum(~e & ~o)

    def add(*xs: int | None) -> int | None:
        return None if any(x is None for x in xs) else sum(xs)  # type: ignore[arg-type]

    hit_rate = _adjust(_div(hit, add(hit, miss)), add(hit, miss))
    fa_rate = _adjust(_div(fa, add(fa, cr)), add(fa, cr))

    z_hit, z_fa = _qnorm(hit_rate), _qnorm(fa_rate)
    d_prime = None if z_hit is None or z_fa is None else z_hit - z_fa
    beta = None if z_hit is None or z_fa is None else math.exp((z_fa**2 - z_hit**2) / 2)

    return AccuracyMeasures(
        hits=hit,
        misses=miss,
        false_alarms=fa,
        correct_rejections=cr,
        accuracy=_div(add(hit, cr), add(hit, cr, fa, miss)),
        sensitivity=hit_rate,
        specificity=fa_rate,
        d_prime=d_prime,
        beta=beta,
    )


def _nullable(df: pd.DataFrame) -> pd.DataFrame:
    """Nullable dtypes, so rows added by a join hold NA without changing type."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_bool_dtype(s.dtype):
            out[col] = s.astype("boolean")
        elif pd.api.types.is_integer_dtype(s.dtype):
            out[col] = s.astype("Int64")
    return out


def _full_join(
    x: pd.DataFrame, y: pd.DataFrame, by: Sequence[str], suffix: tuple[str, str]
) -> pd.DataFrame:
    """``dplyr::full_join()``: x's rows (with matches), then y's unmatched rows."""
    missing = [k for k in by if k not in y.columns]
    if missing:
        raise ValueError(
            "Join columns in `y` must be present in the data.\n"
            f"Problem with {', '.join(f'`{k}`' for k in missing)}."
        )
    missing = [k for k in by if k not in x.columns]
    if missing:
        raise ValueError(
            "Join columns in `x` must be present in the data.\n"
            f"Problem with {', '.join(f'`{k}`' for k in missing)}."
        )
    common = [c for c in x.columns if c in y.columns and c not in by]
    x = _nullable(x).rename(columns={c: f"{c}{suffix[0]}" for c in common})
    y = _nullable(y).rename(columns={c: f"{c}{suffix[1]}" for c in common})
    y_cols = [c for c in y.columns if c not in by]
    left = x.reset_index(drop=True).assign(__x_row=range(len(x)))
    right = y.reset_index(drop=True).assign(__y_row=range(len(y)))
    # join on string forms of the keys, as R compares character keys
    keys = [f"__key{i}" for i in range(len(by))]
    for k, col in zip(keys, by, strict=True):
        left[k] = [None if _missing(v) else str(v) for v in left[col].tolist()]
        right[k] = [None if _missing(v) else str(v) for v in right[col].tolist()]
    merged = left.merge(right.drop(columns=list(by)), on=keys, how="left", sort=False)
    matched = set(int(v) for v in merged["__y_row"].dropna().tolist())
    extra = right[~right["__y_row"].isin(matched)]
    columns = [*x.columns, *y_cols]
    out = pd.concat([merged[columns], extra.reindex(columns=columns)], ignore_index=True)
    for col in columns:
        src = x[col] if col in x.columns else y[col]
        if col in by and col in y.columns and len(extra):
            src = y[col]
        try:
            out[col] = out[col].astype(src.dtype)
        except (TypeError, ValueError):
            pass
    return out


def _missing(v: Any) -> bool:
    if v is None or v is pd.NA:
        return True
    return isinstance(v, float) and math.isnan(v)


def _r_equal(a: pd.Series, b: pd.Series) -> pd.Series:
    """R ``a == b``: compares as strings when either side is character; NA propagates."""
    from pytacheck._r.base import as_character

    def is_chr(s: pd.Series) -> bool:
        if pd.api.types.is_string_dtype(s.dtype) and not pd.api.types.is_object_dtype(s.dtype):
            return True
        return s.dtype == object and any(isinstance(v, str) for v in s.tolist())

    def num(v: Any) -> float:
        return float(v)

    chr_mode = is_chr(a) or is_chr(b)
    out: list[bool | None] = []
    for u, v in zip(a.tolist(), b.tolist(), strict=True):
        if _missing(u) or _missing(v):
            out.append(None)
        elif chr_mode:
            su = u if isinstance(u, str) else ("TRUE" if u is True else "FALSE" if u is False else as_character(u))
            sv = v if isinstance(v, str) else ("TRUE" if v is True else "FALSE" if v is False else as_character(v))
            out.append(su == sv)
        else:
            try:
                out.append(num(u) == num(v))
            except (TypeError, ValueError):
                out.append(u == v)
    return pd.Series(out, dtype="boolean")


def validate(gt: Any, module: Any, compare: str = "table") -> pd.DataFrame:
    """Port of ``validate()``: check a module's output against coded ground truth.

    Each text in ``gt`` becomes its own test paper; the module's output
    ``compare`` table is full-joined to ``gt`` by ``paper_id`` and ``text``
    (colliding columns get ``.gt`` / ``.mod`` suffixes) and a
    ``<column>.valid`` column marks where the two agree. Summarise the
    result with :func:`accuracy`.
    """
    from pytacheck.module import module_run
    from pytacheck.papers.io import test_paper
    from pytacheck.papers.model import PaperList

    if not isinstance(gt, pd.DataFrame):
        texts = [gt] if isinstance(gt, str) else list(gt)
        gt = pd.DataFrame(
            {
                "paper_id": pd.Series([str(i + 1) for i in range(len(texts))], dtype="string"),
                "text": pd.Series(texts, dtype="string")
                if all(isinstance(t, str) or t is None for t in texts)
                else pd.Series(texts),
            }
        )
    papers = []
    ids = gt["paper_id"].tolist()
    texts_col = gt["text"].tolist()
    for pid in ids:
        t = [txt for i, txt in zip(ids, texts_col, strict=True) if i == pid]
        p = test_paper([str(v) for v in t])
        p.paper_id = pid
        papers.append(p)
    mo = module_run(PaperList(papers), module)
    table = mo.get(compare)
    if not isinstance(table, pd.DataFrame):
        raise ValueError(f"`y` must be a data frame, not the module's `{compare}`.")
    comp = _full_join(gt, table, by=["paper_id", "text"], suffix=(".gt", ".mod"))
    comp_cols = [c[: -len(".mod")] for c in comp.columns if str(c).endswith(".mod")]
    for col in comp_cols:
        comp[f"{col}.valid"] = _r_equal(comp[f"{col}.gt"], comp[f"{col}.mod"])
    return comp
