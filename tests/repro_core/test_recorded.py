"""Recorded package versions, the load check, Stan upgrades and old R defaults.

Ports of the testthat blocks of ``test-reproducibility_check.R`` on the helpers of
``metacheck.repro.recorded``. R, Docker and the network are replaced by fakes.
"""

from __future__ import annotations

import gzip
import subprocess
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from metacheck.repro import recorded as rec
from metacheck.repro.docker import _REPOS_LINES
from metacheck.repro.recorded import (
    _repro_date_versions,
    _repro_highest_version,
    _repro_install_order,
    _repro_load_check,
    _repro_load_check_apply,
    _repro_load_check_lines,
    _repro_load_error,
    _repro_old_default_lines,
    _repro_old_defaults,
    _repro_old_defaults_text,
    _repro_recorded_advice,
    _repro_recorded_counts,
    _repro_recorded_plan,
    _repro_recorded_preview,
    _repro_recorded_report,
    _repro_recorded_summary,
    _repro_recorded_warning,
    _repro_run_after_lines,
    _repro_upgrade_stan,
    _repro_upgrade_stan_files,
    _repro_upgrade_stan_in_r,
)


def _df(**cols: Any) -> pd.DataFrame:
    """A data frame of text columns (a scalar is recycled)."""
    n = max((len(v) for v in cols.values() if isinstance(v, list)), default=1)
    return pd.DataFrame(
        {
            k: pd.Series(v if isinstance(v, list) else [v] * n, dtype="string")
            for k, v in cols.items()
        }
    )


def _deps(packages: list[str], source: Any, base: Any) -> pd.DataFrame:
    n = len(packages)
    return pd.DataFrame(
        {
            "package": pd.Series(packages, dtype="string"),
            "source": pd.Series(
                source if isinstance(source, list) else [source] * n, dtype="string"
            ),
            "ref": pd.Series([None] * n, dtype="string"),
            "base": pd.Series(base if isinstance(base, list) else [base] * n, dtype="boolean"),
        }
    )


def _na(s: Any) -> list[Any]:
    return [None if pd.isna(v) else v for v in list(s)]


# recorded versions --------------------------------------------------------------------


def test_highest_version_and_same_version_compare_versions() -> None:
    """.repro_highest_version and .repro_same_version compare versions"""
    from metacheck.repro.core import _repro_same_version

    assert _repro_highest_version(["1.9", "1.10", None, "junk"]) == "1.10"
    assert _repro_highest_version(["1.1-21", "1.1-9"]) == "1.1-21"
    assert _repro_highest_version([None, "junk"]) is None
    assert _repro_highest_version([]) is None
    # a version that only differs in its separators does not replace the first
    assert _repro_highest_version(["1.1-21", "1.1.21"]) == "1.1-21"
    assert _repro_same_version("1.1-21", "1.1.21")
    assert not _repro_same_version("1.1-21", "1.1.22")
    assert not _repro_same_version(None, "1.0")


