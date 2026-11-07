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

# a starred footnote ("*p < .05"): only the p-value that is itself starred
_STAR_P = r"^[pP]\s*<\s*0?\.0*[15]$"


def _starred(p: pd.DataFrame) -> np.ndarray:
    """Which p-values are a table note's "* p < .05" (a star right before them).

    Only ".05", ".01" and ".001" count, and only the p-value that is itself
    starred. The same p-value can appear unstarred and starred in one sentence,
    so as many of its rows are left out as there are starred copies, the last
    ones (a footnote comes after the results).
    """
    import re

    from metacheck._r.regex import compile_r

    out = np.zeros(len(p), dtype=bool)
    texts = p["text"].tolist()
    expanded = p["expanded"].tolist()
    values = p["p_value"].astype("float64").tolist()
    groups: dict[tuple[str, str], list[int]] = {}
    for i, (txt, exp, val) in enumerate(zip(texts, expanded, values, strict=True)):
        if not isinstance(txt, str) or not isinstance(exp, str):
            continue
        if re.match(_STAR_P, txt) and val in (0.05, 0.01, 0.001):
            groups.setdefault((exp, txt), []).append(i)
    # rows of one (sentence, text) pair, whether or not each can be starred
    for exp, txt in groups:
        rows = [
            i
            for i, (t, e) in enumerate(zip(texts, expanded, strict=True))
            if isinstance(t, str) and e == exp and t == txt
        ]
        esc = re.sub(r"([][{}()+*^$|\\.?])", r"\\\1", txt)
        n_star = len(compile_r(r"\*\s*" + esc).findall(exp))
        if n_star > 0:
            for i in rows[-n_star:]:
                out[i] = True
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

    # the match keeps trailing spaces ("ps < .025 (Table 1)" matches "ps < .025 "), so one
    # p-value twice in a sentence gave two report rows (R: trimws(p$text), U127;
    # metacheck 0.4.3.7 trims it too)
    p = p.copy()
    p["text"] = p["text"].str.strip(" \t\r\n")

    # "==" is "=": it counted as imprecise (U127)
    p["p_comp"] = p["p_comp"].where(~p["p_comp"].eq("==").fillna(False), "=")

    comp = p["p_comp"]
    value = p["p_value"].astype("float64")
    value_na = value.isna().to_numpy(dtype=bool)

    # Flag imprecise p-values. R's three-valued logic never leaves an NA here:
    # an NA p_value is caught by is.na(), an NA p_comp by !%in%.
    is_lt = comp.eq("<").fillna(False).to_numpy(dtype=bool)
    imprecise = is_lt & (value.to_numpy() > 0.001)
    imprecise = imprecise | ~comp.isin(["=", "<"]).to_numpy(dtype=bool)
    imprecise = imprecise | value_na

    # remove false positive "*p < .05": only the p-value that is itself
    # starred, and only .05, .01 or .001 (U127)
    imprecise = imprecise & ~_starred(p)

    # Flag p-values reported as exactly zero (e.g., p = .000, p = 0.00), also
    # "p < .000", which was neither imprecise nor zero (U127)
    is_zero = (
        comp.isin(["=", "<"]).to_numpy(dtype=bool) & ~value_na & (value.fillna(1).to_numpy() == 0)
    )
    zero = pd.array(is_zero, dtype="boolean")

    p = p.copy()
    p["imprecise"] = pd.Series(imprecise, index=p.index, dtype="boolean")
    p["zero"] = pd.Series(zero, index=p.index, dtype="boolean")

    imp_rows = imprecise
    zero_rows = np.asarray(p["zero"].fillna(False), dtype=bool)

    # one report row per p-value text and sentence
    cols = ["text", "expanded"]

    def listed(rows: np.ndarray) -> pd.DataFrame:
        sub = p.loc[rows, cols]
        dup = sub.duplicated().to_numpy(dtype=bool)
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
