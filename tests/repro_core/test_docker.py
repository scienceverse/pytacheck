"""The Docker backend (port of the docker tests in test-reproducibility_check.R).

``docker`` itself is replaced by fakes (the ``_docker`` seam); a test with a
real daemon runs only when Docker is available.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro import docker
from metacheck.repro.docker import (
    repro_docker_available,
    repro_install_deps_docker,
    repro_run_scripts_docker,
)


def _na(s: pd.Series) -> list[Any]:
    return [None if pd.isna(v) else v for v in s.tolist()]


def _mount(args: list[str], target: str) -> str:
    """The host side of the ``-v host:target`` mount in docker *args*."""
    for i, a in enumerate(args):
        # host:target[:ro], where a Windows host path has a drive colon of its own
        m = re.fullmatch(r"(.+):(/[^:]*)(?::ro)?", args[i + 1]) if a == "-v" else None
        if m and m.group(2) == target:
            return m.group(1)
    raise AssertionError(f"no mount for {target}: {args}")


# availability and small helpers ------------------------------------------------------


def test_docker_available_shape() -> None:
    res = repro_docker_available()
    assert set(res) == {"ok", "msg"}
    assert isinstance(res["ok"], bool)
    assert (res["msg"] == "") == res["ok"]


def test_docker_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker.shutil, "which", lambda _: None)
    res = repro_docker_available()
    assert res["ok"] is False
    assert res["msg"].startswith("the 'docker' command was not found on PATH.")


def test_docker_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(docker, "_docker", lambda args, **kw: {"status": 1, "timeout": False})
    res = repro_docker_available()
    assert res == {
        "ok": False,
        "msg": "Docker does not appear to be running. Start Docker Desktop and try again.",
    }


def test_docker_daemon_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(docker, "_docker", lambda args, **kw: {"status": 0, "timeout": False})
    assert repro_docker_available() == {"ok": True, "msg": ""}


def test_container_name() -> None:
    a, b = docker._repro_docker_container_name(), docker._repro_docker_container_name()
    assert re.fullmatch(r"repro_repro_[0-9a-f]+", a)
    assert a != b


def test_docker_stop_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def boom(args: list[str], **kw: Any) -> Any:
        seen.append(args)
        raise RuntimeError("no such container")

    monkeypatch.setattr(docker, "_docker", boom)
    assert docker._repro_docker_stop("repro_x") is None
    assert seen == [["stop", "--time", "5", "repro_x"]]


def test_resource_args() -> None:
    from metacheck.utils import local_options

    assert docker._repro_docker_resource_args() == []
    with local_options({"metacheck.docker_resource_limits": {"cpus": 2, "memory_gb": 3.5}}):
        assert docker._repro_docker_resource_args() == ["--cpus", "2", "--memory", "3.5g"]


def test_container_path(tmp_path: Path) -> None:
    root = tmp_path / "sb"
    (root / "a").mkdir(parents=True)
    (root / "a" / "b.R").write_text("x\n")
    assert (
        docker._repro_docker_container_path(str(root / "a" / "b.R"), str(root)) == "/sandbox/a/b.R"
    )
    assert docker._repro_docker_container_path(str(root), str(root)) == "/sandbox/"
    assert docker._repro_docker_container_path("/elsewhere/x.R", str(root)) == "/sandbox/x.R"


def test_image_for() -> None:
    assert docker._repro_docker_image_for(["4.3.1"]) == docker._REPRO_DOCKER_DEFAULT_IMAGE
    assert docker._repro_docker_image_for(["4.3.1"], True) == "rocker/r-ver:4.3.1"
    assert docker._repro_docker_image_for([], True) == "rocker/r-ver:latest"
    assert docker._repro_docker_image_for([None, "\n4.5.0"], True) == "rocker/r-ver:4.5.0"
    assert docker._repro_docker_image_for(["4.5.0 patched"], True) == "rocker/r-ver:latest"


def test_make_names() -> None:
    assert [docker._make_names(x) for x in ["1a.R", "a b.R", ".2x", "if", "_x", None]] == [
        "X1a.R",
        "a.b.R",
        "X.2x",
        "if.",
        "X_x",
        "NA.",
    ]


# the generated install.R (issue #418) -------------------------------------------------


def test_install_script_parses_for_a_long_dependency_list(rscript: str, tmp_path: Path) -> None:
    pkgs = [
        "bayestestR", "brms", "broom", "broom.mixed", "dplyr", "ggdist", "ggh4x",
        "ggnewscale", "ggpubr", "ggridges", "janitor", "lubridate", "modelr", "moments",
        "PearsonDS", "scales", "tidybayes", "tidyverse",
    ]  # fmt: skip
    deps = pd.DataFrame(
        {"package": pkgs, "source": ["cran"] * len(pkgs), "ref": [None] * len(pkgs)}
    )
    header = docker._install_script(deps)[:3]
    assert len(header) == 3
    assert all(re.match(r"^\.repro_docker_(pkgs|srcs|refs) <- ", h) for h in header)
    script = tmp_path / "h.R"
    script.write_text(
        "\n".join(header)
        + '\ncat(identical(.repro_docker_pkgs, c("'
        + '", "'.join(pkgs)
        + '")), all(is.na(.repro_docker_refs)), length(.repro_docker_refs), "\\n")\n'
    )
    out = subprocess.run([rscript, str(script)], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["TRUE", "TRUE", str(len(pkgs))]


def test_install_script_reports_the_unavailable_warning(rscript: str, tmp_path: Path) -> None:
    # issue #421: install.packages() only warns for a package it cannot find; the
    # script keeps that warning as the reason instead of "installed but ... not loadable"
    deps = pd.DataFrame({"package": ["zzznotapackagezzz"], "source": ["cran"], "ref": [None]})
    lines = docker._install_script(deps)
    assert any("withCallingHandlers" in ln for ln in lines)
    assert any('grep("is not available", warns' in ln for ln in lines)
    repo = tmp_path / "repo" / "src" / "contrib"
    repo.mkdir(parents=True)
    (repo / "PACKAGES").write_text("")
    lib = tmp_path / "lib"
    # run the script with the container's paths swapped for local ones
    script = "\n".join(lines)
    # forward slashes and a file URL, so a Windows path is not read as R escapes
    script = script.replace('"/rlib"', f'"{lib.as_posix()}"')
    script = script.replace("/sandbox/", f"{tmp_path.as_posix()}/")
    script = f'options(repos = c(CRAN = "{(tmp_path / "repo").as_uri()}"))\n' + script
    path = tmp_path / "install.R"
    path.write_text(script)
    subprocess.run([rscript, str(path)], capture_output=True, text=True, check=True)
    from metacheck.datacheck._files_rdata import r_frame_to_pandas, read_rds

    out = r_frame_to_pandas(read_rds(str(tmp_path / ".install_results.rds")), subset=False)
    assert [bool(v) for v in out["installed"]] == [False]
    msg = str(next(iter(out["message"])))
    assert "zzznotapackagezzz" in msg and "is not available" in msg


def test_deparse1_escapes(rscript: str, tmp_path: Path) -> None:
    values = ['q"uo\\te', "tab\there", "é", None, "nl\nx"]
    script = tmp_path / "d.R"
    script.write_text(
        f"x <- {docker._deparse1_chr(values)}\n"
        f'cat(identical(x, c("q\\"uo\\\\te", "tab\\there", "\\u00e9", NA, "nl\\nx")), "\\n")\n',
        encoding="utf-8",
    )
    out = subprocess.run(
        [rscript, str(script)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LANG": "C.UTF-8"},
    )
    assert out.stdout.strip() == "TRUE"
    assert docker._deparse1_chr([None, None]) == "c(NA_character_, NA_character_)"
    assert docker._deparse1_chr([]) == "character(0)"
    assert docker._deparse1_chr(None) == "NULL"
    assert docker._deparse1_chr(["a"]) == '"a"'


# repro_install_deps_docker() with a fake docker ----------------------------------------------


DEPS = pd.DataFrame(
    {"package": ["a", "b", "c"], "source": ["cran", "bioc", "github"], "ref": [None, None, "u/c"]}
)


def test_install_deps_docker_empty(tmp_path: Path) -> None:
    out = repro_install_deps_docker(DEPS.iloc[0:0], tmp_path / "lib")
    assert len(out) == 0
    assert "category" in out.columns


def test_install_deps_docker_partial_results(
    rscript: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The container finished two packages, then timed out: the third is padded."""
    seen: dict[str, Any] = {}

    def fake(args: list[str], **kw: Any) -> dict[str, Any]:
        if args[0] == "stop":
            seen["stopped"] = args[-1]
            return {"status": 0, "timeout": False}
        seen["args"] = args
        sandbox = _mount(args, "/sandbox")
        seen["script"] = Path(sandbox, "install.R").read_text(encoding="utf-8")
        rds = Path(sandbox, ".install_results.rds")
        subprocess.run(
            [
                rscript,
                "-e",
                "saveRDS(data.frame(package = c('a', 'b'), source = c('cran', 'bioc'),"
                " installed = c(TRUE, FALSE), message = c('', 'there is no package called \\'zz\\'')),"
                f" '{rds.as_posix()}')",
            ],
            check=True,
        )
        return {"status": None, "timeout": True}

    monkeypatch.setattr(docker, "_docker", fake)
    out = repro_install_deps_docker(DEPS, tmp_path / "lib", timeout=5)
    assert out["package"].tolist() == ["a", "b", "c"]
    assert out["installed"].tolist() == [True, False, False]
    assert out["message"].tolist()[2] == "docker install timed out after 5s"
    assert out["via_archive"].tolist() == [False, False, False]
    assert _na(out["category"]) == [None, "transitive_dependency_missing", "network"]
    assert seen["stopped"].startswith("repro_repro_")
    assert seen["script"].startswith('.repro_docker_pkgs <- c("a", "b", "c")\n')
    args = seen["args"]
    assert args[:2] == ["run", "--rm"]
    assert "--network" not in args
    assert _mount(args, "/rlib") == str(tmp_path / "lib")
    assert args[-3:] == ["rocker/r-ver:latest", "Rscript", "/sandbox/install.R"]


