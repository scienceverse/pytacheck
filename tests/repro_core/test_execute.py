"""Execution helpers of the reproducibility check (port of test-reproducibility_check.R)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.repro import core
from pytacheck.repro.core import (
    MaterialisedRoot,
    repro_install_deps,
    repro_materialize_layout,
    repro_run_scripts,
    repro_write_scripts,
)

# repro_materialize_layout() ------------------------------------------------------


def test_materialize_copies_planned_files(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    src.write_text("a,b\n1,2\n")
    structure_df = pd.DataFrame({"file_name": ["demographics.csv"], "file_location": [str(src)]})
    plan = pd.DataFrame(
        {
            "file_name": ["demographics.csv"],
            "target_path": ["study-ex1/data/study-demographics_data.csv"],
        }
    )
    root = tmp_path / "root"
    out = repro_materialize_layout(plan, structure_df, root)
    assert isinstance(out, MaterialisedRoot) and out == str(root)
    assert (root / "study-ex1/data/study-demographics_data.csv").exists()
    assert (root / "output").is_dir()
    assert out.materialised["ok"].tolist() == [True]


def test_materialize_copies_converted_original(tmp_path: Path) -> None:
    src = tmp_path / "math.dta"
    src.write_text("fake dta bytes\n")
    structure_df = pd.DataFrame({"file_name": ["math.dta"], "file_location": [str(src)]})
    plan = pd.DataFrame(
        {
            "file_name": ["math.dta"],
            "target_path": ["study-ex1/data/study-math_data.csv"],
            "original_target": ["study-ex1/data/math.dta"],
        }
    )
    root = tmp_path / "root"
    repro_materialize_layout(plan, structure_df, root)
    assert (root / "study-ex1/data/math.dta").exists()


def test_materialize_without_plan(tmp_path: Path) -> None:
    out = repro_materialize_layout(None, None, tmp_path / "root")
    assert (tmp_path / "root" / "output").is_dir()
    assert len(out.attrs["materialised"]) == 0


def test_materialize_missing_source(tmp_path: Path) -> None:
    structure_df = pd.DataFrame({"file_name": ["demographics.csv"], "file_location": [None]})
    plan = pd.DataFrame(
        {"file_name": ["demographics.csv"], "target_path": ["data/demographics.csv"]}
    )
    out = repro_materialize_layout(plan, structure_df, tmp_path / "root")
    mat = out.attrs["materialised"]
    assert mat.loc[mat["target_path"] == "data/demographics.csv", "ok"].tolist() == [False]


# repro_write_scripts() ----------------------------------------------------------------


def _no_rewrites() -> pd.DataFrame:
    return core._rewrite_frame([])


def _plan(name: str) -> pd.DataFrame:
    return pd.DataFrame({"file_name": [name], "target_path": [name]})


def test_write_scripts_applies_rewrite(tmp_path: Path) -> None:
    rewrite = core._rewrite_frame(
        [
            (
                "data/demographics.csv",
                "demographics.csv",
                True,
                "study-ex1/data/study-demographics_data.csv",
                False,
                1,
                False,
            )
        ]
    )
    out = repro_write_scripts(
        {"analysis.R": 'd <- read.csv("data/demographics.csv")'},
        {"analysis.R": rewrite},
        _plan("analysis.R"),
        tmp_path,
    )
    written = Path(out["script_path"].iloc[0]).read_text()
    assert "study-ex1/data/study-demographics_data.csv" in written


def test_write_scripts_comments_out_setwd(tmp_path: Path) -> None:
    out = repro_write_scripts(
        {"analysis.R": ['setwd("/Users/author/project")', "x <- 1"]},
        {"analysis.R": _no_rewrites()},
        _plan("analysis.R"),
        tmp_path,
    )
    assert out["setwd_removed"].tolist() == [1]
    assert "/Users/author/project" in out["setwd_paths"].iloc[0]
    written = Path(out["script_path"].iloc[0]).read_text().splitlines()
    assert written[0].startswith("# [reproducibility_check removed setwd]")


def test_write_scripts_redirects_writes(tmp_path: Path) -> None:
    out = repro_write_scripts(
        {"analysis.R": 'write.csv(x, "results/out.csv")'},
        {"analysis.R": _no_rewrites()},
        _plan("analysis.R"),
        tmp_path,
    )
    written = Path(out["script_path"].iloc[0]).read_text()
    assert "output/out.csv" in written
    assert "results/out.csv" not in written


def test_write_scripts_replaces_font_family(tmp_path: Path) -> None:
    out = repro_write_scripts(
        {"plot.R": 'theme_few(base_family = "Times New Roman")'},
        {"plot.R": _no_rewrites()},
        _plan("plot.R"),
        tmp_path,
    )
    assert out["family_replaced"].tolist() == [1]
    assert 'base_family = "sans"' in Path(out["script_path"].iloc[0]).read_text()


def test_write_scripts_without_plan_target(tmp_path: Path) -> None:
    out = repro_write_scripts({"orphan.R": "x <- 1"}, {"orphan.R": _no_rewrites()}, None, tmp_path)
    assert out["script_path"].tolist() == [f"{tmp_path}/orphan.R"]


def test_write_scripts_injects_library(tmp_path: Path) -> None:
    out = repro_write_scripts(
        {"uses_testthat.R": ['test_that("x", { expect_true(TRUE) })']},
        {"uses_testthat.R": _no_rewrites()},
        _plan("uses_testthat.R"),
        tmp_path,
        inject_libs={"uses_testthat.R": "testthat"},
    )
    assert out["library_injected"].tolist() == ["testthat"]
    assert Path(out["script_path"].iloc[0]).read_text().startswith("library(testthat)")


def test_write_scripts_injects_nothing_for_other_files(tmp_path: Path) -> None:
    out = repro_write_scripts(
        {"plain.R": "x <- 1"},
        {"plain.R": _no_rewrites()},
        _plan("plain.R"),
        tmp_path,
        inject_libs={"other.R": "testthat"},
    )
    assert out["library_injected"].isna().all()
    assert "library(" not in Path(out["script_path"].iloc[0]).read_text()


def test_write_scripts_needs_names(tmp_path: Path) -> None:
    with pytest.raises(IndexError):
        repro_write_scripts(["x <- 1"], None, None, tmp_path)  # type: ignore[arg-type]


# repro_run_scripts() (runs R subprocesses) ------------------------------------------------


def _run_tbl(root: Path, names: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "file_name": names,
            "script_path": [str(root / n) for n in names],
            "run_dir": [str(root)] * len(names),
        }
    )


def test_run_scripts_ran_ok(rscript: str, repro_fixtures: Path, tmp_path: Path) -> None:
    shutil.copy(repro_fixtures / "data.csv", tmp_path)
    shutil.copy(repro_fixtures / "ok.R", tmp_path)
    out = repro_run_scripts(_run_tbl(tmp_path, ["ok.R"]), order=["ok.R"], timeout=60)
    assert out["outcome"].tolist() == ["ran_ok"]
    assert "Welch Two Sample t-test" in out["stdout"].iloc[0]
    assert out["script_lines"].iloc[0] == (repro_fixtures / "ok.R").read_text().splitlines()
    assert out["captures"].iloc[0][0]["analysis"] == "Welch Two Sample t-test"


def test_run_scripts_errored(rscript: str, repro_fixtures: Path, tmp_path: Path) -> None:
    shutil.copy(repro_fixtures / "data.csv", tmp_path)
    shutil.copy(repro_fixtures / "errors.R", tmp_path)
    out = repro_run_scripts(_run_tbl(tmp_path, ["errors.R"]), order=["errors.R"], timeout=60)
    assert out["outcome"].tolist() == ["errored"]
    assert out["error_type"].tolist() == ["runtime"]
    assert "deliberate failure" in out["error"].iloc[0]
    assert out["stderr"].iloc[0].startswith("Error in eval(e, envir = env)")


def test_run_scripts_undefined_variable(rscript: str, repro_fixtures: Path, tmp_path: Path) -> None:
    shutil.copy(repro_fixtures / "undefined_var.R", tmp_path)
    out = repro_run_scripts(
        _run_tbl(tmp_path, ["undefined_var.R"]), order=["undefined_var.R"], timeout=60
    )
    assert out["outcome"].tolist() == ["errored"]
    assert out["error_type"].tolist() == ["undefined_variable"]
    assert out["undefined_var"].tolist() == ["some_var_no_script_defines"]


def test_run_scripts_skip(tmp_path: Path) -> None:
    (tmp_path / "skip_me.R").write_text("x <- 1\n")
    out = repro_run_scripts(
        _run_tbl(tmp_path, ["skip_me.R"]), order=["skip_me.R"], skip=["skip_me.R"]
    )
    assert out["outcome"].tolist() == ["skipped_missing_inputs"]


def test_run_scripts_not_parsed(tmp_path: Path) -> None:
    (tmp_path / "bad.R").write_text("x <- 1\n")
    out = repro_run_scripts(_run_tbl(tmp_path, ["bad.R"]), order=["bad.R"], parses={"bad.R": False})
    assert out["outcome"].tolist() == ["not_parsed"]


def test_run_scripts_empty() -> None:
    out = repro_run_scripts(pd.DataFrame(), order=[])
    assert len(out) == 0
    assert {"file_name", "outcome", "script_lines", "captures"} <= set(out.columns)


def test_run_scripts_timeout(rscript: str, tmp_path: Path) -> None:
    (tmp_path / "slow.R").write_text('cat("start\\n")\nSys.sleep(30)\n')
    out = repro_run_scripts(_run_tbl(tmp_path, ["slow.R"]), order=["slow.R"], timeout=2)
    assert out["outcome"].tolist() == ["timed_out"]
    assert out["error"].tolist() == ["! callr timed out"]
    assert out["error_type"].tolist() == ["timeout"]


def test_run_scripts_dependency_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A library() of a package that failed to install (ASCII-quoted message)."""
    (tmp_path / "a.R").write_text("library(gonepkg)\n")

    def fake_run(*args: Any, **kwargs: Any) -> None:
        Path(args[6]).write_text(
            "Error in library(gonepkg) : there is no package called 'gonepkg'\n"
        )
        raise core._RunError("! in callr subprocess.\nCaused by error in `library(gonepkg)`:\n! x")

    monkeypatch.setattr(core, "_callr_run", fake_run)
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    out = repro_run_scripts(_run_tbl(tmp_path, ["a.R"]), order=["a.R"], failed_deps=["gonepkg"])
    assert out["outcome"].tolist() == ["dependency_unavailable"]
    assert out["error_type"].tolist() == ["dependency_unavailable"]


