"""A Doc shared by threads gives each of them what it gives one thread (ARCHITECTURE.md §2.3).

Each stage's text is the previous stage cleaned once more, and the stages are
published as one tuple, replaced whole. A stage list appended to in place could
hand a thread asking for stage 2 the stage-1 text (``'a , , b'`` cleans to
``'a, , b'`` and then to ``'a,, b'``). Masks, literal rows and grouped views are
published the same way.
"""

from __future__ import annotations

import functools
import random
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from metacheck.core.doc import Doc
from metacheck.core.patterns import Pat

TEXTS = ["a , , b", "x  , , , y", "p\n\nq , , r", "plain text", "a , , , , , b c"]
PATTERNS = [Pat(",,"), Pat(", ,"), Pat("a, b"), Pat(r"\s{2}", "pcre"), Pat("text")]
THREADS = 8
ROUNDS = 30


@pytest.fixture(autouse=True)
def _switch_often() -> Iterator[None]:
    """Switch threads as often as the interpreter allows, so that races show."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(interval)


def _doc() -> Doc:
    return Doc.from_strings(TEXTS * 60)


def _work(doc: Doc, order: list[tuple[str, int, int]]) -> dict[tuple[str, int, int], Any]:
    out: dict[tuple[str, int, int], Any] = {}
    for kind, stage, k in order:
        if kind == "field":
            out[kind, stage, k] = list(doc.field(stage))
        else:
            out[kind, stage, k] = doc.mask(PATTERNS[k], stage)
    return out


def _orders(rng: random.Random) -> list[list[tuple[str, int, int]]]:
    jobs = [("field", s, 0) for s in (1, 2, 3)]
    jobs += [("mask", s, k) for s in (0, 1, 2, 3) for k in range(len(PATTERNS))]
    orders = []
    for _ in range(THREADS):
        order = list(jobs)
        rng.shuffle(order)
        orders.append(order)
    return orders


def _in_threads(fns: list[Callable[[], Any]]) -> list[Any]:
    barrier = threading.Barrier(len(fns))
    results: list[Any] = [None] * len(fns)
    errors: list[BaseException] = []

    def run(i: int) -> None:
        barrier.wait()
        try:
            results[i] = fns[i]()
        except BaseException as exc:  # reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(fns))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results


def test_threads_sharing_a_doc_get_the_stages_one_thread_gets() -> None:
    rng = random.Random(7)
    want = _work(_doc(), _orders(rng)[0])
    assert want["field", 2, 0][0] == "a,, b"  # the race this pins: stage 1 is 'a, , b'
    for _ in range(ROUNDS):
        doc = _doc()
        orders = _orders(rng)
        got = _in_threads([functools.partial(_work, doc, o) for o in orders])
        for each in got:
            assert each == want


def test_threads_sharing_a_doc_get_the_groups_one_thread_gets() -> None:
    rows = list(range(len(TEXTS) * 60))
    want = _doc().groups("paper_id", rows, 1).text
    for _ in range(ROUNDS // 4):
        doc = _doc()
        got = _in_threads([functools.partial(_group_text, doc, rows)] * THREADS)
        assert all(g == want for g in got)


def _group_text(doc: Doc, rows: list[int]) -> list[str | None]:
    return doc.groups("paper_id", rows, 1).text
