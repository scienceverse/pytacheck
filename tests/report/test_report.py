"""Port of tests/testthat/test-report.R (report(), module_report(), report_module_run(), report_qmd())."""

from __future__ import annotations

import importlib
import re
import warnings
from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError, ModuleOutput, module_info, module_run
from pytacheck.report import report as report_pkg
from pytacheck.report.report import (
    DEFAULT_MODULES,
    ReportList,
    ReportOutput,
    module_report,
    report,
    report_module_run,
    report_qmd,
    report_repository,
)

PSYCHSCI = (
    Path(__file__).resolve().parents[2] / "upstream/metacheck/tests/testthat/fixtures/psychsci"
)


def test_exports(tmp_path, demo):
    import pytacheck.report

    # pytacheck.report.report is the submodule (the CLI reads report_mod.report)
    # and calling it calls report()
    assert report_pkg.report is report
    assert callable(report_pkg)
    rep = report_pkg(demo, "marginal", tmp_path / "s.md", "md")
    assert rep.save_path == str(tmp_path / "s.md")
    assert pytacheck.report.report_module_run is report_module_run
    # the subpackage is callable, so pc.report(...) works whichever it resolves to
    rep = pytacheck.report(demo, "marginal", tmp_path / "r.md", "md")
    assert rep.save_path == str(tmp_path / "r.md")


def test_errors(tmp_path, demo):
    output_file = tmp_path / "report.qmd"
    # paper not a paper or paperlist
    with pytest.raises(TypeError, match="The paper argument must be a paper object"):
        report(1, "marginal", output_file, "qmd")
    # non-existent module
    with pytest.raises(ModuleError, match="notamodule"):
        report(demo, "notamodule", output_file, "qmd")
    # bad output_file path
    with pytest.raises(ValueError, match="output_file"):
        report(demo, "marginal", "not/a/path/file.html", "qmd")
    # bad format
    with pytest.raises(ValueError, match="output_format"):
        report(demo, "marginal", output_file, "pdf")
    # format case
    rep = report(demo, "marginal", output_file, "QMD")
    assert rep.save_path == str(output_file)
    assert output_file.read_text(encoding="utf-8").startswith("---\ntitle: MetaCheck Report")


def test_bad_paper_without_output_file_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError, match="paper object"):
        report(1, "marginal")
    assert list(tmp_path.iterdir()) == []


