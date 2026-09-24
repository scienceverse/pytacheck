"""``paper_validate()``: check a paper against the bibr schema.

Validation uses ``paper.json`` merged with the bibr 12.x additions
(``.paper_schema_bibr12()``, :func:`pytacheck.papers.schema.load_schema_bibr12`),
so papers read from a bibr 12.x export and papers in the older format both
validate; properties that are not tables (``extraction``) are skipped.
"""

from __future__ import annotations

import warnings

import pandas as pd

from pytacheck.papers.model import Paper
from pytacheck.papers.schema import load_schema_bibr12, table_columns

__all__ = ["PaperValidationError", "paper_validate"]


class PaperValidationError(ValueError):
    """A paper is missing required tables or columns."""


_TYPE_CHECK = {
    "string": lambda s: pd.api.types.is_string_dtype(s) or s.isna().all(),
    "integer": pd.api.types.is_integer_dtype,
    "number": lambda s: pd.api.types.is_float_dtype(s) or pd.api.types.is_integer_dtype(s),
    "boolean": pd.api.types.is_bool_dtype,
    "array": pd.api.types.is_object_dtype,
    "object": pd.api.types.is_object_dtype,
}
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
        for col, typ in table_columns(tbl):
            if col in cols and not _TYPE_CHECK[typ](frame[col]):
                notes.append(
                    f"The {col} column of the {tbl} table is a {frame[col].dtype} type, "
                    f"but should be a {_R_TYPE[typ]} type"
                )
    if notes:
        warnings.warn("\n".join(notes), stacklevel=2)
    if errors:
        raise PaperValidationError("\n".join(errors))
    return True
