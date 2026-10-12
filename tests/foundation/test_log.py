"""``logger()`` and ``lastlog()`` never fail the caller for a log that cannot be used."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import orjson
import pytest

from metacheck import log


@pytest.fixture(autouse=True)
def _fresh_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(log, "_WARNED", False)


def test_an_entry_is_written_as_one_json_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "log" / "x.jsonl"
    monkeypatch.setenv("PYTACHECK_LOG", str(path))
    assert log.logger("label", {"a": 1}) == path
    entry = orjson.loads(path.read_bytes())
    assert entry["label"] == "label"
    assert entry["a"] == 1
    assert log.lastlog(path=path)["a"] == 1


def test_a_log_directory_that_cannot_be_created_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setenv("PYTACHECK_LOG", str(tmp_path / "log" / "x.jsonl"))
    monkeypatch.setattr(Path, "mkdir", refuse)
    target = log.logger("label", {"a": 1})
    assert target == tmp_path / "log" / "x.jsonl"
    assert not target.exists()


def test_a_log_file_that_cannot_be_opened_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "x.jsonl"
    path.touch()
    real_open = Path.open

    def refuse(self: Path, mode: str = "r", *args: object, **kwargs: object):
        if self == path and "a" in mode:
            raise PermissionError(13, "Permission denied")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    assert log.logger("label", {"a": 1}, path=path) == path
    assert path.read_bytes() == b""


def test_the_log_directory_can_be_missing_and_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The data directory sits under a plain file, so it can never be made."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.delenv("PYTACHECK_LOG")
    monkeypatch.setattr(log.platformdirs, "user_data_dir", lambda *a, **k: str(blocker / "data"))
    target = log.logger("label", {"a": 1})
    assert target == blocker / "data" / "log" / "pytacheck.log.jsonl"
    assert blocker.read_text() == "not a directory"


def test_reading_a_log_that_cannot_be_created_gives_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The log location sits under a plain file, so it can never be made."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("PYTACHECK_LOG", str(blocker / "log" / "x.jsonl"))
    assert log.lastlog() is None
    assert log.lastlog([1, 2]) is None
    assert blocker.read_text() == "not a directory"


def test_reading_the_default_log_on_an_unusable_data_directory_gives_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.delenv("PYTACHECK_LOG")
    monkeypatch.setattr(log.platformdirs, "user_data_dir", lambda *a, **k: str(blocker / "data"))
    assert log.lastlog() is None


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs a read-only directory that the user cannot write to",
)
def test_a_read_only_data_directory_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        monkeypatch.delenv("PYTACHECK_LOG")
        monkeypatch.setattr(log.platformdirs, "user_data_dir", lambda *a, **k: str(locked / "d"))
        target = log.logger("label", {"a": 1})
        assert target.parent == locked / "d" / "log"
        assert not (locked / "d").exists()
    finally:
        locked.chmod(0o700)


def test_one_warning_is_given_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setenv("PYTACHECK_LOG", str(tmp_path / "log" / "x.jsonl"))
    monkeypatch.setattr(Path, "mkdir", refuse)
    with caplog.at_level(logging.WARNING, logger="pytacheck"):
        log.logger("one")
        log.logger("two")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Cannot write the log file" in warnings[0].getMessage()