def test_recorded_plan_keeps_only_the_packages_the_scripts_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.repro_recorded_plan keeps only the packages the scripts load"""
    deps = _deps(
        ["dplyr", "lme4", "Matrix", "papaja", "limma", "praise", "groundhog", "stats"],
        ["cran", "cran", "base", "cran", "cran", "cran", "cran", "base"],
        [False, False, True, False, False, False, False, True],
    )
    vp = {
        "renv_packages": _df(
            file_name=["a/renv.lock", "b/renv.lock", "a/renv.lock", "a/renv.lock", "a/renv.lock"],
            package=["dplyr", "dplyr", "papaja", "unused", "Matrix"],
            version=["1.0.0", "1.1.3", "0.1.2", "9.9.9", "1.5-1"],
            source=["Repository", "Repository", "GitHub", "Repository", "Repository"],
            remote=[None, None, "crsh/papaja@abc", None, None],
        ),
        "session_packages": _df(
            file_name="si.txt",
            package=["dplyr", "lme4", "limma"],
            version=["0.8.0", "1.1-21", "3.50.0"],
            source=[None, None, "Bioconductor"],
            remote=None,
        ),
        "pin_dates": _df(mechanism="groundhog", date=["2020-01-01", "2021-03-01"]),
    }
    looked_up: list[tuple[list[str], str]] = []

    def fake_dates(pkgs: list[str], date: str) -> dict[str, str | None]:
        looked_up.append((list(pkgs), date))
        return dict.fromkeys(pkgs, "1.0.0")

    monkeypatch.setattr(rec, "_repro_date_versions", fake_dates)
    p = _repro_recorded_plan(deps, vp)
    assert p is not None
    d = p["deps"]
    by = d.set_index("package")
    # no base package, no package manager, nothing that is only listed
    assert set(d["package"]) == {"dplyr", "lme4", "papaja", "limma", "praise"}
    # the highest version of the two lockfiles, not the session record's
    assert by.loc["dplyr", "version"] == "1.1.3"
    assert by.loc["dplyr", "recorded_from"] == "b/renv.lock"
    assert by.loc["lme4", "version"] == "1.1-21"
    # where it came from decides how it is installed
    assert by.loc["papaja", "source"] == "github"
    assert by.loc["papaja", "ref"] == "crsh/papaja@abc"
    assert by.loc["limma", "source"] == "bioc"
    # the latest date, for what no file lists (and not for a base package)
    assert looked_up == [(["praise"], "2021-03-01")]
    assert by.loc["praise", "version"] == "1.0.0"
    assert by.loc["praise", "recorded_from"] == "date 2021-03-01"
    # R's own packages keep R's version, and are named
    assert list(p["kept_with_r"]["package"]) == ["Matrix"]
    assert list(p["kept_with_r"]["version"]) == ["1.5-1"]
    assert p["dates"] == ["2021-03-01"]
    assert p["date_failed"] is False


def test_recorded_plan_returns_none_when_nothing_records_a_loaded_package() -> None:
    """.repro_recorded_plan returns NULL when nothing records a loaded package"""
    deps = _deps(["dplyr", "groundhog"], "cran", False)

    def none(pkg: str) -> dict[str, Any]:
        return {
            "renv_packages": _df(
                file_name="renv.lock", package=pkg, version="1.0", source="Repository"
            ),
            "session_packages": None,
            "pin_dates": None,
        }

    assert _repro_recorded_plan(deps, none("other")) is None
    assert _repro_recorded_plan(deps, none("groundhog")) is None
    assert _repro_recorded_plan(deps, None) is None
    assert _repro_recorded_plan(None, none("dplyr")) is None
    # a record without a remote column, saved by an earlier version
    plan = _repro_recorded_plan(deps, none("dplyr"))
    assert plan is not None
    assert list(plan["deps"]["version"]) == ["1.0"]


def test_recorded_plan_warns_when_a_dates_versions_cannot_be_looked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.repro_recorded_plan warns when a date's versions cannot be looked up"""
    deps = _deps(["dplyr"], "cran", False)
    vp = {
        "renv_packages": None,
        "session_packages": None,
        "pin_dates": _df(mechanism="groundhog", date="2010-01-01"),
    }

    def failing(pkgs: list[str], date: str) -> rec._NamedVector:
        out = rec._NamedVector(dict.fromkeys(pkgs))
        out.attrs["failed"] = True
        return out

    monkeypatch.setattr(rec, "_repro_date_versions", failing)
    with pytest.warns(UserWarning, match="2010-01-01"):
        assert _repro_recorded_plan(deps, vp) is None
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _repro_recorded_plan(deps, vp, use_dates=False) is None


def test_date_versions_reads_a_dated_copy_of_cran(monkeypatch: pytest.MonkeyPatch) -> None:
    """.repro_date_versions reads a dated copy of CRAN"""
    index = (
        "Package: R6\nVersion: 2.5.0\nDepends: R (>= 3.0)\n\n"
        "Package: R6\nVersion: 2.4.1\n\n"
        "Package: dplyr\nVersion: 1.0.4\nImports: a,\n  b\n"
    )
    seen: list[str] = []

    def fetch(url: str) -> bytes | None:
        seen.append(url)
        if "/2021-03-01/" in url and url.endswith("PACKAGES.gz"):
            return gzip.compress(index.encode())
        return None

    monkeypatch.setattr(rec, "_fetch_packages_file", fetch)
    v = _repro_date_versions(["R6", "notapackage"], "2021-03-01")
    assert v["R6"] == "2.5.0"
    assert v["notapackage"] is None
    assert v.attrs["failed"] is False  # type: ignore[attr-defined]
    assert seen[0] == "https://packagemanager.posit.co/cran/2021-03-01/src/contrib/PACKAGES.gz"
    # before the first copy
    early = _repro_date_versions(["R6"], "2016-01-01")
    assert early.attrs["failed"] is True  # type: ignore[attr-defined]
    assert early["R6"] is None
    # the plain PACKAGES file is read when there is no .gz
    monkeypatch.setattr(
        rec, "_fetch_packages_file", lambda url: None if url.endswith(".gz") else index.encode()
    )
    assert _repro_date_versions(["dplyr"], "2021-03-01")["dplyr"] == "1.0.4"


def _recorded_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    recorded = {
        "deps": pd.DataFrame(
            {
                "package": pd.Series(["a", "b", "c", "d"], dtype="string"),
                "source": pd.Series(["cran", "cran", "cran", "bioc"], dtype="string"),
                "ref": pd.Series([None] * 4, dtype="string"),
                "base": pd.Series([False] * 4, dtype="boolean"),
                "version": pd.Series(["1.0", "2.0", None, "3.0"], dtype="string"),
                "recorded_from": pd.Series(["renv.lock"] * 4, dtype="string"),
            }
        ),
        "kept_with_r": _df(package="Matrix", version="1.5-1"),
        "dates": [],
        "date_failed": False,
    }
    vp = {
        "r_versions": ["4.3.1"],
        "renv_packages": _df(
            file_name="renv.lock", package="a", version="1.0", source="Repository"
        ),
        "session_packages": None,
        "pin_dates": None,
    }
    return recorded, vp