def test_install_deps_docker_failed_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake(args: list[str], stdout: str | None = None, **kw: Any) -> dict[str, Any]:
        if args[0] == "stop":
            return {"status": 0, "timeout": False}
        Path(stdout or "").write_text("Unable to find image 'x'\n")
        return {"status": 125, "timeout": False}

    monkeypatch.setattr(docker, "_docker", fake)
    out = repro_install_deps_docker(DEPS.iloc[:1], tmp_path / "lib")
    assert out["message"].tolist() == [
        "docker install failed (status 125): Unable to find image 'x'"
    ]
    assert out["installed"].tolist() == [False]


def test_install_deps_docker_not_started(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stops: list[str] = []

    def fake(args: list[str], **kw: Any) -> Any:
        if args[0] == "stop":
            stops.append(args[-1])
            return {"status": 1, "timeout": False}
        return FileNotFoundError("docker")

    monkeypatch.setattr(docker, "_docker", fake)
    out = repro_install_deps_docker(DEPS.iloc[:2], tmp_path / "lib")
    assert out["message"].tolist() == ["docker run could not be started"] * 2
    assert out["category"].tolist() == ["other", "other"]
    assert len(stops) == 1


# repro_run_scripts_docker() with a fake docker ------------------------------------------------


def _run_tbl(root: Path, names: list[str]) -> pd.DataFrame:
    for n in names:
        (root / n).write_text("x <- 1\n")
    return pd.DataFrame(
        {
            "file_name": names,
            "script_path": [str(root / n) for n in names],
            "run_dir": [str(root)] * len(names),
        }
    )


def test_run_scripts_docker_outcomes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "sb"
    root.mkdir()
    lib = tmp_path / "lib"
    lib.mkdir()
    tbl = _run_tbl(root, ["ok.R", "undef.R", "slow.R", "nodocker.R", "skip.R", "bad.R"])
    calls: list[list[str]] = []

    def fake(
        args: list[str], stdout: str | None = None, stderr: str | None = None, **kw: Any
    ) -> Any:
        calls.append(args)
        if args[0] == "stop":
            return {"status": 0, "timeout": False}
        wrapper = args[-1]
        host_wrapper = Path(_mount(args, "/sandbox"), wrapper.removeprefix("/sandbox/"))
        assert host_wrapper.exists()
        text = host_wrapper.read_text(encoding="utf-8")
        if "ok.R" in text:
            Path(stdout or "").write_text("> x <- 1\n")
            cap = [{"analysis": "t", "method": "t", "rows": [], "line": 1, "call_text": "t"}]
            Path(_mount(args, "/sandbox"), ".capture.json").write_text(json.dumps(cap))
            return {"status": 0, "timeout": False}
        if "undef.R" in text:
            Path(stderr or "").write_text("Error in eval(e, envir = env) : object 'zz' not found\n")
            return {"status": 1, "timeout": False}
        if "slow.R" in text:
            return {"status": None, "timeout": True}
        return OSError("docker vanished")

    monkeypatch.setattr(docker, "_docker", fake)
    out = repro_run_scripts_docker(
        tbl,
        order=["ok.R", "undef.R", "slow.R", "nodocker.R"],
        sandbox_root=root,
        lib_dir=lib,
        timeout=7,
        skip=["skip.R"],
        parses={"bad.R": False},
    )
    assert out["outcome"].tolist() == [
        "ran_ok",
        "errored",
        "timed_out",
        "errored",
        "skipped_missing_inputs",
        "not_parsed",
    ]
    assert _na(out["error_type"])[:4] == [None, "undefined_variable", "timeout", "runtime"]
    assert out["undefined_var"].tolist()[1] == "zz"
    assert out["error"].tolist()[1] == "Error in eval(e, envir = env) : object 'zz' not found"
    assert out["error"].tolist()[2] == "timed out after 7s"
    assert out["error"].tolist()[3] == "docker vanished"
    assert out["captures"].iloc[0][0]["analysis"] == "t"
    assert out["script_lines"].iloc[0] == ["x <- 1"]
    run_args = calls[0]
    assert run_args[run_args.index("--network") + 1] == "none"
    assert "--read-only" in run_args
    assert f"{os.path.realpath(lib)}:/rlib:ro" in run_args
    assert next(c for c in calls if c[0] == "stop")[:3] == ["stop", "--time", "5"]
    assert not list(root.glob(".runner_*"))
    assert not (root / ".capture.json").exists()


def test_run_scripts_docker_empty() -> None:
    out = repro_run_scripts_docker(None, order=[], sandbox_root="/nonexistent_mc")
    assert len(out) == 0
    assert "captures" in out.columns


def test_run_scripts_docker_missing_root(tmp_path: Path) -> None:
    tbl = pd.DataFrame({"file_name": ["a.R"], "script_path": ["x"], "run_dir": ["x"]})
    with pytest.raises(FileNotFoundError):
        repro_run_scripts_docker(tbl, order=["a.R"], sandbox_root=tmp_path / "nope")


def test_wrapper_runs_in_r(rscript: str, tmp_path: Path) -> None:
    """The container runner (preamble + wrapper) is valid R that echoes and captures."""
    script = tmp_path / "s.R"
    script.write_text("x <- c(1, 2, 3, 4, 5)\nt.test(x, mu = 1)\n")
    cap = tmp_path / "cap.json"
    lines = docker._wrapper_lines(
        docker._repro_docker_capture_preamble(), script.as_posix(), False, cap.as_posix()
    )  # the container's paths have "/"
    wrapper = tmp_path / "w.R"
    wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = subprocess.run(
        [rscript, str(wrapper)], capture_output=True, text=True, check=True, cwd=tmp_path
    )
    assert out.stdout.startswith("> x <- c(1, 2, 3, 4, 5)\n> t.test(x, mu = 1)\n")
    caps = json.loads(cap.read_text(encoding="utf-8"))
    assert caps[0]["analysis"] == "One Sample t-test"
    assert caps[0]["line"] == 2


def test_run_scripts_docker_real(tmp_path: Path) -> None:
    """With a running Docker daemon and the rocker image, a script really runs."""
    if not repro_docker_available()["ok"]:
        pytest.skip("Docker not available")
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True
    )
    if info.stdout.strip() != "linux":  # a Windows-containers daemon cannot run rocker
        pytest.skip("Docker does not run Linux containers")
    root = tmp_path / "sb"
    root.mkdir()
    (root / "a.R").write_text('cat("hello\\n")\n')
    tbl = pd.DataFrame(
        {"file_name": ["a.R"], "script_path": [str(root / "a.R")], "run_dir": [str(root)]}
    )
    out = repro_run_scripts_docker(tbl, ["a.R"], root, timeout=300)
    assert out["outcome"].tolist() == ["ran_ok"]
