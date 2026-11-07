"""LLM extraction for the power module (port of ``inst/modules/power.R``'s helpers).

``.power_type_spec()`` and ``.power_llm_extract()`` live at the bottom of
metacheck's ``inst/modules/power.R``; they are ported here so that
:mod:`metacheck.modules.power` only holds the module itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from metacheck._values import is_missing, is_true

#: the columns the LLM is asked to fill (``llm_cols``)
LLM_COLS = (
    "power_type",
    "statistical_test",
    "sample_size",
    "alpha_level",
    "power",
    "effect_size",
    "effect_size_metric",
    "software",
)

_STRUCTURED_PROMPT = (
    "Identify power analyses from excerpts of scientific manuscripts. Use null/omit a field "
    "when information is missing, do not invent values. For the statistical test, effect size "
    "metric and software, use one of the suggested values when it fits, and otherwise give the "
    "name as written in the text. A paragraph may contain no power analysis, or more than one "
    "-- return one entry in power_analyses per power analysis actually described, and an empty "
    "array if the paragraph only references a power analysis presented elsewhere, or "
    "explicitly states that no power analysis was run."
)

_PREFACE = (
    "Identify and classify power analyses from exerpts of scientific manuscripts. Use null when "
    "information is missing, do not invent values. Only use 'other' if a value not in the "
    "enumerated options can be identified. There may be no power analysis in the text, or more "
    "than one. If the paragraph only references a power analysis implied to be presented "
    "elsewhere in the paper, or explicitly states that no power analysis was run, classify "
    "power_type as 'none'. Return an array of objects, as defined by the JSON schema below, in "
    "the same order as in the paragraphs, bracketed by ```json and ```."
)

#: the schema metacheck's prompt-based fallback downloads (``readLines()`` in R)
SCHEMA_URL = "https://scienceverse.org/schema/power.json"

#: R: the (unused) ``schema`` string at the bottom of ``power.R``, a copy of
#: the JSON schema published at :data:`SCHEMA_URL`. The fallback uses it
#: rather than downloading the schema, so it also works offline (U108).
SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://scienceverse.org/schema/power.json",
  "title": "Power Analyses",
  "description": "A power analysis.",
  "type": "object",
  "properties": {
    "text": {
      "description": "The specific text that contains all of the information used to determine this object's properties.",
      "type": ["string", "null"]
    },

    "power_type": {
      "description": "The type of power analysis. An 'apriori' power analysis is used to calculate the required sample size to achieve a desired level of statistical power given an effect size, statistical test, and alpha level. A 'sensitivity' analysis is used to estimate, given a sample size, which effect sizes a design has sufficient power (e.g., 80% or 90%) to detect, given a statistical test and alpha level. A 'posthoc' power analysis (also referred to as observed power, or retrospective power) uses an empirically observed effect size, and computes the achieved power for that empirically observed effect size, given a statistical test and alpha level.",
      "type": ["string", "null"],
      "enum": ["apriori", "sensitivity", "posthoc", "unknown", "none", null]
    },

    "statistical_test": {
      "description": "The statistical test used. Use null if unclear.",
      "type": ["string", "null"],
      "enum": [
        "paired t-test",
        "unpaired t-test",
        "one-sample t-test",
        "1-way ANOVA",
        "2-way ANOVA",
        "3-way ANOVA",
        "MANOVA",
        "regression",
        "chi-square",
        "correlation",
        "other",
        null
      ]
    },

    "statistical_test_other": {
      "description": "Free-text description if statistical_test is 'other', otherwise null.",
      "type": ["string", "null"]
    },

    "sample_size": {
      "description": "The sample size determined by or used in the power analysis. Give the total number if this is expressed as number per group.",
      "type": ["number", "null"],
      "minimum": 0
    },

    "alpha_level": {
      "description": "The alpha threshold used to determine significance.",
      "type": ["number", "null"],
      "exclusiveMinimum": 0,
      "maximum": 1
    },

    "power": {
      "description" : "The statistical power, expressed as a number between 0 and 1.",
      "type": ["number", "null"],
      "minimum": 0,
      "maximum": 1
    },

    "effect_size": {
      "description": "The numeric effect size used in or determined from the power analysis.",
      "type": ["number", "null"]
    },

    "effect_size_metric": {
      "description": "The effect size metric. Use 'unstandardised' for raw/non-standardized effects.",
      "type": ["string", "null"],
      "enum": [
        "Cohen's d",
        "Hedges' g",
        "Cohen's f",
        "partial eta squared",
        "eta squared",
        "unstandardised",
        "other",
        null
      ]
    },

    "effect_size_metric_other": {
      "description": "Free-text description if effect_size_metric is 'other', otherwise null.",
      "type": ["string", "null"]
    },

    "software": {
      "description": "The software used to conduct the power analysis.",
      "type": ["string", "null"],
      "enum": [
        "G*Power",
        "Superpower",
        "Pangea",
        "Morepower",
        "PASS",
        "pwr",
        "simr",
        "PowerUpR",
        "simulation",
        "InteractionPoweR",
        "pwrss",
        "other",
        null
      ]
    }
  },

  "required": [
    "power_type",
    "statistical_test",
    "statistical_test_other",
    "sample_size",
    "alpha_level",
    "power",
    "effect_size",
    "effect_size_metric",
    "effect_size_metric_other",
    "software"
  ],

  "additionalProperties": false
}"""


