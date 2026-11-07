"""Port of the ``.power_canonical()`` tests of metacheck's test-module-power.R (#475).

The model's free-text answers are mapped onto the known values by the module, so the
structured schema has no enum a provider could reject.
"""

from __future__ import annotations

import pandas as pd

from metacheck.modules import _power as pw


def test_software_answers_map_onto_the_known_names() -> None:
    answers = [
        "G*Power 3.1", "GPower", "POWSIM v4.1", "pwr.t.test in R", "the pwrss package",
        "simulation using simr", "Monte Carlo simulation", "pass.lme", "NCSS PASS",
        "R", "none", None,
    ]  # fmt: skip
    got = pw._power_canonical(answers, pw._power_software_rules())
    assert got == [
        "G*Power", "G*Power", "POWSIM", "pwr", "pwrss", "simr", "simulation", "pass.lme",
        "PASS", "other", None, None,
    ]  # fmt: skip


def test_every_listed_r_package_maps_onto_itself() -> None:
    pkgs = list(pw._POWER_SOFTWARE_PACKAGES)
    assert pw._power_canonical(pkgs, pw._power_software_rules()) == pkgs


def test_statistical_tests() -> None:
    answers = [
        "independent samples t-test", "paired-samples t test", "one-sample t-test",
        "one-way ANOVA", "2 x 2 ANOVA", "2 x 3 x 2 ANOVA", "MANOVA", "logistic regression",
        "chi-square test", "Pearson correlation", "t-test", "linear mixed model",
    ]  # fmt: skip
    assert pw._power_canonical(answers, pw._POWER_TEST_RULES) == [
        "unpaired t-test", "paired t-test", "one-sample t-test", "1-way ANOVA", "2-way ANOVA",
        "3-way ANOVA", "MANOVA", "regression", "chi-square", "correlation", "other", "other",
    ]  # fmt: skip


def test_effect_size_metrics() -> None:
    answers = [
        "Cohen's d", "Cohen’s dz", "d", "Hedges' g", "Cohen's f", "Cohen's f2",
        "partial eta squared", "ηp²", "eta squared", "raw mean difference",
        "standardized mean difference", "odds ratio",
    ]  # fmt: skip
    assert pw._power_canonical(answers, pw._POWER_ES_RULES) == [
        "Cohen's d", "Cohen's d", "Cohen's d", "Hedges' g", "Cohen's f", "other",
        "partial eta squared", "partial eta squared", "eta squared", "unstandardised",
        "other", "other",
    ]  # fmt: skip


def test_power_types() -> None:
    answers = ["a priori", "Sensitivity", "post-hoc", "observed power", "compromise", "none"]
    got = pw._power_canonical(answers, pw._POWER_TYPE_RULES, other="unknown")
    assert got == ["apriori", "sensitivity", "posthoc", "posthoc", "unknown", None]


def test_an_answer_that_becomes_other_is_kept_in_the_other_column() -> None:
    table = pd.DataFrame(
        {
            "power_type": "a priori",
            "software": ["POWSIM", "a custom R script"],
            "statistical_test": ["unpaired t-test", "linear mixed model"],
        }
    )
    out = pw._power_canonicalise(table)
    assert out["software"].tolist() == ["POWSIM", "other"]
    assert out["software_other"].tolist() == [pd.NA, "a custom R script"]
    assert out["statistical_test_other"].tolist() == [pd.NA, "linear mixed model"]
    assert out["power_type"].tolist() == ["apriori", "apriori"]


def test_an_other_the_model_gave_itself_is_not_lost() -> None:
    # the prompt-based path may already have filled *_other
    table = pd.DataFrame(
        {
            "power_type": ["a priori"],
            "software": ["other"],
            "software_other": ["Stata"],
        }
    )
    out = pw._power_canonicalise(table)
    assert out["software"].tolist() == ["other"]
    assert out["software_other"].tolist() == ["Stata"]


def test_the_schema_has_no_enum_left_that_an_answer_could_fail() -> None:
    spec = pw._power_type_spec()
    item = spec["properties"]["power_analyses"]["items"]["properties"]
    assert not any("enum" in prop for prop in item.values())
