"""Tests of the code_check module (port of tests/testthat/test-module-code_check.R).

metacheck's tests give code_check its files through ``local_path`` (a fresh
``repo_check`` of a local directory) or a synthetic ``repo_check`` output.
The listing tests here chain onto a synthetic ``repo_check`` output of the
same directory (``tests/mod_code/helpers.py``: ``run_dir()``), so they do not
depend on the ``repo_check`` port; the ``local_path``/``local_only``/mocked
OSF tests that exercise ``repo_check`` itself run once it is ported.
"""

from __future__ import annotations

import copy
import json
import math
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleOutput, module_find, module_run
from tests.mod_code.helpers import (
    ROOT,
    cc_prev,
    cc_run,
    dir_listing,
    fake_repo_check,
    report_tables,
    report_text,
    run_dir,
)

FIXTURES = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures"
CODE_FILES = FIXTURES / "code_files"
BIBR12_PREPRINT = FIXTURES / "bibr12" / "preprint.json"
LEGACY_PREPRINT = FIXTURES / "formats" / "preprint.pdf.tei.xml"


def _has_repo_check() -> bool:
    try:
        module_find("repo_check")
    except Exception:
        return False
    return True


needs_repo_check = pytest.mark.skipif(
    not _has_repo_check(), reason="the repo_check module is not ported yet"
)


def row(mo: ModuleOutput, name: str) -> pd.Series:
    t = mo.table
    return t[t["file_name"] == name].iloc[0]


def st(mo: ModuleOutput) -> dict[str, Any]:
    """The first summary_table row as a dict."""
    return mo.summary_table.iloc[0].to_dict()


def write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def all_report(mo: ModuleOutput) -> str:
    """Report text plus the report tables' cells (R's report holds both as text)."""
    cells = [
        str(v) for t in report_tables(mo) for c in t.columns for v in t[c].tolist() if v is not None
    ]
    return report_text(mo) + "\n" + "\n".join(cells)


