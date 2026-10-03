"""Regression tests from the adversarial review of the funding modules.

Expected values were checked against R (metacheck at the pinned commit, PCRE2
via ``grepl(perl = TRUE)``); the same inputs are parity cases in
``parity/cases/mod_funding_review.yaml``.
"""

from __future__ import annotations

import numpy as np
import pytest

import metacheck as pc
from metacheck.core.errors import PytacheckWarning
from metacheck.modules import _funding as F

KELVIN = "\u212a"
LONG_S = "\u017f"
DOTTED = "\u0130"
DOTLESS = "\u0131"


# ---------------------------------------------------------------------------
# Non-ASCII letters (Turkish dotted/dotless i, long s, the Kelvin sign)
# ---------------------------------------------------------------------------
# Caseless matching is the regex module's Unicode simple case folding, not
# PCRE2's (docs/PORTING.md, section 3): the parity cases that depend on the
# difference are marked in parity/divergences/regex.yaml.


def test_prefilter_gives_the_full_scan() -> None:
    """The literal prefilter and the shared column cache give a full scan's answer."""
    from metacheck._r.regex import grepl
    from tests.mod_funding.test_funding_check import _all_patterns

    base = [
        "Financial aid from the NIH.",
        "FINANCIAL SUPPORT",
        "This study was funded by NIH.",
        "Grant support: NIH R01 12345",
        "Acknowledgements and funding",
        "No information about funding was received.",
    ]
    texts: list[str | None] = [None, ""]
    for t in base:
        texts.append(t)
        for ch in (DOTTED, DOTLESS, LONG_S, KELVIN):
            texts.append(t.replace("i", ch, 1).replace("I", ch, 1))
            texts.append(t.replace("s", ch, 1).replace("k", ch, 1))
    for pattern in _all_patterns():
        for ignore_case in (False, True):
            full = np.array(grepl(pattern, texts, ignore_case=ignore_case, perl=True))
            fast = F._Article(texts).mask(pattern, ignore_case)
            assert (full == fast).all(), pattern


@pytest.mark.parametrize(
    ("article", "expected"),
    [
        # the regex module folds "İ" to "i" ignoring case (R's PCRE2 does not: [])
        (["FUND" + DOTTED + "NG", "The NIH provided money."], [0, 1]),
        (["A" + LONG_S + " grant support: NIH R01"], []),
        (["Awor" + KELVIN + " grant support: NIH"], []),
        (["Awork grant support: NIH"], [0]),
        (["Grant " + LONG_S + "upport: NIH"], [0]),
    ],
)
def test_grant_and_title_locators(article: list[str], expected: list[int]) -> None:
    assert F.get_grant_1(article) + F.get_fund_2(article) == expected


def test_fund_acknow() -> None:
    assert F.get_fund_acknow(["This study was financed by NIH."]) == [0]


# ---------------------------------------------------------------------------
# rtransparent_funding(): acknowledgements fallback boundaries
# ---------------------------------------------------------------------------

FUNDED = "The pilot was funded partly by a charity."
ABSENT = "Financial information: No information about funding was received."


def _filler(n: int) -> list[str]:
    return [f"Filler sentence number {i}." for i in range(n)]


@pytest.mark.parametrize(("k", "found"), [(99, True), (100, True), (101, False)])
def test_acknowledgement_window(k: int, found: bool) -> None:
    article = ["Acknowledgements", *_filler(k - 1), FUNDED, "References"]
    assert F.rtransparent_funding(article) == (FUNDED if found else "")


@pytest.mark.parametrize(("pos", "found"), [(102, True), (103, False)])
def test_acknowledgement_window_references_first(pos: int, found: bool) -> None:
    article = ["References", "Acknowledgements", *_filler(118)]
    article[pos - 1] = FUNDED
    assert F.rtransparent_funding(article) == (FUNDED if found else "")


@pytest.mark.parametrize(("g", "expected"), [(10, f"{FUNDED} {ABSENT}"), (11, ABSENT)])
def test_acknowledgement_gap(g: int, expected: str) -> None:
    article = [
        "Acknowledgements",
        "Filler one.",
        FUNDED,
        *_filler(g - 3),
        ABSENT,
        "Other text.",
        "References",
    ]
    assert F._where_acknows_txt(article) == [0 if g <= 10 else g]
    assert F.rtransparent_funding(article) == expected


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------


def test_funding_check_oi_section_spelling() -> None:
    """U105: both spellings of the acknowledgment section type are favoured.

    metacheck's likely_section spells "acknowledgement", but its section type is
    "acknowledgment", so acknowledgment sections were never favoured.
    """
    from tests.mod_funding.parity_support import sectioned

    sentences = [
        "This work was supported by grant 1.",
        "Our study was funded by the ERC.",
        "Unrelated support for the theory in this paper.",
        "The authors declare no funding.",
    ]
    expected = ["Our study was funded by the ERC.", "The authors declare no funding."]
    p = sectioned(sentences, ["intro", "acknowledgement", "discussion", "acknowledgment"])
    assert pc.module_run(p, "funding_check_oi").table["text"].tolist() == expected
    p = sectioned(sentences, ["intro", "acknowledgment", "discussion", "acknowledgment"])
    assert pc.module_run(p, "funding_check_oi").table["text"].tolist() == expected


def test_duplicate_paper_ids_are_separate_papers() -> None:
    # U204: papers that share an ID are renamed (`id~2`), so each is grouped on its own;
    # metacheck pools their sentences into one group
    p1 = pc.test_paper(["This research was funded by NIH.", "Funding", "", "We thank NIH."])
    p2 = pc.test_paper(["Our study was supported by the ERC."])
    p2["paper_id"] = p1.paper_id
    p2["text"] = p2["text"].assign(paper_id=p1.paper_id)
    with pytest.warns(PytacheckWarning):
        mo = pc.module_run(pc.PaperList([p1, p2]), "funding_check")
    assert mo.table["text"].tolist() == [
        "This research was funded by NIH. Funding We thank NIH.",
        "Our study was supported by the ERC.",
    ]
    with pytest.warns(PytacheckWarning):
        mo = pc.module_run(pc.PaperList([p1, p2]), "funding_check_oi")
    assert mo.table["text"].tolist() == [
        "Our study was supported by the ERC.",
        "This research was funded by NIH.",
    ]


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_empty_paperlist(module: str) -> None:
    # U79: metacheck fails (text_search() of an empty list has no paper_id column);
    # pytacheck's text_search() returns a typed empty table, so nothing is found
    mo = pc.module_run(pc.PaperList([]), module)
    assert mo.traffic_light == "red"
    assert len(mo.table) == 0
