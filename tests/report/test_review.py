"""Regression tests for the divergences found in the report review.

Each test pins R behaviour checked against metacheck (the parity cases in
parity/cases/report_review.yaml hold the R goldens).
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import module_run
from pytacheck.report.blocks import _cap_num, link, scroll_table
from pytacheck.report.html_output import _html_sniff_kind
from pytacheck.report.render import deparse, scroll_table_qmd, table_chunk
from pytacheck.report.report import (
    module_report,
    render_module_outputs,
    report_module_run,
    report_repository,
)
from pytacheck.validate import accuracy, validate


@pytest.fixture
def flex(test_module):
    return test_module("rv_flex")


def _report(flex: str, **kwargs) -> str:
    return module_report(module_run(pc.test_paper(["x"]), flex, **kwargs))


def test_module_report_recycles_summary_comparison(flex):
    # R: all(summary_text == report) recycles, so c("S.", "S.") == "S." drops the report
    rep = _report(flex, summary_text=["S.", "S."], report="S.")
    assert "S.\n\nS.\n\n::: {.callout-note" in rep
    assert "S.\n\nS.\n\nS." not in rep
    # equal vectors: dropped; different: kept
    assert "S.\n\nT.\n\n::: {.callout" in _report(
        flex, summary_text=["S.", "T."], report=["S.", "T."]
    )
    kept = _report(flex, summary_text=["S.", "T."], report="S.")
    assert "S.\n\nT.\n\nS.\n\n::: {.callout" in kept


def test_module_report_header_numbers(flex):
    p = pc.test_paper(["x"])
    op = module_run(p, flex, summary_text="S.")
    assert module_report(op, header=7.0).startswith("7\n\n")
    assert module_report(op, header=1e5).startswith("1e+05\n\n")
    assert module_report(op, header=2.5).startswith("2.5\n\n")
    assert module_report(op, header="2").startswith("## ")
    assert module_report(op, header=True).startswith("# ")


def test_module_report_two_validations_and_four_authors(flex):
    rep = _report(flex, summary_text="S.")
    assert "This module was developed by A One, B Two, C Three and D Four" in rep
    assert "First details.End text." in rep
    assert rep.endswith(
        "::: {.validation}\nValidation: One.\n:::\n\n::: {.validation}\nValidation: Two.\n:::"
    )


def test_deparse_unicode_like_r():
    assert deparse("͸") == ['"\\u0378"']  # unassigned
    assert deparse("\U000e0080") == ['"\\U{0e0080}"']
    assert deparse("\U0010ffff") == ['"\\U{10ffff}"']
    assert deparse("\u0085") == ['"\\u0085"']
    assert deparse(" ") == ['"\\u2028"']
    # printable: format, private use, emoji, Unicode 15.1 CJK
    for ch in ("​", "﻿", "", "\U0001f600", "⁦", "\U0002ebf0"):
        assert deparse(ch) == [f'"{ch}"']


def test_deparse_factor_columns():
    df = pd.DataFrame(
        {
            "f": pd.Categorical(["b", "a", None]),
            "o": pd.Categorical(["lo", "hi", "lo"], categories=["lo", "hi"], ordered=True),
        }
    )
    out = "".join(deparse(df))  # (ignore R's line breaks)
    assert 'f = structure(c(2L, 1L, NA), levels = c("a", "b"), class = "factor")' in out
    assert 'class = c("ordered", "factor")' in out


def test_scroll_table_follows_r_column_loop():
    # a vector's column has a blank name: R leaves its line breaks alone
    block = scroll_table(["a\nb", "c"])
    assert block.data.iloc[0, 0] == "a\nb"
    assert '"a\\nb"' in table_chunk(block)
    # factors are not character: kept as factors (and class stays before row.names)
    fac = scroll_table(pd.DataFrame({"f": pd.Categorical(["x\ny"])}))
    assert isinstance(fac.data["f"].dtype, pd.CategoricalDtype)
    chunk = scroll_table_qmd(pd.DataFrame({"f": pd.Categorical(["b", "a"])}))
    assert 'class = "data.frame", row.names = c(NA, ' in chunk
    # string dtype survives the replacement
    s = scroll_table(pd.DataFrame({"t": pd.Series(["x\ny", None], dtype="string")}))
    assert str(s.data["t"].dtype) == "string"
    assert s.data["t"].tolist()[0] == "x<br>y"


def test_report_module_run_needs_modules(quiet):
    with pytest.raises(ValueError, match="No modules"):
        report_module_run(pc.demopaper(), [])


def test_report_module_run_paperlist_error_first(quiet, test_module):
    papers = pc.read(
        [
            "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
            "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = report_module_run(papers, [test_module("rp_error"), "marginal"])
    # R: the failed first module leaves a summary table without paper_id, so the
    # next module's summary join fails too, with dplyr's message
    assert out["marginal"].traffic_light == "fail"
    assert out["marginal"].report == "Join columns in `x` must be present in the data."


def test_accuracy_r_logic():
    num = accuracy([1, 0, 2, 0, 1], [1, 1, 0, 0, 0.5])
    assert (num.hits, num.misses, num.false_alarms, num.correct_rejections) == (2, 1, 1, 1)
    empty = accuracy([], [True, False])  # logical(0) & x is logical(0)
    assert (empty.hits, empty.misses) == (0, 0)
    assert empty.sensitivity is None
    # three-valued logic: NA & FALSE is FALSE, NA & TRUE is NA
    na = accuracy([None, False], [False, True])
    assert (na.hits, na.misses, na.false_alarms, na.correct_rejections) == (0, None, 1, None)
    with pytest.raises(TypeError, match="numeric, logical or complex"):
        accuracy(["TRUE"], [True])


def test_validate_na_text_and_key_types(test_module):
    mod = test_module("rp_validate_mod")
    gt = pd.DataFrame(
        {"paper_id": ["1", "2"], "text": pd.Series(["It was significant.", None], dtype="string")}
    )
    out = validate(gt, mod)
    assert out["paper_id"].tolist() == ["1", "2", "zzz"]
    assert out["text"].isna().tolist() == [False, True, False]
    with pytest.raises(TypeError, match="incompatible types"):
        validate(pd.DataFrame({"paper_id": [1, 2], "text": ["It was significant.", "No."]}), mod)


def test_html_sniff_ignores_invalid_utf8(tmp_path):
    f = tmp_path / "a.html"
    f.write_bytes(b'<meta name="generator" content="pandoc" />\n<p>caf\xe9</p>\n')
    assert _html_sniff_kind(str(f)) is None
    g = tmp_path / "b.html"
    g.write_bytes(b"<pre>\n. regress y x\ncaf\xe9\n</pre>\n")
    assert _html_sniff_kind(str(g)) == "stata"


def test_link_and_cap_num_like_r():
    assert link("10.1234/abc", type="doi") == (
        "<a href='https://doi.org/10.1234/abc' target='_blank'>doi.org/10.1234/abc</a>"
    )
    assert link([None], type="doi") == [
        "<a href='https://doi.org/NA' target='_blank'>doi.org/NA</a>"
    ]
    assert link([None, "http://a.b"]) == [None, "<a href='http://a.b' target='_blank'>a.b</a>"]
    assert _cap_num(3.0000000001) == "3"
    assert _cap_num(1e-9) == "0"
    assert _cap_num(99.95) == "100.0"
    assert _cap_num(float("nan")) == "unknown"


def test_render_module_outputs(quiet):
    paper = pc.demopaper()
    outputs = [module_run(paper, "marginal")]
    html = render_module_outputs(outputs, paper)
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "Marginal Significance" in html
    assert render_module_outputs(outputs, paper, "qmd").startswith("---\ntitle: MetaCheck Report")
    assert "# MetaCheck Report" in render_module_outputs(outputs, paper, "md")
    with pytest.raises(ValueError, match="output_format"):
        render_module_outputs(outputs, paper, "pdf")


def test_report_repository_single_module_name(tmp_path, quiet, test_module):
    folder = tmp_path / "study"
    folder.mkdir()
    out = report_repository(
        folder,
        output_file=str(tmp_path / "r.qmd"),
        output_format="qmd",
        modules=test_module("rp_repo"),
    )
    (only,) = out.values()
    assert only.summary_text == "Folder study; local only: TRUE"


def test_report_submodule_is_callable(tmp_path, quiet):
    from pytacheck.report import report as report_mod

    assert callable(report_mod)
    assert callable(report_mod.report)
    out = report_mod(pc.demopaper(), "marginal", str(tmp_path / "r.md"), "md")
    assert out.save_path == str(tmp_path / "r.md")


def test_message_pastes_vectors(capsys):
    from pytacheck.config import verbose
    from pytacheck.utils import message

    old = verbose()
    verbose(True)
    try:
        message(None, "a", [1, 2.5], True, 1e5)
        assert capsys.readouterr().err == "a12.5TRUE1e+05\n"
    finally:
        verbose(old)


def test_datatable_cells_show_numbers_like_the_browser():
    from pytacheck.report.render import _cell_text

    # htmlwidgets writes 16 significant digits; the browser prints Number#toString
    assert _cell_text(0.1 + 0.2) == "0.3"
    assert _cell_text(1e-7) == "1e-7"
    assert _cell_text(1e-5) == "0.00001"
    assert _cell_text(1.5e21) == "1.5e+21"
    assert _cell_text(float("inf")) == ""
    assert _cell_text(2.0) == "2"
    assert _cell_text(True) == "true"
