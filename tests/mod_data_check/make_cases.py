"""Generate ``tests/mod_data_check/scenarios.json`` and ``parity/cases/mod_data_check.yaml``.

Module cases chain data_check onto the repo_check output of a scenario (see
``helpers.py`` / ``dc_helpers.R``): the R side evaluates
``dc_run('<scenario>', ...)`` inside ``base::identity()``, the Python side
``helpers.dc_run()``. Helper cases call the module's internal functions (R:
the module file sourced into an environment, see ``dc_env()``).

    .venv/bin/python tests/mod_data_check/make_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "mod_data_check"
REPOS = HERE / "fixtures" / "repos"


def files_of(d: str) -> list[str]:
    base = REPOS / d
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())


def repo(d: str, url: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"dir": d, "repo_url": url, "files": files_of(d)}
    out.update(extra)
    return out


def remote(name: str, path: str, pid: str = "p1", url: Any = None) -> dict[str, Any]:
    return {
        "paper_id": pid,
        "repo_name": "remote",
        "repo_url": "https://osf.io/remot",
        "file_name": name,
        "file_path": path,
        "file_url": url,
        "file_location": None,
        "file_size": 120,
    }


SCENARIOS: dict[str, Any] = {
    "basic": {"repos": [repo("basic", "https://osf.io/abcde")]},
    "flags": {"repos": [repo("flags", "https://osf.io/flags")]},
    "yellow": {"repos": [repo("yellow", "https://github.com/lab/yellow")]},
    "nolocal": {
        "repos": [repo("basic", "https://osf.io/abcde")],
        "rows": [remote("remote.csv", "data/remote.csv")],
    },
    "nolocal_only": {
        "rows": [
            remote("a.csv", "data/a.csv"),
            remote("b.sav", "data/b.sav"),
            remote("readme.md", "readme.md"),
        ]
    },
    # with download URLs: only run with download = "none" (no network in parity)
    "nolocal_urls": {
        "rows": [
            remote("a.csv", "data/a.csv", url="https://osf.io/download/a"),
            remote("b.sav", "data/b.sav", url="https://osf.io/download/b"),
            remote("readme.md", "readme.md", url="https://osf.io/download/r"),
        ]
    },
    "nodata": {"repos": [repo("nodata", "https://osf.io/nodat")]},
    "none": {},
    "qualtrics": {"repos": [repo("qualtrics", "https://osf.io/qualt")]},
    "careless": {"repos": [repo("careless", "https://osf.io/carel")]},
    "careless_clean": {"repos": [repo("careless_clean", "https://osf.io/clean")]},
    "careless_two": {"repos": [repo("careless_two", "https://osf.io/twosc")]},
    "careless_short": {"repos": [repo("careless_short", "https://osf.io/short")]},
    "spreadsheets": {"repos": [repo("spreadsheets", "https://osf.io/sheet")]},
    "archives": {"copy": True, "repos": [repo("archives", "https://osf.io/archi")]},
    "rdata": {"repos": [repo("rdata", "https://osf.io/rdata")]},
    "trial": {"repos": [repo("trial", "https://osf.io/trial")]},
    "manifest": {"repos": [repo("manifest", "https://osf.io/manif")]},
    "txt": {"repos": [repo("txt", "https://osf.io/txtfi")]},
    "groups": {
        "text": [
            "In Study 1 we measured y.",
            "Study 2 replicated Study 1 with a new sample.",
        ],
        "repos": [repo("groups", "https://osf.io/group")],
    },
    "encoding": {"repos": [repo("encoding", "https://osf.io/encod")]},
    "nontabular": {"repos": [repo("nontabular", "https://osf.io/nonta")]},
    "llm": {"repos": [repo("llm", "https://osf.io/llmrp")]},
    "schema": {"repos": [repo("schema", "https://osf.io/schem")]},
    # remote files (file:// URLs to the fixtures): data_check downloads them
    "download": {"repos": [repo("basic", "https://osf.io/abcde", file_url="local", local=False)]},
    # a repository nothing ever downloads (the cap cases must not find its
    # files in the session's download cache)
    "download_capped": {
        "repos": [repo("basic", "https://osf.io/capped", file_url="local", local=False)]
    },
    "download_mixed": {
        "repos": [
            repo("basic", "https://osf.io/abcde"),
            repo("flags", "https://osf.io/gated", file_url="local", local=False),
        ]
    },
    "download_archives": {
        "repos": [repo("archives", "https://osf.io/archi", file_url="local", local=False)]
    },
    "paperlist": {
        "papers": ["p1", "p2", "p3"],
        "repos": [
            repo("basic", "https://osf.io/abcde", paper_id="p1"),
            repo("flags", "https://osf.io/flags", paper_id="p2"),
        ],
    },
    "samename": {
        "repos": [
            repo("groups/study1", "https://osf.io/stud1", repo_name="study1"),
            repo("groups/study2", "https://osf.io/stud2", repo_name="study2"),
        ]
    },
    "naming": {
        "repos": [repo("basic", "https://osf.io/abcde")],
        "naming_issues": {
            "file_name": ["study.csv", "study.csv", "analysis.R"],
            "rule": ["generic", "case", "generic"],
            "severity": ["info", "info", "info"],
            "detail": [
                "Generic file name",
                "Mixed case",
                "Generic file name; rename it",
            ],
        },
        "gated_repos": {
            "repo_url": ["https://github.com/lab/huge"],
            "repo_type": ["github"],
            "repo_error": ["repository too large to list"],
        },
    },
    "codebook_chain": {
        "repos": [
            repo("flags", "https://osf.io/flags"),
            repo("careless_two", "https://osf.io/twosc"),
        ],
        "codebook": {
            "source_file": ["messy.csv", "messy.csv", "survey.csv", "survey.csv", "survey.csv"],
            "column_name": ["email", "Gender", "panas_1", "rse_2", "participant_id"],
            "label": ["E-mail address", "Gender", "Interested", "Satisfied", "ID"],
            "label_status": ["labelled", "unlabelled", "llm", "labelled", "labelled"],
            "scale": [None, None, "PANAS", "", None],
        },
    },
    "nofilepath": {"repos": [repo("basic", "https://osf.io/abcde")], "drop": ["file_path"]},
    "nourl": {"repos": [repo("basic", "https://osf.io/abcde")], "drop": ["repo_url"]},
    "bibr12": {
        "read": ["tests/fixtures/bibr_v12_full.json"],
        "repos": [repo("basic", "https://osf.io/abcde", paper_id="demo")],
    },
    "psychsci": {
        "read": [
            "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
            "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
        ],
        "repos": [
            repo("basic", "https://osf.io/abcde", paper_id="0956797613520608"),
            repo("careless_two", "https://osf.io/twosc", paper_id="0956797614522816"),
        ],
    },
}

# -- review scenarios (parity/cases/mod_data_check_review.yaml) ------------------------
REVIEW_SCENARIOS: dict[str, Any] = {
    # column types R's readers produce (integer64, POSIXct at midnight, IDate,
    # logical, scientific-notation doubles), a >100-row preview, duplicated and
    # non-ASCII column names, a header-only file
    "review_types": {"repos": [repo("review_types", "https://osf.io/types")]},
    # trial-level files + a manifest (demoted after extraction) + clean data
    "review_mix": {
        "repos": [
            repo("trial", "https://osf.io/trial"),
            repo("manifest", "https://osf.io/manif"),
            repo("basic", "https://osf.io/abcde"),
        ]
    },
    # a paper list where no file is readable tabular data (the empty() return)
    "review_nodata_list": {
        "papers": ["p1", "p2"],
        "repos": [
            repo("nodata", "https://osf.io/nod01", paper_id="p1"),
            repo("nodata", "https://osf.io/nod02", paper_id="p2"),
        ],
    },
    "review_broken_list": {
        "papers": ["p1", "p2"],
        "repos": [
            repo("nodata", "https://osf.io/nod01", paper_id="p1"),
            repo("review_broken", "https://osf.io/brok2", paper_id="p2"),
        ],
    },
    # a one-header-row Qualtrics export: dates typed by the reader, no Duration
    "review_qualtrics": {"repos": [repo("review_qualtrics", "https://osf.io/qual2")]},
    # a careless screen whose identifier column is integer64
    "review_int64": {"repos": [repo("review_int64", "https://osf.io/int64")]},
    # the reader fixtures of tests/datacheck_files (copies): statistics-package
    # and R files, delimited text, spreadsheets
    "review_stats": {"repos": [repo("review_stats", "https://osf.io/stats")]},
    "review_text": {"repos": [repo("review_text", "https://osf.io/textf")]},
    "review_sheets": {"repos": [repo("review_sheets", "https://osf.io/sheet")]},
    # a repeated column name over an IDate and an integer column: each column
    # keeps its own class (data_col_facets() is called by position)
    "review_dupclass": {"repos": [repo("review_dupclass", "https://osf.io/dupcl")]},
    # a paper list whose second paper (listed first) only has an unreadable
    # spreadsheet, the first clean data, the third nothing
    "review_mixed_list": {
        "papers": ["p1", "p2", "p3"],
        "repos": [
            repo("review_broken", "https://osf.io/brok2", paper_id="p2"),
            repo("basic", "https://osf.io/abcde", paper_id="p1"),
        ],
    },
}
SCENARIOS.update(REVIEW_SCENARIOS)

R_HELPERS = "source('tests/mod_data_check/dc_helpers.R', local = TRUE)"
PY_HELPERS = "__import__('tests.mod_data_check.helpers', fromlist=['_'])"
R_ENV = "e <- dc_env()"
PY_H = "__import__('metacheck.modules._data_check', fromlist=['_'])"

IGNORE: list[str] = []


def _r_arg(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, str):
        return repr(v).replace('"', '\\"') if "'" not in v else json.dumps(v)
    if isinstance(v, list):
        if not v:
            return "character(0)"
        return "c(" + ", ".join(_r_arg(x) for x in v) + ")"
    return repr(v)


def _py_arg(v: Any) -> str:
    return repr(v)


def module_case(
    cid: str,
    scenario: str,
    careless: bool = False,
    llm: str | None = None,
    tables: bool | str = False,
    ignore: list[str] | None = None,
    **args: Any,
) -> dict[str, Any]:
    r_args = [repr(scenario)]
    py_args = [repr(scenario)]
    if careless:
        r_args.append("careless = TRUE")
        py_args.append("careless=True")
    if llm:
        r_args.append(f"llm = {llm!r}")
        py_args.append(f"llm={llm!r}")
    for k, v in args.items():
        r_args.append(f"{k} = {_r_arg(v)}")
        py_args.append(f"{k}={_py_arg(v)}")
    fn = "dc_run_tables" if tables else "dc_run"
    if isinstance(tables, str):
        fn = tables
    case: dict[str, Any] = {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_data_check.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{R_HELPERS}; {fn}({', '.join(r_args)})}})",
                    "py": f"{PY_HELPERS}.{fn}({', '.join(py_args)})",
                }
            }
        },
    }
    if IGNORE or ignore:
        case["compare"] = {"ignore": IGNORE + (ignore or [])}
    return case


def helper_case(cid: str, r: str, py: str, compare: dict[str, Any] | None = None) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_data_check.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{R_HELPERS}; {R_ENV}; {r}}})",
                    "py": py,
                }
            }
        },
    }
    if compare:
        case["compare"] = compare
    return case


def cases() -> list[dict[str, Any]]:
    out = [
        # traffic lights / paths through the module
        module_case("data_check.basic", "basic"),
        module_case("data_check.flags_red", "flags"),
        module_case("data_check.yellow", "yellow"),
        module_case("data_check.nolocal_yellow", "nolocal"),
        module_case("data_check.nolocal_only", "nolocal_only"),
        module_case("data_check.nolocal_download_none", "nolocal_urls", download="none"),
        module_case("data_check.nolocal_download_false", "nolocal_urls", download=False),
        module_case("data_check.download_partial", "basic", download="al"),
        module_case("data_check.download_bad", "basic", download="bogus"),
        module_case("data_check.nodata", "nodata"),
        module_case("data_check.none", "none"),
        module_case("data_check.qualtrics", "qualtrics"),
        module_case("data_check.qualtrics_careless", "qualtrics", careless=True),
        module_case("data_check.careless", "careless", careless=True),
        module_case("data_check.careless_missing", "careless"),
        module_case("data_check.careless_clean", "careless_clean", careless=True),
        module_case("data_check.careless_two", "careless_two", careless=True),
        module_case("data_check.careless_short", "careless_short", careless=True),
        module_case("data_check.spreadsheets", "spreadsheets"),
        module_case("data_check.archives", "archives", peek_zips=True),
        module_case("data_check.archives_nopeek", "archives", peek_zips=False),
        module_case("data_check.archives_default", "archives"),
        module_case("data_check.archives_skip_none", "archives", peek_zips=True, skip_types=[]),
        module_case("data_check.rdata", "rdata"),
        module_case("data_check.trial", "trial"),
        module_case("data_check.manifest", "manifest"),
        module_case("data_check.txt", "txt"),
        module_case("data_check.txt_download_none", "txt", download="none"),
        module_case("data_check.groups", "groups"),
        module_case("data_check.encoding", "encoding"),
        module_case("data_check.nontabular", "nontabular"),
        module_case("data_check.llm", "llm", llm="basic"),
        module_case("data_check.llm_errors", "llm", llm="errors"),
        module_case("data_check.llm_groups", "groups", llm="basic"),
        module_case("data_check.llm_samename", "samename", llm="basic"),
        module_case("data_check.llm_schema", "schema", llm="basic"),
        module_case("data_check.schema", "schema"),
        module_case("data_check.codebook_chain", "codebook_chain", careless=True),
        module_case(
            "data_check_tables.codebook_chain", "codebook_chain", careless=True, tables=True
        ),
        module_case("data_check.download", "download"),
        module_case("data_check.download_all", "download", download="all"),
        module_case(
            "data_check.download_all_everything", "download", download="all", skip_types=[]
        ),
        module_case("data_check.download_skip_data", "download", skip_types=["data"]),
        module_case("data_check.download_none", "download", download="none"),
        module_case("data_check.download_oversize", "download_capped", max_file_size=1e-5),
        module_case("data_check.download_gated", "download_capped", max_download_size=1e-5),
        module_case("data_check.download_archives", "download_archives", download="all"),
        module_case(
            "data_check.download_archives_nopeek",
            "download_archives",
            download="all",
            peek_zips=False,
        ),
        module_case("data_check.download_mixed_gated", "download_mixed", max_download_size=1e-5),
        module_case("data_check.download_mixed_oversize", "download_mixed", max_file_size=1e-5),
        module_case("data_check.paperlist", "paperlist"),
        module_case("data_check.samename", "samename"),
        module_case("data_check.naming", "naming"),
        module_case("data_check.nofilepath", "nofilepath"),
        module_case("data_check.nourl", "nourl"),
        module_case("data_check.bibr12", "bibr12"),
        module_case("data_check.psychsci", "psychsci", careless=True),
        # the data of the report tables
        module_case("data_check_tables.basic", "basic", tables=True),
        module_case("data_check_tables.flags", "flags", tables=True),
        module_case("data_check_tables.qualtrics", "qualtrics", tables=True),
        module_case("data_check_tables.careless_two", "careless_two", careless=True, tables=True),
        module_case("data_check_tables.spreadsheets", "spreadsheets", tables=True),
        module_case("data_check_tables.paperlist", "paperlist", tables=True),
        module_case("data_check_tables.groups", "groups", tables=True),
    ]
    # plain module cases (module_run(<repo_check output>, "data_check")); the
    # Python side reads the fixtures by absolute path, R by relative path
    for sc in ("basic", "flags", "none"):
        out.append(
            {
                "id": f"data_check.module.{sc}",
                "module": "data_check",
                "args": {
                    "paper": {
                        "$expr": {
                            "r": f"local({{{R_HELPERS}; dc_prev({sc!r})}})",
                            "py": f"{PY_HELPERS}.dc_prev({sc!r})",
                        }
                    }
                },
                "compare": {"ignore": ["structure.file_location"]},
            }
        )
    # the real pipeline: repo_check lists a local directory (local_path)
    for d in ("basic", "spreadsheets", "groups"):
        out.append(
            {
                "id": f"data_check.local.{d}",
                "r": "base::identity",
                "py": "tests.mod_data_check.helpers.identity",
                "args": {
                    "x": {
                        "$expr": {
                            "r": f"local({{{R_HELPERS}; dc_run_local({d!r})}})",
                            "py": f"{PY_HELPERS}.dc_run_local({d!r})",
                        }
                    }
                },
            }
        )
    # a local zip: repo_check does not peek it (no URL), data_check unpacks the file on
    # disk by default (peek_zips = TRUE since metacheck a16b13f8) and not with peek_zips = FALSE
    # each case copies to its own folder, so that cases running side by side
    # (`parity check --jobs`) do not share one
    for cid, r_arg, py_arg in (
        ("data_check.local.archives", "", ""),
        ("data_check.local.archives_nopeek", ", peek_zips = FALSE", ", peek_zips=False"),
    ):
        label = cid.rsplit(".", 1)[1]
        out.append(
            {
                "id": cid,
                "r": "base::identity",
                "py": "tests.mod_data_check.helpers.identity",
                "args": {
                    "x": {
                        "$expr": {
                            "r": f"local({{{R_HELPERS}; dc_run_local('archives', copy = '{label}'{r_arg})}})",
                            "py": f"{PY_HELPERS}.dc_run_local('archives', copy='{label}'{py_arg})",
                        }
                    }
                },
            }
        )
    rp = "'tests/mod_data_check/fixtures/repos/"
    fx = f"{PY_HELPERS}.REPOS / "
    out += [
        helper_case(
            "repo_tree_rows.mixed",
            "e$repo_tree_rows(c('b/x.csv', 'A/y.R', 'README.md', 'a/z.txt', NA, '', "
            "'b\\\\w.csv', 'b/sub/deep.csv', 'Zeta.txt', 'b/x.csv'))",
            f"{PY_HELPERS}.tree_rows_r(['b/x.csv', 'A/y.R', 'README.md', 'a/z.txt', None, '', "
            "'b\\\\w.csv', 'b/sub/deep.csv', 'Zeta.txt', 'b/x.csv'])",
        ),
        helper_case(
            "repo_tree_rows.empty",
            "e$repo_tree_rows(c(NA, ''))",
            f"{PY_HELPERS}.tree_rows_r([None, ''])",
        ),
        helper_case(
            "repo_tree_block.naming",
            "e$repo_tree_block(data.frame(repo_url = c('r1', 'r1', 'r2', NA, 'r1'), "
            "file_name = c('a.csv', 'README.md', 'x.R', 'y.csv', 'p.png'), "
            "file_path = c('data/a.csv', 'README.md', 'x.R', 'y.csv', 'm/p.png'), "
            "data_type = c('data', 'documentation', 'code', 'data', 'materials'), "
            "doc_role = c(NA, 'readme', NA, NA, NA), group = c('ex1', NA, 'ex2', NA, 'ex1')), "
            "data.frame(file_name = c('a.csv', 'x.R'), detail = c('Generic name', 'Bad')))",
            f"{PY_H}.repo_tree_block(__import__('pandas').DataFrame("
            "{'repo_url': ['r1', 'r1', 'r2', None, 'r1'], "
            "'file_name': ['a.csv', 'README.md', 'x.R', 'y.csv', 'p.png'], "
            "'file_path': ['data/a.csv', 'README.md', 'x.R', 'y.csv', 'm/p.png'], "
            "'data_type': ['data', 'documentation', 'code', 'data', 'materials'], "
            "'doc_role': [None, 'readme', None, None, None], "
            "'group': ['ex1', None, 'ex2', None, 'ex1']}), "
            "__import__('pandas').DataFrame({'file_name': ['a.csv', 'x.R'], "
            "'detail': ['Generic name', 'Bad']}))",
        ),
        helper_case(
            "repo_tree_block.plain",
            "e$repo_tree_block(data.frame(repo_url = 'r', file_name = c('b.csv', 'a.csv')))",
            f"{PY_H}.repo_tree_block(__import__('pandas').DataFrame("
            "{'repo_url': ['r', 'r'], 'file_name': ['b.csv', 'a.csv']}))",
        ),
        helper_case(
            "repo_tree_block.none",
            "e$repo_tree_block(data.frame(repo_url = NA_character_, file_name = 'a.csv'))",
            f"{PY_H}.repo_tree_block(__import__('pandas').DataFrame("
            "{'repo_url': [None], 'file_name': ['a.csv']}))",
        ),
        helper_case(
            "dv_issue_cell.merge",
            "e$.dv_issue_cell(c('Personal info (values)', 'Whitespace', "
            "'Personal info (column name)', 'Mystery check'), c('Values look like emails; "
            "review. More.', \"2 values have 'padding' <here>\", 'The name suggests an email "
            "address that is really far too long to show in the table cell as a whole.', ''))",
            f"{PY_H}.dv_issue_cell(['Personal info (values)', 'Whitespace', "
            "'Personal info (column name)', 'Mystery check'], ['Values look like emails; "
            "review. More.', \"2 values have 'padding' <here>\", 'The name suggests an email "
            "address that is really far too long to show in the table cell as a whole.', ''])",
        ),
        helper_case(
            "dv_careless_block.flag",
            "dc_with_careless(e$.dv_careless_block(data.frame(a = c(3, 1, 2, NA), "
            "b = c(3, 2, 2, 4), c = c(3, 3, 2, 4), d = c(3, 4, 2, 4), e = c(3, 5, 2, 4), "
            "f = c(2, 1, 2, 4)), c('r1', 'r2', 'r3', 'r4'), '1-5', 'panas'))",
            f"{PY_HELPERS}.with_careless({PY_H}.dv_careless_block, "
            "__import__('pandas').DataFrame({'a': [3, 1, 2, None], 'b': [3, 2, 2, 4], "
            "'c': [3, 3, 2, 4], 'd': [3, 4, 2, 4], 'e': [3, 5, 2, 4], 'f': [2, 1, 2, 4]}), "
            "['r1', 'r2', 'r3', 'r4'], '1-5', 'panas')",
        ),
        helper_case(
            "dv_careless_block.none",
            "dc_with_careless(e$.dv_careless_block(data.frame(a = 1:6, b = 6:1, c = 1:6, "
            "d = 6:1, e = 1:6), as.character(1:6), '1-6', 'x'))",
            f"{PY_HELPERS}.with_careless({PY_H}.dv_careless_block, "
            "__import__('pandas').DataFrame({'a': [1, 2, 3, 4, 5, 6], 'b': [6, 5, 4, 3, 2, 1], "
            "'c': [1, 2, 3, 4, 5, 6], 'd': [6, 5, 4, 3, 2, 1], 'e': [1, 2, 3, 4, 5, 6]}), "
            "[str(i) for i in range(1, 7)], '1-6', 'x')",
        ),
        helper_case(
            "dv_careless_by_respondent.multi",
            "e$.dv_careless_by_respondent(data.frame(scale = c('panas (1-5, 10 items)', "
            "'rse (1-5, 6 items)', 'rse (1-5, 6 items)', 'panas (1-5, 10 items)', "
            "'bfi (1-5, 6 items)'), respondent = c('10', '10', '2', '9', 'b'), "
            "longstring = c(8, 6, 6, 10, 5), irv = c(0.4, 0, 0, 0.1, 0.5), "
            "reason = 'straightlining', n_items = c(10L, 6L, 6L, 10L, 6L), "
            "straight_cut = c(8, 5, 5, 8, 5), scale_range = '1-5', "
            "source_file = c('s.csv', 's.csv', 't.csv', 's.csv', 's.csv')))",
            f"{PY_H}.dv_careless_by_respondent(__import__('pandas').DataFrame("
            "{'scale': ['panas (1-5, 10 items)', 'rse (1-5, 6 items)', 'rse (1-5, 6 items)', "
            "'panas (1-5, 10 items)', 'bfi (1-5, 6 items)'], "
            "'respondent': ['10', '10', '2', '9', 'b'], 'longstring': [8.0, 6, 6, 10, 5], "
            "'irv': [0.4, 0, 0, 0.1, 0.5], 'reason': ['straightlining'] * 5, "
            "'n_items': [10, 6, 6, 10, 6], 'straight_cut': [8.0, 5, 5, 8, 5], "
            "'scale_range': ['1-5'] * 5, "
            "'source_file': ['s.csv', 's.csv', 't.csv', 's.csv', 's.csv']}))",
        ),
        helper_case(
            "dv_careless_by_respondent.empty",
            "e$.dv_careless_by_respondent(data.frame())",
            f"{PY_H}.dv_careless_by_respondent(__import__('pandas').DataFrame())",
        ),
        helper_case(
            "dv_scale_n_items",
            "e$.dv_scale_n_items(c('a (1-5, 12 items)', 'b (?, 3 items)'))",
            f"{PY_H}.dv_scale_n_items(['a (1-5, 12 items)', 'b (?, 3 items)'])",
        ),
        helper_case(
            "dv_careless_coverage_text.skipped",
            "e$.dv_careless_coverage_text(1L, 2L)",
            f"{PY_H}.dv_careless_coverage_text(1, 2)",
        ),
        helper_case(
            "dv_careless_coverage_text.all",
            "e$.dv_careless_coverage_text(3L, 0L)",
            f"{PY_H}.dv_careless_coverage_text(3, 0)",
        ),
        helper_case(
            "dv_q_datetime",
            "format(e$.dv_q_datetime(c('2021-05-01 10:00:00', '2021-05-02', "
            "'2021-13-01', 'x', NA, '2021-5-3 7:05:09 extra', '2021-02-29')), "
            "'%Y-%m-%d %H:%M:%S')",
            f"{PY_HELPERS}.q_datetime_fmt(['2021-05-01 10:00:00', '2021-05-02', "
            "'2021-13-01', 'x', None, '2021-5-3 7:05:09 extra', '2021-02-29'])",
        ),
        helper_case(
            "dv_qualtrics_summary.dates",
            "e$.dv_qualtrics_summary(data.frame(StartDate = c('2021-05-01 10:00:00', "
            "'2021-05-01 10:10:00', '2021-05-03 09:00:00'), EndDate = c("
            "'2021-05-01 10:05:00', '2021-05-01 10:11:30', '2021-05-03 11:00:00'), "
            "Status = c('Survey Preview', 'IP Address', 'Spam'), Finished = c(TRUE, FALSE, TRUE), "
            "IPAddress = '1.2.3.4', RecipientEmail = 'a@b.c', check.names = FALSE))",
            f"{PY_H}.dv_qualtrics_summary(__import__('pandas').DataFrame({{"
            "'StartDate': ['2021-05-01 10:00:00', '2021-05-01 10:10:00', '2021-05-03 09:00:00'], "
            "'EndDate': ['2021-05-01 10:05:00', '2021-05-01 10:11:30', '2021-05-03 11:00:00'], "
            "'Status': ['Survey Preview', 'IP Address', 'Spam'], "
            "'Finished': __import__('pandas').array([True, False, True], dtype='boolean'), "
            "'IPAddress': ['1.2.3.4'] * 3, 'RecipientEmail': ['a@b.c'] * 3}))",
        ),
        helper_case(
            "dv_qualtrics_summary.durations",
            "e$.dv_qualtrics_summary(data.frame(`Duration (in seconds)` = c(10, 20, 300, 310, "
            "320, 330, 340, 350, 360, 370, 380, -5, NA), Progress = c(100, 50, rep(100, 11)), "
            "RecordedDate = c('2021-01-02', rep('2021-01-05 12:00:00', 12)), check.names = FALSE))",
            f"{PY_H}.dv_qualtrics_summary(__import__('pandas').DataFrame({{"
            "'Duration (in seconds)': [10, 20, 300, 310, 320, 330, 340, 350, 360, 370, 380, -5, "
            "None], 'Progress': [100, 50] + [100] * 11, "
            "'RecordedDate': ['2021-01-02'] + ['2021-01-05 12:00:00'] * 12}))",
        ),
        helper_case(
            "dv_ods_col_letter",
            "e$.dv_ods_col_letter(c(1L, 26L, 27L, 52L, 703L, 0L))",
            f"{PY_H}.dv_ods_col_letter([1, 26, 27, 52, 703, 0])",
        ),
        helper_case(
            "dv_excel_inspect.messy",
            f"e$.dv_excel_inspect({rp}spreadsheets/data/messy.xlsx')",
            f"{PY_H}.dv_excel_inspect({fx}'spreadsheets/data/messy.xlsx')",
        ),
        helper_case(
            "dv_excel_inspect.broken",
            f"e$.dv_excel_inspect({rp}spreadsheets/data/broken.xlsx')",
            f"{PY_H}.dv_excel_inspect({fx}'spreadsheets/data/broken.xlsx')",
        ),
        helper_case(
            "dv_ods_inspect.ods",
            f"e$.dv_ods_inspect({rp}spreadsheets/data/messy.ods')",
            f"{PY_H}.dv_ods_inspect({fx}'spreadsheets/data/messy.ods')",
        ),
        helper_case(
            "dv_ods_inspect.fods",
            f"e$.dv_ods_inspect({rp}spreadsheets/data/messy.fods')",
            f"{PY_H}.dv_ods_inspect({fx}'spreadsheets/data/messy.fods')",
        ),
        helper_case(
            "dv_ods_inspect.clean",
            f"e$.dv_ods_inspect({rp}spreadsheets/data/clean.ods')",
            f"{PY_H}.dv_ods_inspect({fx}'spreadsheets/data/clean.ods')",
        ),
        helper_case(
            "dv_spreadsheet_offset_header.offset",
            f"e$.dv_spreadsheet_offset_header({rp}spreadsheets/data/offset.xlsx')",
            f"{PY_H}.dv_spreadsheet_offset_header({fx}'spreadsheets/data/offset.xlsx')",
        ),
        helper_case(
            "dv_spreadsheet_offset_header.clean",
            f"e$.dv_spreadsheet_offset_header({rp}spreadsheets/data/clean.xlsx')",
            f"{PY_H}.dv_spreadsheet_offset_header({fx}'spreadsheets/data/clean.xlsx')",
        ),
        helper_case(
            "dv_spreadsheet_offset_header.ods",
            f"e$.dv_spreadsheet_offset_header({rp}spreadsheets/data/messy.ods')",
            f"{PY_H}.dv_spreadsheet_offset_header({fx}'spreadsheets/data/messy.ods')",
        ),
        helper_case(
            "dv_spreadsheet_findings.fixture",
            f"e$.dv_spreadsheet_findings(data.frame(file_name = c('messy.xlsx', 'coding.xlsx', "
            f"'legacy.xls', 'broken.xlsx', 'messy.fods', 'a.csv', 'gone.xlsx'), file_location = "
            f"c({rp}spreadsheets/data/messy.xlsx', {rp}spreadsheets/data/coding.xlsx', "
            f"{rp}spreadsheets/data/legacy.xls', {rp}spreadsheets/data/broken.xlsx', "
            f"{rp}spreadsheets/data/messy.fods', {rp}basic/data/study.csv', 'nope/gone.xlsx'), "
            "tabular_usable = c(TRUE, FALSE, TRUE, TRUE, TRUE, TRUE, TRUE), "
            "non_tabular_reason = c(NA, 'mostly free text', NA, NA, NA, NA, NA)))",
            f"{PY_H}.dv_spreadsheet_findings(__import__('pandas').DataFrame({{"
            "'file_name': ['messy.xlsx', 'coding.xlsx', 'legacy.xls', 'broken.xlsx', "
            "'messy.fods', 'a.csv', 'gone.xlsx'], 'file_location': [str("
            f"{fx}p) for p in ['spreadsheets/data/messy.xlsx', 'spreadsheets/data/coding.xlsx', "
            "'spreadsheets/data/legacy.xls', 'spreadsheets/data/broken.xlsx', "
            "'spreadsheets/data/messy.fods', 'basic/data/study.csv']] + ['nope/gone.xlsx'], "
            "'tabular_usable': [True, False, True, True, True, True, True], "
            "'non_tabular_reason': [None, 'mostly free text', None, None, None, None, None]}))",
        ),
        helper_case(
            "dv_spreadsheet_findings.none",
            "e$.dv_spreadsheet_findings(data.frame(file_name = 'a.csv', file_location = NA_character_))",
            f"{PY_H}.dv_spreadsheet_findings(__import__('pandas').DataFrame("
            "{'file_name': ['a.csv'], 'file_location': [None]}))",
        ),
    ]
    return out


RX = "'tests/mod_data_check/fixtures/review_xlsx/"
PX = f"{PY_HELPERS}.FIXTURES / 'review_xlsx' / "
I64_TYPES = ["previews.types.csv.big"]
I64_INT64 = ["previews.survey.csv.participant"]


def review_cases() -> list[dict[str, Any]]:
    """Cases added by the adversarial review (area ``mod_data_check_review``)."""
    return [
        # parity/r/canonical.R encodes an integer64 column by its raw bits (a
        # double of typeof "double"), so the previews' integer64 columns are
        # not comparable; the module's own use of them (sample_values, the
        # careless respondent ids) is
        module_case("review.types", "review_types", ignore=I64_TYPES),
        module_case("review_tables.types", "review_types", tables=True, ignore=["[3].big"]),
        module_case("review.types_llm", "review_types", llm="basic", ignore=I64_TYPES),
        module_case("review.mix", "review_mix"),
        module_case("review.nodata_list", "review_nodata_list"),
        # dv_report is an R character vector holding a scroll_table() chunk; its
        # prose and table data are compared by review.broken_list.dv_report
        module_case("review.broken_list", "review_broken_list", ignore=["dv_report"]),
        module_case("review.broken_list.dv_report", "review_broken_list", tables="dc_dv_report"),
        module_case("review.qualtrics", "review_qualtrics"),
        module_case("review_tables.qualtrics", "review_qualtrics", tables=True),
        module_case("review.int64", "review_int64", careless=True, ignore=I64_INT64),
        module_case(
            "review_tables.int64",
            "review_int64",
            careless=True,
            tables=True,
            ignore=["[0].participant"],
        ),
        module_case("review.mixed_list", "review_mixed_list"),
        module_case("review.stats", "review_stats"),
        module_case("review_tables.stats", "review_stats", tables=True),
        module_case("review.text", "review_text", ignore=["previews.big_int.csv.big"]),
        module_case("review_tables.text", "review_text", tables=True, ignore=["[0].big"]),
        module_case("review.sheets", "review_sheets"),
        module_case("review.dupclass", "review_dupclass"),
        module_case("review_tables.sheets", "review_sheets", tables=True),
        module_case("review.download_count_cap", "download_capped", max_files_per_repo=1),
        module_case("review.download_true", "basic", download=True),
        module_case("review.archives_download_none", "archives", peek_zips=True, download="none"),
        # hand-built workbooks (fixtures/make_review_xlsx.py): a nameless sheet,
        # a worksheet with no <sheet> entry, cells without a reference, seven
        # merges, rgb/theme/white fills, blank rows and header gaps
        helper_case(
            "review.dv_excel_inspect.noname",
            f"e$.dv_excel_inspect({RX}noname.xlsx')",
            f"{PY_H}.dv_excel_inspect({PX}'noname.xlsx')",
        ),
        helper_case(
            "review.dv_excel_inspect.gaps",
            f"e$.dv_excel_inspect({RX}gaps.xlsx')",
            f"{PY_H}.dv_excel_inspect({PX}'gaps.xlsx')",
        ),
        helper_case(
            "review.dv_ods_inspect.edge",
            f"e$.dv_ods_inspect({RX}edge.fods')",
            f"{PY_H}.dv_ods_inspect({PX}'edge.fods')",
        ),
        helper_case(
            "review.dv_spreadsheet_offset_header.edge",
            f"e$.dv_spreadsheet_offset_header({RX}edge.fods')",
            f"{PY_H}.dv_spreadsheet_offset_header({PX}'edge.fods')",
        ),
        helper_case(
            "review.dv_spreadsheet_findings.handmade",
            "e$.dv_spreadsheet_findings(data.frame(file_name = c('noname.xlsx', 'gaps.xlsx', "
            f"'edge.fods'), file_location = c({RX}noname.xlsx', {RX}gaps.xlsx', {RX}edge.fods')))",
            f"{PY_H}.dv_spreadsheet_findings(__import__('pandas').DataFrame({{"
            "'file_name': ['noname.xlsx', 'gaps.xlsx', 'edge.fods'], "
            f"'file_location': [str({PX}'noname.xlsx'), str({PX}'gaps.xlsx'), "
            f"str({PX}'edge.fods')]}}))",
        ),
        # the tree pads its columns by R's display width (nchar(type = "width"))
        helper_case(
            "review.repo_tree_block.widths",
            "e$repo_tree_block(data.frame(repo_url = 'r', file_name = c('a\\u00adb.csv', "
            "'\\u65e5\\u672c.csv', 'x\\U0001F7F0.txt', 'tab\\there.R', 'e\\u0301.csv', "
            "'\\U0001F600.png'), data_type = c('data', 'data', 'unknown', 'code', 'data', "
            "'materials'), group = c('ex1', 'ex2', NA, 'ex1', 'ex1', NA)))",
            f"{PY_H}.repo_tree_block(__import__('pandas').DataFrame({{'repo_url': ['r'] * 6, "
            "'file_name': ['a\\u00adb.csv', '\\u65e5\\u672c.csv', 'x\\U0001F7F0.txt', "
            "'tab\\there.R', 'e\\u0301.csv', '\\U0001F600.png'], 'data_type': ['data', "
            "'data', 'unknown', 'code', 'data', 'materials'], 'group': ['ex1', 'ex2', None, "
            "'ex1', 'ex1', None]}))",
        ),
        # module_run() of an empty paper and an empty paper list (UPSTREAM_ISSUES
        # U79: R stops in repo_check)
        *(
            {
                "id": f"data_check.{suffix}",
                "module": "data_check",
                "args": {"paper": {"$expr": {"r": r, "py": py}}},
            }
            for suffix, r, py in (
                ("empty_paper", "paper()", "pc.paper()"),
                ("empty_paperlist", "paperlist()", "pc.PaperList([])"),
            )
        ),
    ]


def main() -> None:
    (HERE / "scenarios.json").write_text(
        json.dumps(SCENARIOS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    review_header = (
        "# data_check review cases (inst/modules/data_check.R), generated by\n"
        "# tests/mod_data_check/make_cases.py (review_cases()) -- edit that script.\n"
    )
    review_body = yaml.safe_dump(
        {"area": "mod_data_check_review", "cases": review_cases()},
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_style='"',
    )
    (ROOT / "parity" / "cases" / "mod_data_check_review.yaml").write_text(
        review_header + review_body, encoding="utf-8"
    )
    header = (
        "# data_check parity cases (inst/modules/data_check.R), generated by\n"
        "# tests/mod_data_check/make_cases.py -- edit that script, not this file.\n"
        "# Module cases chain data_check onto the repo_check output of a scenario\n"
        "# (tests/mod_data_check/dc_helpers.R / helpers.py); helper cases call the\n"
        "# module's internal functions (R: the module sourced into an environment).\n"
    )
    body = yaml.safe_dump(
        {"area": "mod_data_check", "cases": cases()},
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_style='"',
    )
    (ROOT / "parity" / "cases" / "mod_data_check.yaml").write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
