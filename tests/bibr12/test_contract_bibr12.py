"""The bibr export schema 12.x contract: other schemas, new enum values, 12.1 exports.

The two fixtures are bibr's own schema conformance examples: a 12.1 export
(with the ``extraction.fields`` block 12.1 adds) and a 12.3 export with a
section type, a key and a top-level block that 12.x does not list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import pandas as pd
import pytest

import metacheck as pc
from metacheck.io import bibr12

FX = Path(__file__).resolve().parent / "fixtures"
V12_1 = FX / "bibr_12_1_full.json"
V12_3 = FX / "bibr_12_3_new_section_type.json"


@pytest.fixture
def log_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The entries written to a fresh log; each new value is new again."""
    path = tmp_path / "log.jsonl"
    monkeypatch.setenv("PYTACHECK_LOG", str(path))
    monkeypatch.setattr(bibr12, "_LOGGED_VALUES", set())

    def entries() -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [orjson.loads(line) for line in path.read_bytes().splitlines() if line.strip()]

    return entries


def with_changes(source: Path, tmp_path: Path, **changes: Any) -> Path:
    data = orjson.loads(source.read_bytes())
    data.update(changes)
    out = tmp_path / "changed.json"
    out.write_bytes(orjson.dumps(data))
    return out


# -- a schema other than 12.x -----------------------------------------------------------


@pytest.mark.parametrize("version", ["11.0", "11.1", "10.3"])
def test_an_older_schema_says_what_to_do(tmp_path: Path, version: str) -> None:
    path = with_changes(V12_1, tmp_path, schema_version=version)
    for read in (lambda: pc.read(path), lambda: pc.from_bibr(orjson.loads(path.read_bytes()))):
        with pytest.raises(ValueError) as err:
            read()
        message = str(err.value)
        assert f"bibr export schema {version} is not supported" in message
        assert "reads schema 12.x" in message
        assert message.endswith(
            "Extract the paper again with a bibr version that writes schema 12.x."
        )


def test_a_newer_schema_says_to_update(tmp_path: Path) -> None:
    path = with_changes(V12_1, tmp_path, schema_version="13.0")
    with pytest.raises(ValueError, match=r"schema 13\.0 is not supported.*Update pytacheck"):
        pc.read(path)


# -- 12.1 and later ---------------------------------------------------------------------


def test_a_bibr_12_1_export_reads(log_entries) -> None:
    paper = pc.read(V12_1)
    assert pc.paper_validate(paper)
    assert paper.info["schema_version"].tolist() == ["12.1"]
    assert bibr12.is_bibr12(paper)
    assert set(paper.extraction["fields"]) >= {"title", "author", "doi"}
    assert paper.extraction["fields"]["title"]["state"] == "extracted"
    assert len(paper.text) > 0
    assert log_entries() == []


def test_a_new_enum_value_is_read_and_logged_once(log_entries) -> None:
    paper = pc.read(V12_3)
    assert paper.info["schema_version"].tolist() == ["12.3"]
    assert paper.section["section_type"].tolist() == ["preregistration"]
    entries = log_entries()
    assert len(entries) == 1
    assert entries[0]["label"] == "bibr12_new_value"
    assert entries[0]["schema_version"] == "12.3"
    assert entries[0]["file"] == V12_3.name
    assert entries[0]["values"] == {"section.section_type": ["preregistration"]}

    # the same value again is not logged again
    pc.read(V12_3)
    assert len(log_entries()) == 1


def test_a_new_value_in_a_batch_is_logged_once_for_all_papers(log_entries) -> None:
    pc.read([V12_3, V12_3])
    assert len(log_entries()) == 1


