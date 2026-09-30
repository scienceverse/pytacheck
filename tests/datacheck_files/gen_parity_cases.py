"""Generate parity/cases/datacheck_files*.yaml (run from the repository root).

``datacheck_files.yaml`` holds cases that depend only on this area;
``datacheck_files_deps.yaml`` holds cases that also need functions ported by
other areas (file_category()/filetype() in metacheck.fileinfo, the Qualtrics
and header-promotion checks in metacheck.datacheck.checks, import_jasp()/
import_omv() in metacheck.statout).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = "tests/datacheck_files/data"
PY = "metacheck.datacheck.files"
HELP = "tests.datacheck_files.parity_helpers"


def fn(r: str, py: str, **args: Any) -> dict[str, Any]:
    return {"r": r, "py": py, "args": args}


def f(name: str) -> dict[str, str]:
    return {"$file": f"{DATA}/{name}"}


cases: list[dict[str, Any]] = []
deps: list[dict[str, Any]] = []


def _r_str(v: Any) -> str:
    if v is None:
        return "NA"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _na_safe(x: Any) -> Any:
    """Rewrite ``$chr``/``$df`` specs holding nulls as ``$expr`` blocks.

    The harness builds ``$chr`` with ``unlist()`` (which drops nulls) and ``$df``
    columns with ``vapply()`` (which errors on NA in a character column), so
    vectors and frames with missing strings are spelled out in R and Python.
    """
    if isinstance(x, dict) and list(x) == ["$chr"] and None in x["$chr"]:
        vals = x["$chr"]
        return {
            "$expr": {"r": "c(" + ", ".join(_r_str(v) for v in vals) + ")", "py": repr(list(vals))}
        }
    if isinstance(x, dict) and list(x) == ["$df"] and any(None in col for col in x["$df"].values()):
        cols = x["$df"]
        r = ", ".join(f"`{k}` = c({', '.join(_r_str(v) for v in col)})" for k, col in cols.items())
        py = ", ".join(
            f"{k!r}: pd.array({list(col)!r}, dtype='string')"
            if all(isinstance(v, str) for v in col if v is not None)
            else f"{k!r}: {list(col)!r}"
            for k, col in cols.items()
        )
        return {
            "$expr": {
                "r": f"data.frame({r}, stringsAsFactors = FALSE, check.names = FALSE)",
                "py": f"pd.DataFrame({{{py}}})",
            }
        }
    if isinstance(x, dict):
        return {k: _na_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_na_safe(v) for v in x]
    return x


def add(case_id: str, spec: dict[str, Any], dep: bool = False, **extra: Any) -> None:
    (deps if dep else cases).append({"id": case_id, **_na_safe(spec), **extra})


# -- data_format --------------------------------------------------------------
add(
    "data_format.mixed",
    fn(
        "data_format",
        f"{PY}.data_format",
        ext={"$chr": ["csv", "edf", "mp4", "sav", "ODS", "fods", "RData", "jasp", "", "txt"]},
    ),
)
add("data_format.empty", fn("data_format", f"{PY}.data_format", ext={"$chr": []}))
add("data_format.na", fn("data_format", f"{PY}.data_format", ext={"$chr": [None, "tsv"]}))

# -- data_is_manifest ---------------------------------------------------------
repo = {"$chr": ["Study 1.r", "Study 1.csv", "notes.txt"]}
manifest_df = {
    "$df": {"type": ["code", "data", "doc"], "file": ["Study 1.r", "Study 1.csv", "notes.txt"]}
}
add(
    "data_is_manifest.manifest",
    fn("data_is_manifest", f"{PY}.data_is_manifest", df=manifest_df, repo_files=repo),
)
add(
    "data_is_manifest.real",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df={"$df": {"id": [1, 2, 3], "score": [1.1, 2.2, 3.3]}},
        repo_files=repo,
    ),
)
add(
    "data_is_manifest.one_ext",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df={"$df": {"stim": ["a.png", "b.png", "c.png"]}},
        repo_files={"$chr": ["img/a.png", "img/b.png", "img/c.png"]},
    ),
)
add(
    "data_is_manifest.one_ext_min1",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df={"$df": {"stim": ["a.png", "b.png", "c.png"]}},
        repo_files={"$chr": ["img/a.png", "img/b.png", "img/c.png"]},
        min_exts=1,
    ),
)
add(
    "data_is_manifest.paths",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df={"$df": {"f": ["dir\\A.CSV", " sub/b.R ", "x", "c.txt", None]}},
        repo_files={"$chr": ["osf/a.csv", "b.r", "C.TXT", None, ""]},
        threshold=0.75,
    ),
)
add(
    "data_is_manifest.no_repo",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df=manifest_df,
        repo_files={"$chr": [None, ""]},
    ),
)
add(
    "data_is_manifest.null",
    fn("data_is_manifest", f"{PY}.data_is_manifest", df={"$null": True}, repo_files=repo),
)
add(
    "data_is_manifest.from_file",
    fn(
        "data_is_manifest",
        f"{PY}.data_is_manifest",
        df={
            "$call": {
                "r": "data_read_head",
                "py": f"{PY}.data_read_head",
                "args": {
                    "path": f("manifest.csv"),
                    "n_rows": {"$expr": {"r": "Inf", "py": "float('inf')"}},
                },
            }
        },
        repo_files=repo,
    ),
)

# -- text_peek / txt_classify_content / sniffers -------------------------------
inf = {"$expr": {"r": "Inf", "py": "float('inf')"}}
for name in [
    "eprime_utf16.txt",
    "eprime_utf8.txt",
    "table.txt",
    "prose.txt",
    "bom.csv",
    "crlf.csv",
    "cr_only.csv",
    "empty.csv",
    "utf16_table.txt",
    "blank_lines.csv",
    "latin1_notes.txt",
    "does_not_exist.txt",
]:
    add(f"text_peek.{name}", fn("text_peek", f"{PY}.text_peek", path=f(name)))
add("text_peek.n2", fn("text_peek", f"{PY}.text_peek", path=f("hundred.csv"), n=2))
add("text_peek.inf", fn("text_peek", f"{PY}.text_peek", path=f("quoted.csv"), n=inf))
add("text_peek.dir", fn("text_peek", f"{PY}.text_peek", path={"$file": DATA}))
for name in [
    "eprime_utf16.txt",
    "eprime_utf8.txt",
    "table.txt",
    "prose.txt",
    "one_line.txt",
    "pipe.txt",
    "latin1_notes.txt",
    "headerless.csv",
    "comments.csv",
    "empty.csv",
    "semicolon.csv",
    "utf16_table.txt",
    "rwrite.csv",
]:
    add(
        f"txt_classify_content.{name}",
        fn("txt_classify_content", f"{PY}.txt_classify_content", path=f(name)),
    )
for name in [
    "rwrite.csv",
    "semicolon.csv",
    "tabbed.tsv",
    "pipe.txt",
    "comments.csv",
    "empty.csv",
    "prose.txt",
    "latin1_header.csv",
    "whitespace.csv",
]:
    add(f"sniff_delimiter.{name}", fn(".sniff_delimiter", f"{PY}._sniff_delimiter", path=f(name)))
for name, sep in [
    ("rwrite.csv", ","),
    ("headerless.csv", ","),
    ("headerless_na.csv", ","),
    ("comments.csv", ","),
    ("single_col.csv", ","),
    ("empty.csv", ","),
    ("tabbed.tsv", "\t"),
    ("quoted.csv", ","),
]:
    add(
        f"detect_header.{name}", fn(".detect_header", f"{PY}._detect_header", path=f(name), sep=sep)
    )
# ids are file names: no '"' (Windows rejects it)
_COUNT_FIELDS_IDS = {"": "empty", '"a,b",c': "quoted_a,b,c", '"x"y"z",1': "escaped_quotes,1"}
for line, sep in [("a,b,c", ","), ('"a,b",c', ","), ("", ","), ('"x"y"z",1', ","), ("a;b;;c", ";")]:
    add(
        f"count_fields.{_COUNT_FIELDS_IDS.get(line, line)}",
        fn(".count_fields", f"{PY}._count_fields", line=line, sep=sep),
    )
for name in ["blob.csv", "bigcells.csv", "single_col.csv", "empty.csv"]:
    add(
        f"is_single_field_blob.{name}",
        fn(".is_single_field_blob", f"{PY}._is_single_field_blob", path=f(name), sep=","),
    )

# -- data_read_head --------------------------------------------------------------
read_files = [
    "rwrite.csv",
    "hundred.csv",
    "semicolon.csv",
    "tabbed.tsv",
    "quoted.csv",
    "latin1_row3.csv",
    "latin1_header.csv",
    "bom.csv",
    "crlf.csv",
    "headerless.csv",
    "headerless_na.csv",
    "single_col.csv",
    "blank_lines.csv",
    "trailing_blank.csv",
    "footer.csv",
    "ragged.csv",
    "title_line.csv",
    "comments.csv",
    "dates.csv",
    "types.csv",
    "na_strings.csv",
    "empty.csv",
    "whitespace.csv",
    "header_only.csv",
    "cr_only.csv",
    "cr_blank_start.csv",
    "pipe.txt",
    "table.txt",
    "prose.txt",
    "utf16_table.txt",
    "blob.csv",
    "bigcells.csv",
    "manifest.csv",
    "xl_basic.xlsx",
    "xl_mixed.xlsx",
    "xl_offset.xlsx",
    "xl_names.xlsx",
    "xl_gaps.xlsx",
    "xl_nums.xlsx",
    "xl_bools.xlsx",
    "xl_dates.xlsx",
    "xl_header_only.xlsx",
    "xl_numhdr.xlsx",
    "xl_coerce.xlsx",
    "xl_sheets.xlsx",
    "xl_error.xlsx",
    "readxl_datasets.xlsx",
    "readxl_datasets.xls",
    "readxl_type-me.xlsx",
    "readxl_type-me.xls",
    "readxl_geometry.xlsx",
    "readxl_clippy.xlsx",
    "written.ods",
    "written.fods",
    "typed.ods",
    "labelled.sav",
    "labelled.dta",
    "portable.por",
    "iris.sas7bdat",
    "study.rds",
    "tibble.rds",
    "list.rds",
    "odd.rds",
    "badname.rds",
    "latin1.rds",
    "study_xz.rds",
    "study_bz2.rds",
    "study_ascii.rds",
    "workspace.RData",
    "noframe.RData",
    "twoframes.rda",
    "script_refs.R",
    "labs.rds",
    "labs_ws.RData",
    "does_not_exist.csv",
]
promote_sensitive = {
    "qualtrics.csv",
    "xl_title.xlsx",
    "title.ods",
    "offset.ods",
    "readxl_deaths.xlsx",
    "readxl_deaths.xls",
}
# data_read_head.odd.rds (integer64) is marked in parity/divergences/data.yaml (D10)
for name in read_files:
    add(
        f"data_read_head.{name}",
        fn("data_read_head", f"{PY}.data_read_head", path=f(name), n_rows=inf),
    )
for name in [
    "hundred.csv",
    "rwrite.csv",
    "xl_coerce.xlsx",
    "labelled.sav",
    "study.rds",
    "workspace.RData",
    "semicolon.csv",
    "typed.ods",
    "readxl_datasets.xls",
    "iris.sas7bdat",
    "title_line.csv",
    "footer.csv",
]:
    add(f"data_read_head.{name}.n5", fn("data_read_head", f"{PY}.data_read_head", path=f(name)))
add(
    "data_read_head.xl_sheets.second",
    fn(
        "data_read_head",
        f"{PY}.data_read_head",
        path=f("xl_sheets.xlsx"),
        n_rows=inf,
        sheet="second",
    ),
)
add(
    "data_read_head.xl_sheets.index2",
    fn("data_read_head", f"{PY}.data_read_head", path=f("xl_sheets.xlsx"), n_rows=inf, sheet=2),
)
add(
    "data_read_head.xl_sheets.missing",
    fn(
        "data_read_head", f"{PY}.data_read_head", path=f("xl_sheets.xlsx"), n_rows=inf, sheet="nope"
    ),
)
add(
    "data_read_head.hundred.n0",
    fn("data_read_head", f"{PY}.data_read_head", path=f("hundred.csv"), n_rows=0),
)
for name in sorted(promote_sensitive):
    add(
        f"data_read_head.{name}",
        fn("data_read_head", f"{PY}.data_read_head", path=f(name), n_rows=inf),
        dep=True,
    )
for name in ["sample.jasp", "sample.omv"]:
    add(
        f"data_read_head.{name}",
        {
            "r": "data_read_head",
            "py": f"{PY}.data_read_head",
            "args": {
                "path": {"$file": f"upstream/metacheck/tests/testthat/fixtures/formats/{name}"},
                "n_rows": inf,
            },
        },
        dep=True,
    )

# column attributes (haven labels, formats, classes) and utf8_repaired
ATTRS_R = (
    'local({{d <- data_read_head(rpath("{path}"), n_rows = {n}); '
    "if (is.null(d)) return(list(names = NULL, attrs = NULL, utf8_repaired = NULL)); "
    "list(names = names(d), attrs = lapply(d, function(x) {{a <- attributes(x); "
    "a$levels <- NULL; a$names <- NULL; if (!is.null(a$labels)) a$labels <- as.list(a$labels); "
    "if (length(a)) a[order(names(a))] else NULL}}), "
    'utf8_repaired = if (is.null(attr(d, "utf8_repaired"))) NULL else as.list(attr(d, "utf8_repaired")))}})'
)
attr_files = [
    ("labelled.sav", "Inf"),
    ("labelled.dta", "Inf"),
    ("portable.por", "Inf"),
    ("iris.sas7bdat", "Inf"),
    ("odd.rds", "Inf"),
    ("latin1_row3.csv", "Inf"),
    ("latin1_header.csv", "Inf"),
    ("badname.rds", "Inf"),
    ("latin1.rds", "Inf"),
    ("labs.rds", "Inf"),
    ("labs.rds", "2"),
    ("labs_ws.RData", "Inf"),
    ("labs_ws.RData", "2"),
    ("labelled.sav", "2"),
    ("dates.csv", "Inf"),
    ("xl_dates.xlsx", "Inf"),
    ("typed.ods", "Inf"),
]
for name, n in attr_files:
    add(
        f"data_read_head.attrs.{name}" + ("" if n == "Inf" else f".n{n}"),
        {
            "r": "identity",
            "py": f"{HELP}.read_head_attrs",
            "args": {
                "x": {"$expr": {"r": ATTRS_R.format(path=f"{DATA}/{name}", n=n), "py": "None"}}
            },
            "py_drop": ["x"],
            "py_args": {"path": f"{DATA}/{name}", "n_rows": float(n) if n != "Inf" else None},
        },
    )

add(
    "read_rdata_isolated.workspace",
    fn(".read_rdata_isolated", f"{PY}._read_rdata_isolated", path=f("workspace.RData"), n_rows=inf),
)
add(
    "read_rdata_isolated.twoframes",
    fn(".read_rdata_isolated", f"{PY}._read_rdata_isolated", path=f("twoframes.rda"), n_rows=inf),
)
add(
    "read_rdata_isolated.noframe",
    fn(".read_rdata_isolated", f"{PY}._read_rdata_isolated", path=f("noframe.RData"), n_rows=2),
)
add(
    "read_rdata_isolated.missing",
    fn(".read_rdata_isolated", f"{PY}._read_rdata_isolated", path=f("nothing.RData")),
)

# -- grouping ------------------------------------------------------------------
paths = [
    "Experiment 1/data.csv",
    "study2a_data.csv",
    "dataexperiment1creplication.csv",
    "experiment3explicit/x.csv",
    "index1.csv",
    "flex2/a.csv",
    "pilot/p.csv",
    "Pilot 2/q.csv",
    "exp4b_run.csv",
    "Study_12/raw.csv",
    "ex-5/x",
    "",
    None,
    "a/b/c.csv",
    "EXPT7.R",
]
add(
    "data_group_from_path.mixed",
    fn(".data_group_from_path", f"{PY}._data_group_from_path", paths={"$chr": paths}),
)
add(
    "data_group_normalize.mixed",
    fn(
        ".data_group_normalize",
        f"{PY}._data_group_normalize",
        x={
            "$chr": [
                "Experiment 1",
                "study 2a",
                "pilot",
                "Pilot 3",
                "ex1",
                "shared",
                "exp_4",
                "expt10b",
                " EX 2 ",
                "ex100",
                None,
                "study2ab",
            ]
        },
    ),
)
add(
    "data_group_sort.mixed",
    fn(
        ".data_group_sort",
        f"{PY}._data_group_sort",
        codes={"$chr": ["ex2", "pilot1", "ex1b", "ex10", "ex1", "ex1a", "pilot2"]},
    ),
)
add("data_group_sort.empty", fn(".data_group_sort", f"{PY}._data_group_sort", codes={"$chr": []}))
add(
    "data_group_check_roster.agree",
    fn(
        ".data_group_check_roster",
        f"{PY}._data_group_check_roster",
        groups={"$chr": ["ex1", "ex2", None, "ex1"]},
        roster={"$chr": ["ex2", "ex1"]},
    ),
)
add(
    "data_group_check_roster.differ",
    fn(
        ".data_group_check_roster",
        f"{PY}._data_group_check_roster",
        groups={"$chr": ["ex1", "ex3"]},
        roster={"$chr": ["ex1", "ex2"]},
    ),
)
add(
    "data_group_check_roster.empty",
    fn(
        ".data_group_check_roster",
        f"{PY}._data_group_check_roster",
        groups={"$chr": ["ex1"]},
        roster={"$chr": []},
    ),
)
add(
    "data_group_from_repo.two",
    fn(
        ".data_group_from_repo",
        f"{PY}._data_group_from_repo",
        repo={"$chr": ["r1", "r1", "r2", "r2", None]},
        paths={"$chr": ["a.csv", "b.csv", "c.csv", "d.csv", "e.csv"]},
    ),
)
add(
    "data_group_from_repo.mirror",
    fn(
        ".data_group_from_repo",
        f"{PY}._data_group_from_repo",
        repo={"$chr": ["r1", "r1", "r2", "r2", "r3"]},
        paths={"$chr": ["a.csv", "b.csv", "x/a.csv", "B.CSV", "z.csv"]},
    ),
)
add(
    "data_group_from_repo.single",
    fn(
        ".data_group_from_repo",
        f"{PY}._data_group_from_repo",
        repo={"$chr": ["r1", "", None]},
        paths={"$chr": ["a", "b", "c"]},
    ),
)
add(
    "data_code_refs.script", fn(".data_code_refs", f"{PY}._data_code_refs", path=f("script_refs.R"))
)
add("data_code_refs.data", fn(".data_code_refs", f"{PY}._data_code_refs", path=f("rwrite.csv")))
add("data_code_refs.missing", fn(".data_code_refs", f"{PY}._data_code_refs", path=f("nope.R")))
# U63: a Latin-1 script (R fails "input string 1 is invalid UTF-8")
add(
    "data_code_refs.latin1",
    fn(".data_code_refs", f"{PY}._data_code_refs", path=f("script_latin1.R")),
)
add(
    "strip_llm_wrapper.prefixed",
    fn(
        ".strip_llm_wrapper",
        f"{PY}._strip_llm_wrapper",
        df={"$df": {"assignments.index": [1, 2], "assignments.group": ["ex1", "ex2"]}},
        wrapper="assignments",
    ),
)
add(
    "strip_llm_wrapper.bare",
    fn(
        ".strip_llm_wrapper",
        f"{PY}._strip_llm_wrapper",
        df={"$df": {"index": [1], "group": ["x"]}},
        wrapper="assignments",
    ),
)
add(
    "strip_llm_wrapper.null",
    fn(".strip_llm_wrapper", f"{PY}._strip_llm_wrapper", df={"$null": True}, wrapper="assignments"),
)

files_mat = {
    "$df": {
        "file_name": ["fig1.png", "photo.jpg", "manual.pdf"],
        "data_type": ["materials", "materials", "materials"],
    }
}
add("data_group_llm.materials", fn("data_group_llm", f"{PY}.data_group_llm", files=files_mat))
add("data_group_llm.null", fn("data_group_llm", f"{PY}.data_group_llm", files={"$null": True}))
multi = {
    "$df": {
        "file_name": ["a.csv", "b.R", "c.csv", "d.csv", "readme.md", "e.csv", "s.png"],
        "file_path": [
            "osf1/a.csv",
            "osf1/b.R",
            "osf2/c.csv",
            "Study 3/d.csv",
            "readme.md",
            "e.csv",
            "stim/s.png",
        ],
        "repo_url": [
            "https://osf.io/aaaaa",
            "https://osf.io/aaaaa",
            "https://osf.io/bbbbb",
            "https://osf.io/bbbbb",
            "https://osf.io/aaaaa",
            "https://osf.io/ccccc",
            "https://osf.io/ccccc",
        ],
        "data_type": ["data", "code", "data", "data", "documentation", "data", "materials"],
    }
}
add("data_group_llm.multi_repo", fn("data_group_llm", f"{PY}.data_group_llm", files=multi))
single = {
    "$df": {
        "file_name": ["exp1_raw.csv", "analysis.R", "exp2_raw.csv", "stimuli.zip", "notes.pdf"],
        "file_path": [
            "Exp1/exp1_raw.csv",
            "analysis.R",
            "Exp2/exp2_raw.csv",
            "stimuli.zip",
            "notes.pdf",
        ],
        "data_type": ["data", "code", "data", "unknown", "documentation"],
    }
}
add("data_group_llm.single_repo", fn("data_group_llm", f"{PY}.data_group_llm", files=single))
roster_paper = {
    "$test_paper": {
        "text": [
            "In Experiment 1 we tested x.",
            "Study 2a replicated it.",
            "A pilot 1 preceded both.",
        ]
    }
}
add(
    "data_group_llm.roster",
    fn("data_group_llm", f"{PY}.data_group_llm", files=multi, paper=roster_paper),
)
refs = {
    "$expr": {
        "r": (
            'data.frame(file_name = c("clean.rds", "run.R", "raw_scores.csv", "Stimuli.xlsx"), '
            'file_path = c("processed/clean.rds", "Study 1/run.R", "raw/raw_scores.csv", '
            '"study2/Stimuli.xlsx"), data_type = c("data", "code", "data", "materials"), '
            f'file_location = c(NA, rpath("{DATA}/script_refs.R"), NA, NA))'
        ),
        "py": (
            'pd.DataFrame({"file_name": ["clean.rds", "run.R", "raw_scores.csv", "Stimuli.xlsx"], '
            '"file_path": ["processed/clean.rds", "Study 1/run.R", "raw/raw_scores.csv", '
            '"study2/Stimuli.xlsx"], "data_type": ["data", "code", "data", "materials"], '
            f'"file_location": [None, "{DATA}/script_refs.R", None, None]}})'
        ),
    }
}
add("data_group_llm.code_refs", fn("data_group_llm", f"{PY}.data_group_llm", files=refs))
for attr in ["roster", "roster_check", "no_evidence", "unresolved", "model"]:
    for label, files, paper in [
        ("multi_repo", multi, None),
        ("roster", multi, roster_paper),
        ("materials", files_mat, None),
        ("single_repo", single, None),
    ]:
        args: dict[str, Any] = {"files": files}
        if paper is not None:
            args["paper"] = paper
        add(
            f"data_group_llm.attr.{attr}.{label}",
            {
                "r": "attr",
                "py": f"{HELP}.frame_attr",
                "args": {
                    "x": {
                        "$call": {"r": "data_group_llm", "py": f"{PY}.data_group_llm", "args": args}
                    },
                    "which": attr,
                },
            },
        )
add(
    "data_study_roster.test_paper",
    fn("data_study_roster", f"{PY}.data_study_roster", paper=roster_paper),
)
add(
    "data_study_roster.demo",
    fn("data_study_roster", f"{PY}.data_study_roster", paper={"$paper": "demo"}),
)
add(
    "data_study_roster.none",
    fn(
        "data_study_roster",
        f"{PY}.data_study_roster",
        paper={"$test_paper": {"text": ["No numbered studies here.", "The experiment went fine."]}},
    ),
)
add(
    "data_study_roster.not_paper",
    fn("data_study_roster", f"{PY}.data_study_roster", paper="Experiment 1"),
)

# -- manifests -------------------------------------------------------------------
add(
    "manifest_merge.create",
    {
        "r": "identity",
        "py": f"{HELP}.manifest_merge_lines",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({p <- tempfile(fileext = ".json"); metacheck::manifest_merge(p, '
                        'list(code = list(packages = list("dplyr", "tidyr")))); '
                        'metacheck::manifest_merge(p, list(files = list(list(file_name = "a.csv", size = 12.5, '
                        "n = 3L, ok = TRUE, miss = NA, nested = list(a = NULL, b = list()))))); "
                        "metacheck::manifest_merge(p, list(extra = 0.000012345, big = 5e8, tiny = 1e-7)); "
                        "readLines(p)})"
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {
            "steps": [
                {"code": {"packages": ["dplyr", "tidyr"]}},
                {
                    "files": [
                        {
                            "file_name": "a.csv",
                            "size": 12.5,
                            "n": 3,
                            "ok": True,
                            "miss": None,
                            "nested": {"a": {"$rnull": True}, "b": []},
                        }
                    ]
                },
                {"extra": 0.000012345, "big": 5e8, "tiny": 1e-7},
            ]
        },
    },
)
add(
    "manifest_merge.remove_key",
    {
        "r": "identity",
        "py": f"{HELP}.manifest_merge_lines",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({p <- tempfile(fileext = ".json"); metacheck::manifest_merge(p, '
                        'list(a = 1, b = "x", c = NA)); metacheck::manifest_merge(p, list(b = NULL, d = TRUE)); '
                        "readLines(p)})"
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {"steps": [{"a": 1.0, "b": "x", "c": None}, {"b": {"$rnull": True}, "d": True}]},
    },
)
manifest_files = (
    'data.frame(repo_url = "https://osf.io/xxxxx", '
    'file_name = c("gotit.csv", "big.rdata", "stim.mp4", "lost.csv", "nourl.csv", "notes.pdf"), '
    'file_path = c("gotit.csv", "big.rdata", "stim.mp4", "lost.csv", "nourl.csv", "notes.pdf"), '
    'file_url = c(rep("https://osf.io/download/x/", 4), NA, "https://osf.io/download/y/"), '
    "file_size = c(100, 5e8, 1e6, 100, 100, 100), "
    'data_type = c("data", "data", "materials", "data", "data", "documentation"), '
    'data_format = "tabular", '
    f'file_location = c(rpath("{DATA}/rwrite.csv"), NA, NA, NA, NA, NA), stringsAsFactors = FALSE)'
)
add(
    "data_check_write_manifest.split",
    {
        "r": "identity",
        "py": f"{HELP}.write_manifest_json",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({stable <- function(m) {m$generated <- "<volatile>"; m$provenance <- "<volatile>"; m}; p <- tempfile(fileext = ".json"); '
                        f"files <- {manifest_files}; "
                        "metacheck:::.data_check_write_manifest(p, files, c(TRUE, TRUE, FALSE, TRUE, TRUE, FALSE), "
                        'gated = NULL, paper_id = "p1", download = "data", max_file_size = 100, '
                        'max_download_size = 500, skip_types = "materials", '
                        'oversize = data.frame(repo_url = "https://osf.io/xxxxx", file_name = "big.rdata", file_size = 5e8), '
                        'failed = data.frame(repo_url = "https://osf.io/xxxxx", file_name = "lost.csv", '
                        'error = "HTTP 429 Too Many Requests.\\ndetails"), model = "test-model"); '
                        "stable(jsonlite::fromJSON(p, simplifyVector = FALSE))})"
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {"scenario": "split"},
        "compare": {"ignore": ["generated", "provenance"]},
    },
)
add(
    "data_check_write_manifest.none",
    {
        "r": "identity",
        "py": f"{HELP}.write_manifest_json",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({stable <- function(m) {m$generated <- "<volatile>"; m$provenance <- "<volatile>"; m}; p <- tempfile(fileext = ".json"); '
                        'files <- data.frame(repo_url = "https://osf.io/xxxxx", file_name = "a.csv", '
                        'file_path = "a.csv", file_url = "https://osf.io/download/x/", file_size = 10, '
                        'data_type = "data", data_format = "tabular", file_location = NA_character_, '
                        'provider = "osfstorage", stringsAsFactors = FALSE); '
                        "metacheck:::.data_check_write_manifest(p, files, want = TRUE, gated = NULL, "
                        'paper_id = "p1", download = "none", max_file_size = 100, max_download_size = 500, '
                        'skip_types = "materials"); stable(jsonlite::fromJSON(p, simplifyVector = FALSE))})'
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {"scenario": "none"},
        "compare": {"ignore": ["generated", "provenance"]},
    },
)
add(
    "data_check_write_manifest.per_paper",
    {
        "r": "identity",
        "py": f"{HELP}.write_manifest_json",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({stable <- function(m) {m$generated <- "<volatile>"; m$provenance <- "<volatile>"; m}; d <- tempfile(); '
                        'files <- data.frame(paper_id = c("paperA", "paperB"), '
                        'repo_url = c("https://osf.io/aaaaa", "https://osf.io/bbbbb"), '
                        'file_name = c("dataA.csv", "dataB.csv"), file_path = c("dataA.csv", "dataB.csv"), '
                        'file_url = c("https://osf.io/download/aaaaa/", "https://osf.io/download/bbbbb/"), '
                        'file_size = c(100, 100), data_type = c("data", "data"), '
                        'data_format = c("tabular", "tabular"), '
                        f'file_location = c(rpath("{DATA}/rwrite.csv"), NA_character_), stringsAsFactors = FALSE); '
                        'failed <- data.frame(repo_url = "https://osf.io/bbbbb", file_name = "dataB.csv", '
                        'error = "download failed (nothing was written)", stringsAsFactors = FALSE); '
                        "paths <- metacheck:::.data_check_write_manifest(d, files, want = c(TRUE, TRUE), "
                        'gated = NULL, paper_id = c("paperA", "paperB"), download = "data", max_file_size = 100, '
                        "max_download_size = 500, failed = failed); "
                        "lapply(sort(basename(paths)), function(b) stable(jsonlite::fromJSON(file.path(d, b), "
                        "simplifyVector = FALSE)))})"
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {"scenario": "per_paper"},
        "compare": {
            "ignore": ["[0].generated", "[0].provenance", "[1].generated", "[1].provenance"]
        },
    },
)
add(
    "data_check_write_manifest.empty",
    {
        "r": "identity",
        "py": f"{HELP}.write_manifest_json",
        "args": {
            "x": {
                "$expr": {
                    "r": (
                        'local({stable <- function(m) {m$generated <- "<volatile>"; m$provenance <- "<volatile>"; m}; d <- tempfile(); '
                        "files <- data.frame(repo_url = character(0), file_name = character(0)); "
                        "paths <- metacheck:::.data_check_write_manifest(d, files, logical(0), NULL, "
                        'paper_id = c("p1", "p2"), download = "data", max_file_size = NULL, max_download_size = Inf); '
                        "lapply(sort(basename(paths)), function(b) stable(jsonlite::fromJSON(file.path(d, b), "
                        "simplifyVector = FALSE)))})"
                    ),
                    "py": "None",
                }
            }
        },
        "py_drop": ["x"],
        "py_args": {"scenario": "empty"},
        "compare": {
            "ignore": ["[0].generated", "[0].provenance", "[1].generated", "[1].provenance"]
        },
    },
)

# -- likert ----------------------------------------------------------------------
likert_vectors = {
    "clean_1_5": "set.seed(1); sample(1:5, 200, TRUE)",
    "bipolar": "set.seed(2); sample(-5:5, 300, TRUE)",
    "floor_inferred": "set.seed(3); sample(3:5, 200, TRUE)",
    "zero_present": "set.seed(4); sample(c(0,2,3,4,5), 200, TRUE)",
    "gap_bridged": "set.seed(5); sample(c(1,2,5,6,7), 200, TRUE)",
    "suspect_99": "set.seed(6); c(sample(1:5, 200, TRUE), 99)",
    "suspect_33": "set.seed(7); c(sample(1:7, 200, TRUE), 33)",
    "adjacent_6": "set.seed(8); c(sample(1:5, 200, TRUE), 6)",
    "neg99": "set.seed(9); c(sample(1:5, 200, TRUE), -99, -99)",
    "age": "set.seed(10); sample(18:65, 200, TRUE)",
    "rt": "set.seed(11); round(rlnorm(200, log(650), .35))",
    "coded": "set.seed(12); sample(c(10, 20, 30), 200, TRUE)",
    "count": "set.seed(13); rpois(200, 8)",
    "nonint": "set.seed(14); rnorm(200)",
    "few": "c(1, 2, 3)",
    "na_inf": "set.seed(15); c(sample(1:7, 60, TRUE), NA, NaN, Inf, -Inf)",
    "wide_11": "set.seed(16); sample(-11:11, 400, TRUE)",
}


def _r_vectors(exprs: dict[str, str]) -> dict[str, list[Any]]:
    """Evaluate R expressions once and return their values (NA -> None)."""
    import json
    import os
    import subprocess

    rscript = os.environ.get("PYTACHECK_RSCRIPT", "Rscript")
    code = (
        "cat(jsonlite::toJSON(list("
        + ", ".join(f"`{k}` = {{{v}}}" for k, v in exprs.items())
        + '), digits = NA, na = "string", auto_unbox = FALSE))'
    )
    out = subprocess.run([rscript, "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _dbl(values: list[Any]) -> dict[str, Any]:
    fixed: list[Any] = []
    for v in values:
        if isinstance(v, str):  # jsonlite writes NaN/Inf as strings
            v = {"NA": None, "NaN": float("nan"), "Inf": float("inf"), "-Inf": float("-inf")}[v]
        fixed.append(v)
    return {"$dbl": fixed}


LIKERT = _r_vectors(likert_vectors)
for name, values in LIKERT.items():
    add(
        f"detect_likert_scale.{name}",
        fn(".detect_likert_scale", f"{PY}._detect_likert_scale", x=_dbl(values)),
    )
add(
    "detect_likert_scale.max_levels",
    fn(
        ".detect_likert_scale",
        f"{PY}._detect_likert_scale",
        x=_dbl(LIKERT["wide_11"]),
        max_levels=10,
    ),
)
add(
    "detect_likert_scale.min_coverage",
    fn(
        ".detect_likert_scale",
        f"{PY}._detect_likert_scale",
        x=_dbl(LIKERT["suspect_99"]),
        min_coverage=1.0,
    ),
)
add(
    "detect_likert_scale.min_core",
    fn(
        ".detect_likert_scale",
        f"{PY}._detect_likert_scale",
        x=_dbl(LIKERT["floor_inferred"]),
        min_core=6,
    ),
)

# -- classification (needs metacheck.fileinfo: file_category(), filetype()) ------
classify_names = [
    "data.csv",
    "analysis.R",
    "README.md",
    "codebook.xlsx",
    "photo.png",
    "experiment.psyexp",
    "notes.pdf",
    "archive.zip",
    "sample.fasta",
    "sample.fasta.gz",
    "random_archive.tar.gz",
    "sample.dat.gz",
    "Results.docx",
    "ro-crate-metadata.json",
    "LICENSE",
    "survey.qsf",
    "model.stan",
    "run.sbatch",
    "prereg.pdf",
    "code_book.csv",
    "metadata.csv",
    "stimuli list.txt",
    "x.jasp",
    "study.omv",
    "data.por",
    "output.out",
    "notes",
    ".gitignore",
    "plot.svg",
    "Materials - Exp 2.csv",
    "readme.xls",
    "my_output_log.html",
]
add(
    "data_classify_files.names",
    fn("data_classify_files", f"{PY}.data_classify_files", file_name={"$chr": classify_names}),
    dep=True,
)
add(
    "data_classify_files.empty",
    fn("data_classify_files", f"{PY}.data_classify_files", file_name={"$chr": []}),
)
add(
    "data_classify_files.paths",
    fn(
        "data_classify_files",
        f"{PY}.data_classify_files",
        file_name={
            "$chr": [
                "Informant Survey_Redacted.pdf",
                "slides.pptx",
                "data.csv",
                "x.pdf",
                "notes.txt",
                "clip.mp4",
                "a.pdf",
                "b.txt",
            ]
        },
        file_path={
            "$chr": [
                "ResearchBox 801/Materials/Informant Survey_Redacted.pdf",
                "Output/slides.pptx",
                "Materials/data.csv",
                None,
                "",
                "study/Data/clip.mp4",
                "Scripts/a.pdf",
                "prereg/b.txt",
            ]
        },
    ),
    dep=True,
)
add(
    "data_doc_role.names",
    fn(".data_doc_role", f"{PY}._data_doc_role", file_name={"$chr": classify_names}),
    dep=True,
)
add("data_doc_role.empty", fn(".data_doc_role", f"{PY}._data_doc_role", file_name={"$chr": []}))
add(
    "is_r_package_file.root_with_study",
    fn(
        ".is_r_package_file",
        f"{PY}._is_r_package_file",
        file_path={
            "$chr": [
                "DESCRIPTION",
                "NAMESPACE",
                "R/fit.R",
                "man/fit.Rd",
                "tests/t.R",
                "analysis.R",
                "data/raw.csv",
                "study_data.csv",
                "proj.Rproj",
            ]
        },
    ),
    dep=True,
)
add(
    "is_r_package_file.root_only",
    fn(
        ".is_r_package_file",
        f"{PY}._is_r_package_file",
        file_path={
            "$chr": [
                "DESCRIPTION",
                "NAMESPACE",
                "R/fit.R",
                "README.md",
                "LICENSE",
                "BaBA.Rproj",
                "Flowchart.png",
            ]
        },
    ),
    dep=True,
)
add(
    "is_r_package_file.nested",
    fn(
        ".is_r_package_file",
        f"{PY}._is_r_package_file",
        file_path={
            "$chr": [
                "pkg/DESCRIPTION",
                "pkg/NAMESPACE",
                "pkg/R/a.R",
                "pkg/inst/x.csv",
                "pkg/analysis.R",
                "other/R/b.R",
                "top.R",
                None,
                "",
            ]
        },
    ),
)
add(
    "is_r_package_file.none",
    fn(
        ".is_r_package_file",
        f"{PY}._is_r_package_file",
        file_path={"$chr": ["DESCRIPTION", "R/a.R", "x/NAMESPACE"]},
    ),
)
add(
    "is_r_package_file.backslash",
    fn(
        ".is_r_package_file",
        f"{PY}._is_r_package_file",
        file_path={"$chr": ["pkg\\DESCRIPTION", "pkg\\NAMESPACE", "pkg\\R\\a.R"]},
    ),
)
add(
    "is_r_package_file.empty",
    fn(".is_r_package_file", f"{PY}._is_r_package_file", file_path={"$chr": []}),
)


class _Dumper(yaml.SafeDumper):
    """Quote every string (R's YAML reader would otherwise retype "1.0", "yes"...)."""


_Dumper.add_representer(str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style='"'))


def dump(name: str, area: str, items: list[dict[str, Any]], header: str) -> None:
    text = yaml.dump(
        {"area": area, "cases": items},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    (ROOT / "parity" / "cases" / name).write_text(header + text, encoding="utf-8")


dump(
    "datacheck_files.yaml",
    "datacheck_files",
    cases,
    "# Parity cases for R/data_check_helpers.R part 1 (files). Generated by\n"
    "# tests/datacheck_files/gen_parity_cases.py -- edit that script, not this file.\n",
)
dump(
    "datacheck_files_deps.yaml",
    "datacheck_files_deps",
    deps,
    "# datacheck_files cases that also need other areas' ports (metacheck.fileinfo,\n"
    "# metacheck.datacheck.checks, metacheck.statout). Generated by\n"
    "# tests/datacheck_files/gen_parity_cases.py.\n",
)
print(len(cases), "cases,", len(deps), "dependent cases")
