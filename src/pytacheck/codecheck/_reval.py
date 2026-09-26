"""A small evaluator for the R expressions knitr evaluates while tangling.

``knitr::purl()`` evaluates the ``purl``, ``eval`` and ``child`` chunk options
(``eval_lang()``), in a fresh R session where nothing from the document has
run. This evaluates the subset of R such options use in practice, with the
answers a vanilla ``Rscript`` session gives:

* constants, ``T``/``F``, ``pi``, ``LETTERS``/``letters``, ``.Platform``;
* ``(``, ``{``, ``if``, ``!``, ``&&``, ``||``, ``&``, ``|``, comparisons (with
  R's string coercion and ``numeric_version`` comparisons), arithmetic,
  ``from:to``, ``%in%``, ``c()``, ``$``/``[[``/``[`` on named values;
* ``seq_len()``, ``seq_along()``, the ``integer()``/``numeric()``/
  ``double()``/``logical()``/``character()`` constructors, ``sum()``/
  ``max()``/``min()``, ``is.numeric()``/``is.character()``/``is.logical()``,
  ``Sys.time()``/``Sys.Date()`` (as numbers);
* ``isTRUE()``, ``isFALSE()``, ``is.null()``, ``is.na()``, ``identical()``,
  ``any()``, ``all()``, ``length()``, ``nchar()``, ``nzchar()``, ``tolower()``,
  ``toupper()``, ``as.logical()``/``as.numeric()``/``as.integer()``/
  ``as.character()``, ``interactive()`` (``FALSE``), ``Sys.getenv()`` (this
  process's environment), ``Sys.info()``, ``getRversion()`` (R 4.5.3, the
  reference version), ``capabilities()`` (all ``TRUE``), ``file.exists()``/
  ``dir.exists()`` (relative to the working directory), ``Sys.which()``,
  ``exists()`` (the datasets package's objects), ``require()``/``requireNamespace()``/``rlang::is_installed()``
  (``FALSE``: packages are not assumed to be installed), and knitr's
  ``is_html_output()``/``is_latex_output()``/``pandoc_to()``, answered from
  the ``out_format()`` of the document being tangled (:data:`OUT_FORMAT`:
  ``"markdown"`` for R Markdown, so ``is_html_output()`` is ``TRUE`` there;
  ``"latex"`` for Rnw).

Anything else raises :class:`EvalError`, which is what R does for a variable
the document has not defined (e.g. ``params``).
"""

from __future__ import annotations

import contextvars
import math
import os
import platform
from typing import Any

from pytacheck.codecheck._rparse import MISSING, Const, Lang, Sym

__all__ = ["NA", "EvalError", "RVersion", "is_false", "r_eval"]

R_VERSION = (4, 5, 3)

# knitr's out_format() while tangling: "markdown" for R Markdown, "latex" for
# Rnw, ... (set by the purl port for the document being tangled)
OUT_FORMAT: contextvars.ContextVar[str | None] = contextvars.ContextVar("OUT_FORMAT", default=None)


class EvalError(Exception):
    """R would raise an error evaluating the expression."""


class _NAType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "NA"


NA = _NAType()


class RVersion:
    """A ``numeric_version`` (``getRversion()``)."""

    def __init__(self, parts: tuple[int, ...]) -> None:
        self.parts = parts

    @staticmethod
    def parse(s: str) -> RVersion:
        try:
            return RVersion(tuple(int(p) for p in s.replace("-", ".").split(".")))
        except ValueError as exc:
            raise EvalError(f"invalid version specification '{s}'") from exc

    def __repr__(self) -> str:
        return ".".join(map(str, self.parts))


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if v is None:
        return []
    return [v]


def _first(v: Any) -> Any:
    vals = _as_list(v)
    if not vals:
        raise EvalError("argument is of length zero")
    return vals[0]


def _truthy(v: Any, op: str) -> Any:
    v = _first(v)
    if v is NA:
        return NA
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return NA if isinstance(v, float) and math.isnan(v) else v != 0
    if isinstance(v, str):
        low = (
            v.lower() if v in ("TRUE", "true", "T", "True", "FALSE", "false", "F", "False") else ""
        )
        if low:
            return low in ("true", "t")
        raise EvalError(f"invalid 'x' type in 'x {op} y'")
    raise EvalError(f"invalid 'x' type in 'x {op} y'")


