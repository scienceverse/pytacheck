"""``json_expand()``: expand a column of JSON text into columns.

Port of ``R/text-json_expand.R``. Each reply is parsed with
:func:`metacheck._json.loads` and flattened into one row per JSON object; the
column types are then guessed from the values. jsonlite's R value model
(vector simplification, dates, deparsed list elements) and
``utils::type.convert()``'s C number grammar are not reproduced (D76).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, cast

import pandas as pd

from metacheck._json import loads
from metacheck._r.regex import gsub
from metacheck._values import as_float, as_str, is_missing

__all__ = ["json_expand"]

_ERROR = "error"
# the strings utils::type.convert() reads as logicals
_LOGICAL = {"TRUE": True, "FALSE": False, "T": True, "F": False}
_INTEGER = re.compile(r"[ \t\n\v\f\r]*[+-]?[0-9]+[ \t\n\v\f\r]*")
_INT64 = 2**63 - 1


def _text(x: Any) -> str:
    """One value as the text of a cell: lists and objects are their leaves joined by ``;``.

    Empty arrays and objects inside are skipped.
    """
    if isinstance(x, dict):
        x = list(x.values())
    if isinstance(x, list):
        return ";".join(_text(v) for v in x if v != [] and v != {})
    s = as_str(x)
    return "NA" if s is None else s  # null in a list is "NA", as R's paste() writes it


def _rows(value: Any) -> list[dict[str, Any]]:
    """The rows one reply contributes: its object(s), or an ``error`` row."""
    if is_missing(value):
        return [{_ERROR: "parsing error"}]
    txt = value if isinstance(value, str) else (as_str(value) or "NA")
    txt = gsub('"null"', "null", txt)
    txt = gsub(".*```json\\s*\n", "", txt)
    txt = gsub("\n\\s*```.*", "", txt)
    try:
        j = loads(txt)
    except ValueError:
        return [{_ERROR: "parsing error"}]
    if j is None or j == [] or j == {}:
        return [{}]
    if isinstance(j, dict):
        objects = [j]
    elif isinstance(j, list) and any(isinstance(v, dict) for v in j):
        if not all(isinstance(v, dict) or v is None for v in j):
            return [{_ERROR: "not a list"}]
        objects = [v or {} for v in j]  # an array of objects: one row each
    else:
        return [{_ERROR: "not a list"}]
    return [{k: _cell(v) for k, v in obj.items()} for obj in objects]


def _cell(v: Any) -> Any:
    """A value of an object: a scalar, or the text of an array or object (``None`` when empty)."""
    if isinstance(v, list | dict):
        return _text(v) if v else None
    return v


def _int(v: Any) -> int | None:
    """*v* as a 64-bit integer, or ``None``."""
    if isinstance(v, str) and _INTEGER.fullmatch(v):
        v = int(v)
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) <= _INT64:
        return v
    return None


def _column(values: list[Any]) -> pd.Series:
    """A column of JSON scalars with a type guessed from them, as ``type.convert()`` would.

    ``"NA"`` and null are missing, a blank string is missing unless the column
    stays text; logicals (``true``, ``"TRUE"``, ``"F"``), integers and numbers
    (also as strings) get their own types, and anything else makes the column text.
    """

    def missing(v: Any) -> bool:
        return v is None or (isinstance(v, str) and not v.strip())

    vals: list[Any] = [None if v is None or v == "NA" else v for v in values]
    present = [v for v in vals if not missing(v)]
    if all(isinstance(v, bool) or v in _LOGICAL for v in present):
        lgl = [pd.NA if missing(v) else v if isinstance(v, bool) else _LOGICAL[v] for v in vals]
        return pd.Series(lgl, dtype="boolean")
    if all(_int(v) is not None for v in present):
        return pd.Series([pd.NA if missing(v) else _int(v) for v in vals], dtype="Int64")
    if all(not isinstance(v, bool) and as_float(v) is not None for v in present):
        return pd.Series([math.nan if missing(v) else as_float(v) for v in vals], dtype="float64")
    return pd.Series([None if v is None else _text(v) for v in vals], dtype="string")


def _add_suffixes(x: list[str], y: list[str], suffix: str) -> list[str]:
    """``dplyr:::add_suffixes()``: suffix the names of *x* found in *y* until none clash."""
    if suffix == "":
        return list(x)
    both = [*y, *x]
    while True:
        seen: set[str] = set()
        dup = []
        for k, nm in enumerate(both):
            if nm in seen:
                dup.append(k)
            seen.add(nm)
        if not dup:
            return both[len(y) :]
        for k in dup:
            both[k] += suffix


def json_expand(
    table: Any,
    col: str | int = "answer",
    suffix: Sequence[str] = ("", ".json"),
) -> pd.DataFrame:
    """Expand a column of JSON text into columns (port of ``json_expand()``).

    Each element is parsed as JSON, after dropping a Markdown ```` ```json ````
    fence: an object becomes one row, an array of objects several rows
    (duplicating the table row), and values that are arrays or nested objects
    become their values joined by ``";"``. Unparsable text gives an ``error``
    column of ``"parsing error"``; JSON that is not an object (a number, a
    string, an array of scalars) gives ``"not a list"``. The ``error`` column
    is dropped when there are no errors. Column types are then guessed from the
    values, so ``"1"`` is a number and ``"FALSE"`` a logical. JSON null is
    missing.

    Parameters
    ----------
    table:
        A DataFrame, or a string / sequence of strings (treated as a table
        with a single ``json`` column).
    col:
        The column to expand: a name, or a 1-based column index as in R.
        When the name is absent the first column is used.
    suffix:
        Suffixes for expanded columns whose names clash with the table's
        (``dplyr::left_join()`` semantics).

    Differences from R: text that is not JSON but looks like a URL or a file
    path is *not* downloaded/read; JSON is read strictly (no comments); a
    repeated key keeps its last value; an array that mixes objects with other
    values is ``"not a list"`` and an empty key an ordinary column (R fails on
    both); nested values are their values joined by ``";"`` (R deparses them,
    ``c(1, 3)``); ``{"$date": ...}`` is not a date; ``"_row"`` and
    ``".temp_id."`` keys are ordinary columns; and a table without rows is
    returned as it is (U9, U151, D76).
    """
    if isinstance(table, str):
        table = [table]
    if not isinstance(table, pd.DataFrame):
        table = pd.DataFrame({"json": list(table)})
    suffixes = [suffix] if isinstance(suffix, str) else list(suffix)
    if len(suffixes) != 2 or any(not isinstance(s, str) for s in suffixes):
        raise ValueError("`suffix` must be a character vector of length 2.")

    if isinstance(col, str) and col not in table.columns:
        col = 1
    if isinstance(col, int):
        if not 1 <= col <= table.shape[1]:
            raise IndexError("subscript out of bounds")
        to_expand = table.iloc[:, col - 1].tolist()
    else:
        to_expand = table[col].tolist()

    source: list[int] = []  # the table row of each expanded row
    rows: list[dict[str, Any]] = []
    for i, value in enumerate(to_expand):
        for row in _rows(value):
            source.append(i)
            rows.append(row)
    names = list(dict.fromkeys(k for row in rows for k in row))
    columns = {nm: _column([row.get(nm) for row in rows]) for nm in names}
    if _ERROR in columns and bool(columns[_ERROR].isna().all()):
        del columns[_ERROR]
        names.remove(_ERROR)

    x_names = [str(c) for c in table.columns]
    out = table.iloc[source].reset_index(drop=True)
    out.columns = _add_suffixes(x_names, names, suffixes[0])
    for nm, new in zip(names, _add_suffixes(names, x_names, suffixes[1]), strict=True):
        out[new] = columns[nm]
    return cast(pd.DataFrame, out)
