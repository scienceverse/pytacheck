"""R's numeric primitives that statcheck depends on.

Faithful re-implementations of the pieces of R's ``nmath`` library and base
coercion that decide statcheck's results:

* :func:`pt`, :func:`pf`, :func:`pchisq`, :func:`pnorm` follow the code
  paths of R's ``nmath/pt.c``, ``pf.c``, ``pgamma.c`` and ``pnorm.c`` (the
  same reductions to the incomplete beta/gamma functions, the same boundary
  cases and the same ``"NaNs produced"`` warnings), evaluated with
  :mod:`scipy.special`;
* :func:`r_round` is R (>= 4.0.0)'s ``round(x, digits)`` algorithm
  (``nmath/fround.c``), which picks the closer of the two decimal candidates
  in double arithmetic;
* :func:`as_numeric` is ``as.numeric()`` of a string (``R_strtod`` +
  ``String2Real``), reporting whether R would warn
  ``"NAs introduced by coercion"``.

Warnings are reported through a ``warn`` callback (``None`` ignores them),
so callers can reproduce R's ``tryCatch(warning = ...)`` semantics.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from pytacheck._r.regex import compile_r

__all__ = ["as_numeric", "pchisq", "pf", "pnorm", "pt", "r_round"]

Warn = Callable[[str], None] | None

NAN = float("nan")
_NANS_PRODUCED = "NaNs produced"


def _warn(warn: Warn, msg: str = _NANS_PRODUCED) -> float:
    if warn is not None:
        warn(msg)
    return NAN


def _dt_0(lower_tail: bool) -> float:
    return 0.0 if lower_tail else 1.0


def _dt_1(lower_tail: bool) -> float:
    return 1.0 if lower_tail else 0.0


def pnorm(x: float, lower_tail: bool = True) -> float:
    """R ``pnorm(x, 0, 1, lower.tail)``."""
    from scipy.special import ndtr

    if math.isnan(x):
        return x
    return float(ndtr(x if lower_tail else -x))


def pbeta(x: float, a: float, b: float, lower_tail: bool = True) -> float:
    """R ``pbeta(x, a, b, lower.tail)`` for the parameters statcheck produces."""
    from scipy.special import betainc, betaincc

    if math.isnan(x) or math.isnan(a) or math.isnan(b):
        return x + a + b
    if a < 0 or b < 0:
        return NAN
    if x <= 0:
        return _dt_0(lower_tail)
    if x >= 1:
        return _dt_1(lower_tail)
    return float(betainc(a, b, x) if lower_tail else betaincc(a, b, x))


def pt(x: float, n: float, lower_tail: bool = True, warn: Warn = None) -> float:
    """R ``pt(x, df, lower.tail)`` (central t distribution), as in ``nmath/pt.c``."""
    from scipy.special import betaln

    if math.isnan(x) or math.isnan(n):
        return x + n
    if n <= 0.0:
        return _warn(warn)
    if not math.isfinite(x):
        return _dt_0(lower_tail) if x < 0 else _dt_1(lower_tail)
    if not math.isfinite(n):
        return pnorm(x, lower_tail)
    nx = 1 + (x / n) * x
    if nx > 1e100:
        lval = (
            -0.5 * n * (2 * math.log(abs(x)) - math.log(n))
            - float(betaln(0.5 * n, 0.5))
            - math.log(0.5 * n)
        )
        val = math.exp(lval)
    elif n > x * x:
        val = pbeta(x * x / (n + x * x), 0.5, n / 2.0, lower_tail=False)
    else:
        val = pbeta(1.0 / nx, n / 2.0, 0.5, lower_tail=True)
    if x <= 0.0:
        lower_tail = not lower_tail
    val /= 2.0
    return (0.5 - val + 0.5) if lower_tail else val


def pchisq(x: float, df: float, lower_tail: bool = True, warn: Warn = None) -> float:
    """R ``pchisq(x, df, lower.tail)``: ``pgamma(x, df / 2, scale = 2)``."""
    from scipy.special import gammainc, gammaincc

    alph = df / 2.0
    if math.isnan(x) or math.isnan(alph):
        return x + alph
    if alph < 0.0:
        return _warn(warn)
    x = x / 2.0
    if alph == 0.0:
        return _dt_0(lower_tail) if x <= 0 else _dt_1(lower_tail)
    if x <= 0:
        return _dt_0(lower_tail)
    if math.isinf(x):
        return _dt_1(lower_tail)
    return float(gammainc(alph, x) if lower_tail else gammaincc(alph, x))


def pf(x: float, df1: float, df2: float, lower_tail: bool = True, warn: Warn = None) -> float:
    """R ``pf(x, df1, df2, lower.tail)``, as in ``nmath/pf.c``."""
    if math.isnan(x) or math.isnan(df1) or math.isnan(df2):
        return x + df2 + df1
    if df1 <= 0.0 or df2 <= 0.0:
        return _warn(warn)
    if x <= 0.0:
        return _dt_0(lower_tail)
    if math.isinf(x):
        return _dt_1(lower_tail)
    if math.isinf(df2):
        if math.isinf(df1):
            if x < 1.0:
                return _dt_0(lower_tail)
            if x == 1.0:
                return 0.5
            return _dt_1(lower_tail)
        return pchisq(x * df1, df1, lower_tail, warn)
    if math.isinf(df1):
        return pchisq(df2 / x, df2, not lower_tail, warn)
    if df1 * x > df2:
        return pbeta(df2 / (df2 + df1 * x), df2 / 2.0, df1 / 2.0, not lower_tail)
    return pbeta(df1 * x / (df2 + df1 * x), df1 / 2.0, df2 / 2.0, lower_tail)


def sqrt(x: float, warn: Warn = None) -> float:
    """R ``sqrt()``: ``NaN`` with a warning for negative input."""
    if math.isnan(x):
        return x
    if x < 0:
        return _warn(warn)
    return math.sqrt(x)


# -- round() -------------------------------------------------------------------

_DBL_DIG = 15
_MAX_DIGITS = 308


def _rint(x: float) -> float:
    """C ``nearbyint()``: round half to even."""
    return float(round(x))


def r_round(x: float, digits: float = 0) -> float:
    """R (>= 4.0.0) ``round(x, digits)`` (``nmath/fround.c``)."""
    if math.isnan(x) or math.isnan(digits):
        return x + digits
    if not math.isfinite(x) or digits > _MAX_DIGITS + 15 or x == 0.0:
        return x
    if digits < -_MAX_DIGITS:
        return 0.0
    if digits == 0.0:
        return _rint(x)
    dig = math.floor(digits + 0.5)
    sgn = 1.0
    if x < 0.0:
        sgn = -1.0
        x = -x
    if dig > 0 and math.log10(x) + dig > _DBL_DIG:
        return sgn * x
    if dig > 0:
        p10 = 10.0**dig
        x10 = p10 * x
        i10 = math.floor(x10)
        xd = i10 / p10
        xu = math.ceil(x10) / p10
    else:
        p10 = 10.0**-dig
        x10 = x / p10
        i10 = math.floor(x10)
        xd = i10 * p10
        xu = math.ceil(x10) * p10
    du = xu - x
    dd = x - xd
    return sgn * (xu if (du < dd or (du == dd and math.fmod(i10, 2.0) == 1)) else xd)


# -- as.numeric(<character>) ---------------------------------------------------

# isspace() in the C locale: what String2Real()/R_strtod() skip.
_C_SPACE = " \t\n\v\f\r"
_DECIMAL = compile_r(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", perl=True)
_HEX = compile_r(r"([+-]?)0[xX]([0-9a-fA-F]+)", perl=True)
_SPECIAL = {"inf": math.inf, "infinity": math.inf, "nan": NAN}


def as_numeric(s: str | None) -> tuple[float, bool]:
    """``as.numeric(s)`` for one string: ``(value, warned)``; ``NaN`` is ``NA``."""
    if s is None:
        return NAN, False
    body = s.strip(_C_SPACE)
    if body == "":
        return NAN, False
    if _DECIMAL.fullmatch(body):
        return float(body), False
    m = _HEX.fullmatch(body)
    if m:
        v = float(int(m.group(2), 16))
        return (-v if m.group(1) == "-" else v), False
    sign = 1.0
    rest = body
    if rest[:1] in "+-":
        sign = -1.0 if rest[0] == "-" else 1.0
        rest = rest[1:]
    special = _SPECIAL.get(rest.lower())
    if special is not None and rest.lower() != "nan":
        return sign * special, False
    if rest.lower() == "nan":
        return NAN, False
    return NAN, True
