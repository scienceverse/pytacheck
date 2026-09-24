"""Regression tests from the adversarial review of the funding modules.

Expected values were checked against R (metacheck at the pinned commit, PCRE2
via ``grepl(perl = TRUE)``); the same inputs are parity cases in
``parity/cases/mod_funding_review.yaml``.
"""

from __future__ import annotations

import numpy as np
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError
from pytacheck.modules import _funding as F

KELVIN = "\u212a"
LONG_S = "\u017f"
DOTTED = "\u0130"
DOTLESS = "\u0131"


# ---------------------------------------------------------------------------
# PCRE caseless matching of non-ASCII letters
# ---------------------------------------------------------------------------

CHARS = ["a", "A", "s", "S", LONG_S, KELVIN, DOTTED, DOTLESS, "1", "_", ".", "\u00e9", "k"]

#: grepl(p, CHARS, perl = TRUE) in R
R_CLASSES = {
    "(?i)\\w": "1111000011001",
    "(?i)^\\W$": "0000111100110",
    "(?i)[[:alnum:]]": "1111000010001",
    "(?i)[[:upper:]]": "1111000000001",
    "(?i)[[:lower:]]": "1111000000001",
    "(?i)[^[:upper:]]": "0000111111110",
    "(?i)[[:upper:][:digit:]]": "1111000010001",
    "(?i)[^\\W]": "1111000011001",
    "(?i)[\\W]": "0000111100110",
    "(?i)[a-zA-Z0-9\\s,()\\[\\]/:-]": "1111110010001",
    "(?i)[^a-z]": "0000001111110",
    "(?i)[Kk]": "0000010000001",
    "(?i)[Ii]": "0000000000000",
    "(?i)[Ss]": "0011100000000",
    "(?i)\\bs\\b": "0011000000000",
}


@pytest.mark.parametrize("pattern", sorted(R_CLASSES))
def test_caseless_classes_match_pcre2(pattern: str) -> None:
    expected = [c == "1" for c in R_CLASSES[pattern]]
    assert F._Article(CHARS).mask(pattern).tolist() == expected


def test_caseless_pcre_rewrites() -> None:
    assert F._caseless_pcre("(?i)\\w+") == "(?i)(?-i:\\w)+"
    assert F._caseless_pcre("(?i)[[:alnum:]]") == "(?i)(?-i:[[:alnum:]])"
    assert F._caseless_pcre("(?i)[[:upper:][:digit:]]") == "(?i)(?-i:[[:alpha:][:digit:]])"
    # negated escapes in sets rely on the foundation's ASCII scoping: untouched
    assert F._caseless_pcre("(?i)[^\\W]") == "(?i)[^\\W]"
    # explicit members are folded by PCRE2 too: untouched
    assert F._caseless_pcre("(?i)[a-z]\\.") == "(?i)[a-z]\\."


def test_dotted_i_rows_with_prefilter() -> None:
    """The literal prefilter and the U+0130/U+0131 re-match give the full scan's answer."""
    from pytacheck._r.regex import grepl
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
            caseless = ignore_case or "(?i)" in pattern
            run = F._caseless_pcre(pattern) if caseless else pattern
            subject = [
                t.translate(F._DOTTED_I) if (t is not None and caseless) else t for t in texts
            ]
            full = np.array(grepl(run, subject, ignore_case=ignore_case, perl=True))
            fast = F._Article(texts).mask(pattern, ignore_case)
            assert (full == fast).all(), pattern


@pytest.mark.parametrize(
    ("article", "expected"),
    [
        (["FUND" + DOTTED + "NG", "The NIH provided money."], []),
        (["A" + LONG_S + " grant support: NIH R01"], []),
        (["Awor" + KELVIN + " grant support: NIH"], []),
        (["Awork grant support: NIH"], [0]),
        (["Grant " + LONG_S + "upport: NIH"], [0]),
    ],
)
def test_grant_and_title_locators(article: list[str], expected: list[int]) -> None:
    assert F.get_grant_1(article) + F.get_fund_2(article) == expected


def test_fund_acknow_dotted_i() -> None:
    # ignore.case = TRUE: PCRE2 does not fold U+0130 to "i"
    assert F.get_fund_acknow(["This study was f" + DOTTED + "nanced by NIH."]) == []
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
    """R's likely_section spells "acknowledgement"; metacheck's type is "acknowledgment"."""
    from tests.mod_funding.parity_support import sectioned

    sentences = [
        "This work was supported by grant 1.",
        "Our study was funded by the ERC.",
        "Unrelated support for the theory in this paper.",
        "The authors declare no funding.",
    ]
    p = sectioned(sentences, ["intro", "acknowledgement", "discussion", "acknowledgment"])
    mo = pc.module_run(p, "funding_check_oi")
    assert mo.table["text"].tolist() == ["Our study was funded by the ERC."]
    p = sectioned(sentences, ["intro", "acknowledgment", "discussion", "acknowledgment"])
    assert len(pc.module_run(p, "funding_check_oi").table) == 4


def test_duplicate_paper_ids_are_one_group() -> None:
    p1 = pc.test_paper(["This research was funded by NIH.", "Funding", "", "We thank NIH."])
    p2 = pc.test_paper(["Our study was supported by the ERC."])
    p2["paper_id"] = p1.paper_id
    p2["text"] = p2["text"].assign(paper_id=p1.paper_id)
    mo = pc.module_run(pc.PaperList([p1, p2]), "funding_check")
    assert mo.table["text"].tolist() == [
        "This research was funded by NIH. Funding We thank NIH. Our study was supported by the ERC."
    ]
    mo = pc.module_run(pc.PaperList([p1, p2]), "funding_check_oi")
    assert mo.table["text"].tolist() == [
        "Our study was supported by the ERC.",
        "This research was funded by NIH.",
    ]


@pytest.mark.parametrize("module", ["funding_check", "funding_check_oi"])
def test_empty_paperlist_errors(module: str) -> None:
    # metacheck fails too (text_search() of an empty list has no paper_id column)
    with pytest.raises(ModuleError):
        pc.module_run(pc.PaperList([]), module)
