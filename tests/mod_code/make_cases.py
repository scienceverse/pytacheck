"""Write parity/cases/mod_code.yaml (the code_check parity cases).

Module cases chain code_check onto the repo_check (or data_check) output of a
scenario in tests/mod_code/scenarios.json (see tests/mod_code/helpers.py and
cc_helpers.R); ``report_tables`` cases compare the data of the report's table
blocks, which the module-output comparison skips. Run from the repository root::

    .venv/bin/python tests/mod_code/make_cases.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_code.yaml"

_PY = "__import__('tests.mod_code.helpers', fromlist=['_'])"
_R = "local({source('tests/mod_code/cc_helpers.R', local = TRUE); %s})"

# (case id suffix, scenario, module arguments)
MODULE_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("code_files", "code_files", {}),
    ("code_files_no_download", "code_files", {"download": False}),
    ("parse_errors", "parse_errors", {}),
    ("demo_code", "demo_code", {}),
    ("psychsci", "psychsci", {}),
    ("psychsci_nocode", "psychsci_nocode", {}),
    ("mixed", "mixed", {}),
    ("green", "green", {}),
    ("session", "session", {}),
    ("groundhog", "groundhog", {}),
    ("library_only", "library_only", {}),
    ("docstring", "docstring", {}),
    ("docstring1", "docstring1", {}),
    ("jasp_only", "jasp_only", {}),
    ("nocode", "nocode", {}),
    ("empty_table", "empty_table", {}),
    ("empty_files", "empty_files", {}),
    ("all_failed", "all_failed", {"download": False}),
    ("some_failed", "some_failed", {"download": False}),
    ("no_location", "no_location", {}),
    ("no_paper_id", "no_paper_id", {}),
    ("no_file_url", "no_file_url", {}),
    ("paperlist", "paperlist", {}),
    ("paperlist_pins", "paperlist_pins", {}),
    ("paperlist_nocode", "paperlist_nocode", {}),
    ("structure", "structure", {}),
    ("expand", "expand", {}),
    ("qmd", "qmd", {}),
    ("duplicates", "duplicates", {}),
    ("windows", "windows", {}),
    ("na_repo", "na_repo", {}),
    ("urls", "urls", {}),
]

# download cases (file:// URLs): paths normalised by cc_norm() (the repository
# root and the per-session download directory); read errors are not compared
# (R's message for a relative path names the working directory, pytacheck's
# code_read() does not)
DOWNLOAD_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("download", "download", {}),
    ("download_no_download", "download", {"download": False}),
    ("download_file_cap", "download_mixed", {"max_files_per_repo": 2}),
    ("download_budget", "download_mixed", {"max_download_size": 0.0001}),
]

# scenarios whose report tables are compared as data
TABLE_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("code_files", "code_files", {}),
    ("parse_errors", "parse_errors", {}),
    ("demo_code", "demo_code", {}),
    ("psychsci", "psychsci", {}),
    ("mixed", "mixed", {}),
    ("green", "green", {}),
    ("session", "session", {}),
    ("docstring", "docstring", {}),
    ("jasp_only", "jasp_only", {}),
    ("empty_files", "empty_files", {}),
    ("all_failed", "all_failed", {"download": False}),
    ("no_location", "no_location", {}),
    ("paperlist", "paperlist", {}),
    ("expand", "expand", {}),
    ("duplicates", "duplicates", {}),
    ("windows", "windows", {}),
    ("urls", "urls", {}),
]


def _r_args(args: dict[str, Any]) -> str:
    def lit(v: Any) -> str:
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, str):
            return '"' + v + '"'
        return repr(v)

    return "".join(f", {k} = {lit(v)}" for k, v in args.items())


def _py_args(args: dict[str, Any]) -> str:
    return "".join(f", {k}={v!r}" for k, v in args.items())


def module_case(suffix: str, scenario: str, args: dict[str, Any]) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": f"code_check.{suffix}",
        "module": "code_check",
        "args": {
            "paper": {
                "$expr": {
                    "r": _R % f"cc_prev('{scenario}')",
                    "py": f"{_PY}.cc_prev('{scenario}')",
                }
            },
            **args,
        },
    }
    return case


def download_case(suffix: str, scenario: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"code_check.{suffix}",
        "r": "identity",
        "py": "tests.mod_code.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": _R % f"cc_norm(cc_run('{scenario}'{_r_args(args)}))",
                    "py": f"{_PY}.cc_norm({_PY}.cc_run('{scenario}'{_py_args(args)}))",
                }
            }
        },
        "compare": {"ignore": ["table.error"]},
    }


def table_case(suffix: str, scenario: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"report_tables.{suffix}",
        "r": "identity",
        "py": "tests.mod_code.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": _R % f"cc_report_tables('{scenario}'{_r_args(args)})",
                    "py": f"{_PY}.cc_report_tables('{scenario}'{_py_args(args)})",
                }
            }
        },
    }


def main() -> None:
    cases = (
        [module_case(*c) for c in MODULE_CASES]
        + [download_case(*c) for c in DOWNLOAD_CASES]
        + [table_case(*c) for c in TABLE_CASES]
    )
    header = (
        "# code_check parity cases (inst/modules/code_check.R), generated by\n"
        "# tests/mod_code/make_cases.py -- edit that script, not this file.\n"
        "# Module cases chain code_check onto the repo_check / data_check output of a\n"
        "# scenario in tests/mod_code/scenarios.json (tests/mod_code/cc_helpers.R and\n"
        "# helpers.py); report_tables cases compare the report's table data.\n"
    )
    body = yaml.safe_dump(
        {"area": "mod_code", "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        width=1000,
        default_style='"',
    )
    OUT.write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
