"""What runs and what is only used (port of metacheck 0.4.3.7's tests of these helpers).

Everything runs offline: no R, no Docker, no network. The one helper that asks R which
package exports a function (``_repro_r_exporters``) is replaced by a fake where a test
depends on its answer.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from metacheck.report.blocks import ReportTable
from metacheck.repro import python as rpy
from metacheck.repro import runsupport as rs
from metacheck.repro.python import (
    _PY_STDLIB,
    _repro_docker_image_py2,
    _repro_find_python,
    _repro_py2_error,
    _repro_py2_modules,
    _repro_py_get_ipython,
    _repro_py_module_folders,
    _repro_py_parses,
    _repro_py_pip_name,
    _repro_py_stdlib_modules,
    _repro_python_missing_reason,
)
from metacheck.repro.runsupport import (
    _repro_attached_pkgs,
    _repro_download_limits,
    _repro_function_names,
    _repro_function_users,
    _repro_later_pass,
    _repro_missing_fn_pkgs,
    _repro_not_run_report,
    _repro_output_cut_note,
    _repro_output_head,
    _repro_py_import_links,
    _repro_py_local_closure,
    _repro_run_by_source,
)
from metacheck.utils import options

# ---------------------------------------------------------------------------
# .repro_run_by_source()
# ---------------------------------------------------------------------------


def _order(names: list[str], frm: list[str], to: list[str]) -> pd.DataFrame:
    ot = pd.DataFrame({"file_name": pd.array(names, dtype="string")})
    ot.attrs["source_edges"] = pd.DataFrame(
        {"from": pd.array(frm, dtype="string"), "to": pd.array(to, dtype="string")}
    )
    return ot


def test_run_by_source_finds_scripts_run_only_through_source_following_chains() -> None:
    """.repro_run_by_source finds scripts run only through source(), following chains"""
    ot = _order(["main.R", "a.R", "b.R", "solo.R"], ["a.R", "b.R"], ["main.R", "a.R"])
    out = _repro_run_by_source(ot, {"main.R": True, "a.R": True, "b.R": True, "solo.R": True})
    assert set(out) == {"a.R", "b.R"}
    assert out["a.R"] == ["main.R"]
    assert out["b.R"] == ["a.R"]


def test_run_by_source_runs_a_script_on_its_own_when_its_sourcer_cannot_run() -> None:
    """.repro_run_by_source runs a script on its own when its sourcer cannot run"""
    ot = _order(["main.R", "a.R"], ["a.R"], ["main.R"])
    # main.R does not parse / lacks inputs: a.R still runs on its own.
    assert _repro_run_by_source(ot, {"main.R": False, "a.R": True}) == {}
    # a.R itself cannot run: it keeps its own outcome rather than being hidden.
    assert _repro_run_by_source(ot, {"main.R": True, "a.R": False}) == {}


def test_run_by_source_leaves_two_scripts_that_source_each_other_on_their_own() -> None:
    """.repro_run_by_source leaves two scripts that source each other on their own"""
    ot = _order(["a.R", "b.R"], ["a.R", "b.R"], ["b.R", "a.R"])
    assert _repro_run_by_source(ot, {"a.R": True, "b.R": True}) == {}


def test_run_by_source_without_edges_and_with_a_series_and_self_sourcing() -> None:
    """No attribute, no edges, a named Series for can_run, a script that sources itself."""
    bare = pd.DataFrame({"file_name": ["a.R"]})
    assert _repro_run_by_source(bare, {"a.R": True}) == {}
    assert _repro_run_by_source(_order(["a.R"], [], []), {"a.R": True}) == {}
    assert _repro_run_by_source(_order(["a.R"], ["a.R"], ["a.R"]), {"a.R": True}) == {}
    ot = _order(["main.R", "a.R"], ["a.R"], ["main.R"])
    flags = pd.Series([True, True], index=["main.R", "a.R"])
    assert _repro_run_by_source(ot, flags) == {"a.R": ["main.R"]}


# ---------------------------------------------------------------------------
# .repro_missing_fn_pkgs()
# ---------------------------------------------------------------------------


@pytest.fixture
def no_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """No package is installed for R (and so exports anything)."""
    monkeypatch.setattr(rs, "_repro_r_exporters", lambda name, pkgs: set())


def test_missing_fn_pkgs_adds_every_package_the_other_scripts_attach(no_r: None) -> None:
    """.repro_missing_fn_pkgs adds every package the other scripts attach"""
    code_list = {
        "1_prep.R": ["library(tidyverse)", "library(lme4)"],
        "2_analysis.R": ["d %>% filter(x > 1)"],
    }
    assert _repro_missing_fn_pkgs("%>%", "2_analysis.R", code_list, []) == ["tidyverse", "lme4"]


def test_missing_fn_pkgs_falls_back_to_magrittr_for_the_pipe_when_nothing_provides_it(
    no_r: None,
) -> None:
    """.repro_missing_fn_pkgs falls back to magrittr for %>% when nothing provides it"""
    code_list = {"only.R": "x <- 1:3 %>% sum()", "other.R": "library(lme4)"}
    assert _repro_missing_fn_pkgs("%>%", "only.R", code_list, []) == ["magrittr"]


def test_missing_fn_pkgs_returns_nothing_for_an_unknown_function(no_r: None) -> None:
    """.repro_missing_fn_pkgs returns nothing for an unknown function"""
    code_list = {"only.R": "x <- not_a_real_function_xyz()"}
    assert _repro_missing_fn_pkgs("not_a_real_function_xyz", "only.R", code_list, []) == []


def test_missing_fn_pkgs_takes_an_exporting_package_the_others_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package the other scripts attach that exports the function (R: installed here)."""
    asked: list[tuple[str, list[str]]] = []

    def fake(name: str, pkgs: list[str]) -> set[str]:
        asked.append((name, list(pkgs)))
        return {p for p in pkgs if p == "lme4"}

    monkeypatch.setattr(rs, "_repro_r_exporters", fake)
    code_list = {
        "1.R": ["library(lme4)", "library(MASS)"],
        "2.R": ["library(MASS)", "glmer(y ~ x)"],
    }
    # the packages 2.R attaches itself are not added again
    assert _repro_missing_fn_pkgs("glmer", "2.R", code_list, []) == ["lme4"]
    assert asked == [("glmer", ["lme4"])]
    # nothing the others attach provides it: the one candidate that does
    monkeypatch.setattr(rs, "_repro_find_export_pkg", lambda name, cands: "nlme")
    assert _repro_missing_fn_pkgs("lme", "2.R", {"2.R": "lme(y ~ x)"}, ["nlme"]) == ["nlme"]


