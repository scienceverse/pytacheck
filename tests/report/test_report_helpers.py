"""Port of tests/testthat/test-report-helpers.R."""

from __future__ import annotations

import pandas as pd
import pytest

from metacheck.report import (
    ReportTable,
    collapse_section,
    format_ref,
    link,
    plural,
    report_table,
    scroll_table,
)
from metacheck.report.render import DataTable, scroll_table_qmd

LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
letters = [c.lower() for c in LETTERS]


def test_scroll_table():
    table = pd.DataFrame({"uc": LETTERS, "lc": letters})
    assert isinstance(scroll_table(table), ReportTable)
    obs = scroll_table_qmd(table)
    assert "```{r}" in obs
    assert 'metacheck::report_table(table, "auto", 2, FALSE)' in obs

    obs = scroll_table_qmd(table, escape=True)
    assert 'metacheck::report_table(table, "auto", 2, TRUE)' in obs

    # vector vs unnamed table version
    obs_table = scroll_table_qmd(pd.DataFrame({"": LETTERS}))
    obs_vec = scroll_table_qmd(LETTERS)
    assert obs_table == obs_vec

    # paginate after maxrows
    obs_2 = scroll_table_qmd(list(range(1, 11)))
    obs_10 = scroll_table_qmd(list(range(1, 11)), maxrows=10)
    assert 'metacheck::report_table(table, "auto", 2, FALSE)' in obs_2
    assert 'metacheck::report_table(table, "auto", 10, FALSE)' in obs_10

    # colwidths
    obs = scroll_table_qmd(pd.DataFrame({"a": [1.0], "b": [2.0]}), [0.3, 0.7])
    assert "metacheck::report_table(table, c(0.3, 0.7), 2, FALSE)" in obs
    obs = scroll_table_qmd(pd.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]}), [0.1, 0.4])
    assert "metacheck::report_table(table, c(0.1, 0.4), 2, FALSE)" in obs
    obs = scroll_table_qmd(
        pd.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]}), [None, 200, None, None]
    )
    assert "metacheck::report_table(table, c(NA, 200, NA, NA), 2, FALSE)" in obs

    # empty tables give ""
    assert scroll_table(pd.DataFrame({"a": []})) == ""
    assert scroll_table_qmd(pd.DataFrame({"a": []})) == ""


def test_scroll_table_line_breaks():
    rt = scroll_table(pd.DataFrame({"t": ["a\nb"]}))
    assert rt.data["t"].tolist() == ["a<br>b"]
    assert "#| column: page" in scroll_table_qmd(pd.DataFrame({"t": ["x"]}), column="page")


def test_report_table():
    with pytest.raises(TypeError):
        report_table("bad_arg")

    # one row
    table = pd.DataFrame({"a": [1.0], "b": [2.0], "c": [3.0], "d": [4.0]})
    obs = report_table(table)
    assert isinstance(obs, DataTable)
    pd.testing.assert_frame_equal(obs.data, table)
    assert obs.options["pageLength"] == 2
    assert obs.options["dom"] == "t"

    # 10 rows, show 2
    table = pd.DataFrame({"a": range(1, 11), "b": range(21, 31)})
    obs = report_table(table)
    pd.testing.assert_frame_equal(obs.data, table)
    assert obs.options["pageLength"] == 2
    assert obs.options["dom"] == "<'top' p>"

    # 10 rows, show 10
    obs = report_table(table, maxrows=10)
    assert obs.options["pageLength"] == 10
    assert obs.options["dom"] == "t"

    # colwidths
    obs = report_table(table, [0.5, 0.5])
    assert obs.options["columnDefs"][0]["width"] == "50%"
    assert obs.options["columnDefs"][1]["width"] == "50%"
    obs = report_table(table, [20, 50])
    assert obs.options["columnDefs"][0]["width"] == "20px"
    assert obs.options["columnDefs"][1]["width"] == "50px"
    obs = report_table(table, [None, "4em"])
    assert obs.options["columnDefs"][0]["targets"] == 1
    assert obs.options["columnDefs"][0]["width"] == "4em"


