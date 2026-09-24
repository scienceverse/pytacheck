"""Structured-output type specifications (port of ellmer's ``type_*()``).

metacheck asks LLMs for structured data with ellmer type specs::

    ellmer::type_object(
      variables = ellmer::type_array(ellmer::type_object(
        variable_name = ellmer::type_string("Exact variable name"),
        label = ellmer::type_string("Verbatim description"))))

The same constructors exist here (``type_object(variables=type_array(...))``,
with ``description``/``required`` keyword arguments). A plain JSON-schema
``dict`` is accepted wherever a type is (:func:`as_type` converts it to the
equivalent type objects), and :func:`type_from_schema` wraps an arbitrary
schema like ``ellmer::type_from_schema()``.

Besides building request schemas (:func:`type_as_json`, one variant per
provider family, as ellmer's ``as_json()`` methods) this module reproduces
two R behaviours that pytacheck's results depend on:

* :func:`type_print_lines` is ``capture.output(print(type))`` -- the S7
  ``str()`` rendering metacheck hashes into its LLM cache keys;
* :func:`convert_from_type` is ellmer's conversion of the parsed JSON reply
  into R values (arrays of objects become data frames, arrays of enums
  factors, missing values typed ``NA``), expressed with the R object model
  of :mod:`pytacheck.llm._rds`.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from pytacheck.llm._rds import RInt, RList, RVec

__all__ = [
    "Type",
    "TypeArray",
    "TypeBasic",
    "TypeEnum",
    "TypeJsonSchema",
    "TypeObject",
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
    "type_print_lines",
    "type_string",
]


class Type:
    """Base class of ellmer's S7 ``Type`` objects."""

    _class = "Type"
    description: str | None
    required: bool

    def props(self) -> list[tuple[str, Any]]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return "\n".join(type_print_lines(self))

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.props() == other.props()  # type: ignore[attr-defined]

    __hash__ = None  # type: ignore[assignment]


class TypeBasic(Type):
    """A scalar: ``boolean``, ``integer``, ``number`` or ``string``."""

    _class = "TypeBasic"

    def __init__(self, type: str, description: str | None = None, required: bool = True):
        if type not in ("boolean", "integer", "number", "string"):
            raise ValueError(f"unknown basic type {type!r}")
        self.type = type
        self.description = description
        self.required = bool(required)

    def props(self) -> list[tuple[str, Any]]:
        return [("description", self.description), ("required", self.required), ("type", self.type)]


class TypeEnum(Type):
    """One of a fixed set of strings (``None`` is R's ``NA``)."""

    _class = "TypeEnum"

    def __init__(
        self, values: Sequence[str | None], description: str | None = None, required: bool = True
    ):
        self.values = [None if v is None else str(v) for v in values]
        self.description = description
        self.required = bool(required)

    def props(self) -> list[tuple[str, Any]]:
        return [
            ("description", self.description),
            ("required", self.required),
            ("values", RVec("chr", self.values)),
        ]


class TypeArray(Type):
    """An array of *items*."""

    _class = "TypeArray"

    def __init__(self, items: Type, description: str | None = None, required: bool = True):
        self.items = as_type(items)
        self.description = description
        self.required = bool(required)

    def props(self) -> list[tuple[str, Any]]:
        return [
            ("description", self.description),
            ("required", self.required),
            ("items", self.items),
        ]


class TypeObject(Type):
    """An object with named *properties*."""

    _class = "TypeObject"

    def __init__(
        self,
        properties: Mapping[str, Type] | None = None,
        description: str | None = None,
        required: bool = True,
        additional_properties: bool = False,
    ):
        self.properties = {str(k): as_type(v) for k, v in (properties or {}).items()}
        self.description = description
        self.required = bool(required)
        self.additional_properties = bool(additional_properties)

    def props(self) -> list[tuple[str, Any]]:
        return [
            ("description", self.description),
            ("required", self.required),
            ("properties", dict(self.properties) if self.properties else []),
            ("additional_properties", self.additional_properties),
        ]


