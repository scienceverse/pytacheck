"""The canonicaliser (parity/canon.py) and its configuration (parity/canon.toml).

The known-difference tests are FIDELITY.md §4.3, guard 3: what canon must flag
and what it must hide.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from parity import canon
from parity.cases import GOLDEN_DIR, load_cases
from parity.compare import Options, compare_paths
from parity.lockfile import digest


def _chr(*v: str | None) -> dict:
    return {"t": "chr", "v": list(v)}


def _dbl(*v: float | str | None) -> dict:
    return {"t": "dbl", "v": list(v)}


def _int(*v: int | None) -> dict:
    return {"t": "int", "v": list(v)}


def _lgl(*v: bool | None) -> dict:
    return {"t": "lgl", "v": list(v)}


def _list(*v: dict, names: list[str] | None = None) -> dict:
    return {"t": "list", "names": names, "v": list(v)}


def _df(**cols: dict) -> dict:
    nrow = len(next(iter(cols.values()))["v"]) if cols else 0
    return {"t": "df", "nrow": nrow, "names": list(cols), "v": list(cols.values())}


def _output(module: str, table: dict, light: str = "green", summary: str = "") -> dict:
    return {
        "t": "module_output",
        "names": ["module", "table", "traffic_light", "summary_text"],
        "v": [_chr(module), table, _chr(light), _chr(summary)],
    }


def _rows(df: dict, order: list[int]) -> dict:
    """*df* with its rows in *order*."""
    return {
        **df,
        "nrow": len(order),
        "v": [{**c, "v": [c["v"][i] for i in order]} for c in df["v"]],
    }


def _golden(area: str, case_id: str) -> tuple[Any, canon.Profile]:
    case = next(c for c in load_cases(area) if c.id == case_id)
    value = json.loads(case.golden_path.read_text(encoding="utf-8"))["value"]
    return value, canon.profile(case.spec)


def _element(x: dict, name: str) -> dict:
    element: dict = x["v"][x["names"].index(name)]
    return element


def _flagged(r: Any, p: Any, prof: canon.Profile | None = None) -> canon.Comparison:
    """How canon compares *r* and *p*, which must differ, in the comparison and in
    their lock digests."""
    c = canon.compare(r, p, prof)
    assert not c.equal
    dr, dp = canon.digests(r, p, prof)
    assert dr != dp
    return c


def _without(rule: str) -> canon.Profile:
    return canon.profile().only(*(set(canon.RULES) - {rule}))


_TABLE = _df(paper_id=_chr("a", "a", "b"), text=_chr("x", "y", "z"), n=_dbl(1, 2, 3))

# -- must flag (FIDELITY.md §4.3, guard 3) ---------------------------------------------


def test_flags_a_dropped_row() -> None:
    c = _flagged(_TABLE, _rows(_TABLE, [0, 2]))
    assert c.problems == ["<root>: a row only R has: {'paper_id': 'a', 'text': 'y'}"]


def test_flags_a_changed_light() -> None:
    c = _flagged(_output("m", _TABLE), _output("m", _TABLE, light="red"))
    assert c.paths == ["traffic_light[]"]


def test_flags_a_changed_number_in_summary_text() -> None:
    r = _output("m", _TABLE, summary="We found 36 p-values.")
    c = _flagged(r, _output("m", _TABLE, summary="We found 37 p-values."))
    assert c.paths == ["summary_text[]"]


def test_flags_a_value_changed_by_1e8_relative() -> None:
    r = _df(k=_chr("x"), v=_dbl(0.5))
    c = _flagged(r, _df(k=_chr("x"), v=_dbl(0.5 * (1 + 1e-8))))
    assert c.paths == ["v[]"]
    # the relative tolerance is 1e-9
    assert canon.compare(r, _df(k=_chr("x"), v=_dbl(0.5 * (1 + 1e-10)))).equal


@pytest.mark.parametrize("where", ["text", "table", "summary"])
def test_flags_a_changed_exponent(where: str) -> None:
    r, p = "p < 2.2e-16", "p < 2.2e16"
    if where == "table":
        _flagged(_df(k=_chr("x"), p=_chr(r)), _df(k=_chr("x"), p=_chr(p)))
    elif where == "summary":
        _flagged(_output("m", _TABLE, summary=r), _output("m", _TABLE, summary=p))
    else:
        _flagged(_chr(r), _chr(p))
    # as a changed number, not as changed words around 2.2 and 16
    # (parity/accuracy.py's _NUM reads both as {2.2, 16}, FIDELITY.md §4.1)
    values = [[t.value for t in canon.tokens(s) if isinstance(t, canon.Number)] for s in (r, p)]
    assert values == [[2.2e-16], [2.2e16]]


def test_flags_numbers_split_apart() -> None:
    _flagged(_chr("Study 12"), _chr("Study 1 2"))
    _flagged(_chr("Study 12"), _chr("Study 1.2"))
    _flagged(_df(k=_chr("Study 12")), _df(k=_chr("Study 1 2")))


def test_flags_a_column_only_r_has() -> None:
    c = _flagged(_TABLE, _df(paper_id=_chr("a", "a", "b"), text=_chr("x", "y", "z")))
    assert c.problems == ["n: a column only R has (R=[1, 2, 3])"]


def test_flags_a_reordered_text_search_result() -> None:
    r, prof = _golden("text", "text_search.demo.significant")
    assert prof.ordered and r["nrow"] > 1
    p = _rows(r, list(range(r["nrow"]))[::-1])
    _flagged(r, p, prof)
    # the same rows in any other result are a multiset
    assert canon.compare(r, p, canon.profile()).equal


# -- must hide (FIDELITY.md §4.3, guard 3) ---------------------------------------------


def test_hides_funding_check_oi_in_document_order() -> None:
    r, prof = _golden("bibr12", "module.funding_check_oi.mixed")
    p = copy.deepcopy(r)
    table = _element(p, "table")
    assert table["nrow"] > 1
    p["v"][p["names"].index("table")] = _rows(table, list(range(table["nrow"]))[::-1])
    assert canon.compare(r, p, prof).equal
    assert canon.digests(r, p, prof) == canon.digests(r, r, prof)
    # the order rule hides it
    unordered = prof.only(*(set(canon.RULES) - {"order"}))
    assert not canon.compare(r, p, unordered).equal
    assert canon.digests(r, p, unordered) != canon.digests(r, r, unordered)


def test_hides_inserted_spaces() -> None:
    pairs = [
        (_chr("Hamilton1964"), _chr("Hamilton 1964")),
        (
            _output("m", _TABLE, summary="see Hamilton1964, p.3"),
            _output("m", _TABLE, summary="see Hamilton 1964 , p. 3"),
        ),
        # U16 also joins a reference's DOI where a line break split it
        (_df(doi=_chr("10.1177/ 0956797617693326")), _df(doi=_chr("10.1177/0956797617693326"))),
    ]
    for r, p in pairs:
        assert canon.compare(r, p).equal
        assert canon.digests(r, p) == canon.digests(r, r)
        # the whitespace rule hides it
        assert not canon.compare(r, p, _without("whitespace")).equal


# -- the rules --------------------------------------------------------------------------


def test_rows_are_a_multiset() -> None:
    once = _df(k=_chr("x"), v=_dbl(1))
    twice = _df(k=_chr("x", "x"), v=_dbl(1, 1))
    assert canon.compare(twice, once).problems == ["<root>: a row only R has: {'k': 'x'}"]
    assert canon.compare(once, twice).problems == ["<root>: a row only Python has: {'k': 'x'}"]


def test_rows_that_share_a_key_compare_as_full_rows() -> None:
    r = _df(k=_chr("x", "x", "y"), v=_dbl(1, 2, 3))
    assert canon.compare(r, _rows(r, [1, 0, 2])).equal
    c = canon.compare(r, _df(k=_chr("x", "x", "y"), v=_dbl(3, 1, 3)))
    assert c.paths == ["v[]"]


def test_a_declared_key_reports_a_changed_result_as_a_value() -> None:
    table = _df(paper_id=_chr("a", "a"), raw=_chr("t(9) = 1", "t(9) = 2"), error=_lgl(True, False))
    changed = {**table, "v": [*table["v"][:2], _lgl(False, False)]}
    r, p = _output("stat_check", table), _output("stat_check", changed)
    assert canon.compare(r, p).paths == ["table.error[]"]
    # by default the key holds the flag: one row dropped and one added
    bare = canon.profile(config=canon.Config())
    c = canon.compare(r, p, bare)
    assert c.paths == ["table"]
    assert sorted(m.split(":")[1] for m in c.problems) == [
        " a row only Python has",
        " a row only R has",
    ]


def test_row_and_column_order_are_free() -> None:
    p = _rows(_TABLE, [2, 0, 1])
    p = {**p, "names": p["names"][::-1], "v": p["v"][::-1]}
    assert canon.compare(_TABLE, p).equal
    assert canon.form(_TABLE) == canon.form(p)
    ordered = canon.Profile(ordered=True)
    assert not canon.compare(_TABLE, p, ordered).equal


def test_a_column_only_python_has_is_info() -> None:
    p = {
        **_TABLE,
        "names": [*_TABLE["names"], "extra"],
        "v": [*_TABLE["v"], _lgl(True, None, False)],
    }
    c = canon.compare(_TABLE, p)
    assert c.equal
    assert c.info == ["extra: a column only Python has"]
    # it changes no digest: R's is its own form's, and Python's is R's
    assert canon.digests(_TABLE, p) == (digest(canon.form(_TABLE)),) * 2
    # without the rule it is a difference
    assert not canon.compare(_TABLE, p, canon.profile().only("order")).equal


def test_a_changed_value_changes_the_paired_digest() -> None:
    p = {**_TABLE, "v": [*_TABLE["v"][:2], _dbl(1, 2, 4)]}
    dr, dp = canon.digests(_TABLE, p)
    assert dr != dp


@pytest.mark.parametrize(
    ("r", "p"),
    [
        (_chr("a", None), _chr("a", "")),
        (_dbl(1, None), _dbl(1, "NaN")),
        (_chr(None), {"t": "null"}),
        (_list(_chr("a"), _lgl(None)), _chr("a", "")),
    ],
)
def test_na_spellings_are_one_value(r: dict, p: dict) -> None:
    assert canon.compare(r, p).equal
    assert canon.form(r) == canon.form(p)


def test_na_strict_facades_keep_the_spelling() -> None:
    prof = canon.profile({"r": "json_expand"})
    assert prof.na_strict
    assert not canon.compare(_chr("a", None), _chr("a", ""), prof).equal
    assert canon.compare(_chr("a", None), _chr("a", ""), canon.profile()).equal


@pytest.mark.parametrize(
    ("r", "p", "equal"),
    [
        ("p = 0.050", "p = .05", True),
        ("p < 2.2e-16", "p < 2.2 × 10^-16", True),
        ("p < 2.2e-16", "p<2.2 x 10 ^ -16", True),
        ("p < 2.2e-16.", "p < 2.2e16.", False),
        ("p ≤ .05", "p <= 0.05", True),
        ("p < .001", "p > .001", False),
        ("p < .001", "p = .001", False),
        ("t(20) = -2.3", "t(20) = 2.3", False),
        ("t(20) = −2.3", "t(20) = -2.3", True),
        ("COVID-19", "COVID -19", False),  # a hyphen, then a minus: {19} against {-19}
        ("ID 1234567890123456", "ID 1234567890123457", False),  # an id, not a number
        ("code 007", "code 7", False),
        ("p = 0.05", "p = 0.051", True),  # at the printed precision of the less precise side
        ("p = 0.05", "p = 0.06", False),
        ("r = 0.123456789012346", "r = 0.12345678901234568", True),  # R's 15 digits
        ("ISBN 9780123456789", "ISBN 9780123456788", False),  # whole numbers have no noise
        ("arXiv:2101.12345", "arXiv:2101.12346", False),
    ],
)
def test_number_text(r: str, p: str, equal: bool) -> None:
    assert canon.compare(_chr(r), _chr(p)).equal is equal


def test_numbers_in_tables_compare_to_the_relative_tolerance() -> None:
    def output(reported: str) -> dict:
        # stat_check's key leaves the cell to be compared as a value
        table = _df(paper_id=_chr("a"), raw=_chr("t(9) = 1"), reported=_chr(reported))
        return _output("stat_check", table)

    r = output("p = 0.05")
    assert canon.compare(r, output("p = .050")).equal
    assert canon.compare(r, output("p = 0.051")).paths == ["table.reported[]"]
    # and at the printed precision: the last digit of an id is a difference
    r = _df(k=_chr("x"), isbn=_chr("9780123456789"))
    assert not canon.compare(r, _df(k=_chr("x"), isbn=_chr("9780123456788"))).equal
    assert not canon.compare(r, _df(k=_chr("x"), isbn=_dbl(9780123456788.0))).equal
    assert canon.compare(r, _df(k=_chr("x"), isbn=_dbl(9780123456789.0))).equal


@pytest.mark.parametrize(
    ("s", "text"),
    [
        ("p = .050", "p= 0.05"),
        ("1.5e3 and 1500", "1500 and 1500"),
        ("d = −.5", "d= -0.5"),
        ("p≤.05", "p <=0.05"),
        ("see Hamilton1964a", "seeHamilton 1964 a"),
    ],
)
def test_text_form(s: str, text: str) -> None:
    assert canon.text_form(s) == text


@pytest.mark.parametrize("rules", [tuple(canon.RULES), ("number_text",), ("whitespace",)])
@pytest.mark.parametrize(
    "s",
    [
        "version 1.0.3 and 1.50.3",
        "md5:c81e728d9d4c2f636f067f89cc14862c",
        "files/6a0255b0c3b256167e201e17/",
        "(total <italic>N</italic> = 693)",
        "1.79769313486232e+308",
        "p<.05, then p = 2.2 × 10^-16.",
    ],
)
def test_text_forms_are_idempotent(s: str, rules: tuple[str, ...]) -> None:
    prof = canon.Profile().only(*rules)
    once = canon.form(_chr(s), prof)
    assert canon.form(once, prof) == once
    assert canon.compare(_chr(s), once, prof).equal


def test_dtypes_and_list_shapes_are_free() -> None:
    assert canon.compare(_int(1, 2), _dbl(1.0, 2.0)).equal
    assert canon.compare(_chr("0.05", None), _dbl(0.05, None)).equal
    assert not canon.compare(_chr("0.05"), _dbl(0.06)).equal
    assert not canon.compare(_lgl(True), _chr("TRUE")).equal
    assert canon.compare(_list(_chr("a"), _chr("b")), _chr("a", "b")).equal
    records = _list(
        _list(_chr("a"), _dbl(1), names=["k", "v"]),
        _list(_chr("b"), _dbl(2), names=["k", "v"]),
    )
    frame = _df(k=_chr("a", "b"), v=_dbl(1, 2))
    assert canon.compare(records, frame).equal
    assert canon.form(records) == canon.form(frame)
    matrix = {"t": "matrix", "dim": [1, 2], "v": _dbl(1, 2)}
    assert canon.compare(matrix, _dbl(1, 2)).equal
    # parity.compare reads a list of text and numbers as text
    mixed = _list(_chr("0.050"), _int(3))
    assert canon.form(mixed) == _dbl(0.05, 3.0)
    assert canon.compare(mixed, _dbl(0.05, 3.0)).equal
    assert not canon.compare(mixed, _dbl(0.05, 4.0)).equal
    assert not canon.compare(_list(_chr("0.050"), _lgl(True)), _dbl(0.05, 1.0)).equal


def test_whitespace_is_free_but_numbers_stay_apart() -> None:
    assert canon.form(_chr("Hamilton1964")) == canon.form(_chr("Hamilton  1964 "))
    assert canon.form(_chr("Study 12")) != canon.form(_chr("Study 1 2"))
    assert canon.text_form("a b, 12 c") == "ab, 12 c"
    assert not canon.compare(
        _chr("Hamilton1964"), _chr("Hamilton 1964"), canon.Profile().only()
    ).equal


def test_tokens() -> None:
    toks = canon.tokens("p < 2.2 × 10^-16 and d = .5")
    assert [t if isinstance(t, str) else t.text for t in toks] == ["p", "<2.2e-16", "andd=", "0.5"]
    assert toks[1] == canon.Number("<2.2e-16", 2.2e-16, 5e-18, "<")
    assert toks[3] == canon.Number("0.5", 0.5, 0.05)
    # markup before a number: its tag's '>' reads the same with and without whitespace
    assert canon.tokens("N</i> = 5") == canon.tokens(canon.text_form("N</i> = 5"))
    assert canon.number_only(" .5 ") == toks[3]
    assert canon.number_only("< .5") is None  # a bound is not a value
    assert canon.number_only("007") is None


@pytest.mark.parametrize(
    ("r", "p"),
    [
        ("10.1037/abc", "10.10370/abc"),  # a DOI's prefix
        ("10.1016/j.cognition.2020.104010", "10.1016/j.cognition.2020.10401"),  # its suffix
        ("R 4.10.1", "R 4.1.1"),  # a version
        ("https://osf.io/abc/1.50", "https://osf.io/abc/1.5"),
    ],
)
def test_numbers_in_names_are_compared_as_written(r: str, p: str) -> None:
    # as numbers they would be equal at the printed precision of the less precise side
    _flagged(_chr(r), _chr(p))
    _flagged(_df(k=_chr("a"), ref=_chr(r)), _df(k=_chr("a"), ref=_chr(p)))
    for s in (r, p):
        assert canon.text_form(canon.text_form(s)) == canon.text_form(s)
    # the whitespace around them is still free
    assert canon.compare(_chr(r), _chr(r.replace("/", " / "))).equal


def test_a_list_keeps_the_printed_precision_of_its_text() -> None:
    # a list against a vector is reshaped, not written in canonical form, first
    _flagged(_list(_chr("p = 0.050")), _chr("p = 0.054"))
    _flagged(_list(_chr("p = 0.050"), _chr("x")), _chr("p = 0.054", "x"))
    assert canon.compare(_list(_chr("p = 0.05")), _chr("p = 0.054")).equal


def test_a_traffic_light_is_compared_exactly() -> None:
    # FIDELITY.md §2.3: no Band B for traffic lights
    r = _output("m", _TABLE, light="green")
    for light in ("gre en", "green ", None):
        c = _flagged(r, _output("m", _TABLE, light=light))  # type: ignore[arg-type]
        assert c.paths == ["traffic_light[]"]
    # and kept as it is in a form
    spaced = _output("m", _TABLE, light="green ")
    assert _element(canon.form(spaced), "traffic_light") == _chr("green ")


def test_a_long_whole_number_keeps_every_digit_in_a_digest() -> None:
    # a digest keeps 10 significant digits of a double, and an ISBN has 13
    r = _df(k=_chr("x"), isbn=_chr("9780123456789"))
    _flagged(r, _df(k=_chr("x"), isbn=_chr("9780123456788")))
    assert canon.form(_chr("9780123456789")) == _chr("9780123456789")
    assert canon.form(_chr("1234567890")) == _dbl(1234567890.0)


def test_a_number_near_zero_keeps_its_digits_in_a_digest() -> None:
    # a digest holds a double this close to 0 as 0
    _flagged(_df(k=_chr("x"), p=_chr("2.2e-16")), _df(k=_chr("x"), p=_chr("1e-13")))
    _flagged(_chr("1e-13"), _chr("-1e-13"))
    assert canon.form(_chr("2.2e-16")) == _chr("2.2e-16")


def test_cells_are_formed_as_a_column() -> None:
    # a cell that is only a number stays text when its column is text
    records = _list(
        _list(_chr("t"), _chr("2"), names=["name", "value"]),
        _list(_chr("CI"), _chr("[1, 2]"), names=["name", "value"]),
    )
    frame = _df(name=_chr("t", "CI"), value=_chr("2", "[1, 2]"))
    assert canon.form(records) == canon.form(frame)
    assert sorted(_element(canon.form(frame), "value")["v"]) == ["2", "[ 1 , 2 ]"]
    cells = _df(k=_chr("a", "b"), value=_list(_chr("2"), _chr("x")))
    assert canon.compare(cells, _df(k=_chr("a", "b"), value=_chr("2", "x"))).equal
    assert canon.form(cells) == canon.form(_df(k=_chr("a", "b"), value=_chr("2", "x")))


# -- profiles and the flip audit --------------------------------------------------------


#: a pair each rule alone makes equal (the Band B category it removes, FIDELITY.md §4.3)
_ONE_RULE = {
    "order": (_TABLE, _rows(_TABLE, [2, 0, 1])),
    "extra_column": (
        _TABLE,
        {**_TABLE, "names": [*_TABLE["names"], "x"], "v": [*_TABLE["v"], _lgl(True, None, False)]},
    ),
    "dtype": (_df(k=_chr("a"), p=_chr("0.05")), _df(k=_chr("a"), p=_dbl(0.05))),
    "na": (_chr("a", ""), _chr("a", None)),
    "number_text": (_chr("p = 0.050, 2.2e-16"), _chr("p = .05, 2.2 × 10^-16")),
    "whitespace": (_chr("Hamilton1964, p.3"), _chr("Hamilton 1964, p. 3")),
}


@pytest.mark.parametrize("rule", list(canon.RULES))
def test_each_rule_frees_its_category(rule: str) -> None:
    r, p = _ONE_RULE[rule]
    assert canon.compare(r, p).equal
    assert canon.compare(r, p, canon.profile().only(rule)).equal
    assert not canon.compare(r, p, _without(rule)).equal
    assert not canon.compare(r, p, canon.profile().only()).equal


def test_every_rule_has_its_pair() -> None:
    assert set(_ONE_RULE) == set(canon.RULES)


def test_without_rules_canon_is_the_old_comparison() -> None:
    frame = _df(k=_chr("a", "b"), v=_dbl(1, 2))
    pairs: list[tuple[dict, dict, Options]] = [
        (_chr("a"), _chr("b"), Options()),
        (_chr("a  b"), _chr("a b"), Options()),
        (_chr("a  b"), _chr("a b"), Options(ws=True)),
        (_dbl(1.0), _dbl(1.0 + 1e-12), Options()),
        (_int(1), _dbl(1.0), Options()),
        (_chr("a", None), _chr("a", ""), Options()),
        (_list(_chr("a"), _chr("b")), _chr("a", "b"), Options()),
        (frame, _rows(frame, [1, 0]), Options()),
        (frame, _rows(frame, [1, 0]), Options(unordered_root=True)),
        (frame, {**frame, "names": ["v", "k"], "v": frame["v"][::-1]}, Options()),
        (frame, {**frame, "names": ["v", "k"], "v": frame["v"][::-1]}, Options(col_order=False)),
        (frame, {**frame, "v": [frame["v"][0], _dbl(1, 3)]}, Options(ignore={"v"})),
        (_output("m", frame), _output("m", frame, light="red"), Options()),
        (
            _output("m", frame, summary="p = .05"),
            _output("m", frame, summary="p = 0.05"),
            Options(),
        ),
    ]
    for r, p, options in pairs:
        c = canon.compare(r, p, canon.Profile().only(), options)
        assert (c.problems, c.paths) == compare_paths(r, p, options), (r, p, options)
        assert c.info == []


def test_only_runs_one_rule() -> None:
    reordered = _rows(_TABLE, [2, 1, 0])
    for rule in canon.RULES:
        prof = canon.profile().only(rule)
        assert prof.rules == {rule}
        assert canon.compare(_TABLE, reordered, prof).equal is (rule == "order")
    with pytest.raises(ValueError, match="unknown canon rules"):
        canon.profile().only("typos")


@pytest.mark.parametrize(
    ("r_function", "ordered"),
    [
        ("text_search", True),
        ("metacheck::text_search", True),
        ("extract_urls", True),
        ("dplyr::bind_rows", True),
        ("metacheck:::text_search", False),  # a private helper is no façade
        ("function(x) text_search(x)", False),
        ("search_text", False),
        (None, False),
    ],
)
def test_facades(r_function: str | None, ordered: bool) -> None:
    prof = canon.profile({"r": r_function})
    assert (prof.ordered, prof.na_strict) == (ordered, ordered)


def test_a_module_case_is_no_facade() -> None:
    prof = canon.profile({"r": "text_search", "module": "funding_check_oi"})
    assert not prof.ordered


# -- configuration ----------------------------------------------------------------------


def test_committed_config() -> None:
    config = canon.load_config()
    # FIDELITY.md §4.2
    assert config.row_keys["funding_check_oi"] == ("paper_id", "text")
    assert config.row_keys["stat_check"] == ("paper_id", "raw")
    for name in ("text_search", "paper_table", "json_expand", "extract_*", "bind_rows", "count"):
        assert config.facades[name] == canon.Facade(ordered=True, na_strict=True), name


def _tables(x: Any, module: str) -> list[dict]:
    """The ``table`` data frames of *module*'s outputs in the canonical value *x*."""
    if not isinstance(x, dict):
        return []
    out = []
    if x.get("t") == "module_output" and canon._module_name(x) == module:
        table = _element(x, "table") if "table" in (x.get("names") or []) else None
        if isinstance(table, dict) and table.get("t") == "df" and table.get("names"):
            out.append(table)
    if isinstance(x.get("v"), list):
        for el in x["v"]:
            out.extend(_tables(el, module))
    return out


