"""Write the FAIR_repo parity case files: parity/cases/fair.yaml and parity/cases/fair_review.yaml.

Run ``python -m tests.fair.make_cases`` from the repository root. Edit this script, not the YAML.

Each case calls a helper that builds its input and returns a summary, on both sides: R sources
``tests/fair/parity_helpers.R`` (``ff_*``, whole runs of ``FAIR_repo()``) or
``tests/fair/review_helpers.R`` (``fr_*``, one internal helper at a time) and calls it; Python
imports the twin module and calls the same function with the same arguments. The values are
written as R and as Python from one Python value, so the two sides cannot drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Raw:
    """An argument written out for each side."""

    r: str
    py: str


class Null:
    """R ``NULL`` (a Python ``None`` inside a list is an ``NA``)."""


def r_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\x01":
            out.append("\\001")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def r_value(x: Any) -> str:
    if isinstance(x, Raw):
        return x.r
    if x is None or isinstance(x, Null):
        return "NULL"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return f"{x}L"
    if isinstance(x, float):
        return repr(x)
    if isinstance(x, str):
        return r_str(x)
    if isinstance(x, list):
        if not x:
            return "character(0)"
        return "c(" + ", ".join("NA" if v is None else r_value(v) for v in x) + ")"
    raise TypeError(type(x))


def py_value(x: Any) -> str:
    if isinstance(x, Raw):
        return x.py
    if isinstance(x, Null):
        return "None"
    return repr(x)


def call(helper: str, fn: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str]:
    """The ``$expr`` of a call of ``fn`` in tests/fair/<helper>.(R|py)."""
    r_args = [r_value(a) for a in args] + [f"{k} = {r_value(v)}" for k, v in kwargs.items()]
    py_args = [py_value(a) for a in args] + [f"{k}={py_value(v)}" for k, v in kwargs.items()]
    return {
        "r": f"{{source('tests/fair/{helper}.R'); {fn}({', '.join(r_args)})}}",
        "py": (f"__import__('tests.fair.{helper}', fromlist=['x']).{fn}({', '.join(py_args)})"),
    }


Case = dict[str, Any]


def make(
    cid: str,
    helper: str,
    fn: str,
    *args: Any,
    tier: str | None = None,
    needs_r: bool = False,
    **kwargs: Any,
) -> Case:
    case: Case = {
        "id": cid,
        "r": "identity",
        "py": "tests.fair.parity_helpers.identity",
        "args": {"x": {"$expr": call(helper, fn, args, kwargs)}},
    }
    if tier:
        case["tier"] = {"level": 1, "reason": tier}
    if needs_r:
        case["needs_r"] = True
    return case


# ── fair.yaml: whole runs of FAIR_repo() ─────────────────────────────────────


def run(cid: str, fixture: str, **kwargs: Any) -> Case:
    # FAIR_repo() runs the checks, and code_check starts R on the folder's scripts
    return make(cid, "parity_helpers", "ff_run", fixture, needs_r=True, **kwargs)


def paper_py(links: bool = False, funding: bool = False) -> Raw:
    r = (
        "ff_paper("
        + ", ".join(f"{k} = TRUE" for k, on in (("links", links), ("funding", funding)) if on)
        + ")"
    )
    py = (
        "__import__('tests.fair.parity_helpers', fromlist=['x']).ff_paper("
        + ", ".join(f"{k}=True" for k, on in (("links", links), ("funding", funding)) if on)
        + ")"
    )
    return Raw(r, py)


DEMO_REASON = (
    "the demo repository of metacheck (demofile('repo'), a copy of a real repository with its "
    "paper), the same inputs the module tests use"
)


def fair_cases() -> list[Case]:
    cases: list[Case] = [
        run("run.basic", "basic"),
        run("run.basic_paper", "basic", paper=paper_py()),
        run("run.basic_paper_links", "basic", paper=paper_py(links=True)),
        run("run.basic_paper_funding", "basic", paper=paper_py(funding=True)),
        run("run.no_readme", "basic", drop=["README.txt"]),
        run("run.no_readme_paper", "basic", drop=["README.txt"], paper=paper_py()),
        run("run.readme_title", "readme_title"),
        run("run.readme_latin1", "readme_latin1"),
        run("run.latin1_column", "latin1_column"),
        run("run.studies", "studies", paper=paper_py()),
        run("run.studies_no_paper", "studies"),
        run("run.sheets", "sheets"),
        run("run.sheets_two", "sheets_two"),
        run("run.sheets_top", "sheets_top"),
        run("run.xlsx_single", "xlsx_single"),
        run("run.dup_names", "dup_names"),
        run("run.text_bagit", "text_bagit"),
        run("run.rds_tables", "rds_tables"),
        run("run.rds_model", "rds_model"),
        run("run.zipped", "zipped"),
        run("run.readmes", "readmes"),
        run("run.scenarios", "scenarios"),
        run("run.code_only", "code_only"),
        run("run.py_requirements", "py_requirements"),
        run("run.own_requirements", "own_requirements"),
        run("run.setwd_variants", "setwd_variants"),
        run("run.write_read", "write_read"),
        run("run.readme_links", "readme_links"),
        run("run.rendered_page", "rendered_page"),
        run("run.rendered_page_with_rmd", "rendered_page_with_rmd"),
        run("run.nested_top", "nested_top"),
        run("run.crlf_script", "crlf_script"),
        run("run.script_kinds", "script_kinds"),
        run("run.multi_ext", "multi_ext"),
        run("run.licensed", "licensed"),
        run("run.empty", "empty"),
        run("run.existing_keep", "basic", existing=True),
        run("run.existing_overwrite", "basic", existing=True, overwrite=True),
    ]
    demo_path = Raw("demofile('repo')", "str(pc.demofile('repo'))")
    demo_paper = Raw("demopaper('stroop_effect')", "pc.demopaper('stroop_effect')")
    cases.append(
        make(
            "demo_repo.paper",
            "parity_helpers",
            "ff_run",
            demo_path,
            paper=demo_paper,
            tier=DEMO_REASON,
            needs_r=True,
        )
    )
    cases.append(
        make(
            "demo_repo.no_paper",
            "parity_helpers",
            "ff_run",
            demo_path,
            tier=DEMO_REASON,
            needs_r=True,
        )
    )
    for kind in (
        "no_output_dir",
        "not_a_folder",
        "two_paths",
        "a_file",
        "inside",
        "same_folder",
        "bad_paper",
    ):
        cases.append(make(f"errors.{kind}", "parity_helpers", "ff_error", kind))
    return cases


# ── fair_review.yaml: the internal helpers ───────────────────────────────────


def fr(cid: str, fn: str, *args: Any, **kwargs: Any) -> Case:
    return make(cid, "review_helpers", fn, *args, **kwargs)


def review_cases() -> list[Case]:
    c: list[Case] = []

    # join_path: a link read from its folder
    c.append(
        fr(
            "join_path.basic",
            "fr_join_path",
            [".", "docs", "a/b", ".", "docs", "docs"],
            ["a.png", "./a/../b.png", "../c/d.png", "x/./y.png", "sub//z.png", "/abs.png"],
        )
    )
    c.append(
        fr(
            "join_path.leaves_the_top",
            "fr_join_path",
            ["docs", ".", "a", ".", "a/b", "a/b"],
            ["../../x", "..", "..", "", "../..", "../c"],
        )
    )

    # sheet_target: the CSV of one sheet
    base = "data/study-harvest_data.csv"
    c.append(
        fr(
            "sheet_target.names",
            "fr_sheet_target",
            [base, base, base, base, "study-ex1/data/study-ex1_harvest_data.csv"],
            ["data sheet", "Sheet1", "2021", "R\u00e9sum\u00e9", "Wave 2"],
            [1, 2, 3, 4, 5],
        )
    )
    c.append(fr("sheet_target.symbols", "fr_sheet_target", [base] * 3, ["!!!", " ", ""], [2, 3, 4]))
    c.append(
        fr(
            "sheet_target.notes",
            "fr_sheet_target",
            [base] * 4 + ["study-ex1/data/study-ex1_harvest_data.csv"] * 2,
            ["README", "Codebook", "Notes", "Variables", "Data dictionary", "Legend"],
            [1, 2, 3, 4, 5, 6],
        )
    )
    c.append(
        fr(
            "sheet_target.other_target",
            "fr_sheet_target",
            ["data/study-x.csv", "documentation/x_data.csv", "x_data.csv_data.csv"],
            ["S", "Sheet2", "S"],
            [1, 2, 3],
        )
    )
    c.append(fr("sheet_key.kinds", "fr_sheet_key"))

    # neutralise_setwd
    r_lines = [
        "library(x)",
        'setwd("C:/Users/me/proj")',
        "setwd('data')",
        "setwd(dirname(rstudioapi::getActiveDocumentContext()$path))",
        "setwd(tempdir())",
        "setwd(old)",
        'base::setwd("~/x")',
        "  setwd()",
        "x <- 1",
    ]
    c.append(fr("setwd.r_lines", "fr_setwd", r_lines))
    in_string = ['x <- "', "setwd('a')", '"', 'setwd("b")']
    c.append(fr("setwd.string_no_parse", "fr_setwd", in_string, parse=False))
    c.append(fr("setwd.string_parse", "fr_setwd", in_string, parse=True))
    c.append(fr("setwd.unparsable_parse", "fr_setwd", ['setwd("a"', "y <-"], parse=True))
    c.append(
        fr(
            "setwd.python",
            "fr_setwd",
            [
                "import os",
                "os.chdir('/home/me/proj')",
                "os.chdir(os.path.dirname(__file__))",
                'chdir("data")',
                "os.chdir(tmp)",
                'setwd("x")',
            ],
            "Python",
        )
    )
    c.append(fr("setwd.none", "fr_setwd", ["x <- 1", "y <- 2"]))

    # point_to_new_paths
    c.append(
        fr(
            "point_paths.basic",
            "fr_point_paths",
            [
                'write.csv(x, "results/table.csv")',
                "d <- read.csv('data/raw.csv')",
                'out <- "table.csv"',
                'x <- "outputs/table.csv"',
            ],
            "basic",
        )
    )
    c.append(
        fr(
            "point_paths.convert",
            "fr_point_paths",
            ['d <- readxl::read_excel("data/trial.xlsx")', 'x <- "trial.xlsx"'],
            "convert",
        )
    )
    c.append(
        fr(
            "point_paths.ambiguous",
            "fr_point_paths",
            ['write.csv(x, "table.csv")', 'ggsave("figure.png")', "ggsave('plots/Figure.PNG')"],
            "ambiguous",
        )
    )
    c.append(
        fr(
            "point_paths.backslash",
            "fr_point_paths",
            ['write.csv(x, "results\\table.csv")', 'f <- "C:\\data\\table.csv"'],
            "backslash",
        )
    )
    c.append(fr("point_paths.no_quotes", "fr_point_paths", ["x <- table.csv", "y <- 1"], "basic"))

    # r_data_kind
    for name, rel in (
        ("lookup_rds", "rds_tables/data/lookup.rds"),
        ("table_in_subfolder", "rds_tables/data/a/tab.rds"),
        ("model_rds", "rds_model/data/fit.rds"),
        ("scores_rds", "rds_model/data/scores.rds"),
        ("not_an_r_file", "basic/data/scores.csv"),
        ("missing_file", "nope/x.rds"),
    ):
        c.append(fr(f"r_data_kind.{name}", "fr_r_data_kind", rel))

    # copy_no_bom
    for kind in (
        "bom_text",
        "plain",
        "empty",
        "bom_only",
        "partial_bom",
        "bom_in_the_middle",
        "latin1",
        "two_boms",
    ):
        c.append(fr(f"no_bom.{kind}", "fr_no_bom", kind))

    # keywords
    for kind in (
        "list_cell",
        "vector",
        "na_and_blank",
        "nested",
        "single_string",
        "empty_chr",
        "null",
        "all_blank",
        "numbers",
        "two_cells",
    ):
        c.append(fr(f"keywords.{kind}", "fr_keywords", kind))

    # small helpers
    c.append(
        fr(
            "is_web.values",
            "fr_is_web",
            [
                "http://a",
                "https://b.org/x",
                "ftp://c",
                "HTTP://upper",
                "",
                None,
                "see https://x",
                "/local/path",
                "C:/x",
                "https://",
            ],
        )
    )
    c.append(
        fr(
            "encoding_format.names",
            "fr_encoding_format",
            [
                "a.csv",
                "b.R",
                "c.xyz",
                "d",
                "E.CSV",
                "x.tar.gz",
                "f.json",
                "g.rds",
                "h.xlsx",
                "i.sav",
                "j.md",
                "k.RData",
                "l.html",
                "README",
                "m.docx",
                ".hidden",
                "n.txt",
                "o.png",
                "p.pdf",
                "q.Rmd",
                "r.zip",
            ],
        )
    )
    c.append(
        fr(
            "software_for.paths",
            "fr_software_for",
            ["a.sav", "b.xlsx", "c.csv", "d", "e.SAV", "f.dta", "g.R", "h.rds", "i.por", "j.zsav"],
        )
    )
    c.append(
        fr(
            "credit_slug.roles",
            "fr_credit_slug",
            [
                "Writing - review & editing",
                "Conceptualization",
                "Data curation",
                "Writing \u2013 original draft",
                "Visualisation",
                "SOFTWARE",
                "",
                None,
                " Formal  analysis. ",
                "Funding acquisition",
                "Project administration",
                "Writing review editing",
                "Supervision (part)",
            ],
        )
    )
    c.append(fr("ddi_array.values", "fr_ddi_array"))
    c.append(
        fr(
            "lang_id.names",
            "fr_lang_id",
            ["R", "Python", "C++", "Stata 17.0", "Jupyter Notebook", "R Markdown", "", "Julia 1.9"],
        )
    )
    c.append(fr("id_label.no_group", "fr_id_label", Null()))
    c.append(fr("id_label.group", "fr_id_label", "ex1"))
    c.append(fr("id_label.mixed_case", "fr_id_label", "a2Bc"))
    for kind in ("none", "one", "two", "study_word"):
        c.append(fr(f"roots.{kind}", "fr_roots", kind))

    # tree
    c.append(
        fr(
            "tree.nested",
            "fr_tree",
            ["data/a.csv", "data/sub/b.csv", "README.md", "analysis/run.R", "data/sub/c.csv"],
        )
    )
    c.append(fr("tree.max", "fr_tree", ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"], max=3))
    c.append(fr("tree.max_one_over", "fr_tree", ["a.txt", "b.txt", "c.txt"], max=2))
    c.append(fr("tree.unsorted_duplicates", "fr_tree", ["b/y.R", "a/x.R", "b/y.R", "a/x.R", "z"]))
    c.append(fr("tree.deep", "fr_tree", ["a/b/c/d.txt", "a/b/e.txt", "a/f.txt", "g/h/i/j.txt"]))
    c.append(fr("tree.empty", "fr_tree", []))
    c.append(fr("tree.mixed_case", "fr_tree", ["B.txt", "a.txt", "b/c.R", "A/x", "_x", "1.csv"]))

    # missing values
    c.append(
        fr(
            "decode_missing.strings",
            "fr_decode_missing",
            [
                '["-99","-98"]',
                '{"-99":"refused","-98":"not applicable"}',
                "",
                None,
                "[]",
                "not json",
                "[-99, -98]",
                '{"-99":"x","-98":""}',
                "{}",
                '["", "-1"]',
            ],
        )
    )
    for kind in ("pooled", "none_declared", "no_column", "not_a_table"):
        c.append(fr(f"missing_scheme.{kind}", "fr_missing_scheme", kind))

    # resolve_ref
    c.append(
        fr(
            "resolve_ref.refs",
            "fr_resolve_ref",
            [
                "a.csv",
                "data/A.CSV",
                "data.csv",
                "data.csv",
                "data.csv",
                "data.csv",
                "gone.csv",
                "nothere.csv",
                "C:\\x\\a.csv",
                "other.R",
            ],
            [None, None, None, "a", "b", "c", None, None, None, "a"],
        )
    )
    for kind in ("two_groups", "none_referenced", "no_column"):
        c.append(fr(f"reused_by_group.{kind}", "fr_reused_by_group", kind))

    # papers
    for kind in ("ff", "none", "blank", "corresponding"):
        c.append(fr(f"authors.{kind}", "fr_authors", kind))
    for kind in ("plain", "https", "dx", "blank", "na", "absent"):
        c.append(fr(f"info.doi_{kind}", "fr_info", kind))
    c.append(fr("info.list_cell", "fr_info_list"))
    for kind in ("plain", "study", "keywords"):
        c.append(fr(f"dataset_description.{kind}", "fr_dataset_description", kind))
    demo = Raw("demopaper()", "pc.demopaper()")
    c.append(fr("abstract_text.demo", "fr_abstract_text", demo))
    c.append(fr("abstract_text.demo_cut", "fr_abstract_text", demo, max_chars=80))
    c.append(
        fr(
            "abstract_text.no_abstract",
            "fr_abstract_text",
            Raw("test_paper('x')", "pc.test_paper(['x'])"),
        )
    )
    c.append(fr("abstract_text.not_a_paper", "fr_abstract_text", Raw("list(1)", "[1]")))
    for kind in ("complete", "nothing", "roles_missing"):
        c.append(fr(f"still_unknown.{kind}", "fr_still_unknown", kind))
    for kind in ("list", "frame", "windows", "text", "no_folder", "web_urls"):
        c.append(fr(f"drop_local.{kind}", "fr_drop_local", kind))
    for kind in ("numeric_and_empty", "categorical", "labelled"):
        c.append(fr(f"variable_measured.{kind}", "fr_variable_measured", kind))

    # files
    for name, written, fixture in (
        ("underlined", ["README.md"], None),
        ("yaml_title", ["README.Rmd"], None),
        ("yaml_single_quotes", ["README.qmd"], None),
        ("plain_first_line", ["README"], None),
        ("heading_with_spaces", ["README.markdown"], None),
        ("not_a_readme", ["notes.txt"], None),
        ("subfolder_only", ["sub/README.md"], None),
        ("first_with_a_title", ["README.md", "README.Rmd"], None),
        ("falls_through", ["README", "README.Rmd"], None),
        ("nothing_written", [], None),
        ("missing_file", ["readme.MD"], None),
        ("latin1", ["README.txt"], "readme_latin1"),
        ("title_fixture", ["README.txt"], "readme_title"),
        ("plain_fixture", ["README.txt"], "basic"),
    ):
        if fixture:
            c.append(fr(f"readme_title.{name}", "fr_readme_title", written, fixture))
        else:
            c.append(fr(f"readme_title.{name}", "fr_readme_title", written))
    for kind in (
        "scalars",
        "numbers",
        "nested",
        "empty_containers",
        "vectors",
        "strings",
        "na_values",
    ):
        c.append(fr(f"write_json.{kind}", "fr_write_json", kind))
    return c


# ── output ───────────────────────────────────────────────────────────────────


def dump(area: str, header: list[str], cases: list[Case]) -> str:
    lines = [f"# {h}" if h else "#" for h in header]
    lines.append(f"{json.dumps('area')}: {json.dumps(area)}")
    lines.append('"cases":')
    for case in cases:
        lines.append(f"- {json.dumps('id')}: {json.dumps(case['id'])}")
        lines.append(f"  {json.dumps('r')}: {json.dumps(case['r'])}")
        lines.append(f"  {json.dumps('py')}: {json.dumps(case['py'])}")
        if "tier" in case:
            lines.append(f"  {json.dumps('tier')}: {json.dumps(case['tier'])}")
        if case.get("needs_r"):
            lines.append(f"  {json.dumps('needs_r')}: true")
        lines.append('  "args":')
        for name, value in case["args"].items():
            lines.append(f"    {json.dumps(name)}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


FAIR_HEADER = [
    "FAIR_repo() parity cases (R/fair-repo.R, fair-repo-metadata.R), generated by",
    "tests/fair/make_cases.py -- edit that script, not this file.",
    "Each case runs FAIR_repo() on a copy of a folder of tests/fair/fixtures (written by",
    "tests/fair/make_fixtures.py) or of the demo repository, with the language model off, and",
    "compares a summary of the folder it wrote. R helper: tests/fair/parity_helpers.R; Python",
    "twin: tests/fair/parity_helpers.py. The file the results are saved in (R .rds, pytacheck",
    ".json, D172) is masked in the helpers, so no mark is needed for it.",
]
REVIEW_HEADER = [
    "FAIR_repo() review parity cases: the internal helpers of R/fair-repo.R and",
    "fair-repo-metadata.R, called one by one. Generated by tests/fair/make_cases.py -- edit that",
    "script, not this file. R helper: tests/fair/review_helpers.R; Python twin:",
    "tests/fair/review_helpers.py.",
]


def main() -> None:
    out = ROOT / "parity" / "cases"
    (out / "fair.yaml").write_text(dump("fair", FAIR_HEADER, fair_cases()), encoding="utf-8")
    (out / "fair_review.yaml").write_text(
        dump("fair_review", REVIEW_HEADER, review_cases()), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
