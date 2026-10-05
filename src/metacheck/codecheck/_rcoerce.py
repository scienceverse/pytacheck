"""R's ``as.character()`` and ``unlist()`` of parsed JSON/YAML values.

Notebooks, ``renv.lock`` files and Quarto headers are parsed with
:func:`metacheck._json.loads` (JSON objects as dicts) or PyYAML, and the code
checks then ask what R would show for a value: ``as.character()`` of a version
number, ``unlist()`` of a cell's ``source``. A scalar is a string, boolean,
integer or float; an integer outside R's integer range
(``-2147483647 .. 2147483647``) is a double, as R's JSON and YAML readers make
it. A tuple is an R atomic vector (YAML sequences of scalars).
"""

from __future__ import annotations

import json
from typing import Any

from metacheck._r.base import as_character

__all__ = ["r_as_character", "r_unlist_chr"]

_INT_MAX = 2147483647


def _num(x: Any) -> Any:
    """Integers beyond R's 32-bit range are doubles."""
    if isinstance(x, int) and not isinstance(x, bool) and abs(x) > _INT_MAX:
        return float(x)
    return x


def _deparse(x: Any) -> str:
    """``deparse()`` of a value (as ``as.character()`` shows a list element)."""
    x = _num(x)
    if x is None:
        return "NULL"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return f"{x}L"
    if isinstance(x, float):
        return str(as_character(x))
    if isinstance(x, str):
        return json.dumps(x, ensure_ascii=False)
    if isinstance(x, dict):
        inner = ", ".join(f"{k} = {_deparse(v)}" if k else _deparse(v) for k, v in x.items())
        return f"list({inner})"
    if isinstance(x, list | tuple):
        return f"list({', '.join(_deparse(v) for v in x)})"
    return str(x)


def _is_scalar(x: Any) -> bool:
    return isinstance(x, str | bool | int | float)


def _scalar_chr(x: Any) -> str:
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, str):
        return x
    return str(as_character(_num(x)))


def r_as_character(x: Any) -> list[str | None]:
    """``as.character(x)`` of a parsed JSON/YAML value.

    A scalar gives one string; a list gives one string per element (a scalar
    element as itself, anything else deparsed, as R does); ``NULL`` gives
    none.
    """
    if x is None:
        return []
    if isinstance(x, dict | list):
        items = x.values() if isinstance(x, dict) else x
        return [_scalar_chr(v) if _is_scalar(v) else _deparse(v) for v in items]
    if isinstance(x, tuple):  # an R atomic vector (YAML)
        return [_scalar_chr(v) for v in x]
    return [_scalar_chr(x)]


def r_unlist_chr(x: Any) -> list[str]:
    """``unlist(x)`` of a parsed JSON value, shown as character.

    The leaves (``NULL`` dropped) are coerced to R's common type first, so a
    ``true`` next to a number is ``1`` and next to a string ``"TRUE"``.
    """
    leaves: list[Any] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for e in v.values():
                walk(e)
        elif isinstance(v, list | tuple):
            for e in v:
                walk(e)
        elif v is not None:
            leaves.append(_num(v))

    walk(x)
    if any(isinstance(v, str) for v in leaves):
        return [_scalar_chr(v) for v in leaves]
    if any(isinstance(v, float) for v in leaves):
        return [str(as_character(float(v))) for v in leaves]
    if any(isinstance(v, int) and not isinstance(v, bool) for v in leaves):
        return [str(int(v)) for v in leaves]
    return ["TRUE" if v else "FALSE" for v in leaves]
