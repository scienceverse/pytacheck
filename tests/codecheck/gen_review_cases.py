"""Write ``parity/cases/codecheck_review.yaml`` (the adversarial-review cases).

Run from the repository root::

    .venv/bin/python tests/codecheck/gen_review_cases.py
    .venv/bin/python -m parity generate --area codecheck_review
    .venv/bin/python -m parity check --area codecheck_review -v

Every string is quoted in the YAML (``default_style='"'``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "codecheck_review.yaml"
FIX = "tests/codecheck/fixtures"
CORE = "metacheck.codecheck.core"

cases: list[dict[str, Any]] = []


def case(cid: str, r: str, py: str, args: dict[str, Any], **extra: Any) -> None:
    cases.append({"id": cid, "r": r, "py": f"{CORE}.{py}", "args": args, **extra})


def chr_(*values: str | None) -> dict[str, Any]:
    return {"$chr": list(values)}


# ---------------------------------------------------------------------------
# code_abs_path(): the paths come from search_text()'s normalised text
# ---------------------------------------------------------------------------
ABS = {
    "ws_collapse": ['f <- "C:/My   Docs/a , b.csv"', 'g <- "/home/u/x\ty.csv"'],
    "unicode_space": ['p <- "/home/u/a\u2003\u2003b.csv"', 'q <- "C:/x\u00a0\u00a0y.txt"'],
    "paragraph_marker": ['x <- "C:/a<~p~>b.csv"', 'y <- "/home/ok/<~p~>z"', 'z <- "D:/fine.R"'],
    "several_per_line_ws": ['read("C:/a  b.csv", "/home/x/y  z.R") ; f(\'~/q  r\')'],
    "carriage_return": ['x <- "C:/a  b.csv"\r', "\r"],
    "tabs_only_line": ["\t\t", 'p <- "\\\\\\\\srv1\\\\share\\\\a  b.txt"'],
}
for name, text in ABS.items():
    case(
        f"code_abs_path.review.{name}", "code_abs_path", "code_abs_path", {"code_text": chr_(*text)}
    )

# ---------------------------------------------------------------------------
# code_line_stats(): an NA line flagged as a comment makes the count NA
# ---------------------------------------------------------------------------
STATS = {
    "sas_block_na": (["/* c", None, "*/", "x;"], "SAS"),
    "matlab_block_na": (["%{", None, "%}", "x = 1;"], "MATLAB"),
    "stata_na_outside_block": ([None, "use x.dta", "* c"], "Stata"),
    "spss_na_not_flagged": (["* comment.", None, "GET FILE='x.sav'."], "SPSS"),
    "mplus_na": (["! c", None, "x ! y"], "Mplus"),
    "stata_block_na": (["/*", None, "*/"], "Stata"),
}
for name, (text, lang) in STATS.items():
    case(
        f"code_line_stats.review.{name}",
        "code_line_stats",
        "code_line_stats",
        {"code_text": chr_(*text), "lang": lang},
    )
    case(
        f"code_remove_comments.review.{name}",
        "code_remove_comments",
        "code_remove_comments",
        {"code_text": chr_(*text), "lang": lang},
    )

# ---------------------------------------------------------------------------
# code_extract_py(text = ): jsonlite/yajl parsing and unlist() coercion
# ---------------------------------------------------------------------------


def nb(src: str) -> str:
    return '{"cells": [{"cell_type": "code", "source": ' + src + "}]}"


JSON = {
    "comments": '{"cells": [/* c */ {"cell_type": "code", "source": "x"}]} // trailing',
    "comment_first": '// head\n{"cells": [{"cell_type": "code", "source": ["a\\n", "b"]}]}',
    "unterminated_block_comment": nb('"q"') + " /* open",
    "hash_is_not_a_comment": nb('"q"') + " # hash",
    "bom": "\ufeff" + nb('"x"'),
    "bom_after_space": " \ufeff" + nb('"x"'),
    "nul_escape": nb('"a\\u0000b\\nc"'),
    "nul_in_key": '{"cells\\u0000x": [{"cell_type": "code", "source": "k"}]}',
    "lone_high_surrogate": nb('"a\\ud800b"'),
    "surrogate_pair": nb('"\\ud83d\\ude00 ok"'),
    "high_then_other_escape": nb('"\\ud800\\u0041|\\ud800\\ud800|\\ud800\\n"'),
    "int_and_logical": nb("[1, true]"),
    "double_and_bigint": nb("[1.5, 3000000000]"),
    "logicals": nb("[true, false]"),
    "doubles_formatting": nb("[0.1, 1e-20, 123456789012345678]"),
    "zero_exponent": nb("[-0.0, 1E2, 2.50, -0]"),
    "int32_bounds": nb("[2147483647, 2147483648, -2147483648, -2147483647]"),
    "string_and_numbers": nb('["x", 1, 2.5, true, null]'),
    "nested_object": nb('{"a": "x", "b": ["y\\n", "z"]}'),
    "vertical_tab_whitespace": "\v" + '{"cells": [\f{"cell_type": "code", "source": "v"}]}',
    "trailing_comma": '{"cells": [{"cell_type": "code", "source": "x"},]}',
    "duplicate_keys": '{"cells": [{"cell_type": "code", "cell_type": "raw", "source": "d"}]}',
    "partial_key_name": '{"cel": [{"cell_type": "code", "source": "p"}]}',
}
for name, text in JSON.items():
    case(
        f"code_extract_py.review.{name}",
        "code_extract_py",
        "code_extract_py",
        {"text": chr_(text)},
    )

NOTEBOOKS = ["nb_comments", "nb_bom", "nb_escapes", "nb_nul", "nb_vtab"]
for name in NOTEBOOKS:
    path = f"{FIX}/review/{name}.ipynb"
    case(f"code_lang.review.{name}", "code_lang", "code_lang", {"file_name": path})
    case(
        f"code_extract_py.review.file_{name}",
        "code_extract_py",
        "code_extract_py",
        {"file_path": path},
    )
    case(f"code_read.review.{name}", "code_read", "code_read", {"file_path": path})

# .qmd_lang(): R's yaml keeps unknown tags' text, makes a tagged sequence a list
for name in ("tag_r", "tag_expr", "tag_expr_seq", "tag_seq", "untagged_seq", "tag_map", "tag_str"):
    case(
        f"code_lang.review.qmd_{name}",
        "code_lang",
        "code_lang",
        {"file_name": f"{FIX}/review/{name}.qmd"},
    )

# ---------------------------------------------------------------------------
# code_read(): gzip-compressed UTF-16 (vroom re-encodes from a file() connection)
# ---------------------------------------------------------------------------
for name in ("gz_utf16le", "gz_utf16be"):
    path = f"{FIX}/review/{name}.R"
    case(f"code_read.review.{name}", "code_read", "code_read", {"file_path": path})
    case(
        f"code_line_stats.review.{name}",
        "code_line_stats",
        "code_line_stats",
        {
            "code_text": {
                "$call": {"r": "code_read", "py": f"{CORE}.code_read", "args": {"file_path": path}}
            },
            "lang": "R",
        },
    )

# ---------------------------------------------------------------------------
# code_lang(): tools::file_ext() anchors at the very end
# ---------------------------------------------------------------------------
case(
    "code_lang.review.trailing_newline",
    "code_lang",
    "code_lang",
    {"file_name": chr_("a.R\n", "b.py", "c.do\n", "d.R ")},
)
case(
    "code_lang.review.single_trailing_newline",
    "code_lang",
    "code_lang",
    {"file_name": "analysis.R\n"},
)

# ---------------------------------------------------------------------------
# code_extract_r(): knitr purl chunk options
# ---------------------------------------------------------------------------


def rmd(header: str, *body: str) -> list[str]:
    return ["Intro", header, *(body or ("x <- 1", "y <- 2")), "```", "End"]


PURL: dict[str, list[str | None]] = {
    "engine_symbol": rmd("```{r, engine=x}"),
    "engine_call": rmd("```{r, engine=paste0('p', 'y')}"),
    "engine_R_symbol": rmd("```{r, engine=R}"),
    "engine_NA": rmd("```{r, engine=NA}"),
    "engine_NULL": rmd("```{r, engine=NULL}"),
    "engine_number": rmd("```{r, engine=1}"),
    "comment_symbol": rmd("```{python, comment=x}"),
    "comment_call": rmd("```{python, comment=f(1)}"),
    "comment_number": rmd("```{python, comment=5}"),
    "duplicate_eval": rmd("```{r, eval=FALSE, eval=TRUE}"),
    "duplicate_eval_reversed": rmd("```{r, eval=TRUE, eval=FALSE}"),
    "duplicate_error": rmd("```{r, error=TRUE, error=FALSE}"),
    "duplicate_purl": rmd("```{r, purl=TRUE, purl=FALSE}"),
    "duplicate_label": rmd("```{r first, label='second'}"),
    "pipe_duplicate_eval": rmd("```{r}", "#| eval=FALSE, eval=TRUE", "x <- 1"),
    "header_and_pipe_eval": rmd("```{r, eval=FALSE}", "#| eval: true", "x <- 1"),
    "eval_colon": rmd("```{r, eval=2:3}"),
    "eval_colon_reversed": rmd("```{r, eval=3:1}"),
    "eval_seq_len": rmd("```{r, eval=seq_len(2)}"),
    "eval_seq_len_zero": rmd("```{r, eval=seq_len(0)}"),
    "eval_integer0": rmd("```{r, eval=integer(0)}"),
    "eval_logical0": rmd("```{r, eval=logical(0)}"),
    "eval_sys_time": rmd("```{r, eval=Sys.time() > 0}"),
    "eval_sum": rmd("```{r, eval=sum(1, 2) > 2}"),
    "eval_is_html_output": rmd("```{r, eval=knitr::is_html_output()}"),
    "eval_is_html_output_excludes": rmd("```{r, eval=knitr::is_html_output(excludes='markdown')}"),
    "eval_is_latex_output": rmd("```{r, eval=knitr::is_latex_output()}"),
    "eval_not_latex": rmd("```{r, eval=!knitr::is_latex_output()}"),
    "rnw_is_latex_output": [
        "<<eval=knitr::is_latex_output()>>=",
        "x <- 1",
        "@",
        "<<eval=knitr::is_html_output()>>=",
        "y <- 2",
        "@",
    ],
    "yaml_label_number": ["```{r}", "#| label: 3", "x <- 1", "```", "```{r}", "<<3>>", "```"],
    "yaml_label_null": ["```{r}", "#| label: null", "x <- 1", "```", "```{r}", "<<3>>", "```"],
    "yaml_label_list": ["```{r}", "#| label: [a, b]", "x <- 1", "```", "```{r}", "<<a>>", "```"],
    "yaml_label_true": ["```{r}", "#| label: TRUE", "x <- 1", "```", "```{r}", "y", "```"],
    "yaml_label_number_one": ["```{r}", "#| label: 1", "x <- 1", "```"],
    "yaml_id": ["```{r}", "#| id: myid", "x <- 1", "```", "```{r}", "<<myid>>", "```"],
    "na_first_code_line": ["```{r}", None, "```"],
    "na_later_code_line": ["```{r}", "x", None, "```"],
    "na_after_chunk": ["```{r}", "x", "```", None],
    "na_in_text": [None, "text", "```{r}", "x", "```"],
    "na_rnw_chunk": ["<<>>=", None, "@"],
    "na_indented_chunk": ["  ```{r}", None, "  ```"],
}
for name, text in PURL.items():
    for doc in (0, 1):
        case(
            f"code_extract_r.review.{name}.doc{doc}",
            "code_extract_r",
            "code_extract_r",
            {"text": chr_(*text), "documentation": doc},
        )

# ---------------------------------------------------------------------------
# .code_expand_*(): local output files (the recovered code is written to a
# code/ folder next to them) and R's handling of missing columns
# ---------------------------------------------------------------------------
EXP = f"{FIX}/expand"


def files(names: list[str], **cols: Any) -> dict[str, Any]:
    df: dict[str, Any] = {
        "repo_url": ["https://osf.io/abcde"] * len(names),
        "file_name": names,
        "file_path": [f"sub/{n}" for n in names],
        "file_url": [None] * len(names),
        "file_location": [f"{EXP}/{n}" for n in names],
        "file_size": [1] * len(names),
    }
    for k, v in cols.items():
        if v is None:
            df.pop(k)
        else:
            df[k] = v
    return {"$df": df}


def expand(cid: str, fn: str, all_files: dict[str, Any]) -> None:
    case(
        cid,
        f"metacheck:::.{fn}",
        f"_{fn}",
        {
            "all_files": all_files,
            "max_file_size": 100,
            "max_download_size": 500,
            "cache": False,
        },
    )


expand(
    "code_expand_smcl.review.local",
    "code_expand_smcl",
    files(["analysis.smcl", "nocommands.smcl", "x.R"]),
)
expand(
    "code_expand_smcl.review.no_file_path",
    "code_expand_smcl",
    files(["analysis.smcl"], file_path=None),
)
expand(
    "code_expand_smcl.review.na_file_path",
    "code_expand_smcl",
    files(["analysis.smcl", "twolevel.out"], file_path=[None, "x/twolevel.out"]),
)
expand(
    "code_expand_smcl.review.no_file_location",
    "code_expand_smcl",
    files(["analysis.smcl"], file_location=None),
)
expand(
    "code_expand_mplus.review.local",
    "code_expand_mplus",
    files(["twolevel.out", "compiler.out", "nosections.out"]),
)
expand(
    "code_expand_spv.review.local",
    "code_expand_spv",
    files(["modern.spv", "notzip.spv", "legacy.spv", "logs_only.spv", "a.sps"]),
)
expand(
    "code_expand_mplus.review.no_file_location",
    "code_expand_mplus",
    files(["twolevel.out"], file_location=None),
)
expand(
    "code_expand_html.review.local_data_type",
    "code_expand_html",
    files(
        ["knit_classic.html", "knit_quarto.html", "stata_log.html", "plain.html", "empty.html"],
        data_type=["other"] * 5,
    ),
)
expand(
    "code_expand_html.review.local",
    "code_expand_html",
    files(["knit_classic.html", "stata_log.html"]),
)
expand(
    "code_expand_html.review.no_file_location",
    "code_expand_html",
    files(["knit_classic.html"], file_location=None),
)
for missing in ("language", "file_location", "file_url"):
    df = files(
        ["a.smcl", "b.R"],
        file_url=["https://example.invalid/a.smcl", "https://example.invalid/b.R"],
        file_location=[None, None],
        language=[None, "R"],
    )
    df["$df"].pop(missing)
    expand(f"code_predownload.review.no_{missing}", "code_predownload", df)

# ---------------------------------------------------------------------------
# .code_version_pin_check(): renv.lock files only jsonlite can read
# ---------------------------------------------------------------------------
PIN = f"{FIX}/review/pin"
pin_names = ["renv.lock", "sub/renv.lock", "SESSION-INFO.txt"]
case(
    "code_version_pin_check.review.jsonlite_quirks",
    "metacheck:::.code_version_pin_check",
    "_code_version_pin_check",
    {
        "all_files": {
            "$df": {
                "repo_url": ["r"] * 3,
                "file_name": pin_names,
                "file_url": [None] * 3,
                "file_location": [f"{PIN}/{n}" for n in pin_names],
            }
        },
        "code_text_list": {
            "$list": [
                chr_("library(groundhog)", "groundhog.library(c('a', 'b'), \"2021-01-01\")"),
                chr_(),
                chr_("checkpoint::checkpoint('2020-02-02')"),
            ]
        },
    },
)
case(
    "code_version_pin_check.review.no_file_location",
    "metacheck:::.code_version_pin_check",
    "_code_version_pin_check",
    {"all_files": {"$df": {"repo_url": ["r"], "file_name": ["renv.lock"], "file_url": [None]}}},
)

# ---------------------------------------------------------------------------
# code_library_names(): a package-list variable is not a package (issue #421)
# ---------------------------------------------------------------------------
LIBS: dict[str, list[str | None]] = {
    # the issue's example: a loop variable over a literal list spread over lines
    "loop_var_issue": [
        'list.packages <- c("activity", "bbmle",',
        '                   "Distance")',
        "for (req.lib in list.packages) {",
        "  if (!require(req.lib, character.only = TRUE)) {",
        "    install.packages(req.lib)",
        "    library(req.lib, character.only = TRUE)",
        "  }",
        "}",
    ],
    # the common new.packages idiom: indexing resolves, a derived variable does not
    "new_packages_idiom": [
        'list.of.packages <- c("ggplot2", "Rcpp")',
        'new.packages <- list.of.packages[!(list.of.packages %in% installed.packages()[,"Package"])]',
        "if (length(new.packages)) install.packages(new.packages)",
        "install.packages(list.of.packages[!list.of.packages %in% rownames(installed.packages())])",
        "lapply(list.of.packages, require, character.only = TRUE)",
    ],
    "not_purely_literal": ['pk <- c("zoo", other)', "install.packages(pk)"],
    "sapply_fun": [
        'pkgs = c("psych", "car")',
        "sapply(pkgs, FUN = library, character.only = TRUE)",
    ],
    "pload_char": ['p <- c("lme4", "car")', "pacman::p_load(char = p)"],
    "pload_character_only": ['p <- c("lme4", "car")', "pacman::p_load(p, character.only = TRUE)"],
    "pload_mixed": ['p <- c("lme4", "car")', 'pacman::p_load(dplyr, "tidyr", tibble)'],
    "pload_named_arg": ["pacman::p_load(dplyr, install = FALSE)"],
    "literal_names": [
        'library(dplyr); library("tidyr")',
        'install.packages("afex", dependencies = TRUE)',
        'BiocManager::install("edgeR")',
    ],
    "assign_forms": [
        'a <<- c("one", "two")',
        'b = "three"',
        "install.packages(a); install.packages(b)",
    ],
    "later_assignment_wins": [
        'p <- c("first")',
        "install.packages(p)",
        'p <- c("second", "third")',
        "install.packages(p)",
    ],
    "loop_over_unknown": [
        "for (p in pkgs) library(p, character.only = TRUE)",
        'for (p in c("a", "b")) library(p, character.only = TRUE)',
    ],
    "single_quotes_and_T": [
        "pk <- c('x1', 'y2')",
        "library(pk, character.only = T)",
        "require(pk, character.only = TRUE)",
    ],
    "require_namespace_var": [
        'pkg <- "stringr"',
        "requireNamespace(pkg, quietly = TRUE)",
        'requireNamespace("glue", quietly = TRUE)',
    ],
    "bare_library_vs_char_only": [
        'x <- "tidyr"',
        "library(x)",
        "library(x, character.only = TRUE)",
    ],
    "install_c_with_variable": [
        'extra <- c("p1", "p2")',
        'install.packages(c("a", "b"))',
        'install.packages(c(extra, "z"))',
        "renv::install(extra)",
    ],
    "walk_map_purrr": [
        'pk <- c("m1", "m2")',
        "purrr::walk(pk, library, character.only = TRUE)",
        "purrr::map(pk, require, character.only = TRUE)",
        "vapply(pk, FUN = require, logical(1), character.only = TRUE)",
    ],
    "indexed_variable": [
        'pk <- c("i1", "i2")',
        "library(pk[1], character.only = TRUE)",
        "install.packages(pk[2])",
    ],
    "unclosed_and_odd": [
        'library("dplyr',
        "install.packages(1 + 2)",
        "install.packages(.hidden)",
        'pkgs <- c("a", \'b")',
        "install.packages(pkgs)",
    ],
    "comment_removed_assignment": [
        '# pk <- c("hidden")',
        "install.packages(pk)",
        'pk <- c("shown") # trailing',
        "install.packages(pk)",
    ],
}
for name, text in LIBS.items():
    case(
        f"code_library_names.review.{name}",
        "code_library_names",
        "code_library_names",
        {"code_text": chr_(*text), "lang": "R"},
    )
    case(
        f"code_char_vector_vars.review.{name}",
        "metacheck:::.code_char_vector_vars",
        "_code_char_vector_vars",
        {"code_text": chr_(*text)},
    )
# a Python file is not affected by the R variable resolution
case(
    "code_library_names.review.python_unchanged",
    "code_library_names",
    "code_library_names",
    {
        "code_text": chr_("import numpy as np", "pkgs = ['a']", "from scipy import stats"),
        "lang": "Python",
    },
)

# ---------------------------------------------------------------------------
# empty files (issue #425): no code is handled like "" by every code_*() function
# ---------------------------------------------------------------------------
for fn in ("code_setwd", "code_install_packages"):
    case(f"{fn}.review.character0", fn, fn, {"code_text": chr_()})
    case(f"{fn}.review.empty_string", fn, fn, {"code_text": ""})
case(
    "code_line_stats.review.character0_python",
    "code_line_stats",
    "code_line_stats",
    {"code_text": chr_(), "lang": "Python"},
)
case(
    "code_remove_comments.review.character0_python",
    "code_remove_comments",
    "code_remove_comments",
    {"code_text": chr_(), "lang": "Python"},
)
case(
    "code_has_docstring.review.character0",
    "metacheck:::.code_has_docstring",
    "_code_has_docstring",
    {"code_text": chr_()},
)
case(
    "code_has_docstring.review.empty_string",
    "metacheck:::.code_has_docstring",
    "_code_has_docstring",
    {"code_text": ""},
)
case(
    "code_has_docstring.review.blocks",
    "metacheck:::.code_has_docstring",
    "_code_has_docstring",
    {"code_text": chr_('"""doc', 'string"""', "x = 1")},
)
case(
    "code_has_docstring.review.unclosed",
    "metacheck:::.code_has_docstring",
    "_code_has_docstring",
    {"code_text": chr_("'''open", "x = 1")},
)
case("code_parse_r.review.empty_string", "code_parse_r", "code_parse_r", {"text": ""})
case(
    "code_parse_r.review.blank_lines",
    "code_parse_r",
    "code_parse_r",
    {"text": chr_("", "  ")},
)
case("code_abs_path.review.character0", "code_abs_path", "code_abs_path", {"code_text": chr_()})

OUT.write_text(
    "# Adversarial-review parity cases for R/code_check.R (generated by\n"
    "# tests/codecheck/gen_review_cases.py; do not edit by hand).\n"
    + yaml.dump(
        {"area": "codecheck_review", "cases": cases},
        default_style='"',
        allow_unicode=True,
        sort_keys=False,
        width=100,
    ),
    encoding="utf-8",
)
print(f"{len(cases)} cases -> {OUT.relative_to(ROOT)}")
