"""Power Analysis Check (port of ``inst/modules/power.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r import grepl, gsub, plural
from pytacheck.module import module
from pytacheck.modules._power import LLM_COLS
from pytacheck.report import collapse_section, scroll_table
from pytacheck.text import text_search

# select paragraphs with power/powers/powered (will not match e.g., powerful)
_POWER_PATTERN = r"\bpower(ed|s)?\b"

# phrases that should also be in the paragraph
_POWER_WORDS = [
    "power analy",
    "effect size",
    "sized? effect",
    "sample[- ]size",
    "g[-* ]?power",
    "a[- ]?priori",
    "a[- ]?posteriori",
    "post[- ]?hoc",
    "sensitivity",
    "pwr",
    "statistical power",
    "to detect",
    "achieve",
    "(small|medium|large) effect",
    "observed power",
    "a power of",
    "%",
    r"power\s*=",
]

# only keep paragraphs with a number: digits, but not years
_NUMERIC_PATTERN = r"\b(?!\d{4}\b)\d+(?:[.,]\d+)?\b"

# regex classification of the power analysis type (dplyr::case_when order)
_POWER_TYPES = (
    ("a[- ]?priori", "apriori"),
    ("sensitivity", "sensitivity"),
    ("compromise power", "compromise"),
    ("a[- ]?posteriori|post[- ]?hoc|retrospective", "posthoc"),
)

# important terms highlighted in the report's text table
_HIGHLIGHT = (
    "("
    + "|".join(
        [
            "power",
            "a[- ]?priori",
            "sensitivity",
            "a[- ]?posteriori",
            "post[- ]?hoc",
            "observed power",
            "retrospective power",
        ]
    )
    + ")"
)

_GUIDANCE = [
    "Power analyses need to contain the following information to be interpretable: the type "
    "of power analysis, the statistical test, the software used, sample size, critical alpha "
    "criterion, power level, effect size, and an effect size metric. In addition, it is "
    "recommended to make sure the power analysis is reproducible (by sharing the code, or a "
    "screenshot, of the power analysis), and to provide good arguments for why the study was "
    "designed to detect an effect of this size.",
    "For an a-priori power analysis, where the sample size is determined, reporting all "
    "information would look like:",
    "> An a priori power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, an "
    "alpha level of 0.05, and a desired power level of 80% required at least 64 participants "
    "in each group.",
    "For a sensitivity power analysis, this sentence would look like:",
    "> A sensitivity power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that with 64 participants in "
    "each group, and an alpha level of 0.05, a desired power level of 80% was reached for an "
    "effect size of d = 0.5.",
]

_NO_LLM_TEXT = (
    "You chose to not use an LLM to assess if all information was reported, so please check "
    "for all required information manually."
)

_NOT_STRUCTURED_TEXT = (
    "(The provider does not support structured outputs for this schema; used prompt-based "
    "extraction instead.)"
)

_LLM_FAILED_TEXT = (
    "The LLM check for power analyses failed to run; no results could be extracted. This is "
    "not the same as finding no power analyses -- please check manually or re-run the check."
)

_OBSERVED_POWER_TEXT = (
    "You reported a power analysis that has been classified as 'post-hoc'. Calculating "
    "observed power is [almost never useful](https://lakens.github.io/statistical_inferences/"
    "08-samplesizejustification.html#sec-posthocpower). If you actually performed a "
    "sensitivity power analysis, label it as such explicitly."
)


def _tolower(x: Any) -> str | None:
    """R ``tolower()`` (simple case mapping: ``İ`` becomes ``i``, not ``i̇``)."""
    if x is None or x is pd.NA or (isinstance(x, float) and x != x):
        return None
    return str(x).replace("İ", "i").lower()


def _classify(texts: list[Any]) -> list[str]:
    """The regex ``case_when()`` classifying each paragraph's power analysis type."""
    lowered = [_tolower(t) for t in texts]
    out: list[str | None] = [None] * len(lowered)
    for pattern, label in _POWER_TYPES:
        # stringr::str_detect() (ICU); NA text never matches
        hits = grepl(pattern, lowered, perl=True)
        for i, hit in enumerate(hits):
            if out[i] is None and hit is True:
                out[i] = label
    return [label if label is not None else "unknown" for label in out]