def _power_type_spec() -> Any:
    """Port of ``.power_type_spec()`` (power.R): the ellmer type for structured extraction.

    A single-field object (``power_analyses``: an array of power-analysis objects). The four
    category fields are free strings, not enums (#475): a provider validates the answer against
    the schema, so a value outside an enum failed the whole call. The descriptions list the
    values to prefer and :func:`_power_canonicalise` maps each answer onto them afterwards.
    """
    from metacheck.llm.types import type_array, type_number, type_object, type_string

    return type_object(
        power_analyses=type_array(
            type_object(
                power_type=type_string(
                    "The type of power analysis, one of 'apriori', 'sensitivity', 'posthoc' or "
                    "'unknown'. 'apriori' calculates the required sample size to achieve a "
                    "desired power given an effect size, statistical test, and alpha level. "
                    "'sensitivity' estimates, given a sample size, which effect sizes a design "
                    "has sufficient power to detect. 'posthoc' (observed/retrospective power) "
                    "computes achieved power for an empirically observed effect size. Use "
                    "'unknown' if a power analysis is present but its type cannot be determined."
                ),
                statistical_test=type_string(
                    "The statistical test used: one of 'paired t-test', 'unpaired t-test', "
                    "'one-sample t-test', '1-way ANOVA', '2-way ANOVA', '3-way ANOVA', "
                    "'MANOVA', 'regression', 'chi-square', 'correlation', or else the test's "
                    "name as written in the text.",
                    required=False,
                ),
                sample_size=type_number(
                    "The sample size determined by or used in the power analysis. Give the "
                    "total number if this is expressed as number per group.",
                    required=False,
                ),
                alpha_level=type_number(
                    "The alpha threshold used to determine significance.",
                    required=False,
                ),
                power=type_number(
                    "The statistical power, expressed as a number between 0 and 1.",
                    required=False,
                ),
                effect_size=type_number(
                    "The numeric effect size used in or determined from the power analysis.",
                    required=False,
                ),
                effect_size_metric=type_string(
                    "The effect size metric: one of 'Cohen's d', 'Hedges' g', 'Cohen's f', "
                    "'partial eta squared', 'eta squared', 'unstandardised' (for "
                    "raw/non-standardized effects), or else the metric as written in the text.",
                    required=False,
                ),
                software=type_string(
                    "The software used to conduct the power analysis, as named in the text "
                    "(e.g. 'G*Power', 'pwr', 'simr', 'Superpower', 'PASS', 'POWSIM'), or "
                    "'simulation' for a simulation without named software.",
                    required=False,
                ),
            ),
            description="Power analyses found in the text. Empty array if none.",
        )
    )


# ---------------------------------------------------------------------------
# known values: the LLM answers the four category fields in free text
# ---------------------------------------------------------------------------
# These rules map each answer onto the values the module reports. Each rule is a perl regular
# expression matched against the lower-cased answer; the FIRST rule that matches decides, so a
# more specific rule comes before a more general one (unpaired before paired, MANOVA before
# ANOVA, partial eta squared before eta squared). An answer no rule matches becomes "other"
# (power_type: "unknown"), with the answer kept in the column's ``*_other`` companion; "none",
# "null" and "n/a" count as missing.

_POWER_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("apriori", r"a[- ]?priori|prospective"),
    ("sensitivity", r"sensitivity|minim(al|um) detectable|smallest detectable"),
    ("posthoc", r"post[- ]?hoc|observed|retrospective|a[- ]?posteriori|achieved"),
)

