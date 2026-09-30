"""The data.table::fread() emulation (``_files_fread``) against recorded R output.

``data/fread_cases.json`` holds 150 small randomly generated delimited files
(quote rules, type ladders, ragged rows, blank lines, NA strings, dates...);
``data/fread_quoted_cases.json`` 80 quote-heavy ones (every field quoted, or
strings quoted as ``write.csv()`` does, with separators and doubled quotes
inside quotes and the odd line only the general tokenizer reads; written by
``data/make_fread_quoted_cases.py``). ``data/<battery>_ref.json`` is what
fread 1.18 returned for each (written by ``data/make_fread_ref.R``).
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

from metacheck.datacheck._files_fread import FreadError, fread
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

_R_CLASS = {"Int64": "integer", "float64": "numeric", "boolean": "logical",
            "string": "character", "object": "IDate"}  # fmt: skip


def _r_class(s: pd.Series) -> str:
    d = str(s.dtype)
    return "POSIXct" if "datetime" in d else _R_CLASS.get(d, d)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fread_matches_r(case: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / case["name"]
    path.write_bytes(base64.b64decode(case["b64"]))
    ref = REF[case["name"]]
    nrows = math.inf if case["nrows"] is None else case["nrows"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "error" in ref:
            with pytest.raises(FreadError):
                fread(path, case["sep"], case["header"], nrows)
            return
        df = fread(path, case["sep"], case["header"], nrows)
    classes = ref["classes"]
    got = []
    for j, cls in enumerate(classes):
        s = df.iloc[:, j]
        if cls == "integer64":  # R side stored as character
            assert str(s.dtype) == "Int64"
            df.isetitem(j, s.astype("string"))
            got.append("integer64")
        else:
            got.append(_r_class(s))
    assert got == classes
    value = ref["value"]
    for j, cls in enumerate(classes):
        if cls == "IDate":  # R's format() writes year 12 as "12-01-01", not "0012-01-01"
            col = value["v"][j]
            col["v"] = [_pad_year(v) for v in col["v"]]
    assert compare(value, canonical(df), Options()) == []


def _pad_year(v: str | None) -> str | None:
    if v is None:
        return None
    sign = "-" if v.startswith("-") else ""
    year, md = v.lstrip("-").split("-", 1)
    return f"{sign}{int(year):04d}-{md}"


def test_empty_file_warns_and_gives_empty_frame(tmp_path: Path) -> None:
    p = tmp_path / "e.csv"
    p.write_bytes(b"")
    with pytest.warns(UserWarning):
        df = fread(p, ",", True)
    assert df.shape == (0, 0)


def test_whitespace_only_file_errors(tmp_path: Path) -> None:
    p = tmp_path / "w.csv"
    p.write_bytes(b"   \n  \n")
    with pytest.raises(FreadError, match="fully whitespace"):
        fread(p, ",", True)


def test_date_and_timestamp_columns(tmp_path: Path) -> None:
    p = tmp_path / "d.csv"
    p.write_text("d,t\n2020-01-02,2020-01-02 03:04:05\n2021-12-31,2021-12-31T23:59:59Z\n")
    df = fread(p, ",", True)
    import datetime as dt

    assert df["d"].tolist() == [dt.date(2020, 1, 2), dt.date(2021, 12, 31)]
    assert str(df["t"].dt.tz) == "UTC"
    assert df.attrs["col_attrs"] == {
        "d": {"class": ["IDate", "Date"]},
        "t": {"class": ["POSIXct", "POSIXt"], "tzone": "UTC"},
    }