def _isna_cells(s: pd.Series) -> list[bool]:
    from pytacheck.modules._power import _col_isna

    return _col_isna(s)


def _r_sum_logical(values: list[Any]) -> Any:
    """R ``sum()`` of a logical vector: an integer, ``NA`` when any value is ``NA``."""
    total = 0
    for v in values:
        if v is None or v is pd.NA or (isinstance(v, float) and v != v):
            return pd.NA
        total += int(bool(v))
    return total


def _summary_table(table: pd.DataFrame) -> pd.DataFrame:
    """``summarise(power_n = n(), power_complete = sum(complete), across(...), .by = paper_id)``."""
    count_cols = [c for c in LLM_COLS[1:] if c in table.columns]
    groups: dict[Any, list[int]] = {}
    for i, pid in enumerate(table["paper_id"].tolist()):
        key = None if pid is None or pid is pd.NA else pid
        groups.setdefault(key, []).append(i)
    complete = table["complete"].tolist()
    na_flags = {c: _isna_cells(table[c]) for c in count_cols}
    data: dict[str, pd.Series] = {
        "paper_id": pd.Series(list(groups), dtype="string"),
        "power_n": pd.Series([len(rows) for rows in groups.values()], dtype="Int64"),
        "power_complete": pd.Series(
            [_r_sum_logical([complete[i] for i in rows]) for rows in groups.values()],
            dtype="Int64",
        ),
    }
    for c in count_cols:
        flags = na_flags[c]
        data[f"power_{c}"] = pd.Series(
            [sum(not flags[i] for i in rows) for rows in groups.values()], dtype="Int64"
        )
    return pd.DataFrame(data)


