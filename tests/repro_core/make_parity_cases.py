"""Generate ``parity/cases/repro_core.yaml``.

Run ``python tests/repro_core/make_parity_cases.py``, then
``python -m parity generate --area repro_core``. Cases that need files, a
sandbox or R on the Python side go through ``parity_support.run`` (Python)
and ``parity_helpers.R`` (R).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "repro_core.yaml"
RUN = "tests.repro_core.parity_support.run"
CORE = "metacheck.repro.core"
DOCKER = "metacheck.repro.docker"
TABLES = "metacheck.repro.tables"
HELPERS = 'source("tests/repro_core/parity_helpers.R", local = TRUE)'


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)

cases: list[dict[str, Any]] = []


def chr_(*xs: Any) -> dict[str, Any]:
    return {"$chr": list(xs)}


def case(id_: str, r: str, py: str, args: dict[str, Any], **kw: Any) -> None:
    cases.append({"id": id_, "r": r, "py": py, "args": args, **kw})


def expr(id_: str, r_code: str, py_code: str, **kw: Any) -> None:
    """A case computed by R code (with the helpers) and a Python ``lambda m: ...``."""
    cases.append(
        {
            "id": id_,
            "r": "identity",
            "py": RUN,
            "args": {"x": {"$expr": {"r": f"{{ {HELPERS}; {r_code} }}", "py": py_code}}},
            **kw,
        }
    )


# ---------------------------------------------------------------------------
# repro_dependencies()
# ---------------------------------------------------------------------------


def deps(id_: str, code: Any, **args: Any) -> None:
    case(
        f"repro_dependencies.{id_}",
        "repro_dependencies",
        f"{CORE}.repro_dependencies",
        {"code_text": code, **args},
    )


deps("library_require", chr_("library(dplyr)", "require(ggplot2)"))
deps("namespace_base", "x <- stats::sd(1:10)")
deps("base_and_cran", chr_("library(stats)", "library(dplyr)"))
deps("github_ref", chr_("library(ggplot2)", "remotes::install_github('tidyverse/ggplot2@v3.4.0')"))
deps(
    "url_source",
    chr_(
        "library(pkgname)",
        'install.packages("https://example.org/src/contrib/pkgname_1.0.tar.gz")',
    ),
)
deps(
    "pooled_github_over_cran",
    {"$list": [chr_("library(ggplot2)", "remotes::install_github('tidyverse/ggplot2')")]},
)
deps(
    "pooled_files",
    {
        "$list": [
            chr_("library(dplyr)", "library(lattice)", "x <- grid::unit(1, 'npc')"),
            chr_("library(ggplot2)", "remotes::install_github('tidyverse/ggplot2@main')"),
            chr_("library(ggplot2)", "library(edgeR)", "library(zzz)", "library(Matrix)"),
            chr_("# nothing here"),
            chr_("library(pkgname)", "install.packages('https://x.org/pkgname_2.1.tar.gz')"),
            chr_("library(pkgname)", "library(Abc)", "library(abc)"),
        ]
    },
)
deps("null", {"$null": True})
deps("python_lang", "library(dplyr)", lang="Python")
deps("empty", {"$chr": []})
deps("no_packages", chr_("x <- 1", "y <- x + 2"))
deps("bioc", chr_("library(edgeR)", "library(dplyr)"))
deps("bioc_github", chr_("library(edgeR)", "remotes::install_github('Bioconductor/edgeR')"))
# issue #421: a package-list variable is resolved to its literal names, or dropped
deps(
    "loop_variable",
    chr_(
        'list.packages <- c("activity", "bbmle")',
        "for (req.lib in list.packages) {",
        "  if (!require(req.lib, character.only = TRUE)) install.packages(req.lib)",
        "}",
    ),
)
deps(
    "loop_variable_unresolved",
    chr_(
        "for (req.lib in list.packages) {",
        "  if (!require(req.lib, character.only = TRUE)) install.packages(req.lib)",
        "}",
        "library(dplyr)",
    ),
)
deps(
    "lapply_variable",
    chr_(
        'pkgs <- c("psych", "car")',
        "invisible(lapply(pkgs, library, character.only = TRUE))",
        "pacman::p_load(char = pkgs)",
    ),
)
deps(
    "mixed",
    chr_(
        "# library(commented)",
        "pacman::p_load(tidyr, purrr)",
        'suppressPackageStartupMessages(library("lme4"))',
        "if (!requireNamespace('car', quietly = TRUE)) install.packages('car')",
        "library(lattice); library(Matrix)",
        "devtools::install_github(\"user/mypkg@v0.1\", upgrade = 'never')",
        "library(mypkg)",
        "x <- utils::head(mtcars)",
        "library(tools)",
    ),
)
deps(
    "two_github_refs",
    chr_(
        "remotes::install_github('a/foo')",
        "remotes::install_github('b/foo@dev')",
        "library(foo)",
        "install.packages('http://x.org/src/foo_1.2.tar.gz')",
    ),
)

# ---------------------------------------------------------------------------
# .repro_format_call_refs() / .repro_simple_string_vars() / .repro_redirect_writes()
# ---------------------------------------------------------------------------


def fcr(id_: str, code: Any) -> None:
    case(
        f"repro_format_call_refs.{id_}",
        "metacheck:::.repro_format_call_refs",
        f"{CORE}._repro_format_call_refs",
        {"code_text": code},
    )


fcr("sprintf_resolved", chr_('wd <- "exp1"', 'd <- read.csv(sprintf("%s_data.csv", wd))'))
fcr(
    "sprintf_two_vars",
    chr_(
        'wd <- "/Users/me/proj"',
        "wd_data <- 'data'",
        'wd_data <- "raw"   # reassigned',
        'x <- read_csv(sprintf("%s/%s/x.csv", wd, wd_data))',
    ),
)
fcr("unresolved_var", chr_('x <- read.csv(paste0(dir_var, "/scores.csv"))'))
fcr("paste_first_quoted", chr_('x <- read.csv(paste0("data/", "scores.csv", sep = ""))'))
fcr("file_path", chr_('base <- "raw"', 'y <- readRDS(file.path("%s/m.rds", base))'))
fcr("format_spec_skipped", chr_('cat(sprintf("%.2e", est))', 'cat(sprintf("%05.2f", x), "\\n")'))
fcr("no_trailing_args", chr_('d <- read.csv(sprintf("Exp1A_Data.txt"))'))
fcr("nested_parens", chr_('d <- read.csv(sprintf("%s.csv", paste0(a, b)))'))
fcr("unbalanced", chr_('d <- read.csv(sprintf("%s.csv", wd'))
fcr(
    "count_mismatch",
    chr_('wd <- "a"', 'd <- read.csv(sprintf("%s/%s.csv", wd))', 'e <- sprintf("%d.csv", wd)'),
)
fcr(
    "multi_line_and_comments",
    chr_(
        "# sprintf('%s.csv', commented)",
        'dir <- "d"',
        "x <- read.csv(",
        '  sprintf("%s/one.csv",',
        "          dir))",
        'y <- paste("no extension here", dir)',
        "z <- paste0('it''s.csv', dir)",
    ),
)
fcr("escaped_quote", chr_('p <- "q"', 'read.csv(sprintf("a\\"b%s.csv", p))'))
fcr("empty", {"$chr": []})
fcr("null", {"$null": True})
fcr("backslash_value", chr_('wd <- "C:\\\\data"', 'read.csv(sprintf("%s/x.csv", wd))'))

case(
    "repro_simple_string_vars.basic",
    "metacheck:::.repro_simple_string_vars",
    f"{CORE}._repro_simple_string_vars",
    {
        "code_text": chr_(
            'a <- "one"',
            "b = 'two'",
            'a <<- "three"',
            'c <- paste0("x", "y")',
            '  d <- "indented"',
            'e <- "esc\\"aped"',
            ".f <- 'dot'",
            'b <- "four"  ',
        )
    },
)
case(
    "repro_simple_string_vars.none",
    "metacheck:::.repro_simple_string_vars",
    f"{CORE}._repro_simple_string_vars",
    {"code_text": chr_("x <- 1", "y <- f()")},
)


def rw(id_: str, code: Any) -> None:
    case(
        f"repro_redirect_writes.{id_}",
        "metacheck:::.repro_redirect_writes",
        f"{CORE}._repro_redirect_writes",
        {"code_text": code},
    )


rw("literal", chr_('write.csv(x, "results/out.csv")'))
rw("named_arg", chr_('write.csv(x, file = "../Data/Cleaned/x.csv", row.names = FALSE)'))
rw(
    "variable",
    chr_(
        'cleaned_file_name <- paste0("../Data/Cleaned/task_level_", Sys.Date(), ".csv")',
        "write.csv(cleaned_file_name, x = df)",
        "saveRDS(model, file = out_rds)",
        'out_rds <- "models/m.rds"',
    ),
)
rw(
    "inline_pipe",
    chr_(
        "df %>%",
        '  write.csv(paste0("dir/", Sys.Date(), ".csv"), row.names = FALSE)',
        'ggsave(filename = file.path("figs", "fig1.png"), plot = p)',
    ),
)
rw(
    "no_target",
    chr_(
        "write.csv(x, path)",
        "save.image()",
        'writeLines("hello")',
        'export(d, "notes")',
        "fwrite(dt, f(x))",
    ),
)
rw(
    "several",
    chr_(
        'save(a, b, file = "ab.RData")',
        "save.image('ws.RData')",
        'write_csv(res, "C:\\\\out\\\\res.csv")',
        'writexl::write_xlsx(list(a = d), "t.xlsx")',
        "# write.csv(x, 'commented.csv')",
        'fwrite(dt, "big.csv.gz")',
        'data.table::fwrite(dt, sprintf("%s.tsv", nm))',
    ),
)
rw("unbalanced", chr_('write.csv(x, "a.csv"'))
rw("empty", {"$chr": []})
rw("none", chr_("x <- 1"))

# ---------------------------------------------------------------------------
# repro_rewrite_paths()
# ---------------------------------------------------------------------------


def rp(id_: str, code: Any, file_name: str, plan: Any, **args: Any) -> None:
    case(
        f"repro_rewrite_paths.{id_}",
        "repro_rewrite_paths",
        f"{CORE}.repro_rewrite_paths",
        {"code_text": code, "file_name": file_name, "plan": plan, **args},
    )


PLAN_1 = {
    "$df": {
        "file_name": ["demographics.csv"],
        "target_path": ["study-ex1/data/study-demographics_data.csv"],
        "current_path": ["ex1/demographics.csv"],
    }
}
PLAN_2 = {
    "$df": {
        "file_name": ["demographics.csv", "demographics.csv"],
        "target_path": [
            "study-ex1/data/study-demographics_data.csv",
            "study-ex2/data/study-demographics_data.csv",
        ],
        "current_path": ["ex1/demographics.csv", "ex2/demographics.csv"],
    }
}
rp("single_match", 'd <- read.csv("data/demographics.csv")', "ex1/analysis.R", PLAN_1)
rp(
    "unmatched",
    'd <- read.csv("data/not_in_plan.csv")',
    "ex1/analysis.R",
    {"$df": {"file_name": ["scores.csv"], "target_path": ["study-ex1/data/scores_data.csv"]}},
)
rp("study_group", 'd <- read.csv("demographics.csv")', "ex2/analysis.R", PLAN_2)
rp("still_ambiguous", 'd <- read.csv("demographics.csv")', "shared/analysis.R", PLAN_2)
rp("ref_names_group", 'd <- read.csv("Study2/demographics.csv")', "shared/analysis.R", PLAN_2)
rp(
    "same_target_twice",
    'd <- read.csv("demographics.csv")',
    "ex1/analysis.R",
    {
        "$df": {
            "file_name": ["demographics.csv", "demographics.csv"],
            "target_path": [
                "study-ex1/data/study-demographics_data.csv",
                "study-ex1/data/study-demographics_data.csv",
            ],
        }
    },
)
PLAN_CONV = {
    "$df": {
        "file_name": ["math.dta", "survey.sav"],
        "target_path": ["study-ex1/data/study-math_data.csv", "data/survey_data.csv"],
        "original_target": ["study-ex1/data/math.dta", None],
    }
}
rp("original_target", 'd <- haven::read_dta("math.dta")', "ex1/analysis.R", PLAN_CONV)
rp(
    "original_target_na",
    chr_('s <- haven::read_sav("survey.sav")', 'm <- read.csv("MATH.DTA")'),
    "analysis.R",
    PLAN_CONV,
)
rp(
    "sprintf_call",
    chr_('wd <- "exp1"', 'd <- read.csv(sprintf("%s_data.csv", wd))'),
    "ex1/analysis.R",
    {"$df": {"file_name": ["exp1_data.csv"], "target_path": ["study-ex1/data/exp1_data.csv"]}},
)
rp(
    "calls_mixed",
    chr_(
        'wd <- "raw"',
        'a <- read.csv(paste0(wd, "/demographics.csv"))',
        'b <- read.csv(sprintf("%s/unknown.csv", other))',
        'c <- read.csv(file.path("%s/scores.csv", wd))',
        'd <- read.csv("scores.csv")',
        'e <- read.csv("data\\\\Demographics.CSV")',
    ),
    "ex2/analysis.R",
    {
        "$df": {
            "file_name": ["demographics.csv", "demographics.csv", "scores.csv", "x.csv"],
            "target_path": [
                "study-ex1/data/study-demographics_data.csv",
                "study-ex2/data/study-demographics_data.csv",
                "data/scores_data.csv",
                None,
            ],
        }
    },
)
# the file string is not the first argument, with arguments after it: a
# runtime-built path whose whole call is rewritten (UPSTREAM_ISSUES U133;
# metacheck rewrites only the literal, giving "raw/<target>")
rp(
    "paste_sep_call",
    chr_('wd <- "raw"', 'd <- read.csv(paste(wd, "demographics.csv", sep = "/"))'),
    "ex1/analysis.R",
    PLAN_1,
)
rp(
    "na_and_empty_targets",
    chr_('a <- read.csv("x.csv")', 'b <- read.csv("y.csv")'),
    "analysis.R",
    {"$df": {"file_name": ["x.csv", "y.csv", "y.csv"], "target_path": [None, "", "data/y.csv"]}},
)
rp(
    "python_lang",
    'read.csv("a.csv")',
    "x.py",
    {"$df": {"file_name": ["a.csv"], "target_path": ["data/a.csv"]}},
    lang="Python",
)
rp("no_plan", 'read.csv("a.csv")', "x.R", {"$null": True})
rp("plan_missing_cols", 'read.csv("a.csv")', "x.R", {"$df": {"file_name": ["a.csv"]}})
rp("no_refs", "x <- 1", "x.R", PLAN_1)
expr(
    "repro_rewrite_paths.mirror_collapse",
    'repro_rewrite_paths(\'d <- read.csv("demographics.csv")\', "ex1/analysis.R",'
    ' data.frame(file_name = rep("demographics.csv", 3),'
    ' target_path = c("study-ex1/data/study-demographics_data.csv",'
    ' "study-ex3/data/study-demographics_data.csv", "study-ex1/data/study-demographics_data.csv")),'
    " structure_df = data.frame(file_name = c('a/demographics.csv', 'b/demographics.csv', 'c/demographics.csv'),"
    " file_location = file.path(rc_fixtures, c('mirror/a/demographics.csv', 'mirror/b/demographics.csv',"
    " 'mirror/c/demographics.csv'))))",
    "lambda m: m.core.repro_rewrite_paths('d <- read.csv(\"demographics.csv\")', 'ex1/analysis.R',"
    " m.pd.DataFrame({'file_name': ['demographics.csv'] * 3,"
    " 'target_path': ['study-ex1/data/study-demographics_data.csv',"
    " 'study-ex3/data/study-demographics_data.csv', 'study-ex1/data/study-demographics_data.csv']}),"
    " structure_df=m.pd.DataFrame({'file_name': ['a/demographics.csv', 'b/demographics.csv', 'c/demographics.csv'],"
    " 'file_location': ['tests/repro_core/fixtures/mirror/' + x for x in"
    " ['a/demographics.csv', 'b/demographics.csv', 'c/demographics.csv']]}))",
)
expr(
    "repro_rewrite_paths.mirror_tie",
    'repro_rewrite_paths(\'d <- read.csv("demographics.csv")\', "shared/analysis.R",'
    ' data.frame(file_name = c("a/demographics.csv", "b/demographics.csv"),'
    ' target_path = c("study-ex3/data/d.csv", "study-ex2/data/d.csv")),'
    " structure_df = data.frame(file_name = c('a/demographics.csv', 'b/demographics.csv'),"
    " file_location = file.path(rc_fixtures, c('mirror/a/demographics.csv', 'mirror/b/demographics.csv'))))",
    "lambda m: m.core.repro_rewrite_paths('d <- read.csv(\"demographics.csv\")', 'shared/analysis.R',"
    " m.pd.DataFrame({'file_name': ['a/demographics.csv', 'b/demographics.csv'],"
    " 'target_path': ['study-ex3/data/d.csv', 'study-ex2/data/d.csv']}),"
    " structure_df=m.pd.DataFrame({'file_name': ['a/demographics.csv', 'b/demographics.csv'],"
    " 'file_location': ['tests/repro_core/fixtures/mirror/a/demographics.csv',"
    " 'tests/repro_core/fixtures/mirror/b/demographics.csv']}))",
)

# ---------------------------------------------------------------------------
# repro_run_order()
# ---------------------------------------------------------------------------


def ro(
    id_: str, names: list[str], reads: Any, writes: Any, sources: Any, edges: Any = None
) -> None:
    def r_list(xs: Any) -> str:
        if xs is None:
            return "NULL"
        items = []
        for x in xs:
            if isinstance(x, str):
                items.append(f'"{x}"')
            elif not x:
                items.append("character(0)")
            else:
                items.append("c(" + ", ".join(f'"{v}"' for v in x) + ")")
        return "list(" + ", ".join(items) + ")"

    def py_list(xs: Any) -> str:
        return "None" if xs is None else repr([[x] if isinstance(x, str) else list(x) for x in xs])

    r_names = "c(" + ", ".join(f'"{n}"' for n in names) + ")"
    r_edges = (
        "NULL" if edges is None else "list(" + ", ".join(f'c("{a}", "{b}")' for a, b in edges) + ")"
    )
    expr(
        f"repro_run_order.{id_}",
        f"rc_run_order_full(rc_files_df({r_names}, {r_list(reads)}, {r_list(writes)},"
        f" {r_list(sources)}), {r_edges})",
        f"lambda m: m.run_order_full(m.files_df({names!r}, {py_list(reads)}, {py_list(writes)},"
        f" {py_list(sources)}), {edges!r})",
    )


E: list[str] = []
ro("read_after_write", ["analysis.R", "prep.R"], ["clean.csv", E], [E, "clean.csv"], [E, E])
ro("source_edge", ["main.R", "helper.R"], [E, E], [E, E], ["helper.R", E])
ro("numeric_prefix", ["1_analysis.R", "0_prep.R"], [E, E], [E, E], [E, E])
ro(
    "digit_runs",
    ["Exp2_1_import.R", "Exp1_02_preprocessing.R", "Exp1_2_models.R", "readme.R"],
    [E] * 4,
    [E] * 4,
    [E] * 4,
)
ro("cycle", ["a.R", "b.R"], ["b_out.csv", "a_out.csv"], ["a_out.csv", "b_out.csv"], [E, E])
ro(
    "cycle_partial",
    ["a.R", "b.R", "c.R", "d.R"],
    ["b_out.csv", "a_out.csv", E, "c.csv"],
    ["a_out.csv", "b_out.csv", "c.csv", E],
    [E, E, E, E],
)
ro("ambiguous", ["a.R", "b.R"], [E, E], [E, E], [E, E])
ro("solo", ["solo.R"], [E], [E], [E])
ro(
    "extra_edges",
    ["a.R", "b.R"],
    [E, E],
    [E, E],
    [E, E],
    [("b.R", "a.R"), ("a.R", "a.R"), ("zz.R", "a.R")],
)
ro(
    "fuzzy_source",
    ["201 - Analysis.R", "103 - Data Prep.R"],
    [E, E],
    [E, E],
    ["001 - data prep.R", E],
)
ro("exact_source", ["main.R", "helper.R"], [E, E], [E, E], ["helper.R", E])
ro(
    "ambiguous_fuzzy",
    ["main.R", "01_prep.R", "02_prep.R"],
    [E, E, E],
    [E, E, E],
    ["00_prep.R", E, E],
)
ro(
    "complex",
    ["R/c.R", "b.R", "R/a_b.R", "a.R", "a-b.R", "x/b.R", "big_20240823120000.R", "Z.R"],
    [["mid.csv", "RAW.CSV"], E, E, "final.csv", E, E, E, E],
    [E, "mid.csv", E, E, "final.csv", E, ["raw.csv", "final.csv"], E],
    [E, E, "b.r", E, E, "missing.R", E, "B.R"],
)
expr(
    "repro_run_order.no_io_columns",
    'rc_run_order_full(data.frame(file_name = c("2.R", "10.R", "b.R", "a.R")))',
    "lambda m: m.run_order_full(m.pd.DataFrame({'file_name': ['2.R', '10.R', 'b.R', 'a.R']}))",
)
case(
    "repro_run_order.empty",
    "repro_run_order",
    f"{CORE}.repro_run_order",
    {"files": {"$df": {"file_name": []}}},
)
case(
    "repro_run_order.null", "repro_run_order", f"{CORE}.repro_run_order", {"files": {"$null": True}}
)

case(
    "repro_normalize_basename.vector",
    "metacheck:::.repro_normalize_basename",
    f"{CORE}._repro_normalize_basename",
    {
        "b": chr_(
            "001 - data prep.R",
            "103 - Data Prep.R",
            "12.analysis_v2.Rmd",
            "noext",
            "2024_01_15 final.r",
            ".hidden.R",
            "a.tar.gz",
        )
    },
)

# ---------------------------------------------------------------------------
# repro_file_io() / repro_defined_vars()
# ---------------------------------------------------------------------------


def fio(id_: str, code_list: Any) -> None:
    case(
        f"repro_file_io.{id_}",
        "repro_file_io",
        f"{CORE}.repro_file_io",
        {"code_text_list": code_list},
    )


fio(
    "reads_writes",
    {
        "prep.R": chr_('dat <- read.csv("raw.csv")', 'write.csv(dat, "clean.csv")'),
        "analysis.R": chr_('dat <- read.csv("clean.csv")'),
    },
)
fio("source", {"main.R": chr_('source("helper.R")')})
fio("empty", {"$list": []})
fio(
    "mixed",
    {
        "a.R": chr_(
            "# read.csv('commented.csv')",
            'x <- readRDS("cache/model.rds")',
            'saveRDS(x, "cache/model.rds")',
            'source("R/Utils.R")',
            'source("R/Utils.R")',
            'd <- readxl::read_excel("Data\\\\Survey.xlsx")',
            'ggsave("fig.png", p)',
            'load("ws.RData"); save(x, file = "ws.RData")',
            'd2 <- read.csv("Survey.xlsx")',
        ),
        "b.R": chr_("x <- 1"),
        "c.R": chr_('WRITE.CSV(d, "upper.csv")', 'data.table::fwrite(d, "f.csv")'),
    },
)
fio("unnamed", {"$list": [chr_('read.csv("a.csv")'), chr_('write.csv(x, "a.csv")')]})


def dv(id_: str, code_list: Any) -> None:
    case(
        f"repro_defined_vars.{id_}",
        "repro_defined_vars",
        f"{CORE}.repro_defined_vars",
        {"code_text_list": code_list},
    )


dv("basic", {"a.R": chr_("x <- 1", "  y <- 2", "assign('z', 3)")})
dv(
    "variety",
    {
        "a.R": chr_(
            "x_1 = 5",
            "x_1 == 5",
            ".hidden <<- TRUE",
            'assign("dq", 1)',
            '  assign("indented", 2)',
            "f <- function() {",
            "  inner <- 1",
            "}",
            "# c <- 3",
            "x_1 <- 6",
            "1bad <- 2",
            "a.b<-3",
        ),
        "b.R": chr_("print(1)"),
    },
)
dv("empty", {"$list": []})

# ---------------------------------------------------------------------------
# repro_missing_inputs()
# ---------------------------------------------------------------------------


def mi(id_: str, refs: Any, structure_df: Any = None, skipped: Any = None) -> None:
    args: dict[str, Any] = {
        "refs": refs,
        "plan": {"$null": True},
        "structure_df": structure_df if structure_df is not None else {"$null": True},
    }
    if skipped is not None:
        args["skipped"] = skipped
    case(
        f"repro_missing_inputs.{id_}", "repro_missing_inputs", f"{CORE}.repro_missing_inputs", args
    )


mi("absent", "missing.csv")
mi("withheld", "big.csv", skipped={"$df": {"file_name": ["big.csv"], "file_size": [209715200]}})
mi(
    "withheld_no_size",
    chr_("big.csv", "huge.csv"),
    skipped={"$df": {"file_name": ["x/big.csv", "huge.csv"], "file_size": [None, "abc"]}},
)
mi("withheld_missing_size_col", "big.csv", skipped={"$df": {"file_name": ["big.csv"]}})
mi(
    "not_downloaded",
    "notdl.csv",
    structure_df={"$df": {"file_name": ["notdl.csv"], "file_location": [None]}},
)
mi(
    "similar",
    chr_(
        "data/Data.csv",
        "scores_v1.csv",
        "x.csv",
        "abcd.csv",
        "Data\\\\Survey.CSV",
        "demographics.csv",
        "notes.txt",
    ),
    structure_df={
        "$df": {
            "file_name": [
                "dir/data.xlsx",
                "scores_v2.csv",
                "y.csv",
                "abce.csv",
                "survey.csv",
                "demographix.csv",
                "notes2.txt",
            ],
            "file_location": [None, "", None, None, None, None, None],
        }
    },
)
mi("empty", {"$chr": []})
expr(
    "repro_missing_inputs.present",
    'repro_missing_inputs(c("demographics.csv", "gone.csv", "DEMOGRAPHICS.csv"), plan = NULL,'
    " structure_df = data.frame(file_name = c('study/demographics.csv', 'gone.csv'),"
    " file_location = c(file.path(rc_fixtures, 'layout/demographics.csv'), file.path(rc_fixtures, 'layout/gone.csv'))))",
    "lambda m: m.core.repro_missing_inputs(['demographics.csv', 'gone.csv', 'DEMOGRAPHICS.csv'], plan=None,"
    " structure_df=m.pd.DataFrame({'file_name': ['study/demographics.csv', 'gone.csv'],"
    " 'file_location': ['tests/repro_core/fixtures/layout/demographics.csv',"
    " 'tests/repro_core/fixtures/layout/gone.csv']}))",
)

# ---------------------------------------------------------------------------
# .repro_content_sniff() / .repro_is_jags_model()
# ---------------------------------------------------------------------------


def sniff(id_: str, code: Any) -> None:
    case(
        f"repro_content_sniff.{id_}",
        "metacheck:::.repro_content_sniff",
        f"{CORE}._repro_content_sniff",
        {"code_text": code},
    )


sniff("json", '{"x":{"material":"phong","data":[1,2,3]}}')
sniff("html_doctype", chr_("<!DOCTYPE html>", "<html><body>hi</body></html>"))
sniff("html_tag", chr_("  <HTML lang='en'>", "</html>"))
sniff("xml", chr_('<?xml version="1.0"?>', "<root></root>"))
sniff("r_block", chr_("{", "  x <- 1", "  y <- 2", "}", "print(x + y)"))
sniff("empty", {"$chr": []})
sniff("null", {"$null": True})
sniff("blank", chr_("   ", ""))
sniff("json_array", chr_("[", '  {"a": "}"},', '  {"b": "\\"q\\""}', "]", "   "))
sniff("json_small_tail", chr_('{"a": 1}', "#x"))
sniff("json_long_tail", chr_('{"a": 1}', "x <- 2"))
sniff("unclosed", chr_('{"a": [1, 2'))


def jags(id_: str, code: Any, **args: Any) -> None:
    case(
        f"repro_is_jags_model.{id_}",
        "metacheck:::.repro_is_jags_model",
        f"{CORE}._repro_is_jags_model",
        {"code_text": code, **args},
    )


jags("model_block", chr_("model {", "  psi ~ dlogis(0, 1)", "}"))
jags("model_after_comments", chr_("# JAGS model", "", "  model{", "}"))
jags("path_component", "x <- 1", file_name="study1/jags_script/conditional_model.R")
jags("path_windows", "x <- 1", file_name="study1\\BUGS_SCRIPT\\m.R")
jags(
    "calling_reference",
    "psi ~ dlogis(0,1)",
    file_name="model_def.R",
    other_code_text={"$list": [chr_('m <- rjags::jags.model(file = "model_def.R", data = d)')]},
)
jags(
    "calling_reference_r2jags",
    "psi ~ dlogis(0,1)",
    file_name="models/m1.txt",
    other_code_text={
        "$list": [
            chr_("x <- 1"),
            chr_(
                "fit <- R2jags::jags(data, inits, params, 'jags/m1.txt')",
                "fit2 <- run.jags('other.txt')",
            ),
        ]
    },
)
jags(
    "not_referenced",
    "psi ~ dlogis(0,1)",
    file_name="model_def.R",
    other_code_text={"$list": [chr_('# jags.model("model_def.R")', 'jags.model("other.R")')]},
)
jags("ordinary", chr_("x <- 1", "y <- 2"), file_name="analysis.R")
jags("empty", {"$chr": []})

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

case("repro_bioc_packages", "metacheck:::.repro_bioc_packages", f"{CORE}._repro_bioc_packages", {})
case("repro_common_pkgs", "metacheck:::.repro_common_pkgs", f"{CORE}._repro_common_pkgs", {})
# .repro_base_packages() depends on the R installation: tested in test_static.py
MSGS = [
    "installation of package 'x' had non-zero exit status",
    "configure: error: libxml2 not found",
    "make: *** [x.o] Error 1",
    "g++: fatal error",
    "Could not resolve host: cloud.r-project.org",
    "Timeout was reached: [cran] Operation timed out",
    "Couldn't connect to server",
    "there is no package called 'Rcpp'",
    "dependency 'abc' is not available for package 'xyz'",
    "package 'foo' is not available for this version of R",
    "package 'foo' was built under R version 4.6.0",
    "installed but package 'foo' is not loadable",
    "",
    None,
    "HTTP error 404. Not Found",
    # issue #421: the install step's own "not available" warning, and with the
    # CRAN Archive retry's result appended
    "package 'list.packages' is not available for this version of R",
    "package 'list.packages' is not available for this version of R"
    " (CRAN Archive retry also failed: package not found in the CRAN Archive)",
    "package 'x' is not available for this version of R"
    " (CRAN Archive retry also failed: could not reach the CRAN Archive listing)",
    "package not found in the CRAN Archive",
]
for i, msg in enumerate(MSGS):
    case(
        f"repro_classify_install_message.{i:02d}",
        "metacheck:::.repro_classify_install_message",
        f"{CORE}._repro_classify_install_message",
        {"msg": msg if msg is not None else {"$NA": True}},
    )


# .repro_not_loadable_msg(): the message for a package that cannot be loaded after
# its install step (issue #421)
NOT_AVAILABLE = "package 'list.packages' is not available for this version of R"
for id_, warnings in [
    ("not_available", chr_("other warning", NOT_AVAILABLE)),
    ("not_available_twice", chr_(NOT_AVAILABLE, NOT_AVAILABLE)),
    (
        "two_unavailable",
        chr_(NOT_AVAILABLE, "unrelated", "package 'b' is not available (for R version 4.5.3)"),
    ),
    ("no_warnings", chr_()),
    ("other_warnings", chr_("installation of package 'x' had non-zero exit status")),
    ("null_warnings", {"$null": True}),
]:
    case(
        f"repro_not_loadable_msg.{id_}",
        "metacheck:::.repro_not_loadable_msg",
        f"{CORE}._repro_not_loadable_msg",
        {"pkg": "list.packages", "warnings": warnings},
    )

# .repro_cran_archive_install(): a 404 on the Archive listing means the package is not
# there, not that CRAN cannot be reached (issue #421); the request is mocked on both sides
for mode in ("not_found", "server_error", "unreachable", "no_tarballs"):
    expr(
        f"repro_cran_archive_install.{mode}",
        f"rc_archive_install('{mode}')",
        f"lambda m: m.archive_install('{mode}')",
    )


def fep(id_: str, name: str, cands: list[str]) -> None:
    cands_r = "character(0)" if not cands else "c(" + ", ".join(f'"{c}"' for c in cands) + ")"
    expr(
        f"repro_find_export_pkg.{id_}",
        f'metacheck:::.repro_find_export_pkg("{name}", {cands_r})',
        f"lambda m: (m.need_r(), m.core._repro_find_export_pkg({name!r}, {cands!r}))[1]",
    )


fep("unique", "test_that", ["testthat", "dplyr"])
fep("none", "not_a_real_exported_name_xyz", ["testthat", "dplyr"])
fep("empty", "test_that", [])
fep("ambiguous", "%>%", ["dplyr", "magrittr"])
fep("not_installed", "mutate", ["notapkg_xyz123", "dplyr", "", "dplyr"])

# ---------------------------------------------------------------------------
# repro_materialize_layout() / repro_write_scripts()
# ---------------------------------------------------------------------------

L = "tests/repro_core/fixtures/layout"
expr(
    "repro_materialize_layout.copies",
    "rc_materialize(data.frame(file_name = c('demographics.csv', 'sub/scores.csv', 'missing.csv', 'x.zip'),"
    " target_path = c('study-ex1/data/study-demographics_data.csv', 'data/scores_data.csv', 'data/missing.csv', NA)),"
    f" data.frame(file_name = c('demographics.csv', 'other/SCORES.csv', 'x.zip'), file_location = c('{L}/demographics.csv', '{L}/scores.csv', '{L}/x.zip')))",
    "lambda m: m.materialize(m.pd.DataFrame({'file_name': ['demographics.csv', 'sub/scores.csv', 'missing.csv', 'x.zip'],"
    " 'target_path': ['study-ex1/data/study-demographics_data.csv', 'data/scores_data.csv', 'data/missing.csv', None]}),"
    f" m.pd.DataFrame({{'file_name': ['demographics.csv', 'other/SCORES.csv', 'x.zip'], 'file_location': ['{L}/demographics.csv', '{L}/scores.csv', '{L}/x.zip']}}))",
)
expr(
    "repro_materialize_layout.original_target",
    "rc_materialize(data.frame(file_name = c('math.dta', 'scores.csv'),"
    " target_path = c('study-ex1/data/study-math_data.csv', 'data/scores.csv'),"
    " original_target = c('study-ex1/data/math.dta', NA)),"
    f" data.frame(file_name = c('math.dta', 'scores.csv'), file_location = c('{L}/math.dta', '{L}/scores.csv')))",
    "lambda m: m.materialize(m.pd.DataFrame({'file_name': ['math.dta', 'scores.csv'],"
    " 'target_path': ['study-ex1/data/study-math_data.csv', 'data/scores.csv'],"
    " 'original_target': ['study-ex1/data/math.dta', None]}),"
    f" m.pd.DataFrame({{'file_name': ['math.dta', 'scores.csv'], 'file_location': ['{L}/math.dta', '{L}/scores.csv']}}))",
)
expr(
    "repro_materialize_layout.no_plan",
    "rc_materialize(NULL, NULL)",
    "lambda m: m.materialize(None, None)",
)
expr(
    "repro_materialize_layout.source_not_found",
    "rc_materialize(data.frame(file_name = 'demographics.csv', target_path = 'data/demographics.csv'),"
    " data.frame(file_name = 'demographics.csv', file_location = NA_character_))",
    "lambda m: m.materialize(m.pd.DataFrame({'file_name': ['demographics.csv'], 'target_path': ['data/demographics.csv']}),"
    " m.pd.DataFrame({'file_name': ['demographics.csv'], 'file_location': [None]}))",
)

EMPTY_RW_R = (
    "data.frame(ref = character(0), basename = character(0), matched = logical(0),"
    " target = character(0), ambiguous = logical(0), n_candidates = integer(0), is_call = logical(0))"
)
EMPTY_RW_PY = "m.core._rewrite_frame([])"


def ws(id_: str, r_args: str, py_args: str) -> None:
    expr(
        f"repro_write_scripts.{id_}",
        f"rc_write_scripts({r_args})",
        f"lambda m: m.write_scripts({py_args})",
    )


ws(
    "literal_rewrite",
    "list(analysis.R = 'd <- read.csv(\"data/demographics.csv\")'),"
    " list(analysis.R = data.frame(ref = 'data/demographics.csv', basename = 'demographics.csv',"
    " matched = TRUE, target = 'study-ex1/data/study-demographics_data.csv', ambiguous = FALSE,"
    " n_candidates = 1L, is_call = FALSE)),"
    " data.frame(file_name = 'analysis.R', target_path = 'analysis.R')",
    "{'analysis.R': 'd <- read.csv(\"data/demographics.csv\")'},"
    " {'analysis.R': m.core._rewrite_frame([('data/demographics.csv', 'demographics.csv', True,"
    " 'study-ex1/data/study-demographics_data.csv', False, 1, False)])},"
    " m.pd.DataFrame({'file_name': ['analysis.R'], 'target_path': ['analysis.R']})",
)
ws(
    "setwd",
    f"list(analysis.R = c('setwd(\"/Users/author/project\")', 'x <- 1', '  setwd(dir)', 'y <- 2 # setwd(\"z\")')), list(analysis.R = {EMPTY_RW_R}),"
    " data.frame(file_name = 'analysis.R', target_path = 'analysis.R')",
    f"{{'analysis.R': ['setwd(\"/Users/author/project\")', 'x <- 1', '  setwd(dir)', 'y <- 2 # setwd(\"z\")']}}, {{'analysis.R': {EMPTY_RW_PY}}},"
    " m.pd.DataFrame({'file_name': ['analysis.R'], 'target_path': ['analysis.R']})",
)
ws(
    "write_redirect",
    f"list(analysis.R = c('write.csv(x, \"results/out.csv\")', 'd %>%', '  write.csv(paste0(\"dir/\", Sys.Date(), \".csv\"))', '')),"
    f" list(analysis.R = {EMPTY_RW_R}), data.frame(file_name = 'analysis.R', target_path = 'code/analysis.R')",
    f"{{'analysis.R': ['write.csv(x, \"results/out.csv\")', 'd %>%', '  write.csv(paste0(\"dir/\", Sys.Date(), \".csv\"))', '']}},"
    f" {{'analysis.R': {EMPTY_RW_PY}}}, m.pd.DataFrame({{'file_name': ['analysis.R'], 'target_path': ['code/analysis.R']}})",
)
ws(
    "font_family",
    f"list(plot.R = c('theme_few(base_family = \"Times New Roman\")', 'text(1, 1, family=\\'Arial\\')')), list(plot.R = {EMPTY_RW_R}),"
    " data.frame(file_name = 'plot.R', target_path = 'plot.R')",
    f"{{'plot.R': ['theme_few(base_family = \"Times New Roman\")', \"text(1, 1, family='Arial')\"]}}, {{'plot.R': {EMPTY_RW_PY}}},"
    " m.pd.DataFrame({'file_name': ['plot.R'], 'target_path': ['plot.R']})",
)
ws(
    "no_plan_target",
    f"list(`sub/orphan.R` = 'x <- 1'), list(`sub/orphan.R` = {EMPTY_RW_R}), NULL",
    f"{{'sub/orphan.R': 'x <- 1'}}, {{'sub/orphan.R': {EMPTY_RW_PY}}}, None",
)
ws(
    "inject_libs",
    f"list(uses_testthat.R = 'test_that(\"x\", {'{'} expect_true(TRUE) {'}'})', plain.R = 'x <- 1'),"
    f" list(uses_testthat.R = {EMPTY_RW_R}), data.frame(file_name = 'uses_testthat.R', target_path = 'uses_testthat.R'),"
    " inject_libs = c(uses_testthat.R = 'testthat', other.R = 'dplyr')",
    f"{{'uses_testthat.R': 'test_that(\"x\", {{ expect_true(TRUE) }})', 'plain.R': 'x <- 1'}},"
    f" {{'uses_testthat.R': {EMPTY_RW_PY}}}, m.pd.DataFrame({{'file_name': ['uses_testthat.R'], 'target_path': ['uses_testthat.R']}}),"
    " inject_libs={'uses_testthat.R': 'testthat', 'other.R': 'dplyr'}",
)
ws(
    "call_and_ambiguous",
    "list(`ex1/a.R` = c('wd <- \"exp1\"', 'd <- read.csv(sprintf(\"%s_data.csv\", wd))', 'e <- read.csv(\"demo.csv\")', 'f <- read.csv(\"demo.csv\")')),"
    " list(`ex1/a.R` = data.frame(ref = c('sprintf(\"%s_data.csv\", wd)', 'demo.csv', 'x.csv'),"
    " basename = c('exp1_data.csv', 'demo.csv', 'x.csv'), matched = c(TRUE, TRUE, FALSE),"
    " target = c('data/exp1_data.csv', NA, NA), ambiguous = c(FALSE, TRUE, FALSE),"
    " n_candidates = c(1L, 2L, 0L), is_call = c(TRUE, FALSE, FALSE))),"
    " data.frame(file_name = c('ex1/a.R', 'b.R'), target_path = c('study-ex1/code/a.R', NA))",
    "{'ex1/a.R': ['wd <- \"exp1\"', 'd <- read.csv(sprintf(\"%s_data.csv\", wd))', 'e <- read.csv(\"demo.csv\")', 'f <- read.csv(\"demo.csv\")']},"
    " {'ex1/a.R': m.core._rewrite_frame([('sprintf(\"%s_data.csv\", wd)', 'exp1_data.csv', True, 'data/exp1_data.csv', False, 1, True),"
    " ('demo.csv', 'demo.csv', True, None, True, 2, False), ('x.csv', 'x.csv', False, None, False, 0, False)])},"
    " m.pd.DataFrame({'file_name': ['ex1/a.R', 'b.R'], 'target_path': ['study-ex1/code/a.R', None]})",
)

# ---------------------------------------------------------------------------
# repro_run_scripts() (runs R on both sides)
# ---------------------------------------------------------------------------


def rs(id_: str, scripts: list[str], order: list[str] | None = None, **kw: Any) -> None:
    r_scripts = "c(" + ", ".join(f'"{s}"' for s in scripts) + ")"
    r_order = "NULL" if order is None else "c(" + ", ".join(f'"{s}"' for s in order) + ")"
    r_kw = "".join(f", {k} = {v[0]}" for k, v in kw.items())
    py_kw = "".join(f", {k}={v[1]}" for k, v in kw.items())
    expr(
        f"repro_run_scripts.{id_}",
        f"rc_run_scripts({r_scripts}, order = {r_order}{r_kw})",
        f"lambda m: m.run_scripts({scripts!r}, order={order!r}{py_kw})",
    )


rs("ok", ["ok.R"], timeout=("60", "60"))
rs("errors", ["errors.R"], timeout=("60", "60"))
rs("undefined_var", ["undefined_var.R", "undefined_fn.R"], timeout=("60", "60"))
rs("model_object", ["model_object.R"], timeout=("60", "60"))
rs("many_tests", ["many_tests.R"], timeout=("60", "60"))
rs(
    "skip_and_parse",
    ["ok.R", "errors.R", "missing_input.R"],
    skip=('"errors.R"', "['errors.R']"),
    parses=("c(ok.R = TRUE, missing_input.R = FALSE)", "{'ok.R': True, 'missing_input.R': False}"),
)
rs(
    "chain_order",
    ["reads_written.R", "writes_then_reads.R", "bad_setwd.R"],
    order=["writes_then_reads.R", "zzz.R", "reads_written.R"],
    timeout=("60", "60"),
)
rs("missing_input", ["missing_input.R", "setwd_fail.R"], timeout=("60", "60"))
rs("edge_errors", ["parse_err.R", "quit0.R", "quit3.R", "nocall.R", "multi.R", "comment_only.R"])
rs("warn_err", ["warn_err.R"])
rs("nopkg_failed_dep", ["nopkg.R"], failed_deps=('"notapkg_xyz"', "['notapkg_xyz']"))
rs("lib_dir", ["libpath.R"], lib=("TRUE", "True"))
# 10 s, not less: R must start and reach the first cat() before the limit, and a
# busy CI host can take over 3 s to start R (sleep.R sleeps 60 s either way)
rs("timeout", ["sleep.R"], timeout=("10", "10"))
case(
    "repro_run_scripts.empty",
    "repro_run_scripts",
    f"{CORE}.repro_run_scripts",
    {"run_tbl": {"$df": {}}, "order": {"$chr": []}},
)
case(
    "repro_run_scripts.null",
    "repro_run_scripts",
    f"{CORE}.repro_run_scripts",
    {"run_tbl": {"$null": True}, "order": {"$chr": []}},
)

# ---------------------------------------------------------------------------
# repro_install_deps() (no network: empty input, packages already installed)
# ---------------------------------------------------------------------------

case(
    "repro_install_deps.empty",
    "repro_install_deps",
    f"{CORE}.repro_install_deps",
    {"install_deps": {"$null": True}, "lib_dir": "unused"},
)
expr(
    "repro_install_deps.main_lib_skip",
    "repro_install_deps(data.frame(package = c('stats', 'utils'), source = 'cran', ref = NA),"
    " lib_dir = tempfile('lib'), cran_to_main_lib = TRUE)",
    "lambda m: (m.need_r(), m.tmp(lambda d: m.core.repro_install_deps(m.pd.DataFrame({'package': ['stats', 'utils'],"
    " 'source': ['cran', 'cran'], 'ref': [None, None]}), d + '/lib', cran_to_main_lib=True)))[1]",
)

# ---------------------------------------------------------------------------
# Docker backend (pure helpers; no Docker needed)
# ---------------------------------------------------------------------------

for id_, versions, use in [
    ("default", "c('4.3.1')", False),
    ("declared", "c('4.3.1')", True),
    ("none_declared", "character(0)", True),
    ("newline", "c(NA, '\\n4.5.0 patched', '\\n4.5.0')", True),
    ("two_part", "c(' 4.2 ')", True),
]:
    py_versions = {
        "c('4.3.1')": "['4.3.1']",
        "character(0)": "[]",
        "c(NA, '\\n4.5.0 patched', '\\n4.5.0')": "[None, '\\n4.5.0 patched', '\\n4.5.0']",
        "c(' 4.2 ')": "[' 4.2 ']",
    }[versions]
    expr(
        f"repro_docker_image_for.{id_}",
        f"metacheck:::.repro_docker_image_for({versions}, use_declared_version = {'TRUE' if use else 'FALSE'})",
        f"lambda m: m.docker._repro_docker_image_for({py_versions}, use_declared_version={use})",
    )
for id_, host, root in [
    ("inside", "/nonexistent_mc/sandbox/a/b.R", "/nonexistent_mc/sandbox"),
    ("root_itself", "/nonexistent_mc/sandbox", "/nonexistent_mc/sandbox"),
    ("outside", "/elsewhere_mc/x/run.R", "/nonexistent_mc/sandbox"),
    ("trailing_slash_root", "/nonexistent_mc/sandbox/c.R", "/nonexistent_mc/sandbox/"),
]:
    case(
        f"repro_docker_container_path.{id_}",
        "metacheck:::.repro_docker_container_path",
        f"{DOCKER}._repro_docker_container_path",
        {"host_path": host, "sandbox_root": root},
    )
case(
    "repro_docker_resource_args.unset",
    "metacheck:::.repro_docker_resource_args",
    f"{DOCKER}._repro_docker_resource_args",
    {},
)
expr(
    "repro_docker_resource_args.set",
    "withr::with_options(list(metacheck.docker_resource_limits = list(cpus = 2, memory_gb = 3.5)),"
    " metacheck:::.repro_docker_resource_args())",
    "lambda m: m.with_options({'metacheck.docker_resource_limits': {'cpus': 2, 'memory_gb': 3.5}},"
    " m.docker._repro_docker_resource_args)",
)
case(
    "repro_install_deps_docker.empty",
    "repro_install_deps_docker",
    f"{DOCKER}.repro_install_deps_docker",
    {"install_deps": {"$df": {"package": []}}, "lib_dir": "unused"},
)
# the install.R the container runs (its warning capture is issue #421)
for id_, pkgs, srcs, refs in [
    ("cran", ["dplyr", "list.packages"], ["cran", "cran"], [None, None]),
    (
        "mixed",
        ["edgeR", "mypkg", "tarpkg"],
        ["bioc", "github", "url"],
        [None, "u/mypkg", "http://x/t.tar.gz"],
    ),
]:
    r_ref = "c(" + ", ".join("NA_character_" if r is None else f"'{r}'" for r in refs) + ")"
    r_pkgs = "c(" + ", ".join(f"'{p}'" for p in pkgs) + ")"
    r_srcs = "c(" + ", ".join(f"'{s}'" for s in srcs) + ")"
    expr(
        f"repro_install_deps_docker.script.{id_}",
        f"rc_docker_install_script(data.frame(package = {r_pkgs}, source = {r_srcs}, ref = {r_ref}))",
        f"lambda m: m.docker._install_script(m.pd.DataFrame({{'package': {pkgs!r},"
        f" 'source': {srcs!r}, 'ref': {refs!r}}}))",
    )
case(
    "repro_run_scripts_docker.empty",
    "repro_run_scripts_docker",
    f"{DOCKER}.repro_run_scripts_docker",
    {"run_tbl": {"$null": True}, "order": {"$chr": []}, "sandbox_root": "/nonexistent_mc"},
)

# ---------------------------------------------------------------------------
# module tables
# ---------------------------------------------------------------------------

T = "tests/repro_core/fixtures/tables_rds"
for module, element in [
    ("all_p_values", "table"),
    ("all_p_values", "summary_table"),
    ("marginal", "table"),
    ("marginal", "report"),
    ("not_a_module", "table"),
]:
    case(
        f"collect_module_tables.rds.{module}.{element}",
        "metacheck:::collect_module_tables",
        f"{TABLES}.collect_module_tables",
        {"results_dir": {"$file": T}, "module": module, "element": element},
    )
case(
    "collect_module_tables.no_files",
    "metacheck:::collect_module_tables",
    f"{TABLES}.collect_module_tables",
    {"results_dir": {"$file": "tests/repro_core/fixtures/scripts"}, "module": "marginal"},
)
expr(
    "load_module_tables.rds",
    f"(function(x) list(x, names(x$prev_outputs), x$prev_outputs$marginal$table, x$prev_outputs$marginal$traffic_light))"
    f"(metacheck:::.load_module_tables('{T}', 'to_err_is_human', demopaper()))",
    f"lambda m: (lambda x: [x, list(x.prev_outputs), x.prev_outputs['marginal'].table,"
    f" x.prev_outputs['marginal'].traffic_light])(m.tables._load_module_tables('{T}', 'to_err_is_human', m.pc.demopaper()))",
)
case(
    "load_module_tables.missing",
    "metacheck:::.load_module_tables",
    f"{TABLES}._load_module_tables",
    {"results_dir": {"$file": T}, "paper_id": "no_such_paper", "paper": {"$null": True}},
)
# a round trip on each side: capture a chain, then collect / reload it
CHAIN_R = "suppressWarnings(report_module_run({paper}, c('marginal', 'all_p_values')))"
CHAIN_PY = "m.pc.report_module_run({paper}, ['marginal', 'all_p_values'])"
for pid, r_paper, py_paper in [
    ("demo", "demopaper()", "m.pc.demopaper()"),
    (
        "psychsci",
        "read('upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json')",
        "m.pc.read('upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json')",
    ),
]:
    expr(
        f"capture_module_tables.roundtrip.{pid}",
        "(function() { d <- tempfile(); chain <- "
        + CHAIN_R.format(paper=r_paper)
        + "; p <- metacheck:::capture_module_tables(chain, d);"
        " x <- metacheck:::.load_module_tables(d, sub('[.]rds$', '', basename(p)), NULL);"
        " list(collect_all = metacheck:::collect_module_tables(d, 'all_p_values'),"
        " collect_sum = metacheck:::collect_module_tables(d, 'all_p_values', 'summary_table'),"
        " loaded = x, prev = names(x$prev_outputs), marginal = x$prev_outputs$marginal) })()",
        "lambda m: m.tmp(lambda d: (lambda chain: (lambda p: (lambda x:"
        " {'collect_all': m.tables.collect_module_tables(d, 'all_p_values'),"
        " 'collect_sum': m.tables.collect_module_tables(d, 'all_p_values', 'summary_table'),"
        " 'loaded': x, 'prev': list(x.prev_outputs), 'marginal': x.prev_outputs['marginal']})"
        "(m.tables._load_module_tables(d, __import__('os').path.basename(p)[:-5], None)))"
        "(m.tables.capture_module_tables(chain, d)))(" + CHAIN_PY.format(paper=py_paper) + "))",
    )
expr(
    "capture_module_tables.paper_id_arg",
    "(function() { d <- tempfile(); chain <- "
    + CHAIN_R.format(paper="demopaper()")
    + "; p1 <- metacheck:::capture_module_tables(chain, d, paper_id = 'given');"
    " p2 <- metacheck:::capture_module_tables(list(), d);"
    " sort(sub('[.]rds$', '', list.files(d))) })()",
    "lambda m: m.tmp(lambda d: (lambda chain: [m.tables.capture_module_tables(chain, d, paper_id='given'),"
    " m.tables.capture_module_tables([], d), sorted(f[:-5] for f in __import__('os').listdir(d))][2])"
    "(" + CHAIN_PY.format(paper="m.pc.demopaper()") + "))",
)


def main() -> None:
    header = (
        "# Parity cases for R/reproducibility_check.R, R/reproducibility_check_docker.R and\n"
        "# the module-table capture of R/module.R. Generated by\n"
        "# tests/repro_core/make_parity_cases.py; filesystem/R cases use\n"
        "# tests/repro_core/parity_support.py (Python) and parity_helpers.R (R).\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "repro_core", "cases": cases},
            Dumper=Dumper,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
