"""Port of metacheck's tests/testthat/test-archive-osf-metadata.R.

Fixtures come from OSF project ezcuj (two wiki pages, 1,324 log entries) and
8uqfb (no wiki). Only the first page of each log is recorded, so the
incomplete-listing warning is expected (and asserted).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pandas as pd
import pytest
import respx

from pytacheck.archives.osf_metadata import (
    _osf_download_logs,
    _osf_download_wikis,
    _osf_metadata_download,
    _osf_node_metadata,
    _osf_write_readme,
)


def test_download_wikis_writes_markdown(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    wikis = _osf_download_wikis("ezcuj", str(tmp_path))
    assert isinstance(wikis, pd.DataFrame)
    assert len(wikis) == 2
    assert set(wikis["name"]) == {"home", "Replicated Studies"}
    assert set(wikis["file"]) == {"wiki_home.md", "wiki_Replicated_Studies.md"}
    for f in wikis["file"]:
        assert (tmp_path / f).exists()
    body = (tmp_path / "wiki_home.md").read_text(encoding="utf-8").splitlines()
    assert len(body) > 1
    assert any(re.search(r"\*\*|^#|\[", line) for line in body)


def test_download_wikis_none(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    assert _osf_download_wikis("8uqfb", str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_download_logs_writes_csv(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="listed only 100 of the 1324"):
        logs = _osf_download_logs("ezcuj", str(tmp_path))
    assert isinstance(logs, pd.DataFrame)
    assert len(logs) > 0
    assert {"date", "action"} <= set(logs.columns)
    path = tmp_path / "logs.csv"
    assert path.exists()
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(logs)
    assert {"date", "action"} <= set(rows[0])
    assert not logs["action"].isna().any()
    assert all(re.match(r"^\d{4}-\d{2}-\d{2}", d) for d in logs["date"])


def test_node_metadata(mock_api: respx.MockRouter) -> None:
    meta = _osf_node_metadata("ezcuj")
    assert meta["osf_id"] == "ezcuj"
    assert meta["osf_url"] == "https://osf.io/ezcuj"
    assert meta["title"] == "Reproducibility Project: Psychology"
    assert meta["public"] is True
    assert len(meta["contributors"]) > 0
    assert all(c["name"] for c in meta["contributors"])
    assert {"date_created", "date_modified", "retrieved"} <= set(meta)


def test_metadata_download_writes_folder(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="listed only 100 of the 1324"):
        meta_dir = _osf_metadata_download("ezcuj", str(tmp_path))
    assert meta_dir is not None
    meta_path = Path(meta_dir)
    assert meta_path.name == "_osf_metadata"
    assert {p.name for p in meta_path.iterdir()} == {
        "wiki_home.md",
        "wiki_Replicated_Studies.md",
        "logs.csv",
        "metadata.json",
        "README.md",
    }
    meta = json.loads((meta_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["osf_id"] == "ezcuj"
    assert meta["title"] == "Reproducibility Project: Psychology"
    assert meta["files_written"]["logs"]["entries"] == 100
    readme = (meta_path / "README.md").read_text(encoding="utf-8")
    for needle in ["Reproducibility Project", "wiki_home.md", "logs.csv", "metadata.json"]:
        assert needle in readme
    assert "2 wiki pages" in readme


def test_metadata_download_no_wiki(mock_api: respx.MockRouter, tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="listed only 100 of the 137"):
        meta_dir = _osf_metadata_download("8uqfb", str(tmp_path))
    assert meta_dir is not None
    assert (Path(meta_dir) / "logs.csv").exists()
    assert (Path(meta_dir) / "metadata.json").exists()
    readme = (Path(meta_dir) / "README.md").read_text(encoding="utf-8")
    assert re.search(r"none \(this project has no wiki\)", readme)


def test_metadata_download_invalid(tmp_path: Path) -> None:
    with pytest.warns(UserWarning):
        assert _osf_metadata_download("notanid", str(tmp_path)) is None


def test_write_readme_formats(tmp_path: Path) -> None:
    meta = {
        "osf_id": "abcde",
        "osf_url": "https://osf.io/abcde",
        "retrieved": "2026-01-01T00:00:00+0000",
        "title": None,
        "description": "",
        "public": False,
        "tags": [],
        "license": None,
        "contributors": [{"name": "A B", "orcid": "0000-0001"}, {"name": "C", "orcid": None}],
    }
    logs = pd.DataFrame({"date": ["2020-05-01T10:00", "2019-01-02T00:00"], "action": ["x", "y"]})
    _osf_write_readme(meta, None, logs.iloc[:1], str(tmp_path))
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# abcde\n\nArchived from <https://osf.io/abcde> on 2026-01-01")
    assert "## Description" not in text
    assert "- Visibility: private" in text
    assert "- License: none recorded on the OSF" in text
    assert "- A B (ORCID 0000-0001)\n- C\n" in text
    assert "- logs.csv: 1 activity log entry, 2020-05-01 to 2020-05-01" in text
    _osf_write_readme(meta, None, logs, str(tmp_path))
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "2 activity log entries, 2019-01-02 to 2020-05-01" in text
