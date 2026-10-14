"""Canonical JSON (``canonical.R``'s encoding of R values) back to Python values.

The inverse of what the parity tooling compares, for showing R's results with pytacheck's
report: a data frame becomes a :class:`pandas.DataFrame`, a module output a
:class:`~metacheck.module.ModuleOutput`, a named list a ``dict`` and an unnamed one a
``list``. R has no scalars, so an unnamed vector of length one is its one value, and a
longer one a ``list`` (a named vector is a ``dict``). In a data frame, ``NA`` is ``None``
in a text column and a missing value of pandas' nullable types in the others.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["decode", "module_output"]

_DOUBLES = {"NaN": math.nan, "Inf": math.inf, "-Inf": -math.inf}
_ATOMIC = ("chr", "dbl", "int", "lgl", "cplx", "raw")
#: the marker run.R puts where a module's report had a table (``scroll_table()``)
TABLE_MARK = re.compile(r"\n?<!-- pytacheck-r-table (\d+) -->\n?")
#: the elements of an R module output that are fields of ModuleOutput
_FIELDS = ("module", "title", "section", "table", "report", "traffic_light", "summary_text")


def _double(e: Any) -> float | None:
    if e is None:
        return None
    if isinstance(e, str):
        return _DOUBLES[e]
    return float(e)


def _values(x: dict[str, Any]) -> list[Any]:
    """The elements of an atomic vector as Python values (``NA`` is ``None``)."""
    t, v = x["t"], x.get("v", [])
    if t == "dbl":
        return [_double(e) for e in v]
    if t == "cplx":
        return [
            None if e is None else complex(_double(e[0]) or 0.0, _double(e[1]) or 0.0) for e in v
        ]
    if t == "lgl":
        return [None if e is None else bool(e) for e in v]
    if t == "int":
        return [None if e is None else int(e) for e in v]
    return list(v)


def _column(x: dict[str, Any], nrow: int) -> pd.Series:
    t = x.get("t")
    if t == "null":
        return pd.Series([None] * nrow, dtype=object)
    if t in _ATOMIC:
        vals = _values(x)
        if t == "dbl":
            return pd.Series([math.nan if e is None else e for e in vals], dtype="float64")
        if t == "int":
            return pd.Series(vals, dtype="Int64" if None in vals else "int64")
        if t == "lgl":
            return pd.Series(vals, dtype="boolean" if None in vals else "bool")
        return pd.Series(vals, dtype=object)
    if t == "list":  # a list column: one value per row
        return pd.Series([decode(e) for e in x.get("v", [])], dtype=object)
    return pd.Series([decode(x)] * nrow, dtype=object)


def frame(x: dict[str, Any]) -> pd.DataFrame:
    """A canonical data frame as a DataFrame (duplicated column names are kept, as in R)."""
    nrow = int(x.get("nrow", 0))
    names = [str(n) for n in x.get("names", [])]
    cols = [_column(c, nrow) for c in x.get("v", [])]
    if not cols:
        return pd.DataFrame(index=range(nrow))
    df = pd.concat(cols, axis=1, ignore_index=True)
    df.columns = names
    return df


def _named(names: Sequence[Any] | None, items: list[Any]) -> Any:
    if names is None:
        return items
    return dict(zip((str(n) for n in names), items, strict=True))


def decode(x: Any) -> Any:
    """A canonical value as a Python value (see the module's docstring)."""
    if not isinstance(x, dict) or "t" not in x:
        return x
    t = x["t"]
    if t == "null" or t == "function":
        return None
    if t in _ATOMIC:
        vals = _values(x)
        if x.get("names") is not None:
            return _named(x["names"], vals)
        return vals[0] if len(vals) == 1 else vals
    if t == "df":
        return frame(x)
    if t == "module_output":
        return module_output(x)
    if t in ("list", "paper", "paperlist"):
        return _named(x.get("names"), [decode(e) for e in x.get("v", [])])
    if t == "matrix":
        dim = [int(d) for d in x.get("dim", [])]
        return np.array(_values(x["v"]), dtype=object).reshape(dim, order="F")
    return x.get("repr")


def _report(value: Any, tables: Sequence[Any]) -> Any:
    """A module's report with each table marker replaced by its table block."""
    if isinstance(value, str):
        if not TABLE_MARK.search(value):
            return value
        parts = TABLE_MARK.split(value)
        out: list[Any] = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    out.append(part)
            else:
                out.append(_table(tables, int(part)))
        return out[0] if len(out) == 1 and isinstance(out[0], str) else out
    if isinstance(value, pd.DataFrame):
        from metacheck.report.blocks import scroll_table

        return scroll_table(value)
    if isinstance(value, list):
        out = []
        for item in value:
            got = _report(item, tables)
            out.extend(got if isinstance(got, list) else [got])
        return out
    return value


def _table(tables: Sequence[Any], number: int) -> Any:
    from metacheck.report.blocks import scroll_table

    if not 1 <= number <= len(tables):
        return ""
    spec = tables[number - 1]
    colwidths = spec.get("colwidths", "auto")
    return scroll_table(
        spec.get("table"),
        colwidths=colwidths,
        maxrows=int(spec.get("maxrows", 2)),
        escape=bool(spec.get("escape", False)),
        column=str(spec.get("column", "body")),
    )


def module_output(x: dict[str, Any], tables: Sequence[Any] = ()) -> Any:
    """A canonical module output as a :class:`~metacheck.module.ModuleOutput`.

    ``tables`` are the tables run.R kept (see :data:`TABLE_MARK`), already decoded.
    Elements beyond the standard ones (``na_replace``, a module's own) are its ``extras``.
    """
    from metacheck.module import ModuleOutput

    elements = _named(x.get("names") or [], [decode(e) for e in x.get("v", [])])
    fields = {k: elements.pop(k, None) for k in _FIELDS}
    summary_table = elements.pop("summary_table", None)
    elements.pop("paper", None)
    elements.pop("prev_outputs", None)
    return ModuleOutput(
        module=str(fields["module"] or ""),
        title=str(fields["title"] if fields["title"] is not None else fields["module"] or ""),
        section=fields["section"],
        table=fields["table"],
        report=_report(fields["report"] if fields["report"] is not None else "", tables),
        traffic_light=str(fields["traffic_light"] or "info"),
        summary_text=fields["summary_text"],
        summary_table=summary_table if isinstance(summary_table, pd.DataFrame) else None,
        extras=elements,
    )
