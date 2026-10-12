"""Port of metacheck's tests/testthat/test-code_check.R."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.codecheck.core import (
    code_abs_path,
    code_extract_py,
    code_extract_r,
    code_file_refs,
    code_lang,
    code_library_lines,
    code_library_names,
    code_line_stats,
    code_packages,
    code_parse_r,
    code_read,
    code_remove_comments,
    code_setwd,
)

BS = "\\"


# ---------------------------------------------------------------------------
# code_read
# ---------------------------------------------------------------------------


def test_code_read(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        code_read(None)  # type: ignore[arg-type]

    obs = code_read(fixtures_dir / "code_files" / "analysis.R")
    assert obs[0] == "# Analysis script"
    assert obs[3] == ""  # blank lines are read

    obs = code_read(fixtures_dir / "code_files" / "stata_latin1.do")
    assert obs[0] == "* Stata do-file with Windows-1252 encoding"
    assert obs[1] == "* Author: Müller"

    obs = code_read(fixtures_dir / "code_files" / "stata_utf16.do")
    assert obs[0] == "* Stata do-file UTF-16 LE"
    assert obs[1] == "* Author: Mueller"

    # a genuinely empty (0-byte) file does not crash
    empty = tmp_path / "empty.R"
    empty.touch()
    assert code_read(empty) == []


# ---------------------------------------------------------------------------
# code_lang
# ---------------------------------------------------------------------------


def test_code_lang() -> None:
    assert code_lang(None) == []
    assert code_lang("file.R") == "R"
    names = ["file.Rmd", "file.SAS", "file.r", "file.qmd", "file.txt"]
    assert code_lang(names) == ["R", "SAS", "R", "R", None]
    assert code_lang([]) == []


def test_code_lang_python_and_notebook_kernels(fixtures_dir: Path) -> None:
    assert code_lang("analysis.py") == "Python"
    assert code_lang("ANALYSIS.PY") == "Python"
    assert code_lang(str(fixtures_dir / "notebooks" / "notebook_python.ipynb")) == "Python"
    assert code_lang(str(fixtures_dir / "notebooks" / "notebook_r.ipynb")) == "R"
    # an unreadable / not yet downloaded notebook falls back to Python
    assert code_lang("no_such_notebook.ipynb") == "Python"


# ---------------------------------------------------------------------------
# code_extract_r
# ---------------------------------------------------------------------------


def test_code_extract_r(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="file_path or text"):
        code_extract_r(None)

    file_path = pc.demofile("qmd")
    obs = code_extract_r(file_path)
    assert obs[0] == "library(metacheck)"
    assert code_extract_r(file_path, None)[0] == "library(metacheck)"

    save_path = tmp_path / "out.R"
    obs = code_extract_r(file_path, save_path)
    assert obs == str(save_path)
    assert save_path.read_text(encoding="utf-8").split("\n")[0] == "library(metacheck)"

    obs0 = code_extract_r(file_path, None, 0)
    obs1 = code_extract_r(file_path, None, 1)
    obs2 = code_extract_r(file_path, None, 2)
    in1 = set(obs1) - set(obs0)
    in2 = set(obs2) - set(obs1)
    assert "#| label: setup" in in1
    assert "#' ### Power Analysis" in in2

    text = ["---", "title: Demo", "format: html", "---", "```{r}", "a <- 1 + 1", "```"]
    assert code_extract_r(text=text) == ["a <- 1 + 1"]


# ---------------------------------------------------------------------------
# code_parse_r
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["python", None])
def test_code_parse_r(fixtures_dir: Path, engine: str | None) -> None:
    with pytest.raises(ValueError, match="file_path or text"):
        code_parse_r(engine=engine)

    pe = fixtures_dir / "parse-errors"
    for name, error in [("error.R", True), ("ok.R", False), ("error.Rmd", True), ("ok.Rmd", False)]:
        fp = str(pe / name)
        obs = code_parse_r(fp, engine=engine)
        assert obs["file_path"].tolist() == [fp]
        assert bool(obs["error"].iloc[0]) is error
        if error:
            assert "line:4:1" in obs["msg"].iloc[0]
        else:
            assert pd.isna(obs["msg"].iloc[0])

    obs = code_parse_r(text=code_read(pe / "ok.R"), engine=engine)
    assert obs["file_path"].tolist() == [""]
    assert not obs["error"].iloc[0]
    assert pd.isna(obs["msg"].iloc[0])

    obs = code_parse_r(text=code_read(pe / "error.Rmd"), engine=engine)
    assert obs["file_path"].tolist() == [""]
    assert obs["error"].iloc[0]
    assert "line:4:1" in obs["msg"].iloc[0]

    files = sorted(str(p) for p in pe.iterdir())
    obs = code_parse_r(files, engine=engine)
    assert obs["file_path"].tolist() == files
    assert obs["error"].tolist() == [True] * 4 + [False] * 4
    assert "unexpected symbol" in obs["msg"].iloc[0]


# ---------------------------------------------------------------------------
# code_abs_path
# ---------------------------------------------------------------------------


def _paths(df: pd.DataFrame) -> list[tuple[str, int]]:
    return list(zip(df["abs_path"].tolist(), df["line"].tolist(), strict=True))


def test_code_abs_path() -> None:
    with pytest.raises(TypeError):
        code_abs_path(None)  # type: ignore[arg-type]

    obs = code_abs_path(
        [
            "# the abs path is C:/User/lakens/file.R",
            "func(file = 'https://lakens.com/file.R')",
            'file <- "lakens/file.R"',
        ]
    )
    assert list(obs.columns) == ["abs_path", "line"]
    assert len(obs) == 0

    assert _paths(code_abs_path('file <- "C:/User/lakens/file.R"')) == [
        ("C:/User/lakens/file.R", 1)
    ]
    assert _paths(code_abs_path('file <- "C:\\User\\lakens\\file.R"')) == [
        ("C:\\User\\lakens\\file.R", 1)
    ]
    assert _paths(code_abs_path("file <- 'C:/User/lakens/file.R'")) == [
        ("C:/User/lakens/file.R", 1)
    ]
    assert _paths(code_abs_path('file <- "/User/lakens/file.R"')) == [("/User/lakens/file.R", 1)]
    assert _paths(code_abs_path("file <- '~/file.R'")) == [("~/file.R", 1)]
    assert len(code_abs_path("file <- 'https://scienceverse.org/file.R'")) == 0

    obs = code_abs_path(
        [
            "file <- 'C:/User/lakens/file.R'",
            "a <- 1 + 1",
            "convert(file, '/User/lakens/file.html')",
        ]
    )
    assert _paths(obs) == [("C:/User/lakens/file.R", 1), ("/User/lakens/file.html", 3)]

    obs = code_abs_path('x <- read_csv("/plots/x.csv"), units="in", extra = FALSE)')
    assert obs["abs_path"].tolist() == ["/plots/x.csv"]

    # regex escapes are not UNC paths
    regex_code = (
        f'x <- gsub("{BS}d+{BS}.{BS}d*", "", y)\n'
        f'z <- gsub("{BS}1-{BS}2", "", z)\n'
        f'a <- strsplit(v, "{BS}-A[MF12]+")'
    )
    assert len(code_abs_path(regex_code)) == 0

    # a genuine UNC path is flagged
    obs = code_abs_path(f'read.csv("{BS}{BS}fileserver{BS}share{BS}data.csv")')
    assert len(obs) == 1
    assert obs["abs_path"].iloc[0].startswith(f"{BS}{BS}fileserver")

    # an all-digit "host" is a backreference string
    backref = f'sub("group ({BS}d+)_({BS}d+)", "{BS}2{BS}1", colnames(clupo))'
    assert len(code_abs_path(backref)) == 0

    # a single-segment string starting with "/" is a route, not a path
    assert len(code_abs_path("self.fetch('/login')")) == 0


def test_code_abs_path_empty_inputs() -> None:
    # no code has the usual columns (R: character(0) gives (line, abs_path), U67)
    assert list(code_abs_path("").columns) == ["abs_path", "line"]
    assert list(code_abs_path([]).columns) == ["abs_path", "line"]
    assert len(code_abs_path([])) == 0


# ---------------------------------------------------------------------------
# code_setwd
# ---------------------------------------------------------------------------


def test_code_setwd() -> None:
    obs = code_setwd(["setwd('D:/Dropbox/project')", "x <- read.csv('data.csv')"])
    assert obs["setwd_call"].tolist() == ["setwd('D:/Dropbox/project')"]
    assert obs["line"].tolist() == [1]

    for call in ["setwd(dir)", "setwd(tempdir())", "setwd(getwd())"]:
        assert len(code_setwd(call)) == 1

    assert len(code_setwd('message("Please run setwd(your_path) before continuing")')) == 0

    obs = code_setwd('x <- "some/path.csv"; setwd("D:/Dropbox/project")')
    assert len(obs) == 1
    assert obs["setwd_call"].iloc[0].startswith("setwd")

    assert len(code_setwd("x <- 1")) == 0


# ---------------------------------------------------------------------------
# code_remove_comments
# ---------------------------------------------------------------------------


def test_code_remove_comments() -> None:
    with pytest.raises(TypeError):
        code_remove_comments(None)  # type: ignore[arg-type]

    code_text = [
        "# this is a comment",
        "  # and a comment with whitespace",
        "",
        "x <- 'And this is code'",
    ]
    assert code_remove_comments(code_text, "R") == ["x <- 'And this is code'"]

    spss = [
        "COMMENT This is an inline comment using COMMENT.",
        "",
        "* This is a single-line comment using *.",
        "",
        "COMMENT BEGIN",
        "  This is a block comment.",
        "  It can span multiple lines.",
        "COMMENT END.",
        "GET FILE='COMMENT.sav'.",
        " /* This is another block comment",
        "    using slash-star notation. */",
        "",
        "DESCRIPTIVES VARIABLES=age income.",
    ]
    assert code_remove_comments(spss, "SPSS") == [
        "GET FILE='COMMENT.sav'.",
        "DESCRIPTIVES VARIABLES=age income.",
    ]

    sas = [
        "* This is a single-line comment using *;",
        "",
        "data example;",
        "  set mylib.mydata; * Inline comment after code;",
        "run;",
        "",
        "/* This is a block comment",
        "   that spans multiple lines. */",
        "",
        "proc means data=example;",
        "  var age income;",
        "run;",
    ]
    assert code_remove_comments(sas, "SAS") == [
        "data example;",
        "  set mylib.mydata; * Inline comment after code;",
        "run;",
        "proc means data=example;",
        "  var age income;",
        "run;",
    ]

    stata = [
        "* This is a full-line comment using *.",
        "",
        'display "Hello world"  // This is an inline comment using //',
        "",
        "/* This is a block comment",
        "   that spans multiple lines. */",
        "",
        "use example.dta, clear",
        "summarize age income",
    ]
    assert code_remove_comments(stata, "Stata") == [
        'display "Hello world"  ',
        "use example.dta, clear",
        "summarize age income",
    ]


def test_code_remove_comments_trailing_r() -> None:
    code_text = [
        "x <- 1 # a trailing comment",
        "y <- read.csv('a#b.csv')",
        "z <- 'no # here either'",
        "# a whole-line comment",
        "w <- 2",
    ]
    assert code_remove_comments(code_text, "R") == [
        "x <- 1 ",
        "y <- read.csv('a#b.csv')",
        "z <- 'no # here either'",
        "w <- 2",
    ]
    nc = code_remove_comments(["# see C:/Users/example/data.csv for details", "x <- 1"], "R")
    assert len(code_abs_path(nc)) == 0


def test_code_remove_comments_python() -> None:
    code_text = ["# whole-line comment", "", "import os  # trailing comment", "x = 1"]
    assert code_remove_comments(code_text, "Python") == ["import os  ", "x = 1"]
    assert code_remove_comments("df = pd.read_csv('a#b.csv')", "Python") == [
        "df = pd.read_csv('a#b.csv')"
    ]
    assert code_remove_comments("u = 'http://x.org/#frag'  # note", "Python") == [
        "u = 'http://x.org/#frag'  "
    ]
    assert code_remove_comments(['"""doc"""', "y = 2"], "Python") == ['"""doc"""', "y = 2"]
    assert code_remove_comments([], "Python") == []


def test_code_remove_comments_markers_inside_strings() -> None:
    stata = [
        'import delimited using "https://example.org//files/x.csv"',
        'use "C://project//data//w1.dta"',
        "summarize x  // a real comment",
    ]
    nc = code_remove_comments(stata, "Stata")
    assert nc == [
        'import delimited using "https://example.org//files/x.csv"',
        'use "C://project//data//w1.dta"',
        "summarize x  ",
    ]
    assert code_file_refs(nc, "Stata") == [
        "https://example.org//files/x.csv",
        "C://project//data//w1.dta",
    ]

    matlab = ["T = readtable('data/a%20b.csv');", "x = 1;  % real comment"]
    nc = code_remove_comments(matlab, "MATLAB")
    assert nc == ["T = readtable('data/a%20b.csv');", "x = 1;  "]
    assert code_file_refs(nc, "MATLAB") == ["data/a%20b.csv"]


# ---------------------------------------------------------------------------
# code_line_stats
# ---------------------------------------------------------------------------


def test_code_line_stats() -> None:
    with pytest.raises(TypeError):
        code_line_stats(None)  # type: ignore[arg-type]

    code_text = [
        "a <- 1 # inline comment",
        "",
        "",
        "",
        "# comment",
        "   # space before comment",
    ]
    assert code_line_stats(code_text, "R") == {
        "total_lines": 3,
        "comment_lines": 3,
        "code_lines": 1,
        "percent_comments": 1.0,
        "has_docstring": None,
    }

    spss = [
        "COMMENT This is an inline comment using COMMENT.",
        "",
        "* This is a single-line comment using *.",
        "",
        "COMMENT BEGIN",
        "  This is a block comment.",
        "  It can span multiple lines.",
        "COMMENT END.",
        "GET FILE='COMMENT.sav'.",
        " /* This is another block comment",
        "    using slash-star notation. */",
        "",
        "DESCRIPTIVES VARIABLES=age income.",
    ]
    assert code_line_stats(spss, "SPSS") == {
        "total_lines": 10,
        "comment_lines": 8,
        "code_lines": 2,
        "percent_comments": 0.8,
        "has_docstring": None,
    }


def test_code_line_stats_mixed_code_and_comment_lines() -> None:
    code_text = [
        "model1_Pos <- lme(Pos ~ scale_ini_age,# Level 2: scale_ini_age;",
        "                   random=~1|id, data=dat)  #random intercept=id",
        "model2_Pos <- lme(Pos ~ scale_ini_age*covid,#Level 1: covid condition;",
        "                   random=~1+covid|id, data=dat)  #random slope=covid",
    ]
    obs = code_line_stats(code_text, "R")
    assert (obs["total_lines"], obs["code_lines"], obs["comment_lines"]) == (4, 4, 4)
    assert obs["percent_comments"] == 1

    synthetic = ["# a whole-line comment"] * 19 + [
        "x <- fit_model(y ~ z, data = dat) # a trailing comment"
    ] * 32
    obs2 = code_line_stats(synthetic, "R")
    assert obs2["comment_lines"] == 51
    assert obs2["code_lines"] == 32


def test_code_line_stats_python() -> None:
    obs = code_line_stats(["# comment", "", "x = 1", "y = 2  # trailing"], "Python")
    assert (obs["total_lines"], obs["code_lines"], obs["comment_lines"]) == (3, 2, 2)
    r_obs = code_line_stats(["# comment", "", "x <- 1", "y <- 2  # trailing"], "R")
    assert obs["code_lines"] == r_obs["code_lines"]
    assert obs["comment_lines"] == r_obs["comment_lines"]
    assert r_obs["has_docstring"] is None
    assert obs["has_docstring"] is False

    ds = code_line_stats(['"""Module docstring."""', "x = 1"], "Python")
    assert ds["has_docstring"] is True
    assert ds["percent_comments"] == 0


def test_code_line_stats_empty() -> None:
    obs = code_line_stats("")
    assert obs["total_lines"] == 0
    assert math.isnan(obs["percent_comments"])
    # no code counts like "" (R: "non-character argument", U67)
    for lang in ("R", "Python"):
        obs = code_line_stats([], lang)
        assert obs["total_lines"] == obs["comment_lines"] == obs["code_lines"] == 0
        assert math.isnan(obs["percent_comments"])
    assert code_line_stats([], "Python")["has_docstring"] is False


# ---------------------------------------------------------------------------
# code_file_refs
# ---------------------------------------------------------------------------


def test_code_file_refs() -> None:
    with pytest.raises(TypeError):
        code_file_refs(None)  # type: ignore[arg-type]

    code_text = [
        'source("functions.R")',
        'a <- "bread"; a1 <- "file0.csv"',
        'b <- read.csv("file.csv")',
        '# b <- read.csv("old_file.csv")',
        'b2 <- readr::read_csv("subdir/file.csv")',
        'b3 <- read_csv("file2.csv", arg = "file3")',
    ]
    assert code_file_refs(code_text, "R") == [
        "functions.R",
        "file.csv",
        "subdir/file.csv",
        "file2.csv",
    ]

    spss = [
        "* --- Load native SPSS file ---",
        "GET FILE='data/example.sav'.",
        "",
        "* --- Load portable SPSS file ---",
        "IMPORT FILE='data/example.por'.",
        "",
        "* --- Load Excel (xls/xlsx) ---",
        "GET DATA",
        "  /TYPE=XLSX",
        "  /FILE='data/example.xlsx'",
        "  /SHEET=name 'Sheet1'",
        "  /CELLRANGE=full",
        "  /READNAMES=on.",
    ]
    assert code_file_refs(spss, "SPSS") == [
        "data/example.sav",
        "data/example.por",
        "data/example.xlsx",
    ]

    spss2 = [
        "* --- Load CSV / delimited text ---",
        "GET DATA",
        "  /TYPE=TXT",
        "  /FILE='data/example.csv'",
        "  /DELCASE=LINE",
        '  /DELIMITERS=","',
        "  /ARRANGEMENT=DELIMITED",
        "  /FIRSTCASE=2",
        "  /VARIABLES=",
        "    id F8.0",
        "    age F8.0",
        "    income F8.2.",
        "",
        "* --- Load tab-delimited file ---",
        "GET DATA",
        "  /TYPE=TXT",
        "  /FILE='data/example.tsv'",
        '  /DELIMITERS="\\t"',
        "  /ARRANGEMENT=DELIMITED",
        "  /FIRSTCASE=2.",
        "",
        "* --- Load fixed-width text file ---",
        "DATA LIST FILE='data/fixed.txt'",
        "  /id 1-4",
        "   age 5-6",
        "   income 7-12.",
        "",
        "* --- Load using ODBC (database) ---",
        "GET DATA",
        "  /TYPE=ODBC",
        "  /CONNECT='DSN=mydb;UID=user;PWD=pass;'",
        "  /SQL='SELECT * FROM mytable'.",
        "",
        "* --- Load SAS file ---",
        "GET SAS DATA='data/example.sas7bdat'.",
        "",
        "* --- Load Stata file ---",
        "GET STATA FILE='data/example.dta'.",
        "",
        "* --- Load data via FILE HANDLE ---",
        "FILE HANDLE myfile /NAME='data/example.txt'.",
        "GET DATA",
        "  /TYPE=TXT",
        "  /FILE=myfile",
        '  /DELIMITERS=",".',
        "",
        "* --- Inline data (not external) ---",
        "DATA LIST LIST /x y.",
        "BEGIN DATA",
        "1 2",
        "3 4",
        "END DATA.",
    ]
    assert code_file_refs(spss2, "SPSS") == [
        "data/example.csv",
        "data/example.tsv",
        "data/fixed.txt",
        "data/example.sas7bdat",
        "data/example.dta",
        "data/example.txt",
    ]


def test_code_file_refs_full() -> None:
    code_text = [
        "x = read.csv('file1.txt')",
        "x=read.csv2('file2.txt')",
        "x <- read.table('file3.txt')",
        "x<-read.delim('file4.txt')",
        "x<-read.delim2('file5.txt')",
        "  x   <-   readRDS('file6.txt')",
        "x <- 'file7.txt' |> load()",
        "readLines('file8.txt', n = 2)",
        "readr::read_csv('file9.txt') -> x",
        "read_csv2 ('file10.txt')",
        "read_tsv  ('file11.txt')",
        "read_delim('file12.txt')",
        "read_rds('file13.txt')",
        "read_lines('file14.txt')",
        "readLines('file15.txt')",
        "fread('file16.txt')",
        "read_xlsx('file17.txt')",
        "read_xls('file18.txt')",
        "read_excel('file19.txt')",
        "read_xlsx('file20.txt')",
        "read_dta('file21.txt')",
        "read_sav('file22.txt')",
        "read_sas('file23.txt')",
        "read.dta('file24.txt')",
        "read_feather('file25.txt')",
        "read_parquet('file26.txt')",
        "fromJSON('file27.txt')",
        "read_yaml('file28.txt')",
        "read_xml('file29.txt')",
        "read_ods('file30.txt')",
        "readtext('file31.txt')",
        "source('file32.txt')",
    ]
    assert code_file_refs(code_text, "R") == [f"file{i}.txt" for i in range(1, 33)]

    code_text_no = [
        "I can read CSV files",
        "read.CSV()",
        "read.csv is a good function",
        "get that from JSON (if you can)",
    ]
    assert code_file_refs(code_text_no, "R") == []


def test_code_file_refs_rio_import() -> None:
    code_text = [
        "d1 <- import('file1.csv')",
        "d2 <- rio::import('file2.ods')",
        "d3 <- import('file3.xlsx')",
        "d4 <- import('file4.sav')",
        "d5 <- import_list('file5.xlsx')",
    ]
    assert code_file_refs(code_text, "R") == [
        "file1.csv",
        "file2.ods",
        "file3.xlsx",
        "file4.sav",
        "file5.xlsx",
    ]
    code_text_no = [
        "np <- reticulate::import('numpy')",
        "os <- import('os')",
        "x <- import(pkg)",
        "importance <- varImp(fit)",
        "imported_data <- 3",
    ]
    assert code_file_refs(code_text_no, "R") == []
    assert code_file_refs("export(d, 'out.csv')", "R") == []
    assert code_file_refs("export(d, 'out.csv')", "R", include_writes=True) == ["out.csv"]


def test_code_file_refs_python() -> None:
    code_text = [
        "df = pd.read_csv('data/trials.csv')",
        "arr = np.loadtxt('vals.txt')",
        "m = scipy.io.loadmat('subj01.mat')",
        "with open('notes.txt') as f: pass",
        "d2 = pd.read_excel('sheets/x.xlsx')",
    ]
    assert set(code_file_refs(code_text, "Python")) == {
        "data/trials.csv",
        "vals.txt",
        "subj01.mat",
        "notes.txt",
        "sheets/x.xlsx",
    }
    assert code_file_refs(["import numpy", "from a.b import c"], "Python") == []


# ---------------------------------------------------------------------------
# code_library_lines / code_library_names / code_packages
# ---------------------------------------------------------------------------


def test_code_library_lines() -> None:
    with pytest.raises(TypeError):
        code_library_lines(None)  # type: ignore[arg-type]

    code_text = [
        "line = 1",
        "library(dplyr)",
        "",
        "# this line won't count",
        'library("tidyr")',
        "line = 5",
        "renv::install('metacheck')",
    ]
    obs = code_library_lines(code_text, "R")
    assert list(obs.columns) == ["code", "line"]
    assert obs["code"].tolist() == [code_text[1], code_text[4], code_text[6]]
    assert obs["line"].tolist() == [2, 3, 5]


def test_code_library_lines_python() -> None:
    obs = code_library_lines(
        ["import os", "from scipy import stats", "x = 1", "import pandas"], "Python"
    )
    assert obs["line"].tolist() == [1, 2, 4]
    assert len(code_library_lines("df.import_csv('a.csv')", "Python")) == 0
    obs2 = code_library_lines(["import sys, re", "import numpy as np"], "Python")
    assert obs2["line"].tolist() == [1, 2]
    docstring_code = [
        '"""',
        "Some docstring text",
        "        from the generated model and api descriptions.",
        '"""',
        "import os",
        "import sys",
    ]
    assert code_library_lines(docstring_code, "Python")["line"].tolist() == [5, 6]


