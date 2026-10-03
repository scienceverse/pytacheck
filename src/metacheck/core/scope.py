"""Trusted scopes and work counters.

This is the part of the run context (docs/design/ARCHITECTURE.md §2.6) that the
Doc cache needs now; CORE-1d moves it into the run context.

**Trusted scopes.** A paper's :class:`~metacheck.core.doc.Doc` is reused while
its text and section tables are still the reader's JSON records, which no
caller can edit in place. Once a table is a DataFrame, a user may have edited it
in place, which no cheap check sees, so the Doc is rebuilt on every use, except
inside a trusted scope: an internal runner (``run_modules()``,
``report()``) freezes its inputs by contract, so a Doc built inside its scope is
reused until the scope ends. A Doc records only the scope's opaque integer
token, never the runner or its caches. ``run_session()`` opens no trusted
scope: it is public, and its users may edit papers between runs.

**Counters.** :func:`counting` collects how often the core does its work (Doc
builds, group builds), for the tests that bound it. Outside it, :func:`count`
does nothing.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
from collections import Counter
from collections.abc import Iterator

__all__ = ["count", "counting", "trusted_scope", "trusted_token"]

_TOKENS = itertools.count(1)
_TRUSTED: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "metacheck_trusted_scope", default=None
)
_COUNTS: Counter[str] | None = None


@contextlib.contextmanager
def trusted_scope() -> Iterator[int]:
    """Open a trusted scope (internal runners only); yields its token.

    A scope opened inside another one is the outer scope: its Docs stay trusted
    until the outermost scope ends.
    """
    outer = _TRUSTED.get()
    if outer is not None:
        yield outer
        return
    token = next(_TOKENS)
    reset = _TRUSTED.set(token)
    try:
        yield token
    finally:
        _TRUSTED.reset(reset)


def trusted_token() -> int | None:
    """The token of the trusted scope in effect, or ``None`` outside one."""
    return _TRUSTED.get()


def count(name: str, n: int = 1) -> None:
    """Add *n* to the counter *name*, while :func:`counting` is collecting."""
    counts = _COUNTS
    if counts is not None:
        counts[name] += n


@contextlib.contextmanager
def counting() -> Iterator[Counter[str]]:
    """Collect the work counters for the duration of the block (tests, benchmarks).

    The counters are process-wide, not per thread; a nested block collects into
    the outer one's counter.
    """
    global _COUNTS
    outer = _COUNTS
    counts: Counter[str] = outer if outer is not None else Counter()
    _COUNTS = counts
    try:
        yield counts
    finally:
        _COUNTS = outer
