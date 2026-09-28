"""Regression tests for divergences from metacheck found in the repro_core review.

Each test pins R behaviour checked against the R reference (the matching
parity cases are in ``parity/cases/repro_core_review.yaml``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pytacheck._r.regex import gsub, sub
from pytacheck.repro import core, docker, tables
from tests.repro_core.parity_support import files_df

UPSTREAM_REPRO = (
    Path(__file__).resolve().parents[2] / "upstream" / "metacheck" / "tests" / "testthat"
    / "fixtures" / "repro"
)  # fmt: skip
TABLES_REVIEW = Path(__file__).resolve().parent / "fixtures" / "tables_review"


def _has_r() -> bool:
    return bool(os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript"))


# -- R replacement strings (pytacheck._r.regex) --------------------------------


def test_perl_case_conversion_applies_to_backreferences_only() -> None:
    # R: sub("(a)", "\\Ux\\1y\\Ez\\1", "a", perl = TRUE) is "xAyza"
    assert sub("(a)", r"\Ux\1y\Ez\1", "a", perl=True) == "xAyza"
    assert sub("(a)", r"\U\1\LB\1", "a", perl=True) == "ABa"
    assert gsub("(b)", r"\U-\1-", "abcb", perl=True) == "a-B-c-B-"
    # without perl, \U is just "U"
    assert sub("(a)", r"\U\1\LB\1", "a") == "UaLBa"


def test_trailing_lone_backslash_is_dropped() -> None:
    assert sub("a", "x\\", "a") == "x"
    assert gsub("a", "x\\", "aa", perl=True) == "xx"
    assert sub("(a)", "\\0|\\q|\\\\|\\", "a", perl=True) == "0|q|\\|"
    # fixed = TRUE replacements are literal
    assert sub("a", "\\0|\\\\", "a", fixed=True) == "\\0|\\\\"


def test_format_call_refs_resolves_escaped_values_like_r() -> None:
    code = [
        'wd <- "C:\\Users\\me\\q"',
        'd <- read.csv(sprintf("%s/x.csv", wd))',
        'w2 <- "a\\1b\\Ec"',
        'e <- read.csv(sprintf("%s/y.csv", w2))',
    ]
    out = core._repro_format_call_refs(code)
    assert out["resolved"].tolist() == ["C:sersmeq/x.csv", "abc/y.csv"]


# -- repro_run_order() -----------------------------------------------------------


def test_run_order_never_matches_empty_or_na_basenames() -> None:
    # R's writer_of[[""]] and writer_of[[NA]] are always NULL
    files = files_df(
        ["a.R", "b.R", "c.R"],
        reads=[[None], [""], ["x.csv"]],
        writes=[[""], [None], ["X.csv"]],
        sources=[[], [None], [""]],
    )
    out = core.repro_run_order(files)
    assert out["depends_on"].tolist() == ["", "", ""]
    assert out["order_basis"].tolist() == ["none", "none", "none"]


def test_run_order_source_with_several_exact_hits() -> None:
    files = files_df(["s1/x.R", "s2/x.R", "main.R"], sources=[[], [], ["x.R"]])
    out = core.repro_run_order(files)
    assert out["depends_on"].tolist() == ["", "", "s1/x.R, s2/x.R"]
    assert len(out.attrs["fuzzy_sources"]) == 0


# -- repro_write_scripts() / repro_materialize_layout() ---------------------------


def test_write_scripts_with_no_scripts_has_no_columns(tmp_path: Path) -> None:
    # dplyr::bind_rows(list()): a 0 x 0 frame
    out = core.repro_write_scripts({}, {}, None, tmp_path)
    assert out.shape == (0, 0)
    assert core.repro_write_scripts([], {}, None, tmp_path).shape == (0, 0)


def test_materialize_failures_are_quiet(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    src.write_text("a\n")
    srcdir = tmp_path / "adir"
    srcdir.mkdir()
    plan = pd.DataFrame(
        {"file_name": ["src.csv", "src2.csv", "adir"], "target_path": ["a", "a/b.csv", "d/dir"]}
    )
    sd = pd.DataFrame(
        {
            "file_name": ["src.csv", "src2.csv", "adir"],
            "file_location": [str(src), str(src), str(srcdir)],
        }
    )
    root = tmp_path / "root"
    out = core.repro_materialize_layout(plan, sd, root)
    # "a" is a file, so "a/b.csv" cannot be created: R's dir.create() fails
    # silently and the copy is recorded as not ok
    assert out.materialised["ok"].tolist() == [True, False, False]
    assert (root / "a").is_file()
    assert (root / "d").is_dir()


# -- repro_run_scripts(): not-run outcomes -------------------------------------------


def test_pre_run_outcome_follows_r_istrue() -> None:
    assert core._pre_run_outcome("a.R", (), {"a.R": np.bool_(True)}) is None
    assert core._pre_run_outcome("a.R", (), pd.Series({"a.R": True})) is None
    for bad in (None, np.bool_(False), 1, "TRUE", [True, True]):
        row = core._pre_run_outcome("a.R", (), {"a.R": bad})
        assert row is not None and row["outcome"] == "not_parsed"
    # a single string is one file name, not a sequence of characters
    row = core._pre_run_outcome("a.R", "a.R", None)
    assert row is not None and row["outcome"] == "skipped_missing_inputs"
    assert core._pre_run_outcome("a", "a.R", None) is None


@pytest.mark.skipif(not _has_r(), reason="Rscript not available")
def test_run_scripts_resolves_relative_paths_like_callr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(UPSTREAM_REPRO.parent)
    run_tbl = pd.DataFrame(
        {"file_name": ["a.R", "b.R"], "script_path": ["ok.R", "repro/ok.R"], "run_dir": "repro"}
    )
    out = core.repro_run_scripts(run_tbl, None, timeout=60)
    # a.R: opened after setwd("repro"); b.R: "repro/repro/ok.R" does not exist
    assert out["outcome"].tolist() == ["ran_ok", "errored"]
    assert out["script_lines"].tolist()[0] == []  # read from the caller's directory


# -- repro_missing_inputs() -----------------------------------------------------------


def test_missing_inputs_na_candidate_blocks_the_near_miss() -> None:
    sd = pd.DataFrame({"file_name": [None, "zzz.xlsx"], "file_location": [None, None]})
    out = core.repro_missing_inputs(["zzz.csv"], None, sd)
    assert out["status"].tolist() == ["absent"]
    assert out["similar_to"].isna().all()
    sd2 = pd.DataFrame({"file_name": ["zzz.xlsx", None], "file_location": [None, None]})
    out2 = core.repro_missing_inputs(["zzz.csv"], None, sd2)
    assert out2["similar_to"].tolist() == ["zzz.xlsx"]


# -- JAGS models, docker helpers ------------------------------------------------------


def test_jags_other_code_text_as_one_string() -> None:
    assert core._repro_is_jags_model("x <- 1", "m.R", 'jags.model("m.R")') is True
    assert core._repro_is_jags_model("x <- 1", "m.R", 'jags.model("n.R")') is False


def test_resource_args_with_missing_limits() -> None:
    from pytacheck.utils import local_options

    with local_options({"metacheck.docker_resource_limits": {"memory_gb": 8}}):
        assert docker._repro_docker_resource_args() == ["--cpus", "--memory", "8g"]
    with local_options({"metacheck.docker_resource_limits": {"cpus": 1.5}}):
        assert docker._repro_docker_resource_args() == ["--cpus", "1.5", "--memory", "g"]


# -- module tables ----------------------------------------------------------------------


def test_collect_skips_dot_files_and_unreadable_entries() -> None:
    with pytest.warns(UserWarning):
        empty = tables.collect_module_tables(TABLES_REVIEW, "nope")
    assert empty.shape == (0, 0)
    out = tables.collect_module_tables(TABLES_REVIEW, "mymod")
    # a.rds and B.rds only: not .hidden.rds, the empty table of z0.rds,
    # corrupt.rds, the d.rds/ directory or the foreign c.json
    assert out["paper_id"].tolist() == ["a", "a", "pp1", "pp2"]
    assert list(out.columns) == ["paper_id", "x", "f", "l", "s"]
    files = [os.path.basename(f) for f in tables._saved_files(TABLES_REVIEW)]
    assert ".hidden.rds" not in files and "d.rds" in files


def test_load_reads_a_dot_file_by_its_id() -> None:
    x = tables._load_module_tables(TABLES_REVIEW, ".hidden", None)
    assert list(x.prev_outputs) == ["mymod"]
    assert tables._load_module_tables(TABLES_REVIEW, "empty", None) is None
    assert tables._load_module_tables(TABLES_REVIEW, "corrupt", None) is None


def test_collect_combines_types_like_dplyr() -> None:
    # logical < integer < double; an all-NA logical column takes the other type
    out = tables.collect_module_tables(TABLES_REVIEW.parent / "tables_types", "m")
    assert out["paper_id"].tolist() == ["p1", "p1", "p2", "p2", "p3", "p3"]
    assert str(out["x"].dtype) in ("float64", "Float64")
    assert out["x"].tolist()[:1] == [1.0] and pd.isna(out["x"].iloc[1])
    assert str(out["y"].dtype) in ("string", "object")
    assert out["y"].dropna().tolist() == ["a"]
    assert str(out["z"].dtype) == "Int64"
    assert out["z"].tolist()[:4] == [0, 1, 5, 6]


def test_redirect_writes_takes_the_first_path_like_argument() -> None:
    # R quirk kept: `d` was assigned from read.csv("...csv"), so it counts as a
    # path-like variable and is redirected instead of the real target "o.csv"
    out = core._repro_redirect_writes(['d <- read.csv("a.csv")', 'write.csv(d, "o.csv")'])
    assert out["replacement"].tolist() == ['write.csv("{{REPRO_OUTPUT}}d.csv", "o.csv")']
