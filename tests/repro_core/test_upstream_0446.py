"""The reproducibility changes of metacheck 0.4.4.4 to 0.4.4.6 (scienceverse/metacheck#648, #649).

Ports of the R tests of ``test-reproducibility_check.R`` and ``test-module-reproducibility_check.R``
for: a document written as ``<name>.<ext>`` and run from its code, packages the run could not
install, the recorded R version of a Docker run, why a script stopped (files, packages, programs,
settings, arguments), the light after a run, the install sentence, the advice by language and
cause, files made under a built name or in another language, and files inside archives unpacked
in their own folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro.core import repro_write_scripts
from metacheck.repro.layout import (
    _repro_archive_text,
    _repro_unpack_archives,
    _repro_unpacked_paths,
)
from metacheck.repro.outcomes import (
    _repro_failure_reason,
    _repro_install_sentence,
    _repro_missing_program,
    _repro_needs_arguments,
    _repro_run_light,
    _repro_unopened_files,
)
from metacheck.repro.section import _repro_advice


def _rewrites(fn: str) -> dict[str, pd.DataFrame]:
    return {
        fn: pd.DataFrame(
            {
                "ref": pd.array([], dtype="string"),
                "basename": pd.array([], dtype="string"),
                "matched": pd.array([], dtype="boolean"),
                "target": pd.array([], dtype="string"),
                "ambiguous": pd.array([], dtype="boolean"),
                "n_candidates": pd.array([], dtype="Int64"),
                "is_call": pd.array([], dtype="boolean"),
            }
        )
    }


def test_repro_write_scripts_leaves_a_document_as_deposited(tmp_path: Path) -> None:
    """a document is run from a file of its own (vtwbs_v2: the .qmd files became bare R code)"""
    plan = pd.DataFrame(
        {
            "file_name": ["report.qmd", "nb.ipynb", "main.R"],
            "target_path": ["analysis/report.qmd", "nb.ipynb", "main.R"],
        }
    )
    root = tmp_path
    (root / "analysis").mkdir()
    doc = root / "analysis" / "report.qmd"
    doc.write_text("---\ntitle: x\n---\n```{r}\nx <- 1\n```\n")
    out = repro_write_scripts({"report.qmd": ["x <- 1"]}, _rewrites("report.qmd"), plan, root)
    path = Path(str(out["script_path"].iloc[0]))
    assert path.name == "report.qmd.R"
    assert path.read_text().splitlines() == ["x <- 1"]
    assert doc.read_text().splitlines()[0] == "---"
    out_py = repro_write_scripts(
        {"nb.ipynb": ["x = 1"]}, _rewrites("nb.ipynb"), plan, root, lang="Python"
    )
    assert Path(str(out_py["script_path"].iloc[0])).name == "nb.ipynb.py"
    # a plain script is written in its own place, as before
    out_r = repro_write_scripts({"main.R": ["y <- 2"]}, _rewrites("main.R"), plan, root)
    assert Path(str(out_r["script_path"].iloc[0])).name == "main.R"


def test_why_a_script_stopped_is_found_in_more_ways() -> None:
    """why a script stopped: files, packages, programs, settings and arguments (#648)"""

    def r(m: str) -> str | None:
        return _repro_failure_reason(["errored"], [m])[0]

    # a missing file, also when the error names it in the script's own words
    assert r("Error in read_experiment(1) : Data file not found: ../data/expt1all.csv") == "file"
    assert r("Error in eval(e, envir = env) : file not found: 'new/wiener_marginal.cpp'") == "file"
    assert r('Error: Cannot open "data/x.shp"; The file doesn\'t seem to exist.') == "file"
    assert r("Error in `ggsave()`: ! Cannot find directory 'out/plots'.") == "file"
    assert r("Error in check(ti) : model file(s) not found in /sandbox: models/a.jags") == "file"
    assert _repro_unopened_files(
        [
            "Error in f(1) : Data file not found: ../data/expt1all.csv",
            "Error in eval(e) : file not found: 'new/w.cpp'",
            "OSError: unable to open file (unable to open file: name = 'res/a.h5', errno = 2)",
            "Error in `ggsave()`: ! Cannot find directory `out/plots`.",
            "FileNotFoundError: data/raw/x.csv",
        ]
    ) == ["expt1all.csv", "w.cpp", "a.h5", "plots", "x.csv"]
    # not files: columns, and read.spss's own words in quotes
    assert r("Error in `select()`: ! Can't select columns that don't exist.") == "other"
    assert (
        _repro_unopened_files(
            "Error in read.spss(x) : unable to open file: 'No such file or directory'"
        )
        == []
    )
    # a package another package loads only when it is used
    assert r("Error in `require_package()`: ! Please install the 'cmdstanr' package.") == "package"
    assert (
        r('Error in check_pkg_installed(x) : The package "broom.helpers" (>= 1.20.0) is required.')
        == "package"
    )
    # a program our run does not have
    assert r("Error in find_chrome() : Cannot find Chromium or Google Chrome") == "environment"
    assert (
        _repro_missing_program(["Error: python3 is required when rerun_text_analysis is TRUE."])[0]
        == "Python"
    )
    # a setting a deposit cannot hold
    assert r("LEVELS_DB is not set. Set it to the path of the study DuckDB database") == "settings"
    assert r("ValueError: ANTHROPIC_API_KEY environment variable is required") == "settings"
    assert r("openai.OpenAIError: Missing credentials. Please pass an `api_key`") == "settings"
    # a script's own message about its command-line arguments, when it reads them
    py = ["import argparse\np = argparse.ArgumentParser()"]
    assert _repro_needs_arguments(
        py, "analysis.py: error: one of --synthetic / --real is required", "Python"
    ) == [True]
    assert _repro_needs_arguments(
        py, "FATAL: --units is required (or use --selftest)", "Python"
    ) == [True]
    assert _repro_needs_arguments(
        ["a <- commandArgs(TRUE)"],
        "Error: Provide the file index as the first command-line argument.",
        "R",
    ) == [True]
    assert _repro_needs_arguments(
        ["x <- 1"], "Error: Provide the first command-line argument.", "R"
    ) == [False]


def test_the_light_after_a_run_and_the_install_sentence() -> None:
    """the light after a run, and the sentences on what was run and installed (#648)"""

    def rr(o: list[str], reason: list[str | None] | None = None) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "outcome": pd.array(o, dtype="string"),
                "reason": pd.array(reason or [None] * len(o), dtype="string"),
            }
        )

    assert _repro_run_light("green", rr(["ran_ok", "errored"], [None, "file"])) == "red"
    # a time-out alone is not an error of the code
    assert _repro_run_light("green", rr(["ran_ok", "timed_out"])) == "yellow"
    # what our run lacks, or a setting a deposit cannot hold
    assert _repro_run_light("green", rr(["ran_ok", "errored"], [None, "settings"])) == "yellow"
    assert _repro_run_light("green", rr(["ran_ok", "errored"], [None, "environment"])) == "yellow"
    # no script ran to the end
    assert _repro_run_light("green", rr(["not_analysis", "dependency_unavailable"])) == "yellow"
    assert _repro_run_light("green", rr(["ran_ok"])) == "green"
    # the files the code reads already gave red
    assert _repro_run_light("red", rr(["timed_out"])) == "red"

    ir = pd.DataFrame(
        {
            "package": pd.array(["a", "b", "c"], dtype="string"),
            "installed": pd.array([True, False, True], dtype="boolean"),
        }
    )
    assert _repro_install_sentence(ir) == (
        "Dependencies were installed into a throwaway library before running: 2 succeeded, "
        "1 failed. a, c were installed."
    )
    assert _repro_install_sentence(ir.iloc[[1]].reset_index(drop=True), pip=True) == (
        "Dependencies were installed into a throwaway library before running (`pip install`): "
        "0 succeeded, 1 failed."
    )


