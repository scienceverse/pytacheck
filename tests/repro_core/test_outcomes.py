"""How a script's run is classified and counted (port of test-reproducibility_check.R)."""

from __future__ import annotations

import pandas as pd

from metacheck.repro.outcomes import (
    _REPRO_REASON_LEVELS,
    _repro_absent_report,
    _repro_by_repository,
    _repro_docker_failed,
    _repro_download_stop_text,
    _repro_error_line,
    _repro_failure_reason,
    _repro_internet_text,
    _repro_mb,
    _repro_memory_limit_text,
    _repro_missing_program,
    _repro_needs_arguments,
    _repro_needs_internet_error,
    _repro_needs_internet_text,
    _repro_needs_person_error,
    _repro_oom_text,
    _repro_out_of_memory,
    _repro_py_starts_server,
    _repro_run_counts,
    _repro_source_unknown,
    _repro_source_unknown_text,
    _repro_starts_app,
    _repro_timeout_text,
    _repro_unopened_files,
)


def _reason(outcome: str, msg: str) -> str | None:
    return _repro_failure_reason([outcome], [msg])[0]


# _repro_error_line() ---------------------------------------------------------
# (no test of its own in metacheck: it is exercised through the section's tests)


def test_error_line_r_stops_at_the_closing_lines() -> None:
    se = "Error in f() : boom\n  more\nCalls: g -> f\nExecution halted"
    assert _repro_error_line(se) == "Error in f() : boom more"
    assert _repro_error_line("Warning message:\nnothing") == ""
    assert _repro_error_line(None) == ""


def test_error_line_python_is_the_last_error_of_the_traceback() -> None:
    se = "Traceback (most recent call last):\n  File x\nKeyError: 'a'\nValueError: b\n"
    assert _repro_error_line(se, python=True) == "ValueError: b"
    assert _repro_error_line("all fine", python=True) == ""


def test_error_line_is_cut_at_max_chars() -> None:
    assert _repro_error_line("Error: " + "x" * 400) == ("Error: " + "x" * 400)[:299] + "\u2026"


# .repro_source_unknown() -----------------------------------------------------


def test_source_unknown_packages() -> None:
    ir = pd.DataFrame(
        {
            "package": ["ecodeal", "dplyr", "rgdal"],
            "installed": [False, True, False],
            "category": ["source_unknown", None, "compile_failure"],
        }
    )
    assert _repro_source_unknown(ir) == ["ecodeal"]
    assert _repro_source_unknown(ir.iloc[1:]) == []
    assert _repro_source_unknown(None) == []
    assert _repro_source_unknown_text([]) is None
    one = _repro_source_unknown_text(["ecodeal"])
    assert one is not None
    assert (
        "**1 package the code loads is not on CRAN or Bioconductor, and the code does not say "
        "where it comes from**: `ecodeal`"
    ) in one
    two = _repro_source_unknown_text(["a", "b"])
    assert two is not None
    assert "where they come from**: `a`, `b`. " in two


# .repro_by_repository() ------------------------------------------------------


def test_by_repository_gives_a_line_per_repository_when_there_are_several() -> None:
    tb = pd.DataFrame(
        {
            "repo_name": ["a", "a", "b", "b"],
            "outcome": ["ran_ok", "errored", "errored", "defines_functions"],
        }
    )
    out = _repro_by_repository(tb)
    assert out is not None
    assert out[0] == "#### Results by repository"
    table = out[1].data  # type: ignore[union-attr]
    assert table["Repository"].tolist() == ["a", "b"]
    assert table["Scripts"].tolist() == [2, 1]
    assert table["Ran to the end"].tolist() == [1, 0]
    assert _repro_by_repository(tb.iloc[:2]) is None
    assert _repro_by_repository(pd.DataFrame({"outcome": ["ran_ok"]})) is None


# scripts that wait ----------------------------------------------------------


def test_py_starts_server_recognises_a_script_that_waits_for_connections() -> None:
    assert _repro_py_starts_server(["import http.server", "httpd.serve_forever()"])
    assert _repro_py_starts_server(["s = socket.socket()", "s.listen(5)"])
    assert _repro_py_starts_server(["app.run(debug = True)"])
    assert not _repro_py_starts_server(["import numpy", "# s.listen(5)", "print(1)"])
    assert not _repro_py_starts_server(["model.run(x)"])


