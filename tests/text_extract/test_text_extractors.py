"""Port of tests/testthat/test-text-extractors.R (extract_urls, extract_p_values, extract_eq)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

import metacheck as pc
from metacheck._r.regex import grepl
from metacheck.text.extract import (
    _detect_live_data,
    extract_eq,
    extract_p_values,
    extract_urls,
)


def test_extract_urls() -> None:
    with pytest.raises(TypeError):
        extract_urls(1)

    valid_urls = [
        "https://osf.io/48ncu",
        "http://researchbox.org/4377",
        "osf.io/48ncu",
        "http://researchbox.org/4377?PASWORD=10&hi",
    ]
    urls = extract_urls(pc.test_paper(valid_urls))
    assert urls["text"].tolist() == valid_urls

    invalid_urls = ["http is a way to get osf .io", "It is done. And now...", "."]
    urls = extract_urls(pc.test_paper(invalid_urls))
    assert len(urls) == 0


def test_extract_urls_et_al_glued_to_a_word_is_not_a_url() -> None:
    # U158: metacheck lists "al.Premotor" (psychsci) and "al.Inhibitory" as URLs
    texts = ["Catarino et al.Inhibitory Control", "as in Smith et al.Premotor areas"]
    assert len(extract_urls(pc.test_paper(texts))) == 0
    # a real host name after another word is still found
    urls = extract_urls(pc.test_paper(["data: set al.com and osf.io/abc"]))
    assert urls["text"].tolist() == ["al.com", "osf.io/abc"]


def test_extract_urls_skips_email_addresses() -> None:
    # U206: metacheck lists "k.aristovich" and "ucl.ac.uk" (and "gmail.com") as URLs
    texts = [
        "Contact k.aristovich@ucl.ac.uk or m.t.calcagni@sub.dept.univ.edu.",
        "Email: someone@hotmail.com.br, then see osf.io/abc.",
    ]
    assert extract_urls(pc.test_paper(texts))["text"].tolist() == ["osf.io/abc"]
    # a URL with an @ after its host is still a URL, and the table keeps rows 0..n-1
    urls = extract_urls(pc.test_paper(["a@b.org", "see twitter.com/@user and https://x.org/a@b.c"]))
    assert urls["text"].tolist() == ["twitter.com/@user", "https://x.org/a@b.c"]
    assert urls.index.tolist() == [0, 1]


def test_extract_urls_paperlist(psychsci) -> None:
    urls = extract_urls(psychsci)
    assert set(urls["paper_id"]) <= set(psychsci.names)


def test_extract_urls_demo(demo) -> None:
    urls = extract_urls(demo)
    assert urls["text"].tolist()[:2] == [
        "https://osf.io/48ncu",
        "https://aspredicted.org/by8i8v.pdf",
    ]
    assert list(urls.columns) == [
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


def test_extract_p_values() -> None:
    with pytest.raises(TypeError):
        extract_p_values(1)

    paper = pc.test_paper(
        [
            "t = 2.23, p = 0.005.",
            "(p = 0.152)",
            "peta = 2.3; p > .05, ppp = 2",
            "2 = p",
            "MAP = .5, P = .04",
        ]
    )
    p = extract_p_values(paper)
    assert len(p) == 4
    assert p["text"].tolist() == ["p = 0.005", "p = 0.152", "p > .05", "P = .04"]
    assert p["p_value"].tolist() == pytest.approx([0.005, 0.152, 0.050, 0.04])
    assert p["p_comp"].tolist() == ["=", "=", ">", "="]


def test_extract_p_values_paperlist(psychsci) -> None:
    p = extract_p_values(psychsci)
    assert len(p) > 0
    assert set(p["paper_id"]) <= set(psychsci.names)


def test_extract_p_values_formats() -> None:
    expected = [
        "p=.05",
        "p\n=\n.05",
        "p = .05",
        "p < .05",
        "p > .05",
        "p <= .05",
        "p >= .05",
        "p == .05",
        "p << .05",
        "p >> .05",
        "p ≤ .05",
        "p ≥ .05",
        "p ≪ .05",
        "p ≫ .05",
        "p ≠ .05",
        "p-value = .05",
        "pvalue = .05",
        "p = 0.05",
        "p = 0.05",
        "p = 0.5e-1",
        "p = n.s.",
        "p = ns",
        "p = 5.0x10^-2",
        "p = 5.0 x 10^-2",
        "p = 5.0 x 10 ^ -2",
        "p = 5.0 * 10 ^ -2",
        "p = 5.0e-2",
        "p = 5.0 e-2",
        "p = 5.0 e -2",
    ]
    not_p = ["up = 0.05", "p = stuff", "p = -0.05", "p less than 0.05", "p = 12.05"]
    paper = pd.DataFrame(
        {
            "id": 1,
            "text": expected + not_p,
            "expected": [True] * len(expected) + [False] * len(not_p),
        }
    )
    p = extract_p_values(paper)
    assert "" not in p["p_comp"].tolist()
    values = p["p_value"].tolist()
    assert values[:20] == pytest.approx([0.05] * 20)
    assert all(math.isnan(v) for v in values[20:22])
    assert values[22:29] == pytest.approx([0.05] * 7)


def test_extract_p_values_ps_and_notation() -> None:
    # U204: "ps", "p's" and "p-values" are p; scientific notation may use "×",
    # leave out "^" and use the Unicode minus (metacheck read 'p = 1.8 × 10 -6'
    # as p = 1.8 and found none of the others)
    texts = [
        "ps < .05",
        "p's > .10",
        "p’s = .03",
        "p-values < .01",
        "Ps < .001",
        "p = 1.8 × 10 -6",
        "p = 6.1 × 10\u22125",
        "p=2.14e\u2212213",
        "p = 5.0 x 10^-5",
    ]
    p = extract_p_values(texts)
    assert [t.strip() for t in p["text"]] == texts
    assert p["p_comp"].tolist() == ["<", ">", "=", "<", "<", "=", "=", "=", "="]
    assert p["p_value"].tolist() == pytest.approx(
        [0.05, 0.10, 0.03, 0.01, 0.001, 1.8e-6, 6.1e-5, 2.14e-213, 5e-5]
    )
    assert len(extract_p_values(["maps < .05", "pss < .05", "p = \u22120.05"])) == 0


def test_extract_eq_unicode_minus() -> None:
    # U205: a number may have the Unicode minus (U+2212), with one space after a
    # leading one; rhs has "-" (metacheck matched none of these)
    eq = extract_eq(
        [
            "t(28) = \u22122.15, p = .04, d = \u22120.80",
            "r = \u2212 0.12 and r(98) = \u2212.32",
            "t(20) = -2.1",
        ]
    )
    assert eq["lhs"].tolist() == ["t", "p", "d", "r", "r", "t"]
    assert eq["rhs"].tolist() == ["-2.15", ".04", "-0.80", "-0.12", "-.32", "-2.1"]


def test_extract_p_values_empty() -> None:
    p = extract_p_values(pc.test_paper(["No p-values here.", "The p-value is 0.03."]))
    assert len(p) == 0
    assert list(p.columns)[-2:] == ["p_comp", "p_value"]


def test_extract_eq() -> None:
    with pytest.raises(TypeError):
        extract_eq(1)

    paper = pc.test_paper(
        [
            "t(10) = 2.23, p = 0.005.",
            "(F(1, 23) = 9.23, p = .023)",
            "peta = 2.3; p > .05, 95% CI = [2, 4]",
            "p-value >= 0.2",
            "2 = p",
        ]
    )
    eq = extract_eq(paper)
    exp = pd.DataFrame(
        {
            "text_id": [1, 1, 2, 2, 3, 3, 3, 4],
            "grp_id": [1.0, 1, 2, 2, 3, 3, 3, 4],
            "lhs": ["t", "p", "F", "p", "peta", "p", "95% CI", "p-value"],
            "df": ["(10)", None, "(1, 23)", None, None, None, None, None],
            "comp": ["=", "=", "=", "=", "=", ">", "=", ">="],
            "rhs": ["2.23", "0.005", "9.23", ".023", "2.3", ".05", "[2, 4]", "0.2"],
        }
    )
    assert list(eq.columns) == ["text_id", "grp_id", "lhs", "df", "comp", "rhs", "paper_id"]
    for col in exp.columns:
        got = [None if pd.isna(v) else v for v in eq[col].tolist()]
        want = [None if pd.isna(v) else v for v in exp[col].tolist()]
        assert got == want, col
    assert set(eq["paper_id"]) == {paper.paper_id}


def test_extract_eq_complex() -> None:
    paper = pc.test_paper(
        "A Mantel-Cox log rank test indicated that the difference between conditions was "
        "significant, Mantel-Cox χ2 (1, N = 60) = 6.99, p = .008, with children waiting "
        "less in the unaware condition (M = 307.10 s, nonparametric bootstrapped 95% CI = "
        "[207.69, 406.51]) than in the unaware/additional rewards condition (M = 529.73 s, "
        "nonparametric bootstrapped 95% CI = [408.56, 650.91])."
    )
    eq = extract_eq(paper)
    assert eq["lhs"].iloc[0] == "χ2"
    assert eq["df"].iloc[0] == "(1, N = 60)"

    # no stats
    eq = extract_eq(pc.test_paper("No stats."))
    assert len(eq) == 0
    assert set(eq.columns) == {"text_id", "grp_id", "lhs", "df", "comp", "rhs", "paper_id"}

    # no df
    eq = extract_eq(pc.test_paper("Stats are t = 2.4."))
    assert eq["df"].isna().tolist() == [True]

    # possessive stats
    eq = extract_eq(pc.test_paper(["What if Cohen's d > 1.0?", "(..., Hedges's g = 0.32, ...)"]))
    assert eq["lhs"].tolist() == ["Cohen's d", "Hedges's g"]


def test_extract_eq_no_numeric_lhs(psychsci) -> None:
    eq = extract_eq(psychsci)
    assert len(eq) > 0
    assert not any(grepl("^[0-9]$", eq["lhs"].tolist()))


def test_extract_eq_grp_id_follows_search_order() -> None:
    # "=" sentences are found first, then "<" ones, so grp_id numbers them in that order
    eq = extract_eq(pc.test_paper(["p < .05", "t = 2.1", "p < .01, t = 3"]))
    assert eq["text_id"].tolist() == [1, 2, 3, 3]
    assert eq["grp_id"].tolist() == [3.0, 1.0, 2.0, 2.0]


def test_extract_eq_paper_list_grp_id_per_paper(psychsci) -> None:
    # U10: R restarts the count at 1 whenever its search order moves to another
    # paper, so different sentences of one paper shared a grp_id
    papers = []
    for pid, texts in [
        ("b", ["a < 2 and b = 3", "c = 4", "d > 5, e = 6", "f \u2264 7"]),
        ("B", ["g > 5", "h = 1"]),
        ("a", ["none", "k \u2248 2 and m < 3"]),
    ]:
        p = pc.test_paper(texts)
        p.paper_id = pid
        papers.append(p)
    eq = extract_eq(pc.PaperList(papers))
    b = eq.loc[eq["paper_id"] == "B"]
    assert b["text_id"].tolist() == [1, 2]
    assert b["grp_id"].tolist() == [2.0, 1.0]  # R: [1, 1]
    # a paper's grp_ids are the ones it gets when searched alone
    for p in papers:
        alone = extract_eq(p).reset_index(drop=True)
        mine = eq.loc[eq["paper_id"] == p.paper_id].reset_index(drop=True)
        pd.testing.assert_frame_equal(mine, alone)
    # a real paper list: every (paper, grp_id) is one sentence
    real = extract_eq(psychsci)
    assert (real.groupby(["paper_id", "grp_id"])["text_id"].nunique() == 1).all()
    # a plain list or a dict of papers works like a PaperList
    pd.testing.assert_frame_equal(extract_eq(list(psychsci)), real)
    pd.testing.assert_frame_equal(
        extract_eq(dict(zip(psychsci.names, psychsci, strict=True))), real
    )


def test_extract_eq_missing_paper_id_and_text_id() -> None:
    # U10: R fails on NA comparisons once there are two equations
    one = extract_eq(pd.DataFrame({"text": ["t = 2.1"]}))
    assert one["lhs"].tolist() == ["t"]
    two = extract_eq(pd.DataFrame({"text": ["t = 2.1, p = .04", "F = 3"]}))
    assert two["lhs"].tolist() == ["t", "p", "F"]
    assert two["grp_id"].tolist() == [1.0, 1.0, 2.0]
    na_ids = pd.DataFrame({"text": ["t = 2.1, p = .04", "F = 3"], "text_id": [None, None]})
    assert extract_eq(na_ids)["grp_id"].tolist() == [1.0, 1.0, 2.0]


def test_extractors_accept_strings() -> None:
    # U150: R fails on a character vector
    eq = extract_eq(["t(20) = 2.1, p < .05", "no numbers", "F(1, 2) = 3"])
    assert eq["lhs"].tolist() == ["t", "p", "F"]
    assert eq["df"].fillna("").tolist() == ["(20)", "", "(1, 2)"]
    assert eq["grp_id"].tolist() == [1.0, 1.0, 2.0]
    p = extract_p_values(["p = .03 and p < .001", "none"])
    assert [t.strip() for t in p["text"]] == ["p = .03", "p < .001"]
    assert p["p_value"].tolist() == [0.03, 0.001]
    assert extract_p_values("p = .5")["p_comp"].tolist() == ["="]


def test_detect_live_data(demo) -> None:
    paper = pc.test_paper(
        [
            "Participants were recruited via Prolific.",
            "The model was tested on held-out data.",
            "Mice were housed in standard cages.",
            "Data were collected from an existing database.",
        ]
    )
    live = _detect_live_data(paper)
    assert live["text_id"].tolist() == [1, 3]
    assert isinstance(_detect_live_data(demo), pd.DataFrame)
