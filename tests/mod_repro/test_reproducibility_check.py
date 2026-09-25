"""The reproducibility_check module (port of test-module-reproducibility_check.R).

Static-analysis tests run offline; ``execute=True`` tests run real ``Rscript``
subprocesses against metacheck's ``tests/testthat/fixtures/repro`` scripts
(base R only). The Docker backend, dependency installs and the concurrent
multi-paper dispatcher are exercised with their external steps mocked.
"""

from __future__ import annotations

import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleOutput, module_find, module_list, module_run
from pytacheck.modules import _reproducibility as h
from pytacheck.report.blocks import ReportTable
from tests.mod_repro import helpers as rh

ROOT = Path(__file__).resolve().parents[2]


def fake_module_output(
    module: str, table: Any, paper: Any, extra: dict[str, Any] | None = None
) -> ModuleOutput:
    """A module output to chain from (R: ``fake_module_output()``)."""
    return ModuleOutput(
        module=module,
        title=module,
        section="results",
        table=table,
        paper=paper,
        summary_table=pd.DataFrame({"paper_id": pc.paper_id(paper)}),
        extras=dict(extra or {}),
    )


def code_tbl_row(
    paper: Any,
    file_name: str | list[str],
    file_location: str | list[str | None] | None,
    language: str = "R",
    packages: str = "",
    parse_error: bool = False,
) -> pd.DataFrame:
    """code_check table rows (R: ``code_tbl_row()``)."""
    names = [file_name] if isinstance(file_name, str) else file_name
    locs = file_location if isinstance(file_location, list) else [file_location]
    n = len(names)
    return pd.DataFrame(
        {
            "paper_id": [pc.paper_id(paper)[0]] * n,
            "file_name": names,
            "file_location": locs,
            "file_url": [None] * n,
            "language": [language] * n,
            "packages": [packages] * n,
            "parse_error": [parse_error] * n,
        }
    )


def chain(
    paper: Any,
    code_tbl: pd.DataFrame,
    structure_df: pd.DataFrame | None = None,
    plan_df: pd.DataFrame | None = None,
    **extra: Any,
) -> ModuleOutput:
    fake = fake_module_output("code_check", code_tbl, paper, extra)
    fake.prev_outputs = {}
    if structure_df is not None:
        fake.prev_outputs["data_check"] = {"structure": structure_df}
    if plan_df is not None:
        fake.prev_outputs["psychds_check"] = {"table": plan_df}
    return fake


def report_text(mo: Any) -> str:
    def flat(x: Any) -> list[str]:
        if isinstance(x, str):
            return [x]
        if isinstance(x, ReportTable):
            return []
        if isinstance(x, list):
            return [s for v in x for s in flat(v)]
        return []

    return "\n".join(flat(mo.report))


@pytest.fixture
def paper() -> pc.Paper:
    p = pc.test_paper()
    p.paper_id = "p1"
    return p


PLAN = pd.DataFrame({"file_name": ["data.csv"], "target_path": ["data.csv"]})


def data_structure(paper: Any, repro_fixture: Any) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "paper_id": [pc.paper_id(paper)[0]],
            "file_name": ["data.csv"],
            "file_location": [repro_fixture("data.csv")],
        }
    )


# -- static analysis (execute = FALSE, the default) --------------------------------------


def test_registered_and_na_with_no_code_files() -> None:
    assert "reproducibility_check" in module_list()["name"].tolist()
    p = pc.test_paper("no code here")
    mo = module_run(p, "reproducibility_check")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert mo.summary_table["repro_code_n"].tolist() == [0]


