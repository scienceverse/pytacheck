"""Structured-output types: JSON-schema builders and the conversion of a reply into columns.

metacheck asks LLMs for structured data with ellmer type specs::

    ellmer::type_object(
      variables = ellmer::type_array(ellmer::type_object(
        variable_name = ellmer::type_string("Exact variable name"),
        label = ellmer::type_string("Verbatim description"))))

The same constructors exist here (``type_object(variables=type_array(...))``,
with ``description``/``required`` keyword arguments). Each builds a plain
JSON-schema ``dict`` (a :class:`Schema`), and a plain ``dict`` is accepted
wherever a type is.

* :func:`type_as_json` words a schema for a provider family (OpenAI's strict
  form, the plain form of Anthropic and Ollama, Gemini's);
* :func:`convert_from_type` is the conversion metacheck's results depend on:
  an array of objects becomes a data frame, an array of scalars a typed
  column, a missing value a typed ``NA``.
"""

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "Schema",
    "as_type",
    "convert_from_type",
    "type_array",
    "type_as_json",
    "type_boolean",
    "type_enum",
    "type_from_schema",
    "type_integer",
    "type_number",
    "type_object",
    "type_string",
]

_BASIC = ("boolean", "integer", "number", "string")


class Schema(dict[str, Any]):
    """A JSON-schema node. *required* is whether the object holding it must have it;
    *raw* marks a schema that is sent as written and whose reply is not converted."""

    required: bool
    raw: bool

    def __init__(self, data: Any = (), *, required: bool = True, raw: bool = False) -> None:
        super().__init__(data)
        self.required = bool(required)
        self.raw = raw


def _node(typ: str, description: str | None, required: bool, **rest: Any) -> Schema:
    out = {"type": typ, **rest}
    if description is not None:
        out["description"] = description
    return Schema(out, required=required)


def type_string(description: str | None = None, required: bool = True) -> Schema:
    """Port of ``ellmer::type_string()``."""
    return _node("string", description, required)


def type_integer(description: str | None = None, required: bool = True) -> Schema:
    """Port of ``ellmer::type_integer()``."""
    return _node("integer", description, required)


def type_number(description: str | None = None, required: bool = True) -> Schema:
    """Port of ``ellmer::type_number()``."""
    return _node("number", description, required)


def type_boolean(description: str | None = None, required: bool = True) -> Schema:
    """Port of ``ellmer::type_boolean()``."""
    return _node("boolean", description, required)


def type_enum(
    values: Sequence[str | None], description: str | None = None, required: bool = True
) -> Schema:
    """Port of ``ellmer::type_enum()`` (``None``, R's ``NA``, may be among the values)."""
    return _node(
        "string", description, required, enum=[None if v is None else str(v) for v in values]
    )


def type_array(items: Any, description: str | None = None, required: bool = True) -> Schema:
    """Port of ``ellmer::type_array()``."""
    return _node("array", description, required, items=items)


def type_object(
    properties: Mapping[str, Any] | None = None,
    /,
    *,
    description: str | None = None,
    required: bool = True,
    additional_properties: bool = False,
    **props: Any,
) -> Schema:
    """Port of ``ellmer::type_object(.description, ..., .required)``.

    Properties are given as keyword arguments (in order) or as a mapping.
    """
    allprops = {str(k): v for k, v in {**(properties or {}), **props}.items()}
    node: dict[str, Any] = {"type": "object", "properties": allprops}
    if description is not None:
        node["description"] = description
    node["required"] = [k for k, v in allprops.items() if getattr(v, "required", True)]
    node["additionalProperties"] = bool(additional_properties)
    return Schema(node, required=required)


def type_from_schema(text: str | None = None, path: str | None = None) -> Schema:
    """Port of ``ellmer::type_from_schema(text, path)``: a schema sent as written."""
    if (text is None) == (path is None):
        raise ValueError("Exactly one of `text` or `path` must be supplied.")
    if path is not None:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    assert text is not None
    return Schema(json.loads(text), raw=True)


def as_type(x: Any) -> Schema:
    """A :class:`Schema` from a schema, a JSON-schema ``dict`` or a JSON string.

    A ``dict`` made only of ``object``/``array``/``string``/``integer``/
    ``number``/``boolean`` nodes (and string ``enum``\\ s) is converted like
    the type builders' output; any other is sent as written (*raw*).
    """
    if isinstance(x, Schema):
        return x
    if isinstance(x, str):
        return type_from_schema(text=x)
    if isinstance(x, Mapping):
        return Schema(x, raw=not _simple(x))
    raise TypeError(f"not a type specification: {type(x).__name__}")


