"""``paper_validate()``: check a paper against the bibr schema.

Validation uses ``paper.json`` merged with the bibr 12.x additions
(``.paper_schema_bibr12()``, :func:`metacheck.papers.schema.load_schema_bibr12`),
so papers read from a bibr 12.x export and papers in the older format both
validate; properties that are not tables (``extraction``) are skipped.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.regex import is_na
from metacheck.papers.model import Paper
from metacheck.papers.schema import load_schema_bibr12, table_columns

__all__ = ["PaperValidationError", "paper_validate"]


class PaperValidationError(ValueError):
    """A paper is missing required tables or columns."""


_LIST_CELL = (list, tuple, dict, np.ndarray, pd.DataFrame, pd.Series)


def _r_typeof(s: pd.Series) -> str:
    """R's ``typeof()`` of the column *s* would have in metacheck's paper object."""
    dt = s.dtype
    if isinstance(dt, pd.StringDtype):
        return "character"
    if pd.api.types.is_bool_dtype(dt):
        return "logical"
    if isinstance(dt, pd.CategoricalDtype):
        return "integer"  # a factor
    if pd.api.types.is_integer_dtype(dt):
        return "integer"
    if pd.api.types.is_float_dtype(dt) or pd.api.types.is_datetime64_any_dtype(dt):
        return "double"
    values: list[Any] = s.tolist()
    if any(isinstance(v, _LIST_CELL) for v in values):
        return "list"
    present = [v for v in values if not is_na(v)]
    if not present:
        return "list"  # an object column of NULLs (or no rows) is a list column
    if all(isinstance(v, bool | np.bool_) for v in present):
        return "logical"
    if all(isinstance(v, str) for v in present):
        return "character"
    if all(isinstance(v, int | np.integer) and not isinstance(v, bool) for v in present):
        return "integer"
    if all(isinstance(v, int | float | np.number) for v in present):
        return "double"
    return "character"


_R_TYPE = {
    "string": "character",
    "integer": "integer",
    "number": "double",
    "boolean": "logical",
    "array": "list",
    "object": "list",
}


def paper_validate(paper: Paper) -> bool:
    """Return ``True`` or raise :class:`PaperValidationError`; warns on extras."""
    if not isinstance(paper, Paper):
        raise PaperValidationError("The following tables are missing:\n info, author, text")
    schema = load_schema_bibr12()
    errors: list[str] = []
    notes: list[str] = []
    required = [t for t in schema["required"] if t != "paper_id"]
    allowed = set(schema["properties"])
    names = paper.keys()
    missing = [t for t in required if t not in names]
    if missing:
        errors.append("The following tables are missing:\n " + ", ".join(missing))
    extra = [t for t in names if t not in allowed]
    if extra:
        notes.append("The paper has extra tables:\n " + ", ".join(extra))

    for tbl in [t for t in schema["properties"] if t != "paper_id" and t in names]:
        frame = paper[tbl]
        if not isinstance(frame, pd.DataFrame):
            continue
        prop = schema["properties"][tbl]
        ref = prop.get("$ref") or (prop.get("items") or {}).get("$ref")
        if not ref:
            continue  # not a table, e.g. extraction
        definition = schema["$defs"][ref.split("/")[2]]
        req_cols = definition.get("required", [])
        cols = list(frame.columns)
        miss = [c for c in req_cols if c not in cols]
        if miss:
            errors.append(f"The {tbl} table is missing required columns:\n " + ", ".join(miss))
        ok = {c for c, _ in table_columns(tbl)}
        extra_cols = [c for c in cols if c not in ok]
        if extra_cols:
            notes.append(f"The {tbl} table has extra columns:\n " + ", ".join(extra_cols))
        # R: for (col in intersect(cols, ok)), in the table's column order
        types = dict(table_columns(tbl))
        for col in dict.fromkeys(c for c in cols if c in ok):
            col_type = _r_typeof(frame[col])
            if col_type != _R_TYPE[types[col]]:
                notes.append(
                    f"The {col} column of the {tbl} table is a {col_type} type, "
                    f"but should be a {_R_TYPE[types[col]]} type"
                )
    if notes:
        warnings.warn("\n".join(notes), stacklevel=2)
    if errors:
        raise PaperValidationError("\n".join(errors))
    return True
