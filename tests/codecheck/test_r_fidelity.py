"""Byte-for-byte fidelity of code_read(), R's parser and knitr::purl().

The corpora in ``fixtures/`` hold inputs and the output R 4.5.3 (readr 2.2.0,
vroom 1.7.1, stringi/ICU 78.3, knitr 1.52) produced for them; they were
generated once with R and are compared here without R.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from metacheck.codecheck import _encoding, _icu
from metacheck.codecheck._purl import purl
from metacheck.codecheck._rparse import (
    Lang,
    RParseError,
    Sym,
    _parse_error_py,
    parse_error,
    parse_exprs,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_bytes().decode("utf-8", "surrogateescape"))


# ---------------------------------------------------------------------------
# code_read()
# ---------------------------------------------------------------------------

_READ = _load("code_read_corpus.json")


def _py_read(data: bytes, name: str) -> list[str] | str:
    try:
        return [] if not data else _encoding.code_read_bytes(data, name)
    except Exception as exc:
        return f"ERROR: {exc}"


def test_code_read_corpus() -> None:
    bad = []
    for rec in _READ:
        data = base64.b64decode(rec["b64"])
        r = rec["r"]
        py = _py_read(data, rec["name"])
        if isinstance(py, str):
            ok = len(r) == 1 and isinstance(r[0], str) and r[0].startswith("ERROR")
        else:
            ok = py == r
        if not ok:
            bad.append((rec["name"], data[:60], r[:3], py if isinstance(py, str) else py[:3]))
    assert not bad, bad[:5]


def test_guess_encoding_shapes() -> None:
    assert _encoding.guess_encoding(b"x <- 1\n") == [("ASCII", 1.0)]
    with pytest.raises(ValueError, match="missing value"):
        _encoding.guess_encoding(b"\xef\xbb\xbf")
    # nothing ICU recognises: stringi's single NA row
    assert _encoding.guess_encoding(b"Th\xa6") == [(None, None)]
    guess = _encoding.guess_encoding("Größe und Häufigkeit\n".encode("latin-1"))
    assert guess[0][0] in ("ISO-8859-1", "windows-1252")


def test_icu_detects_utf16_bom() -> None:
    best = _icu.detect_all(b"\xff\xfeh\x00i\x00")[0]
    assert best[0] == "UTF-16LE"
    assert best[2] == 100


def test_read_lines_base_quirks() -> None:
    # readLines(): "\r\r" is two line ends, NULs are dropped after line splitting,
    # a UTF-8 byte order mark is removed from the first line
    assert _encoding.read_lines_base(b"a\r\r\nb") == ["a", "", "", "b"]
    assert _encoding.read_lines_base(b"a\r\x00\nb") == ["a", "", "b"]
    assert _encoding.read_lines_base(b"\xef\xbb\xbfx\n") == ["x"]


def test_iconv_sub_byte() -> None:
    raw = b"caf\xe9 \xc3\xa9".decode("utf-8", "surrogateescape")
    assert _encoding.iconv_sub_byte(raw) == "caf<e9> é"


# ---------------------------------------------------------------------------
# R's parser
# ---------------------------------------------------------------------------

_PARSE = _load("rparse_corpus.json")


def test_rparse_corpus() -> None:
    bad = []
    for lines, r in _PARSE:
        py = _parse_error_py(lines)
        if py != r:
            bad.append((lines[:3], r, py))
    assert not bad, bad[:5]


def test_parse_error_messages() -> None:
    assert parse_error(["x <- 1", "y y"], "python") == (
        "<text>:2:3: unexpected symbol\n1: x <- 1\n2: y y\n     ^"
    )
    assert parse_error(["f("], "python") == "<text>:2:0: unexpected end of input\n1: f(\n   ^"
    assert parse_error(['x <- "\\q"'], "python") == (
        "'\\q' is an unrecognized escape in character string (<text>:1:8)"
    )
    assert parse_error(["function(x, x) 1"], "python") == (
        "repeated formal argument 'x' (<text>:1:13)"
    )
    assert parse_error(["x |> f"], "python") == (
        "The pipe operator requires a function call as RHS (<text>:1:6)"
    )
    assert parse_error(["if (x) 1", "else 2"], "python") == (
        "<text>:2:1: unexpected 'else'\n1: if (x) 1\n2: else\n   ^"
    )
    assert parse_error(["{", "if (x) 1", "else 2", "}"], "python") is None
    assert parse_error([], "python") is None


def test_parse_exprs_trees() -> None:
    (e,) = parse_exprs(["alist('lab', eval = FALSE, fig.width = 5)"])
    assert isinstance(e, Lang)
    assert e.fun is Sym("alist")
    assert [a[0].name if a[0] else None for a in e.args] == [None, "eval", "fig.width"]
    (p,) = parse_exprs(["x |> f(y = _)"])
    assert isinstance(p, Lang)
    assert p.fun is Sym("f")
    assert p.args[0][1] is Sym("x")
    with pytest.raises(RParseError):
        parse_exprs(["x <- )"])


# ---------------------------------------------------------------------------
# knitr::purl()
# ---------------------------------------------------------------------------

_PURL = _load("purl_corpus.json")


@pytest.mark.parametrize("documentation", [0, 1, 2])
def test_purl_corpus(documentation: int) -> None:
    bad = []
    for lines, outs in _PURL:
        r = outs[documentation]
        try:
            py = purl(lines, documentation)
        except Exception as exc:
            py = f"ERROR: {exc}"
        if r == "<<NOFILE>>":
            r = ""
        if r.startswith("ERROR") and py.startswith("ERROR"):
            continue
        if r != py:
            bad.append((lines[:4], r[:200], py[:200]))
    assert not bad, bad[:3]