def test_report_table_names_and_html():
    obs = report_table(pd.DataFrame({"p_value": [0.5], "note": ["x\ny"]}), maxrows=1)
    assert list(obs.data.columns) == ["p_<wbr>value", "note"]
    assert obs.data["note"].tolist() == ["x<br>y"]
    html = obs.to_html()
    assert '<th class="dt-right">p_<wbr>value</th>' in html
    assert "<td>x<br>y</td>" in html
    assert "data-page-length" not in html  # 1 row, maxrows 1: no pagination


def test_report_table_escape():
    df = pd.DataFrame({"a_b": ["<b>bold</b>"]})
    raw = report_table(df).to_html()
    assert "<td><b>bold</b></td>" in raw
    esc = report_table(df, escape=True).to_html()
    assert "<td>&lt;b&gt;bold&lt;/b&gt;</td>" in esc
    assert "<th>a_<wbr>b</th>" in esc


def test_report_table_pagination_html():
    html = report_table(pd.DataFrame({"a": range(10)}), maxrows=3).to_html()
    assert 'data-page-length="3"' in html
    assert html.count("<tr class=") == 10
    assert 'class="dt-paging"' in html


def test_collapse_section():
    with pytest.raises(TypeError):
        collapse_section()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        collapse_section("a", callout="d")
    obs = collapse_section("hello")
    assert "callout-tip" in obs
    obs = collapse_section("hello", callout="warning")
    assert "callout-warning" in obs
    assert collapse_section(["a", "b"], collapse=False) == (
        '::: {.callout-tip title="Learn More" collapse="false"}\n\na\n\nb\n\n:::\n'
    )


def test_collapse_section_with_table():
    tbl = scroll_table(pd.DataFrame({"a": [1]}))
    obs = collapse_section(tbl, "Full table")
    assert obs == ['::: {.callout-tip title="Full table" collapse="true"}', tbl, ":::\n"]
    obs = collapse_section(["Intro", tbl])
    assert obs[1:3] == ["Intro", tbl]


def test_plural():
    assert plural(0) == "s"
    assert plural(1) == ""
    assert plural(2) == "s"
    assert plural(0, "is", "are") == "are"
    assert plural(1, "is", "are") == "is"
    assert plural(2, "is", "are") == "are"


def test_link():
    assert (
        link("https://google.com") == "<a href='https://google.com' target='_blank'>google.com</a>"
    )
    assert link("http://google.com") == "<a href='http://google.com' target='_blank'>google.com</a>"
    assert (
        link("https://google.com", "Google")
        == "<a href='https://google.com' target='_blank'>Google</a>"
    )
    assert link("https://google.com", "Google", False) == "<a href='https://google.com'>Google</a>"
    url = ["https://google.com", "https://scienceverse.org"]
    text = ["Google", "Scienceverse"]
    assert link(url, text, False) == [
        "<a href='https://google.com'>Google</a>",
        "<a href='https://scienceverse.org'>Scienceverse</a>",
    ]
    assert link([None, "https://scienceverse.org"], text, False) == [
        None,
        "<a href='https://scienceverse.org'>Scienceverse</a>",
    ]


def test_format_ref():
    exp_a = (
        "DeBruine LM (2005). &ldquo;Trustworthy but not lust-worthy: Context-specific effects of "
        "facial resemblance.&rdquo; <em>Proceedings of the Royal Society B: Biological Sciences</em>, "
        '<b>272</b>(1566), 919&ndash;922. <a href="https://doi.org/10.1098/rspb.2004.3003">'
        "doi:10.1098/rspb.2004.3003</a>."
    )
    # pytacheck stores R's formatted HTML; format_ref() tidies it the same way
    assert format_ref("<p>\n" + exp_a + "\n</p>") == exp_a
    assert format_ref(["help", "me"]) == ["help", "me"]