def test_run_scripts_needs_r(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core, "_rscript", lambda: None)
    (tmp_path / "a.R").write_text("x <- 1\n")
    with pytest.raises(RuntimeError, match="Rscript"):
        repro_run_scripts(_run_tbl(tmp_path, ["a.R"]), order=["a.R"])


def test_run_scripts_order_appends_unlisted(rscript: str, tmp_path: Path) -> None:
    for n in ("a.R", "b.R", "c.R"):
        (tmp_path / n).write_text("x <- 1\n")
    out = repro_run_scripts(
        _run_tbl(tmp_path, ["a.R", "b.R", "c.R"]), order=["c.R", "zz.R", "a.R"], timeout=60
    )
    assert out["file_name"].tolist() == ["c.R", "a.R", "b.R"]


# repro_install_deps() -------------------------------------------------------------------


def test_install_deps_empty(tmp_path: Path) -> None:
    out = repro_install_deps(None, tmp_path / "lib")
    assert len(out) == 0
    assert out.columns.tolist() == [
        "package",
        "source",
        "installed",
        "message",
        "via_archive",
        "category",
    ]


def test_install_deps_main_library_skip(rscript: str, tmp_path: Path) -> None:
    deps = pd.DataFrame({"package": ["stats"], "source": ["cran"], "ref": [None]})
    out = repro_install_deps(deps, tmp_path / "lib", cran_to_main_lib=True)
    assert out["installed"].tolist() == [True]
    assert out.columns.tolist() == ["package", "source", "installed", "message", "via_archive"]


