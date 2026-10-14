"""reproducibility_check on Python code (port of metacheck d3b8594's module tests).

The static tests run offline. Execution goes through the same ``sandbox`` argument as R
code: the Docker tests fake the Docker backends, and the ``sandbox="process"`` tests run
this machine's ``python3`` on the fixture scripts (copies of metacheck's ``*.py``
fixtures, in ``tests/repro_core/fixtures/python``).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.core.errors import PytacheckWarning
from metacheck.module import module_run
from metacheck.modules import _reproducibility as h
from metacheck.repro import core, docker
from metacheck.repro import python as rpy
from tests.mod_repro.test_reproducibility_check import (
    PLAN,
    _run_frame,
    chain,
    code_tbl_row,
    report_text,
)

PY = Path(__file__).resolve().parents[1] / "repro_core" / "fixtures" / "python"
DOCKER_OK = {"ok": True, "msg": ""}


@pytest.fixture
def paper() -> pc.Paper:
    p = pc.test_paper()
    p.paper_id = "p1"
    return p


@pytest.fixture
def python3() -> str:
    exe = rpy._python()
    if exe is None:
        pytest.skip("no python3 on PATH")
    return exe


def py_chain(
    paper: Any,
    scripts: list[str],
    extra_files: dict[str, str] | None = None,
    language: str = "Python",
    plan: pd.DataFrame | None = PLAN,
) -> Any:
    """A chain with *scripts* (fixture names or paths) and the fixture ``data.csv``."""
    locs = [s if "/" in s else str(PY / s) for s in scripts]
    names = [Path(s).name for s in scripts]
    code_tbl = code_tbl_row(paper, names, locs, language=language)
    files = {"data.csv": str(PY / "data.csv"), **(extra_files or {})}
    structure_df = pd.DataFrame(
        {
            "paper_id": "p1",
            "file_name": names + list(files),
            "file_location": locs + list(files.values()),
        }
    )
    return chain(paper, code_tbl, structure_df, plan)


def no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("a script was run")

    monkeypatch.setattr(rpy, "repro_run_scripts_py", boom)
    monkeypatch.setattr(rpy, "repro_install_deps_py", boom)
    monkeypatch.setattr(rpy.subprocess, "run", boom)
    monkeypatch.setattr(docker, "_docker", boom)


# -- static analysis ------------------------------------------------------------------------


def test_static_python_only_is_green(paper: pc.Paper, monkeypatch: pytest.MonkeyPatch) -> None:
    no_process(monkeypatch)
    mo = module_run(py_chain(paper, ["ok.py"]), "reproducibility_check")
    assert mo.traffic_light == "green"
    assert mo.table["file_name"].tolist() == ["ok.py"]
    assert mo.table["runnable"].tolist() == [True]
    assert mo.table["reads"].iloc[0] == ["data.csv"]
    assert mo.summary_table["repro_code_n"].tolist() == [1]
    assert mo.summary_table["repro_runnable"].tolist() == [1]
    text = report_text(mo)
    assert text.startswith("### Python code\nThis module also assesses whether the paper's Python")
    assert "no code is run" in text
    assert "We assessed 1 Python code file for reproducibility" in mo.summary_text
    assert mo.get("run_results") is None


def test_static_missing_input_is_yellow(paper: pc.Paper) -> None:
    mo = module_run(py_chain(paper, ["missing_input.py"]), "reproducibility_check")
    assert mo.traffic_light == "yellow"
    assert mo.table["not_runnable_reason"].tolist() == ["missing_input"]
    assert mo.table["unresolved_inputs"].tolist() == ["not_in_repo.csv"]
    assert mo.summary_table["repro_missing_inputs"].tolist() == [1]
    assert "1 referenced Python input unavailable" in mo.summary_text


def test_static_order_and_own_writes(paper: pc.Paper) -> None:
    mo = module_run(
        py_chain(paper, ["reads_written.py", "writes_then_reads.py"]), "reproducibility_check"
    )
    order = dict(zip(mo.table["file_name"], mo.table["run_order"], strict=True))
    assert order["writes_then_reads.py"] < order["reads_written.py"]
    # the file a script opens to write is not an input it is missing (UX-repro-3)
    assert mo.table["runnable"].tolist() == [True, True]
    assert mo.traffic_light == "green"


def test_static_parse_error(paper: pc.Paper, tmp_path: Path) -> None:
    script = tmp_path / "py2.py"
    script.write_text("print 'python 2'\n")
    mo = module_run(py_chain(paper, [str(script), "ok.py"]), "reproducibility_check")
    assert mo.table["parses"].tolist() == [False, True]
    assert mo.table["not_runnable_reason"].tolist()[0] == "parse_error"
    assert mo.traffic_light == "yellow"
    assert "1 Python file did not parse" in report_text(mo)


def test_static_dependencies(paper: pc.Paper, tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text("def mean(x):\n    return sum(x) / len(x)\n")
    (tmp_path / "analysis.py").write_text(
        "import numpy as np\nimport sklearn\nimport helpers\nimport os\n"
    )
    req = tmp_path / "requirements.txt"
    req.write_text("numpy==1.26.4\nscikit-learn>=1.3\n")
    mo = module_run(
        py_chain(
            paper,
            [str(tmp_path / "analysis.py"), str(tmp_path / "helpers.py")],
            {"requirements.txt": str(req)},
        ),
        "reproducibility_check",
    )
    # numpy and sklearn; `helpers` is the paper's own module and `os` the standard library
    assert mo.summary_table["repro_deps"].tolist() == [2]
    assert "2 installable Python dependencies detected." in mo.summary_text


def test_duplicate_python_files_run_once(paper: pc.Paper, tmp_path: Path) -> None:
    copy = tmp_path / "mirror" / "ok.py"
    copy.parent.mkdir()
    copy.write_text((PY / "ok.py").read_text())
    mo = module_run(py_chain(paper, ["ok.py", str(copy)]), "reproducibility_check")
    assert len(mo.table) == 1
    assert "#### Duplicate files across repo mirrors" in report_text(mo)
    assert "1 Python file skipped as byte-identical duplicate" in mo.summary_text


def test_notebook_code_cells_are_the_code(paper: pc.Paper, tmp_path: Path) -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Analysis"]},
            {"cell_type": "code", "source": ["%matplotlib inline\n", "import csv\n"]},
            {"cell_type": "code", "source": ["rows = list(csv.reader(open('data.csv')))"]},
        ],
        "metadata": {"kernelspec": {"language": "python"}},
    }
    path = tmp_path / "analysis.ipynb"
    path.write_text(json.dumps(nb))
    mo = module_run(py_chain(paper, [str(path)]), "reproducibility_check")
    assert mo.table["parses"].tolist() == [True]
    assert mo.table["reads"].iloc[0] == ["data.csv"]


def test_mixed_r_and_python(paper: pc.Paper, repro_fixture: Any) -> None:
    code_tbl = pd.concat(
        [
            code_tbl_row(paper, "ok.R", repro_fixture("ok.R")),
            code_tbl_row(paper, "missing_input.py", str(PY / "missing_input.py"), "Python"),
        ],
        ignore_index=True,
    )
    structure_df = pd.DataFrame(
        {
            "paper_id": "p1",
            "file_name": ["ok.R", "missing_input.py", "data.csv"],
            "file_location": [
                repro_fixture("ok.R"),
                str(PY / "missing_input.py"),
                repro_fixture("data.csv"),
            ],
        }
    )
    mo = module_run(chain(paper, code_tbl, structure_df, PLAN), "reproducibility_check")
    assert mo.table["file_name"].tolist() == ["ok.R", "missing_input.py"]
    assert mo.summary_table["repro_code_n"].tolist() == [2]
    assert mo.summary_table["repro_runnable"].tolist() == [1]
    assert mo.summary_table["repro_missing_inputs"].tolist() == [1]
    # green (R) and yellow (Python): the more severe
    assert mo.traffic_light == "yellow"
    text = report_text(mo)
    assert text.index("We examined 1 R code file") < text.index("### Python code")
    assert "We assessed 1 R code file" in mo.summary_text
    assert "We assessed 1 Python code file" in mo.summary_text


def test_python_files_are_not_unassessed_code(paper: pc.Paper) -> None:
    fnames = ["a.py", "b.jl"]
    code_tbl = pd.DataFrame({"file_name": fnames, "language": ["Python", "Julia"]})
    nonr_lang, _ = h.nonr_code(code_tbl, fnames, False, False, False)
    assert nonr_lang == ["Julia"]
    note = h.non_r_note(pd.DataFrame({"file_name": fnames}), nonr_lang)
    assert "1 non-R code file" in note
    assert "only RUNS R and Python code" in note


def test_spss_data_without_syntax_still_counts_for_a_python_paper(paper: pc.Paper) -> None:
    fake = py_chain(paper, ["ok.py"], {"survey.sav": str(PY / "data.csv")})
    mo = module_run(fake, "reproducibility_check")
    # metacheck drops the SPSS section, and its red, when the paper has no R code (UX-repro-2)
    assert mo.traffic_light == "red"
    assert "#### SPSS" in report_text(mo)


# -- execution: Docker by default ----------------------------------------------------------


def test_docker_is_the_default_for_python(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_install(install_deps: pd.DataFrame, lib_dir: str, **kw: Any) -> pd.DataFrame:
        seen["install"] = install_deps["package"].tolist()
        return rpy._install_rows(["numpy"], ["pypi"], [False], ["no network"])

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        seen["order"] = list(order)
        seen.update(kw)
        seen["script"] = Path(run_tbl["script_path"].iloc[0]).read_text()
        return _run_frame(
            [
                {
                    "fn": "python_plots.py",
                    "outcome": "ran_ok",
                    "stdout": "t(28) = 2.20, p = .036\n",
                }
            ]
        )

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_install_deps_py_docker", fake_install)
    monkeypatch.setattr(docker, "repro_run_scripts_py_docker", fake_run)
    no_r = lambda *a, **k: pytest.fail("an R backend was used")  # noqa: E731
    monkeypatch.setattr(docker, "repro_run_scripts_docker", no_r)
    monkeypatch.setattr(rpy, "repro_run_scripts_py", no_r)
    monkeypatch.setattr(core, "_rscript", no_r)

    script = tmp_path / "python_plots.py"
    script.write_text("import numpy\nimport os\nos.chdir('/home/me')\ndf.to_csv('t.csv')\n")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            py_chain(paper, [str(script)]),
            "reproducibility_check",
            execute=True,
            install_missing=True,
        )
    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]
    assert seen["install"] == ["numpy"]
    assert seen["failed_deps"] == ["numpy"]
    assert seen["order"] == ["python_plots.py"]
    # the write is redirected to the output folder as the container sees it
    assert 'df.to_csv("/sandbox/output/t.csv")' in seen["script"]
    assert "pass  # [reproducibility_check removed os.chdir]" in seen["script"]
    assert "Docker container" in report_text(mo)
    assert "**Failed:** numpy (no network)" in report_text(mo)
    # what the script printed joins the statistical output
    assert [s["source"] for s in mo.stat_output] == ["python_output"]
    mods = mo.modifications
    assert mods["change_type"].tolist() == ["setwd_removed"]
    assert mods["detail"].tolist() == ["/home/me"]


def test_docker_unavailable_runs_no_python(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_process(monkeypatch)
    monkeypatch.setattr(docker, "repro_docker_available", lambda: {"ok": False, "msg": "down."})
    with pytest.raises(pc.ModuleError, match='sandbox = "docker": down\\.'):
        module_run(py_chain(paper, ["ok.py"]), "reproducibility_check", execute=True)


def test_python_output_is_matched_against_the_paper(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_match(paper1: Any, stat_output: Any) -> Any:
        seen["sources"] = [s["source"] for s in stat_output]
        return None

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(
        docker,
        "repro_run_scripts_py_docker",
        lambda *a, **k: _run_frame(
            [{"fn": "ok.py", "outcome": "ran_ok", "stdout": "t(28) = 2.20, p = .036\n"}]
        ),
    )
    monkeypatch.setattr(h, "match_output", fake_match)
    module_run(py_chain(paper, ["ok.py"]), "reproducibility_check", execute=True)
    # metacheck matches before the Python scripts run (UX-repro-1)
    assert seen["sources"] == ["python_output"]


def test_python_error_is_red(paper: pc.Paper, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(
        docker,
        "repro_run_scripts_py_docker",
        lambda *a, **k: _run_frame(
            [
                {
                    "fn": "undefined_var.py",
                    "outcome": "errored",
                    "error": "NameError: name 'x' is not defined",
                    "error_type": "undefined_variable",
                    "undefined_var": "x",
                    "stderr": "Traceback\nNameError: name 'x' is not defined",
                }
            ]
        ),
    )
    mo = module_run(py_chain(paper, ["undefined_var.py"]), "reproducibility_check", execute=True)
    assert mo.traffic_light == "red"
    assert mo.table["error_type"].tolist() == ["undefined_variable"]
    text = report_text(mo)
    assert "**Undefined-name errors**" in text
    assert "0 of 1 Python script ran without error" in mo.summary_text


# -- execution: sandbox = "process" (this machine's python3) -----------------------------


def test_process_runs_python_and_warns_once(
    python3: str, paper: pc.Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        docker, "repro_docker_available", lambda: pytest.fail("Docker was looked at")
    )
    monkeypatch.setattr(core, "_rscript", lambda: pytest.fail("no R code: R is not needed"))
    scripts = ["ok.py", "writes_then_reads.py", "reads_written.py", "bad_chdir.py", "errors.py"]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            py_chain(paper, scripts),
            "reproducibility_check",
            execute=True,
            sandbox="process",
            timeout=60,
        )
    assert len([x for x in w if issubclass(x.category, PytacheckWarning)]) == 1
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got == {
        "ok.py": "ran_ok",
        "writes_then_reads.py": "ran_ok",
        "reads_written.py": "ran_ok",
        "bad_chdir.py": "ran_ok",
        "errors.py": "errored",
    }
    assert mo.traffic_light == "red"
    assert mo.summary_table["repro_ran_ok"].tolist() == [4]
    text = report_text(mo)
    assert "isolated subprocess" in text
    assert "Reproducible after removing `os.chdir()`: bad_chdir.py." in text
    assert "deliberate failure for testing" in text


def test_process_needs_python(paper: pc.Paper, monkeypatch: pytest.MonkeyPatch) -> None:
    no_process(monkeypatch)
    monkeypatch.setattr(rpy.shutil, "which", lambda _: None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(pc.ModuleError, match='sandbox = "process" needs Python'):
            module_run(
                py_chain(paper, ["ok.py"]), "reproducibility_check", execute=True, sandbox="process"
            )
    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]
