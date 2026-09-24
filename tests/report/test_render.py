"""The report renderers: deparse(), Quarto-flavoured markdown to HTML / GFM, the page."""

from __future__ import annotations

import pandas as pd

import pytacheck as pc
from pytacheck.report import emojis, scroll_table
from pytacheck.report.render import (
    TableSlots,
    deparse,
    markdown_to_gfm,
    markdown_to_html,
    table_chunk,
    table_gfm,
)
from pytacheck.report.report import report_html, report_markdown, report_module_run


def test_emojis():
    assert emojis["tl_red"] == "⚠️"
    assert emojis["tl_yellow"] == "\U0001f50d"
    assert list(emojis)[:3] == ["check", "star", "warning"]
    assert len(emojis) == 32


def test_deparse_basics():
    assert deparse([0.1, 0.9]) == ["c(0.1, 0.9)"]
    assert deparse("auto") == ['"auto"']
    assert deparse([None, 200.0]) == ["c(NA, 200)"]
    assert deparse(list(range(1, 11))) == ["1:10"]
    assert deparse([3, 2, 1]) == ["3:1"]
    assert deparse([1, 3]) == ["c(1L, 3L)"]
    assert deparse({"a": 1.0, "b": ["x", None]}) == ['list(a = 1, b = c("x", NA))']
    assert deparse(pd.DataFrame({"a": [1, 2, 3]})) == [
        'structure(list(a = 1:3), class = "data.frame", row.names = c(NA, ',
        "-3L))",
    ]


def test_deparse_list_column_and_factor():
    df = pd.DataFrame({"f": pd.Categorical(["u", "v"]), "l": [[1, 2], ["a"]]})
    out = "\n".join(deparse(df))
    # R: factors deparse with their codes and levels (parity: deparse.df_factor)
    assert 'f = structure(1:2, levels = c("u", "v"), class = "factor")' in out
    assert 'l = list(1:2, "a")' in out


def test_table_chunk_attribute_order():
    # a character column moves class after row.names (R's `[[<-.data.frame`)
    chr_chunk = table_chunk(scroll_table(pd.DataFrame({"a": ["x"]})))
    assert 'row.names = c(NA, -1L), class = "data.frame")' in chr_chunk
    num_chunk = table_chunk(scroll_table(pd.DataFrame({"a": [1.0]})))
    assert 'class = "data.frame", row.names = c(NA, \n-1L))' in num_chunk


def test_markdown_callouts_and_divs():
    md = (
        '::: {.callout-tip title="Learn *More*" collapse="true"}\n\nInside **bold**.\n\n:::\n\n'
        '::: {.callout-note title="Open" collapse="false"}\n\nOpen body\n\n:::\n\n'
        "::: {.callout-warning}\n\nPlain callout\n\n:::\n\n"
        "::: {.validation}\nValidation: text\n:::\n\n"
        "::: {#info}\nInfo\n:::"
    )
    html = markdown_to_html(md)
    assert '<div class="callout callout-tip"><details class="callout-details">' in html
    assert '<summary class="callout-title">Learn <em>More</em></summary>' in html
    assert "<strong>bold</strong>" in html
    assert '<details class="callout-details" open>' in html
    assert '<div class="callout-title">Warning</div>' in html
    assert '<div class="validation">' in html and "<p>Validation: text</p>" in html
    assert '<div id="info">' in html


def test_markdown_code_fences_are_not_divs():
    html = markdown_to_html("```\n:::\n## not a heading\n```\n\n```{r}\nx <- 1\n```")
    assert "<pre><code>:::\n## not a heading\n</code></pre>" in html
    assert "x &lt;- 1" in html


def test_markdown_attributes_and_ids():
    html = markdown_to_html(
        "## Summary\n\n- ok [Title](#title-x){.red}: text\n\n### ✅ Title X {#title-x .green}\n\n"
        "### Same\n\n### Same\n\n[ext](https://example.org)"
    )
    assert '<h2 id="summary">Summary</h2>' in html
    assert '<a href="#title-x" class="red">Title</a>: text' in html
    assert '<h3 id="title-x" class="green">✅ Title X</h3>' in html
    assert '<h3 id="same">Same</h3>' in html and '<h3 id="same-1">Same</h3>' in html
    assert '<a href="https://example.org" target="_blank" rel="noopener">ext</a>' in html