def test_each_new_value_is_logged(tmp_path: Path, log_entries) -> None:
    data = orjson.loads(V12_1.read_bytes())
    data["source"]["input_format"] = "rtf"
    data["section"][0]["section_type"] = "preregistration"
    data["bib"][0]["bib_type"] = "podcast"
    data["xref"][0]["xref_type"] = "video"
    path = tmp_path / "new_values.json"
    path.write_bytes(orjson.dumps(data))

    paper = pc.read(path)
    assert paper.section["section_type"].iloc[0] == "preregistration"
    assert paper.bib["bib_type"].iloc[0] == "podcast"
    (entry,) = log_entries()
    assert entry["values"] == {
        "source.input_format": ["rtf"],
        "section.section_type": ["preregistration"],
        "bib.bib_type": ["podcast"],
        "xref.xref_type": ["video"],
    }


def test_a_file_full_of_new_values_writes_a_small_log_entry(tmp_path: Path, log_entries) -> None:
    data = orjson.loads(V12_1.read_bytes())
    row = data["section"][0]
    data["section"] = [{**row, "section_type": f"type_{i}_" + "x" * 5000} for i in range(100)]
    path = tmp_path / "many_values.json"
    path.write_bytes(orjson.dumps(data))

    pc.read(path)
    (entry,) = log_entries()
    listed = entry["values"]["section.section_type"]
    assert len(listed) == 10
    assert all(len(v) == 64 for v in listed)
    assert entry["left_out"] == 100 - 10
    assert len(bibr12._LOGGED_VALUES) == 100

    # the memory is capped: a process remembers at most 500 values
    data["section"] = [{**row, "section_type": f"other_{i}"} for i in range(2000)]
    path.write_bytes(orjson.dumps(data))
    pc.read(path)
    assert len(bibr12._LOGGED_VALUES) == 500


def test_a_log_that_cannot_be_written_does_not_stop_the_read(monkeypatch) -> None:
    def no_log_dir() -> Path:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(bibr12, "_LOGGED_VALUES", set())
    monkeypatch.setattr("metacheck.log.logpath", no_log_dir)
    paper = pc.read(V12_3)
    assert paper.section["section_type"].tolist() == ["preregistration"]


def test_values_12x_lists_are_not_logged(log_entries, fixtures_dir: Path) -> None:
    for name in ("full", "probe_html", "probe_docx", "PMC4383902", "preprint"):
        pc.read(fixtures_dir / "bibr12" / f"{name}.json")
    pc.read(V12_1)
    assert log_entries() == []


# -- saving a paper (docs/BIBR.md) ------------------------------------------------------


def test_a_12_1_paper_is_saved_as_a_paper_object(tmp_path: Path) -> None:
    paper = pc.read(V12_1)
    with pytest.raises(ValueError, match="cannot rewrite"):
        pc.paper_write(paper, save_path=tmp_path)
    path = pc.paper_write(paper, save_path=tmp_path, schema_version=None)
    again = pc.read(path)
    assert again.text.equals(paper.text)
    assert again.info["schema_version"].tolist() == ["12.1"]
    # what docs/BIBR.md says is lost on the way back
    assert paper.extraction is not None
    assert again.get("extraction") is None
    assert "abstract" in paper.info.columns
    assert "abstract" not in again.info.columns


# -- a 12.1 export from the ScienceVerse Platform (metacheck 46da81a, 97612c7) ---------
#
# platform_12_1.json is metacheck's tests/testthat/fixtures/bibr12/platform_12_1.json: a
# 12.1 export, as bibr's convert_bibr(backend = "scivrs") saves it from the ScienceVerse
# Platform, of Sanchez Medero G (2026) Power concentration and political dynamics in
# liquid democracy, Open Research Europe 6:335,
# https://doi.org/10.12688/openreseurope.23863.2 (CC BY 4.0).

PLATFORM = FX / "platform_12_1.json"


