"""Sentence splitting: tokenizers::tokenize_sentences() (ICU sentence boundaries).

The expected sentences in fixtures/sentences.json were produced by R
(tokenizers 0.3.0, stringi with ICU 78.3) for real Grobid paragraphs,
hand-written edge cases and random strings over one character of every
Unicode Sentence_Break class.
"""

from __future__ import annotations

import json

import pytest

from metacheck.io.grobid import _tokenize_sentences
from tests.io.conftest import IO_FIXTURES

CASES = json.loads((IO_FIXTURES / "sentences.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=range(len(CASES)))
def test_tokenize_sentences_matches_icu(case: dict) -> None:
    assert _tokenize_sentences(case["x"]) == case["sentences"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("   ", [""]),
        (None, [None]),
        ("Hello.  World", ["Hello.", "World"]),
        ("e.g. The", ["e.g.", "The"]),  # SB11: ATerm Sp Upper breaks
        ("U.S.A. is", ["U.S.A. is"]),  # SB8: lowercase continuation
        ("p. 3 and", ["p. 3 and"]),  # SB8 looks past digits to the lowercase
        ("Fig. 1a. Test", ["Fig. 1a.", "Test"]),
        ("3.5 x. 4", ["3.5 x.", "4"]),  # no lowercase follows: SB8 does not apply
        ("Why?! No.", ["Why?!", "No."]),  # SB8a
        ('He said "Stop." Then', ['He said "Stop."', "Then"]),  # SB9/SB10 closers
    ],
)
def test_tokenize_sentences_rules(text, expected) -> None:
    assert _tokenize_sentences(text) == expected