def test_the_warning_and_report_state_that_the_r_version_is_not_changed() -> None:
    """the warning and report state that the R version is not changed"""
    recorded, vp = _recorded_fixture()
    w = _repro_recorded_warning(recorded, vp, "docker")
    assert "3 of the 4 packages" in w
    assert "The R version is not changed" in w
    assert "R 4.3.1" in w
    assert "long time" in w
    assert "depend on" in w
    assert "Matrix" in w
    assert "these get their current version: d" in w
    assert "1 loaded package is not listed and gets the current version." in w
    assert "R version is not changed" not in _repro_recorded_warning(recorded, vp, "docker", True)

    results = pd.DataFrame(
        {
            "package": pd.Series(["a", "b", "c", "d"], dtype="string"),
            "source": pd.Series(["cran"] * 4, dtype="string"),
            "installed": pd.Series([True, True, True, False], dtype="boolean"),
            "message": pd.Series(["", "", "", "no such package"], dtype="string"),
            "version_recorded": pd.Series(["1.0", "2.0", None, "3.0"], dtype="string"),
            "version_installed": pd.Series(["1.0", "2.5", "9.9", None], dtype="string"),
            "version_note": pd.Series(["", "could not build", "", ""], dtype="string"),
        }
    )
    counts = _repro_recorded_counts(recorded, results)
    assert counts["exact"] == [True, False, False]
    assert counts["replaced"] == [False, True, False]
    assert counts["failed"] == [False, False, True]
    report = _repro_recorded_report(recorded, results, vp, "process")
    assert report is not None
    assert report[0] == "#### Recorded package versions"
    assert len(report) == 3
    text = "\n".join([report[0], report[1], report[2].data.to_string()])
    assert "1 was installed at the recorded version, 1 got another version, and 1 could not" in text
    assert "not an exact reproduction" in text
    assert "could not build" in text
    assert "no such package" in text
    assert "also load c" in text
    assert "The R version is not changed" in text
    summary = _repro_recorded_summary(recorded, results)
    assert summary is not None and "1 of the 3 packages" in summary
    assert _repro_recorded_summary(None, results) is None
    assert _repro_recorded_report(recorded, None, vp, "process") is None


def test_recorded_report_table_lists_the_packages_that_are_not_exact() -> None:
    """the report's table names the recorded and the installed version, and why"""
    recorded, vp = _recorded_fixture()
    results = pd.DataFrame(
        {
            "package": pd.Series(["a", "b", "d"], dtype="string"),
            "installed": pd.Series([True, True, False], dtype="boolean"),
            "message": pd.Series(["", "", "no such package"], dtype="string"),
            "version_installed": pd.Series(["1.0", "2.5", None], dtype="string"),
            "version_note": pd.Series(["", "could not build", ""], dtype="string"),
        }
    )
    report = _repro_recorded_report(recorded, results, vp, "docker")
    assert report is not None and len(report) == 3
    table = report[2].data  # the ReportTable's data frame
    assert list(table.columns) == ["Package", "Recorded", "Installed", "Why"]
    assert list(table["Package"]) == ["b", "d"]
    assert list(table["Installed"]) == ["2.5", "not installed"]
    assert list(table["Why"]) == ["could not build", "no such package"]


def test_recorded_preview_says_what_a_run_would_install() -> None:
    """.repro_recorded_preview says what a run would install"""
    deps = _deps(["a", "b"], "cran", False)
    vp: dict[str, Any] = {
        "renv_packages": _df(
            file_name="renv.lock", package="a", version="1.0", source="Repository"
        ),
        "session_packages": None,
        "pin_dates": None,
    }
    text = _repro_recorded_preview(deps, vp)
    assert text is not None
    assert "would install 1 package " in text
    assert "would not change the R version" in text
    assert "an `renv.lock` file" in text
    vp = {
        "renv_packages": None,
        "session_packages": None,
        "pin_dates": _df(mechanism="checkpoint", date="2022-01-01"),
    }
    text = _repro_recorded_preview(deps, vp)
    assert text is not None and "would install 2 packages" in text
    assert "the date 2022-01-01 in a `groundhog` or `checkpoint` call" in text
    assert _repro_recorded_preview(deps, {}) is None


