"""The bibr export schema 12.x contract: other schemas, new enum values, 12.1 exports.

The two fixtures are bibr's own schema conformance examples: a 12.1 export
(with the ``extraction.fields`` block 12.1 adds) and a 12.3 export with a
section type, a key and a top-level block that 12.x does not list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import pytest

import pytacheck as pc
from pytacheck.io import bibr12

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
    monkeypatch.setattr("pytacheck.log.logpath", no_log_dir)
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
