"""Texts and provider replies shared by the power-module parity cases and tests.

Each LLM scenario maps a substring of a paragraph to the ``power_analyses``
array a provider returns for it (or an HTTP error). ``make_fixtures.py``
writes them as Groq replies under ``tests/mod_power/mocks`` (httptest2
layout) and generates ``parity/cases/mod_power.yaml``.
"""

from __future__ import annotations

from typing import Any

# -- texts --------------------------------------------------------------------

POWER_TEXT = [
    "An a priori power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, an "
    "alpha level of 0.05, and a desired power level of 80% required at least 64 participants "
    "in each group.",
    "A sensitivity power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that with 64 participants in "
    "each group, and an alpha level of 0.05, a desired power level of 80% was reached for an "
    "effect size of d = 0.5.",
]

INCOMPLETE_TEXT = [
    "An a priori power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, and "
    "a desired power level of 80% required at least 64 participants in each group.",
    "A sensitivity power analysis for a paired samples t-test, conducted using G-Power, "
    "indicated that with 64 participants, an adequate power was reached for an effect size of "
    "d = 0.5.",
]

COMPLETE = (
    "An a priori power analysis indicated 64 participants per group, d = 0.5, alpha = .05, "
    "power = 80%, using an unpaired t-test, run with pwr."
)

TWO_IN_ONE = (
    "An a priori power analysis was conducted to estimate the sample size required to achieve "
    "80% power to detect a Cohen's d of 0.2 using an unpaired t-test at an alpha level of 0.05. "
    "This required a total sample size of 300 participants. A second a priori power analysis "
    "was conducted to estimate the required sample size for a secondary outcome. To achieve 80% "
    "power to detect a Cohen's f of 0.1 using a one-way ANOVA, a sample size of 350 was "
    "required. The a priori power analyses were conducted with G*Power."
)

MOTH = "Our 12 participants have a lot of power to detect a moth."

ALWAYS_FAILS = (
    "A sensitivity power analysis showed 80% power for d = 0.4 with 100 participants, but this "
    "paragraph always errors during structured extraction."
)

POSTHOC = (
    "A post hoc power analysis showed that the observed power was 0.35 for the observed effect "
    "of d = 0.2 with 40 participants."
)

OMITTED = (
    "An a priori power analysis determined a sample size of 30 for 80% power with a medium "
    "effect size, using an unpaired t-test, run with pwr."
)

OTHER = (
    "A simulation-based power analysis (1000 runs) for a linear mixed model showed 90% power to "
    "detect a 20 ms difference with 60 participants at an alpha of .005."
)

# texts for the regex classification (llm_use(FALSE))
CLASSIFY = [
    "An a-priori power analysis showed that 50 participants give 80% power.",
    "A sensitivity power analysis showed that 50 participants give 80% power for d = 0.4.",
    "A compromise power analysis balanced alpha and beta for 50 participants.",
    "A post-hoc power analysis showed 45% power.",
    "The retrospective power was 30% for the observed effect size.",
    "An A Posteriori power analysis gave 20% power.",
    "The observed power of the test was 0.25 (small effect).",
    "We had 95% power to detect our effect.",
    "Power analysis: with 2019 participants we achieve power.",
]


# -- provider replies -----------------------------------------------------------

_KEYS = (
    "power_type",
    "statistical_test",
    "statistical_test_other",
    "sample_size",
    "alpha_level",
    "power",
    "effect_size",
    "effect_size_metric",
    "effect_size_metric_other",
    "software",
)


def pa(omit: tuple[str, ...] = (), **fields: Any) -> dict[str, Any]:
    """A power-analysis object with every schema key (``null`` unless given)."""
    return {k: fields.get(k) for k in _KEYS if k not in omit}


APRIORI_FULL = pa(
    power_type="apriori",
    statistical_test="unpaired t-test",
    sample_size=128,
    alpha_level=0.05,
    power=0.8,
    effect_size=0.5,
    effect_size_metric="Cohen's d",
    software="pwr",
)
APRIORI_INCOMPLETE = pa(
    power_type="apriori",
    statistical_test="unpaired t-test",
    sample_size=128,
    power=0.8,
    effect_size=0.5,
    effect_size_metric="Cohen's d",
    software="pwr",
)
SENSITIVITY_INCOMPLETE = pa(
    power_type="sensitivity",
    statistical_test="paired t-test",
    sample_size=64,
    effect_size=0.5,
    effect_size_metric="Cohen's d",
    software="G*Power",
)

#: substring of the paragraph -> ``power_analyses`` array, or an int HTTP status
REPLIES: dict[str, Any] = {
    # both INCOMPLETE_TEXT sentences in one paragraph: one call, two analyses
    "in each group. A sensitivity power analysis for a paired": [
        APRIORI_INCOMPLETE,
        SENSITIVITY_INCOMPLETE,
    ],
    COMPLETE[:40]: [APRIORI_FULL],
    "A second a priori power analysis": [
        pa(
            power_type="apriori",
            statistical_test="unpaired t-test",
            sample_size=300,
            alpha_level=0.05,
            power=0.8,
            effect_size=0.2,
            effect_size_metric="Cohen's d",
            software="G*Power",
        ),
        pa(
            power_type="apriori",
            statistical_test="1-way ANOVA",
            sample_size=350,
            power=0.8,
            effect_size=0.1,
            effect_size_metric="Cohen's f",
            software="G*Power",
        ),
    ],
    MOTH: [],
    "always errors during structured extraction": 400,
    "for a Cohen's d = 0.5, and a desired power": [APRIORI_INCOMPLETE],
    "paired samples t-test": [SENSITIVITY_INCOMPLETE],
    "the observed power was 0.35": [
        pa(
            power_type="posthoc",
            sample_size=40,
            power=0.35,
            effect_size=0.2,
            effect_size_metric="Cohen's d",
        )
    ],
    OMITTED[:40]: [
        pa(
            omit=("alpha_level", "statistical_test_other", "effect_size_metric_other"),
            power_type="apriori",
            statistical_test="unpaired t-test",
            sample_size=30,
            power=0.8,
            effect_size=0.5,
            effect_size_metric="Cohen's d",
            software="pwr",
        )
    ],
    "A simulation-based power analysis": [
        pa(
            power_type="apriori",
            statistical_test="other",
            statistical_test_other="linear mixed model",
            sample_size=60,
            alpha_level=0.005,
            power=0.9,
            effect_size=20,
            effect_size_metric="unstandardised",
            software="simulation",
        )
    ],
    # demo paper paragraphs
    "This paper demonstrates some good and poor practices": [],
    "We conducted a sensitivity power analysis": [
        pa(
            power_type="sensitivity",
            sample_size=100,
            power=0.8,
            effect_size=0.5,
            effect_size_metric="Cohen's d",
        )
    ],
    "pwr::pwr.t.test(n = 50": [
        pa(power_type="unknown", sample_size=50, power=0.8, software="pwr"),
    ],
}


def reply_for(text: str) -> Any:
    """The reply for a paragraph (the first matching substring); ``[]`` if none."""
    for key, value in REPLIES.items():
        if key in text:
            return value
    return []