def test_python_program_with_a_window_counts_as_needing_a_person() -> None:
    # 10_1111_1365_2435_14173: a Qt application waited until its time ran out
    assert _repro_py_starts_server(
        ["from PyQt5.QtWidgets import QApplication", "app = QApplication(sys.argv)"]
    )
    assert _repro_py_starts_server(["root = tk.Tk()", "root.mainloop()"])
    assert not _repro_py_starts_server(["import matplotlib.pyplot as plt", "plt.show()"])


def test_starts_app() -> None:
    assert _repro_starts_app(["library(shiny)", "shiny::runApp('a')"])
    assert _repro_starts_app(["shinyApp(ui, server)"])
    assert not _repro_starts_app(["f <- function() runApp('a')", "x <- 1"])
    assert not _repro_starts_app(["runApp("])


def test_needs_person_error() -> None:
    assert _repro_needs_person_error(["Error in file.choose() : file choice cancelled"]) == [True]
    assert _repro_needs_person_error(["EOFError: EOF when reading a line"]) == [True]
    assert _repro_needs_person_error(["Error: cannot open the connection"]) == [False]
    assert _repro_needs_person_error(
        [
            "Error in file(file, ifelse(append, \"a\", \"w\")) : 'mode' for the clipboard must be 'r' on Unix"
        ]
    ) == [True]


# .repro_failure_reason() -----------------------------------------------------


def test_failure_reason_names_why_a_script_did_not_run_through() -> None:
    assert _reason("errored", "cannot open file 'a.csv'") == "file"
    assert _reason("errored", "there is no package called 'x'") == "package"
    assert _reason("errored", "object 'x' not found") == "undefined"
    assert _reason("errored", "cannot cache function 'f'") == "environment"
    segfault = " *** caught segfault ***\naddress 0x10, cause 'memory not mapped'"
    assert _reason("errored", segfault) == "environment"
    assert _reason("errored", " ") == "no_message"
    assert _reason("errored", "list index out of range") == "other"
    assert _repro_failure_reason(
        ["python2", "needs_a_person", "needs_internet", "timed_out"], ["", "", "", ""]
    ) == ["syntax", "interactive", "internet", "time"]
    # what is not a failure of the script has none
    not_failures = [
        "ran_ok",
        "run_by_source",
        "defines_functions",
        "not_analysis",
        "jags_model",
        "docker_failed",
    ]
    assert _repro_failure_reason(not_failures, ["x"] * 6) == [None] * 6


def test_the_clipboard_and_a_screen_device_are_not_failures_of_the_code() -> None:
    msg = 'Error in dev.copy2eps(file = "x.eps") : can only print from a screen device'
    assert _reason("errored", msg) == "environment"


def test_a_download_that_is_too_large_or_too_slow_is_the_reason_a_script_failed() -> None:
    assert _reason("download_too_large", "stopped: ...") == "download"
    arriving = _repro_timeout_text(60, {"arriving": True, "received": 5e6})
    assert _reason("timed_out", arriving) == "download"
    assert (
        _reason("timed_out", _repro_timeout_text(60, {"arriving": False, "received": 5e6}))
        == "time"
    )
    assert _repro_timeout_text(60) == "timed out after 60s"
    assert arriving == "timed out after 60s while a download was still arriving (4.8 MB received)"


def test_the_reason_comes_from_the_error_that_stopped_the_script() -> None:
    # 10.1111/1365-2435.12953: a warning on a file, an error on an object
    se = "\n".join(
        [
            "Error in eval(ei, envir) : object 'egg_hatching_date' not found",
            "Calls: source -> withVisible -> eval -> eval",
            "In addition: Warning message:",
            'In tiff("C://your.file.directory/Figure1.tiff") : TIFFOpen: '
            "C://your.file.directory/Figure1.tiff: No such file or directory",
            "Execution halted",
        ]
    )
    assert _reason("errored", se) == "undefined"
    # Stan's "variable does not exist" is no file
    stan = "Error in eval(e) : Exception: variable does not exist; processing stage=data initialization"
    assert _reason("errored", stan) == "other"
    # "cannot open the connection" names no file: the warning that does counts
    conn = (
        'Error in file(file, "rt") : cannot open the connection\n'
        "In addition: Warning message:\ncannot open file 'x.csv': No such file or directory"
    )
    assert _reason("errored", conn) == "file"
    assert _reason("errored", "Error: `path` does not exist: 'x.xlsx'") == "file"
    py_missing = "FileNotFoundError: [Errno 2] No such file or directory: 'x.csv'"
    assert _reason("errored", py_missing) == "file"


