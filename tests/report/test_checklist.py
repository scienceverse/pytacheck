"""The checklist report: report_type="checklist".

Port of metacheck's test-report-checklist.R (metacheck 0.4.3.7, 4a61888), plus the
parts that are pytacheck's own: the page is rendered here, with no Pandoc.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import CHECK_COLUMNS, CHECK_STATUSES, ModuleOutput
from metacheck.report.blocks import collapse_section, report_table, report_type, scroll_table
from metacheck.report.checklist import (
    CHECKLIST_RESULTS,
    checklist_checks,
    checklist_html,
    checklist_markdown,
    checklist_qmd,
)
from metacheck.report.render import fold_markdown
from metacheck.report.report import (
    render_module_outputs,
    report,
    report_html,
    report_markdown,
    report_module_run,
    report_qmd,
)


@pytest.fixture(autouse=True)
def _full() -> Iterator[None]:
    report_type("full")
    yield
    report_type("full")


def mo(module: str, title: str, traffic_light: str = "info", **kw) -> ModuleOutput:
    """A module output, as module_run() returns it."""
    own = {k: kw.pop(k) for k in ("summary_text", "report") if k in kw}
    return ModuleOutput(
        module=module, title=title, section="general", traffic_light=traffic_light, extras=kw, **own
    )


def test_statuses_and_columns() -> None:
    assert [r[0] for r in CHECKLIST_RESULTS] == list(CHECK_STATUSES)
    assert CHECK_COLUMNS[:3] == ("status", "title", "section")
    assert report_type("checklist") == "checklist"


def test_checks_of_a_module_with_checks() -> None:
    out = mo(
        "my_check",
        "My Check",
        "red",
        checks=pd.DataFrame(
            {
                "status": ["fix", "fine", "improve"],
                "title": ["A", "B", "C"],
                "found": ["Found A.", "Found B.", None],
                "todo": ["Do A.", "", "Do C."],
            }
        ),
    )
    checks = checklist_checks({"my_check": out})
    assert checks["status"].tolist() == ["fix", "fine", "improve"]
    assert checks["found"].tolist() == ["Found A.", "Found B.", ""]
    assert checks["module"].tolist() == ["my_check"] * 3
    # every column, "" when the module left it out
    assert {"why", "note", "section", "details", "details_title"} <= set(checks.columns)
    assert checks["why"].tolist() == [""] * 3

    # an unknown status is shown for information, with a warning
    bad = mo(
        "my_check",
        "My Check",
        "red",
        checks=pd.DataFrame({"status": ["fix", "great"], "title": ["A", "B"]}),
    )
    with pytest.warns(UserWarning, match="unknown status"):
        checks = checklist_checks([bad])
    assert checks["status"].tolist() == ["fix", "info"]


def test_checks_of_a_module_without_checks_are_one_check() -> None:
    outs = [
        mo("a", "Red", "red", summary_text="Something to fix.", report="The details."),
        mo("b", "Yellow", "yellow", summary_text="Something.", report="Something."),
        mo("c", "Green", "green", summary_text="Fine."),
        mo("d", "Not applicable", "na", summary_text="Nothing to check."),
        mo("e", "Failed", "fail", summary_text="This module failed to run", report="an error"),
        mo("f", "Blue", "blue", summary_text=["One.", "Two."], report=["One.", "Two."]),
    ]
    checks = checklist_checks(outs)
    assert checks["status"].tolist() == ["fix", "improve", "fine", "na", "fix", "info"]
    assert checks["title"].tolist()[:2] == ["Red", "Yellow"]
    assert checks["found"][0] == "Something to fix."
    # the report becomes the details, unless it is the summary itself
    assert checks["details"][0] == "The details."
    assert checks["details"][1] == ""
    assert checks["found"][4] == "This module failed to run: an error"
    # a summary of several parts is joined
    assert checks["found"][5] == "One.\n\nTwo."
    assert len(checklist_checks([])) == 0
    assert list(checklist_checks([]).columns) == [*CHECK_COLUMNS, "module"]


def test_checks_take_a_dict_a_list_or_one_output() -> None:
    a = mo("a", "A", "green", summary_text="Fine.")
    b = mo("b", "B", "red", summary_text="Not fine.")
    assert checklist_checks({"a": a, "b": b})["module"].tolist() == ["a", "b"]
    assert checklist_checks([a, b])["module"].tolist() == ["a", "b"]
    assert checklist_checks(a)["module"].tolist() == ["a"]


def test_checklist_qmd() -> None:
    logo = "src/metacheck/report/assets/logo.png"
    out = mo(
        "my_check",
        'My "Check"',
        "red",
        intro="This report checks a package.",
        logo=logo,
        checks=pd.DataFrame(
            {
                "status": ["fix", "fine", "na"],
                "title": ["No junk", "Size", "ORCIDs"],
                "section": ["2 Files", "1.1", ""],
                "found": ["2 junk files.", "1 MB.", "No ORCID."],
                "todo": ["Remove them.", "", ""],
                "details": ["The junk table.", "The size table.", ""],
                "details_title": ["Show the files", "", ""],
            }
        ),
    )
    paper = pc.test_paper()
    paper.info = paper.info.assign(title=pd.Series(["my_package"], dtype="string"))
    txt = checklist_qmd([out], paper)

    # the template's tokens are all filled in
    assert "{{" not in txt
    # the module's title (one module), escaped in the page title and the HTML
    assert 'pagetitle: "My \\"Check\\": my_package"' in txt
    assert '<h1>My "Check"</h1>' in txt
    assert "This report checks a package." in txt
    # counts, with links to the sections that have checks
    assert '<a class="count fix" href="#fix"><b>1</b>' in txt
    assert '<span class="count improve zero"><b>0</b>' in txt
    assert "## To fix [(1)]{.n} {#fix}" in txt
    assert "## To improve" not in txt
    # what was found and what to do on separate lines
    assert "::: {.label}\nWhat we found\n:::\n\n::: {.text}\n2 junk files.\n:::" in txt
    assert "::: {.text .todo}\nRemove them.\n:::" in txt
    assert "[2 Files]{.where}" in txt
    # the details of a check that needs work fold under it; of a fine one, under
    # "More detail", by its title
    assert "<details><summary>Show the files</summary>" in txt
    assert "## More detail" in txt
    assert "<details><summary>Size</summary>" in txt
    # metacheck's logo and the module's: two images, as data: URIs
    assert txt.count('alt="logo"') == 2
    assert "data:font/ttf;base64," in txt
    # fenced divs are balanced
    assert len(re.findall(r"(?m)^::: \{", txt)) == len(re.findall(r"(?m)^:::$", txt))

    # several modules: metacheck's title
    other = mo("other", "Other", "green", summary_text="Fine.")
    assert "<h1>MetaCheck Report</h1>" in checklist_qmd([out, other], paper)


def test_title_and_subtitle() -> None:
    a = mo("a", "Only module", "green", summary_text="Fine.")
    assert "<h1>Only module</h1>" in checklist_qmd([a])
    txt = checklist_qmd([a], title="Given", subtitle="Sub <title>")
    assert "<h1>Given</h1>" in txt and "Sub &lt;title&gt;" in txt
    assert 'pagetitle: "Given: Sub <title>"' in txt
    # a repository name is the subtitle when nothing else is
    assert 'class="subtitle">my_repo<' in checklist_html([a], repository="my_repo")
    paper = pc.test_paper()
    doi = paper.info.assign(doi=pd.Series(["10.1234/abc"], dtype="string"))
    paper.info = doi
    assert "DOI 10.1234/abc" in checklist_qmd([a], paper)


def test_file_tree_once_after_the_module_sections() -> None:
    data_check = mo(
        "data_check",
        "Data Check",
        "yellow",
        summary_text="Some data files.",
        report=["The data files.", "#### Data Tree", "Where the files sit.", "TREE-LINES"],
    )
    repro = mo(
        "reproducibility_check",
        "Reproducibility Check",
        "green",
        summary_text="The code ran.",
        report="Ran.",
        checklist_section="The scripts.",
    )
    other = mo("other", "Other", "green", summary_text="Fine.", report="Other details.")
    txt = checklist_qmd([data_check, repro, other], pc.test_paper())

    # in a section of its own, after the reproducibility section, before "More detail"
    tree_at = txt.index("## Files in the repository {#files}")
    repro_at = txt.index("## Reproducibility Check {#section-reproducibility_check}")
    more_at = txt.index("## More detail")
    assert repro_at < tree_at < more_at
    assert "Where the files sit." in txt
    # shown once: data_check's folded details keep the rest of its report only
    assert txt.count("TREE-LINES") == 1
    assert "#### Data Tree" not in txt
    assert "The data files." in txt

    # without a repository, no section
    assert "Files in the repository" not in checklist_qmd([repro, other], pc.test_paper())


def test_a_module_with_a_section_shows_its_details_there() -> None:
    sec = mo(
        "sec",
        "Sectioned",
        "red",
        summary_text="Look below.",
        report=["Long", "report"],
        checklist_section=["Paragraph one.", "Paragraph two."],
    )
    checks = checklist_checks([sec])
    assert checks["details"].tolist() == [""]
    assert checks["note"][0] == "See the section [Sectioned](#section-sec) below for the details."
    txt = checklist_qmd([sec])
    assert "## Sectioned {#section-sec}\n\nParagraph one.\n\nParagraph two." in txt
    assert "<details><summary>All details of the Sectioned</summary>" in txt


def test_collapse_section_folds_in_a_checklist_report() -> None:
    # R writes <details> in the module's output under report_type("checklist");
    # pytacheck's module output does not depend on the report type, and the
    # report folds the callout when it is rendered
    callout = collapse_section(["One.", "Two."], "How It Works")
    assert isinstance(callout, str)
    assert fold_markdown(callout) == (
        "<details><summary>How It Works</summary>\n\nOne.\n\nTwo.\n\n</details>\n"
    )
    out = mo("fold", "Folding", "yellow", summary_text="Short.", report=["First.", callout])
    txt = checklist_qmd([out])
    assert "<details><summary>How It Works</summary>\n\nOne.\n\nTwo.\n\n</details>" in txt
    assert "callout" not in txt.split("</style>")[-1]


def test_static_table_cell_that_starts_like_a_list_item_stays_text() -> None:
    report_type("simple")
    tbl = pd.DataFrame({"Section": ["1. General information", "- a dash", "2) two", "1.5 cm"]})
    html = report_table(tbl).to_html()
    assert "1. General information" in html
    assert "- a dash" in html
    assert "2) two" in html
    assert "<td>1.5 cm</td>" in html


def test_tables_in_a_checklist_are_static() -> None:
    tbl = pd.DataFrame({"a": [1, 2]})
    out = mo(
        "tab",
        "Tabled",
        "yellow",
        checks=pd.DataFrame(
            {
                "status": ["improve"],
                "title": ["T"],
                "found": ["Found."],
                "details": [scroll_table(tbl)],
            }
        ),
    )
    page = checklist_html([out])
    assert 'class="dt-static"' in page
    assert "<script" not in page


def test_html_page_is_static_and_balanced() -> None:
    paper = pc.test_paper("Example text with p = .03 and p = .5.")
    outputs = report_module_run(paper, ["stat_p_exact", "stat_p_nonsig"])
    page = report_html(outputs, paper, "checklist")
    # static, light only, in the website's font
    assert "html-widget" not in page
    assert "prefers-color-scheme" not in page
    assert "<script" not in page
    assert 'font-family: "Cabin"' in page
    assert "data:font/ttf;base64," in page
    assert 'class="counts"' in page
    assert "Exact" in page
    # every <div> is closed exactly once
    assert len(re.findall(r"<div\b", page)) == len(re.findall(r"</div>", page))
    # no Pandoc syntax left over
    assert "{.tag" not in page and "::: {" not in page and "{#" not in page
    assert page.startswith("<!DOCTYPE html>")
    assert checklist_html(outputs, paper) == page, "report_html() is checklist_html()"


def test_report_writes_the_checklist(tmp_path) -> None:
    paper = pc.test_paper("Example text with p = .03 and p = .5.")
    prev = report_type()
    res = report(
        paper,
        ["stat_p_exact", "stat_p_nonsig"],
        tmp_path / "r.html",
        "html",
        report_type="checklist",
    )
    assert report_type() == prev
    text = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert 'class="counts"' in text and "Exact" in text
    assert res.save_path == str(tmp_path / "r.html")

    qmd = report(paper, ["stat_p_exact"], tmp_path / "r.qmd", "qmd", report_type="checklist")
    assert (tmp_path / "r.qmd").read_text(encoding="utf-8").startswith("---\npagetitle:")
    assert qmd.save_path == str(tmp_path / "r.qmd")

    md = report(paper, ["stat_p_exact"], tmp_path / "r.md", "md", report_type="checklist")
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert text.startswith("# ") and "{.tag" not in text
    assert md.save_path == str(tmp_path / "r.md")


def test_report_type_setting_decides_the_format() -> None:
    paper = pc.test_paper("Example text with p = .03.")
    outputs = report_module_run(paper, ["stat_p_exact"])
    report_type("checklist")
    assert 'class="counts"' in report_html(outputs, paper)
    assert report_qmd(outputs, paper).startswith("---\npagetitle:")
    assert "# " in report_markdown(outputs, paper)
    assert 'class="counts"' in render_module_outputs(outputs, paper, "html")


def test_markdown_is_plain() -> None:
    a = mo("a", "A", "red", summary_text="Bad.", report="Details of A.")
    md = checklist_markdown([a])
    assert md.startswith("# A\n")
    assert "{.tag" not in md and "{#" not in md
    assert "Bad." in md


def test_modules_without_the_new_fields_are_unchanged() -> None:
    out = mo("plain", "Plain", "green", summary_text="Fine.")
    assert out.checks is None and out.checklist_section is None
    assert out.keys() == [*ModuleOutput._FIELDS]
    # they are optional extras of an output that sets them
    with_checks = mo("c", "C", checks=pd.DataFrame({"status": ["fine"], "title": ["T"]}))
    assert with_checks.checks is not None
    assert with_checks.keys()[-1] == "checks"


def test_a_failing_status_warning_does_not_break_the_page() -> None:
    bad = mo(
        "m",
        "M",
        "red",
        checks=pd.DataFrame({"status": ["nope"], "title": ["T"], "found": ["f"]}),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        page = checklist_html([bad])
    assert any("unknown status" in str(w.message) for w in caught)
    assert "For information" in page
