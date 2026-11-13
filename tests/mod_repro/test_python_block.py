"""The Python block of reproducibility_check (metacheck 0.4.3.7's ``if (has_py)`` block).

Ports of the module's testthat cases for Python code (#568): everything runs without R,
Docker or the network. The execution cases use this machine's ``python3`` through
``sandbox = "process"`` (they are skipped without one), or fake the Docker backends.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import module_run
from metacheck.repro import docker
from metacheck.repro import python as rpy
from tests.mod_repro.test_python import DOCKER_OK, PY, py_chain
from tests.mod_repro.test_reproducibility_check import (
    PLAN,
    _run_frame,
    chain,
    code_tbl_row,
    report_text,
)

pytestmark = pytest.mark.filterwarnings("ignore::metacheck.core.errors.PytacheckWarning")


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


def write(d: Path, name: str, text: str) -> str:
    f = d / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return str(f)


def run_process(
    paper: Any, scripts: list[str], extra_files: dict[str, str] | None = None, **kw: Any
) -> Any:
    return module_run(
        py_chain(paper, scripts, extra_files),
        "reproducibility_check",
        execute=True,
        sandbox="process",
        timeout=60,
        **kw,
    )


# -- static analysis -------------------------------------------------------------------------


def test_a_name_joined_to_a_variable_is_not_predicted_missing(
    paper: pc.Paper, tmp_path: Path
) -> None:
    script = write(
        tmp_path,
        "built.py",
        'import pandas as pd\nx = "a"\nd = pd.read_csv(x + "field.csv")\n'
        'e = pd.read_csv("absent.csv")\n',
    )
    mo = module_run(py_chain(paper, [script]), "reproducibility_check")
    assert mo.table["unresolved_inputs"].tolist() == ["absent.csv"]


def test_python_table_columns_follow_the_r_module(paper: pc.Paper) -> None:
    mo = module_run(py_chain(paper, ["ok.py"]), "reproducibility_check")
    assert mo.table.columns.tolist() == [
        "paper_id",
        "file_name",
        "file_path",
        "parses",
        "run_order",
        "order_basis",
        "depends_on",
        "paths_rewritten",
        "paths_ambiguous",
        "setwd_removed",
        "runnable",
        "not_runnable_reason",
        "unresolved_inputs",
        "outcome",
        "error_type",
        "undefined_var",
        "reads",
        "writes",
        "names_corrected",
        "not_analysis_why",
        "paper_link",
        "repo_name",
        "reason",
        "error_message",
        "commands_removed",
    ]


def test_a_package_test_and_setup_py_are_not_the_analysis(paper: pc.Paper, tmp_path: Path) -> None:
    analysis = write(tmp_path, "analysis.py", "import pandas as pd\npd.read_csv('data.csv')\n")
    check = write(tmp_path, "tests/check_rows.py", "def test_rows():\n    assert True\n")
    setup = write(
        tmp_path, "setup.py", "from setuptools import setup\nsetup(name='x', version='1')\n"
    )
    names = ["analysis.py", "tests/check_rows.py", "setup.py"]
    locs: list[str | None] = [analysis, check, setup]
    structure_df = pd.DataFrame(
        {
            "paper_id": "p1",
            "file_name": [*names, "data.csv"],
            "file_location": [*locs, str(PY / "data.csv")],
        }
    )
    mo = module_run(
        chain(paper, code_tbl_row(paper, names, locs, language="Python"), structure_df, PLAN),
        "reproducibility_check",
    )
    got = dict(zip(mo.table["file_name"], mo.table["not_runnable_reason"], strict=True))
    assert pd.isna(got["analysis.py"])
    assert got["tests/check_rows.py"] == "not_analysis"
    assert got["setup.py"] == "not_analysis"
    text = report_text(mo)
    assert "#### Files not run on their own" in text
    assert "2 files are not part of the analysis but of software" in text
    assert mo.summary_table["repro_code_n"].tolist() == [3]
    assert mo.summary_table["repro_runnable"].tolist() == [1]


def test_a_server_script_is_not_run(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve = write(
        tmp_path,
        "serve.py",
        "from http.server import HTTPServer, SimpleHTTPRequestHandler\n"
        "HTTPServer(('', 8000), SimpleHTTPRequestHandler).serve_forever()\n",
    )
    parses: list[dict[str, bool]] = []

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        parses.append(dict(kw["parses"]))
        return _run_frame([{"fn": n, "outcome": "ran_ok"} for n in order])

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_py_docker", fake_run)
    mo = module_run(py_chain(paper, [serve, "ok.py"]), "reproducibility_check", execute=True)
    # it would wait for a connection until the time limit
    assert not parses[0]["serve.py"]
    assert parses[0]["ok.py"]
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got["serve.py"] == "needs_a_person"


def test_same_named_python_files_each_keep_a_row(paper: pc.Paper, tmp_path: Path) -> None:
    one = write(tmp_path, "good/analysis.py", "x = 1\n")
    two = write(tmp_path, "bad/analysis.py", "x = 2\n")
    mo = module_run(py_chain(paper, [one, two]), "reproducibility_check")
    assert len(mo.table) == 2
    assert mo.table["file_name"].nunique() == 2 or mo.table["file_path"].nunique() == 2


# -- execution through the process sandbox -----------------------------------------------------


def test_a_script_finds_the_repositorys_own_modules(
    python3: str, paper: pc.Paper, tmp_path: Path
) -> None:
    init = write(tmp_path, "proj/mymod/__init__.py", "")
    core = write(tmp_path, "proj/mymod/core.py", "VALUE = 1\n")
    run = write(
        tmp_path,
        "proj/scripts/run.py",
        "from mymod import core\nassert core.VALUE == 1\nprint('ok')\n",
    )
    mo = run_process(paper, [run], extra_files={"mymod/__init__.py": init, "mymod/core.py": core})
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got["run.py"] == "ran_ok"


def test_python2_has_its_own_outcome_and_notebook_commands_are_noted(
    python3: str, paper: pc.Paper, tmp_path: Path
) -> None:
    old = write(tmp_path, "old.py", 'print "old"\n')
    new = write(tmp_path, "new.py", 'print("new")\n')
    uses = write(tmp_path, "uses.py", "import oldmod\n")
    oldmod = write(tmp_path, "oldmod.py", 'print "module"\n')
    nb = tmp_path / "n.ipynb"
    nb.write_text(
        json.dumps(
            {
                "metadata": {"kernelspec": {"language": "python", "name": "python3"}},
                "nbformat": 4,
                "nbformat_minor": 5,
                "cells": [
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "outputs": [],
                        "execution_count": None,
                        "source": "%%bash\nls -l",
                    },
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "outputs": [],
                        "execution_count": None,
                        "source": (
                            'if "colab" in str(get_ipython()):\n    !pip install a\nprint("nb")'
                        ),
                    },
                ],
            }
        )
    )
    mo = run_process(paper, [old, new, uses, oldmod, str(nb)])
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got["old.py"] == "python2"
    assert got["oldmod.py"] == "python2"
    assert got["new.py"] == "ran_ok"
    assert got["n.ipynb"] == "ran_ok"
    reasons = dict(zip(mo.table["file_name"], mo.table["not_runnable_reason"], strict=True))
    assert reasons["old.py"] == "python2"
    mods = mo.modifications
    removed = mods.loc[mods["change_type"] == "notebook_command_removed", "detail"]
    assert set(removed) == {"%%bash", "!pip install a"}
    assert "get_ipython_added" in set(mods["change_type"])
    text = report_text(mo)
    assert "written for Python 2" in text
    assert "#### Notebook commands" in text


def test_os_chdir_is_stripped_and_reported(python3: str, paper: pc.Paper) -> None:
    mo = run_process(paper, ["bad_chdir.py"])
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    assert "os.chdir" in report_text(mo)
    # os.chdir() is reported with the label of R's setwd() removal
    assert "setwd_removed" in set(mo.modifications["change_type"])


def test_a_name_another_script_defines_is_named_without_a_rerun(
    python3: str, paper: pc.Paper, tmp_path: Path
) -> None:
    definer = write(tmp_path, "definer.py", "some_var_no_script_defines = 42\n")
    user = write(tmp_path, "undefined_var.py", (PY / "undefined_var.py").read_text())
    mo = run_process(paper, [definer, user])
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    types = dict(zip(mo.table["file_name"], mo.table["error_type"], strict=True))
    assert got["definer.py"] == "ran_ok"
    assert got["undefined_var.py"] == "errored"
    assert types["undefined_var.py"] == "undefined_variable"
    text = report_text(mo)
    assert "Undefined-name errors" in text
    assert "re-ran once" not in text


def test_python_output_feeds_the_statistical_output(python3: str, paper: pc.Paper) -> None:
    mo = run_process(paper, ["ok.py"])
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    assert mo.run_results["outcome"].tolist() == ["ran_ok"]
    assert mo.summary_table["repro_ran_ok"].tolist() == [1]
    assert "1 of 1 Python script ran without error" in mo.summary_text


# -- execution through faked Docker backends ----------------------------------------------------


def test_python2_code_runs_a_second_time_with_the_python2_image(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old = write(tmp_path, "old.py", "print 'half', 5/2\n")
    new = write(tmp_path, "new.py", 'print("new")\n')
    calls: list[dict[str, Any]] = []

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        names = list(order)
        calls.append({"order": names, "image": kw.get("image")})
        rows = []
        for n in names:
            if n == "old.py" and kw.get("image") == "python:2.7-slim":
                rows.append({"fn": n, "outcome": "ran_ok", "stdout": "half 2\n"})
            elif n == "old.py":
                # what the runner gives a file the parser marked as Python 2
                rows.append({"fn": n, "outcome": "python2"})
            else:
                rows.append({"fn": n, "outcome": "ran_ok", "stdout": "new\n"})
        return _run_frame(rows)

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_py_docker", fake_run)
    monkeypatch.setattr(rpy, "repro_run_scripts_py", lambda *a, **k: pytest.fail("process run"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = module_run(py_chain(paper, [old, new]), "reproducibility_check", execute=True)
    # the first run does not run the Python 2 file, the second runs it with its own image
    assert len(calls) == 2
    assert calls[0]["image"] != "python:2.7-slim"
    assert calls[1]["order"] == ["old.py"]
    assert calls[1]["image"] == "python:2.7-slim"
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got == {"old.py": "ran_ok", "new.py": "ran_ok"}
    assert "old.py" in set(
        mo.modifications.loc[mo.modifications["change_type"] == "python2", "file_name"]
    )
    assert "with Python 2.7 (Docker image `python:2.7-slim`)" in report_text(mo)


def test_the_python2_image_can_be_turned_off(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old = write(tmp_path, "old.py", "print 'half', 5/2\n")
    new = write(tmp_path, "new.py", 'print("new")\n')
    calls: list[list[str]] = []

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        calls.append(list(order))
        return _run_frame(
            [
                {"fn": n, "outcome": "python2" if n == "old.py" else "ran_ok", "stdout": "x\n"}
                for n in order
            ]
        )

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_py_docker", fake_run)
    monkeypatch.setattr(rpy, "_repro_docker_image_py2", lambda: None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = module_run(py_chain(paper, [old, new]), "reproducibility_check", execute=True)
    assert len(calls) == 1
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got["old.py"] == "python2"
    assert "python2" not in set(mo.modifications["change_type"])


def test_python_with_too_little_time_installs_and_runs_nothing(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # metacheck 0.4.3.28: a clock time as total_timeout with less than 15 minutes left as
    # the check starts: nothing is copied, installed or run, as for R code
    import datetime as dt
    import time

    script = write(tmp_path, "a.py", "import requests\nprint(1)\n")
    deadlines: list[float] = []

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        deadlines.append(kw["deadline"])
        return _run_frame([{"fn": n, "outcome": "skipped_time_limit"} for n in order])

    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_py_docker", fake_run)
    monkeypatch.setattr(
        docker, "repro_install_deps_py_docker", lambda *a, **k: pytest.fail("installed")
    )
    mo = module_run(
        py_chain(paper, [script]),
        "reproducibility_check",
        execute=True,
        install_missing=True,
        total_timeout=dt.datetime.now() + dt.timedelta(minutes=5),
    )
    assert deadlines and deadlines[0] <= time.time()  # no script starts
    got = dict(zip(mo.table["file_name"], mo.table["outcome"], strict=True))
    assert got == {"a.py": "skipped_time_limit"}
    assert "The checks before this one had left 5 min before the time limit" in report_text(mo)