_T = r"(?=.*\bt(?:[- ]?tests?)?\b)"  # the answer names a t test
_ANOVA = r"(?=.*(anova|analysis of variance))"
_POWER_TEST_RULES: tuple[tuple[str, str], ...] = (
    (
        "unpaired t-test",
        _T + r"(?=.*(unpaired|independent|two[- ]?samples?|between[- ]?(subjects?|groups?)|welch))",
    ),
    ("one-sample t-test", _T + r"(?=.*(one|1|single)[- ]?sample)"),
    (
        "paired t-test",
        _T + r"(?=.*(paired|dependent|within[- ]?subjects?|repeated[- ]?measures?|matched))",
    ),
    ("MANOVA", r"\bmanova\b|multivariate analysis of variance"),
    ("3-way ANOVA", _ANOVA + r"(?=.*((3|three)[- ]?way|\d+\s*[x×]\s*\d+\s*[x×]\s*\d+))"),
    ("2-way ANOVA", _ANOVA + r"(?=.*((2|two)[- ]?way|\d+\s*[x×]\s*\d+))"),
    ("1-way ANOVA", _ANOVA + r"(?=.*(1|one)[- ]?(way|factor))"),
    ("regression", "regression"),
    ("chi-square", r"chi[- ]?sq|χ"),
    ("correlation", r"correlat|pearson|spearman|kendall"),
)

_POWER_ES_RULES: tuple[tuple[str, str], ...] = (
    ("partial eta squared", r"partial\s*(eta|η)|η\s*_?\s*p\b|\beta[ _-]?p(\b|[ _-]?sq)"),
    ("eta squared", r"\beta[ _-]?(sq|\^?2)|η"),
    ("unstandardised", r"unstandardi[sz]ed|\braw\b"),
    ("Hedges' g", r"hedges|^g$"),
    # The apostrophe may be the typographic one a PDF gives. d_z, d_av and d_rm are forms of
    # Cohen's d. A "standardized mean difference" is not mapped: Hedges' g is one too, so it
    # stays "other".
    ("Cohen's d", r"cohen['’]?s?\s*d([ _]?(z|av|rm))?(?![a-z])|^d([ _]?(z|av|rm))?$"),
    # Cohen's f squared is a different metric, so it stays "other"
    ("Cohen's f", r"cohen['’]?s?\s*f(?![a-z])(?!\s*(\^?2|²|sq))|^f$"),
    # a plain mean difference is raw, a standardized one is not
    ("unstandardised", r"(?<!standardized |standardised )mean difference"),
)

