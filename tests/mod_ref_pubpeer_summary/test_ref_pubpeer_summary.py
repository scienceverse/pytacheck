"""Tests for the ref_pubpeer and ref_summary modules.

Ports the ``ref_pubpeer`` and ``ref_summary`` blocks of metacheck's
``tests/testthat/test-module-ref.R`` and adds checks of the branches the
parity cases (``parity/cases/mod_ref_pubpeer_summary.yaml``) compare with R.
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError, module_list, module_run
from pytacheck.modules.ref_summary import _accuracy_mismatches, _join
from pytacheck.report.blocks import ReportTable
from tests.mod_ref_pubpeer_summary import helpers as H

# -- registration -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ref_pubpeer", "ref_summary"])
def test_module_listed(name: str) -> None:
    assert name in module_list()["name"].tolist()
    info = pc.module_info(name)
    assert info.keywords == ("reference",)
    assert info.section == "reference"


def test_requires() -> None:
    assert pc.module_info("ref_pubpeer").requires == ("network",)
    assert pc.module_info("ref_summary").requires == ()


# -- ref_pubpeer (test-module-ref.R: "ref_pubpeer") ------------------------------------


@pytest.mark.usefixtures("apis")
def test_ref_pubpeer_no_references() -> None:
    out = module_run(H.demo_no_refs(), "ref_pubpeer")
    assert out.traffic_light == "na"
    assert out.table is None
    assert out.summary_text == "We found no references with DOIs"
    assert out.report == out.summary_text
    assert out.summary_table["paper_id"].tolist() == ["to_err_is_human"]


@pytest.mark.usefixtures("apis")
def test_ref_pubpeer_no_bib_match() -> None:
    out = module_run(H.demo_no_match(), "ref_pubpeer")
    assert out.traffic_light == "info"
    assert out.table["total_comments"].sum() == 3


@pytest.mark.usefixtures("apis")
def test_ref_pubpeer_no_dois() -> None:
    out = module_run(H.demo_no_dois(), "ref_pubpeer")
    assert out.traffic_light == "na"
    assert out.table is None


@pytest.mark.usefixtures("apis")
def test_ref_pubpeer_relevant_references(demo: pc.Paper) -> None:
    out = module_run(demo, "ref_pubpeer")
    assert out.traffic_light == "info"
    assert out.table["total_comments"].sum() == 3
    assert list(out.table.columns) == [
        "paper_id",
        "bib_id",
        "doi",
        "text",
        "total_comments",
        "url",
        "users",
    ]
    # the Statcheck-only reference (10.1037/0003-066x.54.6.408) is dropped
    assert out.table["doi"].tolist() == ["10.1177/0956797614520714"]
    assert out.summary_text == "You cited 1 reference with comments in PubPeer."
    assert out.summary_table["pubpeer_comments"].tolist() == [3.0]
    assert out.extras["na_replace"] == 0
    text, table = out.report
    assert text.startswith("We checked 5 references with DOIs. You cited 1 reference")
    assert isinstance(table, ReportTable)
    assert list(table.data.columns) == ["Reference", "Comments", "PubPeer Link"]
    assert table.maxrows == 10
    assert table.data["PubPeer Link"].iloc[0] == (
        "<a href='https://pubpeer.com/publications/3FA648ECECB88454C91804F09E2E56' "
        "target='_blank'>link</a>"
    )


@pytest.mark.usefixtures("mock_pubpeer")
def test_ref_pubpeer_multiple_papers() -> None:
    # testthat uses psychsci[c(4, 9)] (not bundled); a synthetic paper list stands in
    papers = H.pp_list()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # dplyr does not warn inside a module
        out = module_run(papers, "ref_pubpeer")
    assert out.traffic_light == "info"
    assert len(out.table) >= 2
    assert out.summary_table["paper_id"].tolist() == papers.names
    # U118: a duplicated DOI joins once per reference (metacheck: 3 references x 3
    # lookups = 9 rows), and each work counts once per paper
    assert (out.table["doi"] == "10.9999/pp.dup").sum() == 3
    # the upper-case DOI keeps its case; zero and Statcheck-only comments are dropped
    assert "10.9999/PP.Upper" in out.table["doi"].tolist()
    assert not out.table["doi"].isin(["10.9999/pp.zero", "10.9999/pp.stat"]).any()
    assert out.summary_table["pubpeer_comments"].tolist() == [5.0, 15.0, 1.0]
    assert out.summary_text == "You cited 5 references with comments in PubPeer."
    # U118: the reference without a PubPeer URL is listed too, without a link
    report_table = out.report[1].data
    assert len(report_table) == 6
    assert report_table["PubPeer Link"].tolist()[3] == ""


@pytest.mark.usefixtures("mock_pubpeer")
def test_ref_pubpeer_statcheck_only() -> None:
    out = module_run(H.pp_statcheck(), "ref_pubpeer")
    assert out.traffic_light == "na"
    assert len(out.table) == 0
    assert out.summary_text == "No references with comments in PubPeer were found."
    assert out.report == (
        "We checked 2 references with DOIs. No references with comments in PubPeer were found."
    )
    # na_replace fills the paper's missing count
    assert out.summary_table["pubpeer_comments"].tolist() == [0]


@pytest.mark.usefixtures("mock_pubpeer")
def test_ref_pubpeer_single_reference() -> None:
    out = module_run(H.pp_single(), "ref_pubpeer")
    assert out.summary_text == "You cited 1 reference with comments in PubPeer."
    assert out.report[0].startswith("We checked 1 reference with DOIs.")


@pytest.mark.usefixtures("mock_pubpeer")
def test_ref_pubpeer_request_failed() -> None:
    # U23: pubpeer_comments() returns NULL and metacheck's inner_join(bib, NULL)
    # fails ("`x` and `y` must share the same src."); the failure is reported
    out = module_run(H.pp_fail(), "ref_pubpeer")
    assert out.traffic_light == "fail"
    assert out.summary_text.startswith("PubPeer could not be reached")


@pytest.mark.usefixtures("apis")
def test_ref_pubpeer_does_not_mutate(demo: pc.Paper) -> None:
    bib = demo.bib.copy()
    module_run(demo, "ref_pubpeer")
    pd.testing.assert_frame_equal(demo.bib, bib)


# -- ref_summary (test-module-ref.R: "ref_summary") -------------------------------------


def test_ref_summary_relevant_references(demo: pc.Paper) -> None:
    out = module_run(demo, "ref_summary")
    assert out.traffic_light == "info"
    assert len(out.table) == 5
    assert "doi" in out.table.columns
    assert "crossref_doi_mismatch" not in out.table.columns
    assert "retractionwatch" not in out.table.columns
    assert out.summary_text == "Summary information provided for 5 references"
    text, table = out.report
    assert text == "See the specific reports above for details."
    assert list(table.data.columns) == ["text"]
    assert table.colwidths == [0.75]
    assert table.maxrows == 10


def test_ref_summary_chaining(demo: pc.Paper) -> None:
    out = module_run(module_run(demo, "ref_accuracy"), "ref_summary")
    assert "accuracy_mismatch" in out.table.columns
    assert "retractionwatch" not in out.table.columns
    assert out.table["accuracy_mismatch"].tolist() == [
        "no match",
        pd.NA,
        pd.NA,
        "author",
        "no match",
    ]
    # ref_summary has no summary_table: the chain's is kept
    assert list(out.summary_table.columns) == ["paper_id", "refs_checked", "incoherent", "no_doi"]


@pytest.mark.usefixtures("apis")
def test_ref_summary_chain_all(demo: pc.Paper) -> None:
    out = H.chain(
        demo, ["ref_accuracy", "ref_pubpeer", "ref_replication", "ref_retraction", "ref_summary"]
    )
    assert list(out.table.columns) == [
        "paper_id",
        "bib_id",
        "doi",
        "text",
        "accuracy_mismatch",
        "pubpeer",
        "replication_type",
        "retractionwatch",
    ]
    assert out.table["pubpeer"].iloc[2].endswith(">Link</a>")
    assert out.table["replication_type"].iloc[1] == "replication"
    assert out.table["retractionwatch"].iloc[2] == "Retraction"
    table = out.report[1]
    assert list(table.data.columns) == [
        "text",
        "accuracy_mismatch",
        "pubpeer",
        "replication_type",
        "retractionwatch",
    ]
    assert table.colwidths == [0.75, None, None, None, None]


def test_ref_summary_no_references() -> None:
    out = module_run(H.demo_no_refs(), "ref_summary")
    assert out.traffic_light == "na"
    assert out.table is None
    assert out.summary_text == "No references to summarise"


def test_ref_summary_extra_arguments(demo: pc.Paper) -> None:
    # R: ref_summary(paper, ...) ignores extra arguments
    out = module_run(demo, "ref_summary", foo=1)
    assert out.traffic_light == "info"


@pytest.mark.usefixtures("mock_pubpeer")
def test_ref_summary_duplicate_rows() -> None:
    # FLoRA has two replications of 10.1002/bdm.586: metacheck's left_join() repeats
    # the reference (7 rows); U119: one row per reference
    out = H.chain(
        H.pp_mixed(),
        ["ref_accuracy", "ref_pubpeer", "ref_replication", "ref_retraction", "ref_summary"],
    )
    assert out.table["bib_id"].tolist() == [0, 1, 2, 3, 4, 5]
    assert out.table["replication_type"].tolist()[0] == "replication"
    # ref_accuracy failed (no bib_match), so there is no accuracy column
    assert "accuracy_mismatch" not in out.table.columns
    assert out.summary_text == "Summary information provided for 6 references"


def test_ref_summary_does_not_mutate_prev_outputs(demo: pc.Paper) -> None:
    acc = module_run(demo, "ref_accuracy")
    table = acc.table.copy()
    module_run(acc, "ref_summary")
    pd.testing.assert_frame_equal(acc.table, table)


# -- helpers --------------------------------------------------------------------------


def test_join_left_order_na_and_suffixes() -> None:
    x = pd.DataFrame(
        {
            "k": pd.array(["a", None, "b", "c"], dtype="string"),
            "id": pd.array([1, 2, 3, 4], dtype="Int64"),
            "v": [1, 2, 3, 4],
        }
    )
    y = pd.DataFrame(
        {"k": ["b", None, "a", "b"], "id": [3.0, 2.0, 1.0, 3.0], "v": [10, 20, 30, 40]}
    )
    out = _join(x, y, ["k", "id"], "left")
    assert list(out.columns) == ["k", "id", "v.x", "v.y"]
    assert out["v.x"].tolist() == [1, 2, 3, 3, 4]
    assert out["v.y"].tolist() == [30, 20, 10, 40, pd.NA]
    inner = _join(x, y, ["k", "id"], "inner")
    assert inner["v.x"].tolist() == [1, 2, 3, 3]


def test_join_missing_key() -> None:
    x = pd.DataFrame({"a": [1]})
    with pytest.raises(ValueError, match="Join columns in `y` must be present"):
        _join(x, pd.DataFrame({"b": [1]}), ["a"], "left")


def test_join_fills_numpy_dtypes() -> None:
    x = pd.DataFrame({"k": [1, 2]})
    y = pd.DataFrame({"k": [1], "flag": [True], "n": [5], "o": [[1, 2]]})
    out = _join(x, y, ["k"], "left")
    assert str(out["flag"].dtype) == "boolean"
    assert str(out["n"].dtype) == "Int64"
    assert out["o"].tolist() == [[1, 2], None]


def test_accuracy_mismatches() -> None:
    acc = pd.DataFrame(
        {
            "paper_id": pd.array(["p", "p", "p", "q"], dtype="string"),
            "bib_id": pd.array([0, 1, 2, 0], dtype="Int64"),
            "doi_mismatch": pd.array([True, False, None, False], dtype="boolean"),
            "title_mismatch": pd.array([True, False, False, False], dtype="boolean"),
            "no_match": pd.array([False, False, True, True], dtype="boolean"),
            "tier": ["a", "b", "c", "d"],
        }
    )
    out = _accuracy_mismatches(acc)
    assert list(out.columns) == ["paper_id", "bib_id", "accuracy_mismatch"]
    # bib 1 (all FALSE) drops out; U119: q/0 has no match although its
    # *_mismatch are all FALSE (metacheck drops it too)
    assert out["bib_id"].tolist() == [0, 2, 0]
    assert out["paper_id"].tolist() == ["p", "p", "q"]
    assert out["accuracy_mismatch"].tolist() == ["doi, title", "no match", "no match"]


def test_accuracy_mismatches_without_no_match() -> None:
    # U119: metacheck fails with an empty error message
    acc = pd.DataFrame({"paper_id": ["p"], "bib_id": [0], "doi_mismatch": [True]})
    out = _accuracy_mismatches(acc)
    assert out["accuracy_mismatch"].tolist() == ["doi"]


# -- review: dplyr suffixes, pivot_longer() types, %in% ---------------------------------


def test_join_repeats_suffixes_until_unique() -> None:
    # dplyr:::add_suffixes(): x's "a" -> "a.x" clashes with x's own "a.x" -> "a.x.x"
    x = pd.DataFrame({"k": [1, 2], "a": ["x1", "x2"], "a.x": ["ax1", "ax2"]})
    y = pd.DataFrame({"k": [2], "a": ["y"], "a.x": ["yax"]})
    out = _join(x, y, ["k"], "left")
    assert list(out.columns) == ["k", "a.x.x", "a.x.x.x", "a.y", "a.x.y"]
    assert out["a.x.x"].tolist() == ["x1", "x2"]
    assert out["a.x.y"].isna().tolist() == [True, False]
    assert out["a.x.y"].iloc[1] == "yax"
    # a y name that only exists suffixed in x keeps its name
    y2 = pd.DataFrame({"k": [1], "a": ["y"], "b.x": ["b"]})
    x2 = pd.DataFrame({"k": [1], "a": ["x"]})
    assert list(_join(x2, y2, ["k"], "left").columns) == ["k", "a.x", "a.y", "b.x"]


def test_join_empty_sides() -> None:
    x = pd.DataFrame({"k": pd.array([1, None], dtype="Int64"), "v": ["a", "b"]})
    y = pd.DataFrame({"k": pd.array([], dtype="Int64"), "w": pd.array([], dtype="string")})
    left = _join(x, y, ["k"], "left")
    assert left["v"].tolist() == ["a", "b"]
    assert left["w"].isna().all()
    assert len(_join(x, y, ["k"], "inner")) == 0
    assert list(_join(x.iloc[0:0], x, ["k"], "left").columns) == ["k", "v.x", "v.y"]


def test_join_many_to_many_keeps_y_order() -> None:
    x = pd.DataFrame({"k": ["a", "b", "a"], "i": [1, 2, 3]})
    y = pd.DataFrame({"k": ["a", "c", "a", "b"], "j": [10, 20, 30, 40]})
    out = _join(x, y, ["k"], "inner")
    assert out["i"].tolist() == [1, 1, 2, 3, 3]
    assert out["j"].tolist() == [10, 30, 40, 10, 30]


def _acc(**cols: object) -> pd.DataFrame:
    n = len(next(iter(cols.values())))  # type: ignore[arg-type]
    return pd.DataFrame(
        {
            "paper_id": pd.array(["p"] * n, dtype="string"),
            "bib_id": pd.array(range(n), dtype="Int64"),
            **cols,
        }
    )


def test_accuracy_mismatches_pivot_types() -> None:
    # logical and character values cannot be combined by pivot_longer()
    acc = _acc(
        doi_mismatch=pd.array(["TRUE", "FALSE"], dtype="string"),
        year_mismatch=pd.array([True, False], dtype="boolean"),
        no_match=pd.array([False, False], dtype="boolean"),
    )
    with pytest.raises(ValueError, match="Can't combine `doi_mismatch` <character>"):
        _accuracy_mismatches(acc)
    # an all-NA logical column is "unspecified" and combines with anything
    acc["year_mismatch"] = pd.array([None, None], dtype="boolean")
    assert _accuracy_mismatches(acc)["accuracy_mismatch"].tolist() == ["doi, year", "year"]
    # logical, integer and double combine
    acc = _acc(
        doi_mismatch=pd.array([1, 0], dtype="Int64"),
        year_mismatch=[0.5, 0.0],
        title_mismatch=pd.array([False, None], dtype="boolean"),
        no_match=[1.0, 2.0],
    )
    out = _accuracy_mismatches(acc)
    # 1 %in% TRUE is TRUE, 2 %in% TRUE is FALSE
    assert out["accuracy_mismatch"].tolist() == ["no match", "title"]


def test_accuracy_mismatches_in_semantics() -> None:
    # `%in%` coerces FALSE / TRUE to "FALSE" / "TRUE" for character values
    acc = _acc(
        doi_mismatch=pd.array(["FALSE", "false", None, "0"], dtype="string"),
        no_match=pd.array(["TRUE", "FALSE", None, "1"], dtype="string"),
    )
    out = _accuracy_mismatches(acc)
    # U119: bib 0 (no_match "TRUE", doi "FALSE") says "no match"
    assert out["bib_id"].tolist() == [1, 2, 3, 0]
    assert out["accuracy_mismatch"].tolist() == ["doi", "doi", "doi", "no match"]
    # factors compare by their labels
    acc = _acc(
        doi_mismatch=pd.Categorical(["FALSE", "x"]),
        no_match=pd.array([False, True], dtype="boolean"),
    )
    assert _accuracy_mismatches(acc)["accuracy_mismatch"].tolist() == ["no match"]


def test_accuracy_mismatches_group_order() -> None:
    # groups follow their first kept cell; duplicated keys collect every name
    acc = pd.DataFrame(
        {
            "paper_id": pd.array(["p", "p", "p", "p"], dtype="string"),
            "bib_id": pd.array([0, 1, 1, 0], dtype="Int64"),
            "doi_mismatch": pd.array([False, True, False, True], dtype="boolean"),
            "title_mismatch": pd.array([False, False, True, True], dtype="boolean"),
            "no_match": pd.array([False, False, False, None], dtype="boolean"),
        }
    )
    out = _accuracy_mismatches(acc)
    assert out["bib_id"].tolist() == [1, 0]
    assert out["accuracy_mismatch"].tolist() == ["doi, title", "doi, title"]
    # nothing kept: an empty table with R's columns
    acc[["doi_mismatch", "title_mismatch"]] = False
    empty = _accuracy_mismatches(acc)
    assert list(empty.columns) == ["paper_id", "bib_id", "accuracy_mismatch"]
    assert len(empty) == 0


def test_ref_summary_suffix_collisions() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from tests.httpmock import replay

        with replay("apis"):
            out = module_run(H.rv_ret_collide(), "ref_summary")
    assert list(out.table.columns) == [
        "paper_id",
        "bib_id",
        "doi",
        "text",
        "accuracy_mismatch.x",
        "pubpeer.x.x",
        "retractionwatch",
        "pubpeer.y",
        "pubpeer.x",
        "accuracy_mismatch.y",
    ]


def test_ref_summary_mixed_accuracy_types_error() -> None:
    with pytest.raises(ModuleError, match="Can't combine"):
        module_run(H.rv_acc_mixed(), "ref_summary")


def test_ref_pubpeer_edge_feedbacks(mock_pubpeer: None) -> None:
    out = module_run(H.pp_odd(), "ref_pubpeer")
    # empty users kept, null total_comments / ["Statcheck"] dropped, "statcheck" kept
    assert out.table["doi"].tolist() == [
        "10.9999/pp.emptyusers",
        "10.9999/pp.emptyurl",
        "10.9999/pp.statcase",
    ]
    assert out.table["users"].tolist() == ["", "Empty Url", "statcheck"]
    # U118: a reference without a URL is listed without a link (metacheck: an
    # empty report table ""), with or without a url column
    for paper in (H.pp_nourl_some(), H.pp_nourl_only()):
        out = module_run(paper, "ref_pubpeer")
        assert out.report[1].data["PubPeer Link"].tolist() == [""]
        assert out.report[1].data["Comments"].tolist() == [3.0]


def test_ref_pubpeer_case_list_c_locale(mock_pubpeer: None) -> None:
    out = module_run(H.pp_case_list(), "ref_pubpeer")
    # arrange(paper_id, bib_id) in the C locale: "A" < "a" < "b"
    assert out.table["paper_id"].tolist() == ["A", "b", "b"]
    assert out.table["doi"].tolist() == ["10.9999/pp.many", "10.9999/pp.one", "10.9999/pp.dup"]
    assert out.summary_table["paper_id"].tolist() == ["b", "A", "a"]
    assert out.summary_table["pubpeer_comments"].tolist() == [3.0, 12.0, 0.0]
