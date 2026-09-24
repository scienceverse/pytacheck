"""Python side of the ``statout_match_review`` parity cases.

Builds the extra inputs the review cases need on top of
:mod:`tests.statout_match._helpers`: an ``extract_tests()``-shaped data frame
(``"tt"``), a paper whose ``eq`` is set while its text has no statistics
(``"eq_paper"``), and a table paper with a column removed (``"table_paper"`` +
``drop``). The R side is built by ``gen_review_cases.py`` from the same data.
``NA`` is spelled ``"__NA__"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from tests.statout_match import _helpers as h

NA = h.NA


def _scalar(v: Any) -> Any:
    return None if v == NA else v


def tt_frame(spec: Mapping[str, Any]) -> pd.DataFrame:
    """An ``extract_tests()``-shaped data frame (``components``: list of dicts)."""
    types = spec.get("types", {})
    dt = {"int": "Int64", "dbl": "float64", "chr": "string"}
    cols: dict[str, Any] = {}
    for k, v in spec["cols"].items():
        cols[k] = pd.Series([_scalar(x) for x in v], dtype=dt[types.get(k, "chr")])
    comps = [[{k: _scalar(x) for k, x in c.items()} for c in row] for row in spec["components"]]
    cols["components"] = pd.Series(comps, dtype=object)
    return pd.DataFrame(cols)


def paper_of(spec: Mapping[str, Any]) -> Any:
    kind = spec["kind"]
    if kind == "tt":
        return tt_frame(spec)
    if kind == "eq_paper":
        from pytacheck.text.extract import extract_eq

        p = h.test_paper(spec["texts"])
        p.eq = extract_eq(h.test_paper(spec["eq_texts"]))
        return p
    p = h.paper_of(spec)
    if kind == "table_paper" and spec.get("drop"):
        p.table = p.table.drop(columns=spec["drop"])
    return p


def match(
    paper: Mapping[str, Any],
    output: Any,
    include_tables: Any = False,
    min_components: int = 1,
    what: str = "table",
) -> Any:
    """``match_reported_output()`` (``what = "summary"``: its summary attribute)."""
    from pytacheck.statout.match_reported import match_reported_output

    res = match_reported_output(
        paper_of(paper),
        h.output_of(output),
        include_tables=include_tables,
        min_components=min_components,
    )
    return res.attrs["summary"] if what == "summary" else res


def table_tests(paper: Mapping[str, Any]) -> Any:
    from pytacheck.statout.match_table import _table_tests

    return _table_tests(paper_of(paper))


def fmt_values(values: list[Any]) -> list[str]:
    """``format(<value>, trim = TRUE)`` as ``match_reported_output()`` prints it."""
    from pytacheck.statout.match_reported import _fmt_comp

    return [
        _fmt_comp({"name": "x", "censored": "", "value": v})[2:] for v in values
    ]


def table_tests_or_error(paper: Mapping[str, Any]) -> Any:
    """``tryCatch(.table_tests(paper), error = function(e) "error")``."""
    try:
        return table_tests(paper)
    except Exception:
        return "error"