def test_code_library_names_r() -> None:
    code_text = [
        "library(dplyr)",
        "require('tidyr')",
        'requireNamespace("purrr")',
        "pacman::p_load(ggplot2, readr)",
        "x <- stringr::str_trim(' a ')",
        "renv::install('metacheck')",
        "install.packages(c('a', 'b'))",
        "# library(commented)",
    ]
    obs = code_library_names(code_text, "R")
    pairs = {f"{p} {s}" for p, s in zip(obs["package"], obs["source"], strict=True)}
    assert pairs == {
        "dplyr library",
        "tidyr require",
        "purrr requireNamespace",
        "ggplot2 p_load",
        "readr p_load",
        "pacman namespace",
        "stringr namespace",
        "metacheck install",
        "renv namespace",
        "a install",
        "b install",
    }
    assert "commented" not in obs["package"].tolist()
    assert list(obs.columns) == ["package", "source", "line"]


def test_code_library_names_chained_library_calls() -> None:
    obs = code_library_names("library(a); library(b); library(c)", "R")
    assert obs["package"].tolist() == ["a", "b", "c"]
    assert obs["line"].tolist() == [1, 1, 1]


def test_code_library_names_python() -> None:
    code_text = [
        "import numpy",
        "import pandas as pd",
        "import os, sys",
        "from sklearn.linear_model import LinearRegression",
        "from . import local",
        "import matplotlib  # inline comment",
    ]
    obs = code_library_names(code_text, "Python")
    assert set(obs["package"]) == {"numpy", "pandas", "os", "sys", "sklearn", "matplotlib"}
    assert set(obs["source"]) == {"import"}


