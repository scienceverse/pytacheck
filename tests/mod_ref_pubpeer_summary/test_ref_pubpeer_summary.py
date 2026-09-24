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
    # duplicated DOIs join many-to-many (3 references x 3 lookups)
    assert (out.table["doi"] == "10.9999/pp.dup").sum() == 9
    # the upper-case DOI keeps its case; zero and Statcheck-only comments are dropped
    assert "10.9999/PP.Upper" in out.table["doi"].tolist()
    assert not out.table["doi"].isin(["10.9999/pp.zero", "10.9999/pp.stat"]).any()
    assert out.summary_table["pubpeer_comments"].tolist() == [10.0, 15.0, 3.0]
    assert out.summary_text == "You cited 12 references with comments in PubPeer."
    # the reference without a PubPeer URL is left out of the report table
    report_table = out.report[1].data
    assert len(report_table) == 11
    assert report_table["PubPeer Link"].notna().all()


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
    # pubpeer_comments() returns NULL and dplyr::inner_join(bib, NULL) fails
    with pytest.raises(ModuleError, match="ref_pubpeer"):
        module_run(H.pp_fail(), "ref_pubpeer")


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
    # FLoRA has two replications of 10.1002/bdm.586: left_join() repeats the reference
    out = H.chain(
        H.pp_mixed(),
        ["ref_accuracy", "ref_pubpeer", "ref_replication", "ref_retraction", "ref_summary"],
    )
    assert out.table["bib_id"].tolist() == [0, 0, 1, 2, 3, 4, 5]
    # ref_accuracy failed (no bib_match), so there is no accuracy column
    assert "accuracy_mismatch" not in out.table.columns
    assert out.summary_text == "Summary information provided for 7 references"


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
    # bib 1 (all FALSE) and q/0 (all FALSE, even with no_match) drop out
    assert out["bib_id"].tolist() == [0, 2]
    assert out["accuracy_mismatch"].tolist() == ["doi, title", "no match"]


def test_accuracy_mismatches_requires_no_match() -> None:
    acc = pd.DataFrame({"paper_id": ["p"], "bib_id": [0], "doi_mismatch": [True]})
    with pytest.raises(ValueError):
        _accuracy_mismatches(acc)
