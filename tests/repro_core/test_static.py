"""Static helpers of the reproducibility check (port of test-reproducibility_check.R)."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from metacheck.repro import core
from metacheck.repro.core import (
    repro_defined_vars,
    repro_dependencies,
    repro_file_io,
    repro_missing_inputs,
    repro_rewrite_paths,
    repro_run_order,
)

# repro_dependencies() -------------------------------------------------------


def _dep(deps: pd.DataFrame, pkg: str, col: str):
    return deps.loc[deps["package"] == pkg, col].iloc[0]


def test_dependencies_library_require() -> None:
    deps = repro_dependencies(["library(dplyr)", "require(ggplot2)"])
    assert set(deps["package"]) == {"dplyr", "ggplot2"}
    assert (deps["source"] == "cran").all()
    assert not deps["base"].any()


def test_dependencies_namespace_reference() -> None:
    deps = repro_dependencies("x <- stats::sd(1:10)")
    assert deps["package"].tolist() == ["stats"]
    assert bool(deps["base"].iloc[0])


def test_dependencies_tags_base() -> None:
    deps = repro_dependencies(["library(stats)", "library(dplyr)"])
    assert bool(_dep(deps, "stats", "base")) is True
    assert bool(_dep(deps, "dplyr", "base")) is False
    assert _dep(deps, "stats", "source") == "base"


def test_dependencies_github_ref() -> None:
    deps = repro_dependencies(
        ["library(ggplot2)", "remotes::install_github('tidyverse/ggplot2@v3.4.0')"]
    )
    assert _dep(deps, "ggplot2", "source") == "github"
    assert _dep(deps, "ggplot2", "ref") == "tidyverse/ggplot2@v3.4.0"


def test_dependencies_url_source() -> None:
    deps = repro_dependencies(
        [
            "library(pkgname)",
            'install.packages("https://example.org/src/contrib/pkgname_1.0.tar.gz")',
        ]
    )
    assert _dep(deps, "pkgname", "source") == "url"


def test_dependencies_pooled_prefers_github() -> None:
    deps = repro_dependencies(
        [["library(ggplot2)", "remotes::install_github('tidyverse/ggplot2')"]]
    )
    assert _dep(deps, "ggplot2", "source") == "github"


def test_dependencies_pooled_mapping_dedups() -> None:
    deps = repro_dependencies(
        {"a.R": ["library(dplyr)"], "b.R": ["library(dplyr)", "library(tidyr)"]}
    )
    assert deps["package"].tolist() == ["dplyr", "tidyr"]


def test_dependencies_empty() -> None:
    cols = ["package", "source", "ref", "base"]
    assert repro_dependencies(None).columns.tolist() == cols
    assert len(repro_dependencies(None)) == 0
    assert len(repro_dependencies("library(dplyr)", lang="Python")) == 0
    assert len(repro_dependencies([])) == 0


def test_dependencies_bioc() -> None:
    deps = repro_dependencies(["library(edgeR)", "library(dplyr)"])
    assert _dep(deps, "edgeR", "source") == "bioc"
    assert _dep(deps, "dplyr", "source") == "cran"
    assert not bool(_dep(deps, "edgeR", "base"))


def test_dependencies_bioc_github_wins() -> None:
    deps = repro_dependencies(["library(edgeR)", "remotes::install_github('Bioconductor/edgeR')"])
    assert _dep(deps, "edgeR", "source") == "github"


def test_base_packages_follow_the_r_installation(rscript: str) -> None:
    import subprocess

    out = subprocess.run(
        [
            rscript,
            "-e",
            'cat(rownames(installed.packages(priority = c("base", "recommended"))), sep = "\\n")',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(core._repro_base_packages()) == sorted(out)


def test_base_packages_without_r(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_rscript", lambda: None)
    pkgs = core._repro_base_packages()
    assert {"stats", "utils", "MASS", "Matrix", "survival"} <= set(pkgs)


def test_bioc_list_is_unique() -> None:
    pkgs = core._repro_bioc_packages()
    assert len(pkgs) == len(set(pkgs))
    assert "edgeR" in pkgs


# format-string calls, simple variables, write redirection ----------------------


def test_format_call_refs_resolves_placeholders() -> None:
    out = core._repro_format_call_refs(
        [
            'wd <- "/Users/me"',
            "wd_data <- 'raw'",
            'x <- read_csv(sprintf("%s/%s/x.csv", wd, wd_data))',
        ]
    )
    assert out["call_text"].tolist() == ['sprintf("%s/%s/x.csv", wd, wd_data)']
    assert out["resolved"].tolist() == ["/Users/me/raw/x.csv"]
    assert out["line"].tolist() == [3]


def test_format_call_refs_skips_format_specs_and_bare_literals() -> None:
    out = core._repro_format_call_refs(
        ['cat(sprintf("%.2e", est))', 'd <- read.csv(sprintf("Exp1A_Data.txt"))']
    )
    assert len(out) == 0


def test_format_call_refs_unresolved() -> None:
    out = core._repro_format_call_refs(['read.csv(sprintf("%s.csv", unknown_var))'])
    assert out["resolved"].isna().all()


def test_simple_string_vars_keeps_last_assignment() -> None:
    out = core._repro_simple_string_vars(['a <- "1"', 'b <- "2"', 'a <- "3"', "c <- f()"])
    assert out == {"b": "2", "a": "3"}


def test_redirect_writes_shapes() -> None:
    out = core._repro_redirect_writes(
        [
            'f <- paste0("../out/x_", Sys.Date(), ".csv")',
            'write.csv(d, file = "../Data/a.csv", row.names = FALSE)',
            "write.csv(d, f)",
            'd %>% write.csv(paste0("dir/", Sys.Date(), ".csv"))',
            "saveRDS(m, path_from_loop)",
        ]
    )
    assert out["redirected_name"].tolist() == ["a.csv", "f.csv", "write_3.csv"]
    # a named target keeps its name (R makes it positional, U134)
    assert out["replacement"].iloc[0] == (
        'write.csv(d, file = "{{REPRO_OUTPUT}}a.csv", row.names = FALSE)'
    )
    assert out["replacement"].iloc[1] == 'write.csv(d,"{{REPRO_OUTPUT}}f.csv")'


def test_redirect_writes_keeps_file_argument_named() -> None:
    # U134: save(a, b, "path") would save the string as a third object;
    # the redirected call keeps file = (R drops the name)
    out = core._repro_redirect_writes(
        ['save(a, b, file = "ab.RData")', 'ggsave(filename="f.png", p)']
    )
    assert out["replacement"].tolist() == [
        'save(a, b, file = "{{REPRO_OUTPUT}}ab.RData")',
        'ggsave(filename="{{REPRO_OUTPUT}}f.png", p)',
    ]


def test_format_call_refs_file_string_after_first_argument() -> None:
    # U133: the arguments after the file string are found wherever it stands,
    # so paste(dir, "data.csv", sep = "/") is a format call (its whole call is
    # rewritten); R skips from the start of the arguments and misses it
    out = core._repro_format_call_refs(
        [
            'a <- paste(dir, "data.csv", sep = "/")',
            'b <- sprintf("%s/data.csv", d)',
            'c <- paste0(wd, "/x.csv")',
            'd <- paste0("Loaded ", n, " rows from data.csv")',
            # R's offset also read this as a format call for a file ".R"
            'source(paste0(path, ".R"))',
        ]
    )
    assert out["call_text"].tolist() == [
        'paste(dir, "data.csv", sep = "/")',
        'sprintf("%s/data.csv", d)',
    ]
    assert out["fmt"].tolist() == ["data.csv", "%s/data.csv"]


# repro_rewrite_paths() --------------------------------------------------------------

PLAN_2 = pd.DataFrame(
    {
        "file_name": ["demographics.csv", "demographics.csv"],
        "target_path": [
            "study-ex1/data/study-demographics_data.csv",
            "study-ex2/data/study-demographics_data.csv",
        ],
        "current_path": ["ex1/demographics.csv", "ex2/demographics.csv"],
    }
)


def test_rewrite_single_match() -> None:
    plan = PLAN_2.iloc[[0]]
    out = repro_rewrite_paths('d <- read.csv("data/demographics.csv")', "ex1/analysis.R", plan)
    assert len(out) == 1
    assert bool(out["matched"].iloc[0]) and not bool(out["ambiguous"].iloc[0])
    assert out["target"].iloc[0] == "study-ex1/data/study-demographics_data.csv"


def test_rewrite_unmatched() -> None:
    plan = pd.DataFrame(
        {"file_name": ["scores.csv"], "target_path": ["study-ex1/data/scores_data.csv"]}
    )
    out = repro_rewrite_paths('d <- read.csv("data/not_in_plan.csv")', "ex1/analysis.R", plan)
    assert len(out) == 1
    assert not bool(out["matched"].iloc[0])
    assert pd.isna(out["target"].iloc[0])


def test_rewrite_study_group() -> None:
    out = repro_rewrite_paths('d <- read.csv("demographics.csv")', "ex2/analysis.R", PLAN_2)
    assert out["target"].iloc[0] == "study-ex2/data/study-demographics_data.csv"
    assert not bool(out["ambiguous"].iloc[0])


def test_rewrite_still_ambiguous() -> None:
    out = repro_rewrite_paths('d <- read.csv("demographics.csv")', "shared/analysis.R", PLAN_2)
    assert bool(out["matched"].iloc[0]) and bool(out["ambiguous"].iloc[0])
    assert pd.isna(out["target"].iloc[0])


def test_rewrite_same_target_is_not_ambiguous() -> None:
    plan = pd.DataFrame(
        {
            "file_name": ["demographics.csv"] * 2,
            "target_path": ["study-ex1/data/study-demographics_data.csv"] * 2,
        }
    )
    out = repro_rewrite_paths('d <- read.csv("demographics.csv")', "ex1/analysis.R", plan)
    assert bool(out["matched"].iloc[0]) and not bool(out["ambiguous"].iloc[0])


def test_rewrite_original_target() -> None:
    plan = pd.DataFrame(
        {
            "file_name": ["math.dta"],
            "target_path": ["study-ex1/data/study-math_data.csv"],
            "original_target": ["study-ex1/data/math.dta"],
        }
    )
    out = repro_rewrite_paths('d <- haven::read_dta("math.dta")', "ex1/analysis.R", plan)
    assert out["target"].iloc[0] == "study-ex1/data/math.dta"


def test_rewrite_sprintf_call() -> None:
    plan = pd.DataFrame(
        {"file_name": ["exp1_data.csv"], "target_path": ["study-ex1/data/exp1_data.csv"]}
    )
    out = repro_rewrite_paths(
        ['wd <- "exp1"', 'd <- read.csv(sprintf("%s_data.csv", wd))'], "ex1/analysis.R", plan
    )
    assert len(out) == 1
    assert bool(out["is_call"].iloc[0]) and bool(out["matched"].iloc[0])
    assert out["target"].iloc[0] == "study-ex1/data/exp1_data.csv"
    assert "sprintf" in out["ref"].iloc[0]


def test_rewrite_empty() -> None:
    cols = ["ref", "basename", "matched", "target", "ambiguous", "n_candidates", "is_call"]
    plan = pd.DataFrame({"file_name": ["a.csv"], "target_path": ["data/a.csv"]})
    assert (
        repro_rewrite_paths('read.csv("a.csv")', "x.py", plan, lang="Python").columns.tolist()
        == cols
    )
    assert len(repro_rewrite_paths('read.csv("a.csv")', "x.py", plan, lang="Python")) == 0
    assert len(repro_rewrite_paths('read.csv("a.csv")', "x.R", None)) == 0


def test_rewrite_collapses_byte_identical_mirrors(tmp_path: Path) -> None:
    t1, t2 = tmp_path / "a.csv", tmp_path / "b.csv"
    t1.write_text("a,b\n1,2\n")
    t2.write_text("a,b\n1,2\n")
    structure_df = pd.DataFrame(
        {"file_name": ["demographics.csv"] * 2, "file_location": [str(t1), str(t2)]}
    )
    plan = pd.DataFrame(
        {
            "file_name": ["demographics.csv"] * 2,
            "target_path": [
                "study-ex1/data/study-demographics_data.csv",
                "study-ex3/data/study-demographics_data.csv",
            ],
        }
    )
    out = repro_rewrite_paths(
        'd <- read.csv("demographics.csv")', "ex1/analysis.R", plan, structure_df=structure_df
    )
    assert len(out) == 1
    assert bool(out["matched"].iloc[0]) and not bool(out["ambiguous"].iloc[0])


def test_rewrite_mirror_tie_keeps_the_first_listed(tmp_path: Path) -> None:
    # U135: byte-identical mirrors split evenly between study groups: the
    # first listed is kept, as metacheck's comment says (its which.max(table())
    # keeps the alphabetically first group, study-ex2 here)
    t1, t2 = tmp_path / "a.csv", tmp_path / "b.csv"
    t1.write_text("a,b\n1,2\n")
    t2.write_text("a,b\n1,2\n")
    structure_df = pd.DataFrame(
        {
            "file_name": ["a/demographics.csv", "b/demographics.csv"],
            "file_location": [str(t1), str(t2)],
        }
    )
    targets = ["study-ex3/data/d.csv", "study-ex2/data/d.csv"]
    for order in (targets, targets[::-1]):
        plan = pd.DataFrame(
            {"file_name": ["a/demographics.csv", "b/demographics.csv"], "target_path": order}
        )
        out = repro_rewrite_paths(
            'd <- read.csv("demographics.csv")',
            "shared/analysis.R",
            plan,
            structure_df=structure_df,
        )
        assert out["target"].tolist() == [order[0]]
        assert not bool(out["ambiguous"].iloc[0])


# repro_run_order() -------------------------------------------------------------------


def _files(names, reads, writes, sources) -> pd.DataFrame:
    from tests.repro_core.parity_support import files_df

    return files_df(names, reads, writes, sources)


def _order(out: pd.DataFrame) -> dict[str, int]:
    return dict(zip(out["file_name"], out["order"], strict=True))


def test_run_order_read_after_write() -> None:
    out = repro_run_order(
        _files(["analysis.R", "prep.R"], ["clean.csv", []], [[], "clean.csv"], [[], []])
    )
    ord_ = _order(out)
    assert ord_["prep.R"] < ord_["analysis.R"]
    row = out[out["file_name"] == "analysis.R"].iloc[0]
    assert row["order_basis"] == "dependency"
    assert row["depends_on"] == "prep.R"


def test_run_order_source_edges() -> None:
    out = repro_run_order(_files(["main.R", "helper.R"], [[], []], [[], []], ["helper.R", []]))
    assert _order(out)["helper.R"] < _order(out)["main.R"]


def test_run_order_numeric_prefixes() -> None:
    out = repro_run_order(_files(["1_analysis.R", "0_prep.R"], [[], []], [[], []], [[], []]))
    assert _order(out)["0_prep.R"] < _order(out)["1_analysis.R"]
    assert (out["order_basis"] == "numeric").all()


def test_run_order_all_digit_runs() -> None:
    out = repro_run_order(
        _files(["Exp2_1_import.R", "Exp1_02_preprocessing.R"], [[], []], [[], []], [[], []])
    )
    assert _order(out)["Exp1_02_preprocessing.R"] < _order(out)["Exp2_1_import.R"]


def test_run_order_cycle() -> None:
    out = repro_run_order(
        _files(["a.R", "b.R"], ["b_out.csv", "a_out.csv"], ["a_out.csv", "b_out.csv"], [[], []])
    )
    assert set(out.attrs["cycle"]) == {"a.R", "b.R"}
    assert out["order"].isna().all()


def test_run_order_ambiguous() -> None:
    out = repro_run_order(_files(["a.R", "b.R"], [[], []], [[], []], [[], []]))
    assert out.attrs["ambiguous"] is True


def test_run_order_single_file() -> None:
    out = repro_run_order(_files(["solo.R"], [[]], [[]], [[]]))
    assert out.attrs["ambiguous"] is False
    assert out["order"].tolist() == [1]


def test_run_order_empty() -> None:
    assert len(repro_run_order(pd.DataFrame({"file_name": pd.Series([], dtype="string")}))) == 0


def test_run_order_extra_edges() -> None:
    out = repro_run_order(
        _files(["a.R", "b.R"], [[], []], [[], []], [[], []]), extra_edges=[("b.R", "a.R")]
    )
    assert _order(out)["b.R"] < _order(out)["a.R"]


def test_run_order_fuzzy_source() -> None:
    out = repro_run_order(
        _files(
            ["201 - Analysis.R", "103 - Data Prep.R"], [[], []], [[], []], ["001 - data prep.R", []]
        )
    )
    assert _order(out)["103 - Data Prep.R"] < _order(out)["201 - Analysis.R"]
    fuzzy = out.attrs["fuzzy_sources"]
    assert len(fuzzy) == 1
    assert fuzzy["from"].iloc[0] == "103 - Data Prep.R"
    assert fuzzy["to"].iloc[0] == "201 - Analysis.R"


def test_run_order_exact_source_is_not_fuzzy() -> None:
    out = repro_run_order(_files(["main.R", "helper.R"], [[], []], [[], []], ["helper.R", []]))
    assert len(out.attrs["fuzzy_sources"]) == 0


def test_run_order_ambiguous_fuzzy_is_unresolved() -> None:
    out = repro_run_order(
        _files(
            ["main.R", "01_prep.R", "02_prep.R"], [[], [], []], [[], [], []], ["00_prep.R", [], []]
        )
    )
    assert len(out.attrs["fuzzy_sources"]) == 0


def test_normalize_basename() -> None:
    assert core._repro_normalize_basename("001 - data prep.R") == "dataprep"
    assert core._repro_normalize_basename(["103 - Data Prep.R", "x.y.R"]) == ["dataprep", "xy"]


# repro_file_io() / repro_defined_vars() -------------------------------------------------


def test_file_io_reads_vs_writes() -> None:
    io = repro_file_io(
        {
            "prep.R": ['dat <- read.csv("raw.csv")', 'write.csv(dat, "clean.csv")'],
            "analysis.R": ['dat <- read.csv("clean.csv")'],
        }
    )
    prep = io[io["file_name"] == "prep.R"].iloc[0]
    assert prep["reads"] == ["raw.csv"]
    assert prep["writes"] == ["clean.csv"]
    analysis = io[io["file_name"] == "analysis.R"].iloc[0]
    assert analysis["reads"] == ["clean.csv"]


def test_file_io_sources() -> None:
    io = repro_file_io({"main.R": ['source("helper.R")']})
    assert io["sources"].iloc[0] == ["helper.r"]
    assert io["reads"].iloc[0] == []


def test_file_io_empty() -> None:
    assert len(repro_file_io({})) == 0


def test_defined_vars_top_level() -> None:
    out = repro_defined_vars({"a.R": ["x <- 1", "  y <- 2", "assign('z', 3)"]})
    assert set(out["defines"].iloc[0]) == {"x", "z"}


def test_defined_vars_empty() -> None:
    assert len(repro_defined_vars({})) == 0


# repro_missing_inputs() ------------------------------------------------------------------


def test_missing_inputs_absent() -> None:
    out = repro_missing_inputs("missing.csv", plan=None, structure_df=None)
    assert out["status"].tolist() == ["absent"]


def test_missing_inputs_withheld() -> None:
    skipped = pd.DataFrame({"file_name": ["big.csv"], "file_size": [200 * 1024 * 1024]})
    out = repro_missing_inputs("big.csv", plan=None, structure_df=None, skipped=skipped)
    assert out["status"].tolist() == ["withheld_size"]
    assert "200 MB" in out["detail"].iloc[0]


def test_missing_inputs_withheld_without_size_column() -> None:
    # U135: without a file_size column the size is left out (R stops with
    # "argument is of length zero")
    skipped = pd.DataFrame({"file_name": ["big.csv"]})
    out = repro_missing_inputs("big.csv", plan=None, structure_df=None, skipped=skipped)
    assert out["status"].tolist() == ["withheld_size"]
    assert out["detail"].tolist() == [
        "referenced file is in the repository but was not downloaded: over the size cap"
    ]


def test_classify_error_typographic_quotes() -> None:
    # U132: in a UTF-8 locale R quotes with sQuote(): "‘pkg’"
    for msg in (
        "Error in library(notapkg) : there is no package called ‘notapkg’",
        "Error in library(notapkg) : there is no package called 'notapkg'",
    ):
        assert core._classify_error(["notapkg"], [msg]) == (None, True)
    assert core._classify_error(["other"], ["there is no package called ‘notapkg’"]) == (
        None,
        False,
    )
    assert core._classify_error([], ["Error: object ‘x’ not found"]) == ("x", False)


def test_archive_listing_line_without_date() -> None:
    # U135: a tarball line without a date sorts last instead of failing the
    # install (R calls as.POSIXct() outside its tryCatch())
    assert core._parse_archive_date("no date here") == -math.inf
    assert core._parse_archive_date("2020-01-02 03:04") > 0


def test_run_order_derives_io_from_code_text() -> None:
    # U135: as metacheck documents, missing reads/writes/sources come from a
    # code_text column (its code treats them as empty)
    files = pd.DataFrame({"file_name": ["1_analysis.R", "0_prep.R", "z.R"]})
    files["code_text"] = [
        ['d <- read.csv("clean.csv")'],
        ['x <- read.csv("raw.csv")', 'write.csv(x, "clean.csv")'],
        ['source("1_analysis.R")'],
    ]
    out = core.repro_run_order(files)
    assert out["file_name"].tolist() == ["1_analysis.R", "0_prep.R", "z.R"]
    assert out["order"].tolist() == [2, 1, 3]
    assert out["depends_on"].tolist() == ["0_prep.R", "", "1_analysis.R"]
    # a file whose code could not be read (NA code_text) has no I/O
    files["code_text"] = [['d <- read.csv("clean.csv")'], ['write.csv(x, "clean.csv")'], None]
    out = core.repro_run_order(files)
    assert out["order"].tolist() == [2, 1, 3]
    assert out["depends_on"].tolist() == ["0_prep.R", "", ""]


def test_missing_inputs_not_downloaded() -> None:
    sdf = pd.DataFrame({"file_name": ["notdl.csv"], "file_location": [None]})
    out = repro_missing_inputs("notdl.csv", plan=None, structure_df=sdf)
    assert out["status"].tolist() == ["in_repo_not_downloaded"]


def test_missing_inputs_present_is_dropped(tmp_path: Path) -> None:
    f = tmp_path / "present.csv"
    f.write_text("a,b\n")
    sdf = pd.DataFrame({"file_name": ["present.csv"], "file_location": [str(f)]})
    assert len(repro_missing_inputs("present.csv", plan=None, structure_df=sdf)) == 0


def test_missing_inputs_similar_name() -> None:
    sdf = pd.DataFrame({"file_name": ["data.xlsx", "scores_v2.csv"], "file_location": [None, None]})
    out = repro_missing_inputs(["Data.csv", "scores_v1.csv"], plan=None, structure_df=sdf)
    assert out["similar_to"].tolist() == ["data.xlsx", "scores_v2.csv"]


def test_missing_inputs_empty() -> None:
    assert len(repro_missing_inputs([], plan=None, structure_df=None)) == 0


# .repro_content_sniff() / .repro_is_jags_model() -------------------------------------------


def test_content_sniff() -> None:
    assert core._repro_content_sniff(['{"x":{"material":"phong","data":[1,2,3]}}']) == "JSON"
    assert core._repro_content_sniff(["<!DOCTYPE html>", "<html><body>hi</body></html>"]) == "HTML"
    assert core._repro_content_sniff(['<?xml version="1.0"?>', "<root></root>"]) == "XML"
    assert core._repro_content_sniff(["{", "  x <- 1", "  y <- 2", "}", "print(x + y)"]) is None
    assert core._repro_content_sniff([]) is None
    assert core._repro_content_sniff(None) is None


def test_is_jags_model() -> None:
    assert core._repro_is_jags_model(["model {", "  psi ~ dlogis(0, 1)", "}"])
    assert core._repro_is_jags_model("x <- 1", file_name="study1/jags_script/conditional_model.R")
    assert core._repro_is_jags_model(
        ["psi ~ dlogis(0,1)"],
        file_name="model_def.R",
        other_code_text=[['m <- rjags::jags.model(file = "model_def.R", data = d)']],
    )
    assert not core._repro_is_jags_model(["x <- 1", "y <- 2"], file_name="analysis.R")


# .repro_find_export_pkg() (asks R which packages export a name) ---------------------------


def test_find_export_pkg(rscript: str) -> None:
    import subprocess

    has_testthat = (
        subprocess.run(
            [rscript, "-e", 'quit(status = !requireNamespace("testthat", quietly = TRUE))'],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    if has_testthat:
        assert core._repro_find_export_pkg("test_that", ["testthat", "dplyr"]) == "testthat"
    assert (
        core._repro_find_export_pkg("not_a_real_exported_name_xyz", ["testthat", "dplyr"]) is None
    )


def test_find_export_pkg_without_candidates() -> None:
    assert core._repro_find_export_pkg("test_that", []) is None


def test_find_export_pkg_without_r(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_rscript", lambda: None)
    assert core._repro_find_export_pkg("test_that", ["testthat"]) is None


def test_common_pkgs() -> None:
    assert core._repro_common_pkgs()[:2] == ["testthat", "magrittr"]


# .repro_classify_install_message() ----------------------------------------------------------


@pytest.mark.parametrize(
    ("msg", "category"),
    [
        ("installation of package 'x' had non-zero exit status", "compile_failure"),
        ("Could not resolve host: cloud.r-project.org", "network"),
        ("there is no package called 'Rcpp'", "transitive_dependency_missing"),
        ("package 'foo' is not available for this version of R", "cran_unavailable"),
        ("HTTP error 404. Not Found", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_classify_install_message(msg: str | None, category: str) -> None:
    assert core._repro_classify_install_message(msg) == category


# package-list variables and install failures (issue #421) -------------------


def test_not_loadable_msg_reports_the_unavailable_warning() -> None:
    # install.packages() only WARNS for a name it cannot find, so the load
    # check reported "installed but package 'x' is not loadable" -- claiming
    # an install that never happened, classified as uncategorised. The
    # warning is the real reason and is reported instead.
    warn = "package 'list.packages' is not available for this version of R"
    msg = core._repro_not_loadable_msg("list.packages", ["other warning", warn])
    assert msg == warn
    assert core._repro_classify_install_message(msg) == "cran_unavailable"

    # with the CRAN Archive retry's own result appended, as repro_install_deps()
    # does: a package the Archive has no folder for is not a network problem
    msg2 = f"{msg} (CRAN Archive retry also failed: package not found in the CRAN Archive)"
    assert core._repro_classify_install_message(msg2) == "cran_unavailable"

    # without such a warning the original wording is kept
    assert core._repro_not_loadable_msg("pkg", []) == "installed but package 'pkg' is not loadable"
    assert core._repro_not_loadable_msg("pkg", None) == (
        "installed but package 'pkg' is not loadable"
    )
    # repeated warnings are reported once, several distinct ones are joined
    a, b = "package 'a' is not available (for R version 4.5.3)", warn
    assert core._repro_not_loadable_msg("pkg", [a, b, a]) == f"{a}; {b}"


def test_dependencies_does_not_list_a_package_list_variable() -> None:
    deps = repro_dependencies(
        [
            'list.packages <- c("activity", "bbmle")',
            "for (req.lib in list.packages) {",
            "  if (!require(req.lib, character.only = TRUE)) install.packages(req.lib)",
            "}",
        ]
    )
    assert set(deps["package"]) == {"activity", "bbmle"}
