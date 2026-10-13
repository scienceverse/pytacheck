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

**The mutation check.** With ``METACHECK_CHECK_MUTATION=1`` (CI and tests), the
outermost trusted scope also watches the papers it indexes: each time a Doc or
reference table is built or reused, it fingerprints any materialised text,
section, bibliography and matched bibliography tables (their column names,
dtypes, shape and every value),
and a later fingerprint that differs, with the same table objects, means that
somebody edited a table in place after the Doc was made.
:class:`~metacheck.core.errors.StaleDocumentError` is then raised by the next Doc
use, by :func:`check_mutation` (the runners call it between modules, naming the
module that ran before) and when the scope ends. A table replaced by another
object is not an edit: the Doc built from the old one can no longer be served.
With the variable off the scope keeps nothing and checks nothing.

**Counters.** :func:`counting` collects how often the core does its work (Doc
builds, group builds), for the tests that bound it. Outside it, :func:`count`
does nothing.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import itertools
from collections import Counter
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from metacheck._env import env_get
from metacheck.core.errors import StaleDocumentError

if TYPE_CHECKING:
    from metacheck.papers.model import Paper

__all__ = [
    "check_mutation",
    "count",
    "counting",
    "module_progress",
    "mutation_check_enabled",
    "note_module",
    "running_module",
    "trusted_scope",
    "trusted_token",
    "watch_paper",
]

_TOKENS = itertools.count(1)
_TRUSTED: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "metacheck_trusted_scope", default=None
)
_DERIVED: contextvars.ContextVar[dict[object, object] | None] = contextvars.ContextVar(
    "metacheck_run_derived", default=None
)
_COUNTS: Counter[str] | None = None
_WATCH: contextvars.ContextVar[_Watch | None] = contextvars.ContextVar(
    "metacheck_watched_papers", default=None
)
_TABLES = ("text", "section", "bib", "bib_match")
#: who wants to hear which module of a run is about to start (the app's progress bar)
_STEPS: contextvars.ContextVar[Callable[[int, int, str], None] | None] = contextvars.ContextVar(
    "metacheck_module_progress", default=None
)


def mutation_check_enabled() -> bool:
    """Whether ``METACHECK_CHECK_MUTATION`` is on (``1``, ``true``, ``yes`` or ``on``, any
    case); read when a trusted scope opens."""
    value = env_get("CHECK_MUTATION")
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _fingerprint(paper: Paper) -> tuple[tuple[Any, ...], str] | None:
    """``(identity, digest)`` of the indexed tables, or ``None`` when
    all are still JSON records (which no caller edits in place) or absent.

    The identity is what the tables are (the frames themselves, or the records'
    token); the digest hashes each frame's column names, dtypes, shape and values.
    """
    import pandas as pd

    ident: list[Any] = []
    h = hashlib.blake2b(digest_size=16)
    frames = 0
    for name in _TABLES:
        if paper._raw_records(name) is not None:
            ident.append((name, paper._lazy.get(name)))
            continue
        table = paper._tables.get(name)
        ident.append((name, table))
        if not isinstance(table, pd.DataFrame):
            continue
        frames += 1
        h.update(repr((name, table.shape, [str(c) for c in table.columns])).encode())
        h.update(repr([str(t) for t in table.dtypes]).encode())
        for i in range(table.shape[1]):
            col = table.iloc[:, i]
            try:
                h.update(pd.util.hash_pandas_object(col, index=False).to_numpy().tobytes())
            except TypeError:  # unhashable cells (lists, dicts)
                h.update(repr(col.tolist()).encode())
    if not frames:
        return None
    return tuple(ident), h.hexdigest()