def _cmp_values(a: Any, b: Any, op: str) -> Any:
    if a is NA or b is NA:
        return NA
    if isinstance(a, RVersion) or isinstance(b, RVersion):
        va = a if isinstance(a, RVersion) else RVersion.parse(str(a))
        vb = b if isinstance(b, RVersion) else RVersion.parse(str(b))
        x, y = va.parts, vb.parts
    elif isinstance(a, str) or isinstance(b, str):
        from pytacheck._r.base import as_character, r_sort_key

        sa = a if isinstance(a, str) else str(as_character(a) if not isinstance(a, bool) else a)
        sb = b if isinstance(b, str) else str(as_character(b) if not isinstance(b, bool) else b)
        if isinstance(a, bool):
            sa = "TRUE" if a else "FALSE"
        if isinstance(b, bool):
            sb = "TRUE" if b else "FALSE"
        if op in ("==", "!="):
            return (sa == sb) == (op == "==")
        x, y = r_sort_key(sa), r_sort_key(sb)  # type: ignore[assignment]
    else:
        x, y = float(a), float(b)  # type: ignore[assignment]
        if math.isnan(x) or math.isnan(y):  # type: ignore[arg-type]
            return NA
    return {
        "==": x == y,
        "!=": x != y,
        "<": x < y,
        ">": x > y,
        "<=": x <= y,
        ">=": x >= y,
    }[op]


def _vectorise(a: Any, b: Any, fn: Any) -> Any:
    la, lb = _as_list(a), _as_list(b)
    if not la or not lb:
        return []
    n = max(len(la), len(lb))
    out = [fn(la[i % len(la)], lb[i % len(lb)]) for i in range(n)]
    return out[0] if n == 1 and not isinstance(a, list) and not isinstance(b, list) else out


def _arith(a: Any, b: Any, op: str) -> Any:
    def one(x: Any, y: Any) -> Any:
        if x is NA or y is NA:
            return NA
        if isinstance(x, str) or isinstance(y, str):
            raise EvalError("non-numeric argument to binary operator")
        x, y = float(x), float(y)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "^":
            return x**y
        return x / y if y else (math.inf if x > 0 else -math.inf if x < 0 else math.nan)

    return _vectorise(a, b, one)


def _colon(a: Any, b: Any) -> Any:
    """R ``from:to`` (steps of 1 from *from* towards *to*)."""
    ends = []
    for v in (a, b):
        x = _first(v)
        if isinstance(x, str):
            try:
                x = float(x)
            except ValueError as exc:
                raise EvalError("NA/NaN argument") from exc
        if x is NA or x is None or (isinstance(x, float) and math.isnan(x)):
            raise EvalError("NA/NaN argument")
        ends.append(float(x))
    lo, hi = ends
    count = math.floor(abs(hi - lo) + 1e-10) + 1
    step = 1.0 if hi >= lo else -1.0
    out = [lo + step * i for i in range(count)]
    return out[0] if len(out) == 1 else out


def _as_logical(v: Any) -> Any:
    def one(x: Any) -> Any:
        if x is NA or x is None:
            return NA
        if isinstance(x, bool):
            return x
        if isinstance(x, int | float):
            return NA if isinstance(x, float) and math.isnan(x) else x != 0
        s = str(x)
        if s in ("TRUE", "true", "T", "True"):
            return True
        if s in ("FALSE", "false", "F", "False"):
            return False
        return NA

    return [one(x) for x in v] if isinstance(v, list) else one(v)


def _as_numeric(v: Any) -> Any:
    def one(x: Any) -> Any:
        if x is NA or x is None:
            return NA
        if isinstance(x, bool):
            return 1.0 if x else 0.0
        try:
            return float(x)
        except (TypeError, ValueError):
            return NA

    return [one(x) for x in v] if isinstance(v, list) else one(v)


def _as_character(v: Any) -> Any:
    from pytacheck._r.base import as_character

    def one(x: Any) -> Any:
        if x is NA or x is None:
            return NA
        if isinstance(x, bool):
            return "TRUE" if x else "FALSE"
        if isinstance(x, RVersion):
            return repr(x)
        return x if isinstance(x, str) else as_character(x)

    return [one(x) for x in v] if isinstance(v, list) else one(v)


