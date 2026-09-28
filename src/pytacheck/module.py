"""The module system: define, find, describe and run checks.

A module is a function that takes a paper (or paper list) and returns a
dict with some of ``table``, ``summary_table``, ``na_replace``,
``traffic_light``, ``summary_text``, ``report`` and any extra results.
Declare one with :func:`module`::

    from pytacheck import module, text_search

    @module(
        title="Marginal Significance",
        description="List all sentences that describe an effect as 'marginally significant'.",
        keywords=["results"],
        author=["Daniel Lakens <D.Lakens@tue.nl>"],
    )
    def marginal(paper):
        table = text_search(paper, "margin\\\\w* significan\\\\w*")
        ...
        return {"table": table, "traffic_light": "red", ...}

Modules are found, in order, among the built-ins (``pytacheck.modules``),
``pack::name`` refs to active packs, installed plugins (entry-point group
``pytacheck.modules``), ``./<name>.py`` and ``./modules/<name>.py``, the
active packs (bare names), or by an explicit path to a ``.py`` file — the
same search metacheck's ``module_find()`` does for ``.R`` files, extended
with packs (see ``docs/design/module-system-v2.md``).
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import importlib
import importlib.util
import inspect
import pkgutil
import re
import sys
import textwrap
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character, r_sort_key
from pytacheck.papers.model import Paper, PaperList

__all__ = [
    "SECTION_LEVELS",
    "TRAFFIC_LIGHTS",
    "ModuleError",
    "ModuleOutput",
    "ModuleSpec",
    "RunSession",
    "get_prev_outputs",
    "module",
    "module_find",
    "module_help",
    "module_info",
    "module_list",
    "module_run",
    "run_session",
    "use",
]

SECTION_LEVELS = ("general", "intro", "method", "results", "discussion", "reference")
TRAFFIC_LIGHTS = ("na", "fail", "info", "green", "yellow", "red")
#: Capabilities a module can declare with ``requires=`` (``offline`` drops them).
CAPABILITIES = ("network", "llm")
_MODULE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class ModuleError(RuntimeError):
    """A module could not be found, loaded or run."""


@dataclass(frozen=True)
class ModuleSpec:
    """Metadata for a module (metacheck's roxygen header)."""

    name: str
    func: Callable[..., Any]
    title: str
    description: str = ""
    details: str = ""
    keywords: tuple[str, ...] = ()
    author: tuple[str, ...] = ()
    params: Mapping[str, str] = field(default_factory=dict)
    returns: str = "a list"
    path: str | None = None
    #: the pack the module came from (``"metacheck"`` for built-ins)
    pack: str | None = None
    #: capabilities it needs, e.g. ``("network",)`` or ``("llm",)``
    requires: tuple[str, ...] = ()
    #: structured validation record (``papers``, ``tp``, ``fp``, ``fn``, ``reference``...)
    validation: Mapping[str, Any] | None = None

    @property
    def section(self) -> str:
        """The report section: the first keyword that is a section level."""
        for k in self.keywords:
            if k in SECTION_LEVELS:
                return k
        return "general"

    @property
    def arg_defaults(self) -> dict[str, Any]:
        """Default values of the module function's arguments."""
        sig = inspect.signature(self.func)
        return {
            k: (None if v.default is inspect.Parameter.empty else v.default)
            for k, v in sig.parameters.items()
            if v.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        }

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


_AUTHOR_EMAIL = re.compile(r"^(.*?)\s*<([^<>@\s]+@[^<>\s]+)>$")


def _roxygen_author(author: str) -> str:
    """``Name <email>`` as metacheck's roxygen ``Name (\\email{email})``.

    module_report() strips the email with R's own pattern, so the stored
    form must be R's for the acknowledgement line to match.
    """
    m = _AUTHOR_EMAIL.match(author.strip())
    return f"{m.group(1)} (\\email{{{m.group(2)}}})" if m else author


def module(
    name: str | None = None,
    *,
    title: str,
    description: str = "",
    details: str = "",
    keywords: Sequence[str] = (),
    author: Sequence[str] = (),
    params: Mapping[str, str] | None = None,
    returns: str = "a list",
    requires: Sequence[str] = (),
    validation: Mapping[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator declaring a function as a pytacheck module.

    ``keywords`` stay faithful to R (the report section); capabilities go in
    ``requires`` (``"network"``, ``"llm"``). For backwards compatibility
    ``"llm"`` / ``"network"`` given as keywords are moved into ``requires``.
    """
    if isinstance(keywords, str):
        keywords = [keywords]
    if isinstance(requires, str):
        requires = [requires]
    caps = [*requires, *(k for k in keywords if k in CAPABILITIES)]

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        mod = sys.modules.get(func.__module__)
        path = getattr(mod, "__file__", None)
        spec = ModuleSpec(
            name=name or func.__name__,
            func=func,
            title=title,
            description=textwrap.dedent(description).strip(),
            details=textwrap.dedent(details).strip(),
            keywords=tuple(k for k in keywords if k not in CAPABILITIES),
            author=tuple(_roxygen_author(a) for a in author),
            params=dict(params or {"paper": "a paper object or paperlist object"}),
            returns=returns,
            path=path,
            pack=_pack_of(func.__module__),
            requires=tuple(dict.fromkeys(caps)),
            validation=dict(validation) if validation is not None else None,
        )
        func.__pytacheck_module__ = spec  # type: ignore[attr-defined]
        return func

    return decorate


def _pack_of(modname: str) -> str | None:
    """The pack of a Python module name (``pytacheck.modules.*`` is ``metacheck``)."""
    if modname.startswith("pytacheck.modules."):
        return "metacheck"
    if modname.startswith("pytacheck_packs."):
        pkg = sys.modules.get(".".join(modname.split(".")[:2]))
        return getattr(pkg, "__pytacheck_pack__", None)
    return None


# ---------------------------------------------------------------------------
# Policy: pc.use(...) and run sessions
# ---------------------------------------------------------------------------

_USE: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar(
    "pytacheck_use",
    default={},  # noqa: B039 - never mutated
)


@contextlib.contextmanager
def use(
    preset: str | None = None, *, allow_local: bool | None = None, offline: bool | None = None
) -> Iterator[None]:
    """Scope settings to a ``with`` block (a ContextVar, so threads/tasks are isolated).

    * ``preset``: the preset :func:`pytacheck.presets.select` uses when none is given;
    * ``allow_local=False``: no ``./name.py``, ``./modules/name.py``, file paths
      or path packs (the API server runs like this);
    * ``offline=True``: selection drops modules that require ``network`` or ``llm``.

    ``None`` keeps the enclosing setting.
    """
    given = {"preset": preset, "allow_local": allow_local, "offline": offline}
    new = {**_USE.get(), **{k: v for k, v in given.items() if v is not None}}
    token = _USE.set(new)
    try:
        yield
    finally:
        _USE.reset(token)


def use_setting(key: str, default: Any = None) -> Any:
    """The value set by the innermost :func:`use` for *key* (or *default*)."""
    return _USE.get().get(key, default)


def _allow_local() -> bool:
    return bool(_USE.get().get("allow_local", True))


class RunSession:
    """Memo of :func:`module_run` calls on plain papers (see :func:`run_session`)."""

    def __init__(self) -> None:
        self._memo: dict[tuple[Any, ...], ModuleOutput] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._memo)

    def get(self, key: tuple[Any, ...]) -> ModuleOutput | None:
        with self._lock:
            out = self._memo.get(key)
            if out is None:
                self.misses += 1
                return None
            self.hits += 1
        return _shallow_copy(out)

    def put(self, key: tuple[Any, ...], out: ModuleOutput) -> None:
        with self._lock:
            self._memo[key] = _shallow_copy(out)

    def clear(self) -> None:
        with self._lock:
            self._memo.clear()


_SESSION: contextvars.ContextVar[RunSession | None] = contextvars.ContextVar(
    "pytacheck_run_session", default=None
)


@contextlib.contextmanager
def run_session() -> Iterator[RunSession]:
    """Memoise :func:`module_run` on plain papers inside a ``with`` block.

    The key is the paper's identity and state (its generation, bumped by
    changes made through the ``Paper`` API, its table objects and ``extra``),
    the module's identity (pack, rev, file hash and function), its bound
    arguments and the LLM options, so implicit re-runs (``data_check`` inside
    ``codebook_check``...) happen once. Edits made *inside* a table are not
    seen, so do not mutate papers within a session (``report()``, the CLI and
    the API never do). Failures are not cached; a hit returns a copy that can
    be edited without changing the memo. Nested sessions share the outermost
    one.
    """
    current = _SESSION.get()
    if current is not None:
        yield current
        return
    session = RunSession()
    token = _SESSION.set(session)
    try:
        yield session
    finally:
        _SESSION.reset(token)


class _Unfreezable(Exception):
    pass


def _first(item: tuple[Any, ...]) -> Any:
    return item[0]


def _freeze(value: Any) -> Any:
    """A hashable, type-tagged form of an argument value (for memo keys)."""
    if value is None or isinstance(value, bool | int | float | str | bytes):
        return (type(value).__name__, value)
    if isinstance(value, list | tuple):
        return ("seq", tuple(_freeze(v) for v in value))
    if isinstance(value, Mapping):
        return ("map", tuple(sorted(((repr(k), _freeze(v)) for k, v in value.items()), key=_first)))
    if isinstance(value, set | frozenset):
        return ("set", frozenset(_freeze(v) for v in value))
    if isinstance(value, Path):
        return ("path", str(value))
    raise _Unfreezable


def _paper_state(paper: Paper) -> tuple[Any, Any, Any] | None:
    """``(generation, table identities, frozen extra)`` of a paper, or ``None``.

    The generation tracks changes made through the ``Paper`` API; the table
    identities catch tables replaced behind its back (e.g. through a
    ``copy.copy()`` sharing the table dict). Edits *inside* a table are not
    seen: papers must not be mutated in place.
    """
    gen = getattr(paper, "_generation", None)
    tables = getattr(paper, "_tables", None)
    if gen is None or not isinstance(tables, dict):
        return None
    # a table read from JSON records keeps its token while the paper holds the frame
    # it built from them, so building it does not change the key; any other frame
    # put in its place (even behind the API's back) has its own identity
    lazy = getattr(paper, "_lazy", None) or {}
    ids = []
    for name, value in tables.items():
        token = lazy.get(name)
        if token is not None and value is token.built:
            ids.append((name, id(token)))
        elif value is not None:
            ids.append((name, id(value)))
    try:
        extra = _freeze(paper.extra)
    except _Unfreezable:
        return None
    return gen, tuple(ids), extra


def _paper_key(paper: Any) -> tuple[Any, ...] | None:
    """The memo key part for a paper: ``(kind, id, generations, tables, extra)``."""
    if isinstance(paper, Paper):
        state = _paper_state(paper)
        return None if state is None else ("paper", id(paper), *state)
    if isinstance(paper, PaperList):
        states = [_paper_state(p) for p in paper]
        if any(s is None for s in states):
            return None
        return (
            "paperlist",
            id(paper),
            tuple(s[0] for s in states),  # type: ignore[index]
            tuple(s[1] for s in states),  # type: ignore[index]
            tuple(s[2] for s in states),  # type: ignore[index]
        )
    return None


#: global options that change what modules return (``llm_use()``, ``llm_model()``...)
_MEMO_OPTIONS = (
    "metacheck.llm.use",
    "metacheck.llm.model",
    "metacheck.llm_reasoning",
    "metacheck.llm_max_tokens",
    "pytacheck.concepts",
    "pytacheck.concepts.model",
    "pytacheck.concepts.threshold",
)


def _memo_options() -> Any:
    from pytacheck.utils import get_option

    return _freeze([get_option(k) for k in _MEMO_OPTIONS])


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@cache
def _builtin_names() -> tuple[str, ...]:
    import pytacheck.modules as pkg

    return tuple(
        sorted(
            m.name
            for m in pkgutil.iter_modules(pkg.__path__)
            if not m.name.startswith("_") and not m.ispkg
        )
    )


def _spec_from_pymodule(pymod: Any, name: str) -> ModuleSpec:
    candidates: list[ModuleSpec] = [
        obj.__pytacheck_module__
        for obj in vars(pymod).values()
        if callable(obj)
        and hasattr(obj, "__pytacheck_module__")
        and obj.__module__ == pymod.__name__
    ]
    for spec in candidates:
        if spec.name == name:
            return spec
    if candidates:
        return candidates[0]
    func = getattr(pymod, name, None)
    if callable(func):
        doc = inspect.getdoc(func) or ""
        title = doc.splitlines()[0] if doc else name
        return ModuleSpec(name=name, func=func, title=title, path=getattr(pymod, "__file__", None))
    raise ModuleError(f"The module '{name}' does not define a module function")


def _load_file(path: Path) -> ModuleSpec:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8", "surrogateescape")).hexdigest()
    mod_name = f"pytacheck_user_module_{path.stem}_{digest[:16]}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ModuleError(f"Cannot load module file {path}")
    pymod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = pymod
    try:
        spec.loader.exec_module(pymod)
    except Exception as exc:
        from pytacheck.log import logger

        logger(path.stem, {"error": str(exc)})
        raise ModuleError(f"The module '{path.stem}' has errors: {exc}") from exc
    found = _spec_from_pymodule(pymod, path.stem)
    return replace(found, path=str(path))


def _entry_point_specs() -> dict[str, Any]:
    try:
        return {ep.name: ep for ep in entry_points(group="pytacheck.modules")}
    except Exception:  # pragma: no cover - broken plugin metadata
        return {}


_QUALIFIED_RE = re.compile(r"^([a-z][a-z0-9_-]{1,39})::([A-Za-z][A-Za-z0-9_]*)$")


def split_ref(ref: Any) -> tuple[str, str] | None:
    """``(pack, module)`` of a ``pack::name`` ref, else ``None``.

    Only refs whose two sides are a valid pack and module name count, so a
    legacy file path containing ``::`` (``"a::b.py"``, or an existing
    ``ab::cd`` / ``./ab::cd.py`` / ``./modules/ab::cd.py`` file) is still a path.
    """
    if not isinstance(ref, str) or "::" not in ref:
        return None
    m = _QUALIFIED_RE.match(ref)
    if m is None:
        return None
    if _allow_local() and any(
        p.is_file() for p in (Path(ref), Path(f"{ref}.py"), Path("modules") / f"{ref}.py")
    ):
        return None
    return m.group(1), m.group(2)


def _locate(key: str) -> tuple[str, Any]:
    """Where a module ref resolves, without importing it.

    Returns ``(kind, where)``: ``("builtin", name)``, ``("pack", (pack, name))``,
    ``("entry_point", ep)`` or ``("file", path)``. Search order:

    1. a built-in name (fast: no config is read);
    2. ``pack::name`` (``metacheck::x`` is ``x``);
    3. legacy ``pytacheck.modules`` entry points, ``./<name>.py``, ``./modules/<name>.py``;
    4. the active packs (an error if several provide the name);
    5. a path to an existing file.

    ``use(allow_local=False)`` skips the file lookups, paths and path packs.
    """
    if key in _builtin_names():
        return ("builtin", key)
    from pytacheck.config import ConfigError

    qualified = split_ref(key)
    if qualified is not None:
        pack_name, mod = qualified
        if pack_name == "metacheck":
            if mod in _builtin_names():
                return ("builtin", mod)
            raise ModuleError(
                f"The pack 'metacheck' has no module '{mod}'. "
                f"Its modules are: {', '.join(_builtin_names())}"
            )
        from pytacheck.packs.registry import get_pack

        try:
            pack = get_pack(pack_name, allow_local=_allow_local())
        except ConfigError as exc:
            raise ModuleError(f"Cannot resolve {key}: {exc}") from exc
        if not pack.has_module(mod):
            mods = pack.modules()
            raise ModuleError(
                f"The pack '{pack_name}' has no module '{mod}'. "
                + (f"Its modules are: {', '.join(mods)}" if mods else "It has no modules.")
            )
        return ("pack", (pack, mod))
    eps = _entry_point_specs()
    if key in eps:
        return ("entry_point", eps[key])
    local = _allow_local()
    if local:
        for candidate in (Path(f"{key}.py"), Path("modules") / f"{key}.py"):
            if candidate.is_file():
                return ("file", candidate)
    problems: list[str] = []
    if _MODULE_NAME_RE.match(key):
        from pytacheck.packs.registry import find_module, registry

        try:
            found = find_module(key, allow_local=local)
            problems = list(registry().problems.values())
        except ConfigError as exc:  # a broken config file must not hide built-ins or paths
            found, problems = [], [str(exc)]
        if len(found) > 1:
            refs = ", ".join(f'"{p.name}::{key}"' for p in found)
            raise ModuleError(
                f"More than one pack provides the module '{key}'; use one of {refs} instead."
            )
        if found:
            return ("pack", (found[0], key))
    if local:
        path = Path(key)
        if path.is_file():
            return ("file", path)
    from pytacheck.log import logger

    logger("module_find", {"module": key, "error": "Can't find module"})
    msg = (
        f"There were no modules that matched {key}\n"
        "use module_list() to see a list of built-in modules."
    )
    if problems:
        msg += "\nSome configured packs are unavailable:\n" + "\n".join(f"* {v}" for v in problems)
    raise ModuleError(msg)


def module_find(name: str | Path | ModuleSpec | Callable[..., Any]) -> ModuleSpec:
    """Resolve a module name, ``pack::name`` ref, path, spec or decorated function to its spec."""
    if isinstance(name, ModuleSpec):
        return name
    if callable(name) and hasattr(name, "__pytacheck_module__"):
        return name.__pytacheck_module__  # type: ignore[no-any-return]
    kind, where = _locate(str(name))
    if kind == "builtin":
        pymod = importlib.import_module(f"pytacheck.modules.{where}")
        return _spec_from_pymodule(pymod, where)
    if kind == "pack":
        from pytacheck.packs.registry import load_module

        return load_module(*where)
    if kind == "entry_point":
        obj = where.load()
        if hasattr(obj, "__pytacheck_module__"):
            return obj.__pytacheck_module__  # type: ignore[no-any-return]
        return _spec_from_pymodule(obj, str(name))
    return _load_file(where)


def module_info(name: str | Path | ModuleSpec | Callable[..., Any]) -> ModuleSpec:
    """Metadata for a module (``module_info()``)."""
    return module_find(name)


def _iter_specs(module_dir: str | Path | None) -> Iterator[ModuleSpec]:
    if module_dir is not None and not _allow_local():
        raise ModuleError("Local module folders are not allowed here (allow_local=False)")
    if module_dir is None:
        for n in _builtin_names():
            try:
                yield module_find(n)
            except ModuleError:
                continue
        return
    for path in sorted(Path(module_dir).rglob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            yield _load_file(path)
        except ModuleError:
            continue


def _iter_pack_specs(pack: str) -> Iterator[ModuleSpec]:
    from pytacheck.packs.registry import active_packs, get_pack, load_module

    local = _allow_local()
    packs = (
        list(active_packs(allow_local=local).values())
        if pack == "*"
        else [get_pack(pack, allow_local=local)]
    )
    for p in packs:
        for n in p.modules():
            try:
                yield load_module(p, n)
            except ModuleError:
                continue


def module_list(module_dir: str | Path | None = None, *, pack: str | None = None) -> pd.DataFrame:
    """A table of available modules, sorted by report section then name.

    With no arguments this lists the built-in modules. ``pack="name"`` lists
    one active pack and ``pack="*"`` every active pack; both add a ``pack``
    column.
    """
    if pack is not None and module_dir is not None:
        raise ValueError("module_list(): give module_dir or pack, not both")
    columns = ["name", "title", "description", "section", "path"]
    specs = _iter_specs(module_dir) if pack is None else _iter_pack_specs(pack)
    rows = [
        {
            "name": Path(s.path).stem if s.path and module_dir is not None else s.name,
            "title": s.title,
            "description": s.description,
            "section": s.keywords[0] if s.keywords else "general",
            "path": s.path,
            **({"pack": s.pack or "metacheck"} if pack is not None else {}),
        }
        for s in specs
    ]
    if pack is not None:
        columns.append("pack")
    df = pd.DataFrame(rows, columns=columns)
    df["section"] = pd.Categorical(df["section"], categories=list(SECTION_LEVELS), ordered=True)
    order = sorted(
        range(len(df)),
        key=lambda i: (
            SECTION_LEVELS.index(df["section"].iloc[i])
            if not pd.isna(df["section"].iloc[i])
            else len(SECTION_LEVELS),
            r_sort_key(df["name"].iloc[i]),
        ),
    )
    return df.iloc[order].reset_index(drop=True)


def module_help(name: str | None = None) -> str:
    """Help text for a module (or the module list when *name* is ``None``)."""
    if name is None:
        return format_module_list(module_list())
    spec = module_find(name)
    params = {k: v for k, v in spec.arg_defaults.items() if k != "paper"}
    extra = "".join(f", {k} = {v!r}" for k, v in params.items())
    usage = f'module_run(paper, "{name}"{extra})'
    args = "\n".join(f"- {k}: {v}  " for k, v in spec.params.items())
    parts = [
        spec.title or "{no title}",
        spec.description or "{no description}",
        usage,
        args,
        spec.details,
    ]
    text = "\n\n".join(p for p in parts if p is not None)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def format_module_list(df: pd.DataFrame) -> str:
    """The printed form of :func:`module_list` (``print.metacheck_module_list``)."""
    out: list[str] = []
    for level in SECTION_LEVELS:
        sub = df[df["section"] == level]
        if len(sub) == 0:
            continue
        out.append(f"\n*** {level.upper()} ***\n\n")
        out.extend(f"* {n}: {d}\n" for n, d in zip(sub["name"], sub["description"], strict=True))
    out.append('\nUse `module_help("module_name")` for help with a specific module\n')
    return "".join(out)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

_PREV_OUTPUTS: contextvars.ContextVar[dict[str, ModuleOutput]] = contextvars.ContextVar(
    "pytacheck_prev_outputs",
    default={},  # noqa: B039 - never mutated
)


def get_prev_outputs(module: str, item: str) -> Any:
    """An item returned by an earlier module in the current chain, or ``None``.

    Only meaningful inside a module function while :func:`module_run` runs
    it, like metacheck's ``get_prev_outputs()``.
    """
    prev = _PREV_OUTPUTS.get().get(module)
    if prev is None:
        return None
    return prev.get(item)


@dataclass
class ModuleOutput:
    """The result of :func:`module_run` (metacheck's ``metacheck_module_output``)."""

    module: str
    title: str
    section: str
    table: Any = None
    report: Any = ""
    traffic_light: str = "info"
    summary_text: Any = None
    summary_table: pd.DataFrame | None = None
    paper: Any = None
    prev_outputs: dict[str, ModuleOutput] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    #: which module/pack/rev/args produced this output (see :mod:`pytacheck.provenance`);
    #: not an element: excluded from ``keys()``, ``results()``, equality and parity.
    #: ``out.provenance`` is an alias, unless the module returned an element of that name.
    run_provenance: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    _FIELDS = (
        "module",
        "title",
        "section",
        "table",
        "report",
        "traffic_light",
        "summary_text",
        "summary_table",
        "paper",
        "prev_outputs",
    )

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        return self.extras.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        return self.extras[key]

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_") or key == "extras":
            raise AttributeError(key)
        extras = self.__dict__.get("extras", {})
        if key in extras:
            return extras[key]
        if key == "provenance":  # alias of run_provenance (a module's own element wins)
            return self.__dict__.get("run_provenance")
        raise AttributeError(key)

    def keys(self) -> list[str]:
        """Element names in R's order: the standard fields then the extras."""
        return [*self._FIELDS, *self.extras]

    def results(self) -> dict[str, Any]:
        """Every element except the plumbing (``paper``, ``prev_outputs``)."""
        return {k: self.get(k) for k in self.keys() if k not in ("paper", "prev_outputs")}

    def __repr__(self) -> str:
        return f"{self.title}: {self.summary_text}"

    def __eq__(self, other: object) -> bool:
        # Python 3.13's generated __eq__ compares field by field without the identity
        # shortcut of tuple comparison, so a table field would raise on bool(frame == frame)
        if other.__class__ is not self.__class__:
            return NotImplemented
        return all(_same(getattr(self, f), getattr(other, f)) for f in (*self._FIELDS, "extras"))

    __hash__ = None  # type: ignore[assignment]


def _same(a: Any, b: Any) -> bool:
    """``a == b`` for :meth:`ModuleOutput.__eq__`: identical objects, then pandas' ``equals``."""
    if a is b:
        return True
    if isinstance(a, pd.DataFrame):
        return isinstance(b, pd.DataFrame) and a.equals(b)
    if isinstance(a, pd.Series):
        return isinstance(b, pd.Series) and a.equals(b)
    return bool(a == b)


def _is_list_column(series: pd.Series) -> bool:
    """Whether an ``object`` column is an R list column (e.g. open_practices' ``*_statements``).

    Character columns are ``string`` (or ``object`` holding strings); an
    ``object`` column without strings holds list cells or only missing ones.
    """
    return not any(isinstance(v, str) for v in series.tolist())


def _na_replace_list_column(series: pd.Series, value: Any) -> pd.Series:
    """``summary_table[is.na(summary_table[[col]]), col] <- value`` on a list column.

    ``is.na()`` of a list is TRUE only for cells holding a single ``NA`` (a
    module's ``NA_character_``: ``None`` / ``pd.NA`` / ``[None]`` here), not
    for the ``NULL`` cells a ``left_join()`` leaves in unmatched rows (the
    ``NaN`` pandas' merge fills in), and the value is stored as is (``0``
    stays a number, not ``"0"``).
    """

    def is_na(v: Any) -> bool:
        if v is None or v is pd.NA:
            return True
        return isinstance(v, list | tuple) and len(v) == 1 and (v[0] is None or v[0] is pd.NA)

    cells = series.tolist()
    return pd.Series([value if is_na(v) else v for v in cells], index=series.index, dtype=object)


def _apply_na_replace(
    summary: pd.DataFrame, na_replace: Any, own: Sequence[str], suffix: str
) -> pd.DataFrame:
    """Fill the ``NA`` cells of the columns the module added (*own*) with *na_replace*.

    An unnamed *na_replace* is recycled over the module's own columns; a named
    one names them (a name that clashed with an earlier column is found under
    its *suffix*). metacheck recycled an unnamed value over every column of
    the chained summary table, so ``na_replace = 0`` also filled earlier
    modules' ``NA`` cells (list columns got ``0``, logical ones turned
    numeric), and a name could reach an earlier module's column (U77).
    """
    cols = [c for c in summary.columns if c in own]
    if isinstance(na_replace, Mapping):
        mapping: dict[str, Any] = {}
        for k, v in na_replace.items():
            if k in cols:
                mapping[k] = v
            elif f"{k}{suffix}" in cols:
                mapping[f"{k}{suffix}"] = v
    else:
        values = list(na_replace) if isinstance(na_replace, list | tuple) else [na_replace]
        mapping = {c: values[i % len(values)] for i, c in enumerate(cols)} if values else {}
    for col, value in mapping.items():
        series = summary[col]
        if series.dtype == object and _is_list_column(series):
            summary[col] = _na_replace_list_column(series, value)
            continue
        mask = series.isna()
        if not mask.any():
            continue
        if pd.api.types.is_string_dtype(series) or series.dtype == object:
            fill = as_character(value) if not isinstance(value, str) else value
            summary[col] = series.astype(object).where(~mask, fill)
            if pd.api.types.is_string_dtype(series):
                summary[col] = summary[col].astype("string")
        elif pd.api.types.is_bool_dtype(series) and not isinstance(value, bool):
            summary[col] = series.astype("Float64").fillna(value).astype("float64")
        else:
            try:
                summary[col] = series.fillna(value)
            except (TypeError, ValueError):
                summary[col] = series.astype(object).where(~mask, value)
    return summary


class _UnusedArgumentError(TypeError):
    pass


def _check_unused_args(spec: ModuleSpec, kwargs: Mapping[str, Any]) -> None:
    """Raise R's ``unused argument (x = 1)`` error for arguments the module lacks."""
    params = inspect.signature(spec.func).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return
    unused = [k for k in kwargs if k not in params]
    if not unused:
        return
    from pytacheck.report.render import deparse

    def as_typed(v: Any) -> Any:
        # R deparses the call as written: `foo = 1` is a double, not 1L
        if isinstance(v, int) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, list | tuple):
            return [as_typed(e) for e in v]
        return v

    args = ", ".join(
        f"{k} = {' '.join(line.strip() for line in deparse(as_typed(kwargs[k])))}" for k in unused
    )
    plural = "s" if len(unused) > 1 else ""
    raise _UnusedArgumentError(f"unused argument{plural} ({args})")


def module_run(
    paper: Any, module: str | Path | ModuleSpec | Callable[..., Any], **kwargs: Any
) -> ModuleOutput:
    """Run a module on a paper, paper list, or the output of a previous module.

    Keyword arguments are passed to the module function and override its
    defaults. Passing a :class:`ModuleOutput` chains modules: the new output
    keeps the combined ``summary_table`` and every previous output in
    ``prev_outputs`` (readable from modules via :func:`get_prev_outputs`).

    A qualified ref (``"pack::name"``) is labelled with the bare module name,
    so chaining and the ``summary_table`` suffix work the same either way.
    Inside :func:`run_session`, runs on plain papers are memoised.
    """
    from pytacheck.provenance import bind_args, module_identity, module_provenance

    spec = module_find(module)
    if isinstance(module, str):
        qualified = split_ref(module)
        module_label = qualified[1] if qualified is not None else module
    else:
        module_label = spec.name
    bound = bind_args(spec.func, paper, kwargs)
    try:
        # an unbindable call raises below, exactly as before; record what was asked for
        effective = bound
        if effective is None:
            effective = {**dict(list(spec.arg_defaults.items())[1:]), **kwargs}
        provenance: dict[str, Any] | None = module_provenance(spec, effective)
    except Exception:  # pragma: no cover - provenance must never break a run
        provenance = None
    session = _SESSION.get()
    memo: tuple[Any, ...] | None = None  # the memo key, minus the paper's state
    pkey = _paper_key(paper) if session is not None else None
    if session is not None and bound is not None and provenance is not None and pkey is not None:
        try:
            memo = (
                str(module_label),
                module_identity(spec, provenance),
                _freeze(bound),
                _memo_options(),
            )
        except _Unfreezable:
            memo = None
        if memo is not None:
            hit = session.get((*memo, pkey))
            if hit is not None:
                return hit
    prev_outputs: dict[str, ModuleOutput] = {}

    if isinstance(paper, ModuleOutput):
        prev = paper
        paper = prev.paper
        summary_table = (
            prev.summary_table.copy()
            if prev.summary_table is not None
            else pd.DataFrame({"paper_id": pd.Series([], dtype="string")})
        )
        prev_outputs = dict(prev.prev_outputs)
        stripped = replace(prev, paper=None, summary_table=None, prev_outputs={})
        prev_outputs[prev.module] = stripped
    elif isinstance(paper, PaperList):
        summary_table = pd.DataFrame({"paper_id": pd.Series(paper.names, dtype="string")})
    elif isinstance(paper, Paper):
        summary_table = pd.DataFrame({"paper_id": pd.Series([paper.paper_id], dtype="string")})
    elif isinstance(paper, list | tuple) and all(isinstance(p, Paper) for p in paper):
        paper = PaperList(paper)
        summary_table = pd.DataFrame({"paper_id": pd.Series(paper.names, dtype="string")})
    elif isinstance(paper, pd.DataFrame):
        # a text table: data.frame(paper_id = paper$paper_id) has one row per table
        # row, or no columns at all when the table has no paper_id column
        summary_table = (
            pd.DataFrame({"paper_id": paper["paper_id"].astype("string").reset_index(drop=True)})
            if "paper_id" in paper.columns
            else pd.DataFrame()
        )
    else:
        summary_table = pd.DataFrame(
            {"paper_id": pd.Series([getattr(paper, "paper_id", None)], dtype="string")}
        )

    token = _PREV_OUTPUTS.set(prev_outputs)
    try:
        _check_unused_args(spec, kwargs)
        results = spec.func(paper, **kwargs)
    except Exception as exc:
        from pytacheck.log import logger

        ids = paper.names if isinstance(paper, PaperList) else [getattr(paper, "paper_id", None)]
        logger(spec.name, {"paper": ids, "error": str(exc), "details": repr(exc)})
        raise ModuleError(f"Running the module '{spec.name}' produced errors: {exc}") from exc
    finally:
        _PREV_OUTPUTS.reset(token)

    if isinstance(results, pd.DataFrame):
        results = {"table": results}
    results = dict(results or {})

    traffic_light = results.pop("traffic_light", None) or "info"
    summary_text = results.pop("summary_text", None)
    report = results.pop("report", None)
    if report is None:
        report = summary_text if summary_text is not None else ""

    mod_summary = results.pop("summary_table", None)
    na_replace = results.get("na_replace")
    if isinstance(mod_summary, pd.DataFrame) and "paper_id" in mod_summary.columns:
        if "paper_id" not in summary_table.columns:
            # dplyr::left_join() (e.g. after a failed module in a paper-list report)
            raise ValueError("Join columns in `x` must be present in the data.")
        suffix = "." + (Path(spec.path).stem if spec.path else spec.name)
        mod_summary = mod_summary.copy()
        mod_summary["paper_id"] = mod_summary["paper_id"].astype("string")
        before = set(summary_table.columns)
        summary_table = summary_table.merge(
            mod_summary, on="paper_id", how="left", suffixes=("", suffix)
        )
        if na_replace is not None:
            own = [c for c in summary_table.columns if c not in before]
            summary_table = _apply_na_replace(summary_table, na_replace, own, suffix)

    table = results.pop("table", None)
    out = ModuleOutput(
        module=str(module_label),
        title=spec.title,
        section=spec.section,
        table=table,
        report=report,
        traffic_light=traffic_light,
        summary_text=summary_text,
        summary_table=summary_table,
        paper=paper,
        prev_outputs=prev_outputs,
        extras=results,
        run_provenance=provenance,
    )
    if memo is not None and session is not None and pkey is not None:
        # keyed on the paper's state after the run (lazy tables are now built),
        # unless the module changed the paper itself
        after = _paper_key(paper)
        if after is not None and after[:3] == pkey[:3] and after[4] == pkey[4]:
            session.put((*memo, after), out)
    return out


def _detach(x: Any) -> Any:
    """*x*, safe to edit without changing the memo: DataFrames and Series become
    copy-on-write views, containers are deep-copied, anything else is shared."""
    if isinstance(x, pd.DataFrame | pd.Series):
        return x.copy(deep=False)
    if isinstance(x, list | dict | set | tuple):
        try:
            return copy.deepcopy(x)
        except Exception:  # pragma: no cover - uncopyable contents: share them
            return x
    return x


def _shallow_copy(out: ModuleOutput) -> ModuleOutput:
    """A new output whose values can be edited without changing *out* (see :func:`_detach`)."""
    prov = out.run_provenance
    return replace(
        out,
        table=_detach(out.table),
        summary_table=_detach(out.summary_table),
        prev_outputs=dict(out.prev_outputs),
        extras={k: _detach(v) for k, v in out.extras.items()},
        run_provenance=None if prov is None else copy.deepcopy(prov),
    )


from pytacheck._callable import callable_module  # noqa: E402

callable_module(__name__, "module")  # `from pytacheck import module` is the decorator either way