# Power analysis software. The R packages are those on CRAN whose title is about power analysis
# or sample size (tools::CRAN_package_db(), 2026-10-17: 121 packages, less five whose name is a
# common abbreviation or that are not about planning studies: CP, RCT, abima, CAISEr,
# TrendInTrend), plus widely-used packages whose title does not say so (MBESS, faux, simglm,
# paramtest, BUCSS, pmsampsize, simsem, SampleSizeProportions, pwrFDR, ssanv) and ones no longer
# on CRAN or from Bioconductor that papers still cite (PowerUpR, powerlmm, mixedpower, powsimR,
# PROPER, RNASeqPower). Matched by whole name, longest name first, so "pwrss" is not read as
# "pwr" and "pass.lme" not as PASS.
_POWER_SOFTWARE_PACKAGES: tuple[str, ...] = (
    "anomo",
    "anovapowersim",
    "bayescount",
    "BayesianPower",
    "BayesPower",
    "BBMBC",
    "BetaPASS",
    "bfpwr",
    "cochranSize",
    "CoRpower",
    "CRTSize",
    "DelayedEffect.Design",
    "depower",
    "designsize",
    "easypower",
    "EBASS",
    "extraSuperpower",
    "FDRsamplesize2",
    "findn",
    "fPASS",
    "GenTwoArmsTrialSize",
    "ggpower",
    "H2x2Factorial",
    "ICC.Sample.Size",
    "InteractionPoweR",
    "intrinsicKappa",
    "irtpwr",
    "JMdesign",
    "kappaSize",
    "longpower",
    "lrstat",
    "MedianaDesigner",
    "metapower",
    "MetSizeR",
    "MIDN",
    "mixpower",
    "MKpower",
    "mlmpower",
    "mlpwr",
    "mpower",
    "MRMCsamplesize",
    "mthapower",
    "NPHMC",
    "nphPower",
    "npsurvSS",
    "odr",
    "pamm",
    "pass.lme",
    "PASSED",
    "peppwR",
    "phylosamp",
    "PICOTsize",
    "pmsesampling",
    "POSSA",
    "power.nb",
    "power4mome",
    "powerbrmsINLA",
    "powerCompRisk",
    "powerEQTL",
    "powergrid",
    "powerGWASinteraction",
    "powerHE",
    "powerLATE",
    "powerMediation",
    "powerNLSEM",
    "powerpkg",
    "powerPLS",
    "powerSurvEpi",
    "powertools",
    "PowerTOST",
    "powRICLPM",
    "PowRPriori",
    "presize",
    "PSpower",
    "pwr",
    "pwr2ppl",
    "pwr4exp",
    "pwrAB",
    "pwranova",
    "pwrRasch",
    "pwrss",
    "rashnu",
    "RCMsize",
    "RDSsamplesize",
    "rerandPower",
    "RMSTpowerBoost",
    "samplesize",
    "SampleSize4ClinicalTrials",
    "samplesize4surveys",
    "samplesizeCMH",
    "samplesizelogisticcasecontrol",
    "SampleSizeMeans",
    "SampleSizeR",
    "samplex",
    "semPower",
    "simpowa",
    "simpr.interaction",
    "simr",
    "SimTOST",
    "smartsizer",
    "sparrpowR",
    "Spower",
    "ssize.fdr",
    "ssizeRNA",
    "ssPilot",
    "SSRMST",
    "ssutil",
    "SteppedPower",
    "stepwedgepower",
    "Superpower",
    "survSNP",
    "swdpwr",
    "TrialSize",
    "twoCoprimary",
    "WebPower",
    "WMWssp",
    "MBESS",
    "faux",
    "simglm",
    "paramtest",
    "BUCSS",
    "pmsampsize",
    "simsem",
    "SampleSizeProportions",
    "pwrFDR",
    "ssanv",
    "PowerUpR",
    "powerlmm",
    "mixedpower",
    "powsimR",
    "PROPER",
    "RNASeqPower",
)

_POWER_SOFTWARE_OTHER: tuple[tuple[str, str], ...] = (
    # software that is not an R package, or written in other ways
    ("G*Power", r"\bg\s*[*-]?\s*power\b"),
    ("Superpower", r"\bsuper\s+power\b"),
    ("Morepower", r"\bmore\s*power\b"),
    ("Pangea", r"\bpangea\b"),
    ("POWSIM", r"\bpowsim\b"),
    ("PASS", r"\bpass\b|\bncss\b"),
    ("nQuery", r"\bnquery\b"),
    ("Optimal Design", r"\boptimal\s*design\b"),
    ("GLIMMPSE", r"\bglimmpse\b"),
    ("MLPowSim", r"\bmlpowsim\b"),
    ("PINT", r"\bpint\b"),
    ("jpower", r"\bjpower\b"),
    ("jamovi", r"\bjamovi\b"),
    ("JASP", r"\bjasp\b"),
    ("SAS", r"\bsas\b|proc\s+power"),
    ("Stata", r"\bstata\b"),
    ("SPSS", r"\bspss\b"),
    ("stats", r"\bpower\.(t|prop|anova)\.test\b"),
    # last: any named tool above is more specific than "a simulation"
    ("simulation", r"simulat|monte\s*carlo"),
)


def _power_software_rules() -> list[tuple[str, str]]:
    """``.power_software_rules()``: package names (longest first), then the other tools."""
    pkgs = sorted(_POWER_SOFTWARE_PACKAGES, key=lambda p: -len(p))  # stable, like order()
    by_name = [(p, "(?<![a-z0-9.])" + p.lower().replace(".", "\\.") + "(?![a-z0-9])") for p in pkgs]
    return [*by_name, *_POWER_SOFTWARE_OTHER]


def _power_canonical(
    x: list[str | None], rules: Sequence[tuple[str, str]], other: str = "other"
) -> list[str | None]:
    """``.power_canonical()``: the known value for each answer in *x*.

    The name of the first rule whose pattern matches, *other* for an answer no rule matches,
    ``None`` for a missing one.
    """
    from metacheck._r import grepl

    lc = [None if v is None else v.strip(" \t\r\n").lower() for v in x]
    out: list[str | None] = [None] * len(lc)
    given = [v is not None and v != "" and v not in ("none", "null", "na", "n/a") for v in lc]
    for i, g in enumerate(given):
        if g:
            out[i] = other
    todo = list(given)
    for name, pattern in rules:
        hits = grepl(pattern, ["" if v is None else v for v in lc], perl=True)
        for i, hit in enumerate(hits):
            if todo[i] and hit:
                out[i] = name
                todo[i] = False
    return out