def test_recorded_advice_recommends_recorded_versions_after_a_failed_run() -> None:
    """a failed run with recorded versions that were not used gets the recommendation"""
    from metacheck.utils import options

    deps = _deps(["a"], "cran", False)
    vp: dict[str, Any] = {
        "renv_packages": _df(
            file_name="renv.lock", package="a", version="1.0", source="Repository"
        ),
    }
    failed = _df(file_name="s.R", outcome="errored")
    ok = _df(file_name="s.R", outcome="ran_ok")
    advice = _repro_recorded_advice(deps, vp, failed)
    assert advice is not None
    assert "`recorded_versions = TRUE`" in advice
    assert "shared server" not in advice
    assert _repro_recorded_advice(deps, vp, ok) is None
    assert _repro_recorded_advice(deps, vp, None) is None
    assert _repro_recorded_advice(deps, {}, failed) is None
    old = options({"metacheck.keys.shared": True})
    try:
        shared = _repro_recorded_advice(deps, vp, failed)
    finally:
        options({"metacheck.keys.shared": old.get("metacheck.keys.shared")})
    assert shared is not None and "not implemented on this shared server" in shared


# the load check -----------------------------------------------------------------------


def test_install_order_installs_current_versions_before_recorded_ones() -> None:
    """.repro_install_order installs current versions before recorded ones"""
    deps = _df(
        package=["a", "b", "c", "d"],
        source=["cran", "cran", "bioc", "github"],
        version=["1.0", None, "3.1", "2.0"],
    )
    assert list(_repro_install_order(deps)["package"]) == ["b", "c", "a", "d"]


def test_the_load_check_script_has_the_repos_lines_and_works_from_r_3_6() -> None:
    """the load check script parses and runs from R 3.6"""
    lines = _repro_load_check_lines("/rlib", ["a", "b"], ["a"], "/sandbox/results.rds")
    assert lines[:5] == [
        'lib <- "/rlib"',
        'pkgs <- c("a", "b")',
        'recorded <- "a"',
        'results_file <- "/sandbox/results.rds"',
        ".libPaths(c(lib, .libPaths()))",
    ]
    assert lines[5 : 5 + len(_REPOS_LINES)] == _REPOS_LINES
    # data.frame() makes factors before R 4.0
    assert sum("stringsAsFactors = FALSE" in ln for ln in lines) == 2
    assert not any("deparse1" in ln or "|>" in ln for ln in lines)
    # a "\n" of the script, not a line break
    assert any('collapse = "\\n"' in ln for ln in lines)
    assert not any("\n" in ln for ln in lines)
    # nothing recorded: character(0)
    assert _repro_load_check_lines("/rlib", [], [], "r.rds")[1:3] == [
        "pkgs <- character(0)",
        "recorded <- character(0)",
    ]


def _install_results(lib: Path) -> pd.DataFrame:
    for pkg in ("mcOld", "mcFine", "mcBroken"):
        (lib / pkg).mkdir(parents=True)
        (lib / pkg / "DESCRIPTION").write_text(f"Package: {pkg}\n")
    return pd.DataFrame(
        {
            "package": pd.Series(["mcNew", "mcOld", "mcFine", "mcBroken"], dtype="string"),
            "source": pd.Series(["cran"] * 4, dtype="string"),
            "installed": pd.Series([True] * 4, dtype="boolean"),
            "message": pd.Series([""] * 4, dtype="string"),
            "category": pd.Series([None] * 4, dtype="string"),
            "version_recorded": pd.Series([None, "1.0", "1.5", "1.0"], dtype="string"),
            "version_installed": pd.Series(["1.0", "1.0", "1.5", "1.0"], dtype="string"),
            "version_note": pd.Series([""] * 4, dtype="string"),
        }
    )


_FOUND = {
    "set_aside": _df(
        package="mcOld",
        because="mcNew",
        version_installed="2.0",
        note="the recorded version keeps mcNew from loading on R 4.5.1, so the current version was used",
    ),
    "not_loading": _df(
        package="mcBroken", message="the package does not load: Error: broken on this R"
    ),
}