def test_rendering_error(tmp_path, demo, monkeypatch):
    """A render failure saves the qmd, warns, and returns the qmd path."""
    rr = importlib.import_module("pytacheck.report.report")

    def boom(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(rr, "report_html", boom)
    output_file = tmp_path / "rep.html"
    with pytest.warns(UserWarning, match="There was an error rendering your report"):
        rep = report(demo, "marginal", output_file, "html")
    exp = str(output_file)[: -len("html")] + "qmd"
    assert rep.save_path == exp
    assert Path(exp).exists()


def test_quarto_renderer_missing(tmp_path, demo, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    output_file = tmp_path / "rep.html"
    with pytest.warns(UserWarning, match="Quarto is not installed"):
        rep = report(demo, "marginal", output_file, "html", renderer="quarto")
    assert rep.save_path.endswith("rep.qmd")


def test_report_return_list(tmp_path, demo, test_module):
    modules = ["marginal", test_module("rp_no_details")]
    paper_report = report(demo, modules, tmp_path / "r.qmd", "qmd")
    assert isinstance(paper_report, ReportOutput)
    assert set(paper_report) == set(modules)
    assert isinstance(paper_report["marginal"], ModuleOutput)
    assert paper_report["marginal"].module == "marginal"
    assert paper_report.paper is demo
    assert str(paper_report) == str(tmp_path / "r.qmd")


def test_report_paperlist(tmp_path):
    paper = pc.read([PSYCHSCI / "0956797613520608.json", PSYCHSCI / "0956797614522816.json"])
    modules = ["marginal"]
    output_file = tmp_path / "_x.qmd"
    paper_report = report(paper, modules, output_file, "qmd")
    assert isinstance(paper_report, ReportList)
    assert list(paper_report) == paper.names
    for pid, rep in paper_report.items():
        assert list(rep) == modules
        # one path given: each paper ID is prefixed to its file name
        assert rep.save_path == str(tmp_path / f"{pid}_x.qmd")
        assert Path(rep.save_path).exists()
    assert list(paper_report.save_path) == paper.names
    # U130: a name without a leading separator gets one (R: "<id>x.qmd")
    named = report(paper, modules, tmp_path / "x.qmd", "qmd")
    assert [r.save_path for r in named.values()] == [
        str(tmp_path / f"{pid}_x.qmd") for pid in paper.names
    ]

    # single reports match
    pr1 = report(paper[[0]], modules, tmp_path / "one.qmd", "qmd")
    pr2 = report(paper[1], modules, tmp_path / "two.qmd", "qmd")
    for a, b in ((paper_report[paper.names[0]], pr1), (paper_report[paper.names[1]], pr2)):
        assert a["marginal"].summary_text == b["marginal"].summary_text
        pd.testing.assert_frame_equal(a["marginal"].table, b["marginal"].table)

    # a vector of output files
    files = [tmp_path / "a.qmd", tmp_path / "b.qmd"]
    report(paper, modules, files, "qmd")
    assert all(f.exists() for f in files)


def test_report_paperlist_error_is_a_warning(tmp_path, monkeypatch):
    paper = pc.read([PSYCHSCI / "0956797613520608.json", PSYCHSCI / "0956797614522816.json"])
    rr = importlib.import_module("pytacheck.report.report")

    calls = {"n": 0}
    real = rr.report_module_run

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("nope")
        return real(*args, **kwargs)

    monkeypatch.setattr(rr, "report_module_run", flaky)
    with pytest.warns(UserWarning, match="Error in 0956797613520608"):
        out = report(paper, "marginal", tmp_path / "_r.qmd", "qmd")
    assert out[paper.names[0]] is None
    assert out[paper.names[1]] is not None
    assert out.save_path[paper.names[0]] is None


def test_render_formats(tmp_path, demo):
    for fmt in ("qmd", "html", "md"):
        out = tmp_path / f"report.{fmt}"
        rep = report(demo, ["marginal"], out, fmt)
        assert rep.save_path == str(out)
        text = out.read_text(encoding="utf-8")
        assert "Marginal Significance" in text
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<h3 id="marginal-significance" class="red">' in html
    assert "<script>" in html and "<style>" in html
    assert "src=" not in html  # self-contained
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| Text | Section Header |" in md
    assert "{.red}" not in md and "{#" not in md


def test_default_output_file(tmp_path, monkeypatch, demo):
    monkeypatch.chdir(tmp_path)
    rep = report(demo, "marginal", output_format="md")
    assert rep.save_path == f"{demo.paper_id}_report.md"
    assert (tmp_path / rep.save_path).exists()


def test_default_modules_skip_unported(tmp_path, demo):
    """Default modules that pytacheck has not ported yet are skipped with a warning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        rep = report(demo, output_file=tmp_path / "r.qmd", output_format="qmd")
    available = [m for m in DEFAULT_MODULES if m in rep]
    assert "marginal" in available
    skipped = [str(x.message) for x in w if "not available in pytacheck" in str(x.message)]
    assert len(skipped) + len(rep) == len(DEFAULT_MODULES)


def test_report_pass_args(tmp_path, demo, test_module):
    chained = test_module("rp_chained")
    modules = ["marginal", chained]
    args = {chained: {"extra": " Look for me in the text!", "irrelevant": None}}
    with pytest.warns(UserWarning, match="Error in"):
        # an unknown argument makes that module fail, as in R
        r = report(demo, modules, tmp_path / "r.qmd", "qmd", args=args)
    assert r[chained].traffic_light == "fail"
    args = {chained: {"extra": " Look for me in the text!"}}
    r = report(demo, modules, tmp_path / "r.qmd", "qmd", args=args)
    qmd_txt = Path(r.save_path).read_text(encoding="utf-8")
    assert "Look for me in the text!" in qmd_txt
    # make sure marginal ran
    assert "(#marginal-significance){.red}" in qmd_txt


def test_detected(tmp_path, demo):
    paper = demo.copy()
    text = paper.text.copy()
    text.loc[0, "text"] = "This effect approached significance."
    paper.text = text
    qmd = tmp_path / "r.qmd"
    rep = report(paper, ["marginal"], output_file=qmd, output_format="qmd")
    assert rep.save_path == str(qmd)
    assert rep["marginal"].summary_text.startswith("You described 3 effects")
    html = tmp_path / "r.html"
    report(paper, ["marginal"], output_file=html, output_format="html")
    assert "approached significance" in html.read_text(encoding="utf-8")


def test_module_report(demo):
    with pytest.raises(TypeError):
        module_report()  # type: ignore[call-arg]
    module_output = module_run(demo, "marginal")
    rep = module_report(module_output)
    assert re.match(r"^### \S* Marginal Significance", rep)
    rep = module_report(module_output, header=4)
    assert re.match(r"^#### \S* Marginal Significance", rep)
    rep = module_report(module_output, header="Custom header")
    assert rep.startswith("Custom header")
    rep = module_report(module_output, header=0)
    assert re.match(r"^\S+ Marginal Significance\n\n", rep)
    rep = module_report(module_output, header=None)
    assert rep.startswith("\n\nYou described")
    # print.metacheck_module_output
    assert repr(module_output).startswith("Marginal Significance")


def test_module_report_howitworks(demo, test_module):
    module_output = module_run(demo, test_module("rp_validation"))
    rep = module_report(module_output)
    assert "Lisa DeBruine, Daniel Lakens and Jakub Werner" in rep
    assert "debruine@gmail.com" not in rep
    assert re.match(r"^### .* Validation Demo", rep)
    assert "Demo description" in rep
    assert "Demo details..." in rep

    # no description, details or authors: no callout
    module_output = module_run(demo, test_module("rp_no_details"))
    rep = module_report(module_output)
    assert re.match(r"^### \S* No Details Module \{#no-details-module \.green\}", rep)
    assert "This module was developed by" not in rep
    assert "How It Works" not in rep


_DESCRIBED = """
from pytacheck.module import module


@module(
    title="No Details But Described",
    description="What it does.",
    keywords=["general"],
    author=["Jane Doe <jane@example.org>", "John Roe"],
)
def rp_described(paper):
    return {"traffic_light": "green", "summary_text": "Fine.", "report": "Fine."}
"""

_BLUE = """
from pytacheck.module import module


@module(title="Blue Module", description="d", keywords=["general"])
def rp_blue(paper):
    return {"traffic_light": "blue", "summary_text": "Blue.", "report": "Blue report."}
"""


def test_module_report_howitworks_without_details(tmp_path):
    # U129: R's gregexpr() on NULL details errors and drops the whole callout,
    # description and authors included
    path = tmp_path / "rp_described.py"
    path.write_text(_DESCRIBED, encoding="utf-8")
    rep = module_report(module_run(pc.test_paper(["x"]), str(path)))
    assert rep.endswith(
        '::: {.callout-note title="How It Works" collapse="true"}\n\nWhat it does.\n\n'
        "This module was developed by Jane Doe and John Roe\n\n:::\n"
    )


def test_module_report_undefined_traffic_light(tmp_path):
    # U128: R's sprintf() with a NULL emoji drops the heading and prints the
    # summary line of report_qmd() as "character(0)"
    path = tmp_path / "rp_blue.py"
    path.write_text(_BLUE, encoding="utf-8")
    op = module_run(pc.test_paper(["x"]), str(path))
    assert module_report(op).startswith("### Blue Module {#blue-module .blue}\n\nBlue.")
    assert module_report(op, header=0).startswith("Blue Module\n\nBlue.")
    qmd = report_qmd(op, pc.test_paper(["x"]))
    assert "- [Blue Module](#blue-module){.blue}: Blue.  " in qmd
    assert "character(0)" not in qmd


def test_report_qmd_without_paper():
    # U128: metacheck's default `paper = list()` always errors
    op = module_run(pc.test_paper(["x"]), "marginal")
    qmd = report_qmd(op)
    assert qmd.startswith('---\ntitle: MetaCheck Report\nsubtitle: ""\n')
    assert "DOI:" not in qmd
    assert "## Summary" in qmd


def test_module_report_two_authors_on_one_line():
    # U6: R's greedy email gsub cut "A (\\email{..}) and B (\\email{..})" to "A"
    from pytacheck.report.report import _strip_email

    line = "Lisa DeBruine (\\email{lisa@x.org}) and Daniel Lakens (\\email{d@y.nl})"
    assert _strip_email(line) == "Lisa DeBruine and Daniel Lakens"
    assert _strip_email("A B (\\email{a@b.c})") == "A B"
    assert _strip_email("A B <a@b.c>") == "A B"
    rep = module_report(module_run(pc.test_paper(["p = 0.04"]), "stat_p_exact"))
    assert "This module was developed by Lisa DeBruine and Daniel Lakens\n" in rep


def test_module_report_validation(demo, test_module):
    v = "::: {.validation}"
    module_output = module_run(demo, test_module("rp_validation"))
    rep = module_report(module_output)
    assert v in rep
    assert "Validation: Here is my demo validation information." in rep
    # no validation
    module_output = module_run(demo, test_module("rp_null_summary"))
    assert v not in module_report(module_output)


def test_module_report_details_wrapper(demo, test_module):
    # long reports are wrapped in a <details> block, short ones are not
    long = module_report(module_run(demo, "marginal"))
    assert "<details><summary>View detailed feedback</summary><div>" in long
    short = module_report(module_run(pc.test_paper(["Nothing."]), "marginal"))
    assert "<details>" not in short
    # a report identical to the summary is dropped
    same = module_report(module_run(demo, test_module("rp_no_details")))
    assert same.count("All good.") == 1
    # without a summary text the report is shown unwrapped (U129: R shows
    # "..." and drops the report)
    null = module_report(module_run(demo, test_module("rp_null_summary")))
    assert ".info}\n\nSome report text.\n\nMore report text.\n\n::: {.callout" in null
    assert "..." not in null
    assert "<details>" not in null


def test_module_report_tables_are_r_chunks(demo):
    rep = module_report(module_run(demo, "marginal"))
    assert "```{r}" in rep
    assert 'metacheck::report_table(table, "auto", 2, FALSE)' in rep


def test_report_module_run(demo, test_module):
    mo = report_module_run(demo, "marginal")
    assert list(mo) == ["marginal"]
    assert mo.paper is demo

    modules = [test_module("rp_chained"), "marginal", test_module("rp_null_summary")]
    mo = report_module_run(demo, modules)
    assert set(mo) == set(modules)
    # sorted by section: intro, results, reference
    assert [m.section for m in mo.values()] == ["intro", "results", "reference"]
    # rp_chained ran first, so it saw no marginal table
    assert mo[test_module("rp_chained")].summary_text == "The marginal table had -1 rows."
    # the paper is not kept in each output
    assert all(m.paper is None and m.prev_outputs == {} for m in mo.values())


def test_report_module_run_error(demo, test_module):
    err = test_module("rp_error")
    with pytest.warns(UserWarning, match="Error in"):
        mo = report_module_run(demo, ["marginal", err, test_module("rp_chained")])
    # failed modules sort last
    assert list(mo)[-1] == err
    fail = mo[err]
    assert fail.traffic_light == "fail"
    assert fail.title == err
    assert fail.section is None
    assert fail.summary_text == "This module failed to run"
    assert "produced errors: boom" in fail.report
    # the chain went on
    assert mo[test_module("rp_chained")].summary_text == "The marginal table had 2 rows."


def test_report_qmd(demo, test_module):
    mo = report_module_run(demo, "marginal")
    report_text = report_qmd(mo, demo)
    assert "MetaCheck Report" in report_text
    assert demo.title in report_text
    assert module_info("marginal").title in report_text
    assert "## Results Modules" in report_text
    assert "%s" not in report_text
    assert "font-size: 150%;" in report_text
    # U128: without a paper there is no subtitle or DOI (R always errors)
    assert report_qmd(mo).startswith('---\ntitle: MetaCheck Report\nsubtitle: ""\n')
    # tables as HTML for Quarto without R
    html_tables = report_qmd(mo, demo, tables="html")
    assert "```{=html}" in html_tables and "```{r}" not in html_tables


def test_report_qmd_fail_and_na(test_module):
    paper = pc.test_paper(["Nothing."])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = report_module_run(
            paper,
            [test_module("rp_list_summary"), test_module("rp_error")],
            {test_module("rp_list_summary"): {"na": True}},
        )
    txt = report_qmd(mo, paper)
    # both only in the summary, not in the module sections
    assert "Modules\n" not in txt
    assert "☠️" in txt and "⬜" in txt
    # a summary starting with a newline is indented under its bullet
    assert (
        ":     \n    * first item" in txt.replace("\n    *", "     \n    *", 1)
        or "\n    * first item" in txt
    )


def test_report_repository(tmp_path, test_module):
    folder = tmp_path / "my_study"
    folder.mkdir()
    out = tmp_path / "out.md"
    rep = report_repository(
        folder, output_file=out, output_format="md", modules=[test_module("rp_repo")]
    )
    op = rep[test_module("rp_repo")]
    assert op.summary_text == "Folder my_study; local only: TRUE"
    assert rep.paper.title == "my_study"
    assert "**my_study**" in out.read_text(encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="No folder found"):
        report_repository(tmp_path / "nope")
    with pytest.raises(ValueError, match="single path"):
        report_repository(None)  # type: ignore[arg-type]


def test_report_repository_default_output(tmp_path, monkeypatch, test_module):
    folder = tmp_path / "data" / "study_a"
    folder.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    rep = report_repository(
        str(folder) + "/", modules=[test_module("rp_repo")], output_format="qmd"
    )
    assert rep.save_path == "study_a_report.qmd"
    assert (tmp_path / "study_a_report.qmd").exists()


def test_report_list_str(tmp_path):
    paper = pc.read([PSYCHSCI / "0956797613520608.json", PSYCHSCI / "0956797614522816.json"])
    out = report(paper, "marginal", [tmp_path / "a.md", tmp_path / "b.md"], "md")
    assert str(out).split("\n") == [str(tmp_path / "a.md"), str(tmp_path / "b.md")]


def test_cli_report(tmp_path, demo):
    from pytacheck.cli import main
    from pytacheck.papers.io import demofile

    out = tmp_path / "cli.html"
    rc = main(["report", str(demofile()), "-m", "marginal", "-o", str(out), "-f", "html"])
    assert rc == 0
    assert out.exists()
    assert "Marginal Significance" in out.read_text(encoding="utf-8")
