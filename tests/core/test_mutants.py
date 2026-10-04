"""The generated chains catch each way a grouped view can go wrong.

Each mutant breaks one rule of the grouped return modes; the chains of
:mod:`tests.core.chains` (with a fixed seed) must then differ from the frozen
``text_search()``:

* ``raw_stage``: after a grouped call, the next call matches the joined text before
  cleaning instead of the cleaned text;
* ``space_join``: a group's paragraphs joined with a space, not the paragraph
  marker (which cleaning turns into a blank line);
* ``matched_only``: groups made of the matched rows only, not of every row the
  call searched;
* ``row_order``: groups in Doc row order, not in the order of the call's input table.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import pytest

from metacheck.core import groups as G
from metacheck.core import hits as H
from tests.core.chains import differences

GROUP = H.Hits.group
CHAINS = 400
SEED = 3


def raw_stage(self: H.Hits, level: str) -> H.Hits:
    return dataclasses.replace(GROUP(self, level), stage=0)


def space_join(self: H.Hits, level: str) -> H.Hits:
    join = G.JOIN
    G.JOIN = " "
    try:
        return GROUP(self, level)
    finally:
        G.JOIN = join


def matched_only(self: H.Hits, level: str) -> H.Hits:
    return GROUP(dataclasses.replace(self, universe=self.masks), level)


def row_order(self: H.Hits, level: str) -> H.Hits:
    return GROUP(dataclasses.replace(self, uorder=None, call_ranks=None), level)


MUTANTS: dict[str, Callable[[H.Hits, str], H.Hits]] = {
    "raw_stage": raw_stage,
    "space_join": space_join,
    "matched_only": matched_only,
    "row_order": row_order,
}


def test_the_chains_pass_without_a_mutant() -> None:
    assert differences(CHAINS, SEED, facade=False) == []


@pytest.mark.parametrize("name", list(MUTANTS))
def test_the_chains_catch_the_mutant(name: str, monkeypatch: Any) -> None:
    monkeypatch.setattr(H.Hits, "group", MUTANTS[name])
    assert differences(CHAINS, SEED, facade=False), f"the chains miss the mutant {name}"
