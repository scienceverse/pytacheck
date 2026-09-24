"""Python side of the statout_match parity cases (and shared test builders).

The R side of each case is an R expression (see ``gen_parity_cases.py``);
these helpers compute the same thing with pytacheck. ``NA`` is spelled
``"__NA__"`` in case arguments (YAML ``null`` is R ``NULL``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NA = "__NA__"


def na(x: Any) -> Any:
    """Replace the ``"__NA__"`` marker by ``pd.NA`` (recursively)."""
    if isinstance(x, str) and x == NA:
        return pd.NA
    if isinstance(x, list):
        return [na(v) for v in x]
    if isinstance(x, dict):
        return {k: na(v) for k, v in x.items()}
    return x


def _none_na(x: Any) -> Any:
    if isinstance(x, list):
        return [_none_na(v) for v in x]
    return None if x is pd.NA else x


def read_lines(path: str | Path) -> list[str]:
    """R ``readLines()``."""
    text = (ROOT / path).read_text(encoding="utf-8")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------- stato-map


def vocab(name: str) -> Any:
    """A vocabulary table as ``{names, values}`` (``_STATO_BY_CALL``: per call)."""
    from pytacheck.statout import stato_map

    tbl = getattr(stato_map, name)
    if isinstance(tbl, str):
        return tbl
    if name == "_STATO_BY_CALL":
        return {k: {"names": list(v), "values": list(v.values())} for k, v in tbl.items()}
    return {"names": list(tbl), "values": list(tbl.values())}


def vocab_keys() -> list[str]:
    from pytacheck.statout.stato_map import _MC_STAT_MAP, _STATO_MAP

    return [*_STATO_MAP, *_MC_STAT_MAP]


def stato_batch(headers: Sequence[Any] | None = None, call_fn: Any = None, vocab: str = "") -> Any:
    """``lapply(headers, stato_type_column, call_fn = call_fn)``.

    ``vocab``: ``"keys"`` (every vocabulary key), ``"upper"`` (the keys,
    upper-cased and padded), ``"variant"`` (the keys with ``[gg]``).
    """
    from pytacheck.statout.stato_map import stato_type_column

    hs = _headers(headers, vocab)
    return [stato_type_column(h, call_fn) for h in hs]


def _headers(headers: Sequence[Any] | None, vocab_: str) -> list[Any]:
    if vocab_ == "keys":
        return vocab_keys()
    if vocab_ == "upper":
        return [f"  {k.upper()} " for k in vocab_keys()]
    if vocab_ == "variant":
        return [f"{k}[gg]" for k in vocab_keys()]
    return list(na(headers or []))


def stato_pairs(headers: Sequence[Any], calls: Sequence[Any]) -> Any:
    """``mapply(stato_type_column, headers, calls)``."""
    from pytacheck.statout.stato_map import stato_type_column

    return [stato_type_column(h, c) for h, c in zip(na(headers), na(calls), strict=True)]


def spv_batch(labels: Sequence[Any] | None = None, vocab: str = "") -> Any:
    from pytacheck.statout.stato_map import _spv_stato_type_label

    return [_spv_stato_type_label(h) for h in _headers(labels, vocab)]


def by_call_pairs(keys: Sequence[Any], calls: Sequence[Any]) -> Any:
    from pytacheck.statout.stato_map import _stato_by_call

    return [_stato_by_call(k, c) for k, c in zip(na(keys), na(calls), strict=True)]


def strip_variant(key: Sequence[Any]) -> Any:
    from pytacheck.statout.stato_map import _stato_strip_variant

    return _stato_strip_variant(_none_na(na(list(key))))


# ---------------------------------------------------------------- match-reported


def batch(fn: str, x: Sequence[Any]) -> Any:
    """``lapply(x, <fn>)`` for a ``pytacheck.statout`` internal."""
    import importlib

    module, _, name = fn.rpartition(".")
    f = getattr(importlib.import_module(f"pytacheck.statout.{module}"), name)
    return [f(v) for v in na(list(x))]


def stat_family(x: Sequence[Any] | None = None, vocab: str = "") -> Any:
    from pytacheck.statout.match_reported import _stat_family

    return _stat_family(_headers(x, vocab))


def share_pairs(a: Sequence[Any], b: Sequence[Any]) -> Any:
    from pytacheck.statout.match_reported import _sites_share_variable

    return [_sites_share_variable(x, y) for x, y in zip(na(a), na(b), strict=True)]


def eq_frame(cols: Mapping[str, Sequence[Any]], types: Mapping[str, str]) -> pd.DataFrame:
    """An ``eq``-like data frame (``types``: ``int``/``dbl``/``chr`` per column)."""
    dt = {"int": "Int64", "dbl": "float64", "chr": "string"}
    return pd.DataFrame(
        {
            k: pd.Series(_none_na(na(list(v))), dtype=dt[types.get(k, "chr")])
            for k, v in cols.items()
        }
    )


def test_paper(texts: Sequence[str]) -> Any:
    import pytacheck as pc

    return pc.test_paper(list(texts))


def recompose(
    source: str, texts: Sequence[str] | None = None, cols: Any = None, types: Any = None
) -> Any:
    """``.recompose_eq()`` of ``extract_eq(<paper>)``, a data frame, or ``NULL``."""
    from pytacheck.statout.match_reported import _recompose_eq

    return _recompose_eq(_eq_source(source, texts, cols, types))


def _eq_source(source: str, texts: Any, cols: Any, types: Any) -> Any:
    import pytacheck as pc
    from pytacheck.text.extract import extract_eq

    if source == "demo":
        return extract_eq(pc.demopaper())
    if source == "texts":
        return extract_eq(test_paper(texts))
    if source == "df":
        return eq_frame(cols, types or {})
    return None


def tests_from_extract(texts: Sequence[str] | None = None, source: str = "texts") -> Any:
    import pytacheck as pc
    from pytacheck.statout.match_reported import _tests_from_extract
    from pytacheck.text.extract_tests import extract_tests

    paper = pc.demopaper() if source == "demo" else test_paper(texts or [])
    return _tests_from_extract(extract_tests(paper))


# ---------------------------------------------------------------- outputs


def long_of(spec: Mapping[str, Any]) -> pd.DataFrame:
    """A ``stat_results_long()`` table from a fixture file (or a literal frame)."""
    from pytacheck.statout.r_output import read_r_output
    from pytacheck.statout.stat_output import stat_results_long
    from pytacheck.statout.stat_tables import read_stat_tables

    kind = spec["kind"]
    if kind == "file":
        tabs = read_stat_tables(str(ROOT / spec["path"]))
        return stat_results_long(tabs, source_file=spec["source_file"])
    if kind == "rout":
        tabs = read_r_output(
            read_lines(spec["path"]),
            source_label=spec["source_file"],
            code_lines=read_lines(spec["code"]),
        )
        return stat_results_long(tabs, source_file=spec["source_file"])
    if kind == "df":
        cols = spec["cols"]
        types = spec.get("types", {})
        return pd.DataFrame(
            {
                k: (
                    pd.Series([float(x) for x in v], dtype="float64")
                    if types.get(k) == "dbl"
                    else pd.Series(_none_na(na(list(v))), dtype="string")
                )
                for k, v in cols.items()
            }
        )
    raise ValueError(kind)


def output_of(spec: Any) -> Any:
    """The ``output`` argument: a long table, a stat_output list, or ``None``."""
    if spec is None:
        return None
    if isinstance(spec, list):
        return [{"file": s.get("source_file"), "long": long_of(s)} for s in spec]
    out = long_of(spec)
    if spec.get("drop"):
        out = out.drop(columns=spec["drop"])
    return out


def table_paper(texts: Sequence[str], section_ids: Sequence[Any], tables: Sequence[Any]) -> Any:
    """A test paper with text section ids and a ``table`` table with ``contents``."""
    import pytacheck as pc

    p = pc.test_paper(list(texts))
    txt = p.text.copy()
    txt["section_id"] = pd.Series(_none_na(na(list(section_ids))), dtype="float64")
    p.text = txt
    p.table = pd.DataFrame(
        {
            "table_id": pd.Series([t["table_id"] for t in tables], dtype="Int64"),
            "section_id": pd.Series(
                [_none_na(na(t.get("section_id"))) for t in tables], dtype="Int64"
            ),
            "html": pd.Series([None] * len(tables), dtype="string"),
            "caption": pd.Series([None] * len(tables), dtype="string"),
            "page_number": pd.Series([None] * len(tables), dtype="Int64"),
            "contents": pd.Series(
                [
                    None if t.get("contents") is None else _none_na(na(t["contents"]))
                    for t in tables
                ],
                dtype=object,
            ),
        }
    )
    return p


def paper_of(spec: Mapping[str, Any]) -> Any:
    import pytacheck as pc
    from pytacheck.text.extract import extract_eq
    from pytacheck.text.extract_tests import extract_tests

    kind = spec["kind"]
    if kind == "demo":
        return pc.demopaper()
    if kind == "texts":
        return test_paper(spec["texts"])
    if kind == "read":
        return pc.read(ROOT / spec["path"])
    if kind == "eq":
        return extract_eq(test_paper(spec["texts"]))
    if kind == "extract_tests":
        return extract_tests(test_paper(spec["texts"]))
    if kind == "table_paper":
        return table_paper(spec["texts"], spec["section_ids"], spec["tables"])
    if kind == "null":
        return None
    if kind == "paperlist":
        return pc.read(ROOT / spec["path"])
    raise ValueError(kind)


def match(
    paper: Mapping[str, Any],
    output: Any,
    include_tables: bool = False,
    min_components: int = 1,
    what: str = "table",
) -> Any:
    """``match_reported_output()`` (``what = "summary"``: its summary attribute)."""
    from pytacheck.statout.match_reported import match_reported_output

    res = match_reported_output(
        paper_of(paper),
        output_of(output),
        include_tables=include_tables,
        min_components=min_components,
    )
    return res.attrs["summary"] if what == "summary" else res


# ---------------------------------------------------------------- match-table


def typed_cells(content: Any, caption: Any = None) -> Any:
    from pytacheck.statout.match_table import _table_typed_cells

    return _table_typed_cells(_none_na(na(content)), _none_na(na(caption)))


def matrix_cols(content: Any) -> Any:
    from pytacheck.statout.match_table import _table_matrix_cols

    return _table_matrix_cols(_none_na(na(content)))


def tests_one(table_id: Any, content: Any, caption: Any = None) -> Any:
    from pytacheck.statout.match_table import _table_tests_one

    return _table_tests_one(table_id, _none_na(na(content)), _none_na(na(caption)))


def table_tests(paper: Mapping[str, Any]) -> Any:
    from pytacheck.statout.match_table import _table_tests

    return _table_tests(paper_of(paper))


def table_caption(paper: Mapping[str, Any], section_ids: Sequence[Any]) -> Any:
    from pytacheck.statout.match_table import _table_caption

    p = paper_of(paper)
    return [_table_caption(p, s) for s in na(list(section_ids))]