# ls("package:datasets") in R 4.5.3: what exists() finds in a vanilla session
_DATASETS = frozenset(
    """ability.cov airmiles AirPassengers airquality anscombe attenu attitude austres beaver1
    beaver2 BJsales BJsales.lead BOD cars ChickWeight chickwts co2 CO2 crimtab discoveries DNase
    esoph euro euro.cross eurodist EuStockMarkets faithful fdeaths Formaldehyde freeny freeny.x
    freeny.y gait HairEyeColor Harman23.cor Harman74.cor Indometh infert InsectSprays iris iris3
    islands JohnsonJohnson LakeHuron ldeaths lh LifeCycleSavings Loblolly longley lynx mdeaths
    morley mtcars nhtemp Nile nottem npk occupationalStatus Orange OrchardSprays penguins
    penguins_raw PlantGrowth precip presidents pressure Puromycin quakes randu rivers rock
    Seatbelts sleep stack.loss stack.x stackloss state.abb state.area state.center
    state.division state.name state.region state.x77 sunspot.m2014 sunspot.month sunspot.year
    sunspots swiss Theoph Titanic ToothGrowth treering trees UCBAdmissions UKDriverDeaths UKgas
    USAccDeaths USArrests UScitiesD USJudgeRatings USPersonalExpenditure uspop VADeaths volcano
    warpbreaks women WorldPhones WWWusage""".split()  # noqa: SIM905
)

_PLATFORM = {
    "OS.type": "windows" if os.name == "nt" else "unix",
    "file.sep": "/",
    "dynlib.ext": ".so",
    "GUI": "X11",
    "endian": "little",
    "pkgType": "source",
    "path.sep": ";" if os.name == "nt" else ":",
    "r_arch": "",
}


def _fun_name(fun: Any) -> str | None:
    if isinstance(fun, Sym):
        return fun.name
    if (
        isinstance(fun, Lang)
        and isinstance(fun.fun, Sym)
        and fun.fun.name in ("::", ":::")
        and len(fun.args) == 2
        and isinstance(fun.args[1][1], Sym | Const)
    ):
        target = fun.args[1][1]
        return target.name if isinstance(target, Sym) else str(target.value)
    return None


def r_eval(x: Any) -> Any:
    """Evaluate the syntax tree *x* (see the module docstring)."""
    if x is MISSING:
        raise EvalError("argument is missing, with no default")
    if isinstance(x, Const):
        if x.na:
            return NA
        return None if x.kind == "NULL" else x.value
    if isinstance(x, Sym):
        name: str | None = x.name
        if name in ("T", "F"):
            return name == "T"
        if name == "pi":
            return math.pi
        if name == "LETTERS":
            return [chr(c) for c in range(65, 91)]
        if name == "letters":
            return [chr(c) for c in range(97, 123)]
        if name == ".Platform":
            return dict(_PLATFORM)
        raise EvalError(f"object '{name}' not found")
    if not isinstance(x, Lang):
        raise EvalError(f"cannot evaluate {x!r}")
    name = _fun_name(x.fun)
    if name is None:
        raise EvalError("attempt to apply non-function")
    args = [a[1] for a in x.args]
    tags = [a[0].name if a[0] is not None else None for a in x.args]
    return _call(name, args, tags)


