"""R's ``as.numeric()`` of a string: ``R_strtod()`` and ``isBlankString()``.

``columns._as_numeric_str()`` needs the exact rules (decimal and hexadecimal
numbers, ``NaN``/``Inf``, blanks as ``iswspace()`` sees them in a UTF-8 locale,
an error on invalid UTF-8) that a plain ``float()`` does not have.
"""

from __future__ import annotations

import math

_C_SPACE = frozenset(b" \t\n\v\f\r")
# iswspace() in R's UTF-8 locale (glibc): no-break spaces are not blank
_WSPACE = frozenset(
    chr(c)
    for c in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x1680, *range(0x2000, 0x2007),
              0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x205F, 0x3000)
)  # fmt: skip


def _mb_error(s: bytes) -> ValueError:
    parts = []
    text = s.decode("utf-8", "surrogateescape")
    for ch in text:
        if "\udc80" <= ch <= "\udcff":
            parts.append(f"<{ord(ch) - 0xDC00:02x}>")
        else:
            parts.append(ch)
    return ValueError(f"invalid multibyte string at '{''.join(parts)}'")


def _is_blank_string(s: bytes) -> bool:
    """``isBlankString()`` in a UTF-8 locale (errors on invalid UTF-8 before a non-blank)."""
    text = s.decode("utf-8", "surrogateescape")
    for k, ch in enumerate(text):
        if "\udc80" <= ch <= "\udcff":
            raise _mb_error(text[k:].encode("utf-8", "surrogateescape"))
        if ch not in _WSPACE:
            return False
    return True


def _strtod(s: bytes, na: bool) -> tuple[float | None, int]:
    """``R_strtod5(dec = ".", exact = FALSE)``: ``(value, end)``; ``None`` is NA."""
    n = len(s)
    p = 0
    while p < n and s[p] in _C_SPACE:
        p += 1
    if na and s.startswith(b"NA", p):
        return None, p + 2
    sign = 1.0
    if p < n and s[p] == 0x2D:
        sign = -1.0
        p += 1
    elif p < n and s[p] == 0x2B:
        p += 1
    low = s[p : p + 8].lower()
    if low.startswith(b"nan"):
        return math.nan, p + 3
    if low.startswith(b"infinity"):
        return sign * math.inf, p + 8
    if low.startswith(b"inf"):
        return sign * math.inf, p + 3
    if n - p > 2 and s[p] == 0x30 and s[p + 1] in b"xX":
        return _strtod_hex(s, p + 2, sign)
    q = p
    while q < n and 0x30 <= s[q] <= 0x39:
        q += 1
    int_end = q
    frac_end = q
    if q < n and s[q] == 0x2E:
        q += 1
        while q < n and 0x30 <= s[q] <= 0x39:
            q += 1
        frac_end = q
    if (int_end - p) + (frac_end - int_end - (1 if frac_end > int_end else 0)) <= 0:
        return None, 0
    mant = s[p:frac_end]
    exp = b""
    if q < n and s[q] in b"eE":
        r = q + 1
        if r < n and s[r] in b"+-":
            r += 1
        e0 = r
        while r < n and 0x30 <= s[r] <= 0x39:
            r += 1
        if r == e0:
            return None, 0
        exp = s[q:r]
        q = r
    try:
        v = float((mant + exp).decode("ascii"))
    except (ValueError, OverflowError):  # pragma: no cover - validated above
        return None, 0
    return sign * v, q


def _strtod_hex(s: bytes, p: int, sign: float) -> tuple[float | None, int]:
    n = len(s)
    ans = 0.0
    exph = -1
    while p < n:
        c = s[p]
        if 0x30 <= c <= 0x39:
            ans = 16 * ans + (c - 0x30)
        elif 0x61 <= c <= 0x66:
            ans = 16 * ans + (c - 0x61 + 10)
        elif 0x41 <= c <= 0x46:
            ans = 16 * ans + (c - 0x41 + 10)
        elif c == 0x2E:
            exph = 0
            p += 1
            continue
        else:
            break
        if exph >= 0:
            exph += 4
        p += 1
    expn = 0
    if p < n and s[p] in b"pP":
        r = p + 1
        esign = 1
        if r < n and s[r] == 0x2D:
            esign = -1
            r += 1
        elif r < n and s[r] == 0x2B:
            r += 1
        e0 = r
        val = 0
        while r < n and 0x30 <= s[r] <= 0x39:
            val = val * 10 + (s[r] - 0x30) if val < 9999 else val
            r += 1
        if r == e0:
            return None, 0
        expn = esign * val
        p = r
    if ans != 0.0:
        if exph > 0:
            expn -= exph
        try:
            ans = math.ldexp(ans, expn)
        except OverflowError:
            ans = math.inf
    return sign * ans, p
