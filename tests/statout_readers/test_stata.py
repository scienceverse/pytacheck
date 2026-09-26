"""Tests for the Stata ``.smcl`` log reader (port of R/stata.R)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pytacheck.statout.stata import (
    _smcl_command_chunks,
    _smcl_export_syntax,
    _smcl_render_line,
    _stata_is_numlike,
    _stata_is_rule_line,
    _stata_output_oneline,
    _stata_output_tables,
    _stata_split_block,
    export_stata_smcl_html,
    import_stata_smcl,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "text"


def test_render_line_layout_directives() -> None:
    line = "{txt}{space 7}price {c |}{res}{col 20}     74    6165.257{hline 5}{c +}{hline}"
    out = _smcl_render_line(line)
    assert out.startswith("       price |" + " " * 6 + "     74    6165.257-----+")
    assert len(out) == 78  # {hline} fills to Stata's default 78 columns


def test_render_line_payload_and_quirks() -> None:
    line = "a{dup 3:ab}b{char 65}{c 0x41}{ralign 8:xy}|{center 7:ab}|{lalign 5:z}|{res:-1.5}{it:x}{* c}{bf}"
    # {c 0xNN} is hexadecimal: 0x41 is "A" (metacheck reads it as decimal 41,
    # ")", U146)
    assert _smcl_render_line(line) == "aabababbAA      xy|  ab   |z    |-1.5x"
    assert _smcl_render_line("unmatched { brace {res:{bf:x}} y") == "unmatched {bf:x} y"
    # {c -(}/{c )-} are literal braces (metacheck re-parses and drops them, U146)
    assert _smcl_render_line("{c -(}x{c )-}{c TT}{c S|}") == "{x}\u252c"
    assert _smcl_render_line("{hi:{c -(}t{c )-}}") == "{t}"


def test_render_line_unusable_dup_count() -> None:
    # U146: a {dup} count outside the integer range is left out (metacheck
    # renders "NA" and a later column directive fails the whole read)
    assert _smcl_render_line("{dup 99999999999:a}q{res:x}") == "qx"
    assert _smcl_render_line("{dup 99999999999:a}{col 3}x") == "   x"


def test_command_chunks() -> None:
    rendered = [
        "header",
        ". summarize x",
        "",
        "  Variable | Obs",
        ". foreach v of varlist a b {",
        "  2.   quietly summarize `v'",
        "  3. }",
        "> continued",
    ]
    chunks = _smcl_command_chunks(rendered)
    assert [c["command"] for c in chunks] == [
        "summarize x",
        "foreach v of varlist a b { quietly summarize `v' } continued",
    ]
    assert chunks[0]["output"] == ["", "  Variable | Obs"]
    assert chunks[1]["output"] == []
    assert _smcl_command_chunks(["no echo"]) == []
    assert _smcl_command_chunks(["> only continuation"]) == []
    # output rows that look like numbered echo lines (list's "  1. | ...")
    # stay output (metacheck counts them into the command, see U140)
    listed = _smcl_command_chunks([". list x", "     +---+", "  1. | 5 |", "  2. | 7 |"])
    assert listed[0]["command"] == "list x"
    assert listed[0]["output"] == ["     +---+", "  1. | 5 |", "  2. | 7 |"]


def test_numlike_split_and_rules() -> None:
    assert _stata_is_numlike(["12", "1,234", "< .001", "Inf", ".", "abc", None]) == [
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    # tabs expand to 8 spaces before the blank-column split
    cols = _stata_split_block(["  Name | Obs", "\tx   | 12"])
    assert cols == [["Name", ""], ["| Obs", "x   |"], ["", "12"]]
    assert _stata_split_block(["  ", " "]) is None
    assert _stata_is_rule_line("---+---")
    assert _stata_is_rule_line("--┬--")
    assert not _stata_is_rule_line("   ")
    assert not _stata_is_rule_line("-- x")


def test_output_tables_and_oneline() -> None:
    lines = [
        "  Group |  Mean   SD",
        "--------+------------",
        "  a     |  1.5   0.2",
        "  b     |  2.5   0.3",
    ]
    (tab,) = _stata_output_tables(lines)
    assert list(tab["data"].columns) == ["Group", "|", "Mean", "SD"]
    assert tab["data"]["Mean"].tolist() == ["1.5", "2.5"]
    res = _stata_output_oneline(["Iteration 2: log likelihood = -640.68  chi2(2) = 12.5", "none"])
    assert len(res) == 1
    assert list(res[0]["data"].columns) == ["likelihood", "chi2"]
    assert res[0]["data"].iloc[0].tolist() == ["-640.68", "12.5"]


def test_import_stata_smcl_fixture() -> None:
    tabs = import_stata_smcl(FIX / "analysis.smcl")
    assert [t["table_index"] for t in tabs] == list(range(1, len(tabs) + 1))
    first = tabs[0]
    assert first["analysis"] == first["syntax"] == "summarize price mpg weight"
    assert first["title"] is None
    assert first["data"]["Obs"].tolist() == ["74", "74", "74"]
    assert any(t["analysis"] == "tabulate rep78" for t in tabs)
    assert import_stata_smcl(FIX / "nocommands.smcl") == []


def test_import_stata_smcl_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        import_stata_smcl(tmp_path / "none.smcl")
    with pytest.raises(ValueError, match=re.escape("Not a .smcl file")):
        import_stata_smcl(FIX / "twolevel.out")


def test_export_html_and_syntax(tmp_path: Path) -> None:
    out = Path(export_stata_smcl_html(FIX / "analysis.smcl", tmp_path / "a.html"))
    html = out.read_text()
    assert html.count("<h3><code>regress price mpg weight</code></h3>") == 1
    assert "<title>analysis.smcl</title>" in html
    src = tmp_path / "analysis.smcl"
    src.write_bytes((FIX / "analysis.smcl").read_bytes())
    do = _smcl_export_syntax(src)
    assert Path(do) == tmp_path / "code" / "analysis.do"  # joined with "/", as R does
    lines = Path(do).read_text().splitlines()
    assert lines[0] == "summarize price mpg weight"
    assert lines[-1] == "log close"
    nocmd = tmp_path / "n.smcl"
    nocmd.write_bytes((FIX / "nocommands.smcl").read_bytes())
    assert _smcl_export_syntax(nocmd) is None
