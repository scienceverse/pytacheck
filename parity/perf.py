"""Work counters and CPU per (module, input), for ``python -m parity bench``.

A :class:`Probe` measures today's code for the hard counters of
docs/design/ARCHITECTURE.md §5.1, without touching ``src/``::

    from parity import perf

    with perf.Probe() as probe:
        probe.input = "problems/203020.xml"
        module_run(paper, "ethics_check")
    probe.rows()  # [{"module": "ethics_check", "input": ..., "cpu_ms": ..., "counts": ...}]

Each top-level ``module_run`` call is timed with ``time.process_time()``. A run
inside it (``codebook_check`` running ``data_check``) counts towards the outer
one. The input is ``probe.input``, or the paper's id when that is ``None``.

The counters count calls of the functions in :data:`TARGETS`, and go to the
top-level run in progress (outside any run, to module ``None``). A probe takes
other targets too (``doc_builds``, once ``core/`` exists):

====================  =============================================================
``text_search``       ``text_search()`` calls
``paper_table``       ``paper_table()`` calls
``table_builds``      tables built from a paper's JSON records (``records_to_frame``)
``deepcopy``          top-level ``copy.deepcopy()`` calls
``regex_compiles``    patterns ``regex`` or ``re`` compiled for the first time
``regex_recompiles``  patterns compiled again, after their engine's cache dropped them
``csv_parses``        delimited files parsed (fread, read.table), also per file
``workbook_parses``   workbooks opened (xlsx, xls, ods), also per file
``code_decodes``      code files decoded (``code_read()``), also per file
``file_opens``        files opened with ``open()`` or ``io.open_code()``, also per file
====================  =============================================================

A compile is a recompile when a probe in this process saw the pattern compiled
before, or found it in its engine's cache when it started. ``regex_recompiles``
varies from run to run once a run uses more than 500 patterns: the ``regex``
package then drops a random fifth of its cache. Every other count is
deterministic, so two warm runs of the same work give the same numbers. Files
are keyed by their path, relative to the tree's root when they are in it.
Temporary files have random names, so compare their counts, not their keys.

``file_opens`` comes from an audit hook (``sys.addaudithook``). It does not see
``os.open()`` or files that C libraries open themselves (lxml parsing a path).
Imports inside the probe open ``.pyc`` files, so measure a warm run.

On Python 3.12 and later the calls are counted with ``sys.monitoring``: every
call, however the function was imported, and ``copy.deepcopy`` stays the
stdlib's own. On 3.11 each function is rebound in every loaded module while the
probe is active, which misses a reference taken before (a local variable, a
default argument). ``module_run`` is always rebound, since timing it needs its
return and its exceptions: call it through a module, or import it after the
probe starts. A target that does not resolve (in a tree from
before or after a refactor) is listed in ``Probe.missing``, and its counter is
left out, also from the targets that did resolve, so a 0 is always a measured 0.

The probe assumes that one thread runs modules at a time, and
``process_time()`` is the CPU of the whole process.
"""

from __future__ import annotations

import functools
import importlib
import os
import sys
import time
import types
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Target:
    """A function whose calls a counter counts."""

    counter: str
    where: str  # "package.module:function" or "package.module:Class.method"
    per_file: str | None = None  # also count per value of this argument (a path)
    top_level: str | None = None  # count only the calls where this argument is None
    # a compile: the caller whose frame holds its `pattern` and `flags`, and the
    # counter of a pattern compiled before
    front: str | None = None
    again: str = "regex_recompiles"

    def adds_to(self) -> set[str]:
        """The counters its calls count towards."""
        return {self.counter, self.again} if self.front else {self.counter}


TARGETS: tuple[Target, ...] = (
    Target("text_search", "pytacheck.text.search:text_search"),
    Target("paper_table", "pytacheck.papers.tables:paper_table"),
    Target("table_builds", "pytacheck.papers.schema:records_to_frame"),
    Target("deepcopy", "copy:deepcopy", top_level="memo"),
    # each runs once per pattern compiled, after the cache lookups
    Target(
        "regex_compiles", "regex._regex_core:_check_group_features", front="regex._main:_compile"
    ),
    Target("regex_compiles", "re._compiler:compile", front="re:_compile"),
    Target("csv_parses", "pytacheck.datacheck._files_fread:fread", per_file="path"),
    Target("csv_parses", "pytacheck.datacheck._files_readtable:read_table", per_file="path"),
    Target(
        "workbook_parses", "pytacheck.datacheck._files_readers:_XlsxBook.__init__", per_file="path"
    ),
    Target(
        "workbook_parses", "pytacheck.datacheck._files_readers:_XlsBook.__init__", per_file="path"
    ),
    Target("workbook_parses", "pytacheck.datacheck._files_readers:_ods_strings", per_file="path"),
    Target("workbook_parses", "pytacheck.datacheck._columns_codebook:_ods_root", per_file="path"),
    Target("code_decodes", "pytacheck.codecheck._encoding:code_read_bytes", per_file="name"),
)