def _power_canonicalise(table: pd.DataFrame) -> pd.DataFrame:
    """``.power_canonicalise()``: map a power table's category columns onto the known values.

    For statistical_test, effect_size_metric and software, an answer that became "other" is
    kept in the ``*_other`` column, so nothing the model wrote is lost (the prompt-based path
    may already have filled ``*_other`` itself).
    """
    fields: list[tuple[str, Sequence[tuple[str, str]], str]] = [
        ("power_type", _POWER_TYPE_RULES, "unknown"),
        ("statistical_test", _POWER_TEST_RULES, "other"),
        ("effect_size_metric", _POWER_ES_RULES, "other"),
        ("software", _power_software_rules(), "other"),
    ]
    table = table.copy()
    for col, rules, other in fields:
        if col not in table.columns:
            continue
        raw = [None if is_missing(v) else _r_chr(v) for v in table[col].tolist()]
        canon = _power_canonical(raw, rules, other)
        if col != "power_type":
            other_col = f"{col}_other"
            if other_col in table.columns:
                given = [None if is_missing(v) else _r_chr(v) for v in table[other_col].tolist()]
            else:
                given = [None] * len(raw)
            for i, v in enumerate(raw):
                if v is not None and v.strip(" \t\r\n").lower() == "other":
                    raw[i] = None
            kept = [
                (g if g is not None else r) if c == "other" else None
                for c, g, r in zip(canon, given, raw, strict=True)
            ]
            table[other_col] = pd.Series(kept, index=table.index, dtype="string")
        table[col] = pd.Series(canon, index=table.index, dtype="string")
    return table


def _r_chr(v: Any) -> str:
    """``as.character()`` of one value."""
    from metacheck._r import as_character

    return v if isinstance(v, str) else str(as_character(v))


def _power_unchecked(table: pd.DataFrame, row_failed: list[bool]) -> pd.DataFrame:
    """``.power_unchecked()``: the paragraphs the LLM could not check, per paper (#475).

    A data frame of ``paper_id`` and ``power_unchecked``, one row per paper with at least one
    such paragraph, sorted by paper_id. *table* is llm()'s result (one row per checked
    paragraph when a call failed) and *row_failed* marks those rows.
    """
    from metacheck._r import r_sort_key

    ids = table["paper_id"].tolist() if "paper_id" in table.columns else [None] * len(table)
    counts: dict[str, int] = {}
    for pid, failed in zip(ids, row_failed, strict=True):
        if failed and not is_missing(pid):  # table() leaves out NA
            counts[str(pid)] = counts.get(str(pid), 0) + 1
    names = sorted(counts, key=r_sort_key)
    return pd.DataFrame(
        {
            "paper_id": pd.Series(names, dtype="string"),
            "power_unchecked": pd.Series([counts[n] for n in names], dtype="Int64"),
        }
    )


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _col_isna(s: pd.Series) -> list[bool]:
    return [is_missing(v) for v in s.tolist()]


def _complete(table: pd.DataFrame, cols: list[str]) -> pd.Series:
    """``rowwise() |> mutate(complete = !any(across(cols, is.na)))``."""
    flags = [True] * len(table)
    for col in cols:
        for i, na in enumerate(_col_isna(table[col])):
            if na:
                flags[i] = False
    return pd.Series(flags, index=table.index, dtype="boolean")


def _read_lines_url(url: str) -> str:
    """``readLines(url) |> paste(collapse = "\\n")`` (no retries, as base R's ``url()``)."""
    from metacheck import http

    resp = http.request("GET", url, max_tries=1)
    if resp is None or resp.status_code >= 400:
        raise RuntimeError(f"cannot open the connection to '{url}'")
    text = resp.content.decode("utf-8", errors="replace")
    # readLines() splits on \n, \r\n and \r and drops a final line terminator
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# .power_llm_extract()
# ---------------------------------------------------------------------------


