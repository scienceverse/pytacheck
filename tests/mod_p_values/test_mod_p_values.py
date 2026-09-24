"""Ports of metacheck's testthat tests for all_p_values, stat_p_exact and stat_p_nonsig.

Sources: tests/testthat/test-module-stat.R, test-module-.R (chaining), test-report.R
(module_report / report_module_run / report_qmd) and test-app-report.R.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleOutput, module_info, module_run

MODULES = Path(__file__).resolve().parent / "modules"


def _module_available(name: str) -> bool:
    from pytacheck.module import module_find

    try:
        module_find(name)
    except Exception:
        return False
    return True


# ------------------------------------------------------------ test-module-stat.R


def test_stat_p_exact(psychsci) -> None:
    paper = pc.test_paper("The p = .0123")
    module = "stat_p_exact"
    mod_output = module_run(paper, module)
    assert mod_output.traffic_light == "green"
    assert len(mod_output.table) == 1

    # add imprecise p-values
    paper = pc.test_paper(
        [
            "Bad p-value example (p < .05)",
            "Bad p-value example (p<.05)",
            "Bad p-value example (p < 0.05)",
            "Bad p-value example; p < .05",
            "Bad p-value example (p < .005)",
            "Bad p-value example (p > 0.05)",
            "Bad p-value example (p > .1)",
            "Bad p-value example (p = n.s.)",
            "Bad p-value example; p=ns",
            "Bad p-value example; p ≤ .001",
            "OK p-value example; p < .001",
            "OK p-value example; p < .0005",
        ]
    )
    mod_output = module_run(paper, module)
    assert mod_output.traffic_light == "red"
    assert len(mod_output.table) == 12
    assert int(mod_output.table["imprecise"].sum()) == 10

    # zero p-values
    paper = pc.test_paper(
        [
            "Significant result (p = .000)",
            "Significant result (p = 0.000)",
            "Significant result (p = 0.00)",
        ]
    )
    mod_output = module_run(paper, module)
    assert mod_output.traffic_light == "red"
    assert len(mod_output.table) == 3
    assert bool(mod_output.table["zero"].all())
    assert not bool(mod_output.table["imprecise"].any())
    assert mod_output.summary_table["n_zero"].tolist() == [3]

    # imprecise and zero together
    paper = pc.test_paper(
        ["Imprecise p-value example (p < .05)", "Zero p-value example (p = .000)"]
    )
    mod_output = module_run(paper, module)
    assert mod_output.traffic_light == "red"
    assert len(mod_output.table) == 2
    assert int(mod_output.table["imprecise"].sum()) == 1
    assert int(mod_output.table["zero"].sum()) == 1

    # iteration (the fixture subset of psychsci)
    mod_output = module_run(psychsci, module)
    assert mod_output.table["p_comp"].iloc[0] == "<"
    assert mod_output.table["p_value"].iloc[0] == 0.001
    lt05 = sum("p < .05" in t for t in mod_output.table["text"])
    assert lt05 == 1


def test_stat_p_nonsig() -> None:
    paper = pc.test_paper(["Significant; p ≤ .01", "Nonsignificant; p = .20"])
    mod_output = module_run(paper, "stat_p_nonsig")
    assert mod_output.table["text"].tolist() == ["p = .20"]


# ----------------------------------------------------- extra branch coverage


def test_all_p_values_traffic_lights(demo) -> None:
    out = module_run(demo, "all_p_values")
    assert out.traffic_light == "info"
    assert out.summary_text == "We found 3 p-values"
    assert out.summary_table["p_values"].tolist() == [3]
    assert out.table["text"].tolist() == ["p = 0.005", "p =0.152", "p > .05"]

    out = module_run(pc.test_paper("One p = .01 here."), "all_p_values")
    assert out.summary_text == "We found 1 p-value"

    out = module_run(pc.test_paper(["Nothing.", "the p-value is 0.03"]), "all_p_values")
    assert out.traffic_light == "na"
    assert out.summary_text == "We found 0 p-values"
    assert len(out.table) == 0
    assert out.summary_table["p_values"].tolist() == [0]
    assert out.report[0] == ""


def test_stat_p_exact_texts_and_summary() -> None:
    out = module_run(pc.test_paper(["Nothing.", "Or here."]), "stat_p_exact")
    assert out.traffic_light == "na"
    assert out.summary_text == out.report == "We detected no *p* values."
    assert out.summary_table.columns.tolist() == ["paper_id", "n_imprecise", "n_zero"]
    assert out.summary_table.iloc[0, 1:].tolist() == [0, 0]

    out = module_run(pc.test_paper("The p = .0123"), "stat_p_exact")
    assert out.summary_text == (
        "We found no imprecise *p* values or *p*-values of exactly zero out of 1 detected."
    )

    # the same p-value text in identical sentences is reported once (unique());
    # (within one sentence the matches differ: "p < .05 " keeps trailing space)
    paper = pc.test_paper(["Dup p < .05.", "Dup p < .05.", "Zero p = .000.", "Zero p = .000."])
    out = module_run(paper, "stat_p_exact")
    assert out.summary_text == (
        "We found 1 imprecise *p* value and 1 *p* value reported as exactly zero "
        "out of 4 detected *p* values."
    )
    assert out.summary_table.iloc[0, 1:].tolist() == [2, 2]
    assert next(b for b in out.report if isinstance(b, str)).startswith("Reporting *p* values")

    # table and figure notes like "* p < .05" are not flagged
    paper = pc.test_paper(["Note. * p < .05, ** p < .01.", "Real: p < .05."])
    out = module_run(paper, "stat_p_exact")
    assert out.table["imprecise"].tolist() == [False, False, True]


def test_stat_p_exact_paperlist_summary() -> None:
    papers = []
    for text, pid in [
        (["Result p < .05.", "Another p = .031."], "b"),
        (["Zero p = .000."], "B"),
        (["Both p > .1 and p = 0.00."], "a"),
        (["No stats here."], "c"),
    ]:
        p = pc.test_paper(text)
        p.paper_id = pid
        papers.append(p)
    out = module_run(pc.PaperList(papers), "stat_p_exact")
    st = out.summary_table
    assert st["paper_id"].tolist() == ["b", "B", "a", "c"]
    assert st["n_imprecise"].tolist() == [1, 0, 1, 0]
    assert st["n_zero"].tolist() == [0, 1, 1, 0]
    assert out.traffic_light == "red"


def test_stat_p_nonsig_branches() -> None:
    out = module_run(pc.test_paper(["Nothing."]), "stat_p_nonsig")
    assert out.traffic_light == "green"
    assert out.summary_text == out.report == "We detected no nonsignificant p values."

    paper = pc.test_paper(["p = .05, p <= .02, p =< .04 and p ≤ .01"])
    out = module_run(paper, "stat_p_nonsig")
    assert out.traffic_light == "green"
    assert len(out.table) == 0

    paper = pc.test_paper(["No difference, p > .05.", "Not significant (p = n.s.)."])
    out = module_run(paper, "stat_p_nonsig")
    assert out.traffic_light == "yellow"
    assert out.summary_text == (
        "We found 2 non-significant p values that should be checked for appropriate interpretation."
    )
    assert out.table["significance"].tolist() == ["nonsignificant"] * 2
    assert out.table["expanded"].tolist() == [
        "No difference, p > .05.",
        "Not significant (p = n.s.).",
    ]
    assert out.summary_table["n_nonsignificant"].tolist() == [2]


@pytest.mark.parametrize("name", ["all_p_values", "stat_p_exact", "stat_p_nonsig"])
def test_modules_do_not_mutate(name: str, demo) -> None:
    from pytacheck.text.extract import extract_p_values

    before = demo.text.copy()
    p = extract_p_values(demo)
    module_run(demo, name)
    pd.testing.assert_frame_equal(demo.text, before)
    pd.testing.assert_frame_equal(extract_p_values(demo), p)


def test_module_metadata() -> None:
    for name, title in [
        ("all_p_values", "List All P-Values"),
        ("stat_p_exact", "Exact P-Values"),
        ("stat_p_nonsig", "Non-Significant P Value Check"),
    ]:
        info = module_info(name)
        assert info.title == title
        assert list(info.keywords) == ["results"]
        assert list(info.requires) == []
    assert "\\\\bp(-| )?values?" in module_info("all_p_values").details
    assert "<validation>" in module_info("stat_p_exact").details
    assert "<validation>" in module_info("stat_p_nonsig").details


# ------------------------------------------------------------- test-module-.R


def test_chaining_modules_one_paper(demo) -> None:
    chained = str(MODULES / "chained.py")

    # chained run
    a = module_run(demo, "all_p_values")
    b = module_run(a, chained)
    pd.testing.assert_frame_equal(
        b.table.reset_index(drop=True), a.table.iloc[0:2, 0:2].reset_index(drop=True)
    )

    # run without chaining
    c = module_run(demo, chained)
    assert c.table["a"].tolist() == ["not from prev"]
    assert c.table.columns.tolist() == ["a"]


def test_chaining_modules_paperlist(psychsci) -> None:
    if not _module_available("all_urls"):
        pytest.skip("all_urls is not ported yet")
    paper = psychsci
    p = module_run(paper, "all_p_values")
    url = module_run(paper, "all_urls")
    noerrors = str(MODULES / "no_error.py")

    x = module_run(module_run(module_run(paper, "all_p_values"), "all_urls"), noerrors)

    assert x.summary_table.columns.tolist() == [
        "paper_id",
        "p_values",
        "urls",
        "p_values.no_error",
    ]
    assert x.summary_table["p_values"].tolist() == p.summary_table["p_values"].tolist()
    assert x.summary_table["urls"].tolist() == url.summary_table["urls"].tolist()


def test_chaining_p_value_modules(psychsci) -> None:
    x = module_run(module_run(psychsci, "all_p_values"), "stat_p_exact")
    x = module_run(x, "stat_p_nonsig")
    assert x.summary_table.columns.tolist() == [
        "paper_id",
        "p_values",
        "n_imprecise",
        "n_zero",
        "n_nonsignificant",
    ]
    assert x.summary_table["p_values"].tolist() == [6, 39, 13]
    assert x.summary_table["n_imprecise"].tolist() == [0, 0, 2]
    assert x.summary_table["n_nonsignificant"].tolist() == [0, 17, 0]
    assert isinstance(x.prev_outputs["all_p_values"], ModuleOutput)


# --------------------------------------------------------------- test-report.R


def test_module_report(psychsci) -> None:
    from pytacheck.report.report import module_report

    module_output = module_run(psychsci[2], "stat_p_exact")

    report = module_report(module_output)
    assert re.match(r"^### \S* Exact P-Values", report)

    report = module_report(module_output, header=4)
    assert re.match(r"^#### \S* Exact P-Values", report)

    report = module_report(module_output, header="Custom header")
    assert report.startswith("Custom header")

    # print.metacheck_module_output
    assert repr(module_output).startswith("Exact P-Values")


def test_report_module_run(demo) -> None:
    from pytacheck.report.report import report_module_run

    modules = ["all_p_values"]
    mo = report_module_run(demo, modules)
    assert list(mo) == modules

    modules = ["stat_p_nonsig", "stat_p_exact"]
    mo = report_module_run(demo, modules)
    assert set(mo) == set(modules)


def test_report_qmd(demo) -> None:
    from pytacheck.report.report import report_module_run, report_qmd

    modules = "stat_p_nonsig"
    mo = report_module_run(demo, modules)
    report_text = report_qmd(mo, demo)
    assert "MetaCheck Report" in report_text
    assert demo.info["title"].iloc[0] in report_text
    assert module_info(modules).title in report_text


# ------------------------------------------------------ review regressions
# Values below were produced by R (metacheck at the pinned commit); see also
# parity/cases/mod_p_values_review.yaml.


def test_stat_p_exact_star_note_information_separators() -> None:
    # TRE's \s is glibc iswspace(): U+001C-U+001F are *not* space in R (they
    # are in Python's str.isspace()), so "*\x1cp < .05" is no star note and
    # text_search() keeps the character instead of collapsing it to " ".
    paper = pc.test_paper(["Separator p < .05 (*\x1cp < .05).", "Thin p < .05 (* p < .05)."])
    out = module_run(paper, "stat_p_exact")
    assert out.table["imprecise"].tolist() == [True, True, False, False]
    assert ["\x1c" in s for s in out.table["expanded"]] == [True, True, False, False]
    assert out.summary_text == "We found 2 imprecise *p* values out of 4 detected *p* values."


def test_stat_p_exact_comparator_quirks() -> None:
    texts = [
        "Double equals zero p == .000 here.",
        "Tilde zero p ~ .000 here.",
        "Less than zero p < 0.0 here.",
        "Underflow p = 1.0e-400 here.",
        "Sci zero p = 0.0 x 10^-3 here.",
        "Boundary p < .001 and p < .0011 here.",
        "Weird p < n.s. here.",
        "Tiny p < 1.0 x 10^-400 here.",
    ]
    out = module_run(pc.test_paper(texts), "stat_p_exact")
    t = out.table
    assert t["p_comp"].tolist() == ["==", "~", "<", "=", "=", "<", "<", "<", "<"]
    assert t["imprecise"].tolist() == [True, True, False, False, False, False, True, True, False]
    assert t["zero"].tolist() == [False, False, False, True, True, False, False, False, False]
    assert out.summary_text == (
        "We found 4 imprecise *p* values and 2 *p* values reported as exactly zero "
        "out of 9 detected *p* values."
    )


def test_stat_p_nonsig_comparator_quirks() -> None:
    # "==" and the "much less than" sign are not in the significant comparators
    paper = pc.test_paper(
        ["Double p == .03 here.", "Much less p ≪ .01 here.", "Fine p =< .05 and p ≤ .05."]
    )
    out = module_run(paper, "stat_p_nonsig")
    assert out.traffic_light == "yellow"
    assert out.table["p_comp"].tolist() == ["==", "≪"]
    assert out.summary_table["n_nonsignificant"].tolist() == [2]


def test_report_tables_match_r() -> None:
    from tests.mod_p_values.report_tables import mp_report_tables

    paper = pc.test_paper(
        [
            "Bad p < .05 and p < .05 again.",
            "Bad p < .05 and p < .05 again.",
            "Zero p = .000\nline two p > .1.",
            "Fine p = .012.",
            "Note * p < .05, p < .01.",
        ]
    )
    imprecise, zero = mp_report_tables(paper, "stat_p_exact")
    assert list(imprecise["table"].columns) == ["P-Value", "Text"]
    assert imprecise["table"]["P-Value"].tolist() == ["p < .05 ", "p > .1"]
    assert imprecise["table"]["Text"].tolist() == [
        "Bad p < .05 and p < .05 again.",
        "Zero p = .000 line two p > .1.",
    ]
    assert zero["table"]["P-Value"].tolist() == ["p = .000<br>"]
    assert imprecise["colwidths"] == zero["colwidths"] == [0.1, 0.9]
    assert imprecise["maxrows"] == zero["maxrows"] == 2

    (nonsig,) = mp_report_tables(paper, "stat_p_nonsig")
    assert list(nonsig["table"].columns) == ["Text", "Sentence"]
    assert nonsig["colwidths"] == [0.1, 0.9]
    assert nonsig["maxrows"] == 10

    (allp,) = mp_report_tables(paper, "all_p_values")
    assert list(allp["table"].columns) == ["Text", "Sentence"]
    assert allp["colwidths"] == ["5em", None]
    assert len(allp["table"]) == 9
