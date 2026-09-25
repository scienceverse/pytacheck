"""Generate the reproducibility_check parity cases.

Writes ``tests/mod_repro/fixtures/scenarios.json`` (the fake upstream-module
chains, read by both twins: ``tests/mod_repro/rc_helpers.R`` and
``tests/mod_repro/helpers.py``) and ``parity/cases/mod_repro.yaml``.

Run from the repository root::

    .venv/bin/python tests/mod_repro/make_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

U = "upstream/metacheck/tests/testthat/fixtures/repro/"
S = "tests/mod_repro/fixtures/scripts/"
O = "tests/mod_repro/fixtures/outputs/"


def code(*rows: dict[str, Any], columns: tuple[str, ...] | None = None) -> dict[str, list[Any]]:
    """A code_check ``table`` (column-oriented) from row dicts."""
    cols = columns or ("file_name", "file_location", "file_url", "language", "packages", "parse_error")
    defaults = {"file_url": None, "language": "R", "packages": "", "parse_error": False}
    out: dict[str, list[Any]] = {c: [] for c in cols}
    for r in rows:
        for c in cols:
            out[c].append(r.get(c, defaults.get(c)))
    return out


def r(name: str, loc: str | None = None, **kw: Any) -> dict[str, Any]:
    """A code row: *name* with location *loc* (default: the upstream repro fixture)."""
    return {"file_name": name, "file_location": U + name if loc is None else loc, **kw}


def table(**cols: list[Any]) -> dict[str, list[Any]]:
    return dict(cols)


DATA = table(file_name=["data.csv"], file_location=[U + "data.csv"])
PLAN = table(file_name=["data.csv"], target_path=["data.csv"])
EMPTY_CODE = code()

SCENARIOS: dict[str, dict[str, Any]] = {
    # -- no code / fallbacks ------------------------------------------------------
    "no_code": {"text": ["no code here"], "real": True},
    # the whole real chain (data_check -> psychds_check -> code_check) on a local project
    "project": {"real": True},
    "ok_noplan": {
        "code": code(r("ok.R")),
        "structure": table(file_name=["ok.R", "data.csv"], file_location=[U + "ok.R", U + "data.csv"]),
    },
    # -- static analysis ----------------------------------------------------------
    "ok_plan": {"code": code(r("ok.R")), "structure": DATA, "plan": PLAN},
    "pipeline": {
        "code": code(r("ok.R"), r("writes_then_reads.R"), r("reads_written.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "missing_input": {
        "code": code(r("missing_input.R")),
        "structure": table(file_name=["missing_input.R"], file_location=[U + "missing_input.R"]),
        "plan": table(
            file_name=["some_other_file.csv"], target_path=["study-ex1/data/some_other_file.csv"]
        ),
    },
    "missing_mixed": {
        "code": code(r("missing_mixed.R", S + "missing_mixed.R")),
        "structure": table(
            file_name=["listed.csv", "results.csv"], file_location=[None, U + "data.csv"]
        ),
        "plan": table(file_name=["results.csv"], target_path=["data/results.csv"]),
    },
    "parse_error": {
        "code": code(r("parse_error.R", S + "parse_error.R", parse_error=True), r("ok.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "sniffed": {
        "code": code(
            r("ok.R"),
            r("json_dump.R", S + "json_dump.R", parse_error=True),
            r("jags_model.R", S + "jags_model.R", parse_error=True),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    "cycle": {
        "code": code(r("cycle_a.R", S + "cycle_a.R"), r("cycle_b.R", S + "cycle_b.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "ambiguous_order": {
        "code": code(r("ok.R"), r("undefined_var.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "fuzzy_source": {
        "code": code(r("02_analysis.R", S + "02_analysis.R"), r("01_clean.R", S + "01_clean.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "ambiguous_path": {
        "code": code(r("amb_path.R", S + "amb_path.R")),
        "structure": table(file_name=["amb_path.R"], file_location=[S + "amb_path.R"]),
        "plan": table(
            file_name=["data.csv", "data.csv"],
            target_path=["study-a/data/data.csv", "study-b/data/data.csv"],
        ),
    },
    "call_paths": {
        "code": code(r("sprintf_paths.R", S + "sprintf_paths.R")),
        "structure": DATA,
        "plan": table(file_name=["data.csv"], target_path=["data/data.csv"]),
    },
    "rmd": {
        "code": code(r("analysis.Rmd", S + "analysis.Rmd")),
        "structure": DATA,
        "plan": PLAN,
    },
    "renv_only": {
        "code": code(r("renv/activate.R", S + "renv/activate.R")),
        "structure": table(
            file_name=["renv/activate.R", "analysis.py", "data.mat"],
            file_location=[S + "renv/activate.R", None, None],
        ),
        "plan": PLAN,
    },
    "duplicates": {
        "code": code(r("ok.R"), r("ok.R"), r("errors.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "mirrors_differ": {
        "code": code(
            r("analysis.R", S + "mirror_a/analysis.R"),
            r("analysis.R", S + "mirror_b/analysis.R"),
            r("analysis.R", S + "mirror_a/analysis.R"),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    "deps": {
        "code": code(r("deps.R", S + "deps.R", packages="dplyr, ggplot2, lme4, utils")),
        "structure": DATA,
        "plan": PLAN,
    },
    "no_language": {
        "code": code(r("ok.R"), columns=("file_name", "file_location")),
        "structure": DATA,
        "plan": PLAN,
    },
    "url_fallback": {
        "code": code({"file_name": "url_only.R", "file_location": None, "file_url": S + "url_only.R"}),
        "structure": DATA,
        "plan": PLAN,
    },
    "structure_lookup": {
        "code": code(
            {"file_name": "ok_nested.R", "file_location": None},
            {"file_name": "ok.R", "file_location": None},
        ),
        "structure": table(
            file_name=["code/ok_nested.R", "ok.R", "data.csv"],
            file_location=[S + "code/ok_nested.R", U + "ok.R", U + "data.csv"],
        ),
        "plan": PLAN,
    },
    # -- SPSS / Stata / other languages -------------------------------------------
    "stata_no_data": {
        "code": code({"file_name": "analysis.do", "file_location": None, "language": "Stata"}),
        "structure": table(file_name=["analysis.do"], file_location=[None]),
        "plan": PLAN,
    },
    "spss_no_syntax": {
        "structure": table(file_name=["data.sav"], file_location=[None]),
        "plan": PLAN,
    },
    "spss_with_syntax": {
        "code": EMPTY_CODE,
        "structure": table(file_name=["data.sav", "syntax.sps"], file_location=[None, None]),
        "plan": PLAN,
    },
    "spss_selfcontained": {
        "text": ["The effect was significant, t(67) = 3.75, p < .001."],
        "code": EMPTY_CODE,
        "structure": table(file_name=["data.sav", "sample.omv"], file_location=[None, O + "sample.omv"]),
        "plan": PLAN,
    },
    "stata_red": {
        "code": EMPTY_CODE,
        "structure": table(file_name=["data.dta"], file_location=[None]),
        "plan": PLAN,
    },
    "stata_with_do": {
        "code": code({"file_name": "analysis.do", "file_location": None, "language": "Stata"}),
        "structure": table(file_name=["data.dta", "analysis.do"], file_location=[None, None]),
        "plan": PLAN,
    },
    "nonr_only": {
        "code": code(
            {"file_name": "model.sas", "file_location": None, "language": "SAS"},
            {"file_name": "analysis.m", "file_location": None, "language": "MATLAB"},
            {"file_name": "script.py", "file_location": None, "language": "Python"},
            {"file_name": "syntax.sps", "file_location": None, "language": "SPSS"},
            {"file_name": "helper.py", "file_location": None, "language": "Python"},
        ),
        "structure": table(
            file_name=["model.sas", "analysis.m", "script.py", "syntax.sps", "helper.py", "data.mat"],
            file_location=[None] * 6,
        ),
        "plan": PLAN,
    },
    "nonr_with_r": {
        "code": code(
            r("ok.R"),
            {"file_name": "model.sas", "file_location": None, "language": "SAS"},
            {"file_name": "analysis.m", "file_location": None, "language": "MATLAB"},
            {"file_name": "run.jl", "file_location": None, "language": "Julia"},
        ),
        "structure": table(
            file_name=["data.csv", "model.sas", "analysis.m", "run.jl"],
            file_location=[U + "data.csv", None, None, None],
        ),
        "plan": PLAN,
    },
    # -- self-reproducible output ---------------------------------------------------
    "jasp_only": {
        "text": ["The effect was significant, t(67) = 3.75, p < .001."],
        "code": EMPTY_CODE,
        "structure": table(
            file_name=["sample.jasp", "sample.omv"], file_location=[O + "sample.jasp", O + "sample.omv"]
        ),
        "plan": PLAN,
    },
    "jasp_with_r": {
        "text": ["Participants improved, t(47.2) = 2.81, p = .007."],
        "code": code(r("ok.R")),
        "structure": table(
            file_name=["data.csv", "sample.jasp", "notebook_r.ipynb"],
            file_location=[U + "data.csv", O + "sample.jasp", O + "notebook_r.ipynb"],
        ),
        "plan": PLAN,
    },
    "smcl_mplus": {
        "code": EMPTY_CODE,
        "structure": table(
            file_name=["analysis.smcl", "twolevel.out", "compiler.out"],
            file_location=[O + "analysis.smcl", O + "twolevel.out", O + "compiler.out"],
        ),
        "plan": PLAN,
    },
    "spv_notebook_py": {
        "text": ["Massed practice helped, t(48) = 3.41, p = .001."],
        "code": code(r("ok.R")),
        "structure": table(
            file_name=["data.csv", "modern.spv", "notebook_python.ipynb"],
            file_location=[U + "data.csv", O + "modern.spv", O + "notebook_python.ipynb"],
        ),
        "plan": PLAN,
    },
    # -- papers ---------------------------------------------------------------------
    "demo": {"paper": "demo", "code": code(r("ok.R")), "structure": DATA, "plan": PLAN},
    "psychsci": {
        "paper": "psychsci",
        "code": code(r("ok.R"), r("errors.R")) | {"paper_id": ["0956797613520608", "0956797614522816"]},
        "structure": DATA,
        "plan": PLAN,
    },
    # a native bibr export schema 12.x paper (its reported tests matched against output)
    "bibr12": {
        "paper": "bibr12",
        "code": code(r("ok.R")),
        "structure": table(
            file_name=["data.csv", "sample.omv", "notebook_r.ipynb"],
            file_location=[U + "data.csv", O + "sample.omv", O + "notebook_r.ipynb"],
        ),
        "plan": PLAN,
    },
    "bibr12_nocode": {
        "paper": "bibr12",
        "code": EMPTY_CODE,
        "structure": table(file_name=["data.sav"], file_location=[None]),
        "plan": PLAN,
    },
    # -- execution --------------------------------------------------------------------
    "exec_ok": {
        "text": ["t(4.96) = -4.04, p = .010."],
        "code": code(r("ok.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_errors": {"code": code(r("errors.R")), "structure": DATA, "plan": PLAN},
    "exec_setwd": {"code": code(r("bad_setwd.R")), "structure": DATA, "plan": PLAN},
    "exec_undefined": {
        "code": code(r("undef_user.R", S + "undef_user.R"), r("undef_definer.R", S + "undef_definer.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_library": {
        "code": code(r("needs_stringr.R", S + "needs_stringr.R")),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_mixed": {
        "code": code(
            r("writes_then_reads.R"),
            r("reads_written.R"),
            r("missing_input.R"),
            r("parse_error.R", S + "parse_error.R", parse_error=True),
            r("json_dump.R", S + "json_dump.R", parse_error=True),
        ),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_font": {"code": code(r("font.R", S + "font.R")), "structure": DATA, "plan": PLAN},
    "exec_model_object": {"code": code(r("model_object.R")), "structure": DATA, "plan": PLAN},
    "exec_timeout": {"code": code(r("sleep.R", S + "sleep.R")), "structure": DATA, "plan": PLAN},
    "exec_install": {
        "code": code(r("needs_missing_pkg.R", S + "needs_missing_pkg.R", packages="notapkg123")),
        "structure": DATA,
        "plan": PLAN,
    },
    "exec_docker": {
        "code": code(r("ok.R"), r("needs_missing_pkg.R", S + "needs_missing_pkg.R")),
        "structure": DATA,
        "plan": PLAN,
        "version_pin": ["4.1.2"],
    },
    "exec_chatty": {"code": code(r("chatty.R", S + "chatty.R")), "structure": DATA, "plan": PLAN},
    "exec_jasp": {
        "code": code(r("ok.R")),
        "structure": table(
            file_name=["data.csv", "sample.jasp"], file_location=[U + "data.csv", O + "sample.jasp"]
        ),
        "plan": PLAN,
    },
}

EXEC_IGNORE = ["run_results.elapsed"]

# (case id, scenario, R args, Python args, extra case keys)
CASES: list[tuple[str, str, str, str, dict[str, Any]]] = []


def add(case_id: str, scenario: str, r_args: str = "", py_args: str = "", **extra: Any) -> None:
    CASES.append((case_id, scenario, r_args, py_args, extra))


for name in SCENARIOS:
    if name.startswith("exec_") or name == "project":
        continue
    add(f"reproducibility_check.{name}", name)

# keep_sandbox on a paper with extracted output: the sandbox is surfaced (scrubbed)
add("reproducibility_check.jasp_only_keep_sandbox", "jasp_only", "keep_sandbox = TRUE", "keep_sandbox=True")
add(
    "reproducibility_check.jasp_with_r_keep_sandbox",
    "jasp_with_r",
    "keep_sandbox = TRUE",
    "keep_sandbox=True",
)
PROJECT = "local_path = 'tests/mod_repro/fixtures/project', local_only = TRUE"
PROJECT_PY = "local_path='tests/mod_repro/fixtures/project', local_only=True"
add("reproducibility_check.project", "project", PROJECT, PROJECT_PY)
add(
    "reproducibility_check.project_exec",
    "project",
    PROJECT + ", execute = TRUE, timeout = 60, keep_sandbox = TRUE",
    PROJECT_PY + ", execute=True, timeout=60, keep_sandbox=True",
    compare={"ignore": ["run_results.elapsed"]},
)
# a prior build's saved module tables (tables_dir) instead of a chain
add("reproducibility_check.tables_dir", "pipeline", "tables = TRUE", "tables=True")
add("reproducibility_check.tables_dir_missing", "ok_plan", "tables = 'none'", "tables='none'")

EXEC = "execute = TRUE, timeout = 60, keep_sandbox = TRUE"
EXEC_PY = "execute=True, timeout=60, keep_sandbox=True"
MOCKED = {
    "exec_install": ("install_missing = TRUE", "install_missing=True"),
    "exec_docker": ('sandbox = "docker", install_missing = TRUE', 'sandbox="docker", install_missing=True'),
}
for name in SCENARIOS:
    if not name.startswith("exec_"):
        continue
    if name in MOCKED:
        r_extra, py_extra = MOCKED[name]
        add(
            f"reproducibility_check.{name}",
            name,
            f"{EXEC}, {r_extra}",
            f"{EXEC_PY}, {py_extra}",
            compare={"ignore": EXEC_IGNORE},
            mocked=True,
        )
        continue
    if name == "exec_timeout":
        add(
            f"reproducibility_check.{name}",
            name,
            "execute = TRUE, timeout = 2, keep_sandbox = TRUE",
            "execute=True, timeout=2, keep_sandbox=True",
            compare={"ignore": [*EXEC_IGNORE, "run_results.stdout", "run_results.stderr"]},
        )
        continue
    add(f"reproducibility_check.{name}", name, EXEC, EXEC_PY, compare={"ignore": EXEC_IGNORE})
# execution without keeping the sandbox: the throwaway root is removed (element absent)
add(
    "reproducibility_check.exec_ok_no_keep",
    "exec_ok",
    "execute = TRUE, timeout = 60",
    "execute=True, timeout=60",
    compare={"ignore": [*EXEC_IGNORE, "run_results.script_lines"]},
)


# `module:` cases (module_run(<chain>, "reproducibility_check") run by the
# parity harness itself), on scenarios whose output holds no file paths
MODULE_CASES = ["ok_plan", "pipeline", "missing_input", "spss_no_syntax", "jasp_only", "nonr_only"]


def main() -> None:
    fixtures = HERE / "fixtures"
    (fixtures / "scripts" / "sleep.R").write_text('Sys.sleep(20)\ncat("done\\n")\n')
    (fixtures / "scripts" / "chatty.R").write_text('for (i in 1:5010) cat("line", i, "\\n")\n')
    (fixtures / "scenarios.json").write_text(
        json.dumps({"scenarios": SCENARIOS}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    cases = []
    for case_id, scenario, r_args, py_args, extra in CASES:
        extra = dict(extra)
        fn = "rc_run_mocked" if extra.pop("mocked", False) else "rc_run"
        r_call = f"{fn}('{scenario}'" + (f", {r_args}" if r_args else "") + ")"
        py_call = f"{fn}('{scenario}'" + (f", {py_args}" if py_args else "") + ")"
        case: dict[str, Any] = {
            "id": case_id,
            "r": "identity",
            "py": "tests.mod_repro.helpers.identity",
            "args": {
                "x": {
                    "$expr": {
                        "r": "local({source('tests/mod_repro/rc_helpers.R', local = TRUE); "
                        f"{r_call}}})",
                        "py": "__import__('tests.mod_repro.helpers', fromlist=['_'])." + py_call,
                    }
                }
            },
        }
        case.update(extra)
        cases.append(case)
    for scenario in MODULE_CASES:
        cases.append(
            {
                "id": f"module.{scenario}",
                "module": "reproducibility_check",
                "args": {
                    "paper": {
                        "$expr": {
                            "r": "local({source('tests/mod_repro/rc_helpers.R', local = TRUE); "
                            f"rc_input('{scenario}', absolute = TRUE)}})",
                            "py": "__import__('tests.mod_repro.helpers', fromlist=['_'])."
                            f"rc_input('{scenario}', absolute=True)",
                        }
                    }
                },
            }
        )
    header = (
        "# reproducibility_check parity cases (inst/modules/reproducibility_check.R), generated\n"
        "# by tests/mod_repro/make_cases.py -- edit that script, not this file. Each case runs\n"
        "# the module on a fake code_check/data_check/psychds_check chain from\n"
        "# tests/mod_repro/fixtures/scenarios.json (R: tests/mod_repro/rc_helpers.R, Python:\n"
        "# tests/mod_repro/helpers.py); execute cases keep and then scrub the sandbox path.\n"
    )

    class Quoted(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Quoted.add_representer(str, str_rep)
    body = yaml.dump(
        {"area": "mod_repro", "cases": cases},
        Dumper=Quoted,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
    )
    (ROOT / "parity" / "cases" / "mod_repro.yaml").write_text(header + body, encoding="utf-8")
    print(f"{len(cases)} cases")


if __name__ == "__main__":
    main()
