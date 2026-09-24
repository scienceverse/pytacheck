"""The bibr paper JSON schema and R-faithful column coercion.

metacheck's paper object *is* bibr's JSON schema (``inst/schema/paper.json``)
held as a list of data frames. This module loads that schema and reproduces
``.paper_coerce()``: every column the schema knows is converted to the
schema's type with R's ``as.character``/``as.integer``/``as.double``/
``as.logical`` rules; unknown columns are left alone.

Canonical pandas dtypes for schema types:

=========  ===================  =========================================
schema     pandas dtype         notes
=========  ===================  =========================================
string     ``"string"``         missing is ``pd.NA``
integer    ``"Int64"``          nullable integer (R ``NA_integer_``)
number     ``float64``          missing is ``NaN`` (R ``NA_real_``)
boolean    ``"boolean"``        nullable, Kleene logic like R
array      ``object``           cells are Python lists
object     ``object``           cells are dicts / lists of dicts
=========  ===================  =========================================
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Iterable, Sequence
from importlib import resources
from typing import Any

import numpy as np
import orjson
import pandas as pd

from pytacheck._r.base import as_character
from pytacheck._r.regex import is_na

__all__ = [
    "SCHEMA_DTYPES",
    "coerce_column",
    "coerce_table",
    "empty_table",
    "infer_column",
    "load_schema",
    "table_columns",
    "table_names",
]

SCHEMA_DTYPES: dict[str, Any] = {
    "string": "string",
    "integer": "Int64",
    "number": "float64",
    "boolean": "boolean",
    "array": object,
    "object": object,
}


@functools.cache
def load_schema() -> dict[str, Any]:
    """The bibr paper JSON schema bundled with this release."""
    raw = resources.files("pytacheck.resources.schema").joinpath("paper.json").read_bytes()
    schema: dict[str, Any] = orjson.loads(raw)
    return schema


@functools.cache
def table_names() -> tuple[str, ...]:
    """Every table the schema defines (``properties`` minus ``paper_id``)."""
    return tuple(k for k in load_schema()["properties"] if k != "paper_id")


@functools.cache
def required_tables() -> tuple[str, ...]:
    """Tables every paper object carries (schema ``required`` minus ``paper_id``)."""
    return tuple(k for k in load_schema()["required"] if k != "paper_id")


@functools.cache
def table_columns(table: str) -> tuple[tuple[str, str], ...]:
    """``(column, schema type)`` pairs for *table*, in schema order.

    The schema type is the *first* listed type (``["integer", "null"]`` ->
    ``"integer"``), exactly like ``.paper_coerce()``.
    """
    schema = load_schema()
    prop = schema["properties"].get(table)
    if prop is None:
        return ()
    ref = prop.get("$ref") or (prop.get("items") or {}).get("$ref")
    if not ref:
        return ()
    definition = schema["$defs"][ref.split("/")[2]]
    cols = []
    for name, spec in definition.get("properties", {}).items():
        typ = spec.get("type", "string")
        if isinstance(typ, list):
            typ = typ[0]
        cols.append((name, typ))
    return tuple(cols)


@functools.cache
def _column_types(table: str) -> dict[str, str]:
    return dict(table_columns(table))


def empty_table(table: str) -> pd.DataFrame:
    """A zero-row table with every schema column, as ``paper()`` builds them."""
    return pd.DataFrame(
        {
            name: pd.Series([], dtype=SCHEMA_DTYPES.get(typ, object))
            for name, typ in table_columns(table)
        }
    )


# ---------------------------------------------------------------------------
# R coercion rules
# ---------------------------------------------------------------------------

_R_NUMBER = re.compile(
    r"^\s*[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|0[xX][0-9a-fA-F]+|Inf|inf|NaN)\s*$"
)
_TRUE = {"TRUE", "true", "T", "True"}
_FALSE = {"FALSE", "false", "F", "False"}


def _r_as_double(v: Any) -> float:
    if is_na(v):
        return math.nan
    if isinstance(v, bool | np.bool_):
        return 1.0 if v else 0.0
    if isinstance(v, int | float | np.integer | np.floating):
        return float(v)
    s = str(v)
    if not _R_NUMBER.match(s):
        return math.nan
    s = s.strip()
    if s.lstrip("+-").lower().startswith("0x"):
        sign = -1 if s.startswith("-") else 1
        return float(sign * int(s.lstrip("+-"), 16))
    if s.lstrip("+-") in ("Inf", "inf"):
        return -math.inf if s.startswith("-") else math.inf
    return float(s)


def _r_as_integer(v: Any) -> Any:
    x = _r_as_double(v)
    if math.isnan(x) or math.isinf(x) or abs(x) > 2147483647:
        return pd.NA
    return math.trunc(x)


def _r_as_logical(v: Any) -> Any:
    if is_na(v):
        return pd.NA
    if isinstance(v, bool | np.bool_):
        return bool(v)
    if isinstance(v, int | float | np.integer | np.floating):
        return pd.NA if math.isnan(float(v)) else float(v) != 0
    s = str(v)
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return pd.NA


def _as_list_cell(v: Any) -> Any:
    if isinstance(v, list):
        return v
    if isinstance(v, tuple | np.ndarray):
        return list(v)
    if is_na(v):
        return [None]
    return [v]


def coerce_column(values: Sequence[Any] | pd.Series, schema_type: str) -> pd.Series:
    """Coerce *values* to *schema_type* with R semantics."""
    series = values if isinstance(values, pd.Series) else pd.Series(list(values), dtype=object)
    dtype = SCHEMA_DTYPES.get(schema_type, object)
    if schema_type in ("array", "object"):
        if series.dtype == object:
            return series
        return pd.Series([_as_list_cell(v) for v in series], index=series.index, dtype=object)
    if str(series.dtype) == str(dtype):
        return series
    if schema_type == "string":
        out = [as_character(v) for v in series]
        return pd.Series(out, index=series.index, dtype="string")
    if schema_type == "integer":
        return pd.Series([_r_as_integer(v) for v in series], index=series.index, dtype="Int64")
    if schema_type == "number":
        return pd.Series([_r_as_double(v) for v in series], index=series.index, dtype="float64")
    if schema_type == "boolean":
        return pd.Series([_r_as_logical(v) for v in series], index=series.index, dtype="boolean")
    return series


def infer_column(values: Sequence[Any]) -> pd.Series:
    """Infer a column type from parsed JSON like ``jsonlite`` simplification.

    all-null -> logical ``NA``; bools -> logical; whole numbers written
    without a decimal point -> integer; any other number -> double; strings
    (possibly mixed with numbers/bools) -> character; lists/dicts -> list.
    """
    kinds: set[str] = set()
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            kinds.add("lgl")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            kinds.add("dbl")
        elif isinstance(v, str):
            kinds.add("chr")
        else:
            kinds.add("list")
    if not kinds or kinds == {"lgl"}:
        return pd.Series(list(values), dtype="boolean")
    if "list" in kinds:
        return pd.Series(list(values), dtype=object)
    if "chr" in kinds:
        return pd.Series([as_character(v) for v in values], dtype="string")
    if kinds == {"int"} and all(v is None or abs(v) <= 2147483647 for v in values):
        return pd.Series(list(values), dtype="Int64")
    return pd.Series([math.nan if v is None else float(v) for v in values], dtype="float64")


def coerce_table(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """``.paper_coerce()`` for one table: coerce every schema column present."""
    types = _column_types(table)
    for col in df.columns:
        typ = types.get(col)
        if typ is not None:
            df[col] = coerce_column(df[col], typ)
    return df


def records_to_frame(
    table: str, records: Iterable[dict[str, Any]], columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """Build a coerced table from JSON records (``as.data.frame()`` + coerce).

    Columns appear in order of first appearance across *records* (the union
    of keys, as jsonlite builds them) unless *columns* is given.
    """
    rows = list(records)
    if columns is None:
        order: dict[str, None] = {}
        for r in rows:
            for k in r:
                order.setdefault(k, None)
        columns = list(order)
    types = _column_types(table)
    data: dict[str, pd.Series] = {}
    for col in columns:
        vals = [r.get(col) for r in rows]
        typ = types.get(col)
        if typ is None:
            data[col] = infer_column(vals)
        elif typ in ("array", "object"):
            data[col] = pd.Series(vals, dtype=object)
        else:
            data[col] = coerce_column(infer_column(vals), typ)
    if not data:
        return pd.DataFrame(index=range(len(rows)))
    return pd.DataFrame(data)
