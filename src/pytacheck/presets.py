"""Presets: named, ordered module lists with per-module arguments.

A preset lives in a pack's ``pack.json`` (ref ``pack::name``; ``pack`` on its
own means ``pack::default``) or in config (ref by bare name). It is composed
with ``extends`` (base presets, deduplicated by label, first wins),
``exclude`` (labels to drop), ``replace`` (swap a module *in place*, so the
chain position is kept) and ``modules`` (appended unless the label is
already present); ``args`` are deep-merged along ``extends``.

The built-in presets mirror lists hard-coded in metacheck:
``metacheck::default`` (``report()``), ``metacheck::repository``
(``report_repository()``) and ``metacheck::validated`` (the Shiny app).

:func:`select` turns a call's ``modules`` / ``preset`` / ``args`` into the
ordered ``[(ref, args)]`` to run. Everything is checked before anything
runs (cycles, unknown presets, unknown modules, missing dependencies), as
metacheck's ``report()`` checks ``module_find()`` on every module up front.

One deliberate exception while the port is in progress: names that
metacheck's own presets list but that are not ported yet are accepted by
default and fail when run, the way a failing module becomes a ``fail``
entry in metacheck's ``report_module_run()``. ``validate=True`` imports
every module and turns those into errors too.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shlex
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck.module import (
    ModuleError,
    ModuleSpec,
    _allow_local,
    _builtin_names,
    _locate,
    module_find,
    use_setting,
)

if TYPE_CHECKING:
    import pandas as pd

    from pytacheck.packs.manifest import Pack

__all__ = [
    "DEFAULT_PRESET",
    "Preset",
    "PresetError",
    "Selection",
    "deep_merge",
    "default_preset",
    "expand",
    "label",
    "lookup",
    "preset",
    "preset_as_r",
    "preset_list",
    "select",
]

DEFAULT_PRESET = "metacheck::default"
_OFFLINE_DROPS = frozenset({"network", "llm"})
_R_NAME = re.compile(r"^(?:[A-Za-z]|\.(?![0-9]))[A-Za-z0-9._]*$")
_R_RESERVED = frozenset(
    {
        "if", "else", "repeat", "while", "function", "for", "next", "break", "in",
        "TRUE", "FALSE", "NULL", "Inf", "NaN", "NA",
        "NA_integer_", "NA_real_", "NA_character_",
    }
)  # fmt: skip


class PresetError(ModuleError):
    """A preset or selection is invalid (raised before any module runs)."""


@dataclass(frozen=True)
class Preset:
    """A preset definition and where it came from."""

    ref: str  # "pack::name" for pack presets, the bare name for config presets
    name: str
    pack: Pack | None  # the defining pack (None for config presets)
    body: Mapping[str, Any]  # validated: description, extends, exclude, replace, modules, args
    defined_in: str  # "builtin", a pack.json path or a config file path

    @property
    def description(self) -> str:
        return str(self.body.get("description", ""))


class Selection(list[tuple[str, dict[str, Any]]]):
    """The ordered ``[(ref, args), ...]`` to run, plus how it was chosen.

    ``preset`` is the preset used (``None`` for explicit modules only),
    ``source`` where that choice came from (``"argument"``, ``"use()"``,
    ``"PYTACHECK_PRESET"``, a config file path or ``"default"``), and
    ``dropped`` the refs left out because ``offline`` was on.
    """

    def __init__(
        self,
        items: Iterable[tuple[str, dict[str, Any]]] = (),
        *,
        preset: str | None = None,
        source: str | None = None,
        dropped: Sequence[str] = (),
        offline: bool = False,
    ) -> None:
        super().__init__(items)
        self.preset = preset
        self.source = source
        self.dropped = list(dropped)
        self.offline = offline

    @property
    def modules(self) -> list[str]:
        return [ref for ref, _ in self]

    def __repr__(self) -> str:
        return (
            f"Selection({list(self)!r}, preset={self.preset!r}, source={self.source!r}, "
            f"dropped={self.dropped!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_path(ref: str) -> bool:
    return ref.endswith(".py") or "/" in ref or "\\" in ref


def label(ref: Any) -> str:
    """The label a ref runs under: the bare module name (a path's file stem)."""
    if isinstance(ref, ModuleSpec):
        return ref.name
    if callable(ref) and hasattr(ref, "__pytacheck_module__"):
        return ref.__pytacheck_module__.name  # type: ignore[no-any-return]
    ref = str(ref)
    if "::" in ref:
        return ref.partition("::")[2]
    return Path(ref).stem if _is_path(ref) else ref


def deep_merge(*dicts: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge mappings left to right; nested mappings merge, other values replace."""
    out: dict[str, Any] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
                out[k] = deep_merge(out[k], v)
            elif isinstance(v, Mapping):
                out[k] = deep_merge(v)
            else:
                out[k] = v
    return out


@cache
def _declared_builtin() -> frozenset[str]:
    """Every module named by metacheck's built-in presets (ported or not)."""
    from pytacheck.packs.registry import builtin_pack

    return frozenset(m for body in builtin_pack().presets.values() for m in body["modules"])


def _is_metacheck_name(ref: str) -> bool:
    if ref.startswith("metacheck::"):
        return True
    return "::" not in ref and not _is_path(ref) and ref in _declared_builtin()


def _qualified(ref: str) -> str | None:
    """The ``pack::name`` form of a ref, when it has one."""
    if "::" in ref:
        return ref
    if _is_path(ref):
        return None
    if ref in _builtin_names() or ref in _declared_builtin():
        return f"metacheck::{ref}"
    try:
        kind, where = _locate(ref)
    except ModuleError:
        return None
    return f"{where[0].name}::{ref}" if kind == "pack" else None


def _dependency_ok(req: str) -> bool:
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:  # packaging is optional: check presence only
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        if m is None:
            raise PresetError(f"Invalid dependency {req!r}") from None
        try:
            importlib.metadata.version(m.group(1))
        except importlib.metadata.PackageNotFoundError:
            return False
        return True
    try:
        r = Requirement(req)
    except InvalidRequirement as exc:
        raise PresetError(f"Invalid dependency {req!r}: {exc}") from exc
    if r.marker is not None and not r.marker.evaluate():
        return True
    try:
        version = importlib.metadata.version(r.name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return not r.specifier or r.specifier.contains(version, prereleases=True)


def _check_dependencies(p: Preset) -> None:
    deps = [*(p.pack.dependencies if p.pack is not None else ()), *p.body.get("dependencies", ())]
    missing = [d for d in dict.fromkeys(deps) if not _dependency_ok(d)]
    if missing:
        raise PresetError(
            f"The preset '{p.ref}' needs {', '.join(missing)}. Install with:\n"
            f"pip install {' '.join(shlex.quote(d) for d in missing)}"
        )


# ---------------------------------------------------------------------------
# Lookup and expansion
# ---------------------------------------------------------------------------


def _pack_preset(pack: Pack, name: str) -> Preset:
    body = pack.presets.get(name)
    if body is None:
        have = ", ".join(pack.presets) or "none"
        raise PresetError(f"The pack '{pack.name}' has no preset '{name}' (its presets: {have})")
    where = "builtin" if pack.kind == "builtin" else str(pack.root / "pack.json")
    return Preset(ref=f"{pack.name}::{name}", name=name, pack=pack, body=body, defined_in=where)


def lookup(ref: str) -> Preset:
    """Find a preset: config presets (bare), ``pack::preset``, ``pack`` (its
    ``default``), else ``metacheck::<ref>``."""
    from pytacheck.packs.manifest import validate_preset
    from pytacheck.packs.registry import active_packs, builtin_pack, get_pack

    if not isinstance(ref, str) or not ref:
        raise PresetError(f"A preset ref must be a non-empty string, not {ref!r}")
    pack_name, sep, name = ref.partition("::")
    if sep:
        if pack_name == "metacheck":
            return _pack_preset(builtin_pack(), name)
        return _pack_preset(get_pack(pack_name, allow_local=_allow_local()), name)
    from pytacheck.config import load_config

    config = load_config()
    if ref in config.presets:
        where = (config.source(f"presets.{ref}") or ("", "config"))[1]
        body = validate_preset(ref, config.presets[ref], where=f"config preset in {where}")
        return Preset(ref=ref, name=ref, pack=None, body=body, defined_in=where)
    packs = active_packs(allow_local=_allow_local())
    if ref in packs:
        return _pack_preset(packs[ref], "default")
    builtin = builtin_pack()
    if ref in builtin.presets:
        return _pack_preset(builtin, ref)
    known = [*config.presets, *(f"{p}::{n}" for p, pk in packs.items() for n in pk.presets)]
    raise PresetError(f"There is no preset '{ref}'. Presets: {', '.join(known)}")


def _local(p: Preset, ref: str) -> str:
    """Inside a pack's preset, a bare name means that pack's own module when it has one."""
    pack = p.pack
    if pack is None or pack.kind == "builtin" or "::" in ref or _is_path(ref):
        return ref
    return f"{pack.name}::{ref}" if pack.has_module(ref) else ref


def _dedup(entries: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for e in entries:
        if label(e) not in seen:
            seen.add(label(e))
            out.append(e)
    return out


def _expand(ref: str, seen: tuple[str, ...]) -> tuple[list[str], dict[str, Any]]:
    p = lookup(ref)
    if p.ref in seen:
        raise PresetError(f"Preset cycle: {' -> '.join([*seen, p.ref])}")
    seen = (*seen, p.ref)
    _check_dependencies(p)
    body = p.body
    entries: list[str] = []
    base_args: list[dict[str, Any]] = []
    for base in body["extends"]:
        if p.pack is not None and "::" not in base and base in p.pack.presets:
            base = f"{p.pack.name}::{base}"  # a pack's own preset
        sub_entries, sub_args = _expand(base, seen)
        entries.extend(sub_entries)
        base_args.append(sub_args)
    entries = _dedup(entries)
    exclude = {label(x) for x in body["exclude"]}
    entries = [e for e in entries if label(e) not in exclude]
    swaps = {label(k): _local(p, v) for k, v in body["replace"].items()}
    absent = sorted(set(swaps) - {label(e) for e in entries})
    if absent:
        warnings.warn(
            f"Preset '{p.ref}' replaces {', '.join(absent)}, which it does not include",
            stacklevel=3,
        )
    entries = _dedup(swaps.get(label(e), e) for e in entries)
    entries = _dedup([*entries, *(_local(p, m) for m in body["modules"])])
    return entries, deep_merge(*base_args, body["args"])


def _entry_args(ref: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Arguments for *ref* from an args mapping; a qualified key beats a bare one."""
    qual = _qualified(ref) if isinstance(ref, str) else None
    return deep_merge(args.get(label(ref)), args.get(qual) if qual and qual != label(ref) else None)


def _check_refs(refs: Iterable[Any], validate: bool) -> None:
    problems: list[str] = []
    for ref in refs:
        if not isinstance(ref, str):
            continue  # a ModuleSpec or decorated function is already loaded
        name = label(ref)
        unported = name not in _builtin_names() and name in _declared_builtin()
        if unported and _is_metacheck_name(ref):
            if validate:
                problems.append(f"{ref}: metacheck's '{name}' is not ported yet")
            continue
        try:
            if validate:
                module_find(ref)
            else:
                _locate(ref)
        except ModuleError as exc:
            problems.append(f"{ref}: {str(exc).splitlines()[0]}")
    if problems:
        raise PresetError("Unknown or broken modules:\n" + "\n".join(f"* {p}" for p in problems))


def expand(ref: str, *, validate: bool = False) -> list[tuple[str, dict[str, Any]]]:
    """Expand a preset to ``[(ref, args)]``, checking it before anything runs."""
    entries, args = _expand(ref, ())
    _check_refs(entries, validate)
    return [(e, _entry_args(e, args)) for e in entries]


def preset(ref: str) -> list[tuple[str, dict[str, Any]]]:
    """The modules and arguments of a preset (``pc.preset("thesis")``)."""
    return expand(ref)


def preset_list() -> pd.DataFrame:
    """Every available preset: ``ref``, ``description``, ``n_modules``, ``defined_in``."""
    import pandas as pd

    from pytacheck.config import load_config
    from pytacheck.packs.registry import active_packs

    rows = []
    for pack in active_packs(allow_local=_allow_local()).values():
        for name in pack.presets:
            p = _pack_preset(pack, name)
            rows.append((p.ref, p.description, p.defined_in))
    config = load_config()
    for name, body in config.presets.items():
        where = (config.source(f"presets.{name}") or ("", "config"))[1]
        rows.append((name, str(body.get("description", "")), where))
    counts: list[Any] = []
    for ref, _, _ in rows:
        try:
            counts.append(len(expand(ref)))
        except ModuleError:
            counts.append(pd.NA)
    return pd.DataFrame(
        {
            "ref": pd.Series([r[0] for r in rows], dtype="string"),
            "description": pd.Series([r[1] for r in rows], dtype="string"),
            "n_modules": pd.Series(counts, dtype="Int64"),
            "defined_in": pd.Series([r[2] for r in rows], dtype="string"),
        }
    )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def default_preset(*, use_config: bool = False) -> tuple[str, str]:
    """``(preset, source)`` used when a call names neither modules nor a preset.

    ``pc.use(preset=...)``, then (only with *use_config*) ``PYTACHECK_PRESET``,
    the project config and the user config, then ``metacheck::default``.
    """
    chosen = use_setting("preset")
    if chosen:
        return str(chosen), "use()"
    if use_config:
        env = os.environ.get("PYTACHECK_PRESET", "").strip()
        if env:
            return env, "PYTACHECK_PRESET"
        from pytacheck.config import load_config

        config = load_config()
        if config.preset:
            return config.preset, (config.source("preset") or ("", "config"))[1]
    return DEFAULT_PRESET, "default"


def _requires(ref: Any) -> set[str]:
    try:
        return set(module_find(ref).requires)
    except ModuleError:
        return set()  # unknown here: it will fail (and be reported) when run


def select(
    modules: Any = None,
    preset: str | None = None,
    args: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    use_config: bool = False,
    offline: bool | None = None,
    validate: bool = False,
) -> Selection:
    """The ordered ``[(ref, args)]`` to run for a call.

    * ``modules`` given: run them in that order, as in R, with no
      deduplication; with ``preset`` too, the preset comes first.
    * Otherwise the preset is ``preset=``, then ``pc.use(preset=...)``, then
      (only with ``use_config=True``, as the CLI and API do)
      ``PYTACHECK_PRESET``, the project and the user config, and finally
      ``metacheck::default`` -- so the library's ``report(paper)`` always runs
      what R's ``report()`` runs.
    * ``args`` (R's ``report(args = list(power = list(seed = 1)))``) are
      deep-merged over the preset's; keys may be bare or ``pack::name`` and a
      qualified key wins.
    * ``offline=True`` (default: ``pc.use(offline=...)``) drops modules that
      require ``network`` or ``llm``; they are listed in ``Selection.dropped``.
    """
    if isinstance(modules, str | Path | ModuleSpec) or callable(modules):
        modules = [modules]
    if args is not None and (
        not isinstance(args, Mapping) or not all(isinstance(v, Mapping) for v in args.values())
    ):
        raise PresetError("args must map module names to dicts of arguments")
    source = "argument" if preset is not None else None
    if modules is None and preset is None:
        preset, source = default_preset(use_config=use_config)
    entries: list[tuple[Any, dict[str, Any]]] = []
    if preset is not None:
        entries = list(expand(preset, validate=validate))
    if modules is not None:
        explicit = [str(m) if isinstance(m, Path) else m for m in modules]
        _check_refs(explicit, validate)
        entries += [(m, {}) for m in explicit]
    call = args or {}
    final = [(ref, deep_merge(a, _entry_args(ref, call))) for ref, a in entries]
    off = bool(use_setting("offline", False)) if offline is None else bool(offline)
    dropped: list[str] = []
    if off:
        kept = []
        for ref, a in final:
            if _requires(ref) & _OFFLINE_DROPS:
                dropped.append(ref if isinstance(ref, str) else label(ref))
            else:
                kept.append((ref, a))
        final = kept
    return Selection(final, preset=preset, source=source, dropped=dropped, offline=off)


# ---------------------------------------------------------------------------
# metacheck interop
# ---------------------------------------------------------------------------


def _r_name(name: str) -> str:
    return name if _R_NAME.match(name) and name not in _R_RESERVED else f"`{name}`"


def _r_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value in (float("inf"), float("-inf")):
            return "Inf" if value > 0 else "-Inf"
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        return (
            "list("
            + ", ".join(f"{_r_name(str(k))} = {_r_value(v)}" for k, v in value.items())
            + ")"
        )
    if isinstance(value, list | tuple):
        scalar = all(isinstance(v, bool | int | float | str) for v in value)
        same = len({type(v) for v in value}) <= 1
        inner = ", ".join(_r_value(v) for v in value)
        return f"c({inner})" if value and scalar and same else f"list({inner})"
    return json.dumps(repr(value))


def preset_as_r(ref: str | Iterable[tuple[str, Mapping[str, Any]]]) -> str:
    """The equivalent metacheck call, ``report(paper, modules = c(...), args = list(...))``.

    Pack modules run in R only when the pack ships ``<name>.R`` next to the
    ``.py`` file (then its path is used); others are listed in a comment.
    """
    entries = expand(ref) if isinstance(ref, str) else list(ref)
    names: list[str] = []
    args: dict[str, Any] = {}
    missing: list[str] = []
    for mod, a in entries:
        mod = str(mod)
        r_ref = label(mod)
        if "::" in mod and not mod.startswith("metacheck::"):
            from pytacheck.packs.registry import get_pack

            pack_name, _, name = mod.partition("::")
            try:
                r_file = get_pack(pack_name).root / f"{name}.R"
            except ModuleError:
                r_file = None
            if r_file is not None and r_file.is_file():
                r_ref = str(r_file)
            else:
                missing.append(mod)
        names.append(r_ref)
        if a:
            args[r_ref] = a
    lines = []
    if missing:
        lines.append(f"# no metacheck (.R) version of: {', '.join(missing)}")
    call = f"report(paper,\n       modules = c({', '.join(json.dumps(n) for n in names)})"
    if args:
        call += f",\n       args = {_r_value(args)}"
    lines.append(call + ")")
    return "\n".join(lines)