def test_install_deps_archive_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_install_r(mode: str, lib_dir: str, **values: Any) -> dict[str, Any]:
        calls.append((mode, values))
        if mode == "check_main":
            return {"ok": True, "skipped": False, "install_lib": "/main/lib"}
        if mode == "install":
            return {
                "ok": False,
                "msg": "installed but package 'oldpkg' is not loadable",
                "install_lib": lib_dir,
            }
        return {"ok": True, "msg": ""}

    monkeypatch.setattr(core, "_install_r", fake_install_r)
    listing = (
        '<a href="oldpkg_1.9.tar.gz">oldpkg_1.9.tar.gz</a>   2019-03-01 10:00  12K\n'
        '<a href="oldpkg_1.10.tar.gz">oldpkg_1.10.tar.gz</a>  2020-05-02 11:30  13K\n'
        '<a href="oldpkg_1.2.tar.gz">oldpkg_1.2.tar.gz</a>   2018-01-01 09:00  10K\n'
    )
    with respx.mock() as router:
        router.get("https://cran.r-project.org/src/contrib/Archive/oldpkg/").mock(
            return_value=httpx.Response(200, text=listing)
        )
        out = repro_install_deps(
            pd.DataFrame({"package": ["oldpkg"], "source": ["cran"], "ref": [None]}),
            tmp_path / "lib",
        )
    assert out["installed"].tolist() == [True]
    assert out["via_archive"].tolist() == [True]
    assert out["category"].isna().all()
    archive = [v for m, v in calls if m == "archive"]
    assert archive[0]["tarball_url"].endswith("/Archive/oldpkg/oldpkg_1.10.tar.gz")