def _call(name: str, args: list[Any], tags: list[str | None]) -> Any:
    n = len(args)
    if name == "(" and n == 1:
        return r_eval(args[0])
    if name == "{":
        value = None
        for a in args:
            value = r_eval(a)
        return value
    if name == "if":
        cond = _truthy(r_eval(args[0]), "&&")
        if cond is NA:
            raise EvalError("missing value where TRUE/FALSE needed")
        if cond:
            return r_eval(args[1])
        return r_eval(args[2]) if n > 2 else None
    if name == "!" and n == 1:
        v = r_eval(args[0])
        vals = [_truthy(e, "!") for e in _as_list(v)]
        out: list[Any] = [NA if e is NA else not e for e in vals]
        return out if isinstance(v, list) else out[0]
    if name in ("-", "+") and n == 1:
        v = r_eval(args[0])
        return _arith(0.0, v, name)
    if name in ("&&", "||") and n == 2:
        a = _truthy(r_eval(args[0]), name)
        if name == "&&" and a is False:
            return False
        if name == "||" and a is True:
            return True
        b = _truthy(r_eval(args[1]), name)
        if name == "&&":
            return False if b is False else (NA if NA in (a, b) else True)
        return True if b is True else (NA if NA in (a, b) else False)
    if name in ("&", "|") and n == 2:

        def logical(p: Any, q: Any) -> Any:
            p, q = _truthy(p, name), _truthy(q, name)
            if name == "&":
                return False if False in (p, q) else (NA if NA in (p, q) else True)
            return True if True in (p, q) else (NA if NA in (p, q) else False)

        return _vectorise(r_eval(args[0]), r_eval(args[1]), logical)
    if name in ("==", "!=", "<", ">", "<=", ">=") and n == 2:
        return _vectorise(r_eval(args[0]), r_eval(args[1]), lambda p, q: _cmp_values(p, q, name))
    if name in ("+", "-", "*", "/", "^") and n == 2:
        return _arith(r_eval(args[0]), r_eval(args[1]), name)
    if name == ":" and n == 2:
        return _colon(r_eval(args[0]), r_eval(args[1]))
    if name == "%in%" and n == 2:
        table = _as_list(r_eval(args[1]))
        v = r_eval(args[0])
        out = [e in table for e in _as_list(v)]
        return out if isinstance(v, list) else (out[0] if out else [])
    if name in ("c", "list"):
        out = []
        for a in args:
            out.extend(_as_list(r_eval(a)))
        return out[0] if len(out) == 1 else out
    if name == "$" and n == 2:
        obj = r_eval(args[0])
        field = args[1].name if isinstance(args[1], Sym) else r_eval(args[1])
        if isinstance(obj, dict):
            return obj.get(str(field))
        if obj is None:
            return None
        raise EvalError("$ operator is invalid for atomic vectors")
    if name in ("[[", "[") and n == 2:
        obj = r_eval(args[0])
        idx = r_eval(args[1])
        if isinstance(obj, dict) and isinstance(idx, str):
            if idx not in obj:
                if name == "[[":
                    raise EvalError("subscript out of bounds")
                return NA
            return obj[idx]
        if isinstance(idx, int | float) and not isinstance(idx, bool):
            vals = _as_list(obj)
            k = int(idx)
            if 1 <= k <= len(vals):
                return vals[k - 1]
            if name == "[[":
                raise EvalError("subscript out of bounds")
            return NA
        raise EvalError("invalid subscript")
    values = [r_eval(a) for a in args]
    named = dict(zip(tags, values, strict=True))
    if name == "isTRUE" and n == 1:
        return values[0] is True
    if name == "isFALSE" and n == 1:
        return values[0] is False
    if name == "is.null" and n == 1:
        return values[0] is None
    if name == "is.na" and n == 1:
        v = values[0]
        out2 = [e is NA or (isinstance(e, float) and math.isnan(e)) for e in _as_list(v)]
        return out2 if isinstance(v, list) else (out2[0] if out2 else [])
    if name == "identical" and n >= 2:
        a, b = values[0], values[1]
        if isinstance(a, RVersion) or isinstance(b, RVersion):
            return isinstance(a, RVersion) and isinstance(b, RVersion) and a.parts == b.parts
        if isinstance(a, bool) != isinstance(b, bool):
            return False
        if isinstance(a, str) != isinstance(b, str):
            return False
        return bool(a == b)
    if name in ("any", "all"):
        vals = [_truthy(e, name) for v in values for e in _as_list(v)]
        if name == "any":
            return True if True in vals else (NA if NA in vals else False)
        return False if False in vals else (NA if NA in vals else True)
    if name == "length" and n == 1:
        return len(_as_list(values[0]))
    if name in ("nchar", "nzchar") and n >= 1:
        v = values[0]
        out3 = [
            NA if e is NA else (len(str(e)) if name == "nchar" else str(e) != "")
            for e in _as_list(_as_character(v))
        ]
        return out3 if isinstance(v, list) else (out3[0] if out3 else [])
    if name in ("tolower", "toupper") and n == 1:
        v = values[0]
        conv = str.lower if name == "tolower" else str.upper
        out4 = [e if e is NA else conv(str(e)) for e in _as_list(_as_character(v))]
        return out4 if isinstance(v, list) else (out4[0] if out4 else [])
    if name == "as.logical" and n == 1:
        return _as_logical(values[0])
    if name in ("as.numeric", "as.double", "as.integer") and n == 1:
        return _as_numeric(values[0])
    if name == "as.character" and n == 1:
        return _as_character(values[0])
    if name == "interactive" and n == 0:
        return False
    if name == "Sys.getenv":
        key = named.get("x", values[0] if values else None)
        unset = named.get("unset", values[1] if n > 1 and tags[1] is None else "")
        if key is None:
            raise EvalError("Sys.getenv() without arguments is not supported")
        keys = _as_list(key)
        res = [os.environ.get(str(k), unset) for k in keys]
        return res[0] if len(res) == 1 else res
    if name == "Sys.info" and n == 0:
        return {
            "sysname": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
    if name == "getRversion" and n == 0:
        return RVersion(R_VERSION)
    if name == "capabilities":
        return True
    if name in ("file.exists", "dir.exists"):
        test = os.path.exists if name == "file.exists" else os.path.isdir
        res2 = [test(str(p)) for v in values for p in _as_list(v)]
        return res2[0] if len(res2) == 1 else res2
    if name == "exists" and values:
        return str(_first(values[0])) in _DATASETS
    if name == "Sys.which":
        import shutil

        res3 = [shutil.which(str(p)) or "" for v in values for p in _as_list(v)]
        return res3[0] if len(res3) == 1 else res3
    if name in ("require", "requireNamespace", "is_installed"):
        return False
    if name == "is_html_output":
        fmt = named.get("fmt", values[0] if n > 0 and tags[0] is None else None)
        excludes = named.get("excludes", values[1] if n > 1 and tags[1] is None else None)
        fmt = _first(fmt) if fmt is not None else OUT_FORMAT.get()
        if fmt is None:
            return False
        fmt = "markdown" if str(fmt).startswith("markdown") else str(fmt)
        fmt = "epub" if fmt == "epub3" else fmt
        html = ["markdown", "epub", "epub2", "html", "html4", "html5", "revealjs", "s5"]
        html += ["slideous", "slidy", "gfm"]
        return fmt in [f for f in html if f not in _as_list(excludes)]
    if name == "is_latex_output" and n == 0:
        return OUT_FORMAT.get() in ("latex", "sweave", "listings")
    if name == "pandoc_to":
        return None if n == 0 else False
    if name in ("seq_len", "seq_along") and n == 1:
        count = len(_as_list(values[0])) if name == "seq_along" else _count(values[0])
        return _vector([float(i) for i in range(1, count + 1)])
    if name in ("integer", "numeric", "double", "logical", "character"):
        count = _count(values[0]) if n else 0
        fill = {"logical": False, "character": ""}.get(name, 0.0)
        return _vector([fill] * count)
    if name in ("sum", "max", "min") and values:
        nums = [e for v in values for e in _as_list(_as_numeric(v))]
        if any(e is NA for e in nums):
            return NA
        if name == "sum":
            return float(sum(nums))
        if not nums:
            return -math.inf if name == "max" else math.inf
        return max(nums) if name == "max" else min(nums)
    if name in ("is.numeric", "is.character", "is.logical") and n == 1:
        kind = {"is.numeric": (int, float), "is.character": (str,), "is.logical": (bool,)}[name]
        vals = _as_list(values[0])
        if name == "is.numeric":
            return all(isinstance(e, int | float) and not isinstance(e, bool) for e in vals) and (
                bool(vals) or isinstance(values[0], list)
            )
        return all(isinstance(e, kind) for e in vals) and not any(e is NA for e in vals)
    if name == "Sys.time" and n == 0:
        import time

        return time.time()
    if name == "Sys.Date" and n == 0:
        import time

        return float(int(time.time() // 86400))
    raise EvalError(f'could not find function "{name}"')


def _count(v: Any) -> int:
    x = _first(_as_numeric(v))
    if x is NA or (isinstance(x, float) and math.isnan(x)) or x < 0:
        raise EvalError("argument must be coercible to non-negative integer")
    return int(x)


def _vector(vals: list[Any]) -> Any:
    """An R vector: a scalar when it has one element, else a list."""
    return vals[0] if len(vals) == 1 else vals


def is_false(v: Any) -> bool:
    """R ``isFALSE()``."""
    return v is False
