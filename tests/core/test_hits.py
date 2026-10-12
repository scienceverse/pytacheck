"""``Hits``: what a chain refuses, and R's errors it keeps."""

from __future__ import annotations

import random

import pytest

from metacheck.core.doc import Docs
from metacheck.core.patterns import Pat, patterns
from tests.core.chains import make_paper


def _docs() -> Docs:
    return Docs.of(make_paper(random.Random(5), "p", False))


def test_a_grouped_mode_after_excluding_a_list_raises() -> None:
    h = _docs().hits(Pat(".*")).without(patterns("power", "size"))
    with pytest.raises(ValueError, match="exclude one pattern at a time"):
        h.group("paragraph")


def test_a_grouped_mode_after_excluding_one_pattern_works() -> None:
    h = _docs().hits(Pat(".*")).without(Pat("power"))
    assert h.group("paragraph").table() is not None


def test_excluding_three_patterns_raises_as_dplyr_intersect_does() -> None:
    with pytest.raises(ValueError, match=r"`\.\.\.` must be empty"):
        _docs().hits(Pat(".*")).without(patterns("power", "size", "the"))


def test_a_grouped_mode_of_a_grouped_table_raises() -> None:
    h = _docs().hits(Pat(".*")).group("paragraph")
    with pytest.raises(ValueError, match="one search call"):
        h.group("section")
