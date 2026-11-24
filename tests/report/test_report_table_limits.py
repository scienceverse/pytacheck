"""Port of the table-limit tests of tests/testthat/test-report-helpers.R (metacheck 0.4.4.36)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from metacheck.report import scroll_table
from metacheck.report.blocks import ReportTable, _report_table_limits
from metacheck.report.brief import _report_extract_tables
from metacheck.report.render import scroll_table_qmd, table_gfm
from metacheck.utils import options

_OPTS = [
    "metacheck.report_max_rows",
    "metacheck.report_max_cols",
    "metacheck.report_max_cell_chars",
]


@pytest.fixture(autouse=True)
def _reset_options():
    old = options(dict.fromkeys(_OPTS))
    yield
    options(old)


def test_a_table_within_the_limits_is_unchanged_and_has_no_note():
    small = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    table, note = _report_table_limits(small)
    pd.testing.assert_frame_equal(table, small)
    assert note is None


def test_rows_and_columns_are_cut_to_100_with_a_note():
    wide = pd.DataFrame(np.arange(150 * 120).reshape(150, 120)).add_prefix("V")
    table, note = _report_table_limits(wide)
    assert table.shape == (100, 100)
    assert list(table.columns) == list(wide.columns[:100])
    assert table.iloc[:, 0].tolist() == wide.iloc[:100, 0].tolist()
    assert note == "*Showing the first 100 of 150 rows and the first 100 of 120 columns.*"


def test_long_text_is_cut_a_link_is_left_whole_and_na_stays_na():
    long = "a" * 400
    link = "<a href='https://osf.io/abcde'>" + "b" * 400 + "</a>"
    table, note = _report_table_limits(pd.DataFrame({"t": [long, link, None, "short"]}))
    assert table["t"].iloc[0] == "a" * 300 + "…"
    assert table["t"].iloc[1] == link
    assert pd.isna(table["t"].iloc[2])
    assert table["t"].iloc[3] == "short"
    assert note == "*Cells longer than 300 characters are cut (…).*"


def test_a_factor_column_is_cut_too():
    long = "a" * 400
    table, _ = _report_table_limits(pd.DataFrame({"f": pd.Categorical([long, "x"])}))
    assert table["f"].iloc[0] == "a" * 300 + "…"


def test_blank_and_repeated_column_names_are_kept():
    dup = pd.DataFrame(np.arange(360).reshape(120, 3))
    dup.columns = pd.Index(["", "", "x"])
    table, _ = _report_table_limits(dup)
    assert list(table.columns) == ["", "", "x"]
    assert len(table) == 100


def test_the_settings_change_the_limits():
    options(
        {
            "metacheck.report_max_rows": 5,
            "metacheck.report_max_cols": 2,
            "metacheck.report_max_cell_chars": 10,
        }
    )
    table, note = _report_table_limits(pd.DataFrame({"a": range(6), "b": range(6), "c": "c" * 20}))
    assert table.shape == (5, 2)
    assert note == "*Showing the first 5 of 6 rows and the first 2 of 3 columns.*"
    table, _ = _report_table_limits(pd.DataFrame({"c": ["c" * 20]}))
    assert table["c"].iloc[0] == "c" * 10 + "…"


@pytest.mark.parametrize("bad", [0, -1, "x", [1, 2]])
def test_an_unusable_setting_falls_back_to_the_default(bad):
    options({"metacheck.report_max_rows": bad})
    table, _ = _report_table_limits(pd.DataFrame({"a": range(150)}))
    assert len(table) == 100


def test_scroll_table_holds_only_what_the_report_shows():
    big = pd.DataFrame({"n": range(1, 13563), "text": "v" * 400})
    block = scroll_table(big)
    assert isinstance(block, ReportTable)
    assert block.note == (
        "*Showing the first 100 of 13562 rows. Cells longer than 300 characters are cut (…).*"
    )
    (tbl,) = _report_extract_tables([block])
    assert len(tbl) == 100
    assert tbl["text"].iloc[0] == "v" * 300 + "…"
    # the note goes under the table in the .qmd and the markdown
    assert scroll_table_qmd(big).rstrip().endswith(block.note)
    assert table_gfm(block).endswith(block.note)


def test_a_line_break_in_a_long_cell_is_cut_before_it_becomes_br():
    block = scroll_table(pd.DataFrame({"t": ["x" * 299 + "\nmore"]}))
    assert block.data["t"].iloc[0] == "x" * 299 + "<br>…"


def test_widths_of_columns_left_out_are_dropped():
    block = scroll_table(pd.DataFrame(np.arange(101).reshape(1, 101)), [10] * 101)
    assert len(block.colwidths) == 100


def test_a_table_within_the_limits_has_no_note():
    assert scroll_table(pd.DataFrame({"a": [1, 2, 3]})).note is None
