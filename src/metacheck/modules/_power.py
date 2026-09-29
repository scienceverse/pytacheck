"""LLM extraction for the power module (port of ``inst/modules/power.R``'s helpers).

``.power_type_spec()`` and ``.power_llm_extract()`` live at the bottom of
metacheck's ``inst/modules/power.R``; they are ported here so that
:mod:`metacheck.modules.power` only holds the module itself.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

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

_ENUM_COLS = ("power_type", "statistical_test", "effect_size_metric", "software")

_STRUCTURED_PROMPT = (
    "Identify power analyses from excerpts of scientific manuscripts. Use null/omit a field "
    "when information is missing, do not invent values. Only use 'other' if a value not in the "
    "enumerated options can be identified. A paragraph may contain no power analysis, or more "
    "than one -- return one entry in power_analyses per power analysis actually described, and "
    "an empty array if the paragraph only references a power analysis presented elsewhere, or "
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

    A single-field object (``power_analyses``: an array of power-analysis
    objects); optional enum fields list ``NA`` among their values so that
    providers accept a ``null`` answer for them.
    """
    from metacheck.llm.types import type_array, type_enum, type_number, type_object, type_string

    return type_object(
        power_analyses=type_array(
            type_object(
                power_type=type_enum(
                    ["apriori", "sensitivity", "posthoc", "unknown"],
                    description=(
                        "The type of power analysis. 'apriori' calculates the required sample "
                        "size to achieve a desired power given an effect size, statistical test, "
                        "and alpha level. 'sensitivity' estimates, given a sample size, which "
                        "effect sizes a design has sufficient power to detect. 'posthoc' "
                        "(observed/retrospective power) computes achieved power for an "
                        "empirically observed effect size. Use 'unknown' if a power analysis is "
                        "present but its type cannot be determined."
                    ),
                ),
                statistical_test=type_enum(
                    [
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
                        None,
                    ],
                    description="The statistical test used.",
                    required=False,
                ),
                statistical_test_other=type_string(
                    "Free-text description if statistical_test is 'other'.",
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
                effect_size_metric=type_enum(
                    [
                        "Cohen's d",
                        "Hedges' g",
                        "Cohen's f",
                        "partial eta squared",
                        "eta squared",
                        "unstandardised",
                        "other",
                        None,
                    ],
                    description=(
                        "The effect size metric. Use 'unstandardised' for "
                        "raw/non-standardized effects."
                    ),
                    required=False,
                ),
                effect_size_metric_other=type_string(
                    "Free-text description if effect_size_metric is 'other'.",
                    required=False,
                ),
                software=type_enum(
                    [
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
                        None,
                    ],
                    description="The software used to conduct the power analysis.",
                    required=False,
                ),
            ),
            description="Power analyses found in the text. Empty array if none.",
        )
    )


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _isna(v: Any) -> bool:
    """R ``is.na()`` of one cell (a list cell is never NA unless it is NULL/NA)."""
    if v is None or v is pd.NA:
        return True
    if isinstance(v, float):
        return v != v
    try:
        return bool(pd.isna(v)) if not isinstance(v, list | tuple | dict) else False
    except (TypeError, ValueError):
        return False


def _is_true(v: Any) -> bool:
    """R ``isTRUE()`` of one cell."""
    import numpy as np

    return isinstance(v, bool | np.bool_) and bool(v)


def _col_isna(s: pd.Series) -> list[bool]:
    return [_isna(v) for v in s.tolist()]


def _complete(table: pd.DataFrame, cols: list[str]) -> pd.Series:
    """``rowwise() |> mutate(complete = !any(across(cols, is.na)))``."""
    flags = [True] * len(table)
    for col in cols:
        for i, na in enumerate(_col_isna(table[col])):
            if na:
                flags[i] = False
    return pd.Series(flags, index=table.index, dtype="boolean")


def _as_character(s: pd.Series) -> pd.Series:
    """R ``as.character()`` of a factor/enum column."""
    if isinstance(s.dtype, pd.StringDtype):
        return s
    vals = [None if _isna(v) else str(v) for v in s.tolist()]
    return pd.Series(vals, index=s.index, dtype="string")


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
    "failed"}``: ``table`` has one row per detected power analysis and a
    ``complete`` column marking whether every :data:`LLM_COLS` field was
    extracted.
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
        and all(_is_true(v) for v in structured_result[".error"].tolist())
    )

    if not all_failed:
        table = structured_result.copy()
        # enum fields come back as factors: plain character, as json_expand() gives
        for col in _ENUM_COLS:
            if col in table.columns:
                table[col] = _as_character(table[col])
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
        return {"table": table, "model": model, "structured": True, "failed": False}

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
        row_failed = [False]  # R: scalar FALSE, recycled

    if "power_type" in table.columns:
        rf = row_failed if len(row_failed) == n else row_failed * n
        keep = [
            (not na) and pt != "none" and not failed
            for pt, na, failed in zip(
                table["power_type"].tolist(), _col_isna(table["power_type"]), rf, strict=True
            )
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
    }
