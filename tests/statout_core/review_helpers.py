"""Python sides of the statout_core review parity cases (R idioms)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"


def parse_html_tables(path: str | Path) -> list[Any]:
    """``lapply(xml_find_all(read_html(path), "//table"), .stat_table_parse)``."""
    from pytacheck.statout.stat_tables import _read_html_file, _stat_table_parse

    doc = _read_html_file(str(path))
    return [_stat_table_parse(tb) for tb in doc.xpath("//table")]


def chr_frame(**cols: list[Any]) -> pd.DataFrame:
    """``data.frame(..., stringsAsFactors = FALSE, check.names = FALSE)`` of text."""
    return pd.DataFrame({k: pd.array(v, dtype="string") for k, v in cols.items()})


def with_roles(df: pd.DataFrame, roles: dict[str, Any]) -> pd.DataFrame:
    """``structure(df, col_roles = roles)``."""
    df = df.copy()
    df.attrs["col_roles"] = roles
    return df


def _so() -> Any:
    import pytacheck.statout.stat_output as so

    return so


def partial_field_tables() -> list[Any]:
    """Tables whose fields only match through R's partial ``$`` matching."""
    return [
        {"analysis": "A", "data": chr_frame(t=["2.5"], p=["0.01"]), "line_seq": 3},
        None,
        {"analysis": "B", "data": chr_frame(t=["1"]), "analysis_i": "x"},
        {"analysi": "C", "data": chr_frame(t=["1"]), "table_ind": 4},
        {"data_frame": chr_frame(t=["3"]), "analysis": "D", "table_index": 5},
        {
            "analysis": "E",
            "data": chr_frame(t=["4"]),
            "is_chart": None,
            "title_x": "TT",
            "model": "m",
            "call_f": "t.test",
        },
        {
            "analysis": "F",
            "data": chr_frame(W=["0.9"]),
            "call_fn_x": "shapiro.test",
            "line": 8,
            "line_seq_a": 2,
            "analysis_id": "",
        },
        {
            "analysis": "G",
            "data": chr_frame(t=["5"]),
            "line": None,
            "line_seq": 1,
            "table_index": None,
            "analysis_idx": "q",
            "syntax_raw": "X",
        },
    ]


def numeric_tables() -> list[Any]:
    """Tables with non-character columns (R numeric/integer/logical)."""
    import numpy as np

    df = pd.DataFrame(
        {
            "term": pd.array(["a", None], dtype="string"),
            "est": [100000.0, np.nan],
            "p": [1 / 3, 0.05],
            "n": pd.array([5, None], dtype="Int64"),
            "flag": [True, False],
        }
    )
    df2 = pd.DataFrame({"x": [np.nan, np.nan], "t": [0.1 + 0.2, 1e-20]})
    return [
        {"analysis": "num", "data": df, "table_index": 1},
        {"analysis": "allna", "data": df2, "table_index": 2, "analysis_id": 1e5},
    ]


def unicode_tables() -> list[Any]:
    return [
        {"analysis": "İx Test", "data": chr_frame(**{"İ": ["1"], "p": ["2"]})},
        {
            "analysis": "İx Test",
            "data": chr_frame(**{"tést": ["1"], "P": ["2"], "p ": ["3"], "***": ["4"]}),
        },
    ]


def roles_tables() -> list[Any]:
    df = with_roles(
        chr_frame(a=["x", "y"], b=["1", "2"], c=["p", "q"], d=["3", "4"], e=["r", "s"]),
        {
            "a": {"type": "number"},
            "b": {"type": "text"},
            "c": {"format": "pvalue"},
            "d": {"type": "Text ", "format": ""},
            "e": {"type": None, "format": None},
            "zz": {"type": "integer"},
        },
    )
    return [{"analysis": "roles", "data": df, "table_index": 1, "analysis_id": 7}]


def named_tables() -> dict[str, Any]:
    return {
        "first": {"analysis": "N1", "data": chr_frame(t=["1.5"]), "table_index": 1},
        "second": {"analysis": "N2", "data": chr_frame(t=["2.5"]), "table_index": 2},
    }


def na_doc() -> dict[str, Any]:
    """A document built in R with NA (not NULL) fields."""
    return {
        "schema": "s",
        "schema_version": "1",
        "paper_id": None,
        "source_file": None,
        "source_format": "R",
        "analyses": [
            {
                "analysis": None,
                "results": [
                    {
                        "result_id": "r1",
                        "test_id": "t",
                        "row_label": "",
                        "values": {"t": {"value": None}},
                    }
                ],
            }
        ],
    }


def merge_partial() -> list[Any]:
    so = __import__("pytacheck.statout.r_capture", fromlist=["_"])
    cap = [{"analysis": "cap", "data": chr_frame(t=["1"]), "line": 3, "line_seq": 1}]
    txt = [
        {"analysis": "txt3", "data": chr_frame(t=["2"]), "line_seq": 3},
        {"analysis": "txt4", "data": chr_frame(t=["3"]), "line_seq": 4},
        {"analysis": "txt_none", "data": chr_frame(t=["4"])},
    ]
    return so._r_merge_captures(cap, txt)


def write_tricky() -> list[Any]:
    """stat_output_write() of odd long tables / documents; returns the files."""
    import os
    import tempfile

    so = _so()
    tabs = [
        {
            "analysis": 'q "uoted", comma\nnewline',
            "data": chr_frame(t=["1.5"], p=["NA"]),
            "table_index": 1,
        },
        {"analysis": "é", "data": chr_frame(lab=["x y"], d=["2, 560"]), "table_index": 2},
    ]
    root = tempfile.mkdtemp()
    items = [
        {
            "file": "a/b.c.jasp",
            "json": so.stat_output_json(tabs, paper_id=12, source_file="b.c.jasp"),
            "long": so.stat_results_long(tabs, paper_id=12, source_file="b.c.jasp"),
        },
        {
            "file": ".hidden",
            "json": so.stat_output_json(tabs, source_file=".hidden"),
            "long": so.stat_results_long(tabs, paper_id=13, source_file=".hidden"),
        },
    ]
    out = so.stat_output_write(items, root)
    files = sorted(os.listdir(out))
    from tests.statout_core._helpers import read_lines

    return [files, *[read_lines(os.path.join(out, f)) for f in files]]