# .repro_missing_program() ----------------------------------------------------

_MARK = "\n".join(
    [
        "sh: 1: mark: not found",
        "Error in run.mark.model(model) : ",
        "Output file does not exist. Unable to find or run mark.exe",
        "Execution halted",
    ]
)
_RSYNC = "\n".join(
    [
        '  File "/usr/local/lib/python3.11/subprocess.py", line 1955, in _execute_child',
        "FileNotFoundError: [Errno 2] No such file or directory: 'rsync'",
    ]
)


def test_a_program_a_script_could_not_start_is_the_reason_environment() -> None:
    assert _repro_missing_program([_MARK, _RSYNC, "Error: x", None]) == [
        "mark",
        "rsync",
        None,
        None,
    ]
    assert _repro_failure_reason(["errored", "errored"], [_MARK, _RSYNC]) == [
        "environment",
        "environment",
    ]
    # a missing data file read by Python is still a file
    data = "FileNotFoundError: [Errno 2] No such file or directory: 'data'"
    assert _repro_missing_program([data]) == [None]


# .repro_run_counts() ---------------------------------------------------------


def test_run_counts_give_a_papers_scripts_its_software_and_its_reasons() -> None:
    rr = pd.DataFrame(
        {
            "outcome": [
                "ran_ok",
                "errored",
                "errored",
                "dependency_unavailable",
                "run_by_source",
                "not_analysis",
                "not_analysis",
                "defines_functions",
                "docker_failed",
                "not_downloaded",
            ],
            "error": [
                "",
                "cannot open file 'a.csv'",
                "list index out of range",
                "",
                "",
                "",
                "",
                "",
                "x",
                "cannot open file 'big.rds'",
            ],
            "stderr": [""] * 10,
        }
    )
    rc = _repro_run_counts(rr)
    assert len(rc) == 1
    assert rc["repro_scripts_n"][0] == 4
    assert rc["repro_not_analysis_n"][0] == 2
    # a script that stopped on a file we did not download is counted apart, not as a failure
    assert rc["repro_not_downloaded_n"][0] == 1
    assert [rc["repro_fail_file"][0], rc["repro_fail_other"][0], rc["repro_fail_package"][0]] == [
        1,
        1,
        1,
    ]
    # every reason has its column, also with no script that failed for it
    assert rc["repro_fail_time"][0] == 0
    assert set(rc.columns) == {
        "repro_scripts_n",
        "repro_not_analysis_n",
        "repro_not_downloaded_n",
        *(f"repro_fail_{r}" for r in _REPRO_REASON_LEVELS),
    }
    # the reason column is read when the run results have one
    rr["reason"] = _repro_failure_reason(rr["outcome"].tolist(), rr["error"].tolist())
    rr.loc[1, "reason"] = "undefined"
    assert _repro_run_counts(rr)["repro_fail_undefined"][0] == 1
    # nothing run: the same columns, all NA
    for none in (_repro_run_counts(None), _repro_run_counts(rr.iloc[0:0])):
        assert list(none.columns) == list(rc.columns)
        assert none.isna().all(axis=None)


def test_run_counts_without_messages_count_no_message() -> None:
    # missing error and output are no text (R's paste() wrote "NA" for them)
    rr = pd.DataFrame({"outcome": ["errored"], "error": [None], "stderr": [None]}, dtype="string")
    assert _repro_run_counts(rr)["repro_fail_no_message"][0] == 1


# .repro_needs_internet_error() and the texts about it -----------------------