def test_load_check_replaces_a_recorded_version_that_keeps_a_package_from_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.repro_load_check replaces a recorded version that keeps a package from loading"""
    lib = tmp_path / "lib"
    ir = _install_results(lib)
    ran: list[str] = []

    def fake_run(code: str, **kwargs: Any) -> None:
        ran.append(code)
        assert kwargs["timeout"] == 300

    monkeypatch.setattr(rec, "_run_r", fake_run)
    monkeypatch.setattr(rec, "_read_found", lambda path: _FOUND)
    out = _repro_load_check(ir, lib, timeout=300)

    # the script ran in a process, on the library of this machine, and saves its results
    assert len(ran) == 1
    # with "/" between folders, so a Windows path is no R escape
    assert f'lib <- "{lib.as_posix()}"' in ran[0]
    assert 'pkgs <- c("mcNew", "mcOld", "mcFine", "mcBroken")' in ran[0]
    assert 'recorded <- c("mcOld", "mcFine", "mcBroken")' in ran[0]
    assert 'results_file <- "' in ran[0] and ran[0].count("results.rds") == 1
    # the current version of the package the error names, and the note says why
    assert out["version_installed"].iloc[1] == "2.0"
    assert "keeps mcNew from loading" in out["version_note"].iloc[1]
    # a recorded version that is not at fault stays
    assert out["version_installed"].iloc[2] == "1.5"
    assert out["version_note"].iloc[2] == ""
    # a package that does not load counts as not installed
    assert not out["installed"].iloc[3]
    assert "the package does not load" in out["message"].iloc[3]
    assert "broken on this R" in out["message"].iloc[3]
    assert out["category"].iloc[3] == "other"
    assert all(out["installed"].iloc[:3])
    # the input is not changed
    assert all(ir["installed"])


def test_load_check_in_docker_runs_the_script_in_a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.repro_load_check in a container: the library and the script are mounted"""
    lib = tmp_path / "lib"
    ir = _install_results(lib)
    calls: list[list[str]] = []
    stopped: list[str] = []
    scripts: list[str] = []

    def fake_docker(args: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(args))
        mount = next(a for a in args if a.endswith(":/sandbox"))
        # the host folder, which on Windows has a drive's ":" of its own
        scripts.append((Path(mount[: -len(":/sandbox")]) / "check.R").read_text())
        assert kwargs["timeout"] == 1800
        return {"status": 0, "timeout": False, "stdout": ""}

    monkeypatch.setattr(rec, "_docker", fake_docker)
    monkeypatch.setattr(rec, "_repro_docker_stop", stopped.append)
    monkeypatch.setattr(rec, "_read_found", lambda path: _FOUND)
    out = _repro_load_check(ir, lib, image="img:1")
    args = calls[0]
    assert args[:2] == ["run", "--rm"]
    assert args[-3:] == ["img:1", "Rscript", "/sandbox/check.R"]
    assert "--cap-drop" in args and "--network" not in args
    assert f"{lib}:/rlib" in args
    assert 'lib <- "/rlib"' in scripts[0]
    assert 'results_file <- "/sandbox/results.rds"' in scripts[0]
    assert stopped == []
    assert not out["installed"].iloc[3]

    # a container that cannot be started is stopped, and the check did not finish
    monkeypatch.setattr(rec, "_docker", lambda args, **kw: OSError("no docker"))
    monkeypatch.setattr(rec, "_read_found", lambda path: None)
    with pytest.warns(UserWarning, match="did not finish"):
        again = _repro_load_check(ir, lib, image="img:1")
    assert len(stopped) == 1 and stopped[0].startswith("repro_")
    assert again is ir


