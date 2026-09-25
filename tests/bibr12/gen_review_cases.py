"""Write ``parity/cases/bibr12_review.yaml``: adversarial bibr 12.x parity cases.

Run ``.venv/bin/python tests/bibr12/gen_review_cases.py`` and then
``python -m parity generate --area bibr12_review``.

The cases read the adversarial files in ``tests/bibr12/fixtures`` (numbers in
string columns, strings in integer columns, scalars for arrays, nulls in
person arrays, every degrees-of-freedom shape, unknown keys, missing tables,
jsonlite's number and string formatting), write 12.x papers that a user has
changed, and check error messages (the plain error cases of bibr12.yaml only
check that Python raises).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "bibr12_review.yaml"

FX = "tests/bibr12/fixtures"
F12 = "upstream/metacheck/tests/testthat/fixtures/bibr12"
EDGES = ("edge_types", "edge_minimal", "edge_numeric_id")
# files whose extraction has no completed_at: the writer uses the time of writing
NO_TIME = {"edge_minimal"}

H = "__import__('tests.bibr12._review_helpers', fromlist=['x'])"

# R: a temporary directory, as the Python helpers use
R_TMP = "local({d <- tempfile('pc_bibr12_'); dir.create(d); d})"


def r_written(paper: str, blank_time: bool = False) -> str:
    blank = (
        " x[grepl('^    \"completed_at\": ', x)] <- '<completed_at>';" if blank_time else ""
    )
    return (
        f"local({{f <- paper_write({paper}, NULL, save_path = {R_TMP}, schema_version = '12.0'); "
        "x <- readLines(f, encoding = 'UTF-8'); i <- which(trimws(x) == '\"converter\": {'); "
        f"x[i + 1] <- '<converter name>'; x[i + 2] <- '<converter version>';{blank} x}})"
    )


def r_roundtrip(paper: str) -> str:
    return f"read(paper_write({paper}, NULL, save_path = {R_TMP}, schema_version = '12.0'))"


def r_error(expr: str) -> str:
    return f"tryCatch({{{expr}; NULL}}, error = \\(e) conditionMessage(e))"


def r_variant(path: str, name: str, value: str) -> str:
    """R: a copy of *path* whose root ``"schema_version": "12.0"`` is *value* (JSON text).

    Text substitution on both sides (see ``_review_helpers.version_variant``),
    so R and Python read byte-identical files.
    """
    old = '"schema_version": "12.0"'
    new = '"schema_version": ' + value
    return (
        f"local({{x <- readLines('{path}', encoding = 'UTF-8'); "
        f"i <- which(grepl({json.dumps(old)}, x, fixed = TRUE))[1]; "
        f"x[i] <- sub({json.dumps(old)}, {json.dumps(new)}, x[i], fixed = TRUE); "
        f"path <- file.path(tempfile('pc_bibr12_'), '{name}'); dir.create(dirname(path)); "
        "writeLines(x, path, useBytes = TRUE); path})"
    )


def py_variant(path: str, name: str, value: str) -> str:
    return f"{H}.version_variant('{path}', '{name}', {value!r})"


def expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


def value_case(case_id: str, r: str, py: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": case_id,
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {"x": expr(r, py)},
        **extra,
    }


R_MODIFIED = (
    "local({p <- read('" + FX + "/edge_types.json'); "
    "tx <- p$text; n <- nrow(tx) + 1L; tx[n, ] <- NA; tx$text[n] <- 'Added row.'; "
    "tx$text_id <- as.double(tx$text_id); tx$text_id[n] <- 9; "
    "tx$paragraph_id <- as.double(tx$paragraph_id); tx$paragraph_id[n] <- 8; "
    "tx$section_id <- as.double(tx$section_id); tx$section_id[n] <- 2; "
    "tx$page_number <- as.double(tx$page_number); tx$formatted <- NULL; p$text <- tx; "
    "p$bib$year <- c(2019.9, NA); p$bib$volume <- c(12, 1e5); "
    "p$bib$is_in_press <- c('TRUE', 'no'); p$bib$extra_col <- c('x', 'y'); "
    "p$info$title <- NA_character_; p$info$keywords <- list(c('k1', 'k2')); "
    "p$author$role <- c('single', NA, 'x', 'y'); p$footnote <- NULL; p})"
)


def cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # reading: the whole paper object ------------------------------------------
    for name in EDGES:
        path = f"{FX}/{name}.json"
        out.append(
            {"id": f"read.{name}", "r": "read", "py": "pytacheck.read",
             "args": {"file_path": {"$file": path}}}
        )
    out.append(
        {"id": "read.edge_types.images", "r": "read", "py": "pytacheck.read",
         "args": {"file_path": {"$file": f"{FX}/edge_types.json"}, "include_images": True}}
    )
    for tbl in ("info", "author", "affiliation", "funding", "text", "section", "url", "bib",
                "xref", "figure", "table", "footnote", "eq", "info_match",
                "affiliation_match", "funding_match", "bib_match"):
        out.append(
            {"id": f"paper_table.edge_types.{tbl}", "r": "paper_table",
             "py": "pytacheck.paper_table",
             "args": {"paper": {"$paper": f"{FX}/edge_types.json"}, "table": tbl}}
        )
    for name in ("edge_types", "edge_minimal", "edge_numeric_id"):
        out.append(
            {"id": f"paper_validate.{name}", "r": "paper_validate",
             "py": "pytacheck.paper_validate",
             "args": {"paper": {"$paper": f"{FX}/{name}.json"}}}
        )
        out.append(
            {"id": f"ref_table.{name}", "r": "ref_table", "py": "pytacheck.ref_table",
             "args": {"paper": {"$paper": f"{FX}/{name}.json"}}}
        )

    # writing -------------------------------------------------------------------
    for name in EDGES:
        read = f"read('{FX}/{name}.json')"
        py_read = f"pc.read({H}.FX / '{name}.json')"
        blank = name in NO_TIME
        out.append(
            value_case(f"paper_write.text.{name}", r_written(read, blank),
                       f"{H}.written({py_read}, blank_time={blank})")
        )
        ignore = ["extraction.converter"] + (["extraction.completed_at"] if blank else [])
        out.append(
            value_case(f"paper_write.roundtrip.{name}", r_roundtrip(read),
                       f"{H}.roundtrip({py_read})", compare={"ignore": ignore})
        )
    out.append(
        value_case(
            "paper_write.completed_at.now",
            f"local({{f <- paper_write(read('{FX}/edge_minimal.json'), NULL, "
            f"save_path = {R_TMP}, schema_version = '12.0'); "
            "grepl('^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$', "
            "jsonlite::read_json(f)$extraction$completed_at)})",
            f"{H}.completed_at_is_iso(pc.read({H}.FX / 'edge_minimal.json'))",
        )
    )
    out.append(
        value_case("paper_write.text.modified", r_written(R_MODIFIED),
                   f"{H}.written({H}.modified_edge())")
    )
    out.append(
        value_case("paper_write.roundtrip.modified", r_roundtrip(R_MODIFIED),
                   f"{H}.roundtrip({H}.modified_edge())",
                   compare={"ignore": ["extraction.converter"]})
    )
    out.append(
        value_case("paper_write.roundtrip.images",
                   r_roundtrip(f"read('{FX}/edge_types.json', include_images = TRUE)"),
                   f"{H}.roundtrip(pc.read({H}.FX / 'edge_types.json', include_images=True))",
                   compare={"ignore": ["extraction.converter"]})
    )

    # schema_version dispatch -----------------------------------------------------
    versions = {"v12_0_1": '"12.0.1"', "v12_5_number": "12.5", "v12_array": '["12.0"]'}
    for vid, value in versions.items():
        r_path = r_variant(f"{FX}/edge_types.json", "v.json", value)
        py_path = py_variant(f"{FX}/edge_types.json", "v.json", value)
        out.append(
            {"id": f"read.version.{vid}", "r": "read", "py": "pytacheck.read",
             "args": {"file_path": expr(r_path, py_path)}}
        )
    errors = {
        "v12": '"12"',
        "v_true": "true",
        "v_empty_string": '""',
        "v11_1": '"11.1"',
        "v_space": '" 12.0"',
        "v_empty_array": "[]",
        "v_empty_object": "{}",
        "v_array_11": '["11.0", "12.0"]',
    }
    for vid, value in errors.items():
        r_path = r_variant(f"{FX}/edge_types.json", "v.json", value)
        py_path = py_variant(f"{FX}/edge_types.json", "v.json", value)
        out.append(
            value_case(f"read.error_message.{vid}", r_error(f"metacheck:::.read_bibr({r_path})"),
                       f"{H}.error_message(lambda: __import__('pytacheck.papers.io', "
                       f"fromlist=['x']).read_bibr({py_path}))")
        )
    # an array df with other than one element stops (is.null(df) || grepl(...))
    for name in ("edge_df_long", "edge_df_empty"):
        out.append(
            value_case(
                f"read.error_message.{name}",
                r_error(f"metacheck:::.read_bibr12('{FX}/{name}.json')"),
                f"{H}.error_message(lambda: __import__('pytacheck.io.bibr12', fromlist=['x'])"
                f".read_bibr12({H}.FX / '{name}.json'))",
            )
        )
    # a root schema_version of null is no schema_version: the older reader
    r_path = r_variant(f"{F12}/probe_html.json", "v.json", "null")
    py_path = py_variant(f"{F12}/probe_html.json", "v.json", "null")
    out.append(
        {"id": "read.version.null", "r": "read", "py": "pytacheck.read",
         "args": {"file_path": expr(r_path, py_path)}}
    )

    # writer error messages -------------------------------------------------------
    out.append(
        value_case(
            "paper_write.error_message.older_format",
            r_error(f"paper_write(demopaper(), NULL, save_path = {R_TMP}, schema_version = '12.0')"),
            f"{H}.error_message(lambda: pc.paper_write(pc.demopaper(), None, "
            f"{H}._tempdir(), schema_version='12.0'))",
        )
    )
    r_path = r_variant(f"{FX}/edge_types.json", "v.json", '"12.0.1"')
    py_path = py_variant(f"{FX}/edge_types.json", "v.json", '"12.0.1"')
    out.append(
        value_case(
            "paper_write.error_message.later_12x",
            r_error(f"paper_write(read({r_path}), NULL, save_path = {R_TMP}, "
                    "schema_version = '12.0')"),
            f"{H}.error_message(lambda: pc.paper_write(pc.read({py_path}), None, "
            f"{H}._tempdir(), schema_version='12.0'))",
        )
    )

    # paper_validate(): its warnings (the harness compares values only) ---------------
    # the paper is read first: reading warns on its own ("NAs introduced by coercion")
    r_validate = (
        "local({{p <- suppressWarnings({paper}); w <- character(0); "
        "v <- withCallingHandlers(paper_validate(p), warning = function(x) "
        "{{w <<- c(w, conditionMessage(x)); invokeRestart('muffleWarning')}}); "
        "list(value = v, warnings = w)}})"
    )
    validate_papers = {
        **{name: (f"read('{FX}/{name}.json')", f"pc.read({H}.FX / '{name}.json')")
           for name in EDGES},
        **{name: (f"read('{F12}/{name}.json')", f"pc.read({H}.F12 / '{name}.json')")
           for name in ("PMC4383902", "probe_docx")},
        "modified": (R_MODIFIED, f"{H}.modified_edge()"),
        # older-format papers: demo, psychsci, Grobid, test_paper
        "demo": ("demopaper()", "pc.demopaper()"),
        "psychsci": (
            "read('upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json')",
            "pc.read(__import__('pathlib').Path("
            "'upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json'))",
        ),
        "grobid": (
            "read('upstream/metacheck/tests/testthat/fixtures/debruine/debruine-child.xml')",
            "pc.read(__import__('pathlib').Path("
            "'upstream/metacheck/tests/testthat/fixtures/debruine/debruine-child.xml'))",
        ),
        "test_paper": ("test_paper()", "pc.test_paper()"),
    }
    for name, (r_paper, py_paper) in validate_papers.items():
        out.append(
            value_case(f"paper_validate.warnings.{name}",
                       r_validate.format(paper=r_paper),
                       f"{H}.validate_warnings({py_paper})")
        )

    # the older writer (schema_version = NULL) on 12.x papers, read back --------------
    for name, (r_paper, py_paper) in {
        "probe_html": (f"read('{F12}/probe_html.json')", f"pc.read({H}.F12 / 'probe_html.json')"),
        "edge_types": (f"read('{FX}/edge_types.json')", f"pc.read({H}.FX / 'edge_types.json')"),
    }.items():
        out.append(
            value_case(
                f"paper_write.legacy_roundtrip.{name}",
                f"read(paper_write({r_paper}, NULL, save_path = {R_TMP}))",
                f"{H}.legacy_roundtrip({py_paper})",
            )
        )

    # file names: one .json or .zip suffix is dropped
    out.append(
        value_case(
            "paper_write.file_name.suffix",
            f"local({{d <- {R_TMP}; p <- read('{F12}/probe_html.json'); "
            "c(basename(paper_write(p, 'a.zip.json', d, '12.0')), "
            "basename(paper_write(p, 'b.json.zip', d, '12.0')), "
            "basename(paper_write(p, 'c.JSON', d, '12.0')))})",
            f"{H}.file_names(pc.read({H}.F12 / 'probe_html.json'), "
            "['a.zip.json', 'b.json.zip', 'c.JSON'])",
        )
    )

    # modules on the adversarial 12.x paper ---------------------------------------
    for mod in ("ref_consistency", "ref_miscitation", "all_urls", "stat_check",
                "all_p_values", "ref_summary"):
        out.append(
            {"id": f"module.{mod}.edge_types", "module": mod,
             "args": {"paper": {"$paper": f"{FX}/edge_types.json"}}}
        )
    return out


HEADER = """\
# Adversarial review of bibr export schema 12.x reading and writing (metacheck PR #423).
# Written by tests/bibr12/gen_review_cases.py; goldens: python -m parity generate --area bibr12_review
#
# Fixtures: tests/bibr12/fixtures/edge_*.json (coercion edge cases, missing tables and keys,
# unknown keys, jsonlite number/string formatting). $expr helpers: tests/bibr12/_review_helpers.py.
"""


def main() -> None:
    lines = [HEADER.rstrip("\n"), 'area: "bibr12_review"', "cases:"]
    for case in cases():
        first = True
        for key, value in case.items():
            prefix = "- " if first else "  "
            first = False
            if isinstance(value, dict):
                lines.append(f"{prefix}{json.dumps(key)}:")
                for k, v in value.items():
                    lines.append(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
            else:
                lines.append(f"{prefix}{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(cases())} cases)")


if __name__ == "__main__":
    main()