@pytest.fixture
def stub_repo_check(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Answer code_check's own ``module_run(paper, "repo_check", ...)`` with a
    listing of ``local_path`` (or no files), recording the call's arguments."""
    import pytacheck.module as mod

    calls: list[dict[str, Any]] = []
    real = mod.module_run

    def fake(paper: Any, module: Any, **kwargs: Any) -> Any:
        if module != "repo_check":
            return real(paper, module, **kwargs)
        calls.append(dict(kwargs))
        path = kwargs.get("local_path")
        table = dir_listing(path, pid=paper.paper_id) if path is not None else None
        return fake_repo_check(table, paper=paper)

    monkeypatch.setattr(mod, "module_run", fake)
    return calls


# ── no code ──────────────────────────────────────────────────────────────────


def test_offline_no_code(stub_repo_check: list[dict[str, Any]]) -> None:
    # R: "code_check offline" -- test_paper("no text") has no repositories
    paper = pc.test_paper(["no text"])
    mo = module_run(paper, "code_check")
    assert stub_repo_check == [{"local_only": False, "cache": False}]
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert {"file_name", "repo_url", "language"} <= set(mo.table.columns)
    exp = pd.DataFrame({"paper_id": [paper.paper_id], "code_n": [0.0]})
    pd.testing.assert_frame_equal(
        mo.summary_table.astype({"paper_id": object}), exp, check_dtype=False
    )
    assert "0" in mo.summary_text
    assert "0" in mo.report


def test_no_code_files_text() -> None:
    # R: "no code files" -- the summary counts every listed language
    mo = cc_run("nocode")
    exp = (
        "We found 0 R, 0 Python, 0 SAS, 0 SPSS, 0 Stata, 0 Mplus, 0 MATLAB, and 0 JASP code files."
    )
    assert mo.summary_text == exp
    assert mo.report == exp
    assert mo.summary_table["code_n"].tolist() == [0]
    assert mo.traffic_light == "na"


def test_multiple_papers_without_code() -> None:
    # R: "multiple paper issue" (metacheck#260)
    prev = cc_prev("paperlist_nocode")
    mo = module_run(prev, "code_check")
    assert set(mo.summary_table["paper_id"]) == set(prev.paper.names)
    assert mo.summary_table["code_n"].tolist() == [0, 0]


def test_null_paper_is_an_error() -> None:
    # R: paper_id(NULL) -- "paper must be a paper or paperlist object."
    prev = ModuleOutput(
        module="repo_check",
        title="Repository Check",
        section="general",
        table=dir_listing(ROOT / "tests" / "mod_code" / "fixtures" / "nocode"),
        paper=None,
    )
    with pytest.raises(Exception, match="paper must be a paper or paperlist object"):
        module_run(prev, "code_check")


# ── local directory listings (R: local_path) ─────────────────────────────────


def test_all_code_files_are_checked() -> None:
    # R: "all code files are checked (no per-repo cap)"
    local = FIXTURES / "demo" / "code"
    n_files = len(list(local.iterdir()))
    mo = run_dir(local)
    assert len(mo.table) == n_files
    assert st(mo)["code_n"] == n_files
    assert st(mo)["code_checked"] == n_files


@pytest.mark.parametrize("name", ["stata_latin1.do", "stata_utf16.do"])
def test_reads_non_utf8_files(name: str) -> None:
    mo = run_dir(CODE_FILES / name)
    assert mo.table["code_lines"].iloc[0] > 0


def test_local_listing_without_code(tmp_path: Path) -> None:
    write(tmp_path / "data.csv", ["x,y", "1,2"])
    mo = run_dir(tmp_path)
    assert mo.traffic_light == "na"
    assert mo.summary_table["code_n"].tolist() == [0]


def test_finds_code_files() -> None:
    mo = run_dir(CODE_FILES)
    assert mo.traffic_light == "yellow"
    assert st(mo)["code_n"] == 5
    assert {
        "analysis.R",
        "analysis_no_comments.R",
        "helper.R",
        "stata_latin1.do",
        "stata_utf16.do",
    } <= set(mo.table["file_name"])


def test_present_and_absent_loaded_files() -> None:
    mo = run_dir(CODE_FILES)
    # analysis.R loads data.csv, which IS in the fixture dir
    assert row(mo, "analysis.R")["loaded_files_missing"] == 0
    # analysis_no_comments.R loads missing_file.csv, which is NOT
    r = row(mo, "analysis_no_comments.R")
    assert r["loaded_files_missing"] == 1
    assert "missing_file.csv" in r["loaded_files_missing_names"]
    assert r["percentage_comment"] == 0


def test_records_loaded_packages() -> None:
    mo = run_dir(CODE_FILES)
    by_name = dict(zip(mo.table["file_name"], mo.table["packages"], strict=True))
    assert by_name["analysis.R"] == "dplyr, ggplot2"
    assert by_name["analysis_no_comments.R"] == "dplyr"
    assert by_name["stata_latin1.do"] == ""
    n_by_name = dict(zip(mo.table["file_name"], mo.table["packages_n"], strict=True))
    assert n_by_name["analysis.R"] == 2
    assert n_by_name["helper.R"] == 0
    assert st(mo)["code_packages_n"] == 2
    assert "2 distinct packages" in mo.summary_text
    assert "ggplot2" in report_text(mo)


# ── version pinning ──────────────────────────────────────────────────────────


def test_no_pinned_environment(tmp_path: Path) -> None:
    write(tmp_path / "analysis.R", ["library(dplyr)", "x <- 1"])
    mo = run_dir(tmp_path)
    assert st(mo)["code_version_pinned"] is False or not st(mo)["code_version_pinned"]
    assert "No pinned" in mo.summary_text
    assert "renv.lock" in report_text(mo)


def test_renv_lock(tmp_path: Path) -> None:
    write(tmp_path / "analysis.R", ["library(dplyr)", "x <- 1"])
    lock = {
        "R": {
            "Version": "4.3.1",
            "Repositories": [{"Name": "CRAN", "URL": "https://cran.rstudio.com"}],
        },
        "Packages": {
            "dplyr": {
                "Package": "dplyr",
                "Version": "1.1.3",
                "Source": "Repository",
                "Repository": "CRAN",
            }
        },
    }
    (tmp_path / "renv.lock").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    mo = run_dir(tmp_path)
    assert st(mo)["code_version_pinned"]
    assert "pinned R/package environment" in mo.summary_text
    txt = all_report(mo)
    assert "renv.lock" in txt
    assert "4.3.1" in txt
    assert "dplyr" in txt
    assert mo.version_pin["r_versions"] == ["4.3.1"]


def test_session_info(tmp_path: Path) -> None:
    write(tmp_path / "analysis.R", ["library(dplyr)", "x <- 1"])
    write(
        tmp_path / "sessionInfo.txt",
        [
            "R version 4.4.2 (2024-10-31)",
            "Platform: aarch64-apple-darwin20",
            "Running under: macOS Sequoia 15.1",
        ],
    )
    mo = run_dir(tmp_path)
    assert st(mo)["code_version_pinned"]
    txt = report_text(mo)
    assert "sessionInfo" in txt
    assert "4.4.2" in txt


def test_library_groundhog_is_not_a_pin(tmp_path: Path) -> None:
    write(tmp_path / "analysis.R", ["library(groundhog)", "x <- 1"])
    assert not st(run_dir(tmp_path))["code_version_pinned"]


@pytest.mark.parametrize(
    ("code", "mechanism"),
    [
        ('groundhog.library("dplyr", "2022-01-01")', "groundhog"),
        ('checkpoint("2022-01-01")', "checkpoint"),
    ],
)
def test_date_pins(tmp_path: Path, code: str, mechanism: str) -> None:
    write(tmp_path / "analysis.R", [code, "x <- 1"])
    mo = run_dir(tmp_path)
    assert st(mo)["code_version_pinned"]
    assert mechanism in report_text(mo)


def test_pins_are_per_paper() -> None:
    # the same file name in two papers: one paper's groundhog call must not pin the other
    mo = cc_run("paperlist_pins")
    assert dict(
        zip(mo.summary_table["paper_id"], mo.summary_table["code_version_pinned"], strict=True)
    ) == {
        "p1": True,
        "p2": False,
        "p3": True,
    }


# ── manifests ────────────────────────────────────────────────────────────────


def _manifests(d: Path) -> dict[str, Any]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.manifest.json"))
    }


