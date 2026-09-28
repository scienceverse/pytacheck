"""Generate the reproducibility_check review parity cases.

Writes ``parity/cases/mod_repro_review.yaml`` (area ``mod_repro_review``):

* ``tables.<scenario>``: the data frames of the report's table chunks
  (``tests/mod_repro/rv_helpers.R`` / ``rv_helpers.py``), which the regular
  cases' prose comparison skips;
* ``review.<scenario>``: whole-module cases on the extra scenarios this
  review added to ``tests/mod_repro/fixtures/review_scenarios.json``.

Run from the repository root::

    .venv/bin/python tests/mod_repro/make_review_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

EXEC = "execute = TRUE, timeout = 60, keep_sandbox = TRUE"
EXEC_PY = "execute=True, timeout=60, keep_sandbox=True"
EXEC_IGNORE: list[str] = []

U = "upstream/metacheck/tests/testthat/fixtures/repro/"
RV = "tests/mod_repro/fixtures/review/"
OUT = "tests/mod_repro/fixtures/outputs/"
CODE_COLS = ("file_name", "file_location", "file_url", "language", "packages", "parse_error")


def code(*rows: dict[str, Any]) -> dict[str, list[Any]]:
    """A code_check ``table`` (column-oriented) from row dicts."""
    defaults = {"file_url": None, "language": "R", "packages": "", "parse_error": False}
    out: dict[str, list[Any]] = {c: [] for c in CODE_COLS}
    for r in rows:
        for c in CODE_COLS:
            out[c].append(r.get(c, defaults.get(c)))
    return out


def r(name: str, loc: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"file_name": name, "file_location": U + name if loc is None else loc, **kw}


def nonr(name: str, lang: str) -> dict[str, Any]:
    return {"file_name": name, "file_location": None, "language": lang}


DATA = {"file_name": ["data.csv"], "file_location": [U + "data.csv"]}
PLAN = {"file_name": ["data.csv"], "target_path": ["data.csv"]}

# Extra scenarios for branches the regular cases do not reach.
REVIEW: dict[str, dict[str, Any]] = {
    # demo paper text matched against a JASP file's tables (match_reported_output)
    "rv_demo_jasp": {
        "paper": "demo",
        "code": code(r("ok.R")),
        "structure": {
            "file_name": ["data.csv", "sample.jasp"],
            "file_location": [U + "data.csv", OUT + "sample.jasp"],
        },
        "plan": PLAN,
    },
    # a paper list with self-reproducible output and no code (empty() + matching)
    "rv_psychsci_omv": {
        "paper": "psychsci",
        "code": code(),
        "structure": {"file_name": ["sample.omv"], "file_location": [OUT + "sample.omv"]},
        "plan": PLAN,
    },
    # no code rows at all, non-R code and MATLAB data only in the structure
    "rv_py_only_structure": {
        "code": code(),
        "structure": {
            "file_name": ["script.py", "helper.PY", "data.mat"],
            "file_location": [None, None, None],
        },
        "plan": PLAN,
    },
    # SPSS syntax with an .spv (unreadable) and no .sav: no SPSS block, light na
    "rv_spss_spv": {
        "code": code(nonr("syntax.sps", "SPSS")),
        "structure": {"file_name": ["syntax.sps", "output.spv"], "file_location": [None, None]},
        "plan": PLAN,
    },
    # self-reproducible output alongside SAS/Stata/Python code
    "rv_jasp_nonr": {
        "code": code(
            nonr("model.sas", "SAS"), nonr("analysis.do", "Stata"), nonr("run.py", "Python")
        ),
        "structure": {
            "file_name": ["sample.jasp", "model.sas", "analysis.do", "run.py"],
            "file_location": [OUT + "sample.jasp", None, None, None],
        },
        "plan": PLAN,
    },
    # code rows with no location and no URL (unreadable), same name twice
    "rv_unreadable": {
        "code": code(
            {"file_name": "gone.R", "file_location": None},
            {"file_name": "gone.R", "file_location": None},
            r("ok.R"),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    # a structure table with no file_location column (SPSS data + syntax)
    "rv_no_loc_col": {
        "code": code(r("ok.R")),
        "structure": {"file_name": ["data.csv", "data.sav", "analysis.sps"]},
        "plan": PLAN,
    },
    # code_check packages the text does not declare: base and CRAN fallbacks
    "rv_base_pkgs": {
        "code": code(r("ok.R", packages="zzzpkg, stats, tidyr, utils")),
        "structure": DATA,
        "plan": PLAN,
    },
    # a code table with a language column but no file_name column: no R rows
    "rv_no_file_name_col": {
        "code": {"language": ["R", "Python"], "file_location": [U + "ok.R", None]},
        "structure": DATA,
        "plan": PLAN,
    },
    # a Quarto document (purled like R Markdown)
    "rv_qmd": {
        "code": code(r("analysis.qmd", RV + "analysis.qmd")),
        "structure": DATA,
        "plan": PLAN,
    },
    # a latin-1 encoded script with CRLF line endings (code_read's charset detection)
    "rv_latin1": {
        "code": code(r("latin1_crlf.R", RV + "latin1_crlf.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_rv_latin1": {
        "code": code(r("latin1_crlf.R", RV + "latin1_crlf.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    # execution: a variable two files define, an unknown function, a
    # non-literal setwd(), stderr-only and silent scripts, a warning
    "exec_rv_definers": {
        "code": code(
            r("user.R", RV + "user.R"),
            r("def_a.R", RV + "def_a.R"),
            r("def_b.R", RV + "def_b.R"),
            r("fn_user.R", RV + "fn_user.R"),
            r("setwd_var.R", RV + "setwd_var.R"),
            r("quiet.R", RV + "quiet.R"),
            r("msg_only.R", RV + "msg_only.R"),
            r("warn.R", RV + "warn.R"),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    # execution: a re-run that both re-orders and injects library()
    "exec_rv_both": {
        "code": code(
            r("undef_user.R", "tests/mod_repro/fixtures/scripts/undef_user.R"),
            r("undef_definer.R", "tests/mod_repro/fixtures/scripts/undef_definer.R"),
            r("needs_stringr.R", "tests/mod_repro/fixtures/scripts/needs_stringr.R"),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    # execution with installs (mocked): base/CRAN fallback packages
    "exec_rv_base_pkgs": {
        "code": code(r("ok.R", packages="zzzpkg, stats, tidyr, utils")),
        "structure": DATA,
        "plan": PLAN,
    },
}

CASES: list[dict[str, Any]] = []


def add_module(
    scenario: str,
    r_args: str = "",
    py_args: str = "",
    mocked: bool = False,
    compare: dict[str, Any] | None = None,
) -> None:
    fn = "rc_run_mocked" if mocked else "rc_run"
    r_call = f"{fn}('{scenario}'" + (f", {r_args}" if r_args else "") + ")"
    py_call = f"{fn}('{scenario}'" + (f", {py_args}" if py_args else "") + ")"
    case: dict[str, Any] = {
        "id": f"review.{scenario}",
        "r": "identity",
        "py": "tests.mod_repro.helpers.identity",
        "args": {
            "x": expr(r_call, py_call, "tests/mod_repro/rc_helpers.R", "tests.mod_repro.helpers")
        },
    }
    if compare:
        case["compare"] = compare
    CASES.append(case)


def expr(r_code: str, py_code: str, r_file: str, py_mod: str) -> dict[str, Any]:
    return {
        "$expr": {
            "r": f"local({{source('{r_file}', local = TRUE); {r_code}}})",
            "py": f"__import__('{py_mod}', fromlist=['_']).{py_code}",
        }
    }


def add_tables(scenario: str, r_args: str = "", py_args: str = "", mocked: bool = False) -> None:
    fn = "rv_run_mocked" if mocked else "rv_run"
    r_call = f"{fn}('{scenario}'" + (f", {r_args}" if r_args else "") + ")"
    py_call = f"{fn}('{scenario}'" + (f", {py_args}" if py_args else "") + ")"
    suffix = "" if not r_args else "_" + ("exec" if "execute" in r_args else "args")
    CASES.append(
        {
            "id": f"tables.{scenario}{suffix}",
            "r": "identity",
            "py": "tests.mod_repro.helpers.identity",
            "args": {
                "x": expr(
                    r_call, py_call, "tests/mod_repro/rv_helpers.R", "tests.mod_repro.rv_helpers"
                )
            },
        }
    )


def main() -> None:
    (HERE / "fixtures" / "review_scenarios.json").write_text(
        json.dumps({"scenarios": REVIEW}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    scenarios = json.loads((HERE / "fixtures" / "scenarios.json").read_text(encoding="utf-8"))
    for name in scenarios["scenarios"]:
        if name.startswith("exec_"):
            continue
        if name == "project":
            add_tables(
                name,
                "local_path = 'tests/mod_repro/fixtures/project', local_only = TRUE",
                "local_path='tests/mod_repro/fixtures/project', local_only=True",
            )
            continue
        add_tables(name)
    for name in scenarios["scenarios"]:
        if not name.startswith("exec_") or name == "exec_timeout":
            continue
        if name == "exec_install":
            add_tables(
                name, f"{EXEC}, install_missing = TRUE", f"{EXEC_PY}, install_missing=True", True
            )
        elif name == "exec_docker":
            add_tables(
                name,
                f'{EXEC}, sandbox = "docker", install_missing = TRUE',
                f'{EXEC_PY}, sandbox="docker", install_missing=True',
                True,
            )
        else:
            add_tables(name, EXEC, EXEC_PY)

    mocked = {"exec_rv_base_pkgs"}
    for name in REVIEW:
        if name.startswith("exec_"):
            r_extra = ", install_missing = TRUE" if name in mocked else ""
            py_extra = ", install_missing=True" if name in mocked else ""
            add_module(
                name, EXEC + r_extra, EXEC_PY + py_extra, name in mocked, {"ignore": EXEC_IGNORE}
            )
            add_tables(name, EXEC + r_extra, EXEC_PY + py_extra, name in mocked)
        else:
            add_module(name)
            add_tables(name)

    # a metacheck-written capture_module_tables() .rds as tables_dir
    CASES.append(
        {
            "id": "review.saved_rds",
            "r": "identity",
            "py": "tests.mod_repro.helpers.identity",
            "args": {
                "x": expr(
                    "rv_run_saved('pipeline')",
                    "rv_run_saved('pipeline')",
                    "tests/mod_repro/rv_helpers.R",
                    "tests.mod_repro.rv_helpers",
                )
            },
        }
    )

    # a real piped chain (the upstream outputs come from get_prev_outputs())
    for cid, r_args, py_args in [
        ("review.chain", "", ""),
        ("review.chain_exec", EXEC, EXEC_PY),
        ("review.chain_proc", 'sandbox = "proc"', 'sandbox="proc"'),
    ]:
        CASES.append(
            {
                "id": cid,
                "r": "identity",
                "py": "tests.mod_repro.helpers.identity",
                "args": {
                    "x": expr(
                        f"rv_chain({r_args})",
                        f"rv_chain({py_args})",
                        "tests/mod_repro/rv_helpers.R",
                        "tests.mod_repro.rv_helpers",
                    )
                },
            }
        )

    class Quoted(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Quoted.add_representer(str, str_rep)
    header = (
        "# reproducibility_check review parity cases, generated by\n"
        "# tests/mod_repro/make_review_cases.py -- edit that script, not this file.\n"
        "# tables.*: the report's table chunks (skipped by the prose comparison).\n"
    )
    body = yaml.dump(
        {"area": "mod_repro_review", "cases": CASES},
        Dumper=Quoted,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
    )
    (ROOT / "parity" / "cases" / "mod_repro_review.yaml").write_text(
        header + body, encoding="utf-8"
    )
    print(f"{len(CASES)} cases")


if __name__ == "__main__":
    main()
