"""report_type(): the simple (static, email-safe) and brief (to-do list) reports.

Port of metacheck's report_type() tests (test-report-helpers.R, test-report.R,
PR #463: 029bf63, c9a0278, dd87807, 7208868).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import metacheck as pc
from metacheck.report import blocks
from metacheck.report.blocks import (
    _report_is_brief,
    _report_is_static,
    collapse_section,
    report_table,
    report_type,
    scroll_table,
)
from metacheck.report.brief import FEEDBACK, flagged_bullets, validation_table
from metacheck.report.render import STATIC_MAX_ROWS, static_markdown, table_chunk
from metacheck.report.report import (
    module_report,
    render_module_outputs,
    report,
    report_html,
    report_markdown,
    report_module_run,
    report_qmd,
    report_repository,
)


@pytest.fixture(autouse=True)
def _full() -> Iterator[None]:
    """Every test starts and ends with the default report type."""
    report_type("full")
    yield
    report_type("full")


@pytest.fixture(scope="module")
def p_values() -> tuple[pc.Paper, object]:
    paper = pc.test_paper(
        ["Example text with p = .03 and p = .5. This was marginally significant (p = 0.06)."]
    )
    return paper, report_module_run(paper, ["stat_p_exact", "stat_p_nonsig", "marginal"])


def test_report_type_gets_and_sets_the_default() -> None:
    assert report_type() == "full"
    assert report_type("simple") == "simple"
    assert report_type() == "simple"
    # case-insensitive, and only the first of several is used
    assert report_type(["FULL", "simple"]) == "full"
    assert report_type() == "full"
    assert report_type("brief") == "brief"
    assert report_type("simple_brief") == "simple_brief"
    with pytest.raises(ValueError, match="'full', 'brief', 'simple' or 'simple_brief'"):
        report_type("fancy")
    # a bad value never changes the setting
    report_type("simple")
    with pytest.raises(ValueError):
        report_type("fancy")
    assert report_type() == "simple"
    assert blocks.REPORT_TYPES == ("full", "brief", "simple", "simple_brief")
    assert pc.report_type is report_type


def test_report_is_static_and_brief() -> None:
    assert [_report_is_static(t) for t in blocks.REPORT_TYPES] == [False, False, True, True]
    assert [_report_is_brief(t) for t in blocks.REPORT_TYPES] == [False, True, False, True]


def test_flagged_bullets() -> None:
    assert flagged_bullets(SimpleNamespace(report=None)) == []
    assert flagged_bullets(SimpleNamespace(report="Nothing to see here.")) == []
    # a table in the report: one bullet per row, one clause per column
    tbl = pd.DataFrame({"Text": ["sentence one", "sentence two"], "Section": ["Intro", "Results"]})
    bullets = flagged_bullets(SimpleNamespace(report=["Some prose.", scroll_table(tbl)]))
    assert len(bullets) == 2
    assert "sentence one" in bullets[0] and "Intro" in bullets[0]
    assert "sentence two" in bullets[1] and "Results" in bullets[1]


def test_flagged_bullets_group_rows_that_share_a_value() -> None:
    # a column whose value repeats is the item; the others are quoted under it
    tbl = pd.DataFrame(
        {
            "Sentence": ["S one.", "S one.", "S one.", "S one.", "S two."],
            "p": ["p = .5", "p = .6", "p = .7", "p = .8", "p = .9"],
            "id": [1, 2, 3, 4, 5],
        }
    )
    bullets = flagged_bullets(SimpleNamespace(report=scroll_table(tbl)))
    assert bullets[0].startswith("- 4 items: **Sentence:** S one.")
    assert "  > p = .5" in bullets[0] and "  > ...and 1 more." in bullets[0]
    assert "id" not in bullets[0]  # an id column is never quoted
    assert bullets[1].startswith("- 1 item: **Sentence:** S two.")
    assert "following sentence:\n  > p = .9" in bullets[1]


def test_report_table_static() -> None:
    table = pd.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]})
    assert report_table(table).static is False
    html = report_table(table, static=True).to_html()
    assert 'class="dt-static"' in html and "<script" not in html and "dataTable" not in html
    # the default follows report_type()
    report_type("simple")
    assert report_table(table).static is True
    assert report_table(table, static=False).static is False
    # escape = FALSE passes HTML through; escape = TRUE shows it as text
    assert "a<br>b" in report_table(pd.DataFrame({"x": ["a<br>b"]})).to_html()
    unsafe = report_table(pd.DataFrame({"x": ["<script>alert(1)</script>"]}), escape=True)
    assert "&lt;script&gt;" in unsafe.to_html() and "<script>alert" not in unsafe.to_html()
    # widths as a <colgroup>
    html = report_table(pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}), [100, 0.5]).to_html()
    assert '<col style="width:100px">' in html and '<col style="width:50%">' in html
    # within maxrows: no scroll box
    assert "max-height" not in report_table(pd.DataFrame({"x": [1, 2, 3]}), maxrows=10).to_html()
    # over maxrows: a scroll box, but every row is there
    html = report_table(pd.DataFrame({"x": range(50)}), maxrows=10).to_html()
    assert "max-height:28em" in html
    assert html.count("<tr>") == 51
    # a huge table is cut, with a note
    huge = pd.DataFrame({"x": range(STATIC_MAX_ROWS + 100)})
    html = report_table(huge, maxrows=10).to_html()
    assert f"Showing the first {STATIC_MAX_ROWS} of {STATIC_MAX_ROWS + 100} rows" in html
    assert html.count("<tr>") == STATIC_MAX_ROWS + 1
    # table_chunk(static=True) writes the same static table in a raw HTML block
    chunk = table_chunk(scroll_table(table), static=True)
    assert chunk.lstrip().startswith("```{=html}\n") and 'class="dt-static"' in chunk


def test_static_markdown_opens_callouts_and_flattens_tabsets() -> None:
    callout = collapse_section("hello", title="How It Works")
    assert isinstance(callout, str)
    obs = static_markdown(callout)
    # no title attribute (plain Pandoc would hide it) and never collapsed
    assert 'title="How It Works"' not in obs and 'collapse="true"' not in obs
    assert "**How It Works**" in obs and "callout-tip" in obs
    assert "callout-warning" in static_markdown(str(collapse_section("x", callout="warning")))
    tabs = "::: {.panel-tabset}\n\n## A\n\nfirst\n\n## B\n\nsecond\n\n:::\n"
    flat = static_markdown(tabs)
    assert "panel-tabset" not in flat
    assert "##### A" in flat and "##### B" in flat and "first" in flat and "second" in flat


def test_simple_html_is_static(p_values) -> None:
    paper, mo = p_values
    page = report_html(mo, paper, "simple")
    assert "<script" not in page and "onclick" not in page
    assert "panel-tabset" not in page and "theme-toggle" not in page
    assert "Exact" in page and 'class="dt-static"' in page
    assert '<div id="TOC" class="toc">' in page
    # every module is there, as in the full report
    assert "Marginal Significance" in page and "Non-Significant P Value" in page
    # the full report has its script
    assert "<script" in report_html(mo, paper)
    # report_type() is the default
    report_type("simple")
    assert report_html(mo, paper) == page


def test_simple_qmd_is_an_r_markdown_document(p_values) -> None:
    paper, mo = p_values
    qmd = report_qmd(mo, paper, report_type="simple")
    assert qmd.startswith('---\ntitle: MetaCheck Report\nsubtitle: "Test Paper"\noutput:\n')
    assert "html_document" in qmd and "    toc: true" in qmd
    assert '```{=html}\n<div class="dt-static-wrap">' in qmd
    assert "<script" not in qmd and 'collapse="true"' not in qmd
    assert qmd.rstrip().endswith("</div>")


def test_brief_report_lists_only_what_needs_attention(p_values) -> None:
    paper, mo = p_values
    lights = {k: m.traffic_light for k, m in mo.items()}
    assert lights["marginal"] == "red" and lights["stat_p_exact"] == "green"
    qmd = report_qmd(mo, paper, report_type="brief")
    assert "    toc: false" in qmd
    assert "::: {.legend}" in qmd and "Legend: ⚠️ possible problems detected" in qmd
    assert "Marginal Significance" in qmd and "Exact P-Values" not in qmd
    # the to-do items, from the report's tables
    assert "::: {.mc-summary-todo}\n- **Text:** Example text" in qmd
    # no module sections: the validation of the flagged modules, and where to send feedback
    assert "## Results Modules" not in qmd and "View detailed feedback" not in qmd
    assert "## Validation" in qmd and FEEDBACK in qmd
    assert qmd.index("Non-Significant P Value Check</td>") < qmd.index("Marginal Significance</td>")
    html = report_html(mo, paper, "brief")
    assert '<nav class="toc"' not in html and "page-layout no-toc" in html
    assert '<div class="legend">' in html
    assert "<script" in html  # brief, not simple
    simple_brief = report_html(mo, paper, "simple_brief")
    assert "<script" not in simple_brief and "## Validation" not in simple_brief
    assert "Validation details" in simple_brief
    md = report_markdown(mo, paper, "brief")
    assert "- **Text:** Example text" in md and "View detailed feedback" not in md


def test_brief_report_with_nothing_flagged() -> None:
    paper = pc.test_paper(["Nothing to see."])
    mo = report_module_run(paper, ["marginal"])
    html = report_html(mo, paper, "brief")
    assert "Nothing flagged -- no modules reported a red, yellow, or fail status." in html
    assert "Validation details" not in html and "please email us" in html


def test_validation_table() -> None:
    out = validation_table([SimpleNamespace(module="marginal", title="Marginal Significance")])
    assert out.startswith("## Validation\n")
    assert "<td>Marginal Significance</td><td>In a sample of 51 papers" in out
    # a module without a validation says so
    out = validation_table([SimpleNamespace(module="code_check", title="Code Check")])
    assert "<td>Code Check</td><td>No validation information yet.</td>" in out


def test_report_writes_each_type(tmp_path: Path) -> None:
    paper = pc.test_paper(["This was marginally significant (p = 0.06)."])
    out = tmp_path / "r.html"
    report(paper, "marginal", out, "html", report_type="simple")
    assert "<script" not in out.read_text(encoding="utf-8")
    # report_type is not left changed by report()
    assert report_type() == "full"
    report(paper, "marginal", out, "html", report_type="brief")
    assert "Validation details" in out.read_text(encoding="utf-8")
    report(paper, "marginal", tmp_path / "r.qmd", "qmd", report_type="simple")
    assert "html_document" in (tmp_path / "r.qmd").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="report_type must be one of"):
        report(paper, "marginal", out, "html", report_type="fancy")
    with pytest.raises(ValueError, match="rendered by pytacheck"):
        report(paper, "marginal", out, "html", renderer="quarto", report_type="simple")
    # the session's default
    report_type("simple_brief")
    report(paper, "marginal", out, "html")
    page = out.read_text(encoding="utf-8")
    assert "<script" not in page and "Validation details" in page


def test_report_repository_passes_report_type(tmp_path: Path) -> None:
    folder = tmp_path / "repo2"
    folder.mkdir()
    (folder / "data.csv").write_text("id,x\n1,2\n", encoding="utf-8")
    out = tmp_path / "r.qmd"
    report_repository(
        folder, output_file=out, output_format="qmd", report_type="simple", modules=["repo_check"]
    )
    assert "html_document" in out.read_text(encoding="utf-8")


def test_render_module_outputs_report_type(p_values) -> None:
    paper, mo = p_values
    assert "<script" not in render_module_outputs(mo, paper, report_type="simple")
    md = render_module_outputs(mo, paper, "md", report_type="brief")
    assert "## Validation" in md


def test_module_heading_slug_keeps_only_safe_characters(tmp_path: Path) -> None:
    # metacheck's slug kept every character but spaces, so a title with a
    # colon, a slash or brackets made an anchor Pandoc rewrote
    src = (
        "from metacheck.module import module\n\n\n"
        '@module(title="P-Values: Exact (and/or) Rounded?", description="d", keywords=["general"])\n'
        "def rp_slug(paper):\n"
        '    return {"traffic_light": "green", "summary_text": "ok"}\n'
    )
    path = tmp_path / "rp_slug.py"
    path.write_text(src, encoding="utf-8")
    from metacheck.module import module_run

    op = module_run(pc.test_paper(["x"]), str(path))
    assert re.search(r"\{#p-values-exact-andor-rounded \.green\}", module_report(op))


def test_how_it_works_links_the_manual_chapter_of_metacheck_modules(tmp_path: Path) -> None:
    from metacheck.module import module_run

    rep = module_report(module_run(pc.test_paper(["x"]), "stat_p_exact"))
    assert (
        "Read more in the [metacheck manual]"
        "(https://www.scienceverse.org/metacheck_book/chapters/mod-stat-p-exact.html)."
    ) in rep
    path = tmp_path / "rp_own.py"
    path.write_text(
        "from metacheck.module import module\n\n\n"
        '@module(title="Own", description="d", keywords=["general"])\n'
        "def rp_own(paper):\n"
        '    return {"traffic_light": "green", "summary_text": "ok"}\n',
        encoding="utf-8",
    )
    # a module of your own has no chapter in the manual
    assert "metacheck manual" not in module_report(module_run(pc.test_paper(["x"]), str(path)))