#: the entry point that is timed
RUN = "pytacheck.module:module_run"
FILE_OPENS = "file_opens"

# sys.monitoring tools the probe may use: not the debugger (0), coverage (1),
# the profiler (2, cProfile's) or the optimizer (5)
_TOOL_IDS = (3, 4)
# how far up from the counting code a compile's front frame may be
_FRONT_DEPTH = 6

Key = tuple[str | None, str | None]  # (module, input)


@dataclass
class Usage:
    """The top-level ``module_run`` calls of one (module, input): their CPU
    seconds and number, and the counters."""

    cpu: float = 0.0
    runs: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    files: defaultdict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, other: Usage) -> None:
        self.cpu += other.cpu
        self.runs += other.runs
        self.counts.update(other.counts)
        for counter, per_file in other.files.items():
            self.files[counter].update(per_file)


@dataclass(frozen=True)
class _Hook:
    """A resolved target: where its function lives and where its argument is."""

    target: Target
    owner: Any  # the module or class
    name: str
    raw: Any  # the attribute as the owner holds it (maybe a classmethod)
    func: types.FunctionType
    arg: str | None
    index: int | None  # the argument's position
    front: types.CodeType | None
    engine: Any  # the front's module, which holds the engine's caches


def _function(where: str) -> tuple[Any, str, Any, types.FunctionType]:
    module, _, qualname = where.partition(":")
    owner: Any = importlib.import_module(module)
    *path, name = qualname.split(".")
    for part in path:
        owner = getattr(owner, part)
    raw = owner.__dict__[name]
    func = getattr(raw, "__func__", raw)  # a classmethod's or staticmethod's function
    if not isinstance(func, types.FunctionType):
        raise TypeError(f"{where} is not a Python function")
    return owner, name, raw, func


def _resolve(target: Target) -> _Hook:
    owner, name, raw, func = _function(target.where)
    arg = target.per_file or target.top_level
    index = None
    if arg is not None:
        code = func.__code__
        index = code.co_varnames.index(arg)
        if index >= code.co_argcount + code.co_kwonlyargcount:
            raise ValueError(f"{target.where} has no argument {arg!r}")
    front, engine = None, None
    if target.front:
        engine, _, _, compile_ = _function(target.front)
        front = compile_.__code__
    return _Hook(target, owner, name, raw, func, arg, index, front, engine)


def _path(value: Any) -> str:
    path = os.fsdecode(value) if isinstance(value, str | bytes | os.PathLike) else str(value)
    root = f"{ROOT}{os.sep}"
    return Path(path[len(root) :]).as_posix() if path.startswith(root) else path


def _label(module: Any) -> str:
    """The module a ``module_run`` call names: its name, or a module file's stem."""
    if isinstance(module, str):
        return module
    if isinstance(module, os.PathLike):
        return Path(module).stem
    spec = getattr(module, "__pytacheck_module__", module)
    return str(getattr(spec, "name", None) or getattr(module, "__name__", module))


def _input_of(paper: Any) -> str | None:
    """The paper's id, also for a previous module's output."""
    if type(paper).__name__ == "ModuleOutput":
        paper = paper.paper
    pid = getattr(paper, "paper_id", None)
    return pid if isinstance(pid, str) else None


def _pattern(front: types.CodeType) -> Hashable | None:
    """``(front, pattern, flags)`` of the compile in progress, from *front*'s frame."""
    frame: types.FrameType | None = sys._getframe(2)
    for _ in range(_FRONT_DEPTH):
        if frame is None:
            return None
        if frame.f_code is front:
            names = frame.f_locals
            return (front, names.get("pattern"), names.get("flags"))
        frame = frame.f_back
    return None


