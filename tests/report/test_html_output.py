"""Recognising rendered statistical output in HTML files (R/html-output.R)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import metacheck as pc
from metacheck.report.html_output import _html_export_r_source, _html_sniff_kind
from metacheck.report.report import report


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("knit_classic.html", "rmd"),
        ("knit_quarto.html", "rmd"),
        ("sourcecode_only.html", "rmd"),
        ("crlf_upper.html", "rmd"),
        ("stata_log.html", "stata"),
        ("plain.html", None),
        ("own_report.html", None),
        ("empty.html", None),
        ("missing.html", None),
    ],
)
def test_html_sniff_kind(html_fixture, name, kind):
    assert _html_sniff_kind(html_fixture(name)) == kind


def test_html_sniff_kind_na():
    assert _html_sniff_kind(None) is None
    assert _html_sniff_kind("") is None


def test_own_report_is_not_output(tmp_path):
    out = tmp_path / "rep.html"
    report(pc.demopaper(), "marginal", out, "html")
    assert _html_sniff_kind(out) is None


def test_html_export_r_source_classic(tmp_path, html_fixture):
    src = tmp_path / "knit_classic.html"
    shutil.copy(html_fixture("knit_classic.html"), src)
    out = _html_export_r_source(src)
    assert out == str(tmp_path / "code" / "knit_classic.R")
    assert Path(out).read_text(encoding="utf-8") == (
        'library(dplyr)\ndat <- read.csv("data.csv")\n\nsummary(dat$x > 2)\n'
    )


def test_html_export_r_source_quarto(tmp_path, html_fixture):
    src = tmp_path / "knit_quarto.html"
    shutil.copy(html_fixture("knit_quarto.html"), src)
    out = _html_export_r_source(src, code_dir_name="recovered")
    text = Path(out).read_text(encoding="utf-8")
    # classic chunks come first, then the syntax-highlighted ones (R's order)
    assert text == "mean(x)\n\nlibrary(ggplot2)\nx <- rnorm(10) # simulate\n"
    assert Path(out).parent.name == "recovered"


def test_html_export_r_source_none(tmp_path, html_fixture):
    src = tmp_path / "plain.html"
    shutil.copy(html_fixture("plain.html"), src)
    assert _html_export_r_source(src) is None
    with pytest.raises(FileNotFoundError, match="File not found"):
        _html_export_r_source(tmp_path / "nope.html")
