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
installed plugins (entry-point group ``pytacheck.modules``), ``./<name>.py``
and ``./modules/<name>.py``, or by an explicit path to a ``.py`` file —
the same search metacheck's ``module_find()`` does for ``.R`` files.
"""

from __future__ import annotations

import contextvars
import importlib
import importlib.util
import inspect
import pkgutil
import sys
import textwrap
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
    "get_prev_outputs",
    "module",
    "module_find",
    "module_help",
    "module_info",
    "module_list",
    "module_run",
]

SECTION_LEVELS = ("general", "intro", "method", "results", "discussion", "reference")
TRAFFIC_LIGHTS = ("na", "fail", "info", "green", "yellow", "red")


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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator declaring a function as a pytacheck module."""

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        mod = sys.modules.get(func.__module__)
        path = getattr(mod, "__file__", None)
        spec = ModuleSpec(
            name=name or func.__name__,
            func=func,
            title=title,
            description=textwrap.dedent(description).strip(),
            details=textwrap.dedent(details).strip(),
            keywords=tuple(keywords),
            author=tuple(author),
            params=dict(params or {"paper": "a paper object or paperlist object"}),
            returns=returns,
            path=path,
        )
        func.__pytacheck_module__ = spec  # type: ignore[attr-defined]
        return func

    return decorate


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
    candidates = [
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
    mod_name = f"pytacheck_user_module_{path.stem}_{abs(hash(str(path.resolve())))}"
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


def module_find(name: str | Path | ModuleSpec | Callable[..., Any]) -> ModuleSpec:
    """Resolve a module name, path, spec or decorated function to its spec."""
    if isinstance(name, ModuleSpec):
        return name
    if callable(name) and hasattr(name, "__pytacheck_module__"):
        return name.__pytacheck_module__  # type: ignore[no-any-return]
    key = str(name)
    if key in _builtin_names():
        pymod = importlib.import_module(f"pytacheck.modules.{key}")
        return _spec_from_pymodule(pymod, key)
    eps = _entry_point_specs()
    if key in eps:
        obj = eps[key].load()
        if hasattr(obj, "__pytacheck_module__"):
            return obj.__pytacheck_module__  # type: ignore[no-any-return]
        return _spec_from_pymodule(obj, key)
    for candidate in (Path(f"{key}.py"), Path("modules") / f"{key}.py"):
        if candidate.is_file():
            return _load_file(candidate)
    path = Path(key)
    if path.is_file():
        return _load_file(path)
    from pytacheck.log import logger

    logger("module_find", {"module": key, "error": "Can't find module"})
    raise ModuleError(
        f"There were no modules that matched {key}\n"
        "use module_list() to see a list of built-in modules."
    )


def module_info(name: str | Path | ModuleSpec | Callable[..., Any]) -> ModuleSpec:
    """Metadata for a module (``module_info()``)."""
    return module_find(name)


def _iter_specs(module_dir: str | Path | None) -> Iterator[ModuleSpec]:
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


def module_list(module_dir: str | Path | None = None) -> pd.DataFrame:
    """A table of available modules, sorted by report section then name."""
    rows = [
        {
            "name": Path(s.path).stem if s.path and module_dir is not None else s.name,
            "title": s.title,
            "description": s.description,
            "section": s.keywords[0] if s.keywords else "general",
            "path": s.path,
        }
        for s in _iter_specs(module_dir)
    ]
    df = pd.DataFrame(rows, columns=["name", "title", "description", "section", "path"])
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
        raise AttributeError(key)

    def keys(self) -> list[str]:
        """Element names in R's order: the standard fields then the extras."""
        return [*self._FIELDS, *self.extras]

    def results(self) -> dict[str, Any]:
        """Every element except the plumbing (``paper``, ``prev_outputs``)."""
        return {k: self.get(k) for k in self.keys() if k not in ("paper", "prev_outputs")}

    def __repr__(self) -> str:
        return f"{self.title}: {self.summary_text}"


def _apply_na_replace(summary: pd.DataFrame, na_replace: Any) -> pd.DataFrame:
    cols = list(summary.columns)
    if isinstance(na_replace, Mapping):
        mapping = {k: v for k, v in na_replace.items() if k in cols}
    else:
        values = list(na_replace) if isinstance(na_replace, list | tuple) else [na_replace]
        mapping = {c: values[i % len(values)] for i, c in enumerate(cols)} if values else {}
    for col, value in mapping.items():
        series = summary[col]
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


def module_run(
    paper: Any, module: str | Path | ModuleSpec | Callable[..., Any], **kwargs: Any
) -> ModuleOutput:
    """Run a module on a paper, paper list, or the output of a previous module.

    Keyword arguments are passed to the module function and override its
    defaults. Passing a :class:`ModuleOutput` chains modules: the new output
    keeps the combined ``summary_table`` and every previous output in
    ``prev_outputs`` (readable from modules via :func:`get_prev_outputs`).
    """
    spec = module_find(module)
    module_label = module if isinstance(module, str) else spec.name
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
    else:
        summary_table = pd.DataFrame(
            {"paper_id": pd.Series([getattr(paper, "paper_id", None)], dtype="string")}
        )

    token = _PREV_OUTPUTS.set(prev_outputs)
    try:
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
        suffix = "." + (Path(spec.path).stem if spec.path else spec.name)
        mod_summary = mod_summary.copy()
        mod_summary["paper_id"] = mod_summary["paper_id"].astype("string")
        summary_table = summary_table.merge(
            mod_summary, on="paper_id", how="left", suffixes=("", suffix)
        )
        if na_replace is not None:
            summary_table = _apply_na_replace(summary_table, na_replace)

    table = results.pop("table", None)
    return ModuleOutput(
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
    )
