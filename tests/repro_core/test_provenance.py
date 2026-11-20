"""Provenance kept for a TRO (``metacheck.repro.provenance``; metacheck 0.4.4.20, #404).

Mirrors metacheck's ``tests/testthat/test-provenance.R``: the helpers, then a check that runs
code and one that does not.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro import provenance as prov
from metacheck.repro.core import _run_frame, _run_row, _RunError

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def write(root: Path, rel: str, text: str = "x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# -- hashing ------------------------------------------------------------------------------------


def test_sha256_file_hashes_files_and_gives_none_for_the_rest(tmp_path: Path) -> None:
    a = write(tmp_path, "a.txt", "hello\n")
    h = prov._sha256_file([str(a), str(tmp_path / "missing.txt"), str(tmp_path), None])
    assert h == [sha(a), None, None, None]
    assert prov._sha256_file([]) == []
    assert prov._sha256_file(str(a)) == [sha(a)]
    (tmp_path / "empty").write_bytes(b"")
    assert prov._sha256_file([str(tmp_path / "empty")]) == [EMPTY_SHA]


def test_sha256_file_streams_a_large_file(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    with big.open("wb") as fh:
        for i in range(1, 41):  # 40 MB
            fh.write(bytes([i % 256]) * 1024**2)
    assert prov._sha256_file([str(big)]) == [sha(big)]


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a file the owner cannot read needs a user that is not root",
)
def test_sha256_file_gives_none_for_an_unreadable_file(tmp_path: Path) -> None:
    f = write(tmp_path, "secret.txt")
    f.chmod(0)
    try:
        assert prov._sha256_file([str(f)]) == [None]
    finally:
        f.chmod(0o644)


# -- times and exit status ----------------------------------------------------------------------


def test_iso_utc_writes_utc_times_with_milliseconds() -> None:
    t = dt.datetime(2026, 11, 18, 21, 5, 3, 250000, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert prov._repro_iso_utc(t) == "2026-11-18T20:05:03.250Z"
    assert prov._repro_iso_utc(dt.datetime(2026, 11, 18, 1, 2, 3)) == "2026-11-18T01:02:03.000Z"
    assert prov._repro_iso_utc(None) is None
    assert prov._repro_iso_utc(pd.NaT) is None
    assert prov._repro_iso_utc(float("nan")) is None


def test_add_run_clock_adds_the_columns_and_is_na_without_a_clock() -> None:
    df = pd.DataFrame({"file_name": ["a.R", "b.R"], "outcome": ["ran_ok", "ran_ok"]})
    t0 = dt.datetime(2026, 11, 18, 10, 0, 0, tzinfo=dt.UTC)
    clock = {"started": t0, "ended": t0 + dt.timedelta(seconds=5), "status": 0}
    out = prov._repro_add_run_clock(df, {"a.R": clock, "b.R": clock})
    assert out is not None
    assert out["started_at"].tolist() == ["2026-11-18T10:00:00.000Z"] * 2
    assert out["ended_at"].tolist() == ["2026-11-18T10:00:05.000Z"] * 2
    assert out["exit_status"].tolist() == [0, 0]
    # a script that did not run has no clock; a row that has the columns keeps them
    none = prov._repro_add_run_clock(df)
    assert none is not None
    assert none["started_at"].isna().all() and none["exit_status"].isna().all()
    assert prov._repro_add_run_clock(out).equals(out)  # type: ignore[union-attr]
    # an empty frame gets the columns too, so rows of every pass bind together
    empty = prov._repro_add_run_clock(df.iloc[0:0])
    assert empty is not None
    assert {"started_at", "ended_at", "exit_status"} <= set(empty.columns)
    assert len(pd.concat([empty, out, none])) == 4


def test_exit_status_reads_what_a_run_reports() -> None:
    f = prov._repro_exit_status
    assert f({"status": 1}) == 1
    assert f({"status": 0.0}) == 0
    assert f(None) is None
    assert f(RuntimeError("docker failed")) is None
    # the stopped docker command's status is not the script's
    assert f({"status": 2, "timeout": True}) is None
    assert f({"status": float("nan")}) is None
    assert f(_RunError("exited", status=2)) == 2
    assert f(_RunError("timed out", timeout=True)) is None


def test_run_frame_without_a_clock_still_has_the_columns() -> None:
    out = _run_frame([_run_row("a.R", "skipped_time_limit")])
    assert {"started_at", "ended_at", "exit_status"} <= set(out.columns)
    assert out["started_at"].isna().all()
    assert {"started_at", "ended_at", "exit_status"} <= set(_run_frame([]).columns)


# -- artifacts ----------------------------------------------------------------------------------


def test_artifacts_lists_every_file_with_hash_size_and_type(tmp_path: Path) -> None:
    d = write(tmp_path, "data/d.csv", "x,y\n1,2\n")
    a = write(tmp_path, "analysis.R", "print(1)\n")
    write(tmp_path, ".Rprofile", "hidden\n")
    write(tmp_path, "_lib/pkg/DESCRIPTION", "lib\n")
    write(tmp_path, "_pylib/m.py", "lib\n")
    write(tmp_path, ".runner_analysis.R", "runner\n")
    out = prov._repro_artifacts(tmp_path, "before_run")
    assert set(out["path"]) == {"data/d.csv", "analysis.R", ".Rprofile"}
    assert set(out["stage"]) == {"before_run"}
    by = out.set_index("path")
    assert by.loc["data/d.csv", "sha256"] == sha(d)
    assert by.loc["analysis.R", "size"] == a.stat().st_size
    assert by.loc["data/d.csv", "mime_type"] == "text/csv"
    assert by.loc["analysis.R", "mime_type"] == "text/x-r-source"
    assert pd.isna(by.loc[".Rprofile", "mime_type"])
    # an empty or missing folder: no rows, the same columns
    empty = tmp_path / "empty"
    empty.mkdir()
    e = prov._repro_artifacts(empty, "before_run")
    assert len(e) == 0
    assert list(e.columns) == list(out.columns)
    assert len(prov._repro_artifacts(tmp_path / "nope", "x")) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file names are UTF-16")
def test_artifacts_keep_file_names_in_other_encodings_as_valid_utf8(tmp_path: Path) -> None:
    name = b"data\x82.csv"  # a CP437 byte in a name, as in old zips
    try:
        with open(os.path.join(os.fsencode(tmp_path), name), "wb") as fh:
            fh.write(b"1,2\n")
    except OSError:
        pytest.skip("the file system does not take that name")
    out = prov._repro_artifacts(tmp_path, "before_run")
    assert len(out) == 1
    path = out["path"].iloc[0]
    path.encode("utf-8")  # valid UTF-8
    assert "<82>" in path
    assert not pd.isna(out["sha256"].iloc[0])
    json.dumps(out.to_dict("records"))


def test_prov_utf8_keeps_other_encodings_bytes_as_xx() -> None:
    bad = os.fsdecode(b"d\x82.R")
    out = prov._repro_prov_utf8(["plain.csv", bad, None])
    assert out[0] == "plain.csv"
    assert out[1] == "d<82>.R"
    assert out[2] is None
    json.dumps(out)


def test_artifacts_after_keeps_new_changed_and_removed_files(tmp_path: Path) -> None:
    write(tmp_path, "same.txt", "a\n")
    write(tmp_path, "changes.txt", "b\n")
    write(tmp_path, "goes.txt", "c\n")
    before = prov._repro_artifacts(tmp_path, "before_run")
    write(tmp_path, "changes.txt", "B\n")
    (tmp_path / "goes.txt").unlink()
    write(tmp_path, "output/table.csv", "result\n")
    after = prov._repro_artifacts_after(before, tmp_path)
    assert set(after["stage"]) == {"after_run"}
    st = dict(zip(after["path"], after["status"], strict=True))
    assert st == {"changes.txt": "changed", "output/table.csv": "new", "goes.txt": "removed"}
    sha_of = dict(zip(after["path"], after["sha256"], strict=True))
    old = dict(zip(before["path"], before["sha256"], strict=True))
    assert sha_of["goes.txt"] == old["goes.txt"]  # a removed file keeps its hash from before
    assert sha_of["changes.txt"] == sha(tmp_path / "changes.txt")
    # nothing listed before: every file is new
    all_new = prov._repro_artifacts_after(None, tmp_path)
    assert set(all_new["status"]) == {"new"}
    # nothing changed: no rows
    assert len(prov._repro_artifacts_after(prov._repro_artifacts(tmp_path, "b"), tmp_path)) == 0


def test_artifacts_are_listed_in_the_order_r_sorts_files(tmp_path: Path) -> None:
    # list.files() sorts in the locale's collation: "_" before "."
    for n in ("sleep.R", "sleep_b.R", "Zed.R", "alpha.R"):
        write(tmp_path, n)
    assert prov._repro_prov_files(tmp_path) == ["alpha.R", "sleep_b.R", "sleep.R", "Zed.R"]


def test_prov_files_leaves_out_the_tools_own_files_and_keeps_the_authors(tmp_path: Path) -> None:
    for f in (
        "statistical_output/results_long.csv",
        "statistical_output/a.R.statistical_output.json",
        "statistical_output/authors_table.csv",
        ".capture.rds",
        ".repro_received",
        "install.R",
        "sub/.runner_x.R",
        "_rlib/x/DESCRIPTION",
        "_pylib2/m.py",
    ):
        write(tmp_path, f)
    assert set(prov._repro_prov_files(tmp_path)) == {
        "statistical_output/authors_table.csv",
        "install.R",
    }


def test_deposit_artifacts_hash_the_downloaded_files_with_their_address(tmp_path: Path) -> None:
    src = write(tmp_path, "cache_x.csv", "1,2\n")
    m = pd.DataFrame(
        {
            "target_path": ["data/x.csv", "data/y.csv", "z.csv"],
            "source": [str(src), str(tmp_path / "gone.csv"), None],
            "ok": [True, True, False],
        }
    )
    sdf = pd.DataFrame(
        {
            "file_name": ["x.csv"],
            "file_location": [str(src)],
            "file_url": ["https://osf.io/download/abc/"],
            "repo_url": ["https://osf.io/xyz/"],
        }
    )
    d = prov._repro_deposit_artifacts(m, sdf)
    assert d["path"].tolist() == ["data/x.csv", "data/y.csv"]
    assert d["stage"].tolist() == ["deposit", "deposit"]
    assert d["sha256"].iloc[0] == sha(src)
    assert pd.isna(d["sha256"].iloc[1])  # vanished before it was hashed
    assert d["source"].iloc[0] == "https://osf.io/download/abc/"
    # no file_url: the repository address
    sdf["file_url"] = None
    assert prov._repro_deposit_artifacts(m.iloc[[0]], sdf)["source"].iloc[0] == (
        "https://osf.io/xyz/"
    )
    assert len(prov._repro_deposit_artifacts(None)) == 0
    assert len(prov._repro_deposit_artifacts(m.iloc[0:0])) == 0


def test_modification_hashes_match_scripts_by_their_path() -> None:
    arts = pd.DataFrame(
        {
            "stage": ["deposit", "before_run", "before_run"],
            "path": ["code/a.R", "code/a.R", "b.R"],
            "size": [1.0, 1.0, 1.0],
            "sha256": ["old", "new", "bnew"],
            "mime_type": [None] * 3,
            "source": [None] * 3,
            "status": [None] * 3,
        }
    )
    mods = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R", "c.R"],
            "change_type": ["setwd_removed"] * 3,
            "detail": [""] * 3,
        }
    )
    scripts = pd.DataFrame({"file_name": ["a.R", "b.R"], "path": ["code/a.R", "b.R"]})
    out = prov._repro_modification_hashes(mods, scripts, arts)
    assert out is not None
    assert out["sha256_before"].tolist() == ["old", pd.NA, pd.NA]
    assert out["sha256_after"].tolist() == ["new", "bnew", pd.NA]
    # no modifications: the columns are still there
    none = prov._repro_modification_hashes(mods.iloc[0:0], scripts, arts)
    assert none is not None
    assert {"sha256_before", "sha256_after"} <= set(none.columns)


def test_prov_scripts_gives_paths_relative_to_the_run_folder(tmp_path: Path) -> None:
    rt = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R"],
            "script_path": [str(tmp_path / "code" / "a.R"), "/elsewhere/b.R"],
        }
    )
    s = prov._repro_prov_scripts(rt, tmp_path)
    assert s["path"].tolist() == ["code/a.R", pd.NA]
    assert len(prov._repro_prov_scripts(None, tmp_path)) == 0


# -- the system that ran the code ---------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def test_environment_describes_the_system_without_docker() -> None:
    prov._repro_prov_reset()
    env = prov._repro_environment(
        "process", _now() - dt.timedelta(seconds=5), _now(), timeout=600, total_timeout=3600
    )
    assert env["sandbox"] == "process"
    assert env["network_isolated"] is False
    assert env["internet"] is False
    assert env["docker"] is None
    assert env["started_at"].endswith("Z")
    assert env["started_at"] < env["ended_at"]
    assert env["host"]["python_version"] == sys.version.split()[0]
    json.dumps(env)
    # a clock time as total_timeout is written as a time
    env2 = prov._repro_environment(
        "process", _now(), _now(), total_timeout=dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)
    )
    assert env2["total_timeout"] == "2026-11-18T12:00:00.000Z"
    # the Python run on this machine, with its version
    env3 = prov._repro_environment("process", _now(), _now(), python_bin=sys.executable)
    # the text of ``python --version``, as metacheck keeps it
    assert env3["python_local"]["version"] == f"Python {sys.version.split()[0]}"


def test_docker_runs_are_noted_once_per_kind_and_isolation_needs_network_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prov._repro_prov_reset()
    args = [
        "run", "--rm", "--name", "repro_x1", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "img:1", "Rscript", "/sandbox/a.R",
    ]  # fmt: skip
    prov._repro_prov_note_docker("r_run", args, "img:1")
    prov._repro_prov_note_docker("r_run", [a.replace("x1", "x2") for a in args], "img:1")
    prov._repro_prov_note_docker("r_install", ["run", "--name", "i", "img:1"], "img:1")
    runs = prov._PROV["docker"]
    assert len(runs) == 2
    assert "--name" not in runs["r_run|img:1"]["args"]
    assert "repro_x1" not in runs["r_run|img:1"]["args"]

    def info(image: str | None) -> dict[str, Any]:
        return {
            "image": image,
            "id": "sha256:abc",
            "repo_digests": ["img@sha256:def"],
            "created": None,
            "architecture": "amd64",
            "r_version": "4.5.1",
            "python_version": None,
        }

    monkeypatch.setattr(prov, "_repro_docker_image_info", info)
    env = prov._repro_environment("docker", _now(), _now())
    assert env["network_isolated"] is True
    assert env["docker"]["images"][0]["id"] == "sha256:abc"
    assert len(env["docker"]["runs"]) == 2
    # a run with the network on is not isolated
    prov._repro_prov_note_docker("python_run", ["run", "img:2", "python", "x.py"], "img:2")
    assert prov._repro_environment("docker", _now(), _now())["network_isolated"] is False
    # internet on: not isolated, whatever the arguments
    prov._repro_prov_reset()
    prov._repro_prov_note_docker("r_run", args, "img:1")
    assert (
        prov._repro_environment("docker", _now(), _now(), internet=True)["network_isolated"]
        is False
    )
    # only an install run noted: no claim of isolation
    prov._repro_prov_reset()
    prov._repro_prov_note_docker("r_install", args, "img:1")
    assert prov._repro_environment("docker", _now(), _now())["network_isolated"] is False
    prov._repro_prov_reset()


def test_docker_image_info_gives_none_fields_when_docker_cannot_tell() -> None:
    info = prov._repro_docker_image_info("metacheck-no-such-image:0")
    assert info["id"] is None
    assert info["repo_digests"] == []
    assert prov._repro_docker_image_info(None)["id"] is None


def test_prov_state_is_fresh_for_each_check() -> None:
    prov._repro_prov_note_docker("r_run", ["run", "img"], "img")
    state = prov._repro_prov_state()
    assert prov._PROV["docker"] == {}
    assert state["before"] is None and state["materialised"] == [] and state["scripts"] == []
    # without a run nothing was hashed: no provenance
    assert prov._repro_prov_finish(
        state, None, None, None, sandbox="process", timeout=1, total_timeout=1, internet=False
    ) == (None, None, None)