def test_needs_internet_error_reads_a_network_error() -> None:
    assert _repro_needs_internet_error(["cannot open URL 'https://a'"]) == [True]
    assert _repro_needs_internet_error(["requests.exceptions.ConnectionError: x"]) == [True]
    assert _repro_needs_internet_error(["cannot open file 'a.csv'"]) == [False]


def test_a_packages_data_downloaded_once_counts_as_needing_the_internet() -> None:
    msg = (
        "Error in hy_src(hydat_path) : No Hydat.sqlite3 found at /tmp/.local/share/tidyhydat. "
        "Run download_hydat() to download the database."
    )
    assert _repro_needs_internet_error([msg]) == [True]


def test_the_report_says_the_scripts_could_use_the_internet_and_what_stopped_one() -> None:
    net = {"max_file": 100 * 1024**2, "max_total": 500 * 1024**2}
    assert "up to 100 MB per file and 500 MB for all scripts together" in _repro_internet_text(net)
    assert "up to 500 MB for all scripts together" in _repro_internet_text(
        {"max_file": float("inf"), "max_total": 500 * 1024**2}
    )
    assert "use the internet, so what" in _repro_internet_text({"max_file": float("inf")})
    assert "could not reach what they asked for" in _repro_needs_internet_text(["a.R"], True)
    assert "which is off during the run" in _repro_needs_internet_text(["a.R"], False)
    assert "**2 scripts need the internet**" in _repro_needs_internet_text(["a.R", "b.R"])
    assert "**1 script needs the internet**" in _repro_needs_internet_text(["a.R"])
    stop = _repro_download_stop_text(["a.R", "b.R"])
    assert (
        "**2 scripts were stopped for downloading more than our limits allow**: a.R, b.R." in stop
    )


def test_mb() -> None:
    assert _repro_mb(5e6) == "4.8"
    assert _repro_mb(100 * 1024**2) == "100"
    assert _repro_mb(1234567.8 * 1024**2) == "1,234,568"


# .repro_absent_report() ------------------------------------------------------


def test_absent_report_puts_scripts_apart_from_data_and_leaves_out_what_has_a_near_match() -> None:
    mi = pd.DataFrame(
        {
            "basename": ["a.csv", "helpers.r", "b.csv", "c.csv"],
            "status": ["absent", "absent", "absent", "in_archive"],
            "detail": [""] * 4,
            "similar_to": [None, None, "b_.csv", None],
        }
    )
    report = _repro_absent_report(mi)
    assert report is not None
    out = "\n".join(report)
    assert "1 data file that is not in the deposit" in out
    assert "`a.csv`" in out
    assert "1 script that is not in the deposit: `helpers.r`" in out
    assert "b.csv" not in out
    assert "c.csv" not in out
    assert _repro_absent_report(mi.iloc[2:]) is None


# .repro_docker_failed() ------------------------------------------------------


def test_docker_failed_reads_an_error_from_docker_itself() -> None:
    assert _repro_docker_failed(
        ["Error response from daemon: Docker Desktop is unable to start"]
    ) == [True]
    docker_api = "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine"
    assert _repro_docker_failed([docker_api]) == [True]
    assert _repro_docker_failed(["Error in read.csv(f): cannot open the connection"]) == [False]


# .repro_unopened_files() -----------------------------------------------------


def test_unopened_files_reads_the_files_scripts_could_not_open() -> None:
    msgs = [
        "Error in file(file, \"rt\") : cannot open file 'data/late.csv': No such file or directory",
        "FileNotFoundError: [Errno 2] No such file or directory: 'in/x.tif'",
        "Error: 'C:/Users/a/b.rds' does not exist in current working directory",
        "object 'x' not found",
        None,
    ]
    assert set(_repro_unopened_files(msgs)) == {"late.csv", "x.tif", "b.rds"}
    assert set(_repro_unopened_files(msgs, full=True)) == {
        "data/late.csv",
        "in/x.tif",
        "C:/Users/a/b.rds",
    }


def test_unopened_files_reads_the_files_sf_and_terra_could_not_open() -> None:
    msgs = [
        'Error: Cannot open "data/x.shp"; The file doesn\'t seem to exist.',
        "Error: [rast] file does not exist: data/y.tif",
    ]
    assert set(_repro_unopened_files(msgs, full=True)) == {"data/x.shp", "data/y.tif"}


