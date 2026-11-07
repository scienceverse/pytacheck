"""The checklist section of reproducibility_check (port of test-reproducibility_check.R).

metacheck's tests call ``.repro_checklist_section()`` with ``data.frame()`` rows whose
single values are recycled; here the columns are spelled out.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.repro.outcomes import _repro_internet_text
from metacheck.repro.section import (
    _repro_advice,
    _repro_cell,
    _repro_checklist_section,
)


def _table(files: list[str], outcome: list[str | None], **extra: Any) -> pd.DataFrame:
    n = len(files)
    return pd.DataFrame(
        {
            "file_name": files,
            "run_order": list(range(1, n + 1)),
            "outcome": outcome,
            "setwd_removed": [0] * n,
            "not_runnable_reason": pd.array([None] * n, dtype="string"),
            "unresolved_inputs": pd.array([None] * n, dtype="string"),
            **extra,
        }
    )


def _results(files: list[str], outcome: list[str | None], **extra: Any) -> pd.DataFrame:
    n = len(files)
    columns: dict[str, Any] = {
        "file_name": files,
        "outcome": outcome,
        "error": [""] * n,
        "stderr": [""] * n,
    }
    columns.update(extra)
    return pd.DataFrame(columns)


def _one(outcome: str, **extra: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One script, ``m.R``, with this outcome; *extra* are columns of the run results."""
    stderr = extra.pop("stderr", "")
    error = extra.pop("error", "")
    return (
        _table(["m.R"], [outcome], **extra),
        _results(["m.R"], [outcome], stderr=[stderr], error=[error]),
    )


def _section(tb: pd.DataFrame, rr: pd.DataFrame, **kwargs: Any) -> str:
    out = _repro_checklist_section(tb, rr, **kwargs)
    assert isinstance(out, str)
    return out


def _has(blocks: list[str], text: str) -> bool:
    return any(text in b for b in blocks)


# the count --------------------------------------------------------------------


def test_a_model_file_is_not_counted_as_a_script_that_failed() -> None:
    tb = _table(["m.R", "run.R"], ["jags_model", "ran_ok"])
    tb["not_runnable_reason"] = pd.array(["jags_model", None], dtype="string")
    out = _section(tb, _results(["m.R", "run.R"], ["jags_model", "ran_ok"]))
    assert "**1 of 1 script ran to the end**" in out
    assert "It is a JAGS model, which another script reads." in out


def test_the_reason_a_file_is_not_part_of_the_analysis() -> None:
    why = (
        "It is part of the code of the package p, which other files use; it is not a script "
        "to run on its own."
    )
    files = ["R/a.R", "test.R", "run.R"]
    outcome = ["not_analysis", "not_analysis", "ran_ok"]
    tb = _table(files, outcome, not_analysis_why=pd.array([why, None, None], dtype="string"))
    out = _section(tb, _results(files, outcome))
    assert "**1 of 1 script ran to the end**" in out
    assert "part of the code of the package p" in out
    assert "It is a test of a package or the file that installs it" in out


def test_function_files_and_tests_are_left_out_of_the_count() -> None:
    files = ["f.R", "t.R", "g.R", "run.R"]
    outcome = ["defines_functions", "not_analysis", "defines_functions", "ran_ok"]
    tb = _table(files, outcome, used_by=pd.array(["run.R", None, None, None], dtype="string"))
    out = _section(tb, _results(files, outcome))
    assert "**1 of 1 script ran to the end**" in out
    assert "We ran the paper's 1 R script" in out
    assert "which run.R uses" in out
    assert "no script in the deposit uses them" in out


def test_docker_failure_is_not_counted_as_the_scripts() -> None:
    tb, rr = _one("docker_failed", error="x")
    out = _section(tb, rr)
    assert "says nothing about the script" in out
    assert "stopped with an error" not in out
    assert "**0 of 1 script ran to the end**: 1 could not run on our server." in out


def test_both_kinds_of_scripts_that_could_not_run_on_our_server_are_one_group() -> None:
    files = ["a.R", "b.R"]
    outcome = ["dependency_unavailable", "docker_failed"]
    out = _section(_table(files, outcome), _results(files, outcome))
    assert "**0 of 2 scripts ran to the end**: 2 could not run on our server." in out