def _pkgs(code: list[str]) -> list[str]:
    return sorted(set(code_library_names(code, "R")["package"]))


def test_code_library_names_does_not_report_a_package_list_variable() -> None:
    # R (issue #421): a paper's own install boilerplate loads its packages
    # through a variable, and the variable's name was reported, installed and
    # listed as a failed dependency.
    # the issue's example: the loop variable resolves to the literal list,
    # which may span several lines
    assert _pkgs(
        [
            'list.packages <- c("activity", "bbmle",',
            '                   "Distance")',
            "for (req.lib in list.packages) {",
            "  if (!require(req.lib, character.only = TRUE)) {",
            "    install.packages(req.lib)",
            "    library(req.lib, character.only = TRUE)",
            "  }",
            "}",
        ]
    ) == ["Distance", "activity", "bbmle"]

    # the common new.packages idiom: indexing a list variable resolves to it;
    # a derived variable (new.packages) cannot be resolved and is dropped
    assert _pkgs(
        [
            'list.of.packages <- c("ggplot2", "Rcpp")',
            'new.packages <- list.of.packages[!(list.of.packages %in% installed.packages()[,"Package"])]',
            "if (length(new.packages)) install.packages(new.packages)",
            "install.packages(list.of.packages[!list.of.packages %in% rownames(installed.packages())])",
            "lapply(list.of.packages, require, character.only = TRUE)",
        ]
    ) == ["Rcpp", "ggplot2"]

    # variables with no literal value in the file are dropped, never reported
    for v in ["x", "pkg", "packages.toinstall", "not_installed", "dependencies", "arma"]:
        assert (
            _pkgs(
                [
                    f"install.packages({v})",
                    f"library({v}, character.only = TRUE)",
                    f"requireNamespace({v}, quietly = TRUE)",
                ]
            )
            == []
        ), v
    # a vector that is not purely literal is not resolved either
    assert _pkgs(['pk <- c("zoo", other)', "install.packages(pk)"]) == []

    # sapply(FUN = ...) and pacman's char = form
    assert _pkgs(
        ['pkgs = c("psych", "car")', "sapply(pkgs, FUN = library, character.only = TRUE)"]
    ) == [
        "car",
        "psych",
    ]
    assert _pkgs(['p <- c("lme4", "car")', "pacman::p_load(char = p)"]) == [
        "car",
        "lme4",
        "pacman",
    ]

    # literal names keep working, including with further arguments
    assert _pkgs(
        [
            'library(dplyr); library("tidyr")',
            'install.packages("afex", dependencies = TRUE)',
            'BiocManager::install("edgeR")',
        ]
    ) == ["BiocManager", "afex", "dplyr", "edgeR", "tidyr"]


