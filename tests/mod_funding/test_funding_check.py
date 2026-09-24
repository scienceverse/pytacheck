"""Tests of the funding_check and funding_check_oi modules.

Ports ``test_that("funding_check", ...)`` of metacheck's
``tests/testthat/test-module-declarations.R`` (and the same checks for the
over-inclusive variant, which upstream does not test), plus unit tests of the
rtransparent helpers in :mod:`pytacheck.modules._funding`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError
from pytacheck.modules import _funding as F

FOUND = "This research was funded by UKRI grant #202020."
NONE = "The funding for arts is not great."


# ---------------------------------------------------------------------------
# test-module-declarations.R: test_that("funding_check", ...)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_module_declarations(module: str, demo: pc.Paper) -> None:
    mods = pc.module_list()
    assert module in mods["name"].tolist()

    pc.module_run(demo, module)

    # no funding
    mo = pc.module_run(pc.test_paper([NONE]), module)
    assert mo.traffic_light == "red"
    assert len(mo.table) == 0

    # has funding
    mo = pc.module_run(pc.test_paper([FOUND]), module)
    assert mo.traffic_light == "green"
    assert len(mo.table) == 1

    # paperlist
    paper = pc.PaperList([pc.test_paper([FOUND]), pc.test_paper([NONE])])
    mo = pc.module_run(paper, module)
    assert len(mo.summary_table) == 2
    assert mo.summary_table["funding_found"].tolist() == [True, False]


# ---------------------------------------------------------------------------
# module outputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_module_metadata(module: str) -> None:
    info = pc.module_info(module)
    assert info.name == module
    assert info.keywords == ("general",)
    assert info.requires == ()
    assert info.description == "Identify and extract funding statements."
    assert info.title == (
        "Funding Check" if module == "funding_check" else "Funding Check (Overinclusive)"
    )


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_outputs_green(module: str) -> None:
    mo = pc.module_run(pc.test_paper([FOUND, "Other text."]), module)
    assert list(mo.table.columns) == ["paper_id", "text"]
    assert mo.table["text"].tolist() == [FOUND]
    assert mo.summary_text == "A funding statement was detected."
    assert mo.report[0] == "The following funding statement was detected:"
    assert mo.report[1].data.iloc[:, 0].tolist() == [FOUND]
    assert mo.extras["na_replace"] is False
    assert list(mo.summary_table.columns) == ["paper_id", "funding_found"]


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_outputs_red_and_empty(module: str) -> None:
    for paper in (pc.test_paper([NONE]), pc.test_paper([])):
        mo = pc.module_run(paper, module)
        assert mo.traffic_light == "red"
        assert list(mo.table.columns) == ["paper_id", "text"]
        assert len(mo.table) == 0
        assert mo.summary_text == "No funding statement was detected."
        assert mo.report == "No funding statement was detected. Consider adding one."
        assert mo.summary_table["funding_found"].tolist() == [False]


def test_funding_check_psychsci(psychsci: pc.PaperList) -> None:
    mo = pc.module_run(psychsci, "funding_check")
    assert mo.traffic_light == "green"
    assert mo.summary_table["funding_found"].tolist() == [True, True, True]
    assert mo.table["text"].iloc[1] == (
        "Funding G. L. Malcolm was supported by Economic and Social Research Council "
        "Grant PTA-026-27-2500."
    )


def test_funding_check_title_lines_error() -> None:
    # R: `if (!is.na(article[a + 1]))` fails for two "Funding" title lines
    with pytest.raises(ModuleError, match="the condition has length > 1"):
        pc.module_run(pc.test_paper(["Funding", "Funding:"]), "funding_check")


def test_funding_check_out_of_range_index() -> None:
    # "Funding" followed by an empty line selects the line after that (NA in R)
    mo = pc.module_run(pc.test_paper(["Funding", ""]), "funding_check")
    assert mo.table["text"].tolist() == ["Funding NA"]


def test_funding_check_acknowledgement_fallback() -> None:
    paper = pc.test_paper(
        [
            "We studied things.",
            "Acknowledgements",
            "Thanks to the lab; this was funded partly by a charity.",
            "Project number 5 is ours.",
            "References",
        ]
    )
    mo = pc.module_run(paper, "funding_check")
    assert mo.table["text"].tolist() == [
        "Thanks to the lab; this was funded partly by a charity. Project number 5 is ours."
    ]


def test_funding_check_oi_prefers_funding_sections() -> None:
    from tests.mod_funding.parity_support import sectioned

    sentences = [
        "This work was supported by grant 1.",
        "Our study was funded by the ERC.",
        "Unrelated support for the theory in this paper.",
    ]
    mo = pc.module_run(sectioned(sentences, ["intro", "funding", "discussion"]), "funding_check_oi")
    assert mo.table["text"].tolist() == ["Our study was funded by the ERC."]
    # "acknowledgement" is not a metacheck section type ("acknowledgment")
    mo = pc.module_run(
        sectioned(sentences, ["intro", "acknowledgment", "discussion"]), "funding_check_oi"
    )
    assert len(mo.table) == 3


def test_modules_do_not_mutate(psychsci: pc.PaperList) -> None:
    before = [p.copy() for p in psychsci]
    for module in ("funding_check", "funding_check_oi"):
        pc.module_run(psychsci, module)
    assert all(a == b for a, b in zip(before, psychsci, strict=True))


# ---------------------------------------------------------------------------
# rtransparent helpers
# ---------------------------------------------------------------------------


def test_rtransparent_funding_basic() -> None:
    text = ["Intro.", "This study was funded by the Wellcome Trust.", "End."]
    assert F.rtransparent_funding(text) == "This study was funded by the Wellcome Trust."
    assert F.rtransparent_funding([]) == ""
    assert F.rtransparent_funding(["Nothing to see."]) == ""
    # absence statements are removed ...
    assert F.rtransparent_funding(["No information about funding was received."]) == ""
    # ... but "no funding" is a funding disclosure
    assert F.rtransparent_funding(["No funding was received for this study."]) == (
        "No funding was received for this study."
    )


def test_locators_are_zero_based() -> None:
    article = ["Intro.", "Funding", "", "Money came from the ERC."]
    assert F.get_fund_2(article) == [1, 3]
    assert F.get_fund_2(["Intro.", "Funding"]) == [1]
    assert F.get_support_1(["x", "This study was funded by the ERC."]) == [1]
    assert F._where_refs_txt(["a", "References", "b", "References"]) == [3]
    assert F._where_refs_txt(["a"]) == []


def test_character_checks() -> None:
    with pytest.raises(TypeError, match=r"is\.character\(article\) is not TRUE"):
        F.get_support_1([1, 2])
    with pytest.raises(TypeError, match=r"is\.character\(article\) is not TRUE"):
        F.obliterate_fullstop_1(None)
    assert F.get_support_1("This study was funded by the ERC.") == [0]
    assert F.get_support_1(pd.Series(["x", "This study was funded by the ERC."])) == [1]


def test_bound_and_title_helpers() -> None:
    assert F._bound(["a", "b"]) == ["a\\b", "b\\b"]
    assert F._bound(["a"], "both") == ["\\b[[:alnum:]]{0,1}a\\b"]
    assert F._bound([], "end") == ["\\b"]
    with pytest.raises(ValueError, match=r"Unknown location in \.bound"):
        F._bound(["a"], "middle")
    assert F._encase(["a", "b"]) == "(a|b)"
    assert F._title("X") == "^X(|:|\\.)$"
    assert F._title_strict("X", within_text=True) == "X( [A-Z][a-zA-Z]|:|\\.|\\s*-+)"
    assert F._max_words("x") == "x(?:\\s+\\w+){0,3}"
    assert F._first_capital(["Funding", "abc"]) == ["F(?i)unding(?-i)", "abc"]


def test_create_synonyms_is_a_copy() -> None:
    syn = F._create_synonyms()
    syn["financial"].append("for supporting")
    assert "for supporting" not in F._create_synonyms()["financial"]
    assert syn["txt"] == ["[a-zA-Z0-9\\s,()\\[\\]/:-]*"]


def test_obliterate_icu_classes() -> None:
    # ICU's \s includes NBSP and its [[:punct:]] excludes symbols such as "$"
    # (R: obliterate_fullstop_1(c("Mr.\u00a0smith", "x.$", "x.!", "x. $", "x. !")))
    nbsp = "\u00a0"
    assert F.obliterate_fullstop_1([f"Mr.{nbsp}smith", "x.$", "x.!", "x. $", "x. !"]) == [
        "Mr smith",
        "x$",
        "x!",
        "x. $",
        "x !",
    ]


# ---------------------------------------------------------------------------
# the literal prefilter never drops a real match
# ---------------------------------------------------------------------------


def _all_patterns() -> list[str]:
    from tests.mod_funding.parity_support import _pattern_table

    pats: list[str] = []
    for fn in _pattern_table().values():
        pats.extend(fn())
    return list(dict.fromkeys(pats))


def test_required_literals() -> None:
    assert F._required("(ab|cd)e\\bfg") == [("ab", "cd"), ("e",), ("fg",)]
    assert F._required("a(|s)b") == [("a",), ("b",)]
    assert F._required("x?yz") == [("yz",)]
    assert F._required("[Ff]unded") == [("unded",)]
    assert F._required("(?<![Ss]pecialty) [Ff]ellowship") == [(" ",), ("ellowship",)]
    assert F._required("ab|c") == [("ab", "c")]
    assert F._required("ab|") == []
    assert F._required("N(?i)O") == [("n",), ("o",)]
    assert F._required("(?:\\s+\\w+){0,3}ab{2,}") == [("ab",)]


def test_prefilter_matches_full_scan(psychsci: pc.PaperList) -> None:
    from pytacheck._r.regex import grepl
    from pytacheck.text import text_search
    from tests.mod_funding.make_parity_cases import BATTERY, BATTERY_2, OBLITERATE_TEXT

    texts = text_search(psychsci)["text"].tolist()
    texts += BATTERY + BATTERY_2 + [t for t in OBLITERATE_TEXT if t is not None]
    texts += [t.upper() for t in BATTERY] + [t.lower() for t in BATTERY]
    for pattern in _all_patterns():
        for ignore_case in (False, True):
            full = np.array(grepl(pattern, texts, ignore_case=ignore_case, perl=True))
            fast = F._Article(texts).mask(pattern, ignore_case)
            assert (full == fast).all(), pattern
