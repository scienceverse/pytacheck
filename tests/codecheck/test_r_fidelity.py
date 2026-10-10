"""Byte-for-byte fidelity of R's parser.

The corpus in ``fixtures/`` holds inputs and the messages R 4.5.3 produced for
them; it was generated once with R and is compared here without R.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
