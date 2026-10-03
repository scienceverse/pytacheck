"""Tests for the open_practices module.

Ports of metacheck's tests/testthat/test-module-open-practices.R, plus checks
of the branches (traffic lights, reports) and of the row prefilter, which
must never change the result of the R search chains.
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.core.errors import PytacheckWarning
from metacheck.module import SECTION_LEVELS, ModuleError

MODULE = "open_practices"


def run(paper: object) -> pc.ModuleOutput:
    return pc.module_run(paper, MODULE)


# test-module-open-practices.R ---------------------------------------------


def test_open_practices_listed() -> None:
    assert MODULE in pc.module_list()["name"].tolist()


def test_essential_components() -> None:
    info = pc.module_info(MODULE)
    assert info.title == "Open Practices Check"
    assert info.description.startswith("This module searches for open data")
    assert info.details.startswith("It is much faster than the previous ODDPub")
    assert info.keywords == ("general",)
    assert info.keywords[0] in SECTION_LEVELS
    assert info.requires == ()


def test_single_paper(demo: pc.Paper) -> None:
    mo = run(demo)
    assert mo.traffic_light == "green"
    assert mo.table["data"].any()
    assert mo.table["code"].any()
    assert not mo.table["materials"].any()
    assert mo.table["prereg"].any()


def test_paperlist() -> None:
    paper = pc.PaperList(
        [
            pc.test_paper(["Open code is available at https://github.com/repo/mine."]),
            pc.test_paper(["Data available upon request."]),
        ]
    )
    mo = run(paper)
    assert len(mo.table) == len(paper)
    assert mo.table["data"].tolist() == [False, True]
    assert mo.table["code"].tolist() == [True, False]
    assert mo.table["on_request"].tolist() == [False, True]

    # on request is flagged, but is not open sharing
    assert mo.summary_table["data_open"].tolist() == [False, False]
    assert mo.summary_table["on_request"].tolist() == [False, True]
    assert mo.traffic_light == "info"

    # unless the same sentence also names a repository
    paper1 = pc.test_paper(["Data are available at https://osf.io/hk4yq/; raw data on request."])
    mo = run(paper1)
    assert mo.summary_table["data_open"].tolist() == [True]
    assert mo.traffic_light == "red"


def test_only_open_data() -> None:
    paper = pc.test_paper(
        [
            "Data for all experiments have been made publicly available on OSF at "
            "https://osf.io/hk4yq/."
        ]
    )
    mo = run(paper)
    assert mo.table["data"].tolist() == [True]
    assert mo.traffic_light == "yellow"
    assert mo.summary_text == "Shared data detected."
    # U83: metacheck's report says "not reconized"
    assert mo.report[1] == (
        "We did not detect open sharing of code, which could be because there is no code "
        "related to this article, or the repository is not recognized by our code. If there "
        "is code, please consider sharing it in a repository."
    )


def test_only_open_code() -> None:
    statements = [
        "The computer code for the analyses reported here can be accessed at the Open "
        "Science Framework (https://osf.io/geq9x/).",
        "All analysis code for this study has been made publicly available via the Open "
        "Science Framework and can be accessed at https://osf.io/geq9x/.",
    ]
    mo = run(pc.test_paper(statements))
    assert mo.table["data"].tolist() == [False, False]
    assert mo.table["code"].tolist() == [True, True]
    assert mo.table["text"].tolist() == statements
    assert mo.summary_table["data_open"].tolist() == [False]
    assert mo.summary_table["code_open"].tolist() == [True]
    assert mo.traffic_light == "yellow"
    assert mo.summary_text == "Shared code detected."


def test_open_data_and_code() -> None:
    paper = pc.test_paper(
        [
            "The data and code to reproduce the findings of this study are available at the "
            "Open Science Framework at https://osf.io/abcde."
        ]
    )
    mo = run(paper)
    assert mo.table["data"].tolist() == [True]
    assert mo.table["code"].tolist() == [True]
    assert mo.summary_table["data_open"].tolist() == [True]
    assert mo.summary_table["code_open"].tolist() == [True]
    assert mo.traffic_light == "green"


def test_no_matches_argument_of_length_zero(psychsci: pc.PaperList) -> None:
    # R uses psychsci$`0956797617714811` (not in the fixtures); this fixture
    # paper also has no open-practices statements.
    paper = psychsci["0956797613520608"]
    mo = run(paper)
    assert len(mo.table) == 0
    assert mo.summary_table["data_open"].tolist() == [False]
    assert mo.summary_table["code_open"].tolist() == [False]
    assert mo.traffic_light == "red"


# branches -------------------------------------------------------------------


def test_no_statements_report() -> None:
    mo = run(pc.test_paper(["Nothing here.", "Or here."]))
    assert mo.traffic_light == "red"
    assert mo.summary_text == "Neither shared data nor code detected."
    # R's report is NULL, so module_run() falls back to the summary text
    assert mo.report == mo.summary_text
    assert mo.na_replace == {"data_open": False, "code_open": False}
    st = mo.summary_table
    assert st.columns.tolist() == [
        "paper_id",
        "data_open",
        "code_open",
        "materials_open",
        "prereg_open",
        "on_request",
        "data_statements",
        "code_statements",
        "materials_statements",
        "prereg_statements",
    ]
    assert st["data_open"].tolist() == [False]
    assert st["materials_open"].isna().all()


def test_empty_paper() -> None:
    mo = run(pc.paper())
    assert len(mo.table) == 0
    assert mo.table.columns[-5:].tolist() == ["data", "code", "materials", "prereg", "on_request"]
    assert mo.traffic_light == "red"


def test_on_request_report() -> None:
    texts = [
        "The analysis code is available on GitHub at https://github.com/me/code.",
        "The data are available from the corresponding author on reasonable request.",
        "Materials can be shared by request.",
    ]
    mo = run(pc.test_paper(texts))
    assert mo.traffic_light == "red"
    assert mo.summary_text == "Shared code detected; some sharing is only on request."
    # U107: one paragraph quotes every on-request sentence (metacheck: one each)
    assert len(mo.report) == 3
    assert mo.report[0].startswith("We did not detect open sharing of data")
    assert mo.report[1] == (
        "Code was openly shared for this article, based on the following text:\n\n> " + texts[0]
    )
    assert mo.report[2].endswith("> " + texts[1] + "\n\n> " + texts[2])


def test_on_request_ignores_case() -> None:
    # U107: "ON REQUEST" passes the (case-insensitive) repository search, so it is
    # flagged too (metacheck's case-sensitive grepl() missed it)
    mo = run(pc.test_paper(["Analysis scripts are available ON REQUEST."]))
    assert mo.table["on_request"].tolist() == [True]
    assert mo.traffic_light == "red"
    assert (
        mo.summary_text == "Neither shared data nor code detected; some sharing is only on request."
    )


def test_lowercase_r_is_not_a_code_word() -> None:
    # U107: metacheck searched \bR\b ignoring case, so "r(76) = .10" or "osf.io/r" made
    # a code statement
    texts = [
        "Scores were correlated, r(76) = .10 (see https://osf.io/abc).",
        "The R2 values are available at https://osf.io/r.",
        "R scripts are available at https://osf.io/xyz.",
    ]
    mo = run(pc.test_paper(texts))
    assert mo.summary_table["code_statements"].iloc[0] == [texts[2]]


def test_statements_columns(demo: pc.Paper) -> None:
    st = run(demo).summary_table
    assert st["materials_statements"].tolist() == [None]
    data = st["data_statements"].iloc[0]
    assert isinstance(data, list)
    assert len(data) == len(set(data)) == 3
    assert st["prereg_open"].tolist() == [True]


def test_paperlist_single_summary_row() -> None:
    # U106: only one paper has statements, but a list gets the paper-list summary
    # (metacheck: the single-paper branch, "Shared data detected; ...", red)
    paper = pc.PaperList(
        [
            pc.test_paper(["Nothing to see."]),
            pc.test_paper(
                ["Data are available at https://osf.io/abc.", "Data are also available on request."]
            ),
        ]
    )
    mo = run(paper)
    assert mo.traffic_light == "info"
    assert mo.summary_text == (
        "0 papers shared both data and code, 1 only data, 0 only code, and 1 neither."
    )
    assert mo.report == mo.summary_text
    assert mo.summary_table["data_open"].tolist() == [False, True]
    assert mo.summary_table["on_request"].isna().tolist() == [True, False]


def test_paperlist_summary_text(psychsci: pc.PaperList, demo: pc.Paper) -> None:
    papers = pc.PaperList([*psychsci, demo])
    mo = run(papers)
    assert mo.traffic_light == "info"
    # U106: the two papers without a flagged sentence count as "neither" (metacheck: 0)
    assert mo.summary_text == (
        "1 paper shared both data and code, 1 only data, 0 only code, and 2 neither."
    )
    assert mo.report == mo.summary_text
    # table rows follow the paper order, then text_id
    order = {pid: i for i, pid in enumerate(papers.names)}
    keys = list(zip(mo.table["paper_id"].map(order), mo.table["text_id"], strict=True))
    assert keys == sorted(keys)


def test_duplicate_paper_ids(demo: pc.Paper) -> None:
    # U101: metacheck stops with "factor level [2] is duplicated"; U208: the second paper
    # is renamed, so the list is two papers
    with pytest.warns(PytacheckWarning, match="'to_err_is_human' as 'to_err_is_human~2'"):
        mo = run(pc.PaperList([demo, demo]))
    assert mo.summary_table["paper_id"].tolist() == ["to_err_is_human", "to_err_is_human~2"]
    assert (
        mo.summary_text
        == "2 papers shared both data and code, 0 only data, 0 only code, and 0 neither."
    )


def test_empty_paper_list() -> None:
    # U79: metacheck's summarise() fails on `text[data]` (its searches of an empty
    # list have no text column); an empty list has no statements
    mo = run(pc.PaperList([]))
    assert mo.traffic_light == "red"
    assert mo.summary_text == "Neither shared data nor code detected."
    assert len(mo.table) == 0
    assert len(mo.summary_table) == 0


def test_text_table_without_text_column_errors_like_r() -> None:
    # text_search() searches the first column and drops `text`: with matching rows
    # R's tibble refuses the 0-length on_request (R 4.5.3 / metacheck), without
    # them summarise() fails on `text[data]` as for an empty paper list
    prefix = "Running the module 'open_practices' produced errors: "
    p = pc.test_paper(["hello"])
    t = p.text.assign(note="data are available on the osf")
    p.text = t[["note"] + [c for c in t.columns if c not in ("note", "text")]]
    with pytest.raises(ModuleError) as err:
        run(p)
    assert str(err.value) == (
        prefix
        + "Assigned data `grepl(on_request, table$text)` must be compatible with existing data."
    )
    p = pc.test_paper(["data are available on the osf"])
    p.text = p.text.drop(columns="text")
    with pytest.raises(ModuleError) as err:
        run(p)
    assert str(err.value) == prefix + "In argument: `data_statements = list(unique(text[data]))`."


def test_does_not_mutate_paper(demo: pc.Paper) -> None:
    before = demo.text.copy()
    run(demo)
    pd.testing.assert_frame_equal(demo.text, before)


# review: paper order and chained summary tables -----------------------------


def _paper(texts: list[str], pid: str) -> pc.Paper:
    p = pc.test_paper(texts)
    p.paper_id = pid
    return p


def test_rows_follow_paper_list_order_not_sorted_ids() -> None:
    # R: factor(paper_id, paper_id(paper)) -> the paper list's order, not C/ICU sort order
    papers = pc.PaperList(
        [
            _paper(["The analysis code is available on GitHub (https://github.com/a/b)."], "zeta"),
            _paper(["Nothing to see."], "Alpha"),
            _paper(["Data are archived at https://zenodo.org/1."], "beta"),
            _paper(["Scripts are available at https://github.com/s/t, data on request."], "Beta"),
        ]
    )
    mo = run(papers)
    assert mo.table["paper_id"].tolist() == ["zeta", "beta", "Beta"]
    assert mo.summary_table["paper_id"].tolist() == ["zeta", "Alpha", "beta", "Beta"]
    assert mo.summary_text == (
        "1 paper shared both data and code, 1 only data, 1 only code, and 1 neither."
    )


def test_chained_na_replace_leaves_open_practices_columns() -> None:
    # U77: all_urls' unnamed na_replace (0) applies only to its own summary column;
    # metacheck applied it to every column of the chain, putting the number 0 in
    # open_practices' list columns and turning its logical NA columns numeric
    papers = pc.PaperList(
        [
            _paper(["The data and code are available at https://osf.io/abcde."], "p1"),
            _paper(["Nothing to see here."], "p2"),
            _paper(["Materials are available on request.", "See www.example.com."], "p3"),
        ]
    )
    mo = pc.module_run(run(papers), "all_urls")
    st = mo.summary_table
    data = st["data_statements"].tolist()
    assert data[0] == ["The data and code are available at https://osf.io/abcde."]
    assert pd.isna(data[1]) and pd.isna(data[2])
    assert st["materials_statements"].tolist()[2] == ["Materials are available on request."]
    assert pd.isna(st["prereg_statements"].tolist()[0])
    # open_practices' own na_replace: data_open and code_open are FALSE for p2
    assert st["data_open"].tolist() == [True, False, False]
    assert st["materials_open"].tolist()[::2] == [False, False]
    assert pd.isna(st["materials_open"].tolist()[1])
    assert st["on_request"].tolist()[::2] == [False, True]
    assert st["urls"].tolist() == [1, 0, 1]


def test_list_column_in_the_text_table() -> None:
    # papers from older bibr exports have a list column (_bbox_2d) in the text table;
    # it is part of the join key, and the module used to stop on the unhashable list
    texts = [
        "The data are available at https://osf.io/abc.",
        "Code is on GitHub: github.com/a/b.",
        "Data and code are available at https://osf.io/abc.",
    ]
    plain = run(pc.test_paper(texts))
    paper = pc.test_paper(texts)
    paper["text"]["_bbox_2d"] = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]
    out = run(paper)
    assert out.traffic_light == plain.traffic_light == "green"
    assert out.summary_text == plain.summary_text
    assert len(out.table) == len(plain.table)