def _cached(hook: _Hook) -> Iterator[Hashable]:
    """The patterns in the caches of *hook*'s engine, as :func:`_pattern` gives them."""
    for name in ("_cache", "_cache2"):
        cache = getattr(hook.engine, name, None)
        for key in list(cache) if isinstance(cache, dict) else ():
            if isinstance(key, tuple) and len(key) >= 3:
                # re's keys are (type, pattern, flags), regex's (pattern, type, flags, ...)
                pattern = key[1] if isinstance(key[0], type) else key[0]
                yield (hook.front, pattern, key[2])


_ACTIVE: Probe | None = None
_AUDITING = False  # an audit hook cannot be removed: it is added once, and does nothing idle
_COMPILED: set[Hashable] = set()  # the patterns probes in this process saw compiled or cached


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _ACTIVE is not None and _ACTIVE.opens:
        path, mode = args[0], args[1]
        # mode is None for os.open(), an int path is a file descriptor
        if mode is not None and not isinstance(path, int):
            usage = _ACTIVE._here()
            usage.counts[FILE_OPENS] += 1
            usage.files[FILE_OPENS][_path(path)] += 1


class Probe:
    """Counts and times while active (``with Probe() as probe:``); one at a time.

    ``monitoring=False`` rebinds the functions on 3.12 and later too.
    """

    def __init__(
        self,
        targets: Iterable[Target] = TARGETS,
        *,
        opens: bool = True,
        monitoring: bool | None = None,
    ) -> None:
        self.targets = tuple(targets)
        self.opens = opens
        self.monitoring = sys.version_info >= (3, 12) if monitoring is None else monitoring
        #: the input the next top-level runs are counted under (None: the paper's id)
        self.input: str | None = None
        self.usage: defaultdict[Key, Usage] = defaultdict(Usage)
        #: the targets that did not resolve, and the counters measured
        self.missing: list[str] = []
        self.counters: tuple[str, ...] = ()
        self.mechanism = ""
        self._run: Usage | None = None  # the top-level run in progress
        self._by_code: dict[types.CodeType, _Hook] = {}
        self._undo: list[Callable[[], None]] = []

    # -- results -----------------------------------------------------------------------

    def total(self) -> Usage:
        out = Usage()
        for usage in self.usage.values():
            out.add(usage)
        return out

    def rows(self) -> list[dict[str, Any]]:
        """One row per (module, input), sorted, every measured counter included."""

        def order(key: Key) -> tuple[str, str]:
            return (key[0] or "", key[1] or "")

        return [
            {
                "module": module,
                "input": inp,
                "runs": usage.runs,
                "cpu_ms": round(1000 * usage.cpu, 3),
                "counts": {c: usage.counts[c] for c in self.counters},
                "files": {c: dict(sorted(usage.files[c].items())) for c in sorted(usage.files)},
            }
            for (module, inp), usage in sorted(self.usage.items(), key=lambda kv: order(kv[0]))
        ]

    def reset(self) -> None:
        """Forget what was measured so far (after a warm-up, say)."""
        self.usage.clear()

    # -- collection --------------------------------------------------------------------

    def __enter__(self) -> Probe:
        global _ACTIVE, _AUDITING
        if _ACTIVE is not None:
            raise RuntimeError("another Probe is active")
        hooks: list[_Hook] = []
        self.missing = []
        partial: set[str] = set()  # counters that would count only part of the work
        rebind: dict[int, tuple[Any, Any]] = {}
        for target in (Target("", RUN), *self.targets):
            try:
                hook = _resolve(target)
            except Exception:
                self.missing.append(target.where)
                partial |= target.adds_to()
                continue
            if target.where == RUN:
                rebind[id(hook.func)] = (hook.func, self._timed(hook.func))
            else:
                hooks.append(hook)
        hooks = [h for h in hooks if not h.target.adds_to() & partial]
        counters = {c for h in hooks for c in h.target.adds_to()}
        for h in hooks:
            if h.front is not None:  # a pattern cached now was compiled before
                _COMPILED.update(_cached(h))
        self.counters = tuple(sorted(counters | ({FILE_OPENS} if self.opens else set())))
        try:
            if not (self.monitoring and self._monitor(hooks)):
                self.mechanism = "rebinding"
                rebind.update({id(h.func): (h.func, self._counted(h)) for h in hooks})
            self._rebind(rebind, hooks)
        except Exception:
            self.__exit__(None, None, None)
            raise
        if self.opens and not _AUDITING:
            sys.addaudithook(_audit)
            _AUDITING = True
        _ACTIVE = self
        return self

    def __exit__(self, *exc: object) -> None:
        global _ACTIVE
        _ACTIVE = None
        self._run = None
        while self._undo:
            self._undo.pop()()

    def _here(self) -> Usage:
        return self._run if self._run is not None else self.usage[(None, self.input)]

    def _count(self, hook: _Hook, value: Any) -> None:
        target = hook.target
        if target.top_level is not None and value is not None:
            return
        counter = target.counter
        if hook.front is not None:
            key = _pattern(hook.front)
            if key is not None:
                if key in _COMPILED:
                    counter = target.again
                _COMPILED.add(key)
        usage = self._here()
        usage.counts[counter] += 1
        if target.per_file is not None:
            usage.files[counter][_path(value)] += 1

    def _monitor(self, hooks: list[_Hook]) -> bool:
        """Count the calls with ``sys.monitoring``; False without it (3.11) or when no
        tool id is free."""
        mon = getattr(sys, "monitoring", None)
        if mon is None:
            return False
        tool = next((i for i in _TOOL_IDS if mon.get_tool(i) is None), None)
        if tool is None:
            return False
        mon.use_tool_id(tool, "parity.perf")
        self._by_code = {h.func.__code__: h for h in hooks}

        def undo() -> None:
            for code in self._by_code:
                mon.set_local_events(tool, code, 0)
            mon.register_callback(tool, mon.events.PY_START, None)
            mon.free_tool_id(tool)

        self._undo.append(undo)
        mon.register_callback(tool, mon.events.PY_START, self._on_start)
        for code in self._by_code:
            mon.set_local_events(tool, code, mon.events.PY_START)
        self.mechanism = "sys.monitoring"
        return True

    def _on_start(self, code: types.CodeType, offset: int) -> None:
        hook = self._by_code.get(code)
        if hook is not None and _ACTIVE is self:
            # the frame that starts is the caller's: this callback runs inside it
            value = sys._getframe(1).f_locals.get(hook.arg) if hook.arg else None
            self._count(hook, value)

    def _counted(self, hook: _Hook) -> Callable[..., Any]:
        func, index, arg = hook.func, hook.index, hook.arg

        @functools.wraps(func)
        def counted(*args: Any, **kwargs: Any) -> Any:
            if _ACTIVE is self:
                if index is not None and index < len(args):
                    value = args[index]
                else:
                    value = kwargs.get(arg) if arg else None
                self._count(hook, value)
            return func(*args, **kwargs)

        return counted

    def _timed(self, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def module_run(*args: Any, **kwargs: Any) -> Any:
            if _ACTIVE is not self or self._run is not None:
                return func(*args, **kwargs)
            paper = args[0] if args else kwargs.get("paper")
            module = args[1] if len(args) > 1 else kwargs.get("module")
            inp = self.input if self.input is not None else _input_of(paper)
            run = self._run = self.usage[(_label(module), inp)]
            start = time.process_time()
            try:
                return func(*args, **kwargs)
            finally:
                run.cpu += time.process_time() - start
                run.runs += 1
                self._run = None

        return module_run

    def _rebind(self, swaps: dict[int, tuple[Any, Any]], hooks: list[_Hook]) -> None:
        """Put each wrapper in place of its function in every loaded module (and on the
        class of a method), and undo it on exit, also in modules imported meanwhile."""
        back = {id(new): (new, old) for old, new in swaps.values()}
        _swap(swaps)
        self._undo.append(lambda: _swap(back))
        for h in hooks:
            if isinstance(h.owner, type) and id(h.func) in swaps:
                new = swaps[id(h.func)][1]
                setattr(h.owner, h.name, new if h.raw is h.func else type(h.raw)(new))
                self._undo.append(functools.partial(setattr, h.owner, h.name, h.raw))


def _swap(swaps: dict[int, tuple[Any, Any]]) -> None:
    """Replace every module-level reference to each ``old`` with its ``new``."""
    for module in list(sys.modules.values()):
        if not isinstance(module, types.ModuleType):
            continue
        names = module.__dict__
        for name, value in list(names.items()):
            swap = swaps.get(id(value))
            if swap is not None and value is swap[0]:
                names[name] = swap[1]