def test_unopened_files_reads_a_message_with_bytes_that_are_not_utf8() -> None:
    # a script in Latin-1, as many written on Windows are: the bytes arrive as surrogates
    latin1 = b"# caf\xe9".decode("utf-8", "surrogateescape")
    msg = (
        latin1
        + " Error in file(file, \"rt\") : cannot open file 'data/late.csv': No such file or directory"
    )
    assert _repro_unopened_files(msg) == ["late.csv"]


# .repro_needs_arguments() ----------------------------------------------------


def test_needs_arguments_programs_that_stopped_for_want_of_their_arguments() -> None:
    py = [["import sys", "f = sys.argv[1]"], ["import argparse"], ["x = [1][2]"]]
    err = [
        'File "a.py", line 2, in <module>\n    f = sys.argv[1]\nIndexError: list index out of range',
        "a.py: error: the following arguments are required: -i",
        'File "c.py", line 1\nIndexError: list index out of range',
    ]
    assert _repro_needs_arguments(py, err, "Python") == [True, True, False]
    r = [["args <- commandArgs(TRUE)"], ["args <- commandArgs(TRUE)"], ["x <- 1"]]
    err = [
        "Error in args[1] : object of type 'closure' is not subsettable",
        'Error in read.csv("x.csv") : cannot open file',
        "Error in args[1] : object of type 'closure' is not subsettable",
    ]
    assert _repro_needs_arguments(r, err, "R") == [True, False, False]


def test_a_program_that_prints_how_to_use_it_needs_arguments() -> None:
    # 10.1111/1365-2656.13545: Stacks tools that print their usage and stop
    py = [
        [
            "import sys",
            "try:",
            "    input_vcf = sys.argv[1]",
            "except:",
            "    print(__doc__)",
            "    sys.exit(1)",
        ]
    ]
    usage = (
        "Keep only the first SNP...\n\nUsage:\n    ./extract_first_snp.py input_vcf output_vcf\n"
    )
    assert _repro_needs_arguments(
        py, "the script stopped without a message", "Python", out=usage
    ) == [True]
    r = [
        [
            "library(optparse)",
            "opt <- parse_args(OptionParser(option_list = l))",
            'if (is.null(opt$prefix)) { print_help(p); stop("Please provide the prefix") }',
        ]
    ]
    help_text = "Usage: popgen.R [options]\n\t-h, --help\n\t\tShow this help message and exit"
    assert _repro_needs_arguments(r, "Error: Please provide the prefix", "R", out=help_text) == [
        True
    ]
    # no arguments read, the word "usage" in its results
    assert _repro_needs_arguments([["x <- 1"]], "Error: boom", "R", out="usage: 3 times") == [False]
    # without the output, as before
    assert _repro_needs_arguments(py, "the script stopped without a message", "Python") == [False]


# the memory limit ------------------------------------------------------------


def test_a_script_stopped_by_the_memory_limit_says_so_with_the_reason_environment() -> None:
    note = _repro_oom_text(137, memory_gb=3)
    assert note is not None
    assert "memory limit of 3 GB" in note
    assert _repro_oom_text(1, memory_gb=3) is None
    assert _repro_oom_text("137", memory_gb=3) == note
    assert _reason("errored", "Compiling " + note) == "environment"
    assert _repro_oom_text(137) is None
    assert _repro_memory_limit_text(memory_gb=2.5) == "2.5 GB"
    assert _repro_memory_limit_text() is None


def test_a_script_that_ran_out_of_memory_says_so_also_when_a_compiler_did() -> None:
    # 10.1111/1365-2435.14319: g++ ran out of memory compiling a Stan model
    gpp = "g++: fatal error: Killed signal terminated program cc1plus"
    assert _repro_out_of_memory(
        [
            gpp,
            "Error: Killed: wrong input",
            "Traceback\nMemoryError",
            "Error: cannot allocate vector of size 2.3 Gb",
            None,
        ]
    ) == [True, False, True, True, False]
    assert _reason("out_of_memory", gpp) == "environment"
