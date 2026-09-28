"""How the parity encoder writes a report's tables (``scroll_table()`` blocks),
and what the comparison and the lock see of them."""

from __future__ import annotations

import pandas as pd

from parity import lockfile
from parity.canonical import canonical
from parity.compare import Options, comparable, compare
from pytacheck.module import module_run
from pytacheck.report.blocks import ReportTable, collapse_section, scroll_table


def _table() -> ReportTable:
    tbl = scroll_table(pd.DataFrame({"text": ["a\nb", None], "p": [0.04, 0.5], "n": [1, 2]}))
    assert isinstance(tbl, ReportTable)
    return tbl


def test_report_table_holds_names_and_rows() -> None:
    assert canonical(_table()) == {
        "t": "report_table",
        "table": {
            "t": "df",
            "nrow": 2,
            "names": ["text", "p", "n"],
            "v": [
                {"t": "chr", "v": ["a<br>b", None]},
                {"t": "dbl", "v": [0.04, 0.5]},
                {"t": "int", "v": [1, 2]},
            ],
        },
    }


def test_display_options_are_not_encoded() -> None:
    tbl = _table()
    wide = ReportTable(tbl.data, colwidths=[0.2, 0.8], maxrows=10, escape=True, column="page")
    assert canonical(wide) == canonical(tbl)


def test_tables_inside_block_lists_are_encoded() -> None:
    blocks = canonical(collapse_section(_table(), "Details"))
    assert blocks["t"] == "list"
    assert [b["t"] for b in blocks["v"]] == ["chr", "report_table", "chr"]
    assert blocks["v"][1]["table"]["names"] == ["text", "p", "n"]


def test_the_lock_sees_only_the_row_count() -> None:
    # what ReportTable.to_canonical() gave before it encoded the table, so no
    # lock digest moves until the R side writes report tables too
    before = {"t": "report_table", "nrow": 2}
    for options in (Options(), Options(report="exact")):
        assert comparable(canonical(_table()), options) == before
        nested = comparable(canonical(["intro", _table()]), options)
        assert nested["v"][1] == before
    assert lockfile.digest(comparable(canonical(_table()), Options())) == lockfile.digest(before)


def test_module_report_tables(demo) -> None:
    out = module_run(demo, "marginal")
    tables = [b for b in out.report if isinstance(b, ReportTable)]
    assert len(tables) == 1
    encoded = canonical(out)
    report = encoded["v"][encoded["names"].index("report")]
    (node,) = [b for b in report["v"] if b["t"] == "report_table"]
    assert node["table"] == canonical(tables[0].data)
    assert node["table"]["nrow"] > 0

    exact = comparable(encoded, Options(report="exact"))
    report = exact["v"][exact["names"].index("report")]
    nrow = len(tables[0].data)
    assert [b for b in report["v"] if b["t"] == "report_table"] == [
        {"t": "report_table", "nrow": nrow}
    ]
    prose = comparable(encoded, Options())
    assert prose["v"][prose["names"].index("report")]["t"] == "prose"


def test_prose_comparison_still_skips_tables() -> None:
    # R writes the table as a code chunk inside the prose; the default
    # comparison drops both the chunk and Python's table
    chunk = "```{r}\n#| echo: false\ntable <- data.frame()\n```"
    r = {
        "t": "module_output",
        "names": ["report"],
        "v": [{"t": "chr", "v": [f"Intro\n\n{chunk}\n\nOutro"]}],
    }
    p = {
        "t": "module_output",
        "names": ["report"],
        "v": [canonical(["Intro", _table(), "Outro"])],
    }
    assert compare(r, p, Options()) == []