def test_a_corrected_name_is_noted() -> None:
    tb, rr = _one("ran_ok", names_corrected="a b.csv -> a_b.csv")
    out = _section(tb, rr)
    assert _repro_cell("a b.csv -> a_b.csv") in out
    assert "should be changed in the script" in out


def test_other_problems_in_the_files() -> None:
    tb, rr = _one("ran_ok", commands_removed="!pip install x")
    tb["setwd_removed"] = [2]
    out = _section(tb, rr)
    assert "### Other problems in the files" in out
    assert "- m.R calls setwd(), which ties the script to a folder" in out
    assert "holds notebook commands that are not Python (!pip install x)" in out


def test_no_script_run_gives_no_section() -> None:
    tb, rr = _one("ran_ok")
    assert _repro_checklist_section(None, rr) is None
    assert _repro_checklist_section(tb.iloc[0:0], rr) is None
    assert _repro_checklist_section(tb, None) is None
    assert _repro_checklist_section(tb, rr.iloc[0:0]) is None


def test_the_scripts_are_listed_in_run_order() -> None:
    tb = _table(["b.R", "a.R", "c.R"], ["ran_ok"] * 3)
    tb["run_order"] = [2, 1, None]
    out = _section(tb, _results(["a.R", "b.R", "c.R"], ["ran_ok"] * 3))
    assert out.index("| a.R |") < out.index("| b.R |") < out.index("| c.R |")


def test_the_comparison_with_the_paper() -> None:
    tb, rr = _one("errored", stderr="Error in f() : boom")
    assert "We found no statistical tests" in _section(tb, rr)
    out = _section(tb, rr, n_tests=3, n_found=1)
    assert "Of the 3 statistical tests reported in the paper, 1 was found in the output" in out
    assert "No script ran to the end, so most results were never produced." in out
    ok, rr_ok = _one("ran_ok")
    out = _section(ok, rr_ok, n_tests=1, n_found=0)
    assert "Of the 1 statistical test reported in the paper, 0 were found" in out
    assert "No script ran" not in out


def test_the_introduction() -> None:
    tb, rr = _one("ran_ok")
    out = _section(tb, rr, n_dup=2, timeout=90, recorded_note="Versions were recorded.")
    assert out.startswith(
        "We ran the paper's 1 R script in the order they depend on each other, each in an "
        "isolated container, for at most 1.5 minutes per script. We did not install packages "
        "the scripts need. Versions were recorded. 2 identical copies of these scripts, which "
        "other links in the paper lead to, were not run again."
    )
    assert "each in a separate R or Python session" in _section(tb, rr, sandbox="process")
    assert "We did not install" not in _section(tb, rr, install_missing=True)


def test_the_python_scripts_are_counted_apart() -> None:
    files = ["a.R", "b.py", "c.ipynb"]
    out = _section(_table(files, ["ran_ok"] * 3), _results(files, ["ran_ok"] * 3))
    assert "the paper's 1 R script and 2 Python scripts" in out


# the result of a script --------------------------------------------------------


def test_a_package_found_nowhere_is_reported_as_one_the_code_does_not_say_where_to_get() -> None:
    tb, rr = _one(
        "dependency_unavailable",
        stderr="Error in library(ecodeal) : there is no package called 'ecodeal'",
    )
    out = _section(tb, rr, install_missing=True, source_unknown=["ecodeal"])
    assert (
        "which is not on CRAN or Bioconductor, and the code does not say where it comes from" in out
    )
    assert "which we could not install" in _section(tb, rr, install_missing=True)
    assert "is not installed on our server (we do not install packages)" in _section(tb, rr)


def test_the_error_of_a_script_that_stopped() -> None:
    tb, rr = _one(
        "errored", stderr="Error in f() : object 'z' not found\nCalls: f\nExecution halted"
    )
    out = _section(tb, rr)
    assert "| m.R | Stopped with an error | <code>Error in f() : object &#39;z&#39;" not in out
    assert (
        "| m.R | Stopped with an error | <code>Error in f() : object 'z' not found</code> |" in out
    )
    tb, rr = _one("errored")
    assert "The script stopped with an error (see its output below)." in _section(tb, rr)