def _base(s: Mapping[str, Any]) -> tuple[Any, bool]:
    """The type of a node and whether ``"null"`` is allowed (``["string", "null"]``)."""
    typ = s.get("type")
    if isinstance(typ, list):
        rest = [t for t in typ if t != "null"]
        return (rest[0] if len(rest) == 1 else None), "null" in typ
    return typ, False


def _simple(s: Any) -> bool:
    if not isinstance(s, Mapping):
        return False
    typ, _ = _base(s)
    if "enum" in s and typ in (None, "string"):
        return True
    if typ in _BASIC:
        return True
    if typ == "array":
        return _simple(s.get("items"))
    if typ == "object":
        return all(_simple(v) for v in (s.get("properties") or {}).values())
    return False


def _required(parent: Mapping[str, Any], name: str, child: Mapping[str, Any]) -> bool:
    """Whether an object's property is required (all are when the schema lists none)."""
    if _base(child)[1]:
        return False
    listed = parent.get("required")
    return name in listed if isinstance(listed, list) else True


# ---------------------------------------------------------------------------
# the schema each provider family is sent
# ---------------------------------------------------------------------------


def type_as_json(type: Any, dialect: str = "generic") -> Any:
    """The request schema for *type* in a provider family's wording.

    *dialect* is ``"openai"`` (every property required, optional ones nullable,
    ``additionalProperties`` false: the strict form of OpenAI and its
    compatibles), ``"gemini"``, ``"ollama"`` or ``"generic"`` (Anthropic). A
    raw schema is returned as written.
    """
    t = as_type(type)
    return dict(t) if t.raw else _wire(t, dialect, True)


def _wire(s: Mapping[str, Any], dialect: str, required: bool) -> Any:
    typ, nullable = _base(s)
    required = required and not nullable
    desc = s.get("description") or ""
    if "enum" in s and typ in (None, "string"):
        out: dict[str, Any] = {"type": "string", "description": desc, "enum": list(s["enum"])}
    elif typ in _BASIC:
        out = {"type": typ, "description": desc}
    elif typ == "array" and isinstance(s.get("items"), Mapping):
        out = {"type": "array", "description": desc, "items": _wire(s["items"], dialect, True)}
    elif typ == "object":
        props = s.get("properties") or {}
        if s.get("additionalProperties") is True and dialect != "generic":
            raise ValueError(f"`.additional_properties` not supported for {dialect}.")
        wired = {n: _wire(p, dialect, _required(s, n, p)) for n, p in props.items()}
        out = {"type": "object", "description": desc, "properties": wired}
        if dialect == "openai":
            out["required"] = list(props)
            out["additionalProperties"] = False
        else:
            out["required"] = [n for n, p in props.items() if _required(s, n, p)]
            if dialect != "gemini":
                out["additionalProperties"] = bool(s.get("additionalProperties", False))
    else:
        return dict(s)  # anything else is sent as written
    if dialect == "openai" and not required:
        out["type"] = [out["type"], "null"]
    return out


def type_has_additional_properties(type: Any) -> bool:
    """Port of ``ellmer:::type_has_additional_properties()``."""

    def walk(s: Any) -> bool:
        if not isinstance(s, Mapping):
            return False
        if s.get("type") == "object":
            return s.get("additionalProperties") is True or any(
                walk(p) for p in (s.get("properties") or {}).values()
            )
        return walk(s.get("items")) if s.get("type") == "array" else False

    return walk(as_type(type))


# ---------------------------------------------------------------------------
# convert_from_type(): parsed JSON -> columns
# ---------------------------------------------------------------------------

_DTYPE = {"boolean": "boolean", "integer": "Int64", "number": "float64", "string": "string"}