def test_missing_fn_pkgs_does_not_add_magrittr_when_a_script_attaches_the_pipe(
    no_r: None,
) -> None:
    """The magrittr fallback only applies when no script attaches a package with the pipe."""
    code_list = {"a.R": ["library(dplyr)", "x %>% y"], "b.R": "library(MASS)"}
    # b.R is the failing script: a.R's dplyr provides the pipe
    assert _repro_missing_fn_pkgs("%>%", "b.R", code_list, []) == ["dplyr"]
    # the failing script attaches it itself, nothing else does: the pipe is provided
    assert _repro_missing_fn_pkgs("%>%", "a.R", {"a.R": "library(dplyr)"}, []) == []


def test_attached_pkgs_are_the_ones_a_script_attaches_in_order() -> None:
    """Packages a script attaches itself, not those it uses with pkg::."""
    code = ["library(b)", "x <- a::f()", "require(c)", "library(b)"]
    assert _repro_attached_pkgs(code) == ["b", "c"]
    assert _repro_attached_pkgs("x <- 1") == []
    assert _repro_attached_pkgs(None) == []


# ---------------------------------------------------------------------------
# .repro_find_python(), Python 2, .repro_python_missing_reason()
# ---------------------------------------------------------------------------


def test_find_python_falls_back_to_python3_when_the_given_name_is_not_found() -> None:
    """.repro_find_python falls back to python3 when the given name is not found"""
    py3 = _repro_find_python("python3")
    if not py3:
        pytest.skip("no working python3 interpreter")
    assert _repro_find_python("definitely_not_a_real_python_binary_xyz") == py3


