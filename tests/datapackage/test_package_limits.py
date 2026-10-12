"""The size limits of a data package can be raised with METACHECK_PACKAGE_* settings.

Raising one moderately must still refuse a zip bomb: the limits are checked while the archive is
read, so a raised limit moves the line and does not remove it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from metacheck.datapackage import PackageError, open_package
from metacheck.datapackage import _open as opening
from metacheck.datapackage._limits import setting

MB = 1024 * 1024


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def small_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bomb of megabytes instead of hundreds of them, so that the tests are quick."""
    monkeypatch.setattr(opening, "RATIO_FLOOR", 1 * MB)
    monkeypatch.setattr(opening, "PROBE_FLOOR", 1 * MB)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in os.environ:
        if key.startswith("METACHECK_PACKAGE_") or key.startswith("PYTACHECK_PACKAGE_"):
            monkeypatch.delenv(key)


# -- reading the settings -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [("5242880", 5242880), ("512MB", 512 * MB), ("20 GiB", 20 * 1024**3), (" 1.5 gb ", 1610612736)],
)
def test_a_size_takes_bytes_or_a_unit(
    monkeypatch: pytest.MonkeyPatch, text: str, value: int
) -> None:
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_UNPACKED", text)
    assert setting("PACKAGE_MAX_UNPACKED", 1, "size") == value


@pytest.mark.parametrize("text", ["lots", "0", "-5", "12 parsecs", "1e9"])
def test_a_value_that_is_not_a_positive_number_gives_the_default_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, text: str
) -> None:
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_FILES", text)
    with caplog.at_level(logging.WARNING, logger="pytacheck"):
        assert setting("PACKAGE_MAX_FILES", 42) == 42
    assert "METACHECK_PACKAGE_MAX_FILES" in caplog.text


def test_spaces_only_mean_unset(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_RATIO", "   ")
    assert setting("PACKAGE_MAX_RATIO", 250.0, "float") == 250.0


def test_the_ratio_is_read_as_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_RATIO", "400")
    assert setting("PACKAGE_MAX_RATIO", 250.0, "float") == 400.0


def test_the_settings_reach_the_app_profiles_when_the_process_starts(clean_env: None) -> None:
    # the local app and the shared server read the settings when they are first imported, so
    # this is checked in a new process with the variables set
    env = dict(os.environ)
    env.update(
        METACHECK_PACKAGE_MAX_UNPACKED="7MB",
        METACHECK_PACKAGE_MAX_FILES="123",
        METACHECK_PACKAGE_MAX_RATIO="300",
        METACHECK_PACKAGE_MAX_DOWNLOAD="2MB",
        METACHECK_PACKAGE_MAX_DEPTH="6",
    )
    code = (
        "import json; from metacheck.app import package as p; "
        "print(json.dumps([p.HOSTED.kwargs(), p.LOCAL.kwargs()]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout
    hosted, local = json.loads(out)
    for kwargs in (hosted, local):
        assert kwargs["max_bytes"] == 7 * MB
        assert kwargs["max_files"] == 123
        assert kwargs["max_ratio"] == 300.0
        assert kwargs["max_depth"] == 6
    assert hosted["max_download"] == 2 * MB
    assert local["max_download"] == 2 * MB  # the same setting applies on either profile


def test_the_shared_server_keeps_stricter_defaults_without_settings(clean_env: None) -> None:
    from metacheck.app import package as p

    assert p.HOSTED.unpacked < p.LOCAL.unpacked
    assert p.HOSTED.ratio < p.LOCAL.ratio
    assert p.HOSTED.files < p.LOCAL.files
    assert p.HOSTED.depth < p.LOCAL.depth
    assert p.HOSTED.download < p.LOCAL.download


# -- zip bombs with the limits raised -------------------------------------------------------


def test_a_moderately_raised_ratio_still_refuses_a_zip_bomb(
    tmp_path: Path, small_floor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 20 MB of zeros is about 1000 times its size: above the default 250, and above 500 too
    archive = _zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (20 * MB)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_RATIO", "500")
    with pytest.raises(PackageError, match="zip bomb"), open_package(archive):
        pass


def test_a_ratio_raised_past_the_bomb_lets_it_through(
    tmp_path: Path, small_floor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the setting is read: raised past the bomb's ratio, the same archive is accepted
    archive = _zip(tmp_path / "zeros.zip", {"zeros.bin": b"\0" * (20 * MB)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_RATIO", "5000")
    with open_package(archive) as pkg:
        assert pkg.files()["rel"].tolist() == ["zeros.bin"]


def test_a_moderately_raised_unpacked_size_still_refuses_what_expands_past_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    # incompressible data, so only the size is in play: 10 MB under a raised 8 MB limit
    archive = _zip(tmp_path / "big.zip", {"noise.bin": os.urandom(10 * MB)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_UNPACKED", "8MB")
    with pytest.raises(PackageError, match="bigger than 8 MB"), open_package(archive):
        pass


def test_a_raised_unpacked_size_accepts_what_fits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    archive = _zip(tmp_path / "big.zip", {"noise.bin": os.urandom(10 * MB)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_UNPACKED", "12MB")
    with open_package(archive) as pkg:
        assert pkg.files()["rel"].tolist() == ["noise.bin"]


def test_a_raised_file_count_still_refuses_an_archive_with_more_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    archive = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(6)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_FILES", "5")
    with pytest.raises(PackageError, match="more than 5 files"), open_package(archive):
        pass


def test_an_explicit_argument_beats_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    archive = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(6)})
    monkeypatch.setenv("METACHECK_PACKAGE_MAX_FILES", "2")
    with open_package(archive, max_files=10) as pkg:
        assert len(pkg.files()) == 6
