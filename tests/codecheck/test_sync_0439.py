"""code_check helpers synchronised with metacheck 0.4.3.9 (R/code_check.R at a6850fd).

The expected values follow metacheck's tests (``test-code_check.R``); the parity cases
``code_file_refs.*_modes``, ``*_savedata`` and ``*_capitals`` in ``parity/cases/codecheck.yaml``
hold the values R produced.
"""

from __future__ import annotations

from metacheck.codecheck.core import _blank_mplus_savedata, _blank_write_opens, code_file_refs

PYTHON = [
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


def test_python_open_in_a_writing_mode_is_not_an_input() -> None:
    # r+ changes a file that must already exist, so it is still an input
    assert set(code_file_refs(PYTHON, "Python")) == {"data.csv", "in.txt", "keep.txt"}


def test_writes_are_found_when_asked_for() -> None:
    refs = code_file_refs(PYTHON, "Python", include_writes=True)
    assert {"out.txt", "res.txt", "multi.txt"} <= set(refs)


def test_blanking_keeps_the_number_of_lines() -> None:
    assert len(_blank_write_opens([*PYTHON, "", ""], "Python")) == len(PYTHON) + 2
    assert _blank_write_opens(["x = 1"], "R") == ["x = 1"]
    assert _blank_write_opens([], "Python") == []


def test_matlab_fopen_modes() -> None:
    code = [
        "d = load('in.mat');",
        "fid = fopen('results.txt', 'w');",
        "fid2 = fopen('raw.txt');",
        "fid3 = fopen(\"app.txt\", 'at');",
        "fid4 = fopen('upd.txt', 'r+');",
    ]
    assert set(code_file_refs(code, "MATLAB")) == {"in.mat", "raw.txt", "upd.txt"}


MPLUS = [
    "TITLE: two files",
    'DATA: FILE = "in.dat";',
    "VARIABLE: NAMES = y1-y3;",
    "MODEL: f BY y1-y3;",
    "SAVEDATA:",
    '  FILE = "fscores.dat";',
    "  SAVE = FSCORES;",
    "OUTPUT: sampstat;",
]


def test_mplus_savedata_is_an_output() -> None:
    assert code_file_refs(MPLUS, "Mplus") == ["in.dat"]
    blanked = _blank_mplus_savedata(MPLUS)
    assert blanked[4:7] == ["", "", ""]
    assert blanked[7] == "OUTPUT: sampstat;"
    assert blanked[:4] == MPLUS[:4]


def test_sas_spss_and_mplus_ignore_capitals() -> None:
    mplus = ['DATA: FILE = "UP.dat";', 'data: file = "low.dat";']
    assert set(code_file_refs(mplus, "Mplus")) == {"UP.dat", "low.dat"}
    sas = [
        "proc import datafile='low.csv' out=a;",
        "PROC IMPORT DATAFILE='UP.csv' OUT=b;",
        "INFILE bare_up.dat;",
    ]
    assert set(code_file_refs(sas, "SAS")) == {"low.csv", "UP.csv", "bare_up.dat"}
    spss = ["GET FILE='UP.sav'.", "get file='low.sav'.", "get data /type=txt /file=bare_low.txt"]
    assert set(code_file_refs(spss, "SPSS")) == {"UP.sav", "low.sav", "bare_low.txt"}
    # Stata does not ignore capitals: USE is not a command
    assert code_file_refs(['use "a.dta"', 'USE "b.dta"'], "Stata") == ["a.dta"]