def test_install_deps_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_install_r(mode: str, lib_dir: str, **values: Any) -> dict[str, Any]:
        return {"ok": False, "msg": "package 'nopkg' is not available for this version of R"}

    monkeypatch.setattr(core, "_install_r", fake_install_r)
    with respx.mock() as router:
        router.get("https://cran.r-project.org/src/contrib/Archive/nopkg/").mock(
            return_value=httpx.Response(404)
        )
        out = repro_install_deps(
            pd.DataFrame(
                {
                    "package": ["nopkg", "ghpkg"],
                    "source": ["cran", "github"],
                    "ref": [None, "u/ghpkg"],
                }
            ),
            tmp_path / "lib",
        )
    assert out["installed"].tolist() == [False, False]
    assert out["message"].iloc[0] == (
        "package 'nopkg' is not available for this version of R "
        "(CRAN Archive retry also failed: could not reach the CRAN Archive listing)"
    )
    # "could not reach" (the Archive retry) reads as a network failure, as in R
    assert out["category"].tolist() == ["network", "cran_unavailable"]
    assert out["via_archive"].tolist() == [False, False]


def test_cran_archive_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_install_r", lambda *a, **k: {"ok": True})
    with respx.mock() as router:
        router.get("https://cran.r-project.org/src/contrib/Archive/zzz/").mock(
            return_value=httpx.Response(200, text="<html>nothing</html>\n")
        )
        res = core._repro_cran_archive_install("zzz", "/lib", "/lib")
    assert res == {"ok": False, "msg": "package not found in the CRAN Archive", "version": None}


def test_cran_archive_installs_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(mode: str, lib_dir: str, **values: Any) -> dict[str, Any]:
        seen.update(values)
        return {"ok": True, "msg": ""}

    monkeypatch.setattr(core, "_install_r", fake)
    listing = (
        '<a href="pkg_0.9.tar.gz">pkg_0.9.tar.gz</a> 2021-01-01 00:00 1K\n'
        '<a href="pkg_0.10.tar.gz">pkg_0.10.tar.gz</a> 2022-01-01 00:00 1K\n'
    )
    with respx.mock() as router:
        router.get("https://cran.r-project.org/src/contrib/Archive/pkg/").mock(
            return_value=httpx.Response(200, text=listing)
        )
        res = core._repro_cran_archive_install("pkg", "/main", "/throwaway")
    assert res == {"ok": True, "msg": "", "version": "0.10"}
    assert seen["install_lib"] == "/main"


def test_install_r_runs_real_r(rscript: str, tmp_path: Path) -> None:
    """The R install step reports an already-installed main-library package."""
    res = core._install_r("check_main", str(tmp_path / "lib"), pkg="utils")
    assert res["ok"] is True and res["skipped"] is True
    res = core._install_r("check_main", str(tmp_path / "lib"), pkg="not_a_pkg_xyz")
    assert res["skipped"] is False and res["install_lib"]


def test_install_r_reports_errors_as_json(rscript: str, tmp_path: Path) -> None:
    """An install failure's message (with control characters) comes back intact."""
    res = core._install_r(
        "archive",
        str(tmp_path / "lib"),
        pkg="not_a_pkg_xyz",
        install_lib=str(tmp_path / "lib"),
        tarball_url=str(tmp_path / "missing\ttab.tar.gz"),
    )
    assert res["ok"] is False
    assert isinstance(res["msg"], str) and res["msg"]


def test_install_deps_main_library_archive_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With cran_to_main_lib, a CRAN Archive retry installs into the main library."""
    seen: dict[str, Any] = {}

    def fake_install_r(mode: str, lib_dir: str, **values: Any) -> dict[str, Any]:
        if mode == "check_main":
            return {"ok": True, "skipped": False, "install_lib": "/main/lib"}
        if mode == "install":
            return {"ok": False, "msg": "boom"}
        seen.update(values)
        return {"ok": False, "msg": "still boom"}

    monkeypatch.setattr(core, "_install_r", fake_install_r)
    listing = '<a href="p_1.0.tar.gz">p_1.0.tar.gz</a> 2020-01-01 00:00 1K\n'
    with respx.mock() as router:
        router.get("https://cran.r-project.org/src/contrib/Archive/p/").mock(
            return_value=httpx.Response(200, text=listing)
        )
        out = repro_install_deps(
            pd.DataFrame({"package": ["p"], "source": ["cran"], "ref": [None]}),
            tmp_path / "lib",
            cran_to_main_lib=True,
        )
    assert seen["install_lib"] == "/main/lib"
    assert out["message"].tolist() == ["boom (CRAN Archive retry also failed: still boom)"]
    assert out["category"].tolist() == ["other"]
