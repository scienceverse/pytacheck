"""The delimited-file reader (``_files_delim``) against recorded R output.

``data/fread_cases.json`` holds 150 small randomly generated delimited files
(quote rules, type ladders, ragged rows, blank lines, NA strings, dates...);
``data/fread_quoted_cases.json`` 80 quote-heavy ones (every field quoted, or
strings quoted as ``write.csv()`` does, with separators and doubled quotes
inside quotes and the odd line only the general tokenizer reads; written by
``data/make_fread_quoted_cases.py``). ``data/<battery>_ref.json`` is what
``data.table::fread()`` 1.18 returned for each (written by ``data/make_fread_ref.R``).

The reader is pandas' C parser plus a layer that finds the table and types the columns the
way fread does. It agrees with fread on every case but the ones listed below, each of them
a difference of the reader chosen on purpose (a register entry in docs/UPSTREAM_ISSUES.md
or parity/divergences/) and pinned here, so that a change of the reader shows.
"""

from __future__ import annotations

import base64
import json
import math
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.datacheck._files_delim import read_delim
from parity.canonical import canonical
from parity.compare import Options, compare

DATA = Path(__file__).parent / "data"
_BATTERIES = ("fread", "fread_quoted")
CASES = [
    c
    for b in _BATTERIES
    for c in json.loads((DATA / f"{b}_cases.json").read_text(encoding="utf-8"))
]
# invalid UTF-8 read by fread stays raw bytes in R's JSON (surrogate escapes here)
REF = {
    r["name"]: r
    for b in _BATTERIES
    for r in json.loads((DATA / f"{b}_ref.json").read_bytes().decode("utf-8", "surrogateescape"))
}


def _words(text: str) -> list[str]:
    return text.split()


# fread keeps a doubled quote inside a quoted field (``"a""b"`` is ``a""b``); the reader
# gives ``a"b``, which is what the writer meant (U216).
DOUBLED_QUOTE = _words("""
    f1_0176 f1_0188 f1_0260 f2_0058 f2_0229 f3_0143 f3_0368 q_000 q_001 q_003 q_004 q_007 q_008
    q_009 q_011 q_013 q_015 q_017 q_018 q_021 q_022 q_024 q_026 q_028 q_029 q_031 q_032 q_034
    q_035 q_036 q_037 q_040 q_041 q_042 q_044 q_045 q_046 q_048 q_051 q_053 q_054 q_055 q_057
    q_058 q_059 q_061 q_063 q_068 q_071 q_073 q_074 q_075 q_076 q_077 q_078 q_079
""")
# A quote that closes a field before its end (``"a"b``) is read as the text ``ab``; fread
# chooses one of four quote rules by the table each gives, and keeps ``"a"b``, or reads
# another number of rows (D70: the file is not valid CSV, so neither reading is the right one).
IMPROPER_QUOTE = _words("""
    f1_0111 q_002 q_005 q_006 q_010 q_012 q_016 q_019 q_020 q_023 q_027 q_030 q_033 q_043 q_047
    q_049 q_050 q_060 q_062 q_066 q_067 q_069 q_070 q_072
""")
# fread keeps a tab that follows the separator in a text column; the reader drops it, as it
# drops every blank around an unquoted field of a table (D72).
LEADING_TAB = ["f2_0116"]
# fread stops with an internal error on these files (a first row that starts with a blank
# and a separator); the reader reads them (D11).
FREAD_ERROR = ["f2_0293", "f2_0342", "f3_0053"]

_R_CLASS = {"Int64": "integer", "float64": "numeric", "boolean": "logical",
            "string": "character", "object": "IDate"}  # fmt: skip


def _r_class(s: pd.Series) -> str:
    d = str(s.dtype)
    return "POSIXct" if "datetime" in d else _R_CLASS.get(d, d)


def _pad_year(v: str | None) -> str | None:
    if v is None:
        return None
    sign = "-" if v.startswith("-") else ""
    year, md = v.lstrip("-").split("-", 1)
    return f"{sign}{int(year):04d}-{md}"


def _read(case: dict[str, Any], path: Path) -> pd.DataFrame:
    nrows = math.inf if case["nrows"] is None else case["nrows"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return read_delim(path, case["sep"], case["header"], nrows)


def _agrees(
    case: dict[str, Any], df: pd.DataFrame, value: dict[str, Any], classes: list[str]
) -> bool:
    got = []
    for j, cls in enumerate(classes[: df.shape[1]]):
        s = df.iloc[:, j]
        if cls == "integer64":  # R side stored as character
            assert str(s.dtype) == "Int64"
            df.isetitem(j, s.astype("string"))
            got.append("integer64")
        else:
            got.append(_r_class(s))
    if got != classes:
        return False
    for j, cls in enumerate(classes):
        if cls == "IDate":  # R's format() writes year 12 as "12-01-01", not "0012-01-01"
            col = value["v"][j]
            col["v"] = [_pad_year(v) for v in col["v"]]
    return compare(value, canonical(df), Options()) == []


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_read_delim_matches_r(case: dict[str, Any], tmp_path: Path) -> None:
    name = case["name"].removesuffix(".csv")
    path = tmp_path / case["name"]
    path.write_bytes(base64.b64decode(case["b64"]))
    ref = REF[case["name"]]
    df = _read(case, path)
    if "error" in ref:
        assert name in FREAD_ERROR
        assert df.shape[1] > 0
        return
    classes, value = ref["classes"], ref["value"]
    if name in DOUBLED_QUOTE:  # R's text with the doubled quote collapsed is the reader's
        for col in value["v"]:
            if col["t"] == "chr":
                col["v"] = [v.replace('""', '"') if v is not None else v for v in col["v"]]
        assert _agrees(case, df, value, classes)
    elif name in IMPROPER_QUOTE or name in LEADING_TAB:
        assert not _agrees(case, df, value, classes)
    else:
        assert _agrees(case, df, value, classes)


def test_known_differences_are_cases() -> None:
    names = {c["name"].removesuffix(".csv") for c in CASES}
    listed = DOUBLED_QUOTE + IMPROPER_QUOTE + LEADING_TAB + FREAD_ERROR
    assert set(listed) <= names
    assert len(listed) == len(set(listed))
