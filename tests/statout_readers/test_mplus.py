"""Tests for the Mplus ``.out`` reader (port of R/mplus.R)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pytacheck.statout.mplus import (
    _mplus_export_syntax,
    _mplus_is_genuine_output,
    _mplus_is_level_label,
    _mplus_is_section_header,
    _mplus_is_stat_header_line,
    _mplus_looks_header,
    _mplus_output_labelvalue,
    _mplus_output_tables,
    _mplus_sections,
    _mplus_syntax_lines,
    _r_seq,
    export_mplus_html,
    import_mplus_output,
)
from pytacheck.statout.spv import _read_lines

FIX = Path(__file__).resolve().parent / "fixtures" / "text"


def test_section_headers() -> None:
    assert _mplus_is_section_header("MODEL RESULTS")
    assert _mplus_is_section_header("  technical 4 output ")
    assert _mplus_is_section_header("MODEL RESULTS FOR CLASS 1")
    assert not _mplus_is_section_header(" SW       WITH")
    assert not _mplus_is_section_header(None)


def test_sections_use_r_sequences() -> None:
    assert _r_seq(3, 1) == [3, 2, 1]
    secs = _mplus_sections(["x", "MODEL RESULTS", "R-SQUARE"])
    # adjacent headers and a last-line header: sections without lines (R's
    # (h+1):end counts down to the header and past the end, U147)
    assert secs[0]["lines"] == []
    assert secs[1]["lines"] == []
    secs = _mplus_sections(["MODEL RESULTS", "a", "b", "R-SQUARE", "c"])
    assert [s["lines"] for s in secs] == [["a", "b"], ["c"]]


def test_line_predicates() -> None:
    assert _mplus_is_stat_header_line("   Estimate   S.E.")
    assert _mplus_is_stat_header_line("   Observed   Variable")
    assert not _mplus_is_stat_header_line("  Expected    Observed")
    assert _mplus_is_level_label("Within Level")
    assert _mplus_is_level_label("Group G1")
    assert _mplus_looks_header("  Y   X")
    assert not _mplus_looks_header("  ______   ______")
    assert _mplus_looks_header(None)  # NA "looks like a header" in R


def test_grouped_table_and_labelvalue() -> None:
    lines = [
        "                    Estimate       S.E.",
        "",
        "Within Level",
        "",
        " Y          ON",
        "    X                  0.498      0.044",
        "",
        "Between Level",
        "    W                  0.203      0.071",
    ]
    tabs = _mplus_output_tables(lines)
    (tab,) = tabs
    df = tab["data"]
    assert list(df.columns) == ["level", "group", "V1", "Estimate", "S.E."]
    assert df["level"].tolist() == ["Within Level", "Between Level"]
    assert df["group"].tolist() == ["Y          ON", ""]
    assert all(tabs.consumed)
    (lv,) = _mplus_output_labelvalue(
        ["Number of groups     1", "  P-Value   0.100D-05", "text"], "T"
    )
    assert lv["title"] == "T"
    assert lv["data"].iloc[0].tolist() == ["1", "0.100D-05"]
    assert _mplus_output_labelvalue(["nothing"], "T") == []


def test_import_mplus_output_twolevel() -> None:
    tabs = import_mplus_output(FIX / "twolevel.out")
    analyses = [t["analysis"] for t in tabs]
    assert analyses[0] == "SUMMARY OF ANALYSIS"
    assert "MODEL RESULTS" in analyses
    assert "INPUT INSTRUCTIONS" not in analyses
    syntax = tabs[0]["syntax"]
    assert syntax.startswith("TITLE: Two-level regression;")
    assert syntax.endswith("y ON w;")
    res = next(t for t in tabs if t["analysis"] == "MODEL RESULTS")["data"]
    assert list(res.columns[:2]) == ["level", "group"]
    assert res["Estimate"].tolist()[0] == "0.498"


def test_import_mplus_output_edge_files(tmp_path: Path) -> None:
    assert [t["analysis"] for t in import_mplus_output(FIX / "adjacent.out")] == [
        "SUMMARY OF ANALYSIS"
    ]
    assert import_mplus_output(FIX / "nosections.out") == []
    with pytest.raises(ValueError, match=re.escape("Not a Mplus .out file")):
        import_mplus_output(FIX / "compiler.out")
    with pytest.raises(ValueError, match=re.escape("Not a .out file")):
        import_mplus_output(FIX / "analysis.smcl")
    with pytest.raises(FileNotFoundError):
        import_mplus_output(tmp_path / "none.out")


def test_genuine_output_and_syntax(tmp_path: Path) -> None:
    assert _mplus_is_genuine_output(FIX / "twolevel.out")
    assert not _mplus_is_genuine_output(FIX / "compiler.out")
    assert not _mplus_is_genuine_output(tmp_path / "none.out")
    lines = _read_lines(FIX / "adjacent.out")
    assert _mplus_syntax_lines(lines) == "TITLE: adjacent headers;"
    assert _mplus_syntax_lines(["no input"]) is None
    src = tmp_path / "twolevel.out"
    src.write_bytes((FIX / "twolevel.out").read_bytes())
    inp = _mplus_export_syntax(src)
    assert Path(inp) == tmp_path / "code" / "twolevel.inp"  # joined with "/", as R does
    assert Path(inp).read_text().splitlines()[0] == "TITLE: Two-level regression;"
    comp = tmp_path / "c.out"
    comp.write_bytes((FIX / "compiler.out").read_bytes())
    assert _mplus_export_syntax(comp) is None


def test_export_mplus_html(tmp_path: Path) -> None:
    out = Path(export_mplus_html(FIX / "twolevel.out", tmp_path / "m.html"))
    html = out.read_text()
    assert html.count("<h3>MODEL RESULTS</h3>") == 1
    assert "<h4>" not in html  # table titles are NA or equal the section
    empty = Path(export_mplus_html(FIX / "nosections.out", tmp_path / "e.html")).read_text()
    assert "No result tables could be recovered from this .out file." in empty