def test_static_runnable_single_script_is_green(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = code_tbl_row(paper, "ok.R", repro_fixture("ok.R"))
    structure_df = pd.DataFrame(
        {
            "paper_id": ["p1", "p1"],
            "file_name": ["ok.R", "data.csv"],
            "file_location": [repro_fixture("ok.R"), repro_fixture("data.csv")],
        }
    )
    # no psychds_check plan in the chain: the module runs psychds_check itself
    mo = module_run(chain(paper, code_tbl, structure_df), "reproducibility_check")
    assert mo.table["file_name"].tolist() == ["ok.R"]
    assert mo.table["runnable"].tolist() == [True]
    assert mo.table["parses"].tolist() == [True]
    assert mo.table["outcome"].isna().all()
    assert "static analysis; no code was run" in mo.summary_text
    assert mo.traffic_light == "green"
    assert mo.run_results is None


def test_static_missing_input_is_diagnosed(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = code_tbl_row(paper, "missing_input.R", repro_fixture("missing_input.R"))
    structure_df = pd.DataFrame(
        {
            "paper_id": ["p1"],
            "file_name": ["missing_input.R"],
            "file_location": [repro_fixture("missing_input.R")],
        }
    )
    plan_df = pd.DataFrame(
        {
            "file_name": ["some_other_file.csv"],
            "target_path": ["study-ex1/data/some_other_file.csv"],
        }
    )
    mo = module_run(chain(paper, code_tbl, structure_df, plan_df), "reproducibility_check")
    assert mo.table["runnable"].tolist() == [False]
    assert mo.table["not_runnable_reason"].tolist() == ["missing_input"]
    assert "not_in_repo.csv" in mo.table["unresolved_inputs"].iloc[0]
    assert mo.traffic_light == "yellow"


def test_static_read_after_write_orders_pipeline(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = code_tbl_row(
        paper,
        ["writes_then_reads.R", "reads_written.R"],
        [repro_fixture("writes_then_reads.R"), repro_fixture("reads_written.R")],
    )
    mo = module_run(
        chain(paper, code_tbl, data_structure(paper, repro_fixture)), "reproducibility_check"
    )
    order = dict(zip(mo.table["file_name"], mo.table["run_order"], strict=True))
    assert order["writes_then_reads.R"] < order["reads_written.R"]
    assert mo.table["reads"].tolist()[1] == ["intermediate.csv"]
    assert mo.table["writes"].tolist()[0] == ["intermediate.csv"]


def test_spss_data_without_syntax_is_red(paper: pc.Paper) -> None:
    structure_df = pd.DataFrame(
        {"paper_id": ["p1"], "file_name": ["data.sav"], "file_location": [None]}
    )
    fake = fake_module_output("data_check", pd.DataFrame(), paper, {"structure": structure_df})
    mo = module_run(fake, "reproducibility_check")
    assert mo.traffic_light == "red"
    assert "SPSS data without syntax" in report_text(mo)


def test_spss_data_with_syntax_is_yellow(paper: pc.Paper) -> None:
    structure_df = pd.DataFrame(
        {
            "paper_id": ["p1", "p1"],
            "file_name": ["data.sav", "syntax.sps"],
            "file_location": [None, None],
        }
    )
    fake = fake_module_output("data_check", pd.DataFrame(), paper, {"structure": structure_df})
    mo = module_run(fake, "reproducibility_check")
    assert mo.traffic_light == "yellow"
    assert "#### SPSS data\n" in report_text(mo) + "\n"


def test_stata_do_without_data_or_output_names_the_gap(paper: pc.Paper) -> None:
    code_tbl = code_tbl_row(paper, "analysis.do", None, language="Stata")
    structure_df = pd.DataFrame(
        {"paper_id": ["p1"], "file_name": ["analysis.do"], "file_location": [None]}
    )
    mo = module_run(chain(paper, code_tbl, structure_df, PLAN), "reproducibility_check")
    assert mo.traffic_light == "info"
    assert "Stata code without data or output" in report_text(mo)


def test_byte_identical_duplicates_run_once(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = code_tbl_row(paper, ["ok.R", "ok.R"], [repro_fixture("ok.R"), repro_fixture("ok.R")])
    mo = module_run(
        chain(paper, code_tbl, data_structure(paper, repro_fixture)), "reproducibility_check"
    )
    assert len(mo.table) == 1
    assert "Duplicate files across repo mirrors" in report_text(mo)
    assert "skipped as byte-identical duplicate" in mo.summary_text


def test_same_name_different_content_both_kept(paper: pc.Paper, area_fixtures: Path) -> None:
    s = area_fixtures / "scripts"
    code_tbl = code_tbl_row(
        paper,
        ["analysis.R", "analysis.R"],
        [str(s / "mirror_a" / "analysis.R"), str(s / "mirror_b" / "analysis.R")],
    )
    mo = module_run(chain(paper, code_tbl, None, PLAN), "reproducibility_check")
    assert mo.table["file_name"].tolist() == ["analysis.R", "analysis.R"]
    assert "Duplicate files" not in report_text(mo)


def test_non_r_content_and_jags_are_not_parse_errors(paper: pc.Paper, area_fixtures: Path) -> None:
    s = area_fixtures / "scripts"
    code_tbl = code_tbl_row(
        paper,
        ["json_dump.R", "jags_model.R"],
        [str(s / "json_dump.R"), str(s / "jags_model.R")],
        parse_error=True,
    )
    mo = module_run(chain(paper, code_tbl, None, PLAN), "reproducibility_check")
    assert mo.table["not_runnable_reason"].tolist() == ["not_r_content", "jags_model"]
    assert mo.table["parses"].tolist() == [True, True]
    text = report_text(mo)
    assert "#### Non-R content detected" in text
    assert "#### Parsing" not in text


def test_renv_bootstrap_is_not_analysis_code(paper: pc.Paper, area_fixtures: Path) -> None:
    code_tbl = code_tbl_row(
        paper, "renv/activate.R", str(area_fixtures / "scripts" / "renv" / "activate.R")
    )
    mo = module_run(chain(paper, code_tbl, None, PLAN), "reproducibility_check")
    assert mo.traffic_light == "na"
    assert "this phase runs R code only" in mo.summary_text


def test_dependencies_include_code_check_packages(paper: pc.Paper, area_fixtures: Path) -> None:
    code_tbl = code_tbl_row(
        paper,
        "deps.R",
        str(area_fixtures / "scripts" / "deps.R"),
        packages="dplyr, ggplot2, lme4, utils",
    )
    mo = module_run(chain(paper, code_tbl, None, PLAN), "reproducibility_check")
    # dplyr, ggplot2 (github), remotes (remotes::) and lme4 (from code_check's
    # packages) need installing; utils is a base package
    assert mo.summary_table["repro_deps"].tolist() == [4]
    assert "4 installable dependencies detected." in mo.summary_text


def test_self_reproducible_output_is_extracted_and_matched(area_fixtures: Path) -> None:
    p = pc.test_paper("The effect was significant, t(67) = 3.75, p < .001.")
    p.paper_id = "p1"
    o = area_fixtures / "outputs"
    structure_df = pd.DataFrame(
        {
            "paper_id": ["p1", "p1"],
            "file_name": ["sample.jasp", "sample.omv"],
            "file_location": [str(o / "sample.jasp"), str(o / "sample.omv")],
        }
    )
    code_tbl = code_tbl_row(p, [], [])
    mo = module_run(chain(p, code_tbl, structure_df, PLAN), "reproducibility_check")
    assert mo.traffic_light == "yellow"
    assert [s["file"] for s in mo.stat_output] == ["sample.jasp", "sample.omv"]
    assert mo.match_table is not None and mo.match_table["found"].any()
    assert mo.summary_table["repro_tests_matched"].tolist() == [1]
    assert mo.get("sandbox") is None


def test_keep_sandbox_surfaces_statistical_output(area_fixtures: Path) -> None:
    p = pc.test_paper()
    p.paper_id = "p1"
    o = area_fixtures / "outputs"
    structure_df = pd.DataFrame(
        {"paper_id": ["p1"], "file_name": ["sample.omv"], "file_location": [str(o / "sample.omv")]}
    )
    fake = fake_module_output("data_check", pd.DataFrame(), p, {"structure": structure_df})
    fake.prev_outputs = {"psychds_check": {"table": PLAN}}
    mo = module_run(fake, "reproducibility_check", keep_sandbox=True)
    root = Path(mo.sandbox)
    try:
        assert (root / "statistical_output" / "results_long.csv").exists()
        assert (root / "output").is_dir()
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_throwaway_sandbox_is_removed(area_fixtures: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    made: list[str] = []
    real = tempfile.mkdtemp

    def spy(*args: Any, **kwargs: Any) -> str:
        d = real(*args, **kwargs)
        made.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    p = pc.test_paper()
    p.paper_id = "p1"
    o = area_fixtures / "outputs"
    structure_df = pd.DataFrame(
        {"paper_id": ["p1"], "file_name": ["sample.omv"], "file_location": [str(o / "sample.omv")]}
    )
    fake = fake_module_output("data_check", pd.DataFrame(), p, {"structure": structure_df})
    mo = module_run(fake, "reproducibility_check")
    sandboxes = [d for d in made if os.path.basename(d).startswith("repro_sandbox_")]
    assert sandboxes and not any(os.path.exists(d) for d in sandboxes)
    assert mo.get("sandbox") is None


def test_tables_dir_replaces_upstream_modules(tmp_path: Path, repro_fixture: Any) -> None:
    from pytacheck.repro import capture_module_tables

    p = pc.test_paper()
    p.paper_id = "p1"
    saved = [
        ModuleOutput(
            module="code_check",
            title="Code Check",
            section="results",
            table=code_tbl_row(p, "ok.R", repro_fixture("ok.R")),
        ),
        ModuleOutput(
            module="data_check",
            title="Data Check",
            section="results",
            extras={"structure": data_structure(p, repro_fixture)},
        ),
        ModuleOutput(module="psychds_check", title="Psych-DS", section="results", table=PLAN),
    ]
    capture_module_tables(saved, tmp_path, paper_id="p1")
    mo = module_run(p, "reproducibility_check", tables_dir=str(tmp_path))
    assert mo.traffic_light == "green"
    assert mo.table["file_name"].tolist() == ["ok.R"]


def test_paperlist_is_checked_as_one_unit(repro_fixture: Any) -> None:
    papers = pc.read([ROOT / f for f in rh.PSYCHSCI])
    ids = pc.paper_id(papers)
    code_tbl = pd.DataFrame(
        {
            "paper_id": ids[:2],
            "file_name": ["ok.R", "errors.R"],
            "file_location": [repro_fixture("ok.R"), repro_fixture("errors.R")],
            "language": ["R", "R"],
        }
    )
    mo = module_run(
        chain(papers, code_tbl, data_structure(papers, repro_fixture), PLAN),
        "reproducibility_check",
    )
    assert mo.table["paper_id"].tolist() == ids[:2]
    # metacheck's .pid(): the summary row belongs to the first paper
    st = mo.summary_table.set_index("paper_id")
    assert st.loc[ids[0], "repro_code_n"] == 2
    assert (st.loc[ids[1:], "repro_code_n"] == 0).all()


def test_inputs_are_not_mutated(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = code_tbl_row(paper, "ok.R", repro_fixture("ok.R"))
    structure_df = data_structure(paper, repro_fixture)
    before = (code_tbl.copy(), structure_df.copy(), PLAN.copy())
    module_run(chain(paper, code_tbl, structure_df, PLAN), "reproducibility_check")
    pd.testing.assert_frame_equal(code_tbl, before[0])
    pd.testing.assert_frame_equal(structure_df, before[1])
    pd.testing.assert_frame_equal(PLAN, before[2])


def test_null_paper_is_an_error() -> None:
    # metacheck's paper_id(NULL) fails, and so does the module
    fake = ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=pd.DataFrame(),
        paper=None,
        summary_table=pd.DataFrame({"paper_id": []}),
        extras={"structure": pd.DataFrame({"file_name": ["data.sav"], "file_location": [None]})},
    )
    with pytest.raises(pc.ModuleError, match="paper must be a paper or paperlist object"):
        module_run(fake, "reproducibility_check")


def test_argument_checks(paper: pc.Paper, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(pc.ModuleError, match="should be one of"):
        module_run(paper, "reproducibility_check", sandbox="vm")
    from pytacheck.repro import core

    monkeypatch.setattr(core, "_rscript", lambda: None)
    with pytest.raises(pc.ModuleError, match='sandbox = "process" needs R'):
        module_run(paper, "reproducibility_check", execute=True)
    from pytacheck.repro import docker

    monkeypatch.setattr(docker, "repro_docker_available", lambda: {"ok": False, "msg": "no daemon"})
    with pytest.raises(pc.ModuleError, match='sandbox = "docker": no daemon'):
        module_run(paper, "reproducibility_check", execute=True, sandbox="docker")


def test_module_metadata() -> None:
    import inspect

    spec = module_find("reproducibility_check")
    assert spec.title == "Reproducibility Check"
    assert spec.section == "results"
    assert spec.requires == ("network",)
    sig = inspect.signature(spec.func)
    assert list(sig.parameters) == [
        "paper",
        "local_path",
        "local_only",
        "model",
        "params",
        "execute",
        "sandbox",
        "docker_use_declared_version",
        "install_missing",
        "cran_install_main",
        "timeout",
        "keep_sandbox",
        "cache",
        "download",
        "skip_types",
        "peek_zips",
        "max_file_size",
        "max_download_size",
        "skip_on_api_limit",
        "tables_dir",
        "workers",
        "results_dir",
    ]
    assert sig.parameters["timeout"].default == 600
    assert sig.parameters["download"].default == "data"


# -- bibr export schema 12.x papers ----------------------------------------------------------


def test_bibr12_paper_matches_its_legacy_reading(tmp_path: Path) -> None:
    """A native 12.x paper and its legacy reading give the same result."""
    paper12 = pc.read(ROOT / "tests" / "fixtures" / "bibr_v12_full.json")
    legacy = pc.read(pc.paper_write(paper12, "legacy", tmp_path, schema_version=None))
    assert pc.paper_id(paper12) == pc.paper_id(legacy) == ["demo"]
    for name in ("bibr12", "bibr12_nocode", "ok_plan", "jasp_only"):
        a = rh.rc_run(name, paper=paper12)
        b = rh.rc_run(name, paper=legacy)
        assert a.traffic_light == b.traffic_light
        assert a.summary_text == b.summary_text
        pd.testing.assert_frame_equal(a.summary_table, b.summary_table)
        if isinstance(a.table, pd.DataFrame) and len(a.table):
            pd.testing.assert_frame_equal(a.table, b.table)
        assert report_text(a) == report_text(b)


def test_bibr12_paper_real_chain() -> None:
    """The fallback data_check/psychds_check/code_check chain runs on a 12.x paper."""
    paper12 = pc.read(ROOT / "tests" / "fixtures" / "bibr_v12_full.json")
    mo = module_run(paper12, "reproducibility_check", local_only=True)
    assert mo.summary_table["paper_id"].tolist() == ["demo"]
    assert mo.traffic_light in ("na", "info", "yellow", "red", "green")


def test_bibr12_paper_is_default_reading() -> None:
    paper12 = pc.read(ROOT / "tests" / "fixtures" / "bibr_v12_full.json")
    mo = rh.rc_run("bibr12", paper=paper12)
    assert mo.summary_table["paper_id"].tolist() == ["demo"]
    assert mo.table["paper_id"].tolist() == ["demo"]
    assert len(mo.stat_output) == 2


# -- execute = TRUE (runs real Rscript subprocesses) -----------------------------------------


def exec_chain(paper: Any, script: str, repro_fixture: Any) -> ModuleOutput:
    code_tbl = code_tbl_row(paper, script, repro_fixture(script))
    return chain(paper, code_tbl, data_structure(paper, repro_fixture), PLAN)


@pytest.mark.slow
def test_execute_runs_script_and_records_ran_ok(
    rscript: str, paper: pc.Paper, repro_fixture: Any
) -> None:
    mo = module_run(
        exec_chain(paper, "ok.R", repro_fixture), "reproducibility_check", execute=True, timeout=60
    )
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    assert mo.run_results["outcome"].tolist() == ["ran_ok"]
    assert "Welch Two Sample t-test" in mo.run_results["stdout"].iloc[0]
    assert "assessed AND ran" in mo.summary_text
    assert mo.summary_table["repro_ran_ok"].tolist() == [1]


@pytest.mark.slow
def test_execute_error_forces_red(rscript: str, paper: pc.Paper, repro_fixture: Any) -> None:
    mo = module_run(
        exec_chain(paper, "errors.R", repro_fixture),
        "reproducibility_check",
        execute=True,
        timeout=60,
    )
    assert mo.traffic_light == "red"
    assert mo.table["outcome"].tolist() == ["errored"]
    assert "deliberate failure" in mo.run_results["error"].iloc[0]


@pytest.mark.slow
def test_execute_strips_setwd(rscript: str, paper: pc.Paper, repro_fixture: Any) -> None:
    mo = module_run(
        exec_chain(paper, "bad_setwd.R", repro_fixture),
        "reproducibility_check",
        execute=True,
        timeout=60,
    )
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    assert mo.table["setwd_removed"].tolist() == [1]
    assert "setwd" in report_text(mo)
    mods = mo.modifications
    assert mods.loc[mods["change_type"] == "setwd_removed", "detail"].tolist() == [
        "/Users/original_author/project"
    ]


@pytest.mark.slow
def test_execute_output_feeds_stat_output_and_match(rscript: str, repro_fixture: Any) -> None:
    p = pc.test_paper("t(4.96) = -4.04, p = .010.")
    p.paper_id = "p1"
    mo = module_run(
        exec_chain(p, "ok.R", repro_fixture), "reproducibility_check", execute=True, timeout=60
    )
    assert len(mo.stat_output) > 0
    assert mo.stat_output[0]["source"] == "r_output"
    assert len(mo.match_table) > 0
    assert mo.match_table["found"].any()


@pytest.mark.slow
def test_execute_model_described_across_statements_unites(
    rscript: str, paper: pc.Paper, repro_fixture: Any
) -> None:
    mo = module_run(
        exec_chain(paper, "model_object.R", repro_fixture),
        "reproducibility_check",
        execute=True,
        timeout=60,
    )
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    long = pd.concat([s["long"] for s in mo.stat_output], ignore_index=True)
    refs = long["model_ref"].dropna().unique().tolist()
    assert len(refs) == 1
    assert long["statistic"].astype(str).str.contains("F").any()


@pytest.mark.slow
def test_execute_reorders_and_injects_library(rscript: str) -> None:
    mo = rh.rc_run("exec_undefined", execute=True, timeout=60)
    assert "re-ran once" in report_text(mo)
    assert "undef_definer.R" in report_text(mo) or mo.run_results.attrs["reran_for_reorder"]
    assert mo.run_results.attrs == {"reran_for_order": True, "reran_for_reorder": True}
    mo = rh.rc_run("exec_library", execute=True, timeout=60)
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    mods = mo.modifications
    assert mods.loc[mods["change_type"] == "library_injected", "detail"].tolist() == [
        "library(stringr)"
    ]


@pytest.mark.network
@pytest.mark.slow
def test_execute_install_missing_installs_cran_dependency(
    rscript: str, paper: pc.Paper, tmp_path: Path
) -> None:
    script = tmp_path / "needs_pkg.R"
    script.write_text('library(digest)\ndigest::digest("x")\n')
    code_tbl = code_tbl_row(paper, "needs_pkg.R", str(script), packages="digest")
    structure_df = pd.DataFrame({"paper_id": [], "file_name": [], "file_location": []})
    mo = module_run(
        chain(paper, code_tbl, structure_df),
        "reproducibility_check",
        execute=True,
        install_missing=True,
        timeout=300,
    )
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    ir = mo.install_results
    assert ((ir["package"] == "digest") & ir["installed"]).any()


# -- mocked execution paths -------------------------------------------------------------------


def _run_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    from pytacheck.repro.core import _run_frame, _run_row

    return _run_frame([_run_row(**r) for r in rows])


def test_install_report_and_dependency_unavailable(
    paper: pc.Paper, area_fixtures: Path, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytacheck.repro import core

    calls: dict[str, Any] = {}

    def fake_install(install_deps: pd.DataFrame, lib_dir: str, cran_to_main_lib: bool = False):
        calls["install"] = (install_deps["package"].tolist(), cran_to_main_lib)
        return pd.DataFrame(
            {
                "package": ["dplyr", "ggplot2", "oldpkg", "lme4"],
                "source": ["cran", "github", "cran", "cran"],
                "installed": [True, False, True, False],
                "message": ["", "boom", "", ""],
                "via_archive": [False, False, True, False],
                "category": [None, "network", None, "weird"],
            }
        )

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        calls["failed_deps"] = list(kw["failed_deps"])
        return _run_frame(
            [
                {
                    "fn": "deps.R",
                    "outcome": "dependency_unavailable",
                    "error": "there is no package called 'ggplot2'",
                    "error_type": "dependency_unavailable",
                    "stderr": "Error in library(ggplot2)",
                }
            ]
        )

    monkeypatch.setattr(core, "repro_install_deps", fake_install)
    monkeypatch.setattr(core, "repro_run_scripts", fake_run)
    code_tbl = code_tbl_row(paper, "deps.R", str(area_fixtures / "scripts" / "deps.R"))
    mo = module_run(
        chain(paper, code_tbl, data_structure(paper, repro_fixture), PLAN),
        "reproducibility_check",
        execute=True,
        install_missing=True,
        cran_install_main=True,
    )
    assert calls["install"] == (["dplyr", "ggplot2", "remotes"], True)
    assert calls["failed_deps"] == ["ggplot2", "lme4"]
    # dependency_unavailable does not force red
    assert mo.traffic_light == "green"
    text = report_text(mo)
    assert "**Dependency-unavailable errors**" in text
    assert (
        "Dependencies were installed into a throwaway library before running: 2 succeeded, "
        "2 failed. dplyr, ggplot2, oldpkg, lme4 were installed."
    ) in text
    assert "**1 of these were no longer on live CRAN**" in text
    assert "**Failed:** ggplot2 [network issue] (boom); lme4 [NA]" in text
    assert "could not run because its own dependency is unavailable" in mo.summary_text


def test_docker_sandbox_mocked(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pytacheck.repro import docker

    seen: dict[str, Any] = {}

    def fake_run_docker(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        seen.update(kw)
        seen["order"] = list(order)
        return _run_frame([{"fn": "ok.R", "outcome": "ran_ok", "stdout": "> 1 + 1\n[1] 2\n"}])

    monkeypatch.setattr(docker, "repro_docker_available", lambda: {"ok": True, "msg": ""})
    monkeypatch.setattr(docker, "repro_run_scripts_docker", fake_run_docker)
    fake = exec_chain(paper, "ok.R", repro_fixture)
    fake.extras["version_pin"] = {"r_versions": ["4.1.2"]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(fake, "reproducibility_check", execute=True, sandbox="docker")
    assert any("declared R version 4.1.2" in str(x.message) for x in w)
    assert seen["image"] == "ghcr.io/scienceverse/metacheck_r:latest"
    assert seen["order"] == ["ok.R"]
    assert mo.table["outcome"].tolist() == ["ran_ok"]

    mo = module_run(
        fake,
        "reproducibility_check",
        execute=True,
        sandbox="docker",
        docker_use_declared_version=True,
    )
    assert seen["image"] == "rocker/r-ver:4.1.2"


def test_batch_runs_papers_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    papers = pc.read([ROOT / f for f in rh.PSYCHSCI[:3]])
    ids = pc.paper_id(papers)
    lights = dict(zip(ids, ["green", "red", "yellow"], strict=True))

    def fake_worker(paper1: Any, args: Any, limits: Any) -> Any:
        pid = paper1.paper_id
        assert args["execute"] is True and "workers" not in args
        assert set(limits) == {"cpus", "memory_gb"}
        if pid == ids[2]:
            raise RuntimeError("container crashed")
        return ModuleOutput(
            module="reproducibility_check",
            title="Reproducibility Check",
            section="results",
            table=pd.DataFrame({"file_name": ["ok.R"], "outcome": ["ran_ok"]}),
            traffic_light=lights[pid],
            summary_table=pd.DataFrame({"paper_id": [pid], "repro_code_n": [1]}),
        )

    monkeypatch.setattr(h, "_batch_worker", fake_worker)
    monkeypatch.setattr(h, "_process_executor", lambda n: ThreadPoolExecutor(max_workers=n))
    mo = module_run(
        papers,
        "reproducibility_check",
        execute=True,
        sandbox="docker",
        workers=2,
        results_dir=str(tmp_path),
    )
    assert mo.traffic_light == "red"
    assert sorted(mo.summary_table["paper_id"].tolist()) == sorted(ids)
    assert sorted(mo.table["paper_id"].tolist()) == sorted(ids[:2])
    assert mo.summary_text == (
        "Ran the reproducibility check on 3 papers (up to 2 at a time): 1 ok, 1 red, 1 error."
    )
    per = mo.per_paper
    assert per[ids[2]]["traffic_light"] == "error"
    assert "container crashed" in per[ids[2]]["summary_text"]
    assert sorted(p.stem for p in tmp_path.glob("*.json")) == sorted(ids)


def test_batch_not_used_for_process_sandbox(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(h, "_reproducibility_check_batch", lambda *a, **k: pytest.fail("batched"))
    papers = pc.read([ROOT / f for f in rh.PSYCHSCI[:2]])
    # execute = FALSE: workers is ignored
    mo = module_run(papers, "reproducibility_check", workers=4, local_only=True)
    assert mo.get("per_paper") is None


def test_docker_resource_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setattr(h, "_repro_docker_host_memory_bytes", lambda: 16 * 1024**3)
    assert h._repro_docker_resource_limits(1) == {"cpus": 8.0, "memory_gb": 16.0}
    assert h._repro_docker_resource_limits(3) == {"cpus": 2.6, "memory_gb": 5.0}
    assert h._repro_docker_resource_limits(100) == {"cpus": 1.0, "memory_gb": 1.0}
    monkeypatch.setattr(h, "_repro_docker_host_memory_bytes", lambda: None)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert h._repro_docker_resource_limits(2) == {"cpus": 2.0, "memory_gb": 4.0}


def test_host_memory_is_positive_or_none() -> None:
    mem = h._repro_docker_host_memory_bytes()
    assert mem is None or mem > 0


# -- helpers -------------------------------------------------------------------------------


def test_r_named_list_semantics() -> None:
    x = h.RNamedList(["a.R", "b.R", "a.R"], [1, 2, 3])
    assert list(x) == ["a.R", "b.R", "a.R"]
    assert x["a.R"] == 1  # x[["a.R"]]: the first match
    assert x.values() == [1, 2, 3]
    assert x.items() == [("a.R", 1), ("b.R", 2), ("a.R", 3)]
    assert len(x) == 3 and "b.R" in x and "c.R" not in x
    assert x.get("c.R") is None
    with pytest.raises(ValueError):
        h.RNamedList(["a"], [])


@pytest.mark.slow
def test_batch_real_worker_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spawned worker processes: without Docker each paper's check fails on its own."""
    from pytacheck.repro import docker

    if docker.repro_docker_available().get("ok"):
        pytest.skip("Docker is available: the real run would execute containers")
    papers = pc.read([ROOT / f for f in rh.PSYCHSCI[:2]])
    mo = module_run(papers, "reproducibility_check", execute=True, sandbox="docker", workers=2)
    assert mo.traffic_light == "error"
    assert mo.summary_text == (
        "Ran the reproducibility check on 2 papers (up to 2 at a time): 0 ok, 0 red, 2 errors."
    )
    for res in mo.per_paper.values():
        assert res["summary_text"].startswith("Docker reproducibility check failed: ")
        assert 'sandbox = "docker"' in res["summary_text"]


def test_module_output_pickles(paper: pc.Paper, repro_fixture: Any) -> None:
    """A worker process returns its result by pickling it."""
    import pickle

    mo = module_run(exec_chain(paper, "ok.R", repro_fixture), "reproducibility_check")
    back = pickle.loads(pickle.dumps(mo))
    pd.testing.assert_frame_equal(back.table, mo.table)
    assert back.summary_text == mo.summary_text
