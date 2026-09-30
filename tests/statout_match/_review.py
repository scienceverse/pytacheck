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
    if kind == "eq_of":
        from metacheck.text.extract import extract_eq

        return extract_eq(paper_of(spec["paper"]))
    if kind == "eq_paper":
        from metacheck.text.extract import extract_eq

        p = h.test_paper(spec["texts"])
        p.eq = extract_eq(h.test_paper(spec["eq_texts"]))
        return p
    p = h.paper_of(spec)
    if kind == "table_paper" and spec.get("drop"):
        p.table = p.table.drop(columns=spec["drop"])
    if spec.get("drop_text"):
        p.text = p.text.drop(columns=spec["drop_text"])
    return p


def match(
    paper: Mapping[str, Any],
    output: Any,
    include_tables: Any = False,
    min_components: int = 1,
    what: str = "table",
) -> Any:
    """``match_reported_output()`` (``what = "summary"``: its summary attribute)."""
    from metacheck.statout.match_reported import match_reported_output

    res = match_reported_output(
        paper_of(paper),
        output_of(output),
        include_tables=include_tables,
        min_components=min_components,
    )
    return res.attrs["summary"] if what == "summary" else res


def output_of(spec: Any) -> Any:
    """The ``output`` argument; ``{"kind": "derived", "paper": ...}`` builds one."""
    if isinstance(spec, Mapping) and spec.get("kind") == "derived":
        return derived_long(spec["paper"])
    if isinstance(spec, Mapping) and spec.get("kind") == "literal":
        return list(spec["value"])
    return h.output_of(spec)


def derived_long(paper: Mapping[str, Any]) -> pd.DataFrame:
    """A synthetic output table derived from a paper's own ``extract_eq()`` rows.

    Every 4th eq row is dropped and every 5th value is perturbed (a digit
    appended), sites group three sentences (``site_<text_id %/% 3>_<grp_id>``),
    ``df`` rows go to a jamovi-like ``..._residuals`` site, and every row of a
    7-sentence block shares a ``model_ref`` -- so real reported text exercises
    partial matches, the residual union, model sites and the regrouping.
    """
    from metacheck.text.extract import extract_eq

    eq = extract_eq(paper_of(paper))
    tid = [None if pd.isna(x) else int(x) for x in eq["text_id"]]
    gid = [None if pd.isna(x) else int(x) for x in eq["grp_id"]]
    lhs = [None if pd.isna(x) else str(x) for x in eq["lhs"]]
    rhs = [None if pd.isna(x) else str(x) for x in eq["rhs"]]

    def na(x: Any) -> str:
        return "NA" if x is None else str(x)

    rows = []
    for i in range(1, len(eq) + 1):
        if i % 4 == 0:
            continue
        t, g, stat, v = tid[i - 1], gid[i - 1], lhs[i - 1], rhs[i - 1]
        if i % 5 == 0:
            v = f"{na(v)}7"
        block = None if t is None else t // 3
        site = f"site_{na(block)}_{'residuals' if stat == 'df' else na(g)}"
        rows.append(
            {
                "source_file": "derived.R",
                "test_id": site,
                "analysis": f"an{na(g)}",
                "row_label": f"variable{na(None if t is None else t % 4)}",
                "statistic": stat,
                "value": v,
                "model_ref": f"m{na(None if t is None else t // 7)}" if i % 2 else None,
            }
        )
    cols = ["source_file", "test_id", "analysis", "row_label", "statistic", "value", "model_ref"]
    return pd.DataFrame({c: pd.Series([r[c] for r in rows], dtype="string") for c in cols})


def table_tests(paper: Mapping[str, Any]) -> Any:
    from metacheck.statout.match_table import _table_tests

    return _table_tests(paper_of(paper))


def fmt_values(values: list[Any]) -> list[str]:
    """``format(<value>, trim = TRUE)`` as ``match_reported_output()`` prints it."""
    from metacheck.statout.match_reported import _fmt_comp

    return [_fmt_comp({"name": "x", "censored": "", "value": v})[2:] for v in values]


def table_tests_or_error(paper: Mapping[str, Any]) -> Any:
    """``tryCatch(.table_tests(paper), error = function(e) "error")``."""
    try:
        return table_tests(paper)
    except Exception:
        return "error"


def table_caption(paper: Mapping[str, Any], section_ids: list[Any]) -> Any:
    from metacheck.statout.match_table import _table_caption

    p = paper_of(paper)
    return [_table_caption(p, None if s == NA else s) for s in section_ids]