def test_manifest_packages(tmp_path: Path) -> None:
    run_dir(CODE_FILES, manifest=str(tmp_path))
    mf = _manifests(tmp_path)
    assert list(mf) == ["p1.manifest.json"]
    assert set(mf["p1.manifest.json"]["code"]["packages"]) == {"dplyr", "ggplot2"}
    assert "ddi_mapping" in mf["p1.manifest.json"]["code"]


def test_manifest_json_path_and_existing_sections(tmp_path: Path) -> None:
    path = tmp_path / "paper.manifest.json"
    path.write_text(json.dumps({"files": [{"file_name": "a.csv"}]}), encoding="utf-8")
    run_dir(CODE_FILES, manifest=str(path))
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["files"] == [{"file_name": "a.csv"}]
    assert m["code"]["packages"] == ["dplyr", "ggplot2"]


def test_no_manifest_without_packages_or_failures(tmp_path: Path) -> None:
    run_dir(CODE_FILES / "stata_latin1.do", manifest=str(tmp_path))
    assert _manifests(tmp_path) == {}


def _failing_download(
    ok: dict[str, str] | None = None,
    error: str = "download failed (nothing was written)",
    with_paper_id: bool = True,
):
    """A download_repo_files() stand-in: files named in *ok* land at the given path,
    the rest fail (listed in the ``failed`` attribute)."""
    ok = ok or {}

    def download_repo_files(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        out = files.copy()
        out["file_location"] = [ok.get(n) for n in out["file_name"]]
        bad = out[[n not in ok for n in out["file_name"]]]
        failed = {
            "repo_url": bad["repo_url"].tolist(),
            "file_name": bad["file_name"].tolist(),
            "file_url": bad["file_url"].tolist(),
            "error": [error] * len(bad),
        }
        if with_paper_id and "paper_id" in bad.columns:
            failed["paper_id"] = bad["paper_id"].tolist()
        out.attrs["failed"] = pd.DataFrame(failed)
        return out

    return download_repo_files


def _remote_listing(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows).astype({"file_location": object})


def test_manifest_records_failed_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.archives.download as dl

    # the failed rows carry no paper_id: with one paper they are all its own
    monkeypatch.setattr(dl, "download_repo_files", _failing_download(with_paper_id=False))
    paper = pc.test_paper(["x"], url=["https://doi.org/10.5061/dryad.does-not-matter"])
    table = _remote_listing(
        [
            {
                "paper_id": paper.paper_id,
                "file_name": "analysis.R",
                "repo_url": "https://doi.org/10.5061/dryad.does-not-matter",
                "file_url": "https://datadryad.org/does-not-matter/analysis.R",
                "file_location": None,
            }
        ]
    )
    mo = module_run(fake_repo_check(table, paper=paper), "code_check", manifest=str(tmp_path))
    assert mo.traffic_light == "na"
    mf = _manifests(tmp_path)
    assert list(mf) == [f"{paper.paper_id}.manifest.json"]
    code = mf[f"{paper.paper_id}.manifest.json"]["code"]
    assert "packages" not in code
    failed = code["files_failed"]
    assert len(failed) == 1
    assert failed[0]["file_name"] == "analysis.R"
    assert failed[0]["file_url"] == "https://datadryad.org/does-not-matter/analysis.R"
    assert "download failed" in failed[0]["error"]


def test_manifest_split_per_paper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.archives.download as dl

    good = write(tmp_path / "src" / "good.R", ["library(ggplot2)", "x <- 1"])

    def download_repo_files(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        out = files.copy()
        is_x = (out["paper_id"] == "paperX").tolist()
        out["file_location"] = [str(good) if x else None for x in is_x]
        bad = out[[not x for x in is_x]]
        out.attrs["failed"] = pd.DataFrame(
            {
                "repo_url": bad["repo_url"].tolist(),
                "file_name": bad["file_name"].tolist(),
                "file_url": bad["file_url"].tolist(),
                "paper_id": bad["paper_id"].tolist(),
                "error": ["download failed (nothing was written)"] * len(bad),
            }
        )
        return out

    monkeypatch.setattr(dl, "download_repo_files", download_repo_files)
    table = _remote_listing(
        [
            {
                "paper_id": "paperX",
                "file_name": "good.R",
                "repo_url": "https://doi.org/10.5061/dryad.aaa",
                "file_url": "file:///does/not/exist/good.R",
                "file_location": None,
            },
            {
                "paper_id": "paperY",
                "file_name": "bad.R",
                "repo_url": "https://doi.org/10.5061/dryad.bbb",
                "file_url": "file:///does/not/exist/bad.R",
                "file_location": None,
            },
        ]
    )
    mdir = tmp_path / "manifests"
    module_run(fake_repo_check(table, pids=["paperX", "paperY"]), "code_check", manifest=str(mdir))
    mf = _manifests(mdir)
    assert set(mf) == {"paperX.manifest.json", "paperY.manifest.json"}
    mx, my = mf["paperX.manifest.json"]["code"], mf["paperY.manifest.json"]["code"]
    assert mx["packages"] == ["ggplot2"]
    assert "files_failed" not in mx
    assert "packages" not in my
    assert len(my["files_failed"]) == 1
    assert my["files_failed"][0]["file_name"] == "bad.R"


# ── downloads ────────────────────────────────────────────────────────────────


def test_downloaded_files_keep_their_results(tmp_path: Path) -> None:
    # R: "downloaded files keep their analysis results (join regression)", offline
    # through a file:// URL and the real download_repo_files()
    src = write(
        tmp_path / "analysis.R",
        [
            "# read the data",
            'dat <- read.csv("C:/Users/lisa/project/data.csv")',
            'other <- read.csv("not_in_repo.csv")',
        ],
    )
    paper = pc.test_paper(["x"])
    table = pd.DataFrame(
        {
            "paper_id": [paper.paper_id],
            "repo_name": ["code-join-regression"],
            "repo_url": ["https://example.org/code-join-regression"],
            "file_name": ["analysis.R"],
            "file_path": ["code/analysis.R"],
            "file_url": ["file://" + str(src)],
            "file_size": [float(src.stat().st_size)],
            "file_type": ["code"],
            "file_location": pd.Series([None], dtype=object),
        }
    )
    mo = module_run(fake_repo_check(table, paper=paper), "code_check", download=True)
    r = row(mo, "analysis.R")
    assert r["checked"]
    assert r["code_abs_path"] >= 1
    assert "lisa" in r["absolute_paths"]
    assert r["loaded_files_missing"] == 2
    assert "not_in_repo.csv" in r["loaded_files_missing_names"]
    assert st(mo)["code_abs_path"] >= 1
    assert st(mo)["code_missing_files"] == 2
    assert "lisa" in all_report(mo)
    assert "loaded 0 files" not in all_report(mo)


def test_every_download_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # R: "code files found but every download fails does not crash"
    import pytacheck.archives.download as dl

    monkeypatch.setattr(
        dl,
        "download_repo_files",
        _failing_download(error="API rate limit exhausted: HTTP 429 Too Many Requests."),
    )
    paper = pc.test_paper(["x"], url=["https://doi.org/10.5061/dryad.does-not-matter"])
    table = _remote_listing(
        [
            {
                "paper_id": paper.paper_id,
                "file_name": n,
                "repo_url": "https://doi.org/10.5061/dryad.does-not-matter",
                "file_url": f"file:///does/not/exist/{n}",
                "file_location": None,
            }
            for n in ("analysis.R", "helpers.R")
        ]
    )
    mo = module_run(fake_repo_check(table, paper=paper), "code_check")
    assert mo.traffic_light == "na"
    s = st(mo)
    assert len(mo.summary_table) == 1
    assert s["code_n"] == 2
    assert s["code_checked"] == 2
    assert s["code_abs_path"] == 0
    assert s["code_setwd"] == 0
    assert s["code_missing_files"] == 0
    assert math.isnan(s["code_min_comments"])
    assert s["code_parse_errors"] == 0
    assert mo.table["error"].notna().all()


def test_size_cap_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.archives.download as dl

    msg = "Repository https://osf.io/x exceeds the 0.0 MB per-repository budget."

    def download_repo_files(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        out = files.copy()
        out["file_location"] = [None] * len(out)
        out.attrs["gated"] = pd.DataFrame({"repo_url": ["https://osf.io/x"], "message": [msg]})
        return out

    monkeypatch.setattr(dl, "download_repo_files", download_repo_files)
    table = _remote_listing(
        [
            {
                "paper_id": "p1",
                "file_name": "a.R",
                "repo_url": "https://osf.io/x",
                "file_url": "file:///does/not/exist/a.R",
                "file_location": None,
            }
        ]
    )
    with pytest.warns(UserWarning, match="per-repository budget"):
        mo = module_run(fake_repo_check(table), "code_check")
    # the pre-pass and the catch-up download both refuse it: reported once
    assert mo.summary_text.count(msg) == 1
    assert mo.summary_text.startswith(
        "\n-  We found 1 R, 0 Python, 0 SAS, 0 SPSS, 0 Stata, 0 Mplus, 0 MATLAB, and 0 JASP "
        f"code file. {msg}\n"
    )


def test_download_false_streams_from_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.archives.download as dl

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("download=False must not download")

    monkeypatch.setattr(dl, "download_repo_files", boom)
    src = str(CODE_FILES / "analysis.R")
    table = _remote_listing(
        [
            {
                "paper_id": "p1",
                "file_name": "analysis.R",
                "repo_url": "https://osf.io/x",
                "file_url": src,
                "file_location": None,
            }
        ]
    )
    mo = module_run(fake_repo_check(table), "code_check", download=False)
    assert row(mo, "analysis.R")["packages"] == "dplyr, ggplot2"


# ── parse errors, traffic lights ─────────────────────────────────────────────


def test_parse_errors() -> None:
    # R: "parse errors" (the analysis columns; the listing columns come from repo_check)
    mo = cc_run("parse_errors")
    obs = mo.table.sort_values("file_name", kind="stable").reset_index(drop=True)
    exp = {
        "file_name": [
            "error-ok.qmd",
            "error.R",
            "error.Rmd",
            "error.qmd",
            "knit-error.Rmd",
            "ok.R",
            "ok.Rmd",
            "ok.qmd",
        ],
        "language": ["R"] * 8,
        "checked": [True] * 8,
        "parse_error": [True] * 4 + [False] * 4,
        "parse_error_msg": [
            "line:5:1: unexpected symbol\n4: \n5: a\n   ^",
            "line:4:1: unexpected symbol\n3: \n4: a\n   ^",
            "line:4:1: unexpected symbol\n3: \n4: a\n   ^",
            "line:4:1: unexpected symbol\n3: \n4: a\n   ^",
            None,
            None,
            None,
            None,
        ],
        "code_abs_path": [0, 0, 0, 1, 0, 0, 0, 0],
        "absolute_paths": ["", "", "", "/User/lisa/file.csv", "", "", "", ""],
        "code_setwd": [0] * 8,
        "setwd_calls": [""] * 8,
        "code_install_packages": [0] * 8,
        "install_packages_calls": [""] * 8,
        "library_lines": [1, 1, 1, 1, 0, 3, 0, 0],
        "library_max_between": [None, None, None, None, None, 5, None, None],
        "packages_n": [1, 1, 1, 1, 0, 2, 0, 0],
        "packages": ["dplyr", "dplyr", "dplyr", "dplyr", "", "dplyr, tidyr", "", ""],
        "comment_lines": [1, 1, 1, 3, 1, 2, 4, 4],
        "code_lines": [4, 2, 2, 3, 1, 7, 1, 1],
        "percentage_comment": [0.2, 1 / 3, 1 / 3, 0.5, 0.5, 2 / 9, 0.8, 0.8],
        "has_docstring": [None] * 8,
        "loaded_files_missing": [0, 0, 0, 1, 0, 0, 0, 0],
        "loaded_files_missing_names": ["", "", "", "file.csv", "", "", "", ""],
    }
    for name, values in exp.items():
        got = [None if pd.isna(v) else v for v in obs[name].tolist()]
        if name == "percentage_comment":
            assert got == pytest.approx(values), name
        else:
            assert got == values, name
    assert all(Path(p).exists() for p in obs["file_location"])
    s = st(mo)
    assert {k: s[k] for k in s if k != "paper_id"} == {
        "code_n": 8,
        "code_checked": 8,
        "code_abs_path": 1,
        "code_setwd": 0,
        "code_install_packages": 0,
        "code_missing_files": 1,
        "code_min_comments": 0.2,
        "code_parse_errors": 4,
        "code_packages_n": 2,
        "code_version_pinned": False,
    }


def test_green_light_and_parse_errors(tmp_path: Path) -> None:
    # R: "code_check local_path: green light and parse errors". metacheck's test
    # expects green for one commented file, but green also needs a pinned
    # environment (version_pin$pinned), so R itself returns "yellow" there:
    # a renv.lock makes the listing green here.
    write(tmp_path / "good.R", ["# comment", "x <- 1"])
    assert run_dir(tmp_path).traffic_light == "yellow"
    (tmp_path / "renv.lock").write_text(
        json.dumps({"R": {"Version": "4.3.1"}, "Packages": {}}), encoding="utf-8"
    )
    assert run_dir(tmp_path).traffic_light == "green"
    write(tmp_path / "bad.R", ["x <- (1"])
    mo = run_dir(tmp_path)
    assert mo.traffic_light == "yellow"
    assert "bad.R" in all_report(mo)
    assert "Parsing issues of R-type files were found." in mo.summary_text


@pytest.mark.parametrize(
    ("scenario", "light"),
    [
        ("green", "green"),
        ("session", "green"),
        ("urls", "green"),
        ("mixed", "yellow"),
        ("groundhog", "yellow"),
        ("jasp_only", "na"),
        ("nocode", "na"),
        ("no_location", "na"),
        ("empty_table", "na"),
    ],
)
def test_traffic_lights(scenario: str, light: str) -> None:
    assert cc_run(scenario).traffic_light == light


def test_listed_but_unanalysed_files() -> None:
    mo = cc_run("jasp_only")
    assert mo.table["checked"].tolist() == [False]
    assert "No code files could be checked for comments." in mo.summary_text
    assert st(mo)["code_n"] == 1


def test_docstring_note() -> None:
    txt = report_text(cc_run("docstring"))
    assert "2 of these Python files are documented via docstrings" in txt
    txt = report_text(cc_run("docstring1"))
    assert "1 of these Python file is documented via docstrings" in txt


def test_data_check_structure_is_preferred() -> None:
    mo = cc_run("structure")
    assert "helper.R" in set(mo.table["file_name"])


def test_local_path_forces_a_fresh_repo_check(stub_repo_check: list[dict[str, Any]]) -> None:
    prev = cc_prev("code_files")
    mo = module_run(prev, "code_check", local_path=str(CODE_FILES / "subdir"), local_only=True)
    assert stub_repo_check == [
        {"local_path": str(CODE_FILES / "subdir"), "local_only": True, "cache": False}
    ]
    assert mo.table["file_name"].tolist() == ["helper.R"]


def test_missing_file_url_column_is_an_error() -> None:
    # R: link(NULL, file_name) cannot fill the report table
    with pytest.raises(Exception, match="replacement has 0 rows, data has 1"):
        cc_run("no_file_url")


def test_missing_paper_id_column_is_an_error() -> None:
    with pytest.raises(Exception, match="paper_id"):
        cc_run("no_paper_id")


# ── pytacheck contracts ──────────────────────────────────────────────────────


def test_inputs_are_not_mutated() -> None:
    prev = cc_prev("mixed")
    snapshot = copy.deepcopy(prev.table)
    module_run(prev, "code_check")
    pd.testing.assert_frame_equal(prev.table, snapshot)
    assert prev.table.attrs == {}


def test_output_table_has_a_clean_index_and_no_attrs() -> None:
    mo = cc_run("mixed")
    assert mo.table.index.tolist() == list(range(len(mo.table)))
    assert mo.table.attrs == {}


# ── bibr export schema 12.x papers ───────────────────────────────────────────


def _comparable(out: ModuleOutput) -> dict[str, Any]:
    return {
        "table": out.table.drop(columns="paper_id").reset_index(drop=True),
        "summary": out.summary_table.drop(columns="paper_id").reset_index(drop=True),
        "traffic_light": out.traffic_light,
        "summary_text": out.summary_text,
        "report": report_text(out),
        "tables": report_tables(out),
    }


@pytest.mark.parametrize("scenario", ["mixed", "green", "parse_errors", "nocode"])
def test_bibr12_paper_matches_the_legacy_paper(scenario: str) -> None:
    from pytacheck.io.bibr12 import read_bibr12

    p12 = read_bibr12(BIBR12_PREPRINT)
    legacy = pc.read(LEGACY_PREPRINT, schema_version=None)
    runs = []
    for paper in (p12, legacy):
        prev = cc_prev(scenario, paper=paper)
        table = prev.table.copy()
        table["paper_id"] = pd.Series([paper.paper_id] * len(table), dtype="string")
        out = module_run(fake_repo_check(table, paper=paper), "code_check")
        assert out.summary_table["paper_id"].tolist() == [paper.paper_id]
        runs.append(_comparable(out))
    a, b = runs
    pd.testing.assert_frame_equal(a["table"], b["table"])
    pd.testing.assert_frame_equal(a["summary"], b["summary"])
    for x, y in zip(a["tables"], b["tables"], strict=True):
        pd.testing.assert_frame_equal(x, y)
    assert a["traffic_light"] == b["traffic_light"]
    assert a["summary_text"] == b["summary_text"]
    assert a["report"] == b["report"]


def test_default_reader_paper_runs() -> None:
    p = pc.read(BIBR12_PREPRINT)
    out = module_run(
        fake_repo_check(dir_listing(CODE_FILES, pid=p.paper_id), paper=p), "code_check"
    )
    assert out.traffic_light == "yellow"
    assert out.summary_table["paper_id"].tolist() == [p.paper_id]
    assert st(out)["code_n"] == 5


# ── with the ported repo_check (R: local_path / local_only / mocked OSF) ─────


@needs_repo_check
def test_repo_check_local_path_finds_code_files() -> None:
    mo = module_run(pc.test_paper(["x"]), "code_check", local_path=str(CODE_FILES))
    assert mo.traffic_light == "yellow"
    assert st(mo)["code_n"] == 5
    assert row(mo, "analysis.R")["loaded_files_missing"] == 0


@needs_repo_check
def test_repo_check_local_path_errors() -> None:
    with pytest.warns(UserWarning, match="/no/such/path/exists"):
        module_run(pc.test_paper(["x"]), "code_check", local_path="/no/such/path/exists")


@needs_repo_check
def test_repo_check_local_only_without_local_path() -> None:
    mo = module_run(pc.test_paper(["x"]), "code_check", local_only=True)
    assert mo.traffic_light == "na"
    assert mo.summary_table["code_n"].tolist() == [0]


@needs_repo_check
def test_repo_check_local_only_ignores_online_repos() -> None:
    from tests.httpmock import replay

    paper = pc.test_paper(["x"], url=["https://osf.io/629bx"])
    with replay("apis"):
        mo = module_run(paper, "code_check", local_path=str(CODE_FILES), local_only=True)
    assert st(mo)["code_n"] == 5
    assert "bad.R" not in set(mo.table["file_name"])


@needs_repo_check
def test_repo_check_local_only_false_is_the_default() -> None:
    paper = pc.test_paper(["x"])
    a = module_run(paper, "code_check", local_path=str(CODE_FILES))
    b = module_run(paper, "code_check", local_path=str(CODE_FILES), local_only=False)
    pd.testing.assert_frame_equal(a.summary_table, b.summary_table)
    assert a.traffic_light == b.traffic_light


@needs_repo_check
def test_repo_check_osf_without_code() -> None:
    from tests.httpmock import replay

    for url in ("https://osf.io/y6a34", "https://osf.io/m4nbv"):
        paper = pc.test_paper(["x"], url=[url])
        with replay("apis"), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mo = module_run(paper, "code_check")
        assert mo.traffic_light == "na"
        assert mo.summary_table["code_n"].tolist() == [0]


def test_zip_members_without_file_url_are_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    # a member of a zip repo_check already expanded has archive_url + archive_member
    # instead of a file_url; it is fetched like any remote code file
    import pytacheck.archives.download as dl

    src = str(CODE_FILES / "analysis.R")
    seen: list[list[str]] = []

    def download_repo_files(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        seen.append(files["file_name"].tolist())
        out = files.copy()
        out["file_location"] = [src] * len(out)
        return out

    monkeypatch.setattr(dl, "download_repo_files", download_repo_files)
    table = _remote_listing(
        [
            {
                "paper_id": "p1",
                "file_name": "analysis.R",
                "repo_url": "https://osf.io/x",
                "file_url": None,
                "file_location": None,
                "archive_url": "https://osf.io/x/code.zip",
                "archive_member": "code/analysis.R",
            },
            {
                "paper_id": "p1",
                "file_name": "orphan.R",
                "repo_url": "https://osf.io/x",
                "file_url": None,
                "file_location": None,
                "archive_url": "https://osf.io/x/code.zip",
                "archive_member": None,
            },
        ]
    )
    mo = module_run(fake_repo_check(table), "code_check")
    assert seen == [["analysis.R"]]
    assert row(mo, "analysis.R")["packages"] == "dplyr, ggplot2"
    assert pd.isna(row(mo, "orphan.R")["packages"])


def test_unexpanded_zip_code_members_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R: .code_expand_zip() -- a remote .zip row repo_check did not expand: its
    # code members are fetched (range requests) and checked like any other file
    import pytacheck.archives.download as dl
    import pytacheck.archives.zip_peek as zp

    member = write(tmp_path / "members" / "analysis.R", ["# code in a zip", "library(dplyr)"])
    monkeypatch.setattr(
        zp,
        "zip_peek",
        lambda url: pd.DataFrame({"name": ["README.txt", "analysis.R"], "size": [6.0, 38.0]}),
    )
    requested: list[list[str]] = []

    def fetch(url: str, names: Any = None, dest: str = ".", verify: bool = True) -> pd.DataFrame:
        requested.append(list(names))
        return pd.DataFrame(
            {"name": ["analysis.R"], "path": [str(member)], "size": [38.0], "ok": [True]}
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", fetch)
    monkeypatch.setattr(dl, "_repo_cache_path", lambda repo_url, file_path: str(tmp_path / "c"))
    table = _remote_listing(
        [
            {
                "paper_id": "p1",
                "file_name": "code.zip",
                "file_path": "code.zip",
                "repo_url": "https://osf.io/x",
                "file_url": "https://osf.io/x/code.zip",
                "file_location": None,
            }
        ]
    )
    mo = module_run(fake_repo_check(table), "code_check", download=False)
    assert requested == [["analysis.R"]]
    r = row(mo, "analysis.R")
    assert r["file_path"] == "code.zip/analysis.R"
    assert r["file_location"] == str(member)
    assert r["packages"] == "dplyr"
    assert st(mo)["code_n"] == 1