def test_the_internet_is_named_when_scripts_could_use_it() -> None:
    net = {"max_file": 100 * 1024**2, "max_total": 500 * 1024**2}
    files = ["a.R", "b.R"]
    outcome = ["download_too_large", "timed_out"]
    error = [
        "stopped: it downloaded x.zip, a file of 120 MB, more than the limit of 100 MB per file "
        "(max_file_size)",
        "timed out after 60s while a download was still arriving (12 MB received)",
    ]
    tb = _table(files, outcome)
    rr = _results(files, outcome, error=error)
    out = _section(tb, rr, timeout=60, net=net)
    assert "The scripts could use the internet" in out
    assert _repro_internet_text(net) in out
    assert "a file of 120 MB" in out
    assert "while it was still downloading" in out
    assert "1 downloaded too much" in out
    # with the network off, nothing about the internet
    assert "could use the internet" not in _section(tb.iloc[[1]], rr.iloc[[1]], timeout=60)


def test_a_script_that_needs_the_internet_says_whether_it_was_on() -> None:
    tb, rr = _one("needs_internet")
    assert "It needs the internet, which is off while we run code." in _section(tb, rr)
    net = {"max_file": float("inf"), "max_total": float("inf")}
    assert "It could not reach what it asked for on the internet." in _section(tb, rr, net=net)


def test_a_program_a_script_could_not_start_is_named() -> None:
    mark = "\n".join(
        [
            "sh: 1: mark: not found",
            "Error in run.mark.model(model) : ",
            "Output file does not exist. Unable to find or run mark.exe",
            "Execution halted",
        ]
    )
    tb, rr = _one("errored", stderr=mark)
    assert "<code>m.R</code> starts <code>mark</code>." in _section(tb, rr)


def test_a_file_renamed_before_it_was_shared_is_named_not_used() -> None:
    tb = _table(["p.R"], ["errored"])
    tb["unresolved_inputs"] = pd.array(["plants2024.txt"], dtype="string")
    stderr = (
        'Error in file(file, "rt") : cannot open the connection\nIn addition: Warning message:\n'
        "cannot open file 'Plants2024.txt': No such file or directory"
    )
    rr = _results(["p.R"], ["errored"], stderr=[stderr])
    mi = pd.DataFrame(
        {
            "basename": ["plants2024.txt"],
            "status": ["absent"],
            "detail": [""],
            "similar_to": ["plants.txt"],
        }
    )
    out = _section(tb, rr, missing_inputs=mi)
    assert (
        "<code>p.R</code> reads <code>Plants2024.txt</code>; your deposit holds "
        "<code>plants.txt</code>."
    ) in out


def test_skipped_missing_inputs_name_the_files_and_why() -> None:
    tb = _table(["m.R"], ["skipped_missing_inputs"])
    tb["unresolved_inputs"] = pd.array(["a.csv, b.csv, c.csv"], dtype="string")
    mi = pd.DataFrame(
        {
            "basename": ["a.csv", "b.csv"],
            "status": ["withheld_size", "in_archive"],
            "detail": ["", ""],
            "similar_to": [None, "b_.csv"],
        }
    )
    out = _section(tb, _results(["m.R"], ["skipped_missing_inputs"]), missing_inputs=mi)
    listed = _repro_cell(
        "a.csv (too large for us to download); "
        "b.csv (only inside a .zip or .tar file, but b_.csv is); c.csv (not in the repository)"
    )
    assert f"It reads files that are not available: {listed}." in out
    assert "b&#95;.csv is" in out


def test_a_script_that_ran_out_of_memory_says_how_much_it_could_use() -> None:
    # 10.1111/1365-2435.14319: g++ ran out of memory compiling a Stan model
    gpp = "g++: fatal error: Killed signal terminated program cc1plus"
    tb, rr = _one("out_of_memory", stderr=gpp)
    out = _section(tb, rr, memory_gb=4)
    assert "1 ran out of memory" in out
    assert "It needed more than the 4 GB of memory it could use here." in out
    assert "**A script needs more memory than our limit of 4 GB.**" in out
    out = _section(tb, rr)
    assert "It needed more memory than it could use here." in out
    assert "**A script needs more memory than our limit.**" in out