class TypeJsonSchema(Type):
    """A raw JSON schema (``ellmer::type_from_schema()``)."""

    _class = "TypeJsonSchema"

    def __init__(self, json: Any, description: str | None = None, required: bool = True):
        self.json = json
        self.description = description
        self.required = bool(required)

    def props(self) -> list[tuple[str, Any]]:
        return [("description", self.description), ("required", self.required), ("json", self.json)]


def type_string(description: str | None = None, required: bool = True) -> TypeBasic:
    """Port of ``ellmer::type_string()``."""
    return TypeBasic("string", description, required)


def type_integer(description: str | None = None, required: bool = True) -> TypeBasic:
    """Port of ``ellmer::type_integer()``."""
    return TypeBasic("integer", description, required)


def type_number(description: str | None = None, required: bool = True) -> TypeBasic:
    """Port of ``ellmer::type_number()``."""
    return TypeBasic("number", description, required)


def type_boolean(description: str | None = None, required: bool = True) -> TypeBasic:
    """Port of ``ellmer::type_boolean()``."""
    return TypeBasic("boolean", description, required)


def type_enum(
    values: Sequence[str | None], description: str | None = None, required: bool = True
) -> TypeEnum:
    """Port of ``ellmer::type_enum()``."""
    return TypeEnum(values, description, required)


def type_array(items: Any, description: str | None = None, required: bool = True) -> TypeArray:
    """Port of ``ellmer::type_array()``."""
    return TypeArray(as_type(items), description, required)


def type_object(
    properties: Mapping[str, Any] | None = None,
    /,
    *,
    description: str | None = None,
    required: bool = True,
    additional_properties: bool = False,
    **props: Any,
) -> TypeObject:
    """Port of ``ellmer::type_object(.description, ..., .required)``.

    Properties are given as keyword arguments (in order) or as a mapping.
    """
    allprops = dict(properties or {})
    allprops.update(props)
    return TypeObject(allprops, description, required, additional_properties)


def type_from_schema(text: str | None = None, path: str | None = None) -> TypeJsonSchema:
    """Port of ``ellmer::type_from_schema(text, path)``."""
    from pytacheck.llm._json import parse_json

    if (text is None) == (path is None):
        raise ValueError("Exactly one of `text` or `path` must be supplied.")
    if path is not None:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    assert text is not None
    return TypeJsonSchema(parse_json(text))


def as_type(x: Any) -> Type:
    """A :class:`Type` from a type, a JSON-schema ``dict`` or a JSON string.

    JSON schemas made of ``object``/``array``/``string``/``integer``/
    ``number``/``boolean`` nodes (and string ``enum``\\ s) become the equivalent
    ellmer type objects, so they behave exactly like the R type specs they
    mirror; anything else is kept as a :class:`TypeJsonSchema`.
    """
    if isinstance(x, Type):
        return x
    if isinstance(x, str):
        return type_from_schema(text=x)
    if isinstance(x, Mapping):
        converted = _schema_to_type(x, True)
        return converted if converted is not None else TypeJsonSchema(dict(x))
    raise TypeError(f"not a type specification: {type(x).__name__}")


def _schema_to_type(s: Mapping[str, Any], required: bool) -> Type | None:
    desc = s.get("description")
    typ = s.get("type")
    if isinstance(typ, list):
        non_null = [t for t in typ if t != "null"]
        if len(non_null) != 1:
            return None
        typ = non_null[0]
        required = False if "null" in s.get("type", []) else required
    if "enum" in s and typ in (None, "string"):
        return TypeEnum(list(s["enum"]), desc, required)
    if typ in ("string", "integer", "number", "boolean"):
        return TypeBasic(typ, desc, required)
    if typ == "array":
        items = s.get("items")
        if not isinstance(items, Mapping):
            return None
        it = _schema_to_type(items, True)
        return None if it is None else TypeArray(it, desc, required)
    if typ == "object":
        props = s.get("properties") or {}
        req = s.get("required")
        req_set = set(req) if isinstance(req, list) else set(props)
        out: dict[str, Type] = {}
        for k, v in props.items():
            if not isinstance(v, Mapping):
                return None
            t = _schema_to_type(v, k in req_set)
            if t is None:
                return None
            out[k] = t
        return TypeObject(out, desc, required, bool(s.get("additionalProperties", False)))
    return None


