"""Tests for the coi_check and coi_check_oi modules.

Sources: tests/testthat/test-module-declarations.R ("coi_check") and
tests/testthat/test-module-.R ("all builtin modules have essential components"),
plus unit tests of the R-faithful helpers (``agrep()`` emulation, TRE lazy
matching) whose expected values were measured with R 4.5.3 / metacheck.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import SECTION_LEVELS
from metacheck.modules.coi_check import (
    _cut_after_authors,
    agrep,
    agrepl,
    rtransparent_coi,
)

MODULES = ["coi_check", "coi_check_oi"]
FIXTURES = Path(__file__).parent / "fixtures"


# -- test-module-declarations.R: "coi_check" -----------------------------------


@pytest.mark.parametrize("module", MODULES)
def test_coi_listed(module: str) -> None:
    assert module in pc.module_list()["name"].tolist()


@pytest.mark.parametrize("module", MODULES)
def test_coi_no_coi(module: str) -> None:
    paper = pc.test_paper("There is a conflict between X and Y.")
    mo = pc.module_run(paper, module)
    assert mo.traffic_light == "red"
    assert len(mo.table) == 0
    assert mo.summary_text == "No conflict of interest statement was detected."
    assert mo.report == "No conflict of interest statement was detected. Consider adding one."


@pytest.mark.parametrize("module", MODULES)
def test_coi_has_coi(module: str) -> None:
    paper = pc.test_paper("The researchers state no conflict of interest.")
    mo = pc.module_run(paper, module)
    assert mo.traffic_light == "green"
    assert len(mo.table) == 1
    assert mo.table["text"].tolist() == ["The researchers state no conflict of interest."]
    assert mo.summary_text == "A conflict of interest statement was detected."
    assert mo.report[0] == "The following conflict of interest statement was detected."


@pytest.mark.parametrize("module", MODULES)
def test_coi_paperlist(module: str) -> None:
    paper = pc.PaperList(
        [
            pc.test_paper("The researchers state no conflict of interest."),
            pc.test_paper("There is a conflict between X and Y."),
        ]
    )
    mo = pc.module_run(paper, module)
    assert len(mo.summary_table) == 2
    assert mo.summary_table["paper_id"].tolist() == paper.names
    # na_replace = FALSE fills the paper without a statement
    assert mo.summary_table["coi_found"].tolist() == [True, False]


# -- test-module-.R: "all builtin modules have essential components" -----------


@pytest.mark.parametrize(
    ("module", "title"), [("coi_check", "COI Check"), ("coi_check_oi", "COI Check Overinclusive")]
)
def test_coi_essential_components(module: str, title: str) -> None:
    info = pc.module_info(module)
    assert info.title == title
    assert info.description == "Identify and extract Conflicts of Interest (COI) statements."
    assert info.details.startswith("The COI Check module uses regular expressions")
    assert info.keywords == ("general",)
    assert info.keywords[0] in SECTION_LEVELS
    assert info.requires == ()
    assert info.author == ("Daniel Lakens (\\email{d.lakens@tue.nl})",)


# -- behaviour on real papers ----------------------------------------------------


def test_coi_check_demo(demo: pc.Paper) -> None:
    mo = pc.module_run(demo, "coi_check")
    assert mo.traffic_light == "green"
    assert mo.table.columns.tolist() == ["paper_id", "text"]
    assert mo.table["text"].tolist() == ["The authors declare a conflict of interest."]
    assert mo.summary_table.to_dict("list") == {
        "paper_id": ["to_err_is_human"],
        "coi_found": [True],
    }
    assert mo.na_replace is False


def test_coi_check_oi_demo(demo: pc.Paper) -> None:
    mo = pc.module_run(demo, "coi_check_oi")
    assert mo.traffic_light == "green"
    # text_search(return = "section") columns
    assert mo.table.columns.tolist() == [
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "page_number",
        "formatted",
        "paper_id",
        "header",
        "section_type",
    ]
    assert mo.table["text"].tolist() == ["The authors declare a conflict of interest."]
    assert mo.table["section_type"].tolist() == ["contribution"]


@pytest.mark.parametrize("module", MODULES)
def test_coi_psychsci(module: str, psychsci: pc.PaperList) -> None:
    mo = pc.module_run(psychsci, module)
    assert mo.traffic_light == "green"
    assert mo.table["paper_id"].tolist() == psychsci.names
    expected = (
        "The authors declared that they had no conflicts of interest with respect to their "
        "authorship or the publication of this article."
    )
    assert mo.table["text"].tolist() == [expected] * 3
    assert mo.summary_table["coi_found"].tolist() == [True] * 3


@pytest.mark.parametrize("module", MODULES)
def test_coi_empty_paper(module: str) -> None:
    mo = pc.module_run(pc.test_paper([]), module)
    assert mo.traffic_light == "red"
    assert len(mo.table) == 0
    assert "text" in mo.table.columns
    assert mo.summary_table["coi_found"].tolist() == [False]


def test_coi_check_oi_merges_section() -> None:
    paper = pc.test_paper(
        [
            "The authors declare no competing interests.",
            "Other text.",
            "Disclosure of interests: none.",
            "Financial disclosure: the conflict of interest was nil.",
        ]
    )
    mo = pc.module_run(paper, "coi_check_oi")
    assert mo.table["text"].tolist() == [
        "The authors declare no competing interests. Disclosure of interests: none."
    ]


@pytest.mark.parametrize("module", MODULES)
def test_coi_references_excluded(module: str) -> None:
    # the only COI sentences are in the references: text_search() skips them
    mo = pc.module_run(pc.read(FIXTURES / "coi_refs_only.json"), module)
    assert mo.traffic_light == "red"
    assert len(mo.table) == 0


def test_coi_section_headers() -> None:
    # expected values from R (metacheck at the pinned commit)
    paper = pc.read(FIXTURES / "coi_headers.json")
    # coi_check only reads sentences: headers, the null sentence and blanks add nothing
    mo = pc.module_run(paper, "coi_check")
    assert mo.table["text"].tolist() == ["Conflict of interest was absent."]
    # coi_check_oi also searches headers (search_header = TRUE), then merges by section
    mo = pc.module_run(paper, "coi_check_oi")
    assert mo.table["text"].tolist() == [
        "None.",
        "Conflict of interest was absent.",
        "The authors declare none. Funding was provided by X.\n\nSecond paragraph here.",
        "Disclosure: none reported.",
        "None declared.",
    ]
    # R row order (one text_search() per pattern, then bind_rows); each section keeps
    # its header (metacheck's return = "section" gives NA headers, U80)
    assert mo.table["section_id"].tolist() == [2, 5, 3, 4, 6]
    assert mo.table["header"].tolist() == [
        "Conflict of Interest",
        "Financial Disclosure",
        "Competing Interests",
        "COI",
        "Declaration of interests",
    ]


@pytest.mark.parametrize("module", MODULES)
def test_coi_does_not_mutate(module: str, demo: pc.Paper) -> None:
    before = demo.text.copy()
    pc.module_run(demo, module)
    pd.testing.assert_frame_equal(demo.text, before)


# -- rtransparent_coi() branches (expected values from R) -------------------------


@pytest.mark.parametrize(
    ("sentences", "expected"),
    [
        (["There is a conflict between X and Y."], ""),
        (
            ["The researchers state no conflict of interest."],
            "The researchers state no conflict of interest.",
        ),
        # short heading: the next sentence (skipping an empty one) is appended
        (["Conflict of Interest", "", "None."], "Conflict of Interest None."),
        (
            ["Competing interests", "The authors declare", "no competing interests exist"],
            "Competing interests The authors declare no competing interests exist",
        ),
        # U97: nothing past the last sentence is pasted (metacheck appends "NA")
        (
            ["Results were clear.", "Conflicts of interest", "The authors declare none"],
            "Conflicts of interest The authors declare none",
        ),
        (["Methods were standard.", "Conflict of Interest", ""], "Conflict of Interest"),
        (["Disclosure", "Nothing"], "Disclosure Nothing"),
        (["Patient information disclosure was handled by the hospital."], ""),
        (["Financial disclosure: The study was funded by X."], ""),
        (["Conflicts of interest. Conflicts of interest."], "Conflicts of interest."),
        (["Conflict of interest: None. Other text follows here."], "Conflict of interest: None."),
        # U95: every "None/No/Nil" alternative stops after the full stop (metacheck
        # keeps only groups 1-4, so "Nil.", "No.", "None mentioned." emptied the text)
        (["Conflict of interest: Nil. Other text follows."], "Conflict of interest: Nil."),
        (["conflict of interest: nil. More text here."], "conflict of interest: nil."),
        (
            ["Competing interests: None mentioned. More text."],
            "Competing interests: None mentioned.",
        ),
        (["Competing interests: No. More text."], "Competing interests: No."),
        (
            ["Acknowledgements We thank X. The authors declare no competing interests."],
            "The authors declare no competing interests.",
        ),
        (
            [
                "No conflicts of interest. Also the note: The authors declare no conflict of interest"
            ],
            "No conflicts of interest.",
        ),
        # agrep() is approximate: a misspelled "confict" is found, then dropped
        (["A confict of interest arises here"], ""),
        (
            [
                "Conflict of interest:",
                "Author A has received fees.",
                "Author B holds shares.",
                "Disclosure: none.",
            ],
            "Conflict of interest: Author A has received fees. Author B holds shares. Disclosure: none.",
        ),
    ],
)
def test_rtransparent_coi(sentences: list[str], expected: str) -> None:
    assert rtransparent_coi(sentences) == expected


def test_rtransparent_coi_heading_last_sentence() -> None:
    # U97: metacheck stops on if (nchar(NA) == 0); the heading is the statement found
    assert (
        rtransparent_coi(["Participants were recruited online.", "Conflict of interest"])
        == "Conflict of interest"
    )
    res = pc.module_run(pc.test_paper(["Conflict of interest"]), "coi_check")
    assert res.traffic_light == "green"
    assert res.table["text"].tolist() == ["Conflict of interest"]


def test_coi_check_nil_statement_found() -> None:
    # U95: metacheck drops "Conflict of interest: Nil." and reports no statement (red)
    res = pc.module_run(pc.test_paper(["Conflict of interest: Nil. Other text."]), "coi_check")
    assert res.traffic_light == "green"
    assert res.table["text"].tolist() == ["Conflict of interest: Nil."]


def test_rtransparent_coi_empty() -> None:
    assert rtransparent_coi([]) == ""


# -- agrep() emulation (expected values from R's agrep/agrepl) --------------------


def test_agrepl_matches_r() -> None:
    x = ["bcdef", "abdef", "abXdef", "aXbcdef", "acbdef", "abc", "Abcdef", "xbcdex"]
    assert agrepl("abcdef", x) == [True, True, True, True, False, False, True, False]
    x = ["conflict", "onflict", "CONFLICT", "cOnflict", "Conflicts", "flict"]
    assert agrepl("Conflict", x) == [True, True, False, False, True, False]
    # fixed = TRUE: regex metacharacters are literal
    x = [
        "no conflict",
        "no.{0,20}conflict",
        "no.{0,2}conflict",
        "no.{0,20}onflict",
        "No.{0,20}Conflict",
    ]
    assert agrepl("no.{0,20}conflict", x, ignore_case=True) == [False, True, True, True, True]
    x = ["interest.", "interest.{0,1}[.:;,]", "interest{0,1}[.:;,]", "interest.{0,1}[.:,]"]
    assert agrepl("interest.{0,1}[.:;,]", x, ignore_case=True) == [False, True, True, True]
    x = ["Conflict of Interest", "conflicts of interests", "conflict  interest", "confli of inter"]
    assert agrep("conflict of interest", x, ignore_case=True) == [1, 2, 3]


def test_agrepl_na_and_scalar() -> None:
    assert agrepl("author", None) is False
    assert agrepl("author", "The AUTHORS") is False
    assert agrepl("author", "The Authors") is True  # one substitution (A -> a)
    assert agrepl("author", "The Authors", ignore_case=True) is True
    assert agrep("author", ["x", None, "authr"], ignore_case=True) == [3]


def test_agrepl_short_patterns() -> None:
    # k = ceiling(0.1 * 1) = 1: every non-NA string (even "") is within one edit
    assert agrepl("a", ["", "b", None, "xyz"]) == [True, True, False, True]
    assert agrep("a", ["", "b", None, "xyz"]) == [1, 2, 4]
    assert agrepl("a", "") is True
    assert agrepl("ab", ["", "b", "x", "xy"]) == [False, True, False, False]


def test_agrepl_repeated_pieces() -> None:
    # the pigeonhole pieces of this pattern are all "abab" (offsets 0, 4 and 8)
    x = [
        "xxabababababyy",
        "ababab",
        "abababababa",
        "abababXabababa",
        "abXbabXbabab",
        "babababababa",
        "aabbaabbaabb",
    ]
    assert agrepl("abababababab", x) == [True, False, True, True, True, True, False]


def test_agrep_does_not_match_across_elements() -> None:
    # the batched prefilter joins the elements; a match must not span two of them
    x = ["xx conflict of inte", "rest yy", None, "conflict of interest"]
    assert agrep("conflict of interest", x, ignore_case=True) == [4]
    x = ["conflict of", "interest", "CONFLICT OF INTERST"]
    assert agrep("conflict of interest", x, ignore_case=True) == [3]


def test_agrepl_only_ascii_case_folding() -> None:
    # TRE compares text characters with the pattern letter's lower/upper case only
    assert agrepl("kelvin", "Kelvin", ignore_case=True) is True  # one substitution
    assert agrepl("ab", "KK", ignore_case=True) is False


# -- TRE lazy quantifiers ---------------------------------------------------------


def test_cut_after_authors_uses_tre_minimal_end() -> None:
    # a backtracking engine would stop at the *last* ". X" after the last "interest"
    s = "Interest. The authors declare no conflict of interest in A. B. C interest. D. E"
    assert _cut_after_authors(s) == "Interest. The authors declare no conflict of interest in A."
    # the first alternative that matches wins, even with a later end
    s = "No competing financial interest. X. The authors declare no conflict of interest. Y z."
    assert _cut_after_authors(s) == (
        "No competing financial interest. X. The authors declare no conflict of interest."
    )
    s = "The author has no competing interest. A. B. The authors declare no conflict of interest. Y z."
    assert _cut_after_authors(s) == "The author has no competing interest."
    assert _cut_after_authors("No match here. At all.") == "No match here. At all."