def _power_llm_extract(potential_power: pd.DataFrame, seed: Any) -> dict[str, Any]:
    """Port of ``.power_llm_extract()`` (power.R).

    Extracts the power-analysis fields with provider-enforced structured
    output, falling back to prompt-instructed JSON + ``json_expand()`` when
    every structured call fails. Returns ``{"table", "model", "structured",
    "failed", "unchecked"}``: ``table`` has one row per detected power analysis and a
    ``complete`` column marking whether every :data:`LLM_COLS` field was
    extracted. ``unchecked`` counts the paragraphs the LLM could not check, per paper.
    """
    from metacheck.datacheck.files import _strip_llm_wrapper
    from metacheck.llm import llm, llm_model

    llm_cols = list(LLM_COLS)

    try:
        structured_result: Any = llm(
            text=potential_power,
            system_prompt=_STRUCTURED_PROMPT,
            type=_power_type_spec(),
            text_col="text",
            model=llm_model(),
            params={"seed": seed},
        )
    except Exception:
        structured_result = None
    model = None
    if isinstance(structured_result, pd.DataFrame):
        model = (structured_result.attrs.get("llm") or {}).get("model")
    structured_result = _strip_llm_wrapper(structured_result, "power_analyses")

    # a systemic rejection: every row failed (R: all(vapply(.error, isTRUE, logical(1))))
    all_failed = structured_result is None or (
        ".error" in structured_result.columns
        and all(is_true(v) for v in structured_result[".error"].tolist())
    )

    if not all_failed:
        # map the free-text category answers onto the known values
        table = _power_canonicalise(structured_result)
        # a row that failed (but not every row) has power_type NA and is dropped just
        # below like an empty result; it is counted first (#475)
        if ".error" in table.columns:
            row_failed = [is_true(v) for v in table[".error"].tolist()]
        else:
            row_failed = [False] * len(table)
        unchecked = _power_unchecked(table, row_failed)
        table = table.drop(columns=[c for c in (".error", ".error_msg") if c in table.columns])
        if "power_type" in table.columns:
            keep = [not na for na in _col_isna(table["power_type"])]
            table = table.loc[keep].reset_index(drop=True)
        else:
            # every call returned an empty array: one stub row per paragraph
            table = table.iloc[0:0].reset_index(drop=True)
            table["power_type"] = pd.Series([], dtype="string")
        for col in llm_cols:
            if col not in table.columns:
                # R: rep(NA, nrow(table)), a logical column
                table[col] = pd.Series([pd.NA] * len(table), dtype="boolean")
        table["complete"] = _complete(table, llm_cols)
        return {
            "table": table,
            "model": model,
            "structured": True,
            "failed": False,
            "unchecked": unchecked,
        }

    # fallback: prompt-instructed JSON + json_expand() ----
    from metacheck.text.json_expand import json_expand

    # the bundled copy of the schema: metacheck downloads it with readLines(),
    # so the fallback fails without a connection (U108)
    system_prompt = f"{_PREFACE}\n\n{SCHEMA}"

    llm_results = llm(
        text=potential_power,
        system_prompt=system_prompt,
        text_col="text",
        model=llm_model(),
        params={"seed": seed},
    )
    fb_model = (llm_results.attrs.get("llm") or {}).get("model")

    table = json_expand(llm_results, suffix=("", ".power"))
    # a field no reply gave is not extracted: metacheck's any_of() skips the
    # missing column, so `complete` could be TRUE without it (U108)
    for col in llm_cols:
        if col not in table.columns:
            table[col] = pd.Series([pd.NA] * len(table), index=table.index, dtype="boolean")
    table["complete"] = _complete(table, llm_cols)

    n = len(table)
    if "error" in table.columns:
        row_failed = [not na for na in _col_isna(table["error"])]
    else:
        row_failed = [False] * n
    unchecked = _power_unchecked(table, row_failed)

    if "power_type" in table.columns:
        # "none" is this schema's "no power analysis here": canonicalised, it becomes NA
        table = _power_canonicalise(table)
        keep = [
            (not na) and not failed
            for na, failed in zip(_col_isna(table["power_type"]), row_failed, strict=True)
        ]
        table = table.loc[keep].reset_index(drop=True)
    else:
        table = table.iloc[0:0].reset_index(drop=True)

    return {
        "table": table,
        "model": fb_model,
        "structured": False,
        # every row of this call failed (not just "nothing found")
        "failed": len(potential_power) > 0 and len(table) == 0 and all(row_failed),
        "unchecked": unchecked,
    }
