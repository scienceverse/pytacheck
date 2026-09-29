"""Generate ``parity/cases/repro_core_review.yaml`` (area ``repro_core_review``).

Edge cases found in an adversarial review of the ``repro_core`` port: R's
list lookups by an empty/NA name, collation, replacement escapes, zero-column
results, quiet ``dir.create()`` failures, named-logical ``parses`` and the
module-table readers on R-written fixtures (``make_review_fixtures.R``).

Run ``python tests/repro_core/make_review_cases.py``, then
``python -m parity generate --area repro_core_review``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "repro_core_review.yaml"
RUN = "tests.repro_core.parity_support.run"
CORE = "metacheck.repro.core"
DOCKER = "metacheck.repro.docker"
TABLES = "metacheck.repro.tables"
HELPERS = 'source("tests/repro_core/parity_helpers.R", local = TRUE)'
TR = "tests/repro_core/fixtures/tables_review"


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


def r_str(s: str | None) -> str:
    if s is None:
        return "NA_character_"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def r_chr(xs: list[str | None]) -> str:
    return "character(0)" if not xs else "c(" + ", ".join(r_str(x) for x in xs) + ")"


def r_list_of_chr(xs: list[list[str | None]]) -> str:
    return "list(" + ", ".join(r_chr(x) for x in xs) + ")"


# ---------------------------------------------------------------------------
# repro_run_order(): names R's `writer_of[[w]]` never finds, ties, collation
# ---------------------------------------------------------------------------


def ro(id_: str, names: list[str], edges: str = "NULL", py_edges: str = "None", **io: Any) -> None:
    r_io = "".join(f", {k} = {r_list_of_chr(v)}" for k, v in io.items())
    py_io = "".join(f", {k}={v!r}" for k, v in io.items())
    expr(
        f"repro_run_order.{id_}",
        f"rc_run_order_full(rc_files_df({r_chr(names)}{r_io}), {edges})",
        f"lambda m: m.run_order_full(m.files_df({names!r}{py_io}), {py_edges})",
    )


ro(
    "na_and_empty_basenames",
    ["a.R", "b.R", "c.R", "d.R"],
    reads=[[None], [""], ["x.csv"], ["na"]],
    writes=[[""], [None], ["X.csv"], []],
    sources=[[], [None], [""], []],
)
ro(
    "same_basename_sources",
    ["s1/x.R", "s2/x.R", "main.R", "sub/Main.R"],
    sources=[[], [], ["x.R"], ["X.r", "main.r"]],
)
ro(
    "ties_and_extra_edges",
    ["z/x.R", "a/x.R", "b.R", "a.R"],
    edges='list(c("b.R", "a/x.R"), c("nope.R", "b.R"), c("a.R", "a.R"), c("a.R", "z/x.R"))',
    py_edges="[('b.R', 'a/x.R'), ('nope.R', 'b.R'), ('a.R', 'a.R'), ('a.R', 'z/x.R')]",
    reads=[[], [], ["q.csv"], []],
    writes=[["q.csv", "Q.CSV"], [], [], []],
)
ro(
    "collation_and_big_numbers",
    [
        "a_b.R",
        "a.b.R",
        "ab.R",
        "Ab.R",
        "a-b.R",
        "a b.R",
        "_x.R",
        "x9.R",
        "x10.R",
        "x09.R",
        "x99999999999.R",
        "y2147483648_1.R",
        "y2147483647_1.R",
    ],
)
ro(
    "duplicate_file_names",
    ["dup.R", "dup.R", "w.R"],
    reads=[["o.csv"], [], []],
    writes=[[], [], ["o.csv", "o.csv"]],
    sources=[[], ["w.R"], []],
)

# ---------------------------------------------------------------------------
# .repro_redirect_writes() / .repro_format_call_refs() / simple string vars
# ---------------------------------------------------------------------------

REDIRECT_CODE = [
    'write.csv (df, "out.csv")',
    'save(a, b, file = "ab.RData")',
    'ggsave("p.png")',
    'saveRDS(m, file.path("out", "m.rds"))',
    'write.csv(df, "noext", outfile)',
    'outfile <- "a.tsv"',
    "outfile <- 3",
    'export(df, "x.xlsx")',
    'data.table::fwrite(dt, sprintf("%s.csv", nm))',
    'write_csv(df, paste("a", "b"))',
    'x <- "é"; write.csv(y, "ü.csv")',
    'writeLines(txt, con = "C:\\\\out\\\\notes.txt")',
    "write.table(x,\n  'multi.tsv',\n  sep = '\\t')",
    '"write.csv(q, \\"in_string.csv\\")"',
]
case(
    "repro_redirect_writes.variety",
    "metacheck:::.repro_redirect_writes",
    f"{CORE}._repro_redirect_writes",
    {"code_text": chr_(*REDIRECT_CODE)},
)

FORMAT_CODE = [
    'x <- "é"; wd <- "D:/data"',
    'd <- read.csv(sprintf("%s/x.csv", wd))',
    'wd <- "E:/d"',
    'e <- read.csv(sprintf("%s/%d_y.csv", wd, n))',
    'n <- "7"',
    'g <- read.csv(sprintf("%s/%d_y.csv", wd, "lit"))',
    'h <- read.csv(paste0("a/b.csv"  ,  wd ,n))',
    'k <- sprintf("%s.csv", wd, n)',
    'p <- sprintf("%s/%s.csv", pct, wd)',
    'pct <- "100%s"',
]
case(
    "repro_format_call_refs.variety",
    "metacheck:::.repro_format_call_refs",
    f"{CORE}._repro_format_call_refs",
    {"code_text": chr_(*FORMAT_CODE)},
)
# a value with backslash escapes is used as an R replacement string: \U and \L
# change the case of back-references only (there are none), \1 is empty
case(
    "repro_format_call_refs.replacement_escapes",
    "metacheck:::.repro_format_call_refs",
    f"{CORE}._repro_format_call_refs",
    {
        "code_text": chr_(
            'wd <- "C:\\Users\\me\\q"',
            'd <- read.csv(sprintf("%s/x.csv", wd))',
            'w2 <- "a\\1b\\Ec\\Ld"',
            'e <- read.csv(sprintf("%s/y.csv", w2))',
            'w3 <- "tail\\\\"',
            'f <- read.csv(sprintf("%s/z.csv", w3))',
        )
    },
)
case(
    "repro_simple_string_vars.variety",
    "metacheck:::.repro_simple_string_vars",
    f"{CORE}._repro_simple_string_vars",
    {
        "code_text": chr_(
            "x = 'a'",
            'x <<- "b"',
            "y <- 'c'  ",
            'z <- "d\\"e"',
            " w <- 'f'",
            "v <- 'g' # c",
            "x <- 'h'",
            "u <- 'mixed\"",
            ".t <- ''",
        )
    },
)

# ---------------------------------------------------------------------------
# repro_rewrite_paths(), repro_dependencies(), repro_file_io(), repro_defined_vars()
# ---------------------------------------------------------------------------

case(
    "repro_rewrite_paths.original_ext_case",
    "repro_rewrite_paths",
    f"{CORE}.repro_rewrite_paths",
    {
        "code_text": chr_(
            'd <- haven::read_dta("raw/MATH.DTA")',
            'e <- read.csv("math_data.csv")',
            'f <- read.csv("Scores.CSV"); g <- read.csv("scores.csv")',
        ),
        "file_name": "analysis.R",
        "plan": {
            "$df": {
                "file_name": ["raw/math.dta", "scores.csv", "other/scores.csv"],
                "target_path": [
                    "data/math_data.csv",
                    "study-ex1/data/scores_data.csv",
                    "study-ex2/data/scores_data.csv",
                ],
                "original_target": ["data/math.dta", None, ""],
            }
        },
    },
)
case(
    "repro_dependencies.pooled_collation",
    "repro_dependencies",
    f"{CORE}.repro_dependencies",
    {
        "code_text": {
            "$list": [
                chr_("library(Matrix)", "library(dplyr)", "library(edgeR)"),
                chr_(
                    "library(DBI)",
                    "library(data.table)",
                    "library(R.utils)",
                    "library(rlang)",
                    "library(Rcpp)",
                    "library(dataverse)",
                    "library(abc)",
                    "library(ABC)",
                    "remotes::install_github('me/edgeR@dev')",
                ),
            ]
        }
    },
)
case(
    "repro_file_io.variety",
    "repro_file_io",
    f"{CORE}.repro_file_io",
    {
        "code_text_list": {
            "a.R": chr_(
                'd <- read.csv("Data/X.CSV")',
                'write.csv(d, "data/x.csv")',
                'source("lib/Helpers.R")',
                'e <- readRDS("cache.rds")',
                'saveRDS(e, "cache.rds")',
                '# write.csv(d, "commented.csv")',
                'SaveRDS(e, "upper.rds")',
            ),
            "b.R": chr_("x <- 1"),
        }
    },
)
case(
    "repro_defined_vars.variety",
    "repro_defined_vars",
    f"{CORE}.repro_defined_vars",
    {
        "code_text_list": {
            "f1.R": chr_(
                "x == 1",
                "  y <- 2",
                'assign("z", 1)',
                "f <- function() 1",
                ".a <- 1",
                "_b <- 1",
                "a.b_c <- 1",
                "x<-1",
                "x <<- 2",
                "names(x) <- 'q'",
                "k = 3",
                "k == 4",
                " assign('w', 2)",
                "x <- 5 # again",
            ),
            "f2.R": chr_("# nothing here"),
        }
    },
)

# ---------------------------------------------------------------------------
# repro_missing_inputs(): numeric-looking sizes, NA locations, near-misses
# ---------------------------------------------------------------------------

case(
    "repro_missing_inputs.variety",
    "repro_missing_inputs",
    f"{CORE}.repro_missing_inputs",
    {
        "refs": chr_(
            "big.csv",
            "C.CSV",
            "d.csv",
            "e.csv",
            "data.csv",
            "data2.csv",
            "q.csv",
            "dta.csv",
            "data_final.csv",
            "datx.csv",
            "zz.R",
            "data_fin.csv",
            "dir\\big.csv",
            "half.csv",
        ),
        "plan": {"$null": True},
        "structure_df": {
            "$df": {
                "file_name": ["x/data.csv", "y/Data2.csv", "q.csv", "dat.csv", "data_final.xlsx"],
                "file_location": [None, "", "/nonexistent_mc", None, None],
            }
        },
        "skipped": {
            "$df": {
                "file_name": ["a/Big.csv", "c.csv", "d.csv", "e.csv", "half.csv"],
                "file_size": ["1572864", "2621440", "abc", " 1e6 ", "524288"],
            }
        },
    },
)
case(
    "repro_missing_inputs.na_candidates",
    "repro_missing_inputs",
    f"{CORE}.repro_missing_inputs",
    {
        "refs": chr_("zzz.csv", "qqq.csv", "abcdefgh.csv"),
        "plan": {"$null": True},
        "structure_df": {
            "$df": {
                "file_name": [None, "zzz.xlsx", "abcdefgx.csv"],
                "file_location": [None, None, None],
            }
        },
    },
)

# ---------------------------------------------------------------------------
# content sniffing and JAGS models
# ---------------------------------------------------------------------------

SNIFF_R = [
    '"\\ufeff{\\"a\\": 1}"',
    'c("  ", " {\\"a\\": \\"}\\" }  ", "")',
    "\"['a', ']']\"",
    '"{\\"a\\": [1, {\\"b\\": \\"\\\\\\"}\\"}]}"',
    "\"<?XML version='1.0'?>\"",
    '"<htmlx>"',
    '"<!doctype  HTML>"',
    '"\\n\\t[1,\\n 2]\\n# trailing comment that is long"',
    '"[1, 2]  x"',
]
SNIFF_PY = [
    "'\\ufeff{\"a\": 1}'",
    "['  ', ' {\"a\": \"}\" }  ', '']",
    "\"['a', ']']\"",
    '\'{"a": [1, {"b": "\\\\"}"}]}\'',
    "\"<?XML version='1.0'?>\"",
    "'<htmlx>'",
    "'<!doctype  HTML>'",
    "'\\n\\t[1,\\n 2]\\n# trailing comment that is long'",
    "'[1, 2]  x'",
]
expr(
    "repro_content_sniff.variety",
    "vapply(list(" + ", ".join(SNIFF_R) + "), metacheck:::.repro_content_sniff, character(1))",
    "lambda m: [m.core._repro_content_sniff(x) for x in [" + ", ".join(SNIFF_PY) + "]]",
)
expr(
    "repro_is_jags_model.variety",
    "(function(jg) c(jg(NULL, 'dir/JAGS_Model/m.txt'),"
    " jg('x <- 1', 'm.R', c('jags.model(\"m.R\")', 'y')),"
    " jg('x <- 1', 'm.R', 'run.jags(\"other/m.R\")'),"
    " jg('x', 'a\\\\b\\\\m.R', list(a = 'R2jags::jags(file = \"c:\\\\\\\\x\\\\\\\\m.R\")')),"
    " jg('model{', ''), jg(c('', '  # c', '  model {'), 'x.R'),"
    " jg(c('# model {', 'x <- 1'), 'bugs_scripts/m.R'),"
    " jg('y <- 2', 'jags_script.R', list(NULL, character(0)))))(metacheck:::.repro_is_jags_model)",
    "lambda m: (lambda jg: [jg(None, 'dir/JAGS_Model/m.txt'),"
    " jg('x <- 1', 'm.R', ['jags.model(\"m.R\")', 'y']),"
    " jg('x <- 1', 'm.R', 'run.jags(\"other/m.R\")'),"
    " jg('x', 'a\\\\b\\\\m.R', {'a': 'R2jags::jags(file = \"c:\\\\\\\\x\\\\\\\\m.R\")'}),"
    " jg('model{', ''), jg(['', '  # c', '  model {'], 'x.R'),"
    " jg(['# model {', 'x <- 1'], 'bugs_scripts/m.R'),"
    " jg('y <- 2', 'jags_script.R', [None, []])])(m.core._repro_is_jags_model)",
)

# ---------------------------------------------------------------------------
# repro_materialize_layout() / repro_write_scripts()
# ---------------------------------------------------------------------------

expr(
    "repro_materialize_layout.quiet_failures",
    "rc_materialize_review()",
    "lambda m: m.materialize_review()",
)
expr(
    "repro_write_scripts.empty",
    "(function() { root <- tempfile(); dir.create(root); repro_write_scripts(list(), list(), NULL, root) })()",
    "lambda m: m.tmp(lambda d: m.core.repro_write_scripts({}, {}, None, d))",
)
expr(
    "repro_write_scripts.variety",
    "rc_write_scripts(list(a.R = c('setwd(\"C:/x\")', 'NA', '  setwd(dir)', 'x <- 1; setwd(\"y\")'),"
    ' b.R = \'d <- read.csv("data/a.csv")\\nsetwd("/z")\\nwrite.csv(d, "o.csv")\\n\\n\','
    " `sub/c.R` = c('plot(1, family=\"Times\")', 'theme(base_family = \\'Arial\\', family=\"\")'),"
    " d.R = 'a <- 1\\n  setwd(\"q\")\\nb <- 2'),"
    " list(b.R = data.frame(ref = 'data/a.csv', basename = 'a.csv', matched = TRUE,"
    " target = 'C:\\\\data\\\\a\\\\1.csv', ambiguous = FALSE, n_candidates = 1L, is_call = FALSE)),"
    " data.frame(file_name = c('x/C.R', 'c.R'), target_path = c(NA, 'code/c.R')),"
    " inject_libs = list(d.R = 'stats', a.R = ''))",
    "lambda m: m.write_scripts({'a.R': ['setwd(\"C:/x\")', 'NA', '  setwd(dir)', 'x <- 1; setwd(\"y\")'],"
    ' \'b.R\': \'d <- read.csv("data/a.csv")\\nsetwd("/z")\\nwrite.csv(d, "o.csv")\\n\\n\','
    " 'sub/c.R': ['plot(1, family=\"Times\")', 'theme(base_family = \\'Arial\\', family=\"\")'],"
    " 'd.R': 'a <- 1\\n  setwd(\"q\")\\nb <- 2'},"
    " {'b.R': m.pd.DataFrame({'ref': ['data/a.csv'], 'basename': ['a.csv'], 'matched': [True],"
    " 'target': ['C:\\\\data\\\\a\\\\1.csv'], 'ambiguous': [False], 'n_candidates': [1], 'is_call': [False]})},"
    " m.pd.DataFrame({'file_name': ['x/C.R', 'c.R'], 'target_path': [None, 'code/c.R']}),"
    " inject_libs={'d.R': 'stats', 'a.R': ''})",
)

# ---------------------------------------------------------------------------
# repro_run_scripts(): order duplicates, a single skip name, named-logical parses
# ---------------------------------------------------------------------------

expr(
    "repro_run_scripts.pre_run_semantics",
    "rc_run_scripts(c('ok.R', 'errors.R'), order = c('errors.R', 'errors.R', 'nope.R'),"
    " skip = 'errors.R', parses = c(ok.R = NA))",
    "lambda m: m.run_scripts(['ok.R', 'errors.R'], order=['errors.R', 'errors.R', 'nope.R'],"
    " skip='errors.R', parses={'ok.R': None})",
)
expr(
    "repro_run_scripts.parses_named_logical",
    "rc_run_scripts(c('ok.R', 'errors.R'), order = NULL,"
    " parses = c(ok.R = TRUE, errors.R = FALSE), failed_deps = 'x', timeout = 60)",
    "lambda m: m.run_scripts(['ok.R', 'errors.R'], order=None,"
    " parses=m.pd.Series({'ok.R': True, 'errors.R': False}), failed_deps='x', timeout=60)",
)

# relative paths, as callr resolves them: the child starts in the caller's
# working directory, setwd()s to run_dir and only then opens script_path
REL = "upstream/metacheck/tests/testthat/fixtures/repro"
expr(
    "repro_run_scripts.relative_paths",
    f"(function() {{ out <- repro_run_scripts(data.frame(file_name = c('a.R', 'b.R'),"
    f" script_path = c('ok.R', '{REL}/ok.R'), run_dir = '{REL}'), order = NULL, timeout = 60);"
    " out$elapsed <- NULL; out })()",
    f"lambda m: (m.need_r(), m.core.repro_run_scripts(m.pd.DataFrame({{'file_name': ['a.R', 'b.R'],"
    f" 'script_path': ['ok.R', '{REL}/ok.R'], 'run_dir': ['{REL}', '{REL}']}}), order=None,"
    " timeout=60).drop(columns=['elapsed']))[1]",
)

# ---------------------------------------------------------------------------
# docker helpers
# ---------------------------------------------------------------------------

expr(
    "repro_docker_resource_args.partial",
    "list(withr::with_options(list(metacheck.docker_resource_limits = list(memory_gb = 8)),"
    " metacheck:::.repro_docker_resource_args()),"
    " withr::with_options(list(metacheck.docker_resource_limits = list(cpus = 1.5)),"
    " metacheck:::.repro_docker_resource_args()))",
    "lambda m: [m.with_options({'metacheck.docker_resource_limits': {'memory_gb': 8}},"
    " m.docker._repro_docker_resource_args),"
    " m.with_options({'metacheck.docker_resource_limits': {'cpus': 1.5}},"
    " m.docker._repro_docker_resource_args)]",
)
expr(
    "repro_docker_image_for.variety",
    "list(metacheck:::.repro_docker_image_for(c(NA, ' 4.3.1 ', '4.2'), TRUE),"
    " metacheck:::.repro_docker_image_for(c('R 4.1', '4', '4.1.2.3'), TRUE),"
    " metacheck:::.repro_docker_image_for(character(0), NA),"
    " metacheck:::.repro_docker_image_for('4.3.1', 'yes'))",
    "lambda m: [m.docker._repro_docker_image_for([None, ' 4.3.1 ', '4.2'], True),"
    " m.docker._repro_docker_image_for(['R 4.1', '4', '4.1.2.3'], True),"
    " m.docker._repro_docker_image_for([], None),"
    " m.docker._repro_docker_image_for('4.3.1', 'yes')]",
)

# ---------------------------------------------------------------------------
# module tables: metacheck-written .rds files (tests/repro_core/fixtures/tables_review)
# ---------------------------------------------------------------------------

for module, element in (
    ("mymod", "table"),
    ("other", "table"),
    ("mymod", "summary_table"),
    ("mymod", "report"),
    ("nope", "table"),
):
    case(
        f"collect_module_tables.review.{module}.{element}",
        "metacheck:::collect_module_tables",
        f"{TABLES}.collect_module_tables",
        {"results_dir": {"$file": TR}, "module": module, "element": element},
    )
case(
    "collect_module_tables.review.types",
    "metacheck:::collect_module_tables",
    f"{TABLES}.collect_module_tables",
    {"results_dir": {"$file": "tests/repro_core/fixtures/tables_types"}, "module": "m"},
)
expr(
    "load_module_tables.review.a",
    f"(function(x) list(x, names(x$prev_outputs), x$prev_outputs$mymod$table,"
    f" x$prev_outputs$mymod$report, x$prev_outputs$other$title, x$summary_table))"
    f"(metacheck:::.load_module_tables('{TR}', 'a', NULL))",
    f"lambda m: (lambda x: [x, list(x.prev_outputs), x.prev_outputs['mymod'].table,"
    f" x.prev_outputs['mymod'].report, x.prev_outputs['other'].title, x.summary_table])"
    f"(m.tables._load_module_tables('{TR}', 'a', None))",
)
expr(
    "load_module_tables.review.B",
    f"(function(x) list(x, names(x$prev_outputs), x$summary_table))"
    f"(metacheck:::.load_module_tables('{TR}', 'B', NULL))",
    f"lambda m: (lambda x: [x, list(x.prev_outputs), x.summary_table])"
    f"(m.tables._load_module_tables('{TR}', 'B', None))",
)
for pid in ("empty", "corrupt", "d", "c", "nope"):
    case(
        f"load_module_tables.review.{pid}",
        "metacheck:::.load_module_tables",
        f"{TABLES}._load_module_tables",
        {"results_dir": {"$file": TR}, "paper_id": pid, "paper": {"$null": True}},
    )
# a dot-file is not listed by collect_module_tables() but loads by its id
expr(
    "load_module_tables.review.hidden",
    f"(function(x) list(names(x$prev_outputs), x$prev_outputs$mymod$table))"
    f"(metacheck:::.load_module_tables('{TR}', '.hidden', NULL))",
    f"lambda m: (lambda x: [list(x.prev_outputs), x.prev_outputs['mymod'].table])"
    f"(m.tables._load_module_tables('{TR}', '.hidden', None))",
)

# capture -> load -> collect of a chain of plain lists (no module-output class):
# a duplicated module name, a missing one, plumbing and archive-excluded elements
CHAIN_R = (
    "list(list(module = 'm1', table = data.frame(a = 1:2),"
    " summary_table = data.frame(paper_id = 'P1', k = 1L)),"
    " list(module = 'm1', table = data.frame(b = 'x'),"
    " summary_table = data.frame(paper_id = 'P1', k = 1L, j = 2)),"
    " list(table = data.frame(c = TRUE), title = 'T', previews = list(1), extra = 'kept'))"
)
CHAIN_PY = (
    "[{'module': 'm1', 'table': m.pd.DataFrame({'a': m.pd.Series([1, 2], dtype='Int64')}),"
    " 'summary_table': m.pd.DataFrame({'paper_id': ['P1'], 'k': m.pd.Series([1], dtype='Int64')})},"
    " {'module': 'm1', 'table': m.pd.DataFrame({'b': ['x']}),"
    " 'summary_table': m.pd.DataFrame({'paper_id': ['P1'], 'k': m.pd.Series([1], dtype='Int64'),"
    " 'j': [2.0]})},"
    " {'table': m.pd.DataFrame({'c': [True]}), 'title': 'T', 'previews': [1], 'extra': 'kept'}]"
)
expr(
    "capture_module_tables.plain_lists",
    "(function() { d <- tempfile(); p <- metacheck:::capture_module_tables(" + CHAIN_R + ", d);"
    " x <- metacheck:::.load_module_tables(d, 'P1', NULL);"
    " list(file = sub('[.]rds$', '', basename(p)), prev = names(x$prev_outputs),"
    " m1 = metacheck:::collect_module_tables(d, 'm1'),"
    " m2 = metacheck:::collect_module_tables(d, 'module_2'),"
    " m3 = metacheck:::collect_module_tables(d, 'module_3'),"
    " sum = x$summary_table, m3_title = x$prev_outputs$module_3$title,"
    " m3_extra = x$prev_outputs$module_3$extra, m3_previews = x$prev_outputs$module_3$previews) })()",
    "lambda m: m.tmp(lambda d: (lambda p: (lambda x: {'file': __import__('os').path.basename(p)[:-5],"
    " 'prev': list(x.prev_outputs), 'm1': m.tables.collect_module_tables(d, 'm1'),"
    " 'm2': m.tables.collect_module_tables(d, 'module_2'),"
    " 'm3': m.tables.collect_module_tables(d, 'module_3'), 'sum': x.summary_table,"
    " 'm3_title': x.prev_outputs['module_3'].title,"
    " 'm3_extra': x.prev_outputs['module_3'].get('extra'),"
    " 'm3_previews': x.prev_outputs['module_3'].get('previews')})"
    "(m.tables._load_module_tables(d, 'P1', None)))"
    "(m.tables.capture_module_tables(" + CHAIN_PY + ", d)))",
)


def main() -> None:
    header = (
        "# Review parity cases for the repro_core port (R/reproducibility_check.R,\n"
        "# R/reproducibility_check_docker.R, module tables of R/module.R). Generated by\n"
        "# tests/repro_core/make_review_cases.py; fixtures from make_review_fixtures.R.\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "repro_core_review", "cases": cases},
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