@module(
    title="Power Analysis Check",
    description="""
        This module uses uses regular expressions to identify sentences that contain a statistical power analysis. If specified by the user, it also uses a large language module (LLM) to extract information reported in power analyses, including the statistical test, sample size, alpha level, desired level of power, and magnitude and type of effect size.
    """,
    details="""
        The Power Analysis Check module uses regular expressions to identify sentences that contain a statistical power analysis. Without the use of an LMM, the module uses regular expressions to classify the power analysis as a-priori, sensitivity or post-hoc. With the use of an LMM, it checks if the power analysis is reported with all required information.

        The regular expressions can miss power analyses, or fail to classify them correctly. The type of power analysis is often difficult to classify, which can easily be solved by explicitly specifying the type of power analysis as 'a-priori', 'sensitivity', or 'post-hoc'. Note that 'post-hoc' or 'observed' power is rarely useful. The LMM can fail to identify information in the paper, and will not have access to information in paragraphs in the paper other than those that contain the word 'power'. This package was validated by the Metacheck team on articles in Psychological Science.

        <validation>In a sample of 128 papers with 246 instances of power statements, 203 were correctly detected (true positives), 22 were missed (false negatives) and 21 were incorrectly detected (false positives). Overall, among all instances flagged as power statements, 90.6% were correct (positive predictive value).</validation>
    """,
    keywords=["method"],
    requires=["llm"],
    author=[
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
        "Daniel Lakens <d.lakens@tue.nl>",
        "Cristian Mesquida <c.mesquida.caldentey@tue.nl>",
    ],
    params={
        "paper": "a paper object or paperlist object",
        "seed": "a seed for the LLM",
    },
)
def power(paper: Any, seed: Any = 8675309) -> dict[str, Any]:
    """Port of ``inst/modules/power.R::power()``.

    Finds paragraphs that look like power analyses (``power`` plus a
    power-related phrase plus a non-year number). With :func:`llm_use` on, an
    LLM extracts the analysis details and the traffic light says whether all
    essential information was found (green/red); without it, a regex
    classifies each paragraph's power analysis type (yellow).
    """
    from pytacheck.llm import llm_use
    from pytacheck.papers.tables import paper_id

    # find potential power analyses ----
    potential_power = text_search(paper, _POWER_PATTERN, return_="paragraph")
    potential_power = text_search(potential_power, _POWER_WORDS, return_="paragraph")
    potential_power = text_search(potential_power, _NUMERIC_PATTERN, return_="paragraph", perl=True)

    llm_cols = list(LLM_COLS)
    llm_failed = False
    tl: str | None = None
    report_text: list[str] = []
    n_potential = len(potential_power)

    if n_potential > 0 and llm_use():
        # use LLM ----
        from pytacheck.modules._power import _power_llm_extract

        extraction = _power_llm_extract(potential_power, seed)
        llm_model_used = extraction["model"]
        table = extraction["table"]
        llm_failed = extraction.get("failed") is True

        if llm_model_used is not None:
            report_text = [
                f"We used the LLM model '{llm_model_used}' to check the contents of "
                f"{n_potential} paragraph{plural(n_potential)} that contained words suggesting "
                "they might contain power analyses."
            ]
        if not extraction["structured"]:
            report_text.append(_NOT_STRUCTURED_TEXT)

        # check for NAs in LLM columns
        na_cols = [c for c in llm_cols if c in table.columns]
        cols_with_na = [c for c in na_cols if any(_isna_cells(table[c]))]
        if len(table) == 0:
            pass  # handled below
        elif not cols_with_na:
            tl = "green"
            report_text.append("All essential information could be detected.")
        else:
            tl = "red"
            report_text.append(
                "Some essential information could not be detected: " + ", ".join(cols_with_na)
            )
    elif n_potential > 0:
        # use regex ----
        table = potential_power.copy()
        table["power_type"] = pd.Series(
            _classify(table["text"].tolist()), index=table.index, dtype="string"
        )
        table["complete"] = pd.Series([pd.NA] * len(table), index=table.index, dtype="boolean")
        tl = "yellow"
        report_text = [_NO_LLM_TEXT]
    else:
        table = potential_power

    # generate report ----
    report: Any
    if len(table) == 0:
        # no power detected ----
        tl = "na"
        summary_text = _LLM_FAILED_TEXT if llm_failed else "No power analyses were detected."
        report = [summary_text, collapse_section(_GUIDANCE)]
        ids = paper_id(paper)
        summary_table = pd.DataFrame(
            {
                "paper_id": pd.Series(ids, dtype="string"),
                "power_n": pd.Series([0.0] * len(ids), dtype="float64"),
                "power_complete": pd.Series([pd.NA] * len(ids), dtype="Int64"),
            }
        )
    else:
        # power detected ----
        power_types = table["power_type"].tolist()
        posthoc = any(v == "posthoc" for v in power_types if isinstance(v, str))
        observed_power_text = _OBSERVED_POWER_TEXT if posthoc else ""

        # report tables
        table = table.reset_index(drop=True)
        n = len(table)
        table["power_id"] = pd.Series(range(1, n + 1), dtype="Int64")

        info_table = table.loc[:, ["power_id", *[c for c in llm_cols if c in table.columns]]]
        # summarise(power_id = paste(power_id, collapse = ";"), .by = text)
        by_text: dict[Any, list[str]] = {}
        for text, pid in zip(table["text"].tolist(), table["power_id"].tolist(), strict=True):
            key = None if text is None or text is pd.NA else text
            by_text.setdefault(key, []).append(str(pid))
        text_table = pd.DataFrame(
            {
                "power_id": pd.Series([";".join(v) for v in by_text.values()], dtype="string"),
                "text": pd.Series(list(by_text), dtype="string"),
            }
        )
        if n == 1:
            # power_id not needed for a single power analysis
            info_table = info_table.drop(columns="power_id")
            text_table = text_table.drop(columns="power_id")

        # highlight important terms in text
        text_table["text"] = pd.Series(
            gsub(_HIGHLIGHT, r"<strong>\1</strong>", text_table["text"].tolist(), ignore_case=True),
            dtype="string",
        )

        summary_text = f"We detected {n} potential power {plural(n, 'analysis', 'analyses')}."
        summary_table = _summary_table(table)

        report = [
            *report_text,
            scroll_table(info_table, maxrows=5),
            observed_power_text,
            scroll_table(text_table, maxrows=5),
            collapse_section(_GUIDANCE),
        ]

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"power_n": 0},
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
