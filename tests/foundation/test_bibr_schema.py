"""Reading current bibr output (schema v12) — fixtures copied from bibr's own tests."""

from __future__ import annotations

from pathlib import Path

import orjson

import pytacheck as pc
from pytacheck.io.bibr_schema import is_modern_bibr, to_metacheck_schema

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load(name: str) -> dict:
    return orjson.loads((FIXTURES / name).read_bytes())


def test_v12_detection() -> None:
    assert is_modern_bibr(load("bibr_v12_full.json"))
    assert not is_modern_bibr(orjson.loads(pc.demofile("json").read_bytes()))


def test_v12_metadata_becomes_info() -> None:
    p = pc.read(FIXTURES / "bibr_v12_full.json")
    assert p.title == "A Demonstration Paper"
    info = p.info.iloc[0]
    assert info["doi"] == "10.1234/demo"
    assert info["input_format"] == "pdf"
    assert len(info["file_hash"]) == 16
    assert list(info["keywords"]) == ["schema", "export"]
    assert info["paper_type_confidence"] == 0.91


def test_v12_links_and_scores_use_v10_meaning() -> None:
    data = load("bibr_v12_full.json")
    old = to_metacheck_schema(data)
    targets = [x.get("target_id") for x in data["xref"]]
    assert [x["xref_id"] for x in old["xref"]] == targets
    assert old["bib_match"][0]["score"] == data["bib_match"][0]["score"] * 100
    assert old["bib_match"][0]["authors"][0]["family"] == "Smith"
    assert old["section"][0]["classification_score"] == 0.97
    assert old["author"][0]["affiliation"] == "Department of Things, Example University"
    assert old["info_match"][0]["service"] == "crossref"


def test_v10_payload_unchanged() -> None:
    demo = orjson.loads(pc.demofile("json").read_bytes())
    assert to_metacheck_schema(demo) == demo


def test_modules_run_on_v12(tmp_path) -> None:
    p = pc.read(FIXTURES / "bibr_v12_inspect.json")
    out = pc.module_run(p, "marginal")
    assert out.traffic_light in ("green", "red")


def test_from_bibr_accepts_result_like_objects() -> None:
    class FakeResult:
        data = load("bibr_v12_full.json")

    p = pc.from_bibr(FakeResult())
    assert p.title == "A Demonstration Paper"
