"""Write ``parity/cases/mod_ref_pubpeer_summary_review.yaml`` (adversarial review cases).

The scenarios are the ``pp_*`` / ``rv_*`` functions of
``tests/mod_ref_pubpeer_summary/helpers.R`` and their Python twins in
``helpers.py``; their PubPeer responses are in this directory's ``mock``
recordings (see ``make_mocks.py``).

    .venv/bin/python -m tests.mod_ref_pubpeer_summary.make_review_cases
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.mod_ref_pubpeer_summary.make_cases import ALL, ALL_PY, APIS, MOCK, _Quoted, expr

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_ref_pubpeer_summary_review.yaml"


def _chain(paper_r: str, paper_py: str, mods: list[str]) -> tuple[str, str]:
    r = ", ".join(f'"{m}"' for m in mods)
    py = ", ".join(f"'{m}'" for m in mods)
    return f"chain({paper_r}, c({r}))", f"H.chain({paper_py}, [{py}])"


# (id, module, R paper expression, Python paper expression, mock_dir, extra spec)
CASES: list[tuple[str, str, str, str, str | None, dict[str, Any]]] = [
    # ---- ref_pubpeer --------------------------------------------------------------
    # empty users, null total_comments, ["Statcheck"], url "", "statcheck", unrequested id
    ("ref_pubpeer.odd", "ref_pubpeer", "pp_odd()", "H.pp_odd()", MOCK, {}),
    # the only commented reference has no URL: `table$url` is NULL and R errors
    ("ref_pubpeer.nourl_only", "ref_pubpeer", "pp_nourl_only()", "H.pp_nourl_only()", MOCK, {}),
    # the only commented reference has an NA URL: the report table is empty ("")
    ("ref_pubpeer.nourl_some", "ref_pubpeer", "pp_nourl_some()", "H.pp_nourl_some()", MOCK, {}),
    # a paper list whose second paper has no references (summary_table na_replace)
    ("ref_pubpeer.list_empty", "ref_pubpeer", "pp_list_empty()", "H.pp_list_empty()", MOCK, {}),
    # mixed-case paper ids with bib_match: ref_table() arranges in the C locale
    ("ref_pubpeer.case_list", "ref_pubpeer", "pp_case_list()", "H.pp_case_list()", MOCK, {}),
    # R's module_run() passes `...` on: ref_pubpeer(paper, ...) has no `...`
    (
        "ref_pubpeer.extra_arg",
        "ref_pubpeer",
        "pp_single()",
        "H.pp_single()",
        MOCK,
        {"args_extra": {"foo": "1"}},
    ),
    # ---- ref_summary after edge-case ref_pubpeer outputs ----------------------------
    ("ref_summary.odd.chain_pubpeer", "ref_summary", *_chain("pp_odd()", "H.pp_odd()",
     ["ref_pubpeer"]), MOCK, {}),
    ("ref_summary.nourl_some.chain_pubpeer", "ref_summary", *_chain(
        "pp_nourl_some()", "H.pp_nourl_some()", ["ref_pubpeer"]), MOCK, {}),
    ("ref_summary.case_list.chain_pubpeer", "ref_summary", *_chain(
        "pp_case_list()", "H.pp_case_list()", ["ref_pubpeer"]), MOCK, {}),
    ("ref_summary.list_empty.chain_all", "ref_summary", f"chain(pp_list_empty(), c({ALL}))",
     f"H.chain(H.pp_list_empty(), [{ALL_PY}])", MOCK, {}),
    ("ref_summary.pp_url_na", "ref_summary", "rv_pp_url_na()", "H.rv_pp_url_na()", APIS, {}),
    # ---- ref_summary after edited ref_accuracy / ref_replication / ref_retraction ----
    ("ref_summary.ret_collide", "ref_summary", "rv_ret_collide()", "H.rv_ret_collide()", APIS,
     {}),
    ("ref_summary.ret_keys", "ref_summary", "rv_ret_keys()", "H.rv_ret_keys()", None, {}),
    ("ref_summary.rep_no_type", "ref_summary", "rv_rep_no_type()", "H.rv_rep_no_type()", None,
     {}),
    ("ref_summary.acc_dup", "ref_summary", "rv_acc_dup()", "H.rv_acc_dup()", None, {}),
    ("ref_summary.acc_chr", "ref_summary", "rv_acc_chr()", "H.rv_acc_chr()", None, {}),
    ("ref_summary.acc_num", "ref_summary", "rv_acc_num()", "H.rv_acc_num()", None, {}),
    ("ref_summary.acc_mixed", "ref_summary", "rv_acc_mixed()", "H.rv_acc_mixed()", None, {}),
    ("ref_summary.acc_na_chr", "ref_summary", "rv_acc_na_chr()", "H.rv_acc_na_chr()", None, {}),
    ("ref_summary.acc_extra", "ref_summary", "rv_acc_extra()", "H.rv_acc_extra()", None, {}),
    ("ref_summary.acc_no_mismatch", "ref_summary", "rv_acc_no_mismatch()",
     "H.rv_acc_no_mismatch()", None, {}),
]  # fmt: skip

# report table data: (id, R output expression, Python output expression, mock_dir)
REPORT_TABLES: list[tuple[str, str, str, str | None]] = [
    ("ref_pubpeer.odd.report_table", 'chain(pp_odd(), "ref_pubpeer")',
     "H.chain(H.pp_odd(), ['ref_pubpeer'])", MOCK),
    ("ref_pubpeer.case_list.report_table", 'chain(pp_case_list(), "ref_pubpeer")',
     "H.chain(H.pp_case_list(), ['ref_pubpeer'])", MOCK),
    ("ref_summary.ret_collide.report_table", 'module_run(rv_ret_collide(), "ref_summary")',
     "H.chain(H.rv_ret_collide(), ['ref_summary'])", APIS),
    ("ref_summary.acc_dup.report_table", 'module_run(rv_acc_dup(), "ref_summary")',
     "H.chain(H.rv_acc_dup(), ['ref_summary'])", None),
    ("ref_summary.odd.chain_pubpeer.report_table",
     'chain(pp_odd(), c("ref_pubpeer", "ref_summary"))',
     "H.chain(H.pp_odd(), ['ref_pubpeer', 'ref_summary'])", MOCK),
]  # fmt: skip


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for cid, mod, r, py, mock, extra in CASES:
        extra = dict(extra)
        args: dict[str, Any] = {"paper": expr(r, py)}
        args.update(extra.pop("args_extra", {}))
        case: dict[str, Any] = {"id": cid, "module": mod, "args": args}
        if mock:
            case["mock_dir"] = mock
        case.update(extra)
        cases.append(case)
    for cid, r, py, mock in REPORT_TABLES:
        case = {
            "id": cid,
            "r": "identity",
            "py": "copy.copy",
            "args": {"x": expr(f"report_tbl({r})", f"H.report_tbl({py})")},
        }
        if mock:
            case["mock_dir"] = mock
        cases.append(case)
    return {"area": "mod_ref_pubpeer_summary_review", "cases": cases}


def main() -> None:
    header = (
        "# Adversarial review cases for the ref_pubpeer and ref_summary modules.\n"
        "# Generated by tests/mod_ref_pubpeer_summary/make_review_cases.py -- edit that file.\n"
    )
    body = yaml.dump(build(), Dumper=_Quoted, sort_keys=False, allow_unicode=True, width=1000)
    OUT.write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