def _advice(tbl: pd.DataFrame, rr: pd.DataFrame, outcome: list[str], **kw: Any) -> str:
    blocks, _ = _repro_advice(tbl, rr, outcome, kw.pop("mi", pd.DataFrame()), **kw)
    return "\n".join(blocks)


def test_the_advice_for_a_package_that_could_not_be_installed_fits_its_language_and_cause() -> None:
    """the advice for a package that could not be installed (#648)"""
    tbl = pd.DataFrame({"file_name": ["a.py", "b.R", "c.R"]})
    rr = pd.DataFrame(
        {
            "error": [
                "ModuleNotFoundError: No module named 'ehrql'",
                "Error in library(mixedpower) : there is no package called 'mixedpower'",
                "Error in library(sf) : there is no package called 'sf'",
            ],
            "stderr": [None, None, None],
            "reason": ["package"] * 3,
        }
    )
    ir = pd.DataFrame(
        {
            "package": ["ehrql", "mixedpower", "sf"],
            "installed": [False, False, False],
            "category": [None, "cran_unavailable", "compile_failure"],
        }
    )
    adv = _advice(tbl, rr, ["dependency_unavailable"] * 3, install_results=ir)
    assert "probably because it is old" not in adv
    assert "<code>ehrql</code> could not be installed with <code>pip</code>" in adv
    assert "<code>mixedpower</code> is not on CRAN" in adv
    assert "BiocManager::install" in adv
    assert "<code>sf</code> could not be built" in adv


