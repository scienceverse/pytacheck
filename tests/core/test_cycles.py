"""A Doc and its grouped views leave no reference cycle: dropping the paper frees them.

A Doc has no ``__weakref__``, so the test counts the Doc objects the interpreter
holds (``gc.get_objects()``) with the cycle collector off: a cycle would keep them.
"""

from __future__ import annotations

import gc
import random
from collections.abc import Iterator

import pytest

from metacheck.core.doc import Doc
from metacheck.text.search import text_search
from tests.core.chains import make_paper


@pytest.fixture(autouse=True)
def _no_cycle_collector() -> Iterator[None]:
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def _docs_alive() -> int:
    return sum(isinstance(o, Doc) for o in gc.get_objects())


@pytest.mark.parametrize("level", ["paragraph", "section", "header", "paper_id"])
def test_dropping_a_paper_frees_its_docs_and_groups(level: str) -> None:
    before = _docs_alive()
    p = make_paper(random.Random(5), "p", False)
    assert len(text_search(p, "the", return_=level)) > 0
    assert _docs_alive() > before
    del p
    assert _docs_alive() == before
