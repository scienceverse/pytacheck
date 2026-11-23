"""Port of the marginal test of metacheck's tests/testthat/test-module-stat.R (#695, point 9)."""

from __future__ import annotations

import metacheck as pc
from metacheck.module import module_run


def test_marginal_significan_close_after_the_trigger_word() -> None:
    txt = [
        "It was marginally non-significant (p = .07).",
        "A trend-level significance was found.",
        "Results approached conventional levels of significance.",
        "This approach is a significant contribution.",
        "The trend was significant.",
        "There was almost no significant change.",
        "The margins had significantly more cover.",
    ]
    found = module_run(pc.test_paper(txt), "marginal").table["text"].tolist()
    assert found == txt[:3]


def test_marginal_forms_and_exclusions() -> None:
    txt = [
        "The effect was marginally statistically significant.",
        "A submarginal significance was seen.",
        "The result bordered on significance.",
        "Scores trended towards significance.",
        "It approaches significance tests.",
        "Almost reaching significance, the effect was small.",
    ]
    found = module_run(pc.test_paper(txt), "marginal").table["text"].tolist()
    assert found == [txt[0], txt[2], txt[3], txt[5]]