@pytest.mark.parametrize("module", sorted(canon.load_config().row_keys))
def test_declared_keys_are_columns_of_the_modules_table(module: str) -> None:
    key = canon.load_config().row_keys[module]
    tables = []
    for case in load_cases():
        if case.spec.get("module") == module and case.golden_path.exists():
            golden = json.loads(case.golden_path.read_text(encoding="utf-8"))
            tables.extend(_tables(golden.get("value"), module))
    # a table can lack some key columns (power's without its model): the key is
    # then the columns there are
    assert any(set(key) <= set(table["names"]) for table in tables), module


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"modules": {}}, "unknown tables"),
        ({"module": {"m": {"row_key": "paper_id"}}}, r"\[module.m\] is row_key"),
        ({"module": {"m": {"row_key": []}}}, r"\[module.m\] is row_key"),
        ({"module": {"m": {"row_key": ["a", "a"]}}}, r"\[module.m\] is row_key"),
        ({"module": {"m": {"row_key": ["a"], "ordered": True}}}, r"\[module.m\] is row_key"),
        ({"facade": {"f": {"sorted": True}}}, r"\[facade.f\] sets ordered"),
        ({"facade": {"f": {"ordered": "yes"}}}, r"\[facade.f\] sets ordered"),
        ({"facade": {"f": {}}}, r"\[facade.f\] sets ordered"),
        ({"module": ["m"]}, "module is a table"),
        ({"facade": 1}, "facade is a table"),
    ],
)
def test_config_is_validated(data: dict, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        canon.parse_config(data, "canon.toml")


def test_config_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "canon.toml"
    path.write_text('[module.m]\nrow_key = ["id"]\n\n[facade."read_*"]\nordered = true\n')
    config = canon.load_config(path)
    assert config.row_keys == {"m": ("id",)}
    assert config.facade("read_csv") == canon.Facade(ordered=True)
    assert config.facade("readr::read_csv").ordered


# -- idempotence over the committed goldens (R's side) -----------------------------------


_AREAS = sorted(d.name for d in GOLDEN_DIR.iterdir() if d.is_dir())


def test_every_golden_is_in_an_area() -> None:
    in_areas = {p for area in _AREAS for p in (GOLDEN_DIR / area).glob("*.json")}
    assert in_areas == set(GOLDEN_DIR.rglob("*.json"))
    assert len(in_areas) > 9000


@pytest.mark.parametrize("area", _AREAS)
def test_form_is_idempotent_on_the_goldens(area: str) -> None:
    specs = {c.golden_path: c.spec for c in load_cases(area)}
    bad = []
    values = 0
    for path in sorted((GOLDEN_DIR / area).glob("*.json")):
        golden = json.loads(path.read_text(encoding="utf-8"))
        if not golden.get("ok"):  # R failed: there is no value
            continue
        values += 1
        prof = canon.profile(specs.get(path))
        once = canon.form(golden["value"], prof)
        if canon.form(once, prof) != once:
            bad.append(f"{path.name}: form(form(x)) != form(x)")
        c = canon.compare(golden["value"], once, prof)
        if not c.equal:
            bad.append(f"{path.name}: x differs from form(x): {c.problems[:2]}")
    assert not bad, "\n".join(bad)
    assert values or all(
        not json.loads(p.read_text(encoding="utf-8")).get("ok")
        for p in (GOLDEN_DIR / area).glob("*.json")
    )
