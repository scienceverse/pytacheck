"""Unit tests for the R regex emulation (parity/cases/rcompat.yaml checks it against R)."""

from __future__ import annotations

import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from pytacheck._r import regex as rx


def test_tre_is_leftmost_longest() -> None:
    assert rx.regextract("a|ab", "ab") == "ab"
    assert rx.regextract("a|ab", "ab", perl=True) == "a"


def test_tre_bracket_backslash_is_literal() -> None:
    assert rx.grepl(r"^[\d]$", "d")
    assert not rx.grepl(r"^[\d]$", "5")
    assert rx.grepl(r"^[\d]$", "5", perl=True)


def test_pcre_word_is_ascii() -> None:
    assert rx.grepl(r"^\w$", "é")
    assert not rx.grepl(r"^\w$", "é", perl=True)


def test_vectorisation_keeps_series_index() -> None:
    s = pd.Series(["a1", None, "b"], index=[10, 20, 30])
    out = rx.grepl(r"\d", s)
    assert list(out.index) == [10, 20, 30]
    assert list(out) == [True, False, False]
    assert rx.gsub("a", "b", s).tolist() == ["b1", None, "b"]


@given(st.text(max_size=30))
def test_fixed_gsub_matches_str_replace(s: str) -> None:
    assert rx.gsub(".", "!", s, fixed=True) == s.replace(".", "!")


def test_strsplit_matches_r_examples() -> None:
    # R: strsplit(c(",", ",,", "a,,", ",a"), ",")
    assert rx.strsplit([",", ",,", "a,,", ",a"], ",") == [[""], ["", ""], ["a", ""], ["", "a"]]
    assert rx.strsplit("aaa", "^a") == ["", "", ""]