def test_load_check_does_nothing_without_a_recorded_version_in_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.repro_load_check does nothing without a recorded version in the library"""
    ir = pd.DataFrame(
        {
            "package": pd.Series(["a"], dtype="string"),
            "source": pd.Series(["cran"], dtype="string"),
            "installed": pd.Series([True], dtype="boolean"),
            "message": pd.Series([""], dtype="string"),
            "category": pd.Series([None], dtype="string"),
            "version_recorded": pd.Series(["1.0"], dtype="string"),
            "version_installed": pd.Series(["1.0"], dtype="string"),
            "version_note": pd.Series([""], dtype="string"),
        }
    )

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("should not run")

    monkeypatch.setattr(rec, "_run_r", boom)
    monkeypatch.setattr(rec, "_docker", boom)
    monkeypatch.setattr(rec, "_repro_load_check_lines", boom)
    assert _repro_load_check(ir, tmp_path).equals(ir)
    # in the library, but not at the recorded version
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "DESCRIPTION").write_text("Package: a\n")
    other = ir.copy()
    other["version_installed"] = pd.Series(["2.0"], dtype="string")
    assert _repro_load_check(other, tmp_path).equals(other)
    # the check could not find its results
    monkeypatch.undo()
    monkeypatch.setattr(rec, "_run_r", lambda *a, **k: None)
    with pytest.warns(UserWarning, match="did not finish"):
        assert _repro_load_check(ir, tmp_path).equals(ir)


def test_load_check_apply_puts_the_findings_in_the_install_results(tmp_path: Path) -> None:
    """a package of the findings that is not in the results is skipped"""
    ir = _install_results(tmp_path / "lib")
    found = {
        "set_aside": pd.concat(
            [
                _FOUND["set_aside"],
                _df(package="gone", because="x", version_installed="1", note="n"),
            ],
            ignore_index=True,
        ),
        "not_loading": None,
    }
    out = _repro_load_check_apply(ir, found)
    assert out["version_installed"].iloc[1] == "2.0"
    assert all(out["installed"])
    assert _repro_load_check_apply(ir, {"set_aside": None, "not_loading": None}).equals(ir)


def test_read_found_reads_the_results_of_the_load_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """the RDS the load check saves: two data frames, NULL when nothing happened"""
    from metacheck.datacheck import _files_rdata as rd

    def strs(*v: str) -> rd.RObject:
        return rd.RObject(rd.STRSXP, list(v))

    def frame(**cols: str) -> rd.RObject:
        return rd.RObject(
            rd.VECSXP,
            [strs(v) for v in cols.values()],
            {
                "names": strs(*cols),
                "class": strs("data.frame"),
                "row.names": rd.RObject(rd.INTSXP, np.array([-(2**31), -1], dtype=np.int32)),
            },
        )

    obj = rd.RObject(
        rd.VECSXP,
        [frame(package="mcBroken", message="the package does not load: boom"), None],
        {"names": strs("not_loading", "set_aside")},
    )
    monkeypatch.setattr(rd, "read_rds", lambda path: obj)
    found = rec._read_found("results.rds")
    assert found is not None
    assert found["set_aside"] is None
    assert list(found["not_loading"]["package"]) == ["mcBroken"]  # type: ignore[index]
    monkeypatch.setattr(rd, "read_rds", lambda path: (_ for _ in ()).throw(OSError("gone")))
    assert rec._read_found("results.rds") is None


def test_load_error_says_what_r_said(monkeypatch: pytest.MonkeyPatch) -> None:
    """a package that does not load is reported with what R said"""
    seen: list[str] = []

    def fake_run(code: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(code)
        return subprocess.CompletedProcess(
            [], 0, stdout="<<repro-load-error>>libfoo.so.2:\n  cannot open" + " x" * 200, stderr=""
        )

    monkeypatch.setattr(rec, "_run_r", fake_run)
    reason = _repro_load_error("mcNoLoad", "/tmp/lib")
    assert reason.startswith("libfoo.so.2: cannot open")
    assert len(reason) == 300
    assert 'pkg <- "mcNoLoad"' in seen[0] and "loadNamespace(pkg, lib.loc" in seen[0]
    # a package that loads has nothing to report
    monkeypatch.setattr(
        rec,
        "_run_r",
        lambda code, **k: subprocess.CompletedProcess(
            [], 0, stdout="<<repro-load-error>>", stderr=""
        ),
    )
    assert _repro_load_error("mcLoads", "/tmp/lib") == ""
    monkeypatch.setattr(rec, "_run_r", lambda code, **k: None)
    assert _repro_load_error("mcLoads", "/tmp/lib") == ""


# scripts that load others first, and old defaults --------------------------------------


def test_run_after_lines_loads_a_script_from_its_own_folder(tmp_path: Path) -> None:
    """.repro_run_after_lines loads a script from its own folder (#568)"""
    root = str(tmp_path)
    run_tbl = _df(
        file_name=["use.R", "def.R", "top.R"],
        script_path=[f"{root}/a/use.R", f"{root}/b/c/def.R", f"{root}/top.R"],
        run_dir=[f"{root}/a", f"{root}/b", root],
    )
    out = _repro_run_after_lines({"use.R": ["def.R"], "top.R": ["use.R"]}, run_tbl, root)
    assert out is not None
    assert list(out) == ["use.R", "top.R"]
    assert len(out["use.R"]) == 1
    assert 'setwd("../b")' in out["use.R"][0]
    assert 'source("c/def.R", local = globalenv())' in out["use.R"][0]
    assert 'setwd("a")' in out["top.R"][0]
    assert 'source("use.R", local = globalenv())' in out["top.R"][0]
    assert out["top.R"][0].endswith(
        "  # [reproducibility_check: load the script that defines what this one uses]"
    )
    assert _repro_run_after_lines({}, run_tbl, root) is None
    assert _repro_run_after_lines(None, run_tbl, root) is None


def test_old_default_lines_give_r_its_old_behaviour(rscript: str, tmp_path: Path) -> None:
    """the lines give R's old behaviour, also inside packages; in a session of its own (#568)"""
    script = tmp_path / "s.R"
    script.write_text(
        "\n".join(
            [
                *_repro_old_default_lines(["factors"]),
                'cat(class(read.csv(text = "a\\nx")$a), class(data.frame(a = "u")$a),',
                '    class(data.frame(a = "u", stringsAsFactors = FALSE)$a))',
            ]
        )
    )
    res = subprocess.run([rscript, str(script)], capture_output=True, text=True, check=False)
    assert res.stdout.strip() == "factor factor character"


def test_old_defaults_from_the_error_of_a_script() -> None:
    """a script that stops on a changed default of R runs again with the old one (#568)"""
    rr = _df(
        file_name=["prep.R", "map.R", "other.R", "ok.R"],
        outcome=["errored", "errored", "errored", "ran_ok"],
        error=[
            'Error in relevel.default(dframe1$Treatment, "NoFire") : '
            "'relevel' only for (unordered) factors",
            "Error in type.convert.default(data[[i]], as.is = as.is[i], dec = dec, : "
            "invalid multibyte string at '<b0>10'",
            "Error: object 'x' not found",
            "",
        ],
        stderr="",
    )
    od = _repro_old_defaults(rr)
    assert od == {"prep.R": ["factors"], "map.R": ["latin1"]}
    assert _repro_old_defaults(rr.iloc[2:4]) == {}
    assert _repro_old_defaults(None) == {}
    # a script that did not stop, or stopped for another reason, is left alone
    lines = _repro_old_default_lines(["factors", "latin1"])
    assert len(lines) == 2
    assert any('options(encoding = "latin1")' in ln for ln in lines)
    assert lines[0].startswith("local({ for (w in list(")
    assert lines[0].endswith("  # [reproducibility_check: text read as factors, as before R 4.0]")
    assert _repro_old_default_lines(["latin1"]) == lines[1:]
    assert _repro_old_default_lines([]) == []
    txt = _repro_old_defaults_text(od, ran_ok=["prep.R"])
    assert txt is not None
    assert "2 scripts ran again with a default R had when the code was written" in txt
    assert "(text read as factors, as R did before version 4.0; data files read as Latin-1" in txt
    assert "because they, or a script that writes a file they read, stopped" in txt
    assert "It then ran to the end: prep.R." in txt
    assert '`stringsAsFactors = TRUE` or `fileEncoding = "latin1"`' in txt
    assert "prep.R, map.R" in txt
    assert _repro_old_defaults_text({}) is None
    one = _repro_old_defaults_text({"prep.R": ["factors"]})
    assert one is not None
    assert "1 script ran again" in one
    assert "because it, or a script that writes a file it reads, stopped" in one
    assert "then ran" not in one


def test_four_more_messages_of_text_that_is_no_longer_a_factor_start_the_old_default() -> None:
    """four more messages of text that is no longer a factor start the old default (#578, item 5)"""

    def rr(m: str) -> pd.DataFrame:
        return _df(file_name="s.R", outcome="errored", error="", stderr=m)

    dup = "Error in `levels<-`(x, value = l) : factor level [3] is duplicated"
    assert _repro_old_defaults(rr(dup))["s.R"] == ["factors"]
    droplevels = (
        "Error in UseMethod(\"droplevels\") : \n  no applicable method for 'droplevels' "
        'applied to an object of class "character"'
    )
    assert _repro_old_defaults(rr(droplevels))["s.R"] == ["factors"]
    std = "Error in standardize(m) : The object area must be either numeric or a factor."
    assert _repro_old_defaults(rr(std))["s.R"] == ["factors"]
    xlim = "Error in plot.window(...) : need finite 'xlim' values"
    warn = (
        "\nIn addition: Warning message:\nIn xy.coords(x, y, xlabel, ylabel, log) :\n"
        "  NAs introduced by coercion"
    )
    assert _repro_old_defaults(rr(xlim + warn))["s.R"] == ["factors"]
    # without the warning, it has other causes
    assert _repro_old_defaults(rr(xlim)) == {}


# Stan ---------------------------------------------------------------------------------


def test_stan_code_from_before_2_33_gets_the_newer_syntax(tmp_path: Path) -> None:
    """Stan code from before 2.33 gets the newer syntax (#568)"""
    old = [
        "# Stan code for degree days", "data {", "  int<lower=0> N;",
        "  real<lower=0, upper=0.2> r_hat[3];", "  vector[ndays] Tn[N];",
        "  int loc[N];   // # a note", "}", "model {", "  y ~ normal(mu, sigma);",
        "  increment_log_prob(normal_log(y, mu, sigma));",
        "  real a <- fabs(b);", "  target += poisson_log(k, lambda);", "}",
        "functions { real f(real[] x, int[,] m) { return x[1]; } }", "#include other.stan",
    ]  # fmt: skip
    up = _repro_upgrade_stan(old)
    txt = up["txt"]
    assert txt[0] == "// Stan code for degree days"
    assert txt[3] == "  array[3] real<lower=0, upper=0.2> r_hat;"
    assert txt[4] == "  array[N] vector[ndays] Tn;"
    assert txt[5] == "  array[N] int loc;   // // a note"
    assert txt[2] == "  int<lower=0> N;"
    assert txt[8] == "  y ~ normal(mu, sigma);"
    assert txt[9] == "  target += normal_lpdf(y | mu, sigma);"
    assert txt[10] == "  real a = abs(b);"
    assert txt[11] == "  target += poisson_lpmf(k | lambda);"
    assert txt[13] == "functions { real f(array[] real x, array[,] int m) { return x[1]; } }"
    assert txt[14] == "#include other.stan"
    assert up["changes"] == [
        "# comments",
        "<- assignments",
        "increment_log_prob()",
        "fabs()",
        "_log() functions",
        "array declarations",
        "array arguments",
    ]
    # new syntax stays as it is
    new = [
        "data {",
        "  array[N] int<lower=0> y;",
        "}",
        "model { target += normal_lpdf(y | 0, 1); }",
    ]
    assert _repro_upgrade_stan(new)["txt"] == new
    assert _repro_upgrade_stan(new)["changes"] == []
    assert _repro_upgrade_stan([]) == {"txt": [], "changes": []}
    root = tmp_path
    (root / "m").mkdir()
    (root / "m" / "DegDay.stan").write_text("\n".join(old) + "\n")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "x.stan").write_text("y[N] <- 1;\n")
    rows = _repro_upgrade_stan_files(root)
    assert rows is not None
    assert list(rows.columns) == ["file_name", "change_type", "detail"]
    assert list(rows["file_name"]) == ["m/DegDay.stan"]
    assert list(rows["change_type"]) == ["stan_syntax"]
    assert "array declarations" in rows["detail"].iloc[0]
    assert rows["detail"].iloc[0].startswith("Stan from before 2.33: # comments, ")
    assert (root / "m" / "DegDay.stan").read_text().split("\n")[0] == "// Stan code for degree days"
    assert _repro_upgrade_stan_files(root) is None  # already converted
    assert _repro_upgrade_stan_files(tmp_path / "none") is None


