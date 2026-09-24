"""Write ``parity/cases/mod_ref_pubpeer_summary.yaml``.

Papers and module chains come from ``tests/mod_ref_pubpeer_summary/helpers.R``
(R) and ``helpers.py`` (Python, bound to ``H``). PubPeer requests are replayed
from metacheck's recordings (``mock_dir: apis``) or from this directory's
``mock`` recordings (see ``make_mocks.py``). ``*.report_table`` cases compare
the data behind a module's report table (the prose comparison skips table
widgets).

    .venv/bin/python -m tests.mod_ref_pubpeer_summary.make_cases
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_ref_pubpeer_summary.yaml"

R_PRE = 'source(file.path(root, "tests/mod_ref_pubpeer_summary/helpers.R")); '
PY_WRAP = '(lambda H: {})(__import__("tests.mod_ref_pubpeer_summary.helpers", fromlist=["x"]))'
APIS = "apis"
MOCK = "tests/mod_ref_pubpeer_summary/mock"
TEST_PAPER_IGNORE = {"ignore": ["summary_table.paper_id"]}

ALL = '"ref_accuracy", "ref_pubpeer", "ref_replication", "ref_retraction"'
ALL_PY = "'ref_accuracy', 'ref_pubpeer', 'ref_replication', 'ref_retraction'"


def expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": R_PRE + r, "py": PY_WRAP.format(py)}}


def chain(paper_r: str, paper_py: str, mods_r: str, mods_py: str) -> tuple[str, str]:
    return f"chain({paper_r}, c({mods_r}))", f"H.chain({paper_py}, [{mods_py}])"


# (id, module, R paper expression, Python paper expression, mock_dir, extra spec)
CASES: list[tuple[str, str, str, str, str | None, dict[str, Any]]] = [
    # ---- ref_pubpeer --------------------------------------------------------------
    ("ref_pubpeer.demo", "ref_pubpeer", "demopaper()", "pc.demopaper()", APIS, {}),
    ("ref_pubpeer.demo_no_match", "ref_pubpeer", "demo_no_match()", "H.demo_no_match()", APIS, {}),
    ("ref_pubpeer.no_refs", "ref_pubpeer", "demo_no_refs()", "H.demo_no_refs()", MOCK, {}),
    ("ref_pubpeer.no_dois", "ref_pubpeer", "demo_no_dois()", "H.demo_no_dois()", MOCK, {}),
    (
        "ref_pubpeer.no_comments",
        "ref_pubpeer",
        'pp_paper("10.1016/j.tics.2006.06.010", "tics")',
        "H.pp_paper(['10.1016/j.tics.2006.06.010'], 'tics')",
        APIS,
        {},
    ),
    (
        "ref_pubpeer.invalid_doi",
        "ref_pubpeer",
        'pp_paper(c(NA, "nope/hasd", ""), "invalid")',
        "H.pp_paper([None, 'nope/hasd', ''], 'invalid')",
        APIS,
        {},
    ),
    ("ref_pubpeer.single", "ref_pubpeer", "pp_single()", "H.pp_single()", MOCK, {}),
    ("ref_pubpeer.statcheck_only", "ref_pubpeer", "pp_statcheck()", "H.pp_statcheck()", MOCK, {}),
    ("ref_pubpeer.request_failed", "ref_pubpeer", "pp_fail()", "H.pp_fail()", MOCK, {}),
    ("ref_pubpeer.paperlist", "ref_pubpeer", "pp_list()", "H.pp_list()", MOCK, {}),
    ("ref_pubpeer.mixed", "ref_pubpeer", "pp_mixed()", "H.pp_mixed()", MOCK, {}),
    ("ref_pubpeer.psychsci", "ref_pubpeer", "psychsci3()", "H.psychsci3()", MOCK, {}),
    (
        "ref_pubpeer.psychsci.paper1",
        "ref_pubpeer",
        "psychsci3()[[1]]",
        "H.psychsci3()[0]",
        MOCK,
        {},
    ),
    (
        "ref_pubpeer.psychsci.paper2_no_hits",
        "ref_pubpeer",
        "psychsci3()[[2]]",
        "H.psychsci3()[1]",
        MOCK,
        {},
    ),
    # ---- ref_summary: standalone ---------------------------------------------------
    ("ref_summary.demo", "ref_summary", "demopaper()", "pc.demopaper()", None, {}),
    ("ref_summary.no_refs", "ref_summary", "demo_no_refs()", "H.demo_no_refs()", None, {}),
    ("ref_summary.no_dois", "ref_summary", "demo_no_dois()", "H.demo_no_dois()", None, {}),
    ("ref_summary.psychsci", "ref_summary", "psychsci3()", "H.psychsci3()", None, {}),
    ("ref_summary.paperlist", "ref_summary", "pp_list()", "H.pp_list()", None, {}),
    (
        "ref_summary.extra_args",
        "ref_summary",
        "demopaper()",
        "pc.demopaper()",
        None,
        {"args_extra": {"foo": 1, "bar": "x"}},
    ),
    # ---- ref_summary: chained -------------------------------------------------------
    (
        "ref_summary.demo.chain_accuracy",
        "ref_summary",
        *chain("demopaper()", "pc.demopaper()", '"ref_accuracy"', "'ref_accuracy'"),
        None,
        {},
    ),
    (
        "ref_summary.demo.chain_pubpeer",
        "ref_summary",
        *chain("demopaper()", "pc.demopaper()", '"ref_pubpeer"', "'ref_pubpeer'"),
        APIS,
        {},
    ),
    (
        "ref_summary.demo.chain_replication",
        "ref_summary",
        *chain("demopaper()", "pc.demopaper()", '"ref_replication"', "'ref_replication'"),
        None,
        {},
    ),
    (
        "ref_summary.demo.chain_retraction",
        "ref_summary",
        *chain("demopaper()", "pc.demopaper()", '"ref_retraction"', "'ref_retraction'"),
        None,
        {},
    ),
    (
        "ref_summary.demo.chain_all",
        "ref_summary",
        *chain("demopaper()", "pc.demopaper()", ALL, ALL_PY),
        APIS,
        {},
    ),
    (
        "ref_summary.demo.chain_all_reversed",
        "ref_summary",
        *chain(
            "demopaper()",
            "pc.demopaper()",
            '"ref_retraction", "ref_replication", "ref_pubpeer", "ref_accuracy", "marginal"',
            "'ref_retraction', 'ref_replication', 'ref_pubpeer', 'ref_accuracy', 'marginal'",
        ),
        APIS,
        {},
    ),
    (
        "ref_summary.demo_no_match.chain_all",
        "ref_summary",
        *chain("demo_no_match()", "H.demo_no_match()", ALL, ALL_PY),
        APIS,
        {},
    ),
    (
        "ref_summary.no_dois.chain_all",
        "ref_summary",
        *chain("demo_no_dois()", "H.demo_no_dois()", ALL, ALL_PY),
        MOCK,
        {},
    ),
    (
        "ref_summary.no_refs.chain_all",
        "ref_summary",
        *chain("demo_no_refs()", "H.demo_no_refs()", ALL, ALL_PY),
        MOCK,
        {},
    ),
    (
        "ref_summary.statcheck.chain_pubpeer",
        "ref_summary",
        *chain("pp_statcheck()", "H.pp_statcheck()", '"ref_pubpeer"', "'ref_pubpeer'"),
        MOCK,
        {},
    ),
    (
        "ref_summary.paperlist.chain_all",
        "ref_summary",
        *chain("pp_list()", "H.pp_list()", ALL, ALL_PY),
        MOCK,
        {},
    ),
    (
        "ref_summary.mixed.chain_all",
        "ref_summary",
        *chain("pp_mixed()", "H.pp_mixed()", ALL, ALL_PY),
        MOCK,
        {},
    ),
    (
        "ref_summary.psychsci.chain_all",
        "ref_summary",
        *chain("psychsci3()", "H.psychsci3()", ALL, ALL_PY),
        MOCK,
        {},
    ),
    (
        "ref_summary.psychsci.chain_accuracy",
        "ref_summary",
        *chain("psychsci3()", "H.psychsci3()", '"ref_accuracy"', "'ref_accuracy'"),
        None,
        {},
    ),
    # ---- ref_summary: edited ref_accuracy tables ----------------------------------
    (
        "ref_summary.accuracy.all_false",
        "ref_summary",
        'set_mismatch(chain(demopaper(), "ref_accuracy"), FALSE)',
        "H.set_mismatch(H.chain(pc.demopaper(), ['ref_accuracy']), False)",
        None,
        {},
    ),
    (
        "ref_summary.accuracy.all_na",
        "ref_summary",
        'set_mismatch(chain(demopaper(), "ref_accuracy"), NA)',
        "H.set_mismatch(H.chain(pc.demopaper(), ['ref_accuracy']), None)",
        None,
        {},
    ),
    (
        "ref_summary.accuracy.several",
        "ref_summary",
        'set_table_col(set_table_col(chain(demopaper(), "ref_accuracy"), "title_mismatch", '
        'c(TRUE, NA, FALSE, TRUE, FALSE)), "year_mismatch", c(TRUE, FALSE, NA, FALSE, TRUE))',
        "H.set_table_col(H.set_table_col(H.chain(pc.demopaper(), ['ref_accuracy']), "
        "'title_mismatch', [True, None, False, True, False]), 'year_mismatch', "
        "[True, False, None, False, True])",
        None,
        {},
    ),
    (
        "ref_summary.accuracy.no_match_na",
        "ref_summary",
        'set_table_col(set_mismatch(chain(demopaper(), "ref_accuracy"), TRUE), "no_match", '
        "c(NA, FALSE, TRUE, NA, TRUE))",
        "H.set_table_col(H.set_mismatch(H.chain(pc.demopaper(), ['ref_accuracy']), True), "
        "'no_match', [None, False, True, None, True])",
        None,
        {},
    ),
    (
        "ref_summary.accuracy.no_no_match_col",
        "ref_summary",
        'drop_table_cols(chain(demopaper(), "ref_accuracy"), "no_match")',
        "H.drop_table_cols(H.chain(pc.demopaper(), ['ref_accuracy']), ['no_match'])",
        None,
        {},
    ),
    (
        "ref_summary.pubpeer.no_url_col",
        "ref_summary",
        'drop_table_cols(chain(demopaper(), "ref_pubpeer"), "url")',
        "H.drop_table_cols(H.chain(pc.demopaper(), ['ref_pubpeer']), ['url'])",
        APIS,
        {},
    ),
]

# report table data: (id, module, R output expression, Python output expression, mock_dir)
REPORT_TABLES: list[tuple[str, str, str, str | None]] = [
    ("ref_pubpeer.demo.report_table", 'chain(demopaper(), "ref_pubpeer")',
     "H.chain(pc.demopaper(), ['ref_pubpeer'])", APIS),
    ("ref_pubpeer.paperlist.report_table", 'chain(pp_list(), "ref_pubpeer")',
     "H.chain(H.pp_list(), ['ref_pubpeer'])", MOCK),
    ("ref_summary.demo.report_table", 'chain(demopaper(), "ref_summary")',
     "H.chain(pc.demopaper(), ['ref_summary'])", None),
    ("ref_summary.demo.chain_all.report_table", f"chain(demopaper(), c({ALL}, \"ref_summary\"))",
     f"H.chain(pc.demopaper(), [{ALL_PY}, 'ref_summary'])", APIS),
    ("ref_summary.mixed.chain_all.report_table", f"chain(pp_mixed(), c({ALL}, \"ref_summary\"))",
     f"H.chain(H.pp_mixed(), [{ALL_PY}, 'ref_summary'])", MOCK),
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
    # a test paper (random paper_id) without references
    for mod in ("ref_pubpeer", "ref_summary"):
        cases.append(
            {
                "id": f"{mod}.test_paper",
                "module": mod,
                "args": {"paper": {"$test_paper": {"text": ["No references here."]}}},
                "compare": TEST_PAPER_IGNORE,
            }
        )
    # the demo paper read from its JSON file
    cases.append(
        {
            "id": "ref_pubpeer.to_err_is_human",
            "module": "ref_pubpeer",
            "args": {"paper": {"$read": ["upstream/metacheck/inst/demos/to_err_is_human.json"]}},
            "mock_dir": APIS,
        }
    )
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
    return {"area": "mod_ref_pubpeer_summary", "cases": cases}


class _Quoted(yaml.SafeDumper):
    """Quote every string (R's YAML reader would otherwise retype ``"1.0"`` etc.)."""


def _str(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style='"')


_Quoted.add_representer(str, _str)


def main() -> None:
    header = (
        "# Parity cases for the ref_pubpeer and ref_summary modules.\n"
        "# Generated by tests/mod_ref_pubpeer_summary/make_cases.py -- edit that file.\n"
    )
    body = yaml.dump(build(), Dumper=_Quoted, sort_keys=False, allow_unicode=True, width=1000)
    OUT.write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
