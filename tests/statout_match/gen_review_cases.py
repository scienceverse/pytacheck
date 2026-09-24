"""Generate ``parity/cases/statout_match_review.yaml`` (every string quoted).

Adversarial-review cases for ``match_reported_output()`` and its helpers:
NA / absent ``text_id`` (dropped by the evidence regrouping), site collation
ties, value formatting, non-finite values, degenerate output tables, the
``eq`` fallback of a paper object and table papers missing a column.

Run ``python tests/statout_match/gen_review_cases.py`` after changing the
cases, then ``python -m parity generate --area statout_match_review``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.statout_match.gen_parity_cases import (
    NA,
    QuotedDumper,
    r_output,
    r_paper,
    rchr,
    rs,
)

R = "tests.statout_match._review"
cases: list[dict[str, Any]] = []

_CHR_KEYS = {"name", "comp", "value", "df"}


def r_vec(values: list[Any], type_: str) -> str:
    conv = {"int": "as.integer", "dbl": "as.numeric", "chr": "as.character"}[type_]
    return f"{conv}(c({', '.join(rs(v) for v in values)}))" if values else f"{conv}(c())"


def r_comp(c: dict[str, Any]) -> str:
    parts = []
    for k, v in c.items():
        if v is None or v == NA:
            parts.append(f"{k} = {'NA_character_' if k in _CHR_KEYS else 'NA_integer_'}")
        else:
            parts.append(f"{k} = {rs(v)}")
    return "list(" + ", ".join(parts) + ")"


def r_tt(spec: dict[str, Any]) -> str:
    types = spec.get("types", {})
    cols = ", ".join(f"{k} = {r_vec(v, types.get(k, 'chr'))}" for k, v in spec["cols"].items())
    comps = ", ".join(
        "list(" + ", ".join(r_comp(c) for c in row) + ")" for row in spec["components"]
    )
    return (
        f"local({{tt <- data.frame({cols}, stringsAsFactors = FALSE); "
        f"tt$components <- list({comps}); tt}})"
    )


def r_paper_review(spec: dict[str, Any]) -> str:
    kind = spec["kind"]
    if kind == "tt":
        return r_tt(spec)
    if kind == "eq_paper":
        return (
            f"local({{p <- metacheck::test_paper({rchr(spec['texts'])}); "
            f"p$eq <- metacheck::extract_eq(metacheck::test_paper({rchr(spec['eq_texts'])})); p}})"
        )
    code = r_paper(spec)
    if kind == "table_paper" and spec.get("drop"):
        code = (
            f"local({{p <- {code}; p$table <- p$table[setdiff(names(p$table), "
            f"{rchr(spec['drop'])})]; p}})"
        )
    return code


def expr_case(id_: str, r_code: str, py_fn: str, py_args: dict[str, Any], **extra: Any) -> None:
    cases.append(
        {
            "id": id_,
            "r": "identity",
            "py": f"{R}.{py_fn}",
            "args": {"x": {"$expr": {"r": r_code, "py": "None"}}},
            "py_drop": ["x"],
            "py_args": py_args,
            **extra,
        }
    )


def match_case(
    id_: str,
    paper: dict[str, Any],
    output: Any,
    include_tables: Any = False,
    min_components: int = 1,
    summary: bool = True,
    **extra: Any,
) -> None:
    call = (
        f"metacheck::match_reported_output({r_paper_review(paper)}, {r_output(output)}, "
        f"include_tables = {rs(include_tables)}, min_components = {rs(min_components)})"
    )
    args = {
        "paper": paper,
        "output": output,
        "include_tables": include_tables,
        "min_components": min_components,
    }
    expr_case(f"match_reported_output.{id_}", call, "match", args, **extra)
    if summary:
        expr_case(
            f"match_reported_output.{id_}.summary",
            f'attr({call}, "summary")',
            "match",
            {**args, "what": "summary"},
        )


def comp(name: str, value: Any, pos: Any = None, comp_: Any = "=", df: Any = NA) -> dict[str, Any]:
    c: dict[str, Any] = {"name": name, "comp": comp_, "value": value, "df": df}
    if pos is not None:
        c["sentence_pos"] = pos
    return c


def long_df(rows: list[tuple[Any, ...]], cols: tuple[str, ...], **kw: Any) -> dict[str, Any]:
    return {
        "kind": "df",
        "source_file": rows[0][0] if rows else "",
        "cols": {c: [r[i] for r in rows] for i, c in enumerate(cols)},
        **kw,
    }


COLS = ("source_file", "test_id", "analysis", "row_label", "statistic", "value")

# two t-tests, both confirmed by the output
T1 = [comp("t", "2.10", 1, df="(20)"), comp("p", ".048", 2), comp("d", "0.45", 3)]
T2 = [comp("t", "3.10", 1, df="(30)"), comp("p", ".002", 2)]
LONG_T = long_df(
    [
        ("a.omv", "a_t1", "T-Test", "score", "t", "2.103"),
        ("a.omv", "a_t1", "T-Test", "score", "df", "20"),
        ("a.omv", "a_t1", "T-Test", "score", "p", "0.0481"),
        ("a.omv", "a_t1", "T-Test", "score", "es", "0.452"),
        ("b.omv", "b_t2", "T-Test", "anx", "t", "3.1"),
        ("b.omv", "b_t2", "T-Test", "anx", "df", "30"),
        ("b.omv", "b_t2", "T-Test", "anx", "p", "0.0021"),
    ],
    COLS,
)


def tt(text_id: list[Any] | None, rows: list[list[dict[str, Any]]], **kw: Any) -> dict[str, Any]:
    cols: dict[str, Any] = {"test_no": list(range(1, len(rows) + 1))}
    types = {"test_no": "int", "text_id": "int"}
    if text_id is not None:
        cols["text_id"] = text_id
    cols["sentence"] = [f"sentence {i}" for i in range(1, len(rows) + 1)]
    return {"kind": "tt", "cols": cols, "types": types, "components": rows, **kw}


# -- NA / absent text_id: .regroup_by_evidence() split() drops the NA key
match_case("tt.na_text_id_some", tt([1, NA], [T1, T2]), LONG_T)
match_case("tt.na_text_id_all", tt([NA, NA], [T1, T2]), LONG_T)
match_case("tt.no_text_id", tt(None, [T1, T2]), LONG_T)
# no numeric output value at all: no site, so no regrouping, and the rows keep
# R's data.frame(text_id = NULL, ...) shape
LONG_NOVAL = long_df([("a.omv", "a_t1", "T-Test", "score", "t", "n/a")], COLS)
match_case("tt.no_text_id.no_sites", tt(None, [T1, T2]), LONG_NOVAL)
match_case("tt.na_text_id_some.no_sites", tt([1, NA], [T1, T2]), LONG_NOVAL)
# split() orders the sentences by their text_id STRING ("10" < "2")
match_case("tt.text_id_string_order", tt([10, 2, 10], [T1, T2, [comp("d", "0.45", 4)]]), LONG_T)
# components without sentence_pos (pos falls back to the local index)
T1_NOPOS = [comp("t", "2.10", df="(20)"), comp("p", ".048"), comp("d", "0.45")]
T2_NOPOS = [comp("p", ".048"), comp("t", "3.10", df="(30)"), comp("p", ".002")]
match_case("tt.no_sentence_pos", tt([5, 5], [T1_NOPOS, T2_NOPOS]), LONG_T)
# identical components in one sentence (match(list, list) collapses them)
match_case("tt.duplicate_components", tt([3, 3, 3], [T1, T1, T2]), LONG_T)
# comp given inside the value, missing comp field, numeric value and df
T_ODD = [
    {"name": " t ", "value": "3.1", "df": "(30)", "sentence_pos": 1},
    {"name": "p", "comp": NA, "value": "< .01", "df": NA, "sentence_pos": 2},
    {"name": "d", "comp": "=", "value": 0.452, "df": 20, "sentence_pos": 3},
    {"name": "Estimate", "comp": ">", "value": "0.4", "df": NA, "sentence_pos": 4},
]
match_case("tt.odd_components", tt([7], [T_ODD]), LONG_T)
# min_components above every test: nothing is assessed
match_case("tt.min_components_high", tt([1, 2], [T1, T2]), LONG_T, min_components=5)
# include_tables is ignored for a data frame input (and isTRUE() needs TRUE)
match_case("tt.include_tables_df", tt([1, 2], [T1, T2]), LONG_T, include_tables=True)

# -- value formatting: format(value, trim = TRUE) (7 significant digits)
FMT = [
    "0.0000123", "1234567", "100000", "123456.7", "0.1234567891", "-0", "1e-300",
    "12345678901", "0.00012", "1e5", "2.50", "-.5", "1234567.5", "0.1", "99999.99",
]  # fmt: skip
T_FMT = [comp("b", v, i + 1) for i, v in enumerate(FMT)]
LONG_FMT = long_df(
    [("f.R", "f_1", "lm", "x", "Estimate", v) for v in ["1.23e-05", "1234567", "1e+05"]],
    COLS,
)
match_case("tt.format_values", tt([1], [T_FMT]), LONG_FMT)

# -- ties between sites: the first site in split()'s ICU collation order wins
TIE_IDS = ["b_site", "B_site", "a_site", "_z", "A_site", "a2", "10", "9"]
LONG_TIE = long_df(
    [
        (f"{tid}.omv", tid, f"an {tid}", tid, stat, val)
        for tid in TIE_IDS
        for stat, val in (("t", "2.103"), ("df", "20"), ("p", "0.0481"))
    ],
    COLS,
)
match_case("tt.site_ties_collation", tt([1], [T1]), LONG_TIE)
LONG_TIE_NOID = {**LONG_TIE, "drop": ["test_id"]}
match_case("tt.site_ties_collation.no_test_id", tt([1], [T1]), LONG_TIE_NOID)
# numeric test_id: as.character() of a double
LONG_NUMID = long_df(
    [
        ("n.omv", tid, "an", "x", stat, val)
        for tid in ("10", "2", "1.5")
        for stat, val in (("t", "2.103"), ("p", "0.0481"))
    ],
    COLS,
    types={"test_id": "dbl"},
)
match_case("tt.numeric_test_id", tt([1], [T1]), LONG_NUMID)

# -- degenerate output tables
match_case("tt.no_statistic_col", tt([1, 2], [T1, T2]), {**LONG_T, "drop": ["statistic"]})
match_case("tt.no_value_col", tt([1, 2], [T1, T2]), {**LONG_T, "drop": ["value"]})
match_case(
    "tt.only_value_col",
    tt([1, 2], [T1, T2]),
    {**LONG_T, "drop": ["source_file", "test_id", "analysis", "row_label"]},
)
# a stat_output list whose second table has no test_id: its rows get an NA
# test_id from bind_rows() and are dropped by split()
LONG_T2_NOID = {**long_df(
    [
        ("c.omv", "x", "T-Test", "dep", "t", "5.5"),
        ("c.omv", "x", "T-Test", "dep", "p", "0.0001"),
    ],
    COLS,
), "drop": ["test_id"]}
match_case(
    "tt.list_mixed_test_id",
    tt([1, 2, 3], [T1, T2, [comp("t", "5.50", 1), comp("p", "< .001", 2)]]),
    [LONG_T, LONG_T2_NOID],
)
# an empty test_id: by_site[[""]] is NULL and used_sites[[""]] errors in R
LONG_EMPTY_ID = long_df(
    [("e.omv", "", "an", "x", "t", "2.103"), ("e.omv", "", "an", "x", "p", "0.0481")]
    + [("e.omv", "e_1", "an", "x", "t", "2.103")],
    COLS,
)
match_case("tt.empty_test_id", tt([1], [T1]), LONG_EMPTY_ID, summary=False)
match_case(
    "tt.empty_test_id.censored_only",
    tt([1], [[comp("t", "< 3", 1), comp("p", "< .05", 2)]]),
    LONG_EMPTY_ID,
    summary=False,
)
# the same with no anchor-tagged test (a raw eq data frame: no regrouping)
match_case(
    "eq.empty_test_id.censored_only",
    {"kind": "eq", "texts": ["The test gave t < 3, p < .05."]},
    LONG_EMPTY_ID,
    summary=False,
)
# non-finite values: round(Inf) - Inf is NaN, and `if (NA)` errors in R
LONG_INF = long_df(
    [("i.R", "i_1", "an", "x", "t", "1e400"), ("i.R", "i_1", "an", "x", "p", "0.01")],
    COLS,
)
match_case("tt.inf_value", tt([1], [[comp("t", "1e400", 1), comp("p", ".01", 2)]]), LONG_INF,
           summary=False)  # fmt: skip
match_case(
    "tt.inf_value_other_family",
    tt([1], [[comp("F", "1e400", 1), comp("p", ".01", 2)]]),
    LONG_INF,
)
match_case(
    "tt.inf_value_hit_first",
    tt([1], [[comp("t", "1e400", 1), comp("p", ".01", 2)]]),
    long_df(
        [
            ("i.R", "a_0", "an", "x", "t", "1e400"),
            ("i.R", "a_0", "an", "x", "p", "0.01"),
            ("i.R", "a_1", "an", "x", "t", "5"),
        ],
        COLS,
    ),
    summary=False,
)

# -- paper objects
# extract_tests() finds nothing in the text: the paper's own eq is recomposed
match_case(
    "eq_paper.fallback",
    {
        "kind": "eq_paper",
        "texts": ["No statistics are reported in this sentence."],
        "eq_texts": ["The effect was t(20) = 2.10, p = .048, d = 0.45.", "And t(30) = 3.10."],
    },
    LONG_T,
)
# include_tables with a table that lacks section_id / table_id: .table_tests()
# errors in R and tryCatch() drops every table test
_TABLE = {
    "table_id": 1,
    "section_id": 2,
    "contents": [["", "t", "df", "p"], ["Score", "2.10", "20", ".048"]],
}
_TABLE_PAPER = {
    "kind": "table_paper",
    "texts": ["Results are in Table 1.", "Table 1. Tests"],
    "section_ids": [1, 2],
    "tables": [_TABLE],
}
match_case("table_paper.include_tables", _TABLE_PAPER, LONG_T, include_tables=True)
match_case(
    "table_paper.no_section_id",
    {**_TABLE_PAPER, "drop": ["section_id"]},
    LONG_T,
    include_tables=True,
)
match_case(
    "table_paper.no_table_id",
    {**_TABLE_PAPER, "drop": ["table_id"]},
    LONG_T,
    include_tables=True,
)
match_case(
    "table_paper.no_contents",
    {**_TABLE_PAPER, "drop": ["contents"]},
    LONG_T,
    include_tables=True,
)
expr_case(
    "table_tests.no_section_id",
    f"tryCatch(metacheck:::.table_tests({r_paper_review({**_TABLE_PAPER, 'drop': ['section_id']})}),"
    ' error = function(e) "error")',
    "table_tests_or_error",
    {"paper": {**_TABLE_PAPER, "drop": ["section_id"]}},
)

# format() of a value, as the reported / match_values columns print it
FMT_NUM = [
    0.0000123, 1234567.0, 100000.0, 123456.7, 0.1234567891, -0.0, 1e-300, 12345678901.0,
    0.00012, 2.5, -0.5, 1234567.5, 0.1, 99999.99, 0.1 + 0.2, 1 / 3, 2 / 3, 1e15, 1e16,
    123456789012345.0, 0.000099999999, 5e-324, 1.7976931348623157e308, 0.30000000000000004,
    1234.5678, 0.0001, 0.001, 1e-5, 314159.26535,
]  # fmt: skip
expr_case(
    "format_values",
    "vapply(c(" + ", ".join(repr(v) for v in FMT_NUM) + "), function(v) format(v, trim = TRUE),"
    ' "")',
    "fmt_values",
    {"values": FMT_NUM},
)


if __name__ == "__main__":
    out = {"area": "statout_match_review", "cases": cases}
    path = Path(__file__).resolve().parents[2] / "parity/cases/statout_match_review.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Adversarial-review parity cases for match_reported_output() and helpers.\n"
            "# Generated by tests/statout_match/gen_review_cases.py -- do not edit by hand.\n"
        )
        yaml.dump(out, fh, Dumper=QuotedDumper, sort_keys=False, allow_unicode=True, width=10000)
    print(len(cases))