def test_a_platform_12_1_export_reads() -> None:
    data = orjson.loads(PLATFORM.read_bytes())
    paper = pc.read(PLATFORM)
    assert pc.paper_validate(paper)
    assert paper.paper_id == "08be289905897e61"

    # metadata and source make the info table
    info = paper.info.iloc[0]
    assert info["schema_version"] == "12.1"
    assert info["title"] == data["metadata"]["title"]
    assert info["journal"] == "Open Research Europe"
    assert list(info["keywords"]) == data["metadata"]["keywords"]
    assert info["sha256"] == data["source"]["sha256"]
    assert info["bibr_version"] == "0.5.1"

    # extraction.fields, which 12.1 adds, says why the DOI is missing
    assert pd.isna(info["doi"])
    fields = paper.extraction["fields"]
    assert fields["doi"]["state"] == "abstained"
    assert fields["doi"]["issues"][0] == "VAL_DOI_AMBIGUOUS"
    assert fields["title"]["state"] == "extracted"

    # a citation resolves to its reference by target_id; its xref_id is the
    # row's own key, which no reference has
    text = paper.text.set_index("text_id")
    cite = paper.xref[paper.xref["xref_type"] == "bib"].iloc[0]
    assert cite["contents"] == "Paulin, 2020"
    assert "Paulin, 2020" in text.loc[cite["text_id"], "text"]
    ref = paper.bib[paper.bib["bib_id"] == cite["target_id"]]
    assert ref["title"].tolist() == ["An overview of ten years of liquid democracy research"]
    assert cite["xref_id"] not in paper.bib["bib_id"].tolist()

    # the table caption and the note under it are text rows with no section
    tbl = paper.table.iloc[0]
    assert tbl["label"] == "1"
    assert text.loc[tbl["text_id"], "text"] == tbl["caption"]
    assert pd.isna(text.loc[tbl["text_id"], "section_id"])
    (note_id,) = paper.footnote["text_id"].tolist()
    assert text.loc[note_id, "text"].startswith("Note: All metrics")
    assert pd.isna(text.loc[note_id, "section_id"])

    # every citation counts
    out = pc.module_run(paper, "ref_consistency")
    assert out.summary_table["n_bib"].tolist() == [10]
    assert out.summary_table["n_xrefs"].tolist() == [28]


def test_a_later_12x_platform_export_reads(tmp_path: Path) -> None:
    data = orjson.loads(PLATFORM.read_bytes())
    data["schema_version"] = "12.3"
    data["notes"] = {"added_in": "12.3"}
    data["section"][0]["section_type"] = "preregistration"
    data["extraction"]["fields"]["data_availability"] = {"state": "extracted"}
    path = tmp_path / "platform_12_3.json"
    path.write_bytes(orjson.dumps(data))

    paper = pc.read(path)
    orig = pc.read(PLATFORM)
    assert pc.paper_validate(paper)
    assert paper.info["schema_version"].tolist() == ["12.3"]
    assert list(paper.keys()) == list(orig.keys())
    assert paper.section["section_type"].iloc[0] == "preregistration"
    pd.testing.assert_frame_equal(paper.text, orig.text)
    pd.testing.assert_frame_equal(paper.xref, orig.xref)
    assert paper.extraction["fields"]["data_availability"]["state"] == "extracted"


def test_a_folder_reads_both_formats(tmp_path: Path) -> None:
    (tmp_path / "demo.json").write_bytes(pc.demofile("json").read_bytes())
    (tmp_path / "platform.json").write_bytes(PLATFORM.read_bytes())
    papers = pc.read(tmp_path)
    assert len(papers) == 2
    assert [bibr12.is_bibr12(p) for p in papers] == [False, True]


def test_read_warns_why_it_skipped_a_file(tmp_path: Path) -> None:
    path = with_changes(PLATFORM, tmp_path, schema_version="13.0")
    (tmp_path / "platform.json").write_bytes(PLATFORM.read_bytes())
    with pytest.warns(
        UserWarning, match=r"^Could not read changed\.json: bibr export schema 13\.0 is not"
    ):
        paper = pc.read(tmp_path)
    assert paper.info["schema_version"].tolist() == ["12.1"]
    # one file on its own raises its error
    with pytest.raises(ValueError, match=r"schema 13\.0 is not supported"):
        pc.read(path)
