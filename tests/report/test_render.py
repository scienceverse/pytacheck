"""The report renderers: table blocks, Quarto-flavoured markdown to HTML / GFM, the page."""

from __future__ import annotations

import pandas as pd

import metacheck as pc
from metacheck.report import emojis, scroll_table
from metacheck.report.render import (
    TableSlots,
    markdown_to_gfm,
    markdown_to_html,
    table_chunk,
    table_gfm,
)
from metacheck.report.report import report_html, report_markdown, report_module_run


def test_emojis():
    assert emojis["tl_red"] == "⚠️"
    assert emojis["tl_yellow"] == "\U0001f50d"
    assert list(emojis)[:3] == ["check", "star", "warning"]
    assert len(emojis) == 32


def test_r_literal_quotes_values_as_r_code():
    # what "unused argument" messages and RegCheck's list cells show
    from metacheck._r.base import r_literal

    assert r_literal([0.1, 0.9]) == "c(0.1, 0.9)"
    assert r_literal("auto") == '"auto"'
    assert r_literal([None, 200.0]) == "c(NA, 200)"
    assert r_literal(list(range(1, 11))) == "1:10"
    assert r_literal([3, 2, 1]) == "3:1"
    assert r_literal([1, 3]) == "c(1L, 3L)"
    assert r_literal([None, None]) == "c(NA, NA)"
    assert r_literal(pd.Series([None, None], dtype="string")) == "c(NA, NA)"
    assert r_literal(["a", None]) == 'c("a", NA)'
    assert r_literal({"a": 1.0, "b c": ["x", None]}) == 'list(a = 1, "b c" = c("x", NA))'
    assert r_literal([[1, 2], "a"]) == 'list(1:2, "a")'
    assert r_literal(['q"\\\n\x01\u0085']) == '"q\\"\\\\\\n\\001\\u0085"'
    assert r_literal(None) == "NULL"
    assert r_literal([]) == "logical(0)"


def test_table_chunk_is_a_raw_html_block():
    # D75: the .qmd holds the table as HTML, not as R code that rebuilds it
    chunk = table_chunk(scroll_table(pd.DataFrame({"a_b": ["x\ny", "<i>"], "n": [1.5, 2.0]})))
    assert chunk.startswith('\n```{=html}\n<div class="datatables"><div class="dt-scroll">')
    assert chunk.endswith("</table></div></div>\n```\n")
    assert "<th>a_<wbr>b</th>" in chunk and '<th class="dt-right">n</th>' in chunk
    assert "<td>x<br>y</td>" in chunk and '<td class="dt-right">1.5</td>' in chunk
    assert "<td><i></td>" in chunk  # escape = FALSE, as in metacheck
    escaped = table_chunk(scroll_table(pd.DataFrame({"a": ["<i>"]}), escape=True))
    assert "<td>&lt;i&gt;</td>" in escaped and "```{r}" not in escaped


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
    from metacheck.report.render import render_blocks

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
    from metacheck.modules.marginal import marginal

    mo = report_module_run(pc.demopaper(), [marginal])
    assert list(mo) == ["marginal"]
