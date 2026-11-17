"""The changes of metacheck 0.4.3.8 to 0.4.3.28 in the reproducibility helpers.

Ports of the R tests of #591 (a folder a write needs), #594 (``lib.loc``), #597 (escaping),
#598 (install addresses, the CRAN fallback), #615 (``error_message``) and #620 (files not
read), and of the clock-time limit the scripts run under.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro import core
from metacheck.repro.core import (
    _repro_download_failed_msg,
    _repro_drop_author_lib_loc,
    _repro_min_prep_time,
    _repro_no_prep_text,
    _repro_short_of_time,
    repro_dependencies,
    repro_install_deps,
    repro_materialize_layout,
    repro_rewrite_paths,
)
from metacheck.repro.docker import _repro_install_error_lines
from metacheck.repro.layout import (
    _repro_folders_made_text,
    _repro_make_failed_write_folders,
    _repro_write_failed_folders,
)
from metacheck.repro.outcomes import _repro_error_message, _repro_read_failed_msg
from metacheck.repro.runsupport import _repro_not_run_report
from metacheck.utils import options

# #597: a file name is text, not a pattern ------------------------------------------------


def test_rewrite_takes_file_names_with_backslash_and_brackets_as_written() -> None:
    plan = pd.DataFrame(
        {
            "file_name": ["we2.tsv", "fit.csv", "results(1).csv", "a[1].csv", "data+x.csv"],
            "target_path": [
                "study-ex1/data/we2.tsv",
                "study-ex1/data/fit.csv",
                "study-ex1/data/results(1).csv",
                "study-ex1/data/a[1].csv",
                "study-ex1/data/data+x.csv",
            ],
        }
    )
    code = [
        r'd <- read.table("D:\AviaNZ\Primary_dataset\ruru\we2.tsv")',
        r'f <- read.csv("out\6.0\fit.csv")',
        'r <- read.csv(file.path(dir, "results(1).csv"))',
        'a <- read.csv("a[1].csv")',
        'x <- read.csv(here("data+x.csv"))',
    ]
    out = repro_rewrite_paths(code, "ex1/analysis.R", plan)
    assert {r.replace("\\", "/").split("/")[-1] for r in out["ref"]} == set(plan["file_name"])
    assert out["matched"].all()
    by_ref = out.set_index("ref")
    assert bool(by_ref.loc["results(1).csv", "in_join"])
    assert bool(by_ref.loc["data+x.csv", "in_here"])
    # "a[1].csv" is not taken as the pattern "a1.csv" or as "a.csv"
    assert not (bool(by_ref.loc["a[1].csv", "in_join"]) or bool(by_ref.loc["a[1].csv", "in_here"]))


def test_install_error_lines_of_a_package_with_a_dot_in_its_name() -> None:
    txt = (
        "ERROR: dependency 'x' is not available for package 'data.table'\n"
        "ERROR: dependency 'y' is not available for package 'dataXtable'"
    )
    assert (
        _repro_install_error_lines(txt, "data.table")
        == "ERROR: dependency 'x' is not available for package 'data.table'"
    )


# #594: lib.loc ---------------------------------------------------------------------------


def test_a_lib_loc_on_the_authors_computer_goes_and_a_relative_one_stays() -> None:
    txt, paths = _repro_drop_author_lib_loc(
        [
            'library(hydroGOF, lib.loc = "C:/Users/me/Documents/R/win-library/4.0")',
            "suppressPackageStartupMessages(require(sp, lib.loc='/home/me/R/x86_64'))",
            'requireNamespace("raster", lib.loc = "~/Rlibs", quietly = TRUE)',
            'library(mypkg, lib.loc = "lib")',
            '# library(y, lib.loc = "C:/z")',
            "library(dplyr)",
        ]
    )
    assert txt == [
        "library(hydroGOF)",
        "suppressPackageStartupMessages(require(sp))",
        'requireNamespace("raster", quietly = TRUE)',
        'library(mypkg, lib.loc = "lib")',
        '# library(y, lib.loc = "C:/z")',
        "library(dplyr)",
    ]
    assert paths == ["C:/Users/me/Documents/R/win-library/4.0", "/home/me/R/x86_64", "~/Rlibs"]


# #598: install addresses and the CRAN fallback -------------------------------------------


def test_no_install_address_comes_from_a_commented_line() -> None:
    code = [
        '# install.packages("embryogrowth")',
        '# install.packages("http://www.ese.u-psud.fr/epc/conservation/CRAN/embryogrowth.tar.gz", '
        'repos=NULL, type="source")',
        "#devtools::install_github('someone/HelpersMG')",
        "library(embryogrowth); library(HelpersMG)",
        'remotes::install_github("user/mypkg")  # this one is used',
        "library(mypkg)",
    ]
    d = repro_dependencies(code).set_index("package")
    assert d.loc["embryogrowth", "source"] == "cran"
    assert pd.isna(d.loc["embryogrowth", "ref"])
    assert d.loc["HelpersMG", "source"] == "cran"
    assert d.loc["mypkg", "source"] == "github"
    assert d.loc["mypkg", "ref"] == "user/mypkg"


def test_a_failed_download_says_so() -> None:
    f = _repro_download_failed_msg
    assert (
        f("http://a.org/p.tar.gz", ["x", "cannot open URL 'http://a.org/p.tar.gz'"])
        == "could not download http://a.org/p.tar.gz: cannot open URL 'http://a.org/p.tar.gz'"
    )
    assert (f("u/r", ["HTTP error 404.\n  Not Found"]) or "").startswith(
        "could not download u/r: HTTP error 404"
    )
    assert f("u/r", ["non-zero exit status", "compilation failed"]) is None


@pytest.mark.parametrize(
    ("fallback", "installed", "note", "message"),
    [
        (
            {"ok": True, "alt": "CRAN", "version_installed": "1.0"},
            True,
            "installed from CRAN: the install from http://x.invalid/praise.tar.gz failed "
            "(could not download",
            "",
        ),
        ({"ok": False, "alt": "CRAN"}, False, "", "installing it from CRAN failed too"),
        ({"ok": False, "alt": None}, False, "", "could not download"),
    ],
)
def test_a_package_whose_github_or_url_install_fails_is_tried_from_cran(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fallback: dict[str, Any],
    installed: bool,
    note: str,
    message: str,
) -> None:
    modes: list[str] = []

    def fake(mode: str, lib_dir: str, **values: Any) -> dict[str, Any]:
        modes.append(mode)
        if mode == "check_main":
            return {"ok": True, "skipped": False, "install_lib": lib_dir}
        if mode == "fallback":
            return fallback
        return {
            "ok": False,
            "msg": "",
            "warnings": ["cannot open URL 'http://x.invalid/praise.tar.gz'"],
            "install_lib": lib_dir,
        }

    monkeypatch.setattr(core, "_install_r", fake)
    deps = pd.DataFrame(
        {
            "package": ["praise"],
            "source": ["url"],
            "ref": ["http://x.invalid/praise.tar.gz"],
            "base": [False],
        }
    )
    out = repro_install_deps(deps, tmp_path / "lib")
    assert "fallback" in modes
    assert out["installed"].tolist() == [installed]
    assert note in out["version_note"].iloc[0]
    assert message in (out["message"].iloc[0] or "")


# #591: the folder a write needs ----------------------------------------------------------

Q1, Q2 = "\u2018", "\u2019"


@pytest.mark.parametrize(
    ("msg", "folders"),
    [
        (
            'Error in saveWorkbook(wb, paste0(wdoutputs, filename, ".xlsx")) :\n  cannot create '
            "file './outputs/Butterfly_GAM_Outputs.xlsx', reason 'No such file or directory'",
            ["./outputs"],
        ),
        (
            "Error in `ggsave()`:\n! Cannot find directory '/sandbox/SERDP/5_figs/final'.",
            ["/sandbox/SERDP/5_figs/final"],
        ),
        (
            'Error in gzfile(file, "wb") : cannot open the connection\nIn addition: Warning '
            f"message:\n  cannot open compressed file {Q1}results/m.rds{Q2}, probable reason "
            f"{Q1}No such file or directory{Q2}",
            ["results"],
        ),
        (
            'Error in png("figs/a.png") : unable to start png() device\nIn addition: Warning '
            "message:\ncannot open file 'figs/a.png': No such file or directory",
            ["figs"],
        ),
        (
            'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n    with '
            "open('out/res.txt', 'w') as f:\nFileNotFoundError: [Errno 2] No such file or "
            "directory: 'out/res.txt'",
            ["out"],
        ),
        (
            'Traceback (most recent call last):\n  File "a.py", line 9, in <module>\n    '
            "plt.savefig('figs/a.png')\nFileNotFoundError: [Errno 2] No such file or "
            "directory: 'figs/a.png'",
            ["figs"],
        ),
        # a file a script reads but does not find is not a folder to make
        (
            'Error in file(file, "rt") : cannot open the connection\nIn addition: Warning '
            "message:\ncannot open file 'data/x.csv': No such file or directory",
            [],
        ),
        (
            "Error in readChar(con, 5L, useBytes = TRUE) : cannot open the connection\nIn "
            "addition: Warning message:\ncannot open compressed file 'out/m.Rdata', probable "
            "reason 'No such file or directory'",
            [],
        ),
        (
            'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n    df = '
            "pd.read_csv('data/x.csv')\nFileNotFoundError: [Errno 2] No such file or directory: "
            "'data/x.csv'",
            [],
        ),
        (None, []),
    ],
)
def test_the_folder_a_write_stopped_on_not_a_reads(msg: str | None, folders: list[str]) -> None:
    assert _repro_write_failed_folders(msg) == folders


def test_a_failed_write_folder_is_made_in_the_sandbox_only_and_once(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "code").mkdir(parents=True)
    run_tbl = pd.DataFrame({"file_name": ["a.R", "b.R"], "run_dir": [str(root / "code")] * 2})
    res = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R"],
            "outcome": ["errored", "errored"],
            "error": [
                'Error in saveRDS(x, "../out/m.rds") : cannot open the connection\n'
                "cannot create file '../out/m.rds'",
                "Error in pdf(\"C:/Users/me/fig.pdf\") : cannot open file 'C:/Users/me/fig.pdf'",
            ],
            "stderr": ["", ""],
        }
    )
    made = _repro_make_failed_write_folders(res, run_tbl, root)
    assert made["script"].tolist() == ["a.R"]
    assert made["folder"].tolist() == ["out"]
    assert (root / "out").is_dir()
    (root / "out").rmdir()
    again = _repro_make_failed_write_folders(res, run_tbl, root, {"a.R": ["out"]})
    assert len(again) == 0
    assert "out" in _repro_folders_made_text(made)
    assert _repro_folders_made_text(again) == ""


# #615: the error in the table ------------------------------------------------------------


def test_the_error_message_is_each_failures_message_short() -> None:
    m = _repro_error_message
    out = (
        "Loading required package: x\nError in .Primitive(\":\")() : 0 arguments passed to ':' "
        "which requires 2\nCalls: source -> withVisible\nExecution halted"
    )
    assert m(["errored"], [out]) == [
        "Error in .Primitive(\":\")() : 0 arguments passed to ':' which requires 2"
    ]
    got = m(
        ["dependency_unavailable"],
        ["Error in library(lme4) : there is no package called 'lme4'\nExecution halted"],
    )
    assert "there is no package called 'lme4'" in got[0]
    # the address it asked for, when the error line names none
    assert m(
        ["needs_internet"],
        ["Error in curl::curl_fetch_memory(url) : Could not resolve host\nExecution halted"],
        ["trying URL 'https://example.org/data.csv'"],
    ) == [
        "Error in curl::curl_fetch_memory(url) : Could not resolve host (https://example.org/data.csv)"
    ]
    assert m(
        ["needs_internet"], ["Error in file(con) : cannot open URL 'https://a.org/x.csv'"]
    ) == ["Error in file(con) : cannot open URL 'https://a.org/x.csv'"]
    assert m(["timed_out"], ["timed out after 3600s"], ["...last output"]) == [
        "timed out after 3600s"
    ]
    py = (
        'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n    print(x)\n'
        "NameError: name 'x' is not defined"
    )
    assert m(["errored"], [py], python=True) == ["NameError: name 'x' is not defined"]
    assert m(["errored"], ["killed\nthe script stopped without a message"]) == [
        "the script stopped without a message"
    ]
    long = "Error in f() :" + " word" * 200
    assert len(m(["errored"], [long])[0] or "") <= 300
    none = m(
        ["ran_ok", "run_by_source", "defines_functions", "errored", None],
        ["x", "x", "x", "", "x"],
    )
    assert all(x is None for x in none)
    assert m([], []) == []


# #620: a file that could not be read ------------------------------------------------------


def test_the_read_failure_says_why_and_is_shortened() -> None:
    assert _repro_read_failed_msg([None, None]) == [None, None]
    msg = _repro_read_failed_msg([None, "cannot open URL 'https://x.org/a.R': HTTP status was 404"])
    assert msg[0] is None
    assert msg[1] == (
        "metacheck could not read the file: cannot open URL 'https://x.org/a.R': HTTP status "
        "was 404."
    )
    # pytacheck reads files with Python, so there is no R package that is installed but
    # cannot be loaded to name (D139): the message never has R's second part
    stringi = _repro_read_failed_msg(["stringi package required for encoding operations"])[0]
    assert stringi == (
        "metacheck could not read the file: stringi package required for encoding operations."
    )
    long = _repro_read_failed_msg(["a" * 1000])[0] or ""
    assert "a" * 299 + "\u2026" in long
    assert "a" * 300 not in long


def test_the_not_run_report_lists_files_not_read_and_empty_files() -> None:
    rep = _repro_not_run_report(
        ["a.R", "b.R", "c.R"],
        ["not_read", "empty", None],
        read_msg=["metacheck could not read the file: x.", None, None],
    )
    assert rep is not None
    txt = "\n".join(x if isinstance(x, str) else x.data.to_string() for x in rep)
    assert "1 file could not be read, so it was not run and needs to be checked again" in txt
    assert "metacheck could not read the file: x." in txt
    assert (
        "1 file is empty, so there is nothing to run, and it is not counted as a script: b.R."
        in txt
    )
    assert _repro_not_run_report(["a.R"], [None]) is None


# the clock time of the scripts -----------------------------------------------------------


def test_short_of_time_is_only_asked_of_a_clock_time() -> None:
    now = dt.datetime.now()
    assert not _repro_short_of_time(3600, 900)
    assert not _repro_short_of_time(None, 900)
    assert _repro_short_of_time(now + dt.timedelta(seconds=300), 900)
    assert not _repro_short_of_time(now + dt.timedelta(seconds=3000), 900)


def test_the_minimum_time_option_and_its_sentence() -> None:
    assert _repro_min_prep_time() == 900
    old = options({"metacheck.repro_min_time": 120})
    try:
        assert _repro_min_prep_time() == 120
    finally:
        options(old)
    assert _repro_no_prep_text(None) == ""
    assert "too little to copy the repository, install its packages and run its scripts" in (
        _repro_no_prep_text(300)
    )


def test_the_layout_is_not_copied_after_the_deadline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.csv").write_text("x\n1\n")
    structure = pd.DataFrame({"file_name": ["a.csv"], "file_location": [str(src / "a.csv")]})
    plan = pd.DataFrame({"file_name": ["a.csv"], "target_path": ["data/a.csv"]})
    root = tmp_path / "late"
    root.mkdir()
    res = repro_materialize_layout(plan, structure, root, deadline=time.time() - 1)
    assert not (root / "data" / "a.csv").exists()
    assert (root / "data").is_dir()  # the folder is still made
    assert [r["ok"] for r in res.attrs["materialised"].to_dict("records")] == [False]
    assert res.attrs["not_copied_time"] == 1
    root2 = tmp_path / "in_time"
    root2.mkdir()
    res2 = repro_materialize_layout(plan, structure, root2, deadline=time.time() + 600)
    assert (root2 / "data" / "a.csv").exists()
    assert [r["ok"] for r in res2.attrs["materialised"].to_dict("records")] == [True]
