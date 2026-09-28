"""Port of tests/testthat/test-validate.R."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from pytacheck.module import ModuleError
from pytacheck.validate import AccuracyMeasures, accuracy, validate


def test_accuracy():
    exp = [True, False] * 50
    obs = [not v if i < 20 else v for i, v in enumerate(exp)]
    a = accuracy(exp, obs)
    assert isinstance(a, AccuracyMeasures)
    assert a.hits == 40
    assert a.misses == 10
    assert a.false_alarms == 10
    assert a.correct_rejections == 40
    assert a.accuracy == pytest.approx(0.8)
    assert a.sensitivity == pytest.approx(0.8)
    assert a.specificity == pytest.approx(0.2)
    assert round(a.d_prime, 2) == 1.68
    assert a.beta == pytest.approx(1)
    assert list(a) == [
        "hits",
        "misses",
        "false_alarms",
        "correct_rejections",
        "accuracy",
        "sensitivity",
        "specificity",
        "d_prime",
        "beta",
    ]


def test_accuracy_extremes():
    a = accuracy([True, True, False, False], [True, True, False, False])
    assert a.sensitivity == pytest.approx(1 - 0.5 / 2)
    assert a.specificity == pytest.approx(0.5 / 2)
    assert math.isfinite(a.d_prime)
    # no expected positives: the hit rate is NaN -> NA
    a = accuracy([False, False], [False, True])
    assert a.sensitivity is None and a.d_prime is None
    # NA in the input propagates
    a = accuracy([True, None], [True, True])
    assert a.hits is None and a.accuracy is None


def test_validate_errors():
    with pytest.raises(TypeError):
        validate()  # type: ignore[call-arg]
    with pytest.raises(ModuleError):
        validate(["p < .05"], "notamodule")


def test_validate_no_ground_truth():
    text = ["It was marginally significant.", "Nothing.", "A trend toward significance."]
    v = validate(text, "marginal")
    assert v["paper_id"].tolist() == ["1", "2", "3"]
    assert v["text"].tolist() == text
    assert v["text_id"].isna().tolist() == [False, True, False]
    assert not any(c.endswith(".valid") for c in v.columns)


def test_validate_with_ground_truth():
    gt = pd.DataFrame(
        {
            "paper_id": ["1", "2", "3"],
            "text": ["It was marginally significant.", "Nothing.", "A trend toward significance."],
            "header": ["Test", "Test", "Intro"],
        }
    )
    v = validate(gt, "marginal")
    for col in ("header.gt", "header.mod", "header.valid"):
        assert col in v.columns
    assert v["header.valid"].tolist() == [True, pd.NA, False]


def test_validate_stat_p_exact():
    """The R test uses stat_p_exact; run it when that module is available."""
    from pytacheck.module import module_find

    try:
        module_find("stat_p_exact")
    except ModuleError:
        pytest.skip("stat_p_exact is not ported yet")
    text = ["p < .05", "p = 0.034", "p > .10"]
    v = validate(text, "stat_p_exact")
    assert v["imprecise"].tolist() == [True, False, True]
    assert v["p_comp"].tolist() == ["<", "=", ">"]
    gt = pd.DataFrame({"paper_id": ["1", "2", "3"], "text": text, "p_comp": ["<", "=", ">"]})
    v = validate(gt, "stat_p_exact")
    assert {"p_comp.gt", "p_comp.mod", "p_comp.valid"} <= set(v.columns)
    assert v["p_comp.valid"].tolist() == [True, True, True]


def test_validate_full_join_keeps_unmatched(test_module):
    gt = pd.DataFrame(
        {
            "paper_id": ["1", "2"],
            "text": ["It was significant.", "Not in the paper."],
            "flag": [True, True],
        }
    )
    v = validate(gt, test_module("rp_validate_mod"))
    # gt rows first (with their matches), then the module's unmatched rows
    assert v["paper_id"].tolist() == ["1", "2", "zzz"]
    assert v["flag.valid"].tolist() == [True, False, pd.NA]
    assert v["text"].tolist()[-1] == "an extra row"
