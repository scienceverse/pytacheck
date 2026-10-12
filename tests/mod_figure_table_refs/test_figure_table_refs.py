"""Tests for figure_table_refs, a pytacheck module (metacheck has no counterpart)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

import metacheck as pc

MODULE = "figure_table_refs"
FULL = Path(__file__).parents[1] / "bibr12" / "fixtures" / "bibr_12_1_full.json"


def _paper(
    sentences: list[str],
    figures: Sequence[dict[str, object]] = (),
    tables: Sequence[dict[str, object]] = (),
    xrefs: Sequence[dict[str, object]] = (),
) -> pc.Paper:
    paper = pc.test_paper(sentences)
    paper["figure"] = pd.DataFrame(
        {
            "figure_id": pd.array([f["id"] for f in figures], dtype="Int64"),
            "label": pd.array([f.get("label") for f in figures], dtype="string"),
            "text_id": pd.array([f.get("text_id") for f in figures], dtype="Int64"),
            "caption": pd.array([f.get("caption") for f in figures], dtype="string"),
        }
    )
    paper["table"] = pd.DataFrame(
        {
            "table_id": pd.array([t["id"] for t in tables], dtype="Int64"),
            "label": pd.array([t.get("label") for t in tables], dtype="string"),
            "text_id": pd.array([t.get("text_id") for t in tables], dtype="Int64"),
            "caption": pd.array([t.get("caption") for t in tables], dtype="string"),
        }
    )
    paper["xref"] = pd.DataFrame(
        {
            "xref_id": pd.array(range(1, len(xrefs) + 1), dtype="Int64"),
            "target_id": pd.array([x["target_id"] for x in xrefs], dtype="Int64"),
            "xref_type": pd.array([x["type"] for x in xrefs], dtype="string"),
            "text_id": pd.array([x["text_id"] for x in xrefs], dtype="Int64"),
        }
    )
    return paper


def test_listed_with_its_metadata() -> None:
    assert MODULE in pc.module_list()["name"].tolist()
    info = pc.module_info(MODULE)
    assert info.title == "Figure and Table References"
    assert info.requires == ()


def test_bibr_export() -> None:
    out = pc.module_run(pc.read(FULL), MODULE)
    assert out.traffic_light == "green"
    assert out.table["found_by"].tolist() == ["xref", "xref"]
    assert out.summary_table["floats"].tolist() == [2]
    assert out.summary_table["unreferenced"].tolist() == [0]


def test_xref_first_then_label_search() -> None:
    paper = _paper(
        ["As Figure 1 shows, it works.", "Results are in Tables 1 and 2.", "Figure 1. A plot."],
        figures=[{"id": 1, "label": "1", "text_id": 3}, {"id": 2, "label": "2"}],
        tables=[{"id": 1, "label": "1"}, {"id": 2, "label": "2"}],
        xrefs=[{"target_id": 1, "type": "figure", "text_id": 1}],
    )
    out = pc.module_run(paper, MODULE)
    table = out.table
    assert table["kind"].tolist() == ["figure", "figure", "table", "table"]
    assert table["found_by"].tolist()[0] == "xref"
    assert pd.isna(table["found_by"].tolist()[1])
    assert table["found_by"].tolist()[2:] == ["text", "text"]
    assert table["references"].tolist() == [1, 0, 1, 1]
    assert out.traffic_light == "yellow"
    assert out.summary_text == (
        "1 of 4 figures and tables is not referred to in the text: Figure 2."
    )
    assert out.summary_table["unreferenced"].tolist() == [1]


def test_caption_is_not_a_reference() -> None:
    paper = _paper(
        ["We ran a study.", "Figure 1. The design."],
        figures=[{"id": 1, "label": "1", "text_id": 2}],
    )
    out = pc.module_run(paper, MODULE)
    assert out.table["references"].tolist() == [0]
    assert out.traffic_light == "yellow"


def test_label_must_match_whole() -> None:
    paper = _paper(
        ["See Figure 10 and Fig. 2a."],
        figures=[{"id": 1, "label": "1"}, {"id": 2, "label": "2"}],
    )
    assert pc.module_run(paper, MODULE).table["references"].tolist() == [0, 1]


def test_without_labels_or_links_cannot_be_checked() -> None:
    paper = _paper(["See Figure 1."], figures=[{"id": 1}])
    out = pc.module_run(paper, MODULE)
    assert out.traffic_light == "na"
    assert out.table["references"].isna().all()
    assert out.summary_table["unreferenced"].tolist() == [0]


def test_duplicate_float_is_listed_once() -> None:
    paper = _paper(
        ["See Figure 1."],
        figures=[{"id": 1, "label": "1"}, {"id": 2, "label": "1"}],
    )
    out = pc.module_run(paper, MODULE)
    assert out.table["label"].tolist() == ["1"]
    assert out.traffic_light == "green"


def test_no_floats() -> None:
    out = pc.module_run(pc.test_paper(["Nothing."]), MODULE)
    assert out.traffic_light == "na"
    assert len(out.table) == 0
    assert out.summary_table["floats"].tolist() == [0]


def test_paper_list() -> None:
    a = _paper(["See Figure 1."], figures=[{"id": 1, "label": "1"}])
    b = _paper(["No reference."], figures=[{"id": 1, "label": "1"}])
    out = pc.module_run(pc.PaperList([a, b]), MODULE)
    assert out.traffic_light == "info"
    assert out.summary_table["unreferenced"].tolist() == [0, 1]
    assert out.summary_text == (
        "1 of 2 papers have a figure or table that is not referred to in the text."
    )