class _Watch:
    """The papers one trusted scope fingerprinted (only while the check is on)."""

    __slots__ = ("entries", "module")

    def __init__(self) -> None:
        # id(paper) -> (paper, identity, digest); the paper is held so the id stays its own
        self.entries: dict[int, tuple[Paper, tuple[Any, ...], str]] = {}
        self.module: str | None = None  #: the module running now, for the messages

    def note(self, paper: Paper, rebuilt: bool) -> None:
        """A Doc was just built for (*rebuilt*) or served from *paper*."""
        fp = _fingerprint(paper)
        key = id(paper)
        if fp is None:
            self.entries.pop(key, None)
            return
        old = self.entries.get(key)
        if old is not None and not rebuilt and _same_tables(old[1], fp[0]) and old[2] != fp[1]:
            self._stale(paper, None)
        self.entries[key] = (paper, *fp)

    def verify(self, ran: str | None) -> None:
        """Raise when a watched paper's tables changed since their Doc was built."""
        for key, (paper, ident, digest) in list(self.entries.items()):
            fp = _fingerprint(paper)
            if fp is None or not _same_tables(ident, fp[0]):
                del self.entries[key]  # replaced: the old Doc can no longer be served
            elif fp[1] != digest:
                self._stale(paper, ran)

    def _stale(self, paper: Paper, ran: str | None) -> None:
        who = f"paper {paper.paper_id!r}"
        if ran is not None:
            when = f"the module {ran!r} edited an indexed table of {who} in place"
        elif self.module is not None:
            when = (
                f"an indexed table of {who} was edited in place, and the module "
                f"{self.module!r} used values cached before the edit"
            )
        else:
            when = f"an indexed table of {who} was edited in place"
        raise StaleDocumentError(
            f"{when} after its tables were indexed. A run reads each paper's tables once, so the "
            "modules after it would search the old values. Copy the paper (paper.copy()) "
            "before changing a table."
        )


def _same_tables(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    return all(x[0] == y[0] and x[1] is y[1] for x, y in zip(a, b, strict=True))


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
    reset_derived = _DERIVED.set({})
    watch = _Watch() if mutation_check_enabled() else None
    reset_watch = _WATCH.set(watch)
    try:
        yield token
        if watch is not None:  # not when the block raised: that error is the one to see
            watch.verify(watch.module)
    finally:
        _WATCH.reset(reset_watch)
        _DERIVED.reset(reset_derived)
        _TRUSTED.reset(reset)


def watch_paper(paper: Paper, rebuilt: bool) -> None:
    """Fingerprint *paper* (a Doc was built for it, or served from it) and check it
    against the last fingerprint; nothing unless the mutation check is on and a
    trusted scope is open."""
    watch = _WATCH.get()
    if watch is not None:
        watch.note(paper, rebuilt)


def check_mutation(ran: str | None = None) -> None:
    """Raise ``StaleDocumentError`` if a paper this scope indexed was edited in place.

    For a runner, between its modules: *ran* names the module that ran last (the
    one that did the edit). Nothing unless the mutation check is on.
    """
    watch = _WATCH.get()
    if watch is not None:
        watch.verify(ran)


@contextlib.contextmanager
def running_module(label: str) -> Iterator[None]:
    """Name the module running now, so that an error from a Doc served after an
    in-place edit says which module used it (nothing unless the check is on)."""
    watch = _WATCH.get()
    if watch is None:
        yield
        return
    before, watch.module = watch.module, label
    try:
        yield
    finally:
        watch.module = before


@contextlib.contextmanager
def module_progress(callback: Callable[[int, int, str], None]) -> Iterator[None]:
    """Tell *callback* ``(done, total, label)`` as a module runner starts each module of a
    run (``report_module_run()``, so also ``report()``), with ``done`` modules finished and
    ``total`` to run, and once more with ``done == total`` and an empty label when the last
    has finished. Only the runs of this thread (and of the threads that copy its context)
    are reported."""
    token = _STEPS.set(callback)
    try:
        yield
    finally:
        _STEPS.reset(token)


def note_module(done: int, total: int, label: str) -> None:
    """For the runners: tell the callback of :func:`module_progress`, if any. A callback that
    fails is not allowed to stop the run."""
    callback = _STEPS.get()
    if callback is not None:
        with contextlib.suppress(Exception):
            callback(done, total, label)


def trusted_token() -> int | None:
    """The token of the trusted scope in effect, or ``None`` outside one."""
    return _TRUSTED.get()


def derived_cache() -> dict[object, object] | None:
    """Values shared only until the outermost trusted run ends."""
    return _DERIVED.get()


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