def test_code_char_vector_vars() -> None:
    from metacheck.codecheck.core import _code_char_vector_vars

    code = [
        'a <- c("x", "y")',
        "b = 'z'",
        'd <<- c("p",',
        '         "q")',
        'e <- c("k", other)',
        'f <- g("k")',
        "for (v in a) print(v)",
        "for (w in unknown) print(w)",
        'a <- c("late")',
    ]
    assert _code_char_vector_vars(code) == {
        "a": ["late"],
        "b": ["z"],
        "d": ["p", "q"],
        "v": ["late"],  # loops see the last assignment: all assignments are read first
    }
    assert _code_char_vector_vars([]) == {}


def test_empty_code_is_handled_like_an_empty_string() -> None:
    # R (issue #425): an empty script (such as a Python package's __init__.py)
    # gave "Unknown or uninitialised column" warnings, and code_parse_r() and
    # code_line_stats() stopped with an error.
    from metacheck.codecheck.core import _code_has_docstring, code_install_packages

    x: list[str] = []
    ap = code_abs_path(x)
    assert len(ap) == 0
    assert list(ap.columns) == ["abs_path", "line"]
    assert len(code_setwd(x)) == 0
    assert len(code_install_packages(x)) == 0
    stats = code_line_stats(x, "Python")
    assert stats["total_lines"] == 0
    assert math.isnan(stats["percent_comments"])
    assert stats["has_docstring"] is False
    assert _code_has_docstring(x) is False
    assert code_remove_comments(x, "R") == []
    assert not code_parse_r(text=x)["error"].iloc[0]


