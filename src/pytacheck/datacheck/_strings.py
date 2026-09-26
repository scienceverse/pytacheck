"""``tools::file_ext()`` and ``tolower()`` on strings that are not valid UTF-8.

File names read as bytes are kept as Python strings with the undecodable
bytes as lone surrogates (``errors="surrogateescape"``; see
:mod:`pytacheck.fileinfo._strings`). R treats such names in two ways:

* its regex engine refuses them, so ``regexpr()`` is ``-1`` and
  ``tools::file_ext()`` gives ``""`` -- unless another element of the vector
  has an extension, in which case ``substring()`` walks every string with
  glibc's ``mbrtowc()`` and stops at the first byte it cannot convert with
  ``invalid multibyte string at '<ff>...'``;
* ``tolower()`` converts with glibc's ``mbstowcs()`` and fails with
  ``invalid multibyte string <i>``.

glibc's UTF-8 decoder is a little more lenient than R's validity check (it
takes 4-byte sequences beyond U+10FFFF), so the two can disagree; both are
emulated here, including the odd way R prints the offending bytes (a 6-byte
``R_MB_CUR_MAX`` budget that runs out part-way through the message).
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from pytacheck._r.regex import compile_r
from pytacheck.fileinfo._strings import invalid_utf8

#: The dtype of text read from a data file before ``_utf8_repair_df()``: it
#: can hold the lone surrogates that stand for bytes that are not UTF-8,
#: which Arrow-backed strings (pandas' default with pyarrow) cannot.
RAW_STRING = pd.StringDtype("python")

__all__ = ["file_ext", "file_ext1", "glibc_invalid", "tolower_checked"]

_FILE_EXT_RX = r"\.([[:alnum:]]+)$"
_MB_CUR_MAX = 6  # glibc's MB_CUR_MAX in a UTF-8 locale (R_MB_CUR_MAX)
_SIZE_T = 2**64


def _raw(x: str) -> bytes:
    """The bytes R holds for *x* (NUL-terminated, as C sees them)."""
    return x.encode("utf-8", "surrogateescape") + b"\x00"


def _seq_len(lead: int) -> int:
    """Length of the UTF-8 sequence *lead* starts (0: glibc rejects the byte)."""
    if lead < 0x80:
        return 1
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF7:
        return 4
    return 0


def _mbrtowc(buf: bytes, i: int, n: int, state: list[int]) -> int:
    """glibc ``mbrtowc(NULL, buf + i, n, state)`` in a UTF-8 locale.

    Returns the bytes consumed, ``0`` at the NUL, ``-1`` (``EILSEQ``; the
    state is left alone) or ``-2`` (incomplete; the bytes read are kept in
    *state* and complete the character on the next call).
    """
    if n == 0:
        return -2
    seq = list(state)
    avail = n
    j = i
    if not seq:
        lead = buf[j]
        if lead == 0:
            return 0
        need = _seq_len(lead)
        if need == 0:
            return -1
        seq.append(lead)
        j += 1
        avail -= 1
    else:
        need = _seq_len(seq[0])
    while len(seq) < need:
        if avail == 0:
            state[:] = seq
            return -2
        c = buf[j]
        if c & 0xC0 != 0x80:  # the NUL terminator ends a sequence here too
            return -1
        seq.append(c)
        j += 1
        avail -= 1
    cp = seq[0] & (0x7F >> need if need > 1 else 0x7F)
    for c in seq[1:]:
        cp = (cp << 6) | (c & 0x3F)
    if (need == 3 and cp < 0x800) or (need == 4 and cp < 0x10000) or 0xD800 <= cp <= 0xDFFF:
        return -1
    state.clear()
    return j - i


def _first_bad(buf: bytes) -> int | None:
    """Offset of the first character glibc cannot convert (``None``: all fine)."""
    p = 0
    state: list[int] = []
    while buf[p] != 0:
        used = _mbrtowc(buf, p, _MB_CUR_MAX, state)
        if used <= 0:
            return p
        p += used
    return None


def glibc_invalid(x: object) -> bool:
    """Would glibc (``mbstowcs()``, ``mbrtowc()``) refuse the string *x*?"""
    return invalid_utf8(x) and _first_bad(_raw(x)) is not None  # type: ignore[arg-type]


def _mbcs_error_text(buf: bytes, s: int) -> str:
    """R's ``Mbrtowc()`` error text for a conversion that failed at ``buf[s]``.

    R prints the rest of the string, re-converting it with the ``n`` bytes it
    passed to the failed call (``R_MB_CUR_MAX``) and counting that budget down
    -- a ``size_t`` that wraps round once it is spent -- so characters near
    the budget's end come out as ``<xx>`` or as raw bytes.
    """
    n = _MB_CUR_MAX
    state: list[int] = []
    out = bytearray()
    p = s
    used = -1  # the call that failed
    while buf[p] != 0:
        if p > s:
            used = _mbrtowc(buf, p, n, state)
        if used == 0:
            break
        if used > 0:
            out += buf[p : p + used]
            p += used
            n = (n - used) % _SIZE_T
        else:
            out += f"<{buf[p]:02x}>".encode("ascii")
            p += 1
            n = (n - 1) % _SIZE_T
    return out.decode("utf-8", "surrogateescape")


def file_ext(x: Sequence[str | None]) -> list[str | None]:
    """``tools::file_ext(x)`` over a whole vector (``None`` is ``NA``).

    An element that is not valid UTF-8 has no extension, but when any other
    element has one, R's ``substring()`` fails on the first element glibc
    cannot convert (``ValueError``, R's message).
    """
    rx = compile_r(_FILE_EXT_RX)
    out: list[str | None] = []
    bad: list[bool] = []
    for v in x:
        b = invalid_utf8(v)
        bad.append(b)
        if v is None:
            out.append(None)
        elif b:
            out.append("")
        else:
            m = rx.search(v)
            out.append(m.group(1) if m else "")
    if any(bad) and any(e for e in out):
        for v, b in zip(x, bad, strict=True):
            if not b:
                continue
            buf = _raw(v)  # type: ignore[arg-type]
            at = _first_bad(buf)
            if at is not None:
                raise ValueError(f"invalid multibyte string at '{_mbcs_error_text(buf, at)}'")
    return out


def file_ext1(path: str | None) -> str:
    """``tools::file_ext(path)`` of one path (``""`` for ``NA`` or an invalid string)."""
    return file_ext([path])[0] or ""


def tolower_checked(x: Sequence[str | None]) -> None:
    """Raise as R's ``tolower(x)`` does for the first string glibc cannot convert."""
    for i, v in enumerate(x, start=1):
        if glibc_invalid(v):
            raise ValueError(f"invalid multibyte string {i}")