def test_a_file_a_script_makes_under_a_built_name_or_in_another_language() -> None:
    """a missing file made under a built name, or in another language, is said to be made (#648)"""
    tbl = pd.DataFrame({"file_name": ["a.R", "b.R"], "writes": ["", "fullfit_%s.rds"]})
    rr = pd.DataFrame(
        {
            "error": [
                "Error in readRDS(\"fullfit_M2.rds\") : cannot open file 'fullfit_M2.rds': "
                "No such file or directory",
                None,
            ],
            "stderr": [None, None],
            "reason": ["file", None],
        }
    )
    adv = _advice(tbl, rr, ["errored", "ran_ok"])
    assert (
        "<code>a.R</code> needs <code>fullfit&#95;M2.rds</code>, which <code>b.R</code> makes."
        in adv
    )
    tbl2 = pd.DataFrame({"file_name": ["a.R"], "writes": [""]})
    rr2 = pd.DataFrame(
        {
            "error": [
                "Error in file(file, \"rt\") : cannot open file 'arima_standardised.csv': "
                "No such file or directory"
            ],
            "stderr": [None],
            "reason": ["file"],
        }
    )
    other = pd.DataFrame(
        {
            "file_name": ["002_incidence.do"],
            "files_written": ["$projectdir/output/arima_standardised.csv"],
        }
    )
    adv2 = _advice(tbl2, rr2, ["errored"], other_writes=other)
    assert "A script needs a file that a script in another language makes." in adv2
    assert "which <code>002&#95;incidence.do</code> makes" in adv2


def test_files_inside_archives_go_where_unpacking_the_archive_in_its_folder_puts_them(
    tmp_path: Path,
) -> None:
    """files inside archives are where unpacking the archive in its folder puts them (#648)"""
    p = [
        "behaviour_data.zip/behaviour_data/p01.json",  # one folder: beside the zip
        "d/loose.zip/a.csv",
        "d/loose.zip/sub/b.csv",  # loose files: a folder of its name
        "raw/s3_raw.json.gz/s3_raw.json",  # one compressed file: beside it
        "Data.tar.gz/Data/x.csv",
        "outer.zip/top/inner.zip/q.csv",
        "outer.zip/top/r.csv",
        "dup.zip/a.csv",
        "dup/a.csv",  # a file outside the zip keeps its path
        "plain.csv",
        None,
    ]
    assert _repro_unpacked_paths(p) == [
        "behaviour_data/p01.json",
        "d/loose/a.csv",
        "d/loose/sub/b.csv",
        "raw/s3_raw.json",
        "Data/x.csv",
        "top/inner/q.csv",
        "top/r.csv",
        "dup.zip_files/a.csv",
        "dup/a.csv",
        "plain.csv",
        None,
    ]

    # the archive on disk goes into the run folder too, at its own path; the report keeps the
    # path in the deposit
    gz = tmp_path / "res.nii.gz"
    gz.write_text("x\n")
    s = pd.DataFrame(
        {
            "file_name": ["res.nii"],
            "file_path": ["templates/res.nii.gz/res.nii"],
            "file_location": [str(tmp_path / "res.nii")],
            "archive_file_path": ["templates/res.nii.gz"],
            "archive_file_location": [str(gz)],
        }
    )
    code = pd.DataFrame({"file_name": ["a.R"], "file_path": ["code/a.R"]})
    up = _repro_unpack_archives(s, code)
    assert up["structure_df"]["file_path"].tolist() == ["templates/res.nii"]
    assert up["structure_df"]["file_path_deposit"].tolist() == ["templates/res.nii.gz/res.nii"]
    assert up["code_tbl"]["file_path"].tolist() == ["code/a.R"]
    assert up["archives"]["file_path"].tolist() == ["templates/res.nii.gz"]
    assert up["archives"]["file_location"].tolist() == [str(gz)]

    # the sentence for the report: only for an archive whose files the code reads and that no
    # script unpacks
    s2 = pd.DataFrame(
        {
            "file_name": ["p01.json"],
            "file_path": ["behaviour_data/p01.json"],
            "file_path_deposit": ["behaviour_data.zip/behaviour_data/p01.json"],
        }
    )
    assert "only inside `behaviour_data.zip`" in _repro_archive_text(
        s2, [["f <- Sys.glob('behaviour_data/*.json')"]]
    )
    assert (
        _repro_archive_text(
            s2, [["unzip('behaviour_data.zip'); f <- Sys.glob('behaviour_data/*.json')"]]
        )
        == ""
    )
    assert _repro_archive_text(s2, [["x <- read.csv('other.csv')"]]) == ""


def test_missing_fn_pkgs_leaves_out_packages_the_run_could_not_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.repro_missing_fn_pkgs leaves out packages the run could not install (sd2hq_v2; #648)"""
    from metacheck.repro import runsupport as rs

    monkeypatch.setattr(rs, "_repro_r_exporters", lambda name, pkgs: set())
    code_list = {
        "_targets.R": ["library(tidyverse)", "library(mixedpower)", "library(lme4)"],
        "R/data.R": ["d %>% filter(x > 1)"],
    }
    assert rs._repro_missing_fn_pkgs(
        "%>%", "R/data.R", code_list, [], unavailable=["mixedpower"]
    ) == ["tidyverse", "lme4"]