# ---------------------------------------------------------------------------
# print(): utils::str() of the S7 object, as captured by capture.output()
# ---------------------------------------------------------------------------

_WIDTH = 80
_NCHAR_MAX = 128
_VEC_LEN = 4


def _char_width(c: str) -> int:
    if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def _nchar_w(s: str) -> int:
    return sum(_char_width(c) for c in s)


def _strtrim(s: str, width: int) -> str:
    out = []
    w = 0
    for c in s:
        cw = _char_width(c)
        if w + cw > width:
            break
        out.append(c)
        w += cw
    return "".join(out)


_ENC = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\v": "\\v",
}


def _encode_string(s: str) -> str:
    """``encodeString(s, quote = '"')`` in a UTF-8 locale."""
    out = ['"']
    for c in s:
        if c in _ENC:
            out.append(_ENC[c])
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\{ord(c):03o}")
        elif ord(c) > 0x7F and unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn", "Cs"):
            out.append(f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _maybe_truncate(x: str, s_quote: str = '"') -> str:
    ch = s_quote + "| __truncated__"
    if _nchar_w(x) > _NCHAR_MAX:
        tr = _strtrim(x, _NCHAR_MAX - len(ch))
        if tr != x and tr + s_quote != x:
            return tr + ch
    return x


def _format_num3(v: Any) -> str:
    from pytacheck._r import format_num

    if v is None:
        return "NA"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return format_num(float(v), 3)


class _Printer:
    def __init__(self) -> None:
        self.text: list[str] = []

    def cat(self, *parts: str) -> None:
        self.text.append("".join(parts))

    # str.S7_object --------------------------------------------------------
    def s7(self, obj: Type, nest_lev: int, indent: str | None, vec_len: int | None) -> None:
        if nest_lev > 0:
            self.cat(" ")
        self.cat(f"<ellmer::{obj._class}>")
        self.cat("\n")
        props = obj.props()
        ind = indent if indent is not None else _default_indent(nest_lev)
        names = _format_names([n for n, _ in props])
        for (_, value), name in zip(props, names, strict=True):
            self.cat(ind, "@", " ", name, ":")
            self.show(value, nest_lev + 1, None, vec_len)

    # str() dispatch -------------------------------------------------------
    def show(self, x: Any, nest_lev: int, indent: str | None, vec_len: int | None) -> None:
        if isinstance(x, Type):
            self.s7(x, nest_lev, indent, vec_len)
        elif x is None:
            self.cat(" NULL\n")
        elif isinstance(x, dict | list) and not isinstance(x, RVec):
            self.lst(x, nest_lev, indent)
        else:
            self.atomic(_as_vec(x), nest_lev, vec_len)

    def lst(self, x: dict[str, Any] | list[Any], nest_lev: int, indent: str | None) -> None:
        items = list(x.items()) if isinstance(x, dict) else [("", v) for v in x]
        if not items:
            self.cat(" ", "Named " if isinstance(x, dict) else "", "list()\n")
            return
        self.cat(f"List of {len(items)}\n")
        ind = indent if indent is not None else _default_indent(nest_lev)
        names = _format_names([k for k, _ in items]) if isinstance(x, dict) else [""] * len(items)
        for (_, v), name in zip(items[:99], names, strict=False):
            self.cat(ind, "$ ", name, ":")
            # strSub(): every str() formal is passed on explicitly, vec.len included
            self.show(v, nest_lev + 1, ind + " ..", _VEC_LEN)
        if len(items) > 99:
            self.cat(ind, " [list output truncated]\n")

    def atomic(self, v: RVec, nest_lev: int, vec_len: int | None) -> None:
        le = len(v.values)
        mod = {"chr": "chr", "lgl": "logi", "int": "int", "dbl": "num"}[v.type]
        if le == 1:
            str1 = " " + mod
        else:
            str1 = " " + mod + (" " if le > 0 else "") + (f"[1:{le}]" if le > 0 else "(0)")
        if v.type == "chr":
            enc = [
                None if s is None else _maybe_truncate(_encode_string(_strtrim(s, _NCHAR_MAX)))
                for s in v.values
            ]
            v_len: float
            if vec_len is None:
                limit = _WIDTH - (4 + 5 * nest_lev + _nchar_w(str1))
                total = 0
                count = 0
                for e in enc:
                    total += 1 + (2 if e is None else _nchar_w(e))
                    if total < limit:
                        count += 1
                v_len = max(1, count)
            else:
                v_len = vec_len
            ile = min(le, v_len)
            body = " ".join("NA" if e is None else e for e in enc[:ile])
        else:
            base = vec_len if vec_len is not None else _VEC_LEN
            if v.type == "lgl":
                v_len = 1.5 * base
                fmt = ["NA" if b is None else ("TRUE" if b else "FALSE") for b in v.values]
            else:
                v_len = round(2.5 * base)
                fmt = [_format_num3(n) for n in v.values]
            ile = int(min(v_len, le))
            body = _maybe_truncate(" ".join(fmt[:ile]), "")
        self.cat(str1, " ", body, " ..." if le > v_len else "", "\n")


def _as_vec(x: Any) -> RVec:
    if isinstance(x, RVec):
        return x
    if isinstance(x, bool):
        return RVec("lgl", [x])
    if isinstance(x, RInt):
        return RVec("int", [int(x)])
    if isinstance(x, int):
        return RVec("int", [x])
    if isinstance(x, float):
        return RVec("dbl", [x])
    return RVec("chr", [str(x)])


def _default_indent(nest_lev: int) -> str:
    return "..".join([" "] * max(0, nest_lev + 1))


def _format_names(names: list[str]) -> list[str]:
    width = max((_nchar_w(n) for n in names), default=0)
    return [n + " " * (width - _nchar_w(n)) for n in names]


def type_print_lines(type: Type) -> list[str]:
    """``capture.output(print(type))`` for an ellmer type object."""
    p = _Printer()
    p.show(as_type(type), 0, None, None)
    text = "".join(p.text)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# JSON schemas sent to providers (ellmer's as_json() methods)
# ---------------------------------------------------------------------------


def type_as_json(type: Type, provider: str = "generic") -> Any:
    """The request schema for *type*.

    *provider* selects ellmer's method family: ``"openai"`` (OpenAI and
    OpenAI-compatible providers: every property required, optional ones
    nullable, strict), ``"ollama"``, ``"gemini"`` or ``"generic"``
    (Anthropic and others).
    """
    from pytacheck.llm._json import Vec

    t = as_type(type)
    if isinstance(t, TypeJsonSchema):
        return t.json
    if isinstance(t, TypeBasic):
        return {"type": t.type, "description": t.description or ""}
    if isinstance(t, TypeEnum):
        return {"type": "string", "description": t.description or "", "enum": list(t.values)}
    if isinstance(t, TypeArray):
        return {
            "type": "array",
            "description": t.description or "",
            "items": type_as_json(t.items, provider),
        }
    assert isinstance(t, TypeObject)
    names = list(t.properties)
    if provider == "openai":
        if t.additional_properties:
            raise ValueError("`.additional_properties` not supported for OpenAI.")
        props: dict[str, Any] = {}
        for name, p in t.properties.items():
            out = type_as_json(p, provider)
            if not p.required and isinstance(out, dict):
                out = dict(out)
                out["type"] = Vec([*_as_list(out.get("type")), "null"])
            props[name] = out
        return {
            "type": "object",
            "description": t.description or "",
            "properties": props,
            "required": list(names),
            "additionalProperties": False,
        }
    required = [n for n, p in t.properties.items() if p.required]
    if provider == "gemini":
        if t.additional_properties:
            raise ValueError("`.additional_properties` not supported for Gemini.")
        if not t.properties:
            return []
        out_g: dict[str, Any] = {"type": "object"}
        if t.description is not None:
            out_g["description"] = t.description
        out_g["properties"] = {n: type_as_json(p, provider) for n, p in t.properties.items()}
        out_g["required"] = required
        return out_g
    if provider == "ollama":
        if t.additional_properties:
            raise ValueError("`.additional_properties` not supported for Ollama.")
        out_o: dict[str, Any] = {"type": "object", "description": t.description or ""}
        if t.properties:
            out_o["properties"] = {n: type_as_json(p, provider) for n, p in t.properties.items()}
        if required:
            out_o["required"] = required
        out_o["additionalProperties"] = False
        return out_o
    return {
        "type": "object",
        "description": t.description or "",
        "properties": {n: type_as_json(p, provider) for n, p in t.properties.items()}
        if t.properties
        else [],
        "required": required,
        "additionalProperties": t.additional_properties,
    }


def _as_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return list(x)
    return [x]


def type_has_additional_properties(type: Type) -> bool:
    """Port of ``ellmer:::type_has_additional_properties()``."""
    t = as_type(type)
    if isinstance(t, TypeObject):
        return t.additional_properties or any(
            type_has_additional_properties(p) for p in t.properties.values()
        )
    if isinstance(t, TypeArray):
        return type_has_additional_properties(t.items)
    return False


# ---------------------------------------------------------------------------
# convert_from_type(): parsed JSON -> R values
# ---------------------------------------------------------------------------

_R_TYPES = {"boolean": "lgl", "integer": "int", "number": "dbl", "string": "chr"}


def _r_typeof(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "lgl"
    if isinstance(v, RInt):
        return "int"
    if isinstance(v, int):
        return "int" if -(2**31) < v < 2**31 else "dbl"
    if isinstance(v, float):
        return "dbl"
    if isinstance(v, str):
        return "chr"
    if isinstance(v, RVec):
        return v.type
    return "list"


def _scalar_of(v: Any) -> Any:
    if isinstance(v, RVec):
        return v.values[0] if v.values else None
    return v


def _r_elements(x: Any) -> list[Any]:
    """The elements ``lapply(x, ...)`` visits: a JSON object's values, an array's items."""
    if x is None:
        return []
    if isinstance(x, dict):
        return list(x.values())
    if isinstance(x, list | tuple):
        return list(x)
    if isinstance(x, RVec):
        return list(x.values)
    return [x]


def _as_integer(v: float | None) -> int | None:
    """``as.integer()`` of a double: truncation; ``NA`` (with R's warning) out of range."""
    import warnings

    if v is None or math.isnan(v):
        return None
    if math.isinf(v) or v >= 2147483648 or v <= -2147483648:
        warnings.warn("NAs introduced by coercion to integer range", stacklevel=5)
        return None
    return int(v)


def list_to_atomic(x: Any, type: str) -> RVec:
    """Port of ``ellmer:::list_to_atomic()`` (a list of JSON scalars -> vector)."""
    r_type = _R_TYPES[type]
    items = _r_elements(x)
    out: list[Any] = []
    for v in items:
        t = _r_typeof(v)
        val = _scalar_of(v)
        if r_type == "int" and t == "dbl":
            val = _as_integer(val)
            t = "int"
        elif r_type == "dbl" and t == "int":
            val = None if val is None else float(val)
            t = "dbl"
        if t != r_type:
            out.append(None)
        else:
            out.append(val)
    return RVec(r_type, out)


def _deparse(v: Any) -> str:
    """``deparse()`` of a parsed-JSON value (as ``as.character()`` shows a list element)."""
    from pytacheck._r import as_character

    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int) and _r_typeof(v) == "int":
        return f"{v}L"
    if isinstance(v, int | float):
        return as_character(float(v)) or "NA"
    if isinstance(v, str):
        return _encode_string(v)
    if isinstance(v, dict):
        return "list(" + ", ".join(f"{_deparse_name(k)} = {_deparse(x)}" for k, x in v.items()) + ")"
    if isinstance(v, list | tuple):
        return "list(" + ", ".join(_deparse(x) for x in v) + ")"
    return str(v)


def _deparse_name(k: str) -> str:
    import re

    reserved = {"if", "else", "repeat", "while", "function", "for", "next", "break", "TRUE",
                "FALSE", "NULL", "Inf", "NaN", "NA", "in"}  # fmt: skip
    if re.fullmatch(r"(?:[A-Za-z]|[.](?![0-9]))[A-Za-z0-9._]*|[.]", k) and k not in reserved:
        return k
    return "`" + k.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _as_character_elem(v: Any) -> str:
    """``as.character()`` of one element of an R list."""
    from pytacheck._r import as_character

    if v is None:
        return "NULL"
    if isinstance(v, str):
        return v
    if isinstance(v, bool | int | float):
        return as_character(v) or "NA"
    return _deparse(v)


def _subscript(y: Any, name: str) -> Any:
    """R ``y[[name]]`` on a parsed-JSON value."""
    if y is None:
        return None
    if isinstance(y, dict):
        return y.get(name)
    raise ValueError("subscript out of bounds")


def _factor(labels: list[str | None], levels: list[str | None]) -> RVec:
    levs = [lv for lv in levels if lv is not None]
    index = {lv: i + 1 for i, lv in enumerate(levs)}
    codes = [None if lab is None else index.get(lab) for lab in labels]
    return RVec("int", codes, {"levels": RVec("chr", levs), "class": RVec("chr", ["factor"])})


def _tibble(cols: dict[str, Any], nrow: int) -> RList:
    return RList(
        [cols[k] for k in cols],
        {
            "names": RVec("chr", list(cols)),
            "row.names": RVec("int", [None, -nrow]),
            "class": RVec("chr", ["tbl_df", "tbl", "data.frame"]),
        },
    )


def convert_from_type(x: Any, type: Type) -> Any:
    """Port of ``ellmer:::convert_from_type()``.

    Returns the R object model: ``dict`` (named list), ``list``, :class:`RVec`
    (vectors, factors and typed ``NA``), :class:`RList` data frames (tibbles),
    Python scalars for JSON scalars, ``None`` for ``NULL``.
    """
    t = as_type(type)
    if x is None and not t.required:
        return None
    if isinstance(t, TypeArray):
        items = t.items
        seq = _r_elements(x)  # lapply() visits a JSON object's values
        if isinstance(items, TypeBasic):
            return list_to_atomic(x, items.type)
        if isinstance(items, TypeArray):
            return [convert_from_type(y, items) for y in seq]
        if isinstance(items, TypeEnum):
            return _factor([_as_character_elem(v) for v in seq], items.values)
        if isinstance(items, TypeObject):
            if items.additional_properties:
                out_ap = []
                for y in seq:
                    if not isinstance(y, dict):
                        raise ValueError("subscript out of bounds")
                    keys = list(dict.fromkeys([*items.properties, *y]))
                    out_ap.append({k: y.get(k) for k in keys})
                return out_ap
            cols = {
                name: convert_from_type([_subscript(y, name) for y in seq], TypeArray(prop))
                for name, prop in items.properties.items()
            }
            return _tibble(cols, len(seq) if cols else 0)
        return x
    if isinstance(t, TypeObject):
        if x is not None and not isinstance(x, dict):
            raise ValueError("subscript out of bounds")  # x[[name]] on an array or scalar
        src = x or {}
        out = {name: convert_from_type(src.get(name), prop) for name, prop in t.properties.items()}
        if t.additional_properties:
            out.update({k: v for k, v in src.items() if k not in t.properties})
        return out
    if isinstance(t, TypeBasic):
        if x is None:
            return RVec(_R_TYPES[t.type], [None])
        if isinstance(x, list | dict) and len(x) == 1:
            return _r_elements(x)[0]
        return x
    if isinstance(t, TypeEnum):
        if x is None:
            return RVec("chr", [None])
        if isinstance(x, list | dict):
            return RVec("chr", [_as_character_elem(v) for v in _r_elements(x)])
        return _as_character_elem(x)
    return x
