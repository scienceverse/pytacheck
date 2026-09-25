"""The data_check file manifest -- port of upstream tests/testthat/test-data-manifest.R.

Provenance differs by design: pytacheck records itself (and the metacheck
release it ports) plus Python's version instead of metacheck and R.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from pytacheck.datacheck import files as F


@pytest.fixture
def llm_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "_llm_use", lambda: False)


def _read(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _one_file(**extra: Any) -> pd.DataFrame:
    return pd.DataFrame({
        "repo_url": ["https://osf.io/xxxxx"], "file_name": ["a.csv"], "file_path": ["a.csv"],
        "file_url": ["https://osf.io/download/x/"], "file_size": [10.0],
        "data_type": ["data"], "data_format": ["tabular"], "file_location": [None], **extra,
    })  # fmt: skip


def test_separates_intentional_and_unintentional(tmp_path: Path, llm_off: None) -> None:
    real = tmp_path / "real.csv"
    real.write_text("a,b\n1,2\n", encoding="utf-8")
    names = ["gotit.csv", "big.rdata", "stim.mp4", "lost.csv", "nourl.csv", "notes.pdf"]
    files = pd.DataFrame({
        "repo_url": "https://osf.io/xxxxx", "file_name": names, "file_path": names,
        "file_url": ["https://osf.io/download/x/"] * 4 + [None, "https://osf.io/download/y/"],
        "file_size": [100, 5e8, 1e6, 100, 100, 100],
        "data_type": ["data", "data", "materials", "data", "data", "documentation"],
        "data_format": "tabular",
        "file_location": [str(real), None, None, None, None, None],
    })  # fmt: skip
    want = [True, True, False, True, True, False]
    oversize = pd.DataFrame({"repo_url": ["https://osf.io/xxxxx"], "file_name": ["big.rdata"],
                             "file_size": [5e8]})  # fmt: skip
    failed = pd.DataFrame({"repo_url": ["https://osf.io/xxxxx"], "file_name": ["lost.csv"],
                           "error": ["HTTP 429 Too Many Requests.\ndetails"]})  # fmt: skip
    path = tmp_path / "m.json"
    F._data_check_write_manifest(
        path,
        files,
        want,
        None,
        "p1",
        "data",
        100,
        500,
        skip_types="materials",
        oversize=oversize,
        failed=failed,
        model="test-model",
    )
    m = _read(path)
    nd = m["not_downloaded"]
    assert nd["unintentional_n"] == 2
    assert nd["intentional_n"] == 3
    assert nd["rerun_recommended"] is True
    assert {f["file_name"] for f in nd["unintentional_files"]} == {"lost.csv", "nourl.csv"}

    by_name = {f["file_name"]: f for f in m["files"]}
    assert by_name["gotit.csv"]["status"] == "downloaded"
    assert "skip_reason" not in by_name["gotit.csv"]
    assert by_name["big.rdata"]["status"] == "skipped"
    assert by_name["big.rdata"]["skip_intentional"] is True
    assert "max_file_size" in by_name["big.rdata"]["skip_reason"]
    assert by_name["stim.mp4"]["status"] == "skipped"
    assert "excluded type 'materials'" in by_name["stim.mp4"]["skip_reason"]
    assert by_name["lost.csv"]["status"] == "failed"
    assert by_name["lost.csv"]["skip_intentional"] is False
    assert "429" in by_name["lost.csv"]["skip_reason"]
    assert "details" not in by_name["lost.csv"]["skip_reason"]  # first line only
    assert by_name["nourl.csv"]["status"] == "failed"
    assert by_name["notes.pdf"]["status"] == "skipped"
    assert 'download = "all"' in by_name["notes.pdf"]["skip_reason"]


def test_records_reproducibility_provenance(tmp_path: Path, llm_off: None) -> None:
    path = tmp_path / "m.json"
    F._data_check_write_manifest(
        path, _one_file(), True, None, "p1", "none", 100, 500, skip_types="materials"
    )
    m = _read(path)
    prov = m["provenance"]
    assert prov["software"]["name"] == "pytacheck"
    assert prov["software"]["version"]
    assert prov["software"]["port_of"].startswith("metacheck ")
    assert prov["python_version"].startswith("Python ")
    assert prov["prod_date"] == m["generated"]
    assert prov["llm"]["used"] is False
    assert m["skip_types"] == ["materials"]
    assert "ProcStat" in prov["ddi_mapping"]["files[].status"]
    assert m["not_downloaded"]["unintentional_n"] == 0
    assert m["not_downloaded"]["rerun_recommended"] is False


def test_records_llm_model_when_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "_llm_use", lambda: True)
    path = tmp_path / "m.json"
    F._data_check_write_manifest(
        path, _one_file(), False, None, "p1", "none", 100, 500, model="groq/test-model"
    )
    m = _read(path)
    assert m["provenance"]["llm"] == {"used": True, "model": "groq/test-model"}


def test_manifest_merge_creates_overlays_preserves(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    F.manifest_merge(path, {"code": {"packages": ["dplyr", "tidyr"]}})
    assert _read(path)["code"]["packages"] == ["dplyr", "tidyr"]

    F.manifest_merge(path, {"files": [{"file_name": "a.csv"}]})
    m = _read(path)
    assert m["code"]["packages"] == ["dplyr", "tidyr"]  # preserved
    assert m["files"][0]["file_name"] == "a.csv"  # added

    F.manifest_merge(path, {"code": {"packages": ["readr"]}})
    m = _read(path)
    assert m["code"]["packages"] == ["readr"]
    assert m["files"][0]["file_name"] == "a.csv"


def test_manifest_merge_null_removes_na_keeps(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    F.manifest_merge(path, {"a": 1.0, "b": "x", "c": None})
    assert _read(path) == {"a": 1, "b": "x", "c": None}
    F.manifest_merge(path, {"b": F.R_NULL, "d": True})
    # U62: a stored null stays null (metacheck re-reads it as NULL and writes {})
    assert _read(path) == {"a": 1, "c": None, "d": True}
    F.manifest_merge(path, {"e": 2.0})
    assert _read(path) == {"a": 1, "c": None, "d": True, "e": 2}


def test_manifest_merge_jsonlite_layout(tmp_path: Path) -> None:
    # R: manifest_merge(p, list(x = c(1, 2), l = list(1, 2), y = list(z = 0.000012345),
    #                           n = 5e8, t = 1e-7, s = "a"))
    # jsonlite's layout, but numbers at full precision (U62: digits = 4 writes 0 for z)
    path = tmp_path / "m.json"
    F.manifest_merge(path, {"x": F.RVector([1.0, 2.0]), "l": [1.0, 2.0],
                            "y": {"z": 0.000012345}, "n": 5e8, "t": 1e-7, "s": "a"})  # fmt: skip
    assert path.read_text(encoding="utf-8").splitlines() == [
        "{",
        '  "x": [1, 2],',
        '  "l": [',
        "    1,",
        "    2",
        "  ],",
        '  "y": {',
        '    "z": 1.2345e-05',
        "  },",
        '  "n": 500000000,',
        '  "t": 1e-07,',
        '  "s": "a"',
        "}",
    ]


def test_data_check_write_preserves_code_section(tmp_path: Path, llm_off: None) -> None:
    path = tmp_path / "m.json"
    F.manifest_merge(path, {"code": {"packages": ["dplyr"]}})
    F._data_check_write_manifest(path, _one_file(), True, None, "p1", "none", 100, 500)
    m = _read(path)
    assert m["code"]["packages"] == ["dplyr"]
    assert m["provenance"]["software"]["name"] == "pytacheck"


def test_split_one_file_per_paper(tmp_path: Path, llm_off: None) -> None:
    real_a = tmp_path / "a.csv"
    real_a.write_text("a,b\n1,2\n", encoding="utf-8")
    mdir = tmp_path / "manifests"
    files = pd.DataFrame({
        "paper_id": ["paperA", "paperB"],
        "repo_url": ["https://osf.io/aaaaa", "https://osf.io/bbbbb"],
        "file_name": ["dataA.csv", "dataB.csv"], "file_path": ["dataA.csv", "dataB.csv"],
        "file_url": ["https://osf.io/download/aaaaa/", "https://osf.io/download/bbbbb/"],
        "file_size": [100.0, 100.0], "data_type": ["data", "data"],
        "data_format": ["tabular", "tabular"], "file_location": [str(real_a), None],
    })  # fmt: skip
    failed = pd.DataFrame({
        "repo_url": ["https://osf.io/bbbbb"], "file_name": ["dataB.csv"],
        "file_url": ["https://osf.io/download/bbbbb/"], "paper_id": ["paperB"],
        "error": ["download failed (nothing was written)"],
    })  # fmt: skip
    paths = F._data_check_write_manifest(
        mdir, files, [True, True], None, ["paperA", "paperB"], "data", 100, 500, failed=failed
    )
    assert len(paths) == 2
    written = sorted(p.name for p in mdir.glob("*.manifest.json"))
    assert written == ["paperA.manifest.json", "paperB.manifest.json"]
    ma = _read(mdir / "paperA.manifest.json")
    mb = _read(mdir / "paperB.manifest.json")
    assert ma["paper_id"] == "paperA"
    assert ma["n_files"] == 1
    assert ma["files"][0]["file_name"] == "dataA.csv"
    assert mb["paper_id"] == "paperB"
    assert mb["n_files"] == 1
    assert mb["files"][0]["file_name"] == "dataB.csv"
    assert mb["files"][0]["status"] == "failed"
    assert mb["not_downloaded"]["rerun_recommended"] is True
    assert ma["not_downloaded"]["rerun_recommended"] is False


def test_data_is_manifest() -> None:
    repo = ["Study 1.r", "Study 1.csv", "notes.txt"]
    manifest = pd.DataFrame({"type": ["code", "data", "doc"], "file": repo})
    assert F.data_is_manifest(manifest, repo) is True
    real = pd.DataFrame({"id": [1, 2, 3], "score": [1.1, 2.2, 3.3]})
    assert F.data_is_manifest(real, repo) is False
    assert F.data_is_manifest(None, repo) is False