def test_a_missing_stderr_or_error_column_is_no_text() -> None:
    tb, _ = _one("errored")
    rr = pd.DataFrame({"file_name": ["m.R"], "outcome": ["errored"]})
    out = _section(tb, rr)
    assert "The script stopped with an error (see its output below)." in out


def test_a_script_without_a_result_gets_its_static_outcome() -> None:
    tb = _table(["a.R", "b.R"], [None, None])
    tb["not_runnable_reason"] = pd.array(["python2", "unplaceable"], dtype="string")
    out = _section(tb, _results(["z.R"], ["ran_ok"]))
    assert "| a.R | Not run | It is written for Python 2" in out
    assert "| b.R | Not run | We could not work out when to run it" in out
    assert "**0 of 2 scripts ran to the end**: 2 were not run." in out


# _repro_advice() --------------------------------------------------------------


def _advice_inputs() -> tuple[pd.DataFrame, pd.DataFrame, list[str | None], pd.DataFrame]:
    files = [f"{c}.R" for c in "abcdefghi"]
    tbl = pd.DataFrame(
        {"file_name": files, "writes": pd.array([None, None, "y.csv"] + [None] * 6, dtype="string")}
    )
    stderr = [
        'Error in file(file, "rt") : cannot open the connection\nIn addition: Warning message:\n'
        "cannot open file '/Users/me/x.csv': No such file or directory",
        'Error in file(file, "rt") : cannot open the connection\nIn addition: Warning message:\n'
        "cannot open file 'y.csv': No such file or directory",
        "Error in f() : object 'z' not found",
        "Error: `funs()` was deprecated in dplyr 0.8.0 and is now defunct.",
        "Error in library(rgdal) : there is no package called 'rgdal'",
        "Error in `$<-.data.frame`(x, a, value = 1) : replacement has 1 row, data has 0",
        "",
        "",
        "",
    ]
    rr = pd.DataFrame({"file_name": files, "error": [""] * 9, "stderr": stderr})
    outcome: list[str | None] = [
        "errored",
        "errored",
        "errored",
        "errored",
        "dependency_unavailable",
        "errored",
        "needs_a_person",
        "timed_out",
        "ran_ok",
    ]
    return tbl, rr, outcome, pd.DataFrame({"basename": []})


def test_how_to_make_the_code_run_is_given_per_problem_in_the_order_to_fix_them() -> None:
    tbl, rr, outcome, mi = _advice_inputs()
    ir = pd.DataFrame(
        {"package": ["rgdal"], "installed": [False], "category": ["cran_unavailable"]}
    )
    adv, scripts = _repro_advice(tbl, rr, outcome, mi, ir, timeout=3000)
    # a script that needs another script's file comes first: that one is fixed first
    assert "**A script needs a file that another script should have made.**" in adv[0]
    assert "<code>b.R</code> needs <code>y.csv</code>, which <code>c.R</code> makes." in adv[0]
    assert _has(
        adv,
        "**A script uses a location that exists only on your computer.**\n\n"
        "- <code>a.R</code> reads <code>/Users/me/x.csv</code>.",
    )
    assert _has(adv, "1. Add the file to the deposit.")
    assert _has(adv, "**Your code was written for older versions of its packages.**")
    assert _has(
        adv,
        "<code>rgdal</code> was removed from CRAN in 2023 and can no longer be installed. "
        "Replace it with <code>sf</code> or <code>terra</code>",
    )
    assert _has(adv, "- <code>c.R</code>: <code>z</code>")
    assert _has(adv, "**A script runs longer than our limit of 50 minutes.**")
    assert _has(adv, "**A script waits for a person")
    # a cause it cannot tell is said to be unknown, last
    assert adv[-1].startswith(
        "**Metacheck can't automatically establish what caused the error, or how it should "
        "be improved.**\n\n- <code>f.R</code>"
    )
    # every script that did not run through is in a block, the one that ran in none
    assert scripts == list(range(8))
    # with recorded versions, a run with them is suggested instead of recording them
    rec, _ = _repro_advice(tbl, rr, outcome, mi, ir, versions_recorded=True)
    assert _has(rec, "Your deposit records which package versions you used")
    assert not any("renv::init()</code> in your project" in b for b in rec)
    # nothing failed, nothing to say
    none, covered = _repro_advice(tbl.iloc[[8]], rr.iloc[[8]], ["ran_ok"], mi)
    assert none == []
    assert covered == []


