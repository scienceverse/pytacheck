"""Four fixes from metacheck's BES run and the error message column (metacheck 0.4.3.12-0.4.3.21).

Port of the tests added to ``test-reproducibility_check.R`` and
``test-module-reproducibility_check.R``: #594 (``lib.loc``), #598 (install addresses, the
fallback to CRAN), #591 (folders a write needs), #615 (``error_message``) and #597 (file
names with regular-expression characters).
"""

from __future__ import annotations

import pandas as pd

from metacheck.repro.core import (
    _repro_drop_author_lib_loc,
    repro_dependencies,
    repro_rewrite_paths,
)
from metacheck.repro.layout import _repro_make_failed_write_folders, _repro_write_failed_folders
from metacheck.repro.outcomes import _repro_error_message


def test_drop_author_lib_loc_removes_a_lib_loc_on_the_authors_computer_only() -> None:
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


def test_repro_dependencies_takes_no_install_address_from_a_commented_line() -> None:
    # 10.1111/1365-2435.13410: the address no longer answers; embryogrowth is on CRAN
    code = [
        '# install.packages("embryogrowth")',
        '# install.packages("http://www.ese.u-psud.fr/epc/conservation/CRAN/embryogrowth.tar.gz", repos=NULL, type="source")',
        "#devtools::install_github('someone/HelpersMG')",
        "library(embryogrowth); library(HelpersMG)",
        'remotes::install_github("user/mypkg")  # this one is used',
        "library(mypkg)",
    ]
    d = repro_dependencies(code)
    row = d.set_index("package")
    assert row.loc["embryogrowth", "source"] == "cran"
    assert pd.isna(row.loc["embryogrowth", "ref"])
    assert row.loc["HelpersMG", "source"] == "cran"
    assert row.loc["mypkg", "source"] == "github"
    assert row.loc["mypkg", "ref"] == "user/mypkg"


def test_write_failed_folders_finds_the_folder_a_write_stopped_on_not_a_reads() -> None:
    f = _repro_write_failed_folders
    q1, q2 = "\u2018", "\u2019"
    assert f(
        'Error in saveWorkbook(wb, paste0(wdoutputs, filename, ".xlsx")) :\n'
        "  cannot create file './outputs/Butterfly_GAM_Outputs.xlsx', reason 'No such file or directory'"
    ) == ["./outputs"]
    assert f("Error in `ggsave()`:\n! Cannot find directory '/sandbox/SERDP/5_figs/final'.") == [
        "/sandbox/SERDP/5_figs/final"
    ]
    assert f(
        'Error in gzfile(file, "wb") : cannot open the connection\nIn addition: Warning message:\n'
        f"  cannot open compressed file {q1}results/m.rds{q2}, probable reason {q1}No such file or directory{q2}"
    ) == ["results"]
    assert f(
        'Error in png("figs/a.png") : unable to start png() device\nIn addition: Warning message:\n'
        "cannot open file 'figs/a.png': No such file or directory"
    ) == ["figs"]
    assert f(
        'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n'
        "    with open('out/res.txt', 'w') as f:\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'out/res.txt'"
    ) == ["out"]
    assert f(
        'Traceback (most recent call last):\n  File "a.py", line 9, in <module>\n'
        "    plt.savefig('figs/a.png')\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'figs/a.png'"
    ) == ["figs"]
    # a file a script reads but does not find is not a folder to make
    assert (
        f(
            'Error in file(file, "rt") : cannot open the connection\nIn addition: Warning message:\n'
            "cannot open file 'data/x.csv': No such file or directory"
        )
        == []
    )
    assert (
        f(
            "Error in readChar(con, 5L, useBytes = TRUE) : cannot open the connection\n"
            "In addition: Warning message:\n"
            "cannot open compressed file 'out/m.Rdata', probable reason 'No such file or directory'"
        )
        == []
    )
    assert (
        f(
            'Traceback (most recent call last):\n  File "a.py", line 3, in <module>\n'
            "    df = pd.read_csv('data/x.csv')\n"
            "FileNotFoundError: [Errno 2] No such file or directory: 'data/x.csv'"
        )
        == []
    )
    assert f(None) == []


def test_make_failed_write_folders_makes_the_folder_in_the_sandbox_only_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path
    (root / "code").mkdir()
    run_tbl = pd.DataFrame({"file_name": ["a.R", "b.R"], "run_dir": [str(root / "code")] * 2})
    res = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R"],
            "outcome": "errored",
            "error": [
                'Error in saveRDS(x, "../out/m.rds") : cannot open the connection\n'
                "cannot create file '../out/m.rds'",
                "Error in pdf(\"C:/Users/me/fig.pdf\") : cannot open file 'C:/Users/me/fig.pdf'",
            ],
            "stderr": "",
        }
    )
    made = _repro_make_failed_write_folders(res, run_tbl, root)
    assert list(made["script"]) == ["a.R"]
    assert list(made["folder"]) == ["out"]
    assert (root / "out").is_dir()
    # not again
    (root / "out").rmdir()
    again = _repro_make_failed_write_folders(res, run_tbl, root, {"a.R": ["out"]})
    assert len(again) == 0


def test_error_message_gives_each_failures_message_short() -> None:
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
    )[0]
    assert got is not None and "there is no package called 'lme4'" in got
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
    got = m(["errored"], [long])[0]
    assert got is not None and len(got) <= 300
    # none for what ran through, or when there is no message
    assert (
        m(
            ["ran_ok", "run_by_source", "defines_functions", "errored", None],
            ["x", "x", "x", "", "x"],
        )
        == [None] * 5
    )
    assert m([], []) == []


def test_rewrite_paths_takes_file_names_with_regex_characters_as_written() -> None:
    # a Windows path with "\P" (10.1111/2041-210X.13928) or "\6" (jskhb_v1) made an invalid
    # pattern and stopped reproducibility_check for the paper in R; Python escapes the name
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
        r'r <- read.csv(file.path(dir, "results(1).csv"))',
        r'a <- read.csv("a[1].csv")',
        r'x <- read.csv(here("data+x.csv"))',
    ]
    out = repro_rewrite_paths(code, "ex1/analysis.R", plan)
    refs = [str(r).replace("\\", "/").rsplit("/", 1)[-1] for r in out["ref"]]
    assert set(refs) == set(plan["file_name"])
    assert out["matched"].all()
    by = out.set_index("ref")
    assert bool(by.loc["results(1).csv", "in_join"]) is True
    assert bool(by.loc["data+x.csv", "in_here"]) is True
    # "a[1].csv" is not taken as the pattern "a1.csv" or as "a.csv"
    assert not (bool(by.loc["a[1].csv", "in_join"]) or bool(by.loc["a[1].csv", "in_here"]))