def test_stan_puts_the_vertical_bar_in_cdf_and_lpdf_calls() -> None:
    """.repro_upgrade_stan puts the vertical bar in _cdf() and _lpdf() calls (#568)"""
    stan = [
        "functions {",
        "  real foo_lpdf(real y, real mu) { return normal_lpdf(y | mu, 1); }",
        "}",
        "model {",
        "  p_zero[i] = logistic_cdf(cutoff, log(growth[i]), 1);",
        "  target += foo_lpdf(y[i], f(a, b));",
        "  target += normal_lcdf(x | 0, 1) + normal_lccdf(z, 0, 1);",
        "}",
    ]
    out = _repro_upgrade_stan(stan)["txt"]
    assert out[1] == stan[1]  # the definition keeps its commas
    assert out[4].strip() == "p_zero[i] = logistic_cdf(cutoff | log(growth[i]), 1);"
    assert out[5].strip() == "target += foo_lpdf(y[i] | f(a, b));"
    assert out[6].strip() == "target += normal_lcdf(x | 0, 1) + normal_lccdf(z | 0, 1);"


def test_stan_programs_held_in_r_code_get_the_newer_syntax() -> None:
    """Stan programs held in R code get the newer syntax, and other R text is left alone (#568)"""
    r = [
        "library(rstan)",
        'write("data{',
        "  int<lower=0> N;",
        "  int<lower=0> y[N];  # counts",
        "}",
        "parameters{ real mu; }",
        'model{ y ~ poisson(exp(mu)); }",',
        '      "m.stan")',
        'x <- "a text with a # and model { inside, on one line"',
        'note <- "not stan:',
        "  y[N] means something else here",
        '"',
    ]
    up = _repro_upgrade_stan_in_r(r)
    assert len(up["changes"]) > 0
    assert up["txt"][3].strip() == "array[N] int<lower=0> y;  // counts"
    # R code and the other texts unchanged
    for i in (0, 1, *range(6, 12)):
        assert up["txt"][i] == r[i]
    # model_code in single quotes
    r2 = [
        "m <- stan_model(model_code = 'data {",
        "  int N;",
        "  real x[N];",
        "}",
        "parameters { real b; }",
        "model { x ~ normal(b, 1); }",
        "')",
    ]
    up2 = _repro_upgrade_stan_in_r(r2)
    assert up2["txt"][2].strip() == "array[N] real x;"
    # a distribution with a log link after ~ stays; the old density name in a call is converted
    st = _repro_upgrade_stan(
        [
            "y ~ poisson_log(y_1);",
            "z ~ neg_binomial_2_log(eta, phi);",
            "target += poisson_log(n, lambda);",
            "w ~ normal(0, 1);",
        ]
    )["txt"]
    assert st[:2] == ["y ~ poisson_log(y_1);", "z ~ neg_binomial_2_log(eta, phi);"]
    assert st[2] == "target += poisson_lpmf(n | lambda);"
    # no Stan, nothing changed
    assert _repro_upgrade_stan_in_r(['a <- "x', 'y"'])["changes"] == []
    assert _repro_upgrade_stan_in_r([])["changes"] == []


def test_stan_comments_vector_and_function_names() -> None:
    """a few more branches of the Stan update: _cdf_log, get_lp, nested calls"""
    out = _repro_upgrade_stan(
        [
            "  lp = get_lp();",
            "  x = normal_cdf_log(y, 0, 1) + exponential_ccdf_log(y, 2);",
            "  target += foo_lpdf(a, bar_lpdf(b, c));",
            "  y ~ normal(0, 1); // keep // here",
            '  print("a#b"); #include "x.stan"',
        ]
    )
    assert out["txt"][0] == "  lp = target();"
    assert out["txt"][1] == "  x = normal_lcdf(y | 0, 1) + exponential_lccdf(y | 2);"
    assert out["txt"][2] == "  target += foo_lpdf(a | bar_lpdf(b | c));"
    assert out["txt"][3] == "  y ~ normal(0, 1); // keep // here"
    assert out["txt"][4] == '  print("a//b"); #include "x.stan"'
    assert "get_lp()" in out["changes"] and "_cdf_log()" in out["changes"]
    assert "_ccdf_log()" in out["changes"]