def _kind(v: Any) -> str | None:
    """The type of a JSON scalar as R types it (an integer is one within 32 bits)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer" if -(2**31) < v < 2**31 else "number"
    if isinstance(v, float):
        return "number"
    return "string" if isinstance(v, str) else "list"


def _elements(x: Any) -> list[Any]:
    """What ``lapply(x, ...)`` visits: an object's values, an array's items."""
    if x is None:
        return []
    if isinstance(x, dict):
        return list(x.values())
    return list(x) if isinstance(x, list | tuple) else [x]


def _atomic(values: Sequence[Any], typ: str) -> pd.Series[Any]:
    """Port of ``ellmer:::list_to_atomic()``: a typed column (a value of another type is ``NA``)."""
    import pandas as pd

    out: list[Any] = []
    for v in values:
        k = _kind(v)
        if typ == "integer" and k == "number":  # as.integer() truncates; out of range is NA
            if math.isnan(v) or math.isinf(v) or abs(v) >= 2147483648:
                warnings.warn("NAs introduced by coercion to integer range", stacklevel=4)
                v = None
            else:
                v = int(v)
        elif typ == "number" and k == "integer":
            v = float(v)
        elif k != typ:
            v = None
        out.append(v)
    na = [float("nan") if v is None else v for v in out] if typ == "number" else out
    return pd.Series(na, dtype=_DTYPE[typ])  # type: ignore[call-overload]


def _as_character(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    from metacheck._r import as_character

    return as_character(v) if isinstance(v, bool | int | float) else json.dumps(v)


def _column(col: Any) -> pd.Series[Any]:
    """A column of a converted array of objects: vectors stay, lists and frames are cells."""
    import pandas as pd

    if isinstance(col, pd.Series):
        return col
    if isinstance(col, pd.DataFrame):  # a data-frame column: one record per row
        return pd.Series(col.to_dict("records"), dtype=object)
    return pd.Series([_cell(v) for v in col], dtype=object)


def _cell(v: Any) -> Any:
    """One element of a list column: vectors are lists (``character(0)`` is ``[]``)."""
    import pandas as pd

    if isinstance(v, pd.Series):
        return [None if pd.isna(e) else e for e in v.astype(object).tolist()]
    if isinstance(v, list | tuple):
        return [_cell(e) for e in v]
    return v


def _subscript(y: Any, name: str) -> Any:
    """``y[[name]]`` on a parsed-JSON value."""
    if y is None:
        return None
    if isinstance(y, dict):
        return y.get(name)
    if isinstance(y, list | tuple):  # an unnamed list: no such element
        return None
    raise ValueError("subscript out of bounds")  # an atomic vector


def convert_from_type(x: Any, type: Any) -> Any:
    """Port of ``ellmer:::convert_from_type()``.

    Arrays of objects become data frames (``pandas.DataFrame``), arrays of
    scalars and of enum values typed columns (``pandas.Series``), objects
    dicts, and a missing value a one-row typed ``NA`` column. A raw schema
    leaves the reply as it is.
    """
    t = as_type(type)
    return x if t.raw else _convert(x, t, t.required)


def _convert(x: Any, s: Mapping[str, Any], required: bool) -> Any:
    import pandas as pd

    typ, nullable = _base(s)
    if x is None and (nullable or not required):
        return None
    if "enum" in s and typ in (None, "string"):
        if x is None:
            return pd.Series([None], dtype="string")
        if isinstance(x, list | dict):
            return pd.Series([_as_character(v) for v in _elements(x)], dtype="string")
        return _as_character(x)
    if typ in _BASIC:
        if x is None:
            return pd.Series([None], dtype=_DTYPE[typ])  # type: ignore[call-overload]
        if isinstance(x, list | dict) and len(x) == 1:
            return _elements(x)[0]
        return x
    if typ == "array" and isinstance(s.get("items"), Mapping):
        return _convert_array(x, s["items"])
    if typ == "object":
        props = s.get("properties") or {}
        out = {n: _convert(_subscript(x, n), p, _required(s, n, p)) for n, p in props.items()}
        if s.get("additionalProperties") is True and isinstance(x, dict):
            out.update({k: v for k, v in x.items() if k not in props})
        return out
    return x


def _convert_array(x: Any, items: Mapping[str, Any], required: bool | None = None) -> Any:
    """An array whose items have the schema *items* (``required``: whether that item may not be null)."""
    import pandas as pd

    if required is None:
        required = getattr(items, "required", True)
    seq = _elements(x)
    typ, _ = _base(items)
    if "enum" in items and typ in (None, "string"):
        levels = [v for v in dict.fromkeys(items["enum"]) if v is not None]
        return pd.Series(  # a factor: a value that is not a level is NA
            pd.Categorical([_as_character(v) for v in seq], categories=levels)
        )
    if typ in _BASIC:
        return _atomic(seq, typ)
    if typ == "array" and isinstance(items.get("items"), Mapping):
        return [_convert(y, items, bool(required)) for y in seq]
    if typ == "object":
        props = items.get("properties") or {}
        if items.get("additionalProperties") is True:
            out = []
            for y in seq:  # y[union(names(properties), names(y))]
                src = y if isinstance(y, dict) else {}
                out.append({k: src.get(k) for k in dict.fromkeys([*props, *src])})
            return out
        cols = {
            n: _column(_convert_array([_subscript(y, n) for y in seq], p, _required(items, n, p)))
            for n, p in props.items()
        }
        if not cols:
            return pd.DataFrame(index=range(0))
        return pd.DataFrame(cols)
    return x