def test_code_library_names_other_languages_empty() -> None:
    for lang in ["SPSS", "SAS", "Stata"]:
        obs = code_library_names("anything", lang)
        assert list(obs.columns) == ["package", "source", "line"]
        assert len(obs) == 0
    obs = code_library_names(["a <- 1", "b <- 2"], "R")
    assert list(obs.columns) == ["package", "source", "line"]
    assert len(obs) == 0


def test_code_packages() -> None:
    assert code_packages(["dplyr, ggplot2", "", None, "dplyr, tidyr"]) == [
        "dplyr",
        "ggplot2",
        "tidyr",
    ]
    tbl = pd.DataFrame({"packages": ["readr, dplyr", ""]})
    assert code_packages(tbl) == ["dplyr", "readr"]
    assert code_packages([]) == []
    assert code_packages(["", None]) == []


# ---------------------------------------------------------------------------
# code_extract_py
# ---------------------------------------------------------------------------


def test_code_extract_py(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="file_path or text"):
        code_extract_py(None)

    fp = fixtures_dir / "notebooks" / "notebook_python.ipynb"
    obs = code_extract_py(fp)
    assert not any(s.startswith("# Analysis") for s in obs)
    assert not any(s.startswith("%matplotlib") for s in obs)
    assert not any(s.startswith("!pip") for s in obs)
    assert "import pandas as pd" in obs
    assert "df = pd.read_csv('data/trials.csv')" in obs
    assert "x = 1  # never run" in obs

    save_path = tmp_path / "out.py"
    assert code_extract_py(fp, save_path) == str(save_path)
    assert save_path.exists()

    r_src = code_extract_py(fixtures_dir / "notebooks" / "notebook_r.ipynb")
    assert "library(dplyr)" in r_src
    assert set(code_library_names(r_src, "R")["package"]) == {"dplyr", "ggplot2"}

    bad = tmp_path / "bad.ipynb"
    bad.write_text('{"foo": 1}\n', encoding="utf-8")
    assert code_extract_py(bad) == []