def test_find_python_runs_the_candidate_and_gives_an_empty_string_when_none_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A name that is on the path but does not run is skipped; no interpreter gives ''."""
    monkeypatch.setattr(rpy.shutil, "which", lambda cand: None)
    assert _repro_find_python("python") == ""
    broken = tmp_path / "python"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    monkeypatch.setattr(rpy.shutil, "which", lambda cand: str(broken))
    assert _repro_find_python("python") == ""
    assert "Python interpreter" in _repro_python_missing_reason()


def test_py2_error_reads_python_2_code_from_an_error_message() -> None:
    """.repro_py2_error reads Python 2 code from an error message (#568)"""
    assert _repro_py2_error(
        "  File \"a.py\"\nSyntaxError: Missing parentheses in call to 'print'. "
        "Did you mean print(...)?"
    )
    assert _repro_py2_error("TabError: inconsistent use of tabs and spaces in indentation")
    assert not _repro_py2_error("SyntaxError: invalid syntax")
    assert not _repro_py2_error("NameError: name 'x' is not defined")


def test_py2_error_names_the_modules_of_python_2() -> None:
    """Python install names, and Python 2's modules (#568): the error part"""
    assert _repro_py2_error("ModuleNotFoundError: No module named 'cPickle'")
    assert not _repro_py2_error("ModuleNotFoundError: No module named 'numpy'")
    assert _repro_py2_error(None) is False
    assert _repro_py2_error(["x", "No module named 'urlparse'"]) == [False, True]
    assert {"cPickle", "urlparse", "htmlentitydefs"} <= set(_repro_py2_modules())
    assert len(_repro_py2_modules()) == 32


# ---------------------------------------------------------------------------
# .repro_output_head(), .repro_output_cut_note()
# ---------------------------------------------------------------------------


def test_output_head_keeps_the_first_lines_of_long_output() -> None:
    """.repro_output_head keeps the first lines of long output (#523, item 2)"""
    txt = "\n".join(f"line {i}" for i in range(1, 11))
    out = _repro_output_head(txt, 4)
    assert str(out) == "line 1\nline 2\nline 3\nline 4"
    assert out.n_lines == 10
    assert _repro_output_head(txt, 10) is txt
    # a vector of lines, as the Python path has
    out = _repro_output_head(["a", "b", "c"], 2)
    assert list(out) == ["a", "b"]
    assert out.n_lines == 3
    assert _repro_output_head(None, 2) is None
    assert _repro_output_head("", 2) == ""
    assert not hasattr(_repro_output_head(["a"], 2), "n_lines")
    assert _repro_output_head(["a", None, "c"], 1) == ["a", None, "c"]
    assert _repro_output_head(txt, math.inf) is txt
    try:
        options({"metacheck.repro_output_max_lines": 3})
        assert _repro_output_head(txt).n_lines == 10
    finally:
        options({"metacheck.repro_output_max_lines": None})
    # the default is 20000 lines
    assert _repro_output_head("\n".join(["x"] * 20000)) == "\n".join(["x"] * 20000)
    assert _repro_output_head("\n".join(["x"] * 20001)).n_lines == 20001


def test_output_head_counts_a_final_newline_as_a_line() -> None:
    """A trailing newline is one more line, as R's count of newlines plus one."""
    out = _repro_output_head("a\nb\nc\n", 2)
    assert str(out) == "a\nb"
    assert out.n_lines == 4


def test_output_cut_note_names_the_scripts_and_their_lines() -> None:
    """.repro_output_head keeps the first lines of long output: the note"""
    assert _repro_output_cut_note({}) is None
    assert _repro_output_cut_note(None) is None
    note = _repro_output_cut_note({"V1_Modeling.R": 2300000}, 20000)
    assert note is not None
    assert "first 20,000 lines of the output of `V1_Modeling.R` (2,300,000 lines)" in note
    two = _repro_output_cut_note({"a.R": 30000, "b.R": 25000})
    assert two is not None
    assert "`a.R` (30,000 lines), `b.R` (25,000 lines)" in two
    assert two.startswith("*Statistical results were searched for in the first 20,000 lines")
    assert two.endswith("to keep the check within its time.*")


# ---------------------------------------------------------------------------
# the repository's own Python modules
# ---------------------------------------------------------------------------

_FILES = [
    "repo/maad/__init__.py",
    "repo/maad/sound.py",
    "repo/scripts/run.py",
    "repo/lib/helper.py",
    "repo/scripts/beside.py",
    "other/maad/__init__.py",
    "bad/json.py",
    "bad/own.py",
]


def test_py_module_folders_finds_the_repositorys_own_modules() -> None:
    """.repro_py_module_folders finds the repository's own modules (#568)"""
    f = _repro_py_module_folders
    assert f("from maad import sound", "repo/scripts/run.py", _FILES) == ["repo"]
    assert f("import helper", "repo/scripts/run.py", _FILES) == ["repo/lib"]
    # nearest to the script, when a name is in two places
    assert f("import maad", "other/sub/x.py", _FILES) == ["other"]
    # beside the script Python finds it already; what is not in the repository is not looked for
    assert f("import beside", "repo/scripts/run.py", _FILES) == []
    assert f(["import numpy", "import os"], "repo/scripts/run.py", _FILES) == []
    # a folder that would hide a standard module the script uses
    assert f(["import json", "import own"], "repo/scripts/run.py", _FILES) == []
    assert f("import own", "repo/scripts/run.py", _FILES) == ["bad"]
    # comments do not count
    assert f("# import helper", "repo/scripts/run.py", _FILES) == []


def test_py_module_folders_edge_cases() -> None:
    """Several names, aliases, the top of the repository, a module that is a folder's file."""
    f = _repro_py_module_folders
    run = "repo/scripts/run.py"
    assert f(["import helper as h, maad.sound", "import os"], run, _FILES) == ["repo/lib", "repo"]
    assert f("from helper import x", run, []) == []
    assert f("import top", "a/b.py", ["top.py", "a/other.py"]) == ["."]
    # the script's own folder is not added
    assert f("import helper", "repo/lib/x.py", _FILES) == []
    # backslashes in the paths
    assert f("import helper", "repo\\scripts\\run.py", [x.replace("/", "\\") for x in _FILES]) == [
        "repo/lib"
    ]


def test_py_stdlib_modules_are_metachecks_and_inside_the_dependency_list() -> None:
    """The static list of the standard library (R: .repro_py_stdlib_modules)"""
    mods = _repro_py_stdlib_modules()
    assert len(mods) == len(set(mods)) == 205
    assert {"os", "json", "tomllib", "distutils", "cProfile"} <= set(mods)
    assert "parser" not in mods
    # the list used for the dependencies holds all of these (and more)
    assert set(mods) <= _PY_STDLIB


def test_pip_name_maps_the_import_name_of_the_top_module() -> None:
    """Python install names (#568)"""
    assert _repro_py_pip_name("sklearn.linear_model") == "scikit-learn"
    assert _repro_py_pip_name(["pywt", "ruamel.yaml", "pylab", "numpy", None]) == [
        "PyWavelets",
        "ruamel.yaml",
        "matplotlib",
        "numpy",
        None,
    ]


def test_py_import_links_and_local_closure_follow_the_repositorys_own_imports() -> None:
    """The repository's own files a Python file imports, and those they import in turn."""
    imports = {
        "scripts/run.py": ["numpy", "run_NNs"],
        "run_NNs.py": ["torch", "fluxrgnn"],
        "fluxrgnn/__init__.py": ["utils"],
        "fluxrgnn/utils.py": ["numpy"],
        "lonely.py": [],
    }
    paths = list(imports)
    links = _repro_py_import_links(imports, paths)
    # stems: run, run_NNs, __init__, utils, lonely (positions count from 0)
    assert links["scripts/run.py"] == [1]
    assert links["run_NNs.py"] == []
    assert links["fluxrgnn/__init__.py"] == [3]
    assert links["lonely.py"] == []
    assert _repro_py_local_closure(imports, paths, "scripts/run.py") == ["run_NNs.py"]
    assert _repro_py_local_closure(imports, paths, "fluxrgnn/__init__.py", links) == [
        "fluxrgnn/utils.py"
    ]
    assert _repro_py_local_closure(imports, paths, "lonely.py") == []
    assert _repro_py_local_closure(imports, paths, "not a file") == []


def test_py_import_links_use_the_file_name_for_a_missing_path_and_cycles_end() -> None:
    """A path that is NA or empty falls back to the file name; imports in a cycle end."""
    imports = {"a.py": ["b"], "b.py": ["a", "c"], "c.py": ["a"]}
    links = _repro_py_import_links(imports, [None, "", "src\\c.py"])
    assert links == {"a.py": [1], "b.py": [0, 2], "c.py": [0]}
    assert _repro_py_local_closure(imports, [None, "", "src\\c.py"], "a.py") == ["b.py", "c.py"]


def test_py_get_ipython_adds_a_stand_in_only_when_needed_and_in_a_valid_place() -> None:
    """.repro_py_get_ipython adds a stand-in only when needed, and in a valid place (#568)"""
    assert _repro_py_get_ipython("x = 1") == ["x = 1"]
    txt = ["# -*- coding: latin-1 -*-", "", 'if "colab" in str(get_ipython()):', "    pass"]
    out = _repro_py_get_ipython(txt)
    assert out[:2] == txt[:2]
    assert (out[2] or "").startswith("get_ipython = lambda")
    assert out[3:] == txt[2:]
    fut = _repro_py_get_ipython(
        ['"""doc"""', "from __future__ import print_function", "get_ipython().system('ls')"]
    )
    assert (fut[2] or "").startswith("get_ipython = lambda")
    own = ["def get_ipython(): pass", "get_ipython()"]
    assert _repro_py_get_ipython(own) == own
    assert _repro_py_get_ipython([]) == []
    assert _repro_py_parses([out, fut]).all()


def test_py_get_ipython_stand_in_works() -> None:
    """The stand-in answers like a function that does nothing, whatever method is called."""
    out = _repro_py_get_ipython(["get_ipython().run_line_magic('matplotlib', 'inline')", "x = 1"])
    ns: dict[str, object] = {}
    exec("\n".join(s or "" for s in out), ns)
    assert ns["x"] == 1


def test_docker_image_py2_is_the_option_or_the_python_27_image() -> None:
    """The image for Python 2 code: option, default, or off."""
    assert _repro_docker_image_py2() == "python:2.7-slim"
    for off in (False, ""):
        try:
            options({"metacheck.docker_image_py2": off})
            assert _repro_docker_image_py2() is None
        finally:
            options({"metacheck.docker_image_py2": None})
    try:
        options({"metacheck.docker_image_py2": ["my/py2:1", "other"]})
        assert _repro_docker_image_py2() == "my/py2:1"
    finally:
        options({"metacheck.docker_image_py2": None})
    assert _repro_docker_image_py2() == "python:2.7-slim"


# ---------------------------------------------------------------------------
# files that only define functions
# ---------------------------------------------------------------------------


def test_function_names_recognises_a_file_that_only_defines_functions() -> None:
    """.repro_function_names recognises a file that only defines functions (#568)"""
    f = _repro_function_names
    assert f(["library(a)", "k <- 1", "f <- function(x) x", "g = function() 2"]) == ["f", "g"]
    assert f(['"doc"', 'pal <- c("a", "b")', "setClass('A')", "h <- function() 1"]) == ["h"]
    # anything else it does makes it a script
    assert f(["f <- function(x) x", "f(1)"]) == []
    assert f(["f <- function(x) x", "d <- read.csv('a.csv')"]) == []
    assert f(["f <- function(x) x", "print(1)"]) == []
    # no functions at all, or a file that does not parse
    assert f(["library(a)", "k <- 1"]) == []
    assert f("f <- function(") == []


def test_function_names_reads_an_empty_argument() -> None:
    """.repro_assigned_names finds text assigned to a variable (#568): the function part"""
    assert _repro_function_names(["f <- function(x) x", "k <- x[1, ]"]) == []
    assert _repro_function_names(["f <- function(x) x[1, ]", "k <- c(1, )"]) == ["f"]


def test_function_names_other_forms() -> None:
    """Quoted names, lambdas, <<-, globalVariables, constants; other calls and symbols stop it."""
    f = _repro_function_names
    assert f(['"f" <- function(x) x', "g <<- \\(x) x + 1", "h = function() NULL"]) == [
        "f",
        "g",
        "h",
    ]
    assert f(["utils::globalVariables(c('a', 'b'))", "f <- function() 1"]) == ["f"]
    assert f(["f <- function() 1", "TRUE", "'a string'", "x <- c('a', c(1, 2))"]) == ["f"]
    assert f(["f <- function() 1", "x <- NULL"]) == []
    assert f(["f <- function() 1", "x <- -1"]) == []
    assert f(["f <- function() 1", "x <- c(a, 1)"]) == []
    assert f(["f <- function() 1", "other::g(1)"]) == []
    assert f(["f <- function() 1", "x"]) == []
    assert f(["f <- function() 1", "if (TRUE) g <- function() 2"]) == []
    assert f(["f <- function() 1", "a$b <- function() 2"]) == []
    assert f(["f <- function() 1", "x <- y <- function() 2"]) == []
    assert f([]) == []
    assert f(None) == []
    assert f(["# only a comment"]) == []


def test_function_users_names_the_scripts_that_use_a_files_functions() -> None:
    """.repro_function_users names the scripts that use a file's functions (#568)"""
    code = {
        "f.R": "fit <- function(x) x",
        "a.R": "fit(1)",
        "b.R": "misfit(1)",
        "c.R": "x <- fit",
        "d.R": 'source("f.R")',
        "e.R": "stats::fit(2)",
    }
    users = _repro_function_users("f.R", "fit", code, {"d.R": "f.r"})
    assert set(users) == {"a.R", "d.R", "e.R"}
    assert users == ["a.R", "d.R", "e.R"]
    assert _repro_function_users("f.R", "nothing", code) == []
    # a dot in a name is a dot
    assert _repro_function_users("f.R", "a.b", {"f.R": "", "g.R": "axb(1)"}) == []
    assert _repro_function_users("f.R", "a.b", {"f.R": "", "g.R": "a.b(1)"}) == ["g.R"]
    # a call after a space, on a later line; nothing to look for or in
    assert _repro_function_users("f.R", ["fit"], {"f.R": "", "g.R": ["x <- 1", "fit  (2)"]}) == [
        "g.R"
    ]
    assert _repro_function_users("f.R", [], code) == []
    assert _repro_function_users("f.R", "fit", {"f.R": ""}) == []
    # sourced by a path in another folder
    assert _repro_function_users(
        "sub\\F.R", "zz", {"sub\\F.R": "", "m.R": "x"}, {"m.R": ["f.r"]}
    ) == ["m.R"]


# ---------------------------------------------------------------------------
# the report: files not run on their own
# ---------------------------------------------------------------------------


def test_not_run_report_gives_the_sentences_for_files_that_are_not_part_of_the_analysis() -> None:
    """the sentences for files that are not part of the analysis (#568)"""
    why = [
        None,
        "It is part of the code of the package p, which other files use; "
        "it is not a script to run on its own.",
    ]
    rep = _repro_not_run_report(["a.R", "b.R"], ["not_analysis", "not_analysis"], why=why)
    assert rep is not None
    text = "\n".join(x if isinstance(x, str) else x.data.to_csv() for x in rep)
    assert "2 files are not part of the analysis" in text
    assert "package p" in text
    assert "test of a package" in text
    assert rep[0] == "#### Files not run on their own"
    tables = [x for x in rep if isinstance(x, ReportTable)]
    assert len(tables) == 1
    assert list(tables[0].data.columns) == ["File", "Why"]
    assert tables[0].maxrows == 10


def test_not_run_report_functions_and_their_users() -> None:
    """Files that only define functions: who uses them, and the counts in the sentence."""
    rep = _repro_not_run_report(
        ["f.R", "g.R", "run.R", "t.R"],
        ["defines_functions", "defines_functions", None, "not_analysis"],
        used_by=["run.R, other.R", None, None, None],
    )
    assert rep is not None
    assert rep[1] == (
        "2 files only define functions, so running them on their own says nothing: a function "
        "counts when a script uses it. These files are not counted as scripts. 1 is used by "
        "no script in the deposit."
    )
    first = rep[2]
    assert isinstance(first, ReportTable)
    assert first.data["File"].tolist() == ["f.R", "g.R"]
    assert first.data["Used by"].tolist() == ["run.R, other.R", "no script"]
    assert rep[3] == (
        "1 file is not part of the analysis but of software (a package, its tests or "
        "documentation, a tool, a web application), so it was not run or counted as scripts."
    )
    one = _repro_not_run_report(["f.R"], ["defines_functions"])
    assert one is not None
    assert one[1].startswith("1 file only defines functions, so running it on its own says")
    assert one[1].endswith("1 is used by no script in the deposit.")


def test_not_run_report_is_none_when_every_file_is_run() -> None:
    """NULL when no file is skipped for these reasons."""
    assert _repro_not_run_report(["a.R"], ["ran"]) is None
    assert _repro_not_run_report(["a.R"], [None]) is None
    assert _repro_not_run_report([], []) is None


# ---------------------------------------------------------------------------
# downloads, later passes
# ---------------------------------------------------------------------------


def test_download_limits_keeps_the_network_off_unless_asked() -> None:
    """.repro_download_limits keeps the network off unless asked (#568)"""
    assert _repro_download_limits(False) is None
    assert _repro_download_limits() is None
    net = _repro_download_limits(True, 100, 500)
    assert net is not None
    assert net["max_file"] == 100 * 1024**2
    assert net["max_total"] == 500 * 1024**2
    assert net["used"]["bytes"] == 0
    # no limit
    unlimited = _repro_download_limits(True, math.inf, None)
    assert unlimited is not None
    assert unlimited["max_total"] == math.inf
    assert unlimited["max_file"] == math.inf
    # what was received so far is shared by every script that gets the same object
    net["used"]["bytes"] += 5
    assert net["used"] == {"bytes": 5}


def _results(
    outcome: list[str], error_type: list[str | None], elapsed: list[float]
) -> pd.DataFrame:
    n = len(outcome)
    return pd.DataFrame(
        {
            "file_name": pd.array([f"s{i}.R" for i in range(n)], dtype="string"),
            "outcome": pd.array(outcome, dtype="string"),
            "error": pd.array(["boom"] * n, dtype="string"),
            "error_type": pd.array(error_type, dtype="string"),
            "elapsed": pd.array(elapsed, dtype="Float64"),
        }
    )


def test_later_pass_turns_a_script_the_time_limit_stopped_into_skipped_time_limit() -> None:
    """A script stopped by the time left for all scripts says nothing about the change (#581)."""
    res = _results(
        ["timed_out", "timed_out", "errored", "ran_ok"],
        ["deadline", "timeout", "deadline", None],
        [12.2, 30.0, 1.0, 0.5],
    )
    out = _repro_later_pass(res)
    assert out is not None
    assert out["outcome"].tolist() == ["skipped_time_limit", "timed_out", "errored", "ran_ok"]
    assert out["error"].tolist() == [
        "stopped after 13 s: the time for all scripts together had run out",
        "boom",
        "boom",
        "boom",
    ]
    # the first pass is unchanged
    assert res["outcome"].tolist() == ["timed_out", "timed_out", "errored", "ran_ok"]
    assert str(out["outcome"].dtype) == "string"


def test_later_pass_leaves_other_results_alone() -> None:
    """Nothing to change, or no result table at all."""
    res = _results(["ran_ok"], [None], [1.0])
    assert _repro_later_pass(res) is res
    assert _repro_later_pass(None) is None
    empty = res.iloc[0:0]
    assert _repro_later_pass(empty) is empty
    assert _repro_later_pass(res.drop(columns="error_type")) is not None
    missing = _results(["timed_out"], ["deadline"], [0.0])
    missing["elapsed"] = pd.array([None], dtype="Float64")
    out = _repro_later_pass(missing)
    assert out is not None
    assert out["error"].tolist() == [
        "stopped after NA s: the time for all scripts together had run out"
    ]
