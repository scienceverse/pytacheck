"""code_check synchronised with metacheck 0.4.3.28 (tests/testthat/test-code_check.R)."""

from __future__ import annotations

from pathlib import Path

from metacheck.codecheck.core import (
    _code_blank_mplus_savedata,
    _code_blank_write_opens,
    code_file_refs,
    code_read,
)

FIXTURES = Path(__file__).parents[2] / "upstream/metacheck/tests/testthat/fixtures/code_files"


def test_code_read_utf8_and_latin1(tmp_path: Path) -> None:
    # ASCII and UTF-8 are read as they are, bytes that are not UTF-8 as a Western encoding
    assert code_read(FIXTURES / "analysis.R")[0] == "# Analysis script"
    utf8 = tmp_path / "utf8.R"
    utf8.write_bytes("# Müller\nx <- 1\n".encode())
    assert code_read(utf8) == ["# Müller", "x <- 1"]
    assert code_read(FIXTURES / "stata_latin1.do")[1] == "* Author: Müller"


def test_code_file_refs_leaves_out_files_the_code_writes() -> None:
    # open() in a mode that creates the file is an output, not a missing input;
    # r+ needs the file to exist, so it is still an input
    py = [
        'rows = open("data.csv").read()',
        'open("out.txt", "w").write("1")',
        'with open("log.txt", mode="a") as f: pass',
        'with open("in.txt") as f, open("res.txt", "w") as g: pass',
        'f = open("keep.txt", "r+")',
        'g = open("new.bin", "xb")',
        "h = open(",
        '    "multi.txt",',
        '    "w")',
    ]
    assert set(code_file_refs(py, "Python")) == {"data.csv", "in.txt", "keep.txt"}
    # repro_file_io() asks for the writes, and sorts them itself
    assert {"out.txt", "res.txt", "multi.txt"} <= set(
        code_file_refs(py, "Python", include_writes=True)
    )
    assert len(_code_blank_write_opens([*py, "", ""], "Python")) == len(py) + 2

    m = [
        "d = load('in.mat');",
        "fid = fopen('results.txt', 'w');",
        "fid2 = fopen('raw.txt');",
        "fid3 = fopen(\"app.txt\", 'at');",
        "fid4 = fopen('upd.txt', 'r+');",
    ]
    assert set(code_file_refs(m, "MATLAB")) == {"in.mat", "raw.txt", "upd.txt"}

    # Mplus: FILE = under SAVEDATA: is the file the model writes
    mp = [
        "TITLE: two files",
        'DATA: FILE = "in.dat";',
        "VARIABLE: NAMES = y1-y3;",
        "MODEL: f BY y1-y3;",
        "SAVEDATA:",
        '  FILE = "fscores.dat";',
        "  SAVE = FSCORES;",
        "OUTPUT: sampstat;",
    ]
    assert code_file_refs(mp, "Mplus") == ["in.dat"]


def test_blank_write_opens_keeps_the_lines() -> None:
    lines = ['x = open("a.txt", "w")', "y = 1"]
    assert _code_blank_write_opens(lines, "Python") == ["x = " + " " * (len(lines[0]) - 4), "y = 1"]
    # a mode regex of the caller: the reproducibility check counts r+ as a write
    keep = ['f = open("keep.txt", "r+")']
    assert _code_blank_write_opens(keep, "Python") == keep
    wr = r"^(['\"])(?=[^'\"]*[wax+])[rwaxbt+]+\1$"
    assert _code_blank_write_opens(keep, "Python", mode_regex=wr) != keep
    # other languages are left as they are
    assert _code_blank_write_opens(lines, "R") == lines


def test_blank_mplus_savedata() -> None:
    mp = ["DATA: FILE = a.dat;", "savedata:", "  file = b.dat;", "OUTPUT: x;", "  y"]
    assert _code_blank_mplus_savedata(mp) == [mp[0], "", "", mp[3], mp[4]]


def test_code_file_refs_ignores_capitals_in_sas_spss_and_mplus() -> None:
    # these languages ignore capitals; only one way of writing was found
    assert set(code_file_refs(['DATA: FILE = "UP.dat";', 'data: file = "low.dat";'], "Mplus")) == {
        "UP.dat",
        "low.dat",
    }
    sas = [
        "proc import datafile='low.csv' out=a;",
        "PROC IMPORT DATAFILE='UP.csv' OUT=b;",
        "INFILE bare_up.dat;",
    ]
    assert set(code_file_refs(sas, "SAS")) == {"low.csv", "UP.csv", "bare_up.dat"}
    spss = ["GET FILE='UP.sav'.", "get file='low.sav'.", "get data /type=txt /file=bare_low.txt"]
    assert set(code_file_refs(spss, "SPSS")) == {"UP.sav", "low.sav", "bare_low.txt"}
    # Stata does not: USE is not a command
    assert code_file_refs(['use "a.dta"', 'USE "b.dta"'], "Stata") == ["a.dta"]
