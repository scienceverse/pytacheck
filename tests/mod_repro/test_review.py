"""Tests for the reproducibility_check review fixes (see parity area ``mod_repro_review``)."""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import module_run
from metacheck.modules import _reproducibility as h
from metacheck.modules.reproducibility_check import _match_sandbox
from tests.mod_repro.test_reproducibility_check import (
    PLAN,
    chain,
    code_tbl_row,
    data_structure,
    exec_chain,
)


@pytest.fixture
def paper() -> pc.Paper:
    p = pc.test_paper()
    p.paper_id = "p1"
    return p


def test_sandbox_is_matched_like_match_arg() -> None:
    # match.arg(): exact or unique partial match, the full choice vector is the default; the
    # choices are ("docker", "process"), the reverse of metacheck's (D62)
    assert _match_sandbox("process") == "process"
    assert _match_sandbox("proc") == "process"
    assert _match_sandbox("p") == "process"
    assert _match_sandbox("docker") == "docker"
    assert _match_sandbox("d") == "docker"
    assert _match_sandbox(["docker", "process"]) == "docker"
    assert _match_sandbox(("docker", "process")) == "docker"
    assert _match_sandbox(("docker",)) == "docker"
    assert _match_sandbox(["proc"]) == "process"
    assert _match_sandbox(None) == "docker"
    with pytest.raises(ValueError, match="'arg' must be of length 1"):
        _match_sandbox(["docker", "docker", "process"])
    with pytest.raises(ValueError, match="'arg' should be one of “docker”, “process”"):
        _match_sandbox("")
    with pytest.raises(ValueError, match="'arg' should be one of"):
        _match_sandbox("vm")
    with pytest.raises(ValueError, match="must be NULL or a character vector"):
        _match_sandbox(1)


@pytest.mark.parametrize("old_default", [["process", "docker"], ("process", "docker")])
def test_the_old_default_vector_is_refused(old_default: Any) -> None:
    # metacheck's c("process", "docker") used to mean "process"; a caller that still passes it
    # must say which sandbox it wants, not get one of them silently
    with pytest.raises(ValueError, match=r"'arg' must be of length 1: .*metacheck's default"):
        _match_sandbox(old_default)


def test_partial_sandbox_runs_the_static_check(paper: pc.Paper) -> None:
    mo = module_run(paper, "reproducibility_check", sandbox="proc", local_only=True)
    assert mo.traffic_light == "na"


def test_code_table_without_file_name_has_no_r_rows(paper: pc.Paper, repro_fixture: Any) -> None:
    # R: r_files[!grepl(pattern, r_files$file_name), ] with no file_name column
    # is r_files[logical(0), ] -- zero rows, so the "R code only" empty result
    code_tbl = pd.DataFrame(
        {"paper_id": ["p1"], "language": ["R"], "file_location": [repro_fixture("ok.R")]}
    )
    mo = module_run(
        chain(paper, code_tbl, data_structure(paper, repro_fixture), PLAN),
        "reproducibility_check",
    )
    assert mo.traffic_light == "na"
    assert "this phase runs R code only" in mo.summary_text


def test_rnamedlist_is_an_r_named_list() -> None:
    x = h.RNamedList(["a.R", "b.R", "a.R"], [1, 2, 3])
    assert x["a.R"] == 1  # x[["a.R"]]: the first element of that name
    assert x.at(2) == 3
    assert list(x) == ["a.R", "b.R", "a.R"]
    assert x.values() == [1, 2, 3]
    assert "b.R" in x and "c.R" not in x and ["a.R"] not in x
    with pytest.raises(KeyError):
        x["c.R"]


def test_each_code_file_is_read_once(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.codecheck import core

    calls: list[str] = []
    real = core.code_read

    def counting(path: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(core, "code_read", counting)
    code_tbl = code_tbl_row(
        paper,
        ["ok.R", "ok.R", "errors.R"],
        [repro_fixture("ok.R"), repro_fixture("ok.R"), repro_fixture("errors.R")],
    )
    mo = module_run(
        chain(paper, code_tbl, data_structure(paper, repro_fixture), PLAN),
        "reproducibility_check",
    )
    assert mo.table["file_name"].tolist() == ["ok.R", "errors.R"]
    # one read per code_check row (the duplicate is still read to hash it), none again
    assert len(calls) == 3


@pytest.mark.parametrize("declared", [np.array(["4.1.2"]), "4.1.2", pd.Series(["4.1.2", "4.0.0"])])
def test_docker_declared_version_of_any_vector_type(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch, declared: Any
) -> None:
    from metacheck.repro import docker

    seen: dict[str, Any] = {}

    def fake_run_docker(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        from tests.mod_repro.test_reproducibility_check import _run_frame

        seen.update(kw)
        return _run_frame([{"fn": "ok.R", "outcome": "ran_ok", "stdout": ""}])

    monkeypatch.setattr(docker, "repro_docker_available", lambda: {"ok": True, "msg": ""})
    monkeypatch.setattr(docker, "repro_run_scripts_docker", fake_run_docker)
    fake = exec_chain(paper, "ok.R", repro_fixture)
    fake.extras["version_pin"] = {"r_versions": declared}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        module_run(fake, "reproducibility_check", execute=True, sandbox="docker")
    assert any("declared R version 4.1.2" in str(x.message) for x in w)
    module_run(
        fake,
        "reproducibility_check",
        execute=True,
        sandbox="docker",
        docker_use_declared_version=True,
    )
    assert seen["image"] == "rocker/r-ver:4.1.2"
