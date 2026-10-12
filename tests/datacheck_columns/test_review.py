"""Adversarial-review tests for the datacheck_columns port.

These pin R behaviours that a parity golden cannot hold (raw invalid-UTF-8
lines, bit-exact doubles) or that guard against regressions (vectorised
numeric paths). Expected values were produced by metacheck in R 4.5.3.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from metacheck.datacheck.columns import (
    _decode_value_labels,
    _missing_from_value_labels,
    data_col_stats,
    data_col_type,
    parse_codebook,
    parse_qsf,
)

REV = Path(__file__).parent / "fixtures" / "review"
# R accumulates in LDOUBLE, which is plain double on some platforms (arm64 macOS):
# there R's sums, and so the expected values, are the double ones
EXTENDED = np.finfo(np.longdouble).nmant > np.finfo(np.float64).nmant


def test_stats_bit_exact_with_r() -> None:
    # R's long-double mean/var and powl(): identical doubles, not just close ones
    x = [0.1, 0.2, 0.3, 1 / 3, 2 / 3, 0.1, 0.7, 1e8 + 0.1]
    st = data_col_stats(x, x).iloc[0]
    want = {
        "mean": 12500000.3125 if EXTENDED else 12500000.312499998,
        "sd": 35355338.973464407,
        "se": 12499999.969642855,
        "median": 0.31666666666666665,
        "min": 0.10000000000000001,
        "max": 100000000.09999999,
        "range": 100000000.0,
        "p25": 0.17500000000000002,
        "p75": 0.67500000000000004,
        "iqr": 0.5,
        "skewness": 1.8561553006146874,
        "kurtosis": 1.703125,
    }
    for k, v in want.items():
        assert st[k] == v, k
    assert (st["n"], st["n_missing"], st["n_unique"]) == (8, 0, 7)


@pytest.mark.parametrize(
    ("xs", "sd", "skew"),
    [
        ([1e200, -1e200, 3.0, 5.0], math.inf, math.nan),  # squares overflow double
        # the long-double sum of squares fits
        ([1e154, -1e154, 1e154], 1.1547005383792515e154 if EXTENDED else math.inf, math.nan),
        ([1e103, 2.0, 3.0, 4.0], 5e102, math.inf),  # cubes overflow
    ],
)
def test_stats_overflow_like_r(xs: list[float], sd: float, skew: float) -> None:
    st = data_col_stats(xs, xs)
    assert st["sd"].iloc[0] == sd
    got = float(st["skewness"].iloc[0])
    assert got == skew or (math.isnan(skew) and math.isnan(got))


def test_numeric_paths_are_vectorised() -> None:
    x = pd.Series(np.random.default_rng(0).normal(size=1_000_000))
    # CPU time, not wall time: a busy CI host must not fail a complexity check
    start = time.process_time()
    res = data_col_type("x", x)
    data_col_stats(res["numeric_values"], x)
    assert res["col_type"] == "continuous"
    assert time.process_time() - start < 5


def test_invalid_utf8_files_fall_back_to_raw_lines() -> None:
    # R: jsonlite rejects the bytes / gsub() fails, so parse_codebook() returns
    # readLines() -- the raw bytes kept (here as surrogate escapes)
    rtf = parse_codebook(REV / "latin1.rtf")
    assert rtf == ["{\\rtf1\\ansi age:\\tab Alter in Jahren\\par caf\udce9 = coffee shop\\par}"]
    js = parse_codebook(REV / "latin1.json")
    assert js == ['[{"name": "a", "label": "caf\udce9"}, {"name": "b", "label": "B"}]']
    assert parse_qsf(REV / "latin1.qsf") is None
    qsf = parse_codebook(REV / "latin1.qsf")
    assert isinstance(qsf, list) and len(qsf) == 1 and "caf\udce9?" in qsf[0]


def test_negative_lookahead_is_an_error_caught_by_parse_codebook() -> None:
    # R: seq_len(-1) errors inside the tryCatch -> readLines() of the workbook
    lines = parse_codebook(REV / "tricky.xlsx", header_lookahead=-1)
    assert isinstance(lines, list) and lines[:2] == ["PK\x03\x04\x14", "-'"]
    assert isinstance(parse_codebook(REV / "tricky.xlsx", header_lookahead=2.7), pd.DataFrame)


def test_decode_value_labels_keeps_duplicate_keys() -> None:
    vl = _decode_value_labels('{"1": "Refused", "1": "N/A", "2": "x"}')
    assert isinstance(vl, pd.Series)
    assert list(vl.index) == ["1", "1", "2"] and vl.tolist() == ["Refused", "N/A", "x"]
    assert _decode_value_labels('{"1": "a", "2": "b"}') == {"1": "a", "2": "b"}
    assert _missing_from_value_labels('{"1": "Refused", "1": "N/A"}') == (
        '{"1":"Refused","1.1":"N/A"}'
    )


def test_json_literals_and_invalid_bytes_rejected_like_jsonlite() -> None:
    assert _decode_value_labels('{"1": NaN, "2": "b"}') is None
    assert _decode_value_labels("[Infinity]") is None
    assert _decode_value_labels('{"1": "caf\udce9"}') is None