def test_advice_for_a_package_found_nowhere_and_for_an_old_one() -> None:
    tbl = pd.DataFrame({"file_name": ["a.R", "b.R"]})
    rr = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R"],
            "error": ["", ""],
            "stderr": [
                "Error in library(ecodeal) : there is no package called 'ecodeal'",
                "Error in library(oldpkg) : there is no package called 'oldpkg'",
            ],
        }
    )
    ir = pd.DataFrame(
        {
            "package": ["ecodeal", "oldpkg"],
            "installed": [False, False],
            "category": ["source_unknown", "compile_failure"],
        }
    )
    adv, _ = _repro_advice(
        tbl, rr, ["dependency_unavailable"] * 2, pd.DataFrame({"basename": []}), ir
    )
    assert len(adv) == 1
    assert "- <code>a.R</code> needs <code>ecodeal</code>." in adv[0]
    assert (
        "<code>ecodeal</code> is not on CRAN, and your code does not say where it comes from."
        in adv[0]
    )
    assert 'remotes::install_github("user/ecodeal")' not in adv[0]  # escaped by the cell rule
    assert "<code>oldpkg</code> could not be installed with the current version of R" in adv[0]


def test_advice_for_a_file_inside_an_archive() -> None:
    tbl = pd.DataFrame({"file_name": ["a.R"]})
    rr = pd.DataFrame(
        {
            "file_name": ["a.R"],
            "error": [""],
            "stderr": [
                "Error in file(f) : cannot open file 'data/d.csv': No such file or directory"
            ],
        }
    )
    mi = pd.DataFrame(
        {
            "basename": ["d.csv"],
            "status": ["in_archive"],
            "detail": ["only inside Data.zip; unpack it"],
            "similar_to": [None],
        }
    )
    adv, _ = _repro_advice(tbl, rr, ["errored"], mi)
    assert (
        "<code>a.R</code> reads <code>data/d.csv</code>, which is inside <code>Data.zip</code>."
        in adv[0]
    )
    assert "**A file is only inside a zip file.**" in adv[0]


def test_the_checklist_says_how_to_make_the_code_run_on_another_computer() -> None:
    tb, rr = _one("errored", stderr="Error in f() : object 'z' not found")
    out = _section(tb, rr)
    assert "### How to make your code run on another computer" in out
    assert "**A script uses something it does not create or load itself.**" in out
    tb, rr = _one("ran_ok")
    assert "How to make your code run" not in _section(tb, rr)


def test_the_advice_blocks_cap_their_script_lists() -> None:
    files = [f"s{k:02d}.R" for k in range(12)]
    tbl = pd.DataFrame({"file_name": files})
    rr = pd.DataFrame(
        {
            "file_name": files,
            "error": [""] * 12,
            "stderr": ["Error in f() : object 'z' not found"] * 12,
        }
    )
    adv, scripts = _repro_advice(tbl, rr, ["errored"] * 12, pd.DataFrame({"basename": []}))
    assert "- and 2 more" in adv[0]
    assert "s09.R" in adv[0]
    assert "s10.R" not in adv[0]
    assert scripts == list(range(12))


# _repro_cell() ----------------------------------------------------------------


def test_cell_escapes_html_and_markdown() -> None:
    assert _repro_cell("a<b>&c") == "a&lt;b&gt;&amp;c"
    assert _repro_cell("x_y|z*") == "x&#95;y&#124;z&#42;"
    assert _repro_cell("`~^$[]\\") == "&#96;&#126;&#94;&#36;&#91;&#93;&#92;"