def test_markdown_typography():
    html = markdown_to_html("It's 'quoted' -- and --- \"this\"...")
    assert "It’s ‘quoted’ – and — “this”…" in html


def test_markdown_tabset():
    md = "::: {.panel-tabset}\n\n## a.csv\n\nfirst\n\n## b.csv\n\nsecond\n\n:::"
    html = markdown_to_html(md)
    assert '<div class="panel-tabset">' in html
    assert '<button type="button" role="tab" class="tab-btn active">a.csv</button>' in html
    assert '<div class="tab-pane active" role="tabpanel">' in html
    assert "<p>second</p>" in html
    assert "<h2" not in html


def test_markdown_to_gfm():
    md = (
        "- ✅ [T](#t){.green}: ok  \n\n### ✅ T {#t .green}\n\n"
        '::: {.callout-note title="How It Works" collapse="true"}\n\nBody\n\n:::\n\n'
        "::: {.validation}\nV\n:::"
    )
    out = markdown_to_gfm(md)
    assert "[T](#t): ok" in out
    assert '<a id="t"></a>\n\n### ✅ T' in out
    assert "<details>\n<summary>How It Works</summary>\n\nBody\n\n</details>" in out
    assert '<div class="validation">' in out
    assert "{" not in out


def test_table_gfm():
    rt = scroll_table(pd.DataFrame({"a|b": ["x|y", "z\nw"], "n": [1.5, None]}))
    assert table_gfm(rt).split("\n") == [
        "| a\\|b | n |",
        "| --- | --: |",
        "| x\\|y | 1.5 |",
        "| z<br>w |  |",
    ]


def test_table_slots():
    slots = TableSlots()
    rt = scroll_table(pd.DataFrame({"a": [1]}))
    md = "text\n\n" + slots(rt) + "\n\nmore"
    html = slots.fill(markdown_to_html(md))
    assert '<table class="dataTable display">' in html
    assert "pytacheck-table" not in html


def test_report_html_page():
    paper = pc.demopaper()
    mo = report_module_run(paper, ["marginal"])
    html = report_html(mo, paper)
    assert html.startswith("<!DOCTYPE html>")
    assert f"<title>MetaCheck Report – {paper.title}</title>" in html
    assert '<nav id="TOC"' in html
    assert '<a href="#marginal-significance">' in html  # TOC entry
    assert "Report Created:" in html and "pytacheck" in html
    assert '<div id="info">' in html
    # no leftover Quarto syntax or placeholders
    for leftover in ("{{", ":::", "{.red}", "pytacheck-table", "```{r}"):
        assert leftover not in html
    # sniffers never mistake our own report for deposited output
    assert "metacheck@scienceverse.org" in html


def test_report_markdown():
    paper = pc.demopaper()
    mo = report_module_run(paper, ["marginal"])
    md = report_markdown(mo, paper)
    assert md.startswith("# MetaCheck Report\n\n**To Err is Human")
    assert "## Summary" in md
    assert "<summary>How It Works</summary>" in md
    assert ":::" not in md and "```{r}" not in md


def test_tabset_with_tables_and_balanced_html():
    from pytacheck.report.render import render_blocks

    t1 = scroll_table(pd.DataFrame({"a": range(5)}))
    t2 = scroll_table(pd.DataFrame({"b": ["x"]}))
    blocks = ["::: {.panel-tabset}", "## a.csv", t1, "## b.csv", t2, ":::", "after"]
    slots = TableSlots()
    html = slots.fill(markdown_to_html(render_blocks(blocks, slots)))
    assert html.count('<div class="tab-pane') == 2
    assert html.count('<table class="dataTable display">') == 2
    assert html.count("<div") == html.count("</div>")
    assert html.rstrip().endswith("<p>after</p>")


def test_report_page_is_balanced():
    paper = pc.demopaper()
    mo = report_module_run(paper, ["marginal"])
    html = report_html(mo, paper)
    for tag in ("div", "details", "table", "ul", "nav", "main"):
        assert html.count(f"<{tag}") == html.count(f"</{tag}>"), tag


def test_module_objects_as_modules(tmp_path):
    from pytacheck.modules.marginal import marginal

    mo = report_module_run(pc.demopaper(), [marginal])
    assert list(mo) == ["marginal"]
