"""Exact P-Values (port of ``inst/modules/stat_p_exact.R``)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.base import plural
from metacheck._r.frames import count
from metacheck.module import module
from metacheck.report import collapse_section, format_ref, scroll_table

# R: format_ref(apa), rendered by R's bibentry html style.
_APA_REF = (
    "American Psychological Association (2020). <em>Publication manual of the American "
    "Psychological Association</em>, 7 edition. American Psychological Association."
)

# remove false positive "*p < .05"
_STAR_PATTERN = r"\*\s*[pP]\s*<\s*0?\.0+[15]"
_STAR_P = r"^[pP]\s*<\s*0?\.0+[15]"
# comparators of an exact value ("==" is a typo of "=", not imprecise, U127)
_EXACT = ("=", "==")
# comparators that report a value of zero ("p < .000" is zero too, U127)
_ZERO_COMPS = ("=", "==", "<", "<=", "=<", "≤")


def _zero_literal(text: Any, comp: Any) -> bool:
    """Whether the number after the comparator is written as zero (".000", "0.0 x 10^-3").

    A value that only underflows to 0 ("p < 1.0 x 10^-400") is not reported as zero.
    """
    if not isinstance(text, str) or not isinstance(comp, str) or comp not in text:
        return False
    rest = text.split(comp, 1)[1].lstrip()
    mantissa = ""
    for ch in rest:
        if not (ch.isdigit() or ch == "."):
            break
        mantissa += ch
    return any(ch.isdigit() for ch in mantissa) and not any(ch in "123456789" for ch in mantissa)


def _starred(p: pd.DataFrame) -> np.ndarray:
    """Which p-values are a table note's "* p < .05" (a star right before them).

    metacheck tests the star pattern on the whole sentence, so one "* p < .05"
    note hid every imprecise p-value of that sentence (U127). Each match is
    found in its sentence in order (matches of a sentence do not overlap).
    """
    from metacheck._r.regex import grepl

    out = np.zeros(len(p), dtype=bool)
    cursor: dict[tuple[Any, ...], int] = {}
    cols = [p[c].tolist() for c in ("paper_id", "text_id", "text", "expanded")]
    for i, (pid, tid, txt, exp) in enumerate(zip(*cols, strict=True)):
        if not isinstance(txt, str) or not isinstance(exp, str):
            continue
        key = (pid, tid, exp)
        start = exp.find(txt, cursor.get(key, 0))
        if start < 0:  # not found as is: metacheck's test of the whole sentence
            out[i] = bool(grepl(_STAR_PATTERN, exp))
            continue
        cursor[key] = start + len(txt)
        out[i] = bool(grepl(r"\*\s*$", exp[:start])) and bool(grepl(_STAR_P, txt))
    return out


_REPORT_TEXT = (
    "Reporting *p* values imprecisely (e.g., *p* < .05) reduces transparency, reproducibility, "
    "and re-use (e.g., in *p* value meta-analyses). Best practice is to report exact p-values "
    "with three decimal places (e.g., *p* = .032) unless *p* values are smaller than 0.001, in "
    "which case you can use *p* < .001."
)
_GUIDANCE = (
    "The APA manual states: Report exact *p* values (e.g., *p* = .031) to two or three decimal "
    "places. However, report *p* values less than .001 as *p* < .001. However, 2 decimals is too "
    "imprecise for many use-cases (e.g., a *p* value meta-analysis), so report *p* values with "
    "three digits."
)
_ZERO_TEXT = (
    "*P* values are never exactly zero. A *p* value of .000 is a rounding artifact — the actual "
    "value is simply smaller than the reported precision. Very small *p* values should be "
    "reported as *p* < .001 rather than *p* = .000."
)
_ZERO_GUIDANCE = "The APA manual states: report *p* values less than .001 as *p* < .001."


def _full_join(x: pd.DataFrame, y: pd.DataFrame, by: str) -> pd.DataFrame:
    """``dplyr::full_join(x, y, by)`` for key-unique *x* and *y*.

    x's rows in order (with their match in *y*), then y's unmatched rows.
    """
    out = x.merge(y, on=by, how="left", sort=False)
    extra = y.loc[~y[by].isin(x[by]).to_numpy(dtype=bool)]
    if len(extra):
        out = pd.concat([out, extra], ignore_index=True, sort=False)
    out = out.loc[:, [*x.columns, *(c for c in y.columns if c != by)]]
    for c in out.columns:
        if c != by:
            out[c] = out[c].astype("Int64")
    return out.reset_index(drop=True)


@module(
    title="Exact P-Values",
    description=(
        "List any p-values reported with insufficient precision (e.g., p < .05 or p = n.s.) or "
        "reported as exactly zero (e.g., p = .000)."
    ),
    details="""
        This module uses regular expressions to identify p-values. It will flag any values reported as p > ? or p < numbers greater than .001. It will also flag p-values reported as exactly zero (e.g., p = .000, p = 0.00), which are mathematically impossible — p-values are never exactly zero and should instead be reported as p < .001.

        We try to exclude figure and table notes like "* p < .05", but may not succeed at excluding all false positives.

        This module only checks p-values reported in the running text of the manuscript. It cannot (yet) process p-values reported only in tables (as opposed to a table's footnote text, which the module does see and tries to exclude — see above).

        <validation>In a sample of 225 papers containing 405 instances of non-exact p-values, the module correctly detected 269 cases (true positives) and incorrectly identified 78 (false positives). It missed 136 instances of imprecisely reported p-values (false negatives) and correctly identified 4557 cases of precisely reported p-values (true negative). Additionally, 78% of positive detections were correct (positive predictive value).</validation>
    """,
    keywords=["results"],
    # one R @author line naming both authors (metacheck's module_report() kept only the first; U6)
    author=[
        "Lisa DeBruine (\\email{lisa.debruine@glasgow.ac.uk}) and "
        "Daniel Lakens (\\email{D.Lakens@tue.nl})"
    ],
    params={"paper": "a paper object or paperlist object"},
)
def stat_p_exact(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/stat_p_exact.R::stat_p_exact()``."""
    from metacheck.text.expand import expand_text
    from metacheck.text.extract import extract_p_values

    # table ----
    p = extract_p_values(paper)

    # Expand the sentences so the full sentence can be seen
    p = expand_text(p, paper, expand_to="sentence")

    comp = p["p_comp"]
    value = p["p_value"].astype("float64")
    value_na = value.isna().to_numpy(dtype=bool)

    # Flag imprecise p-values. R's three-valued logic never leaves an NA here:
    # an NA p_value is caught by is.na(), an NA p_comp by !%in%.
    is_lt = comp.eq("<").fillna(False).to_numpy(dtype=bool)
    is_zero = ~value_na & (value.to_numpy() == 0)
    imprecise = is_lt & (value.to_numpy() > 0.001)
    imprecise = imprecise | ~comp.isin([*_EXACT, "<"]).to_numpy(dtype=bool)
    imprecise = imprecise | value_na

    # remove false positive "*p < .05" (table notes)
    imprecise = imprecise & ~_starred(p)

    # Flag p-values reported as zero (e.g., p = .000, p = 0.00, p < .000), which
    # are not also imprecise. metacheck checks only "=" and the numeric value,
    # so "p == .000" and "p < .000" were missed and "p = 1.0e-400" (which
    # underflows) was flagged (U127).
    in_zero = comp.isin(_ZERO_COMPS).fillna(False).to_numpy(dtype=bool)
    written_zero = np.array(
        [_zero_literal(t, c) for t, c in zip(p["text"].tolist(), comp.tolist(), strict=True)],
        dtype=bool,
    )
    is_zero = in_zero & is_zero & written_zero
    zero = pd.array(is_zero, dtype="boolean")
    imprecise = imprecise & ~is_zero

    p = p.copy()
    p["imprecise"] = pd.Series(imprecise, index=p.index, dtype="boolean")
    p["zero"] = pd.Series(zero, index=p.index, dtype="boolean")

    imp_rows = imprecise
    zero_rows = np.asarray(p["zero"].fillna(False), dtype=bool)

    # one report row per p-value text and sentence: the match text can keep
    # trailing whitespace, so metacheck's unique() listed the same p-value twice (U127)
    cols = ["text", "expanded"]

    def listed(rows: np.ndarray) -> pd.DataFrame:
        sub = p.loc[rows, cols]
        dup = sub.assign(text=sub["text"].str.strip()).duplicated().to_numpy(dtype=bool)
        out = sub.loc[~dup].reset_index(drop=True)
        out.columns = ["P-Value", "Text"]
        return out

    report_table = listed(imp_rows)
    zero_table = listed(zero_rows)

    # summary_table ----
    imprecise_summary = count(p.loc[imp_rows], "paper_id", name="n_imprecise")
    zero_summary = count(p.loc[zero_rows], "paper_id", name="n_zero")
    summary_table = _full_join(imprecise_summary, zero_summary, "paper_id")

    # traffic light ----
    n_p, n_imp, n_zero = len(p), len(report_table), len(zero_table)
    if n_p == 0:
        tl = "na"
    elif n_imp == 0 and n_zero == 0:
        tl = "green"
    else:
        tl = "red"

    # report / summary_text ----
    report: Any
    if tl == "na":
        report = "We detected no *p* values."
        summary_text = report
    elif tl == "green":
        report = (
            "We found no imprecise *p* values or *p*-values of exactly zero out of "
            f"{n_p:d} detected."
        )
        summary_text = report
    else:
        summary_parts = []
        if n_imp > 0:
            summary_parts.append(f"{n_imp:d} imprecise *p* value{plural(n_imp)}")
        if n_zero > 0:
            summary_parts.append(f"{n_zero:d} *p* value{plural(n_zero)} reported as exactly zero")
        summary_text = (
            f"We found {' and '.join(summary_parts)} out of {n_p:d} detected "
            f"*p* value{plural(n_p)}."
        )

        # Guidance text
        report = []
        if n_imp > 0:
            guidance = [_GUIDANCE, format_ref(_APA_REF)]
            report.extend(
                [
                    _REPORT_TEXT,
                    scroll_table(report_table, colwidths=[0.1, 0.9]),
                    collapse_section(guidance),
                ]
            )
        if n_zero > 0:
            zero_guidance = [_ZERO_GUIDANCE, format_ref(_APA_REF)]
            report.extend(
                [
                    _ZERO_TEXT,
                    scroll_table(zero_table, colwidths=[0.1, 0.9]),
                    collapse_section(zero_guidance),
                ]
            )

    # ---- Return list ----
    return {
        "table": p,
        "summary_table": summary_table,
        "traffic_light": tl,
        "na_replace": 0,
        "summary_text": summary_text,
        "report": report,
    }
