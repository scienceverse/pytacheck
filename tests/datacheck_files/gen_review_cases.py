"""Generate parity/cases/datacheck_files_review.yaml (run from the repository root).

Cases added by the adversarial review of the datacheck_files port. Each group
pins an R behaviour the first port got wrong; the fixtures are written by
``data/make_review_fixtures.py`` and ``data/make_review_fixtures.R``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REVIEW = "tests/datacheck_files/data/review"
PY = "pytacheck.datacheck.files"
HELP = "tests.datacheck_files.review_helpers"

cases: list[dict[str, Any]] = []


def add(case_id: str, **spec: Any) -> None:
    cases.append({"id": case_id, **spec})


def f(name: str) -> dict[str, str]:
    return {"$file": f"{REVIEW}/{name}"}


def inf() -> dict[str, Any]:
    return {"$expr": {"r": "Inf", "py": "float('inf')"}}


# column types first; then complex -> character and integer64 -> double, which
# the canonical encoder would otherwise garble
_R_TYPED = (
    "local({{d <- {call}; if (is.null(d)) return(NULL); "
    "types <- unname(lapply(d, function(x) c(typeof(x), class(x)))); "
    "for (j in seq_along(d)) {{if (is.complex(d[[j]])) d[[j]] <- as.character(d[[j]]); "
    'if (inherits(d[[j]], "integer64")) '
    "d[[j]] <- as.numeric(bit64:::as.character.integer64(d[[j]]))}}; "
    "list(names = names(d), types = types, data = d)}})"
)


def typed_head(name: str, n_rows: float | None = None) -> None:
    n_r = "Inf" if n_rows is None else repr(n_rows)
    call = f'data_read_head(rpath("{REVIEW}/{name}"), n_rows = {n_r})'
    suffix = "" if n_rows is None else f".n{n_rows:g}"
    add(
        f"review.read_head_typed.{name}{suffix}",
        r="identity",
        py=f"{HELP}.read_head_typed",
        args={"x": {"$expr": {"r": _R_TYPED.format(call=call), "py": "None"}}},
        py_drop=["x"],
        py_args={"path": f"{REVIEW}/{name}", "n_rows": n_rows},
    )


def typed_delim(name: str, sep: str, header: bool, nrows: float | None = None,
                encoding: str | None = None) -> None:  # fmt: skip
    extra = "" if nrows is None else f", nrows = {nrows:g}"
    if encoding:
        extra += f', fileEncoding = "{encoding}"'
    sep_r = sep.replace("\t", "\\t")
    call = (
        f'suppressWarnings(utils::read.delim(rpath("{REVIEW}/{name}"), sep = "{sep_r}", '
        f"header = {'TRUE' if header else 'FALSE'}{extra}, check.names = FALSE))"
    )
    tag = f"{name}.{'hdr' if header else 'nohdr'}" + ("" if nrows is None else f".n{nrows:g}")
    if encoding:
        tag += f".{encoding}"
    add(
        f"review.read_delim.{tag}",
        r="identity",
        py=f"{HELP}.read_delim_typed",
        args={"x": {"$expr": {"r": _R_TYPED.format(call=call), "py": "None"}}},
        py_drop=["x"],
        py_args={"path": f"{REVIEW}/{name}", "sep": sep, "header": header, "nrows": nrows,
                 "encoding": encoding},
    )  # fmt: skip


# -- text_peek(): NUL handling of rawToChar() / iconv() --------------------------
for name in ("tp_trailing_nul.csv", "tp_trailing_nul1.csv", "tp_dbl_nul.txt", "tp_all_nul.txt",
             "tp_mid_nul.txt"):  # fmt: skip
    add(f"review.text_peek.{name}", r="text_peek", py=f"{PY}.text_peek", args={"path": f(name)})
for name in ("tp_trailing_nul.csv", "tp_dbl_nul.txt"):
    add(f"review.txt_classify_content.{name}", r="txt_classify_content",
        py=f"{PY}.txt_classify_content", args={"path": f(name)})  # fmt: skip

# -- readLines() drops a UTF-8 BOM from the first line of each call ------------
for name in ("bom_numeric.csv", "bom_comment_cr.txt"):
    add(f"review.sniff_delimiter.{name}", r=".sniff_delimiter", py=f"{PY}._sniff_delimiter",
        args={"path": f(name)})  # fmt: skip
add("review.detect_header.bom_numeric.csv", r=".detect_header", py=f"{PY}._detect_header",
    args={"path": f("bom_numeric.csv"), "sep": ","})  # fmt: skip
add("review.is_single_field_blob.bom_nohdr_blob.csv", r=".is_single_field_blob",
    py=f"{PY}._is_single_field_blob", args={"path": f("bom_nohdr_blob.csv"), "sep": ","})  # fmt: skip

# -- data_read_head(): values and R column types ------------------------------
HEAD_FILES = [
    "bom_numeric.csv", "bom_comment_cr.txt", "bom_nohdr_blob.csv", "tab_in_quotes.tsv",
    "fread_nul_name.csv", "fread_nul_value.csv", "fread_cr_ws_first.tsv", "int64.csv",
    "rt_quotes.csv", "rt_int.csv", "rt_int_trailing_ws.csv", "rt_lgl.csv", "rt_lgl_lower.csv",
    "rt_dbl.csv", "rt_nan_upper.csv", "rt_cplx.csv", "rt_blank_na.csv", "rt_na_latin1.csv",
    "rt_mb_error.csv", "rt_dup_rownames.txt", "rt_rownames.txt", "rt_wrap.csv",
    "rt_nul_quote_header.csv", "rt_fallback_latin1.csv", "rt_nul_value.csv", "rt_cr_cr.csv", "rt_nrows.csv",
    "stata_int_na.dta", "zero_rows.sav", "zero_rows.dta", "all_na_dates.sav", "list_cells.rds",
    "dup_names.csv", "tagged_na.dta",
]  # fmt: skip
for name in HEAD_FILES:
    typed_head(name)
# U154: a Japanese era date format is a date (readxl reads the serial number)
typed_head("era_date.xlsx")
for name in ("tab_in_quotes.tsv", "rt_nrows.csv", "rt_wrap.csv", "stata_int_na.dta",
             "int64.csv", "bom_comment_cr.txt"):  # fmt: skip
    typed_head(name, 5)
for name in ("stata_int_na.dta", "all_na_dates.sav", "zero_rows.sav", "int64.csv",
             # repeated value-label texts (F04) and duplicated column names (F32)
             "dup_labels.sav", "dup_labels.dta", "dup_labels.rds", "dup_names.rds",
             # Stata extended missing values as label codes, numeric codes on a string
             "tagged_na.dta"):  # fmt: skip
    add(
        f"review.read_head_attrs.{name}",
        r="identity",
        py="tests.datacheck_files.parity_helpers.read_head_attrs",
        args={"x": {"$expr": {"r": (
            f'local({{d <- data_read_head(rpath("{REVIEW}/{name}"), n_rows = Inf); '
            "list(names = names(d), attrs = lapply(d, function(x) {a <- attributes(x); "
            "a$levels <- NULL; a$names <- NULL; if (!is.null(a$labels)) a$labels <- as.list(a$labels); "
            "if (length(a)) a[order(names(a))] else NULL}), utf8_repaired = NULL)})"
        ), "py": "None"}}},
        py_drop=["x"],
        py_args={"path": f"{REVIEW}/{name}", "n_rows": None},
    )  # fmt: skip

# -- utils::read.delim() (the fallback reader) on its own ---------------------
for name in ("rt_quotes.csv", "rt_int.csv", "rt_int_trailing_ws.csv", "rt_lgl.csv",
             "rt_lgl_lower.csv", "rt_dbl.csv", "rt_nan_upper.csv", "rt_cplx.csv",
             "rt_blank_na.csv", "rt_na_latin1.csv", "rt_mb_error.csv", "rt_dup_rownames.txt",
             "rt_rownames.txt", "rt_wrap.csv", "rt_nul_quote_header.csv", "rt_nul_value.csv",
             "rt_cr_cr.csv", "fread_nul_name.csv", "bom_numeric.csv", "fread_cr_ws_first.tsv",
             "tab_in_quotes.tsv"):  # fmt: skip
    sep = "\t" if name.endswith(".tsv") else ","
    typed_delim(name, sep, True)
for name in ("rt_wrap.csv", "bom_numeric.csv", "rt_blank_na.csv", "fread_cr_ws_first.tsv"):
    typed_delim(name, "\t" if name.endswith(".tsv") else ",", False)
typed_delim("rt_nrows.csv", ",", True, nrows=5)
typed_delim("rt_wrap.csv", ",", True, nrows=1)
typed_delim("rt_wrap.csv", ",", False, nrows=0)
typed_delim("rt_na_latin1.csv", ",", True, encoding="latin1")
typed_delim("rt_mb_error.csv", ",", True, encoding="latin1")

# -- .detect_likert_scale(): as.integer() turns |x| > .Machine$integer.max into NA
for tag, r_x, py_x in [
    ("huge", "c(rep(1:5, 5), 3e9)", "[1, 2, 3, 4, 5] * 5 + [3e9]"),
    ("huge_neg", "c(rep(0:4, 6), -3e9, 99)", "[0, 1, 2, 3, 4] * 6 + [-3e9, 99]"),
    ("huge_only_suspect", "c(rep(1:7, 4), 1e10, 1e10)", "[1, 2, 3, 4, 5, 6, 7] * 4 + [1e10, 1e10]"),
]:
    add(f"review.detect_likert_scale.{tag}", r=".detect_likert_scale",
        py=f"{PY}._detect_likert_scale", args={"x": {"$expr": {"r": r_x, "py": py_x}}})  # fmt: skip

# -- tolower() is towlower() per character (U+0130 -> "i", no final sigma) ------
_UNICODE_PATHS = [
    "study_raw_Experiment 1\u0130\u0130", "smashed\u0130Pilots1", "\u0130pilot",
    "experiment\u0130ex1pilot", "EXP2\u03a3/x", "Pilot/\u0130/y", "Study 4\u03a3\u03a3.csv",
]  # fmt: skip
add("review.data_group_from_path.unicode", r=".data_group_from_path",
    py=f"{PY}._data_group_from_path", args={"paths": {"$chr": _UNICODE_PATHS}})  # fmt: skip
add("review.data_group_normalize.unicode", r=".data_group_normalize",
    py=f"{PY}._data_group_normalize",
    args={"x": {"$chr": ["Experiment 1\u0130", "PILOT2\u0130", "STUDY 3\u03a3", "ex\u0130"]}})  # fmt: skip
add("review.data_classify_files_ext.unicode", r="data_format", py=f"{PY}.data_format",
    args={"ext": {"$chr": ["CSV", "\u0130", "SAV", "Rdata"]}})  # fmt: skip

# -- .data_check_write_manifest() without a repo_url column; directory paths ----
add(
    "review.write_manifest.no_repo_url",
    r="identity",
    py=f"{HELP}.manifest_scenario",
    args={
        "x": {
            "$expr": {
                "r": (
                    'local({stable <- function(m) {m$generated <- "<volatile>"; '
                    'm$provenance <- "<volatile>"; m}; '
                    'p <- tempfile(fileext = ".json"); files <- data.frame(file_name = c("a.csv", '
                    '"b.pdf"), file_url = c(NA, "https://osf.io/b/"), data_type = c("data", '
                    '"documentation"), stringsAsFactors = FALSE); metacheck:::.data_check_write_manifest(p, '
                    'files, want = c(TRUE, FALSE), gated = NULL, paper_id = "p1", download = "data", '
                    "max_file_size = 100, max_download_size = 500); "
                    "stable(jsonlite::fromJSON(p, simplifyVector = FALSE))})"
                ),
                "py": "None",
            }
        }
    },
    py_drop=["x"],
    py_args={"scenario": "no_repo_url"},
    compare={"ignore": ["generated", "provenance"]},
)
add(
    "review.write_manifest.no_repo_url_gated",
    r="identity",
    py=f"{HELP}.manifest_scenario",
    args={
        "x": {
            "$expr": {
                "r": (
                    'local({p <- tempfile(fileext = ".json"); files <- data.frame(file_name = "b.csv", '
                    'file_url = "https://osf.io/b/", stringsAsFactors = FALSE); '
                    "metacheck:::.data_check_write_manifest(p, files, want = TRUE, gated = NULL, "
                    'paper_id = "p1", download = "data", max_file_size = 100, max_download_size = 500)})'
                ),
                "py": "None",
            }
        }
    },
    py_drop=["x"],
    py_args={"scenario": "no_repo_url_gated"},
)
add(
    "review.write_manifest.dir_slash",
    r="identity",
    py=f"{HELP}.manifest_scenario",
    args={
        "x": {
            "$expr": {
                "r": (
                    'local({d <- tempfile(); files <- data.frame(repo_url = "u", file_name = "a.csv", '
                    'stringsAsFactors = FALSE); p <- metacheck:::.data_check_write_manifest(paste0(d, "/out/"), '
                    'files, want = FALSE, gated = NULL, paper_id = "P 1", download = "data", '
                    'max_file_size = 100, max_download_size = 500); sub(d, "<tmp>", p, fixed = TRUE)})'
                ),
                "py": "None",
            }
        }
    },
    py_drop=["x"],
    py_args={"scenario": "dir_slash"},
)


# .data_group_seed is 8675309L: the llm() cache key must see an R integer
add(
    "review.data_group_llm.seed_cache_key",
    r="identity",
    py=f"{HELP}.data_group_cache_key",
    args={
        "x": {
            "$expr": {
                "r": (
                    "local({key <- NULL; testthat::with_mocked_bindings(metacheck::data_group_llm("
                    'data.frame(file_name = c("first_raw.csv", "second_raw.csv"), data_type = "data"), '
                    'model = "m"), llm = function(..., params) {key <<- metacheck:::.llm_cache_key('
                    '"t", "s", NULL, "m", do.call(ellmer::params, c(params, list(temperature = 0, '
                    'max_tokens = 4096)))); stop("no llm")}, .package = "metacheck"); key})'
                ),
                "py": "None",
            }
        }
    },
    py_drop=["x"],
)


# file names that are not valid UTF-8 (UPSTREAM_ISSUES U76): tools::file_ext()'s
# substring() and tolower() raise in R; names are written with R's \xff escapes
def _r_chr(xs: list[str | None]) -> str:
    return "c(" + ", ".join("NA" if x is None else f'"{x}"' for x in xs) + ")"


for tag, names, paths, fn in [
    ("ext_substring", ["a.csv", "b\\xff.txt"], None, "classify"),
    ("tolower_only", ["b\\xff.txt"], None, "classify"),
    ("tolower_second", ["a", "b\\xff"], None, "classify"),
    ("na_first", [None, "b\\xff.txt"], None, "classify"),
    ("invalid_first", ["b\\xff.txt", "a.csv"], None, "classify"),
    ("budget_msg", ["a.csv", "abc\\xffdef.txt"], None, "classify"),
    ("budget_multibyte", ["a.csv", "\\xffabcd\\xc3\\xa9xyz.txt"], None, "classify"),
    ("surrogate_seq", ["a.csv", "abc\\xed\\xa0\\x80.txt"], None, "classify"),
    ("path_invalid", ["a.csv", "b.csv"], ["x/a.csv", "y\\xff/b.csv"], "classify"),
    ("glibc_valid", ["a.csv", "abc\\xf4\\x90\\x80\\x80.txt", "data/x\\xf4\\x90\\x80\\x80.csv"],
     None, "classify"),
    ("doc_role", ["a.csv", "b\\xff.txt"], None, "doc_role"),
]:  # fmt: skip
    call = (
        f"metacheck:::.data_doc_role({_r_chr(names)})"
        if fn == "doc_role"
        else f"data_classify_files({_r_chr(names)}"
        + ("" if paths is None else f", file_path = {_r_chr(paths)}")
        + ")"
    )
    add(
        f"review.data_classify_files.invalid_utf8.{tag}",
        r="identity",
        py=f"{HELP}.classify",
        args={"x": {"$expr": {"r": f"suppressWarnings({call})", "py": "None"}}},
        py_drop=["x"],
        py_args={"file_name": names, "file_path": paths, "fn": fn},
    )
add(
    "review.data_read_head.invalid_utf8_name",
    r="identity",
    py=f"{HELP}.read_head_invalid_name",
    args={
        "x": {
            "$expr": {
                "r": (
                    'local({d <- tempfile(); dir.create(d); p <- file.path(d, "b\\xff.csv"); '
                    'writeBin(charToRaw("a,b\\n1,2\\n"), p); suppressWarnings(data_read_head(p))})'
                ),
                "py": "None",
            }
        }
    },
    py_drop=["x"],
)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style='"'))


def main() -> None:
    text = yaml.dump(
        {"area": "datacheck_files_review", "cases": cases},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    header = (
        "# Review cases for R/data_check_helpers.R part 1 (files). Generated by\n"
        "# tests/datacheck_files/gen_review_cases.py -- edit that script, not this file.\n"
    )
    (ROOT / "parity" / "cases" / "datacheck_files_review.yaml").write_text(
        header + text, encoding="utf-8"
    )
    print(f"{len(cases)} cases")


if __name__ == "__main__":
    main()
