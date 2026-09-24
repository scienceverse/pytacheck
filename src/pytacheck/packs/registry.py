"""The registry of active packs, and loading their modules.

Active packs, in order: the built-in ``metacheck`` pack, the packs pinned in
config (installed under ``<data>/packs/<name>/<rev12>/``, or ``{"path": ...}``
path packs), then pip-installed packs registering the ``pytacheck.packs``
entry point (``false`` in config hides one). The registry is built lazily and
cached against :func:`pytacheck.config.config_stamp`, so config edits are
picked up without a restart; :func:`refresh` forgets everything.

Installed and path pack modules are imported as
``pytacheck_packs.<key>.<module>``, where ``<key>`` is ``<name>_<rev12>``
(installed) or ``local_<sha8 of path>`` (path packs). Each
``pytacheck_packs.<key>`` is a synthetic package whose ``__path__`` is the
pack folder, so relative helper imports work and two revisions of a pack
never share module objects. Pack code is compiled from source without
reading or writing bytecode; path packs are re-imported when any of their
``.py`` files changes ``(mtime_ns, size)``.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
import threading
import warnings
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from pytacheck.config import Config, data_dir, load_config
from pytacheck.module import ModuleError, ModuleSpec, _spec_from_pymodule
from pytacheck.packs.manifest import (
    MODULE_NAME_RE,
    Pack,
    PackError,
    read_manifest,
    validate_manifest,
    validate_pack_name,
)
from pytacheck.packs.tree import INSTALL_RECORD, clear_cache, file_sha256
from pytacheck.provenance import builtin_source

__all__ = [
    "ENTRY_POINT_GROUP",
    "PACKAGE_ROOT",
    "Registry",
    "active_packs",
    "builtin_pack",
    "builtin_source",
    "find_module",
    "get_pack",
    "install_dir",
    "integrity",
    "load_module",
    "overlay",
    "pack_for_spec",
    "pin_rev12",
    "refresh",
    "registry",
]

ENTRY_POINT_GROUP = "pytacheck.packs"
PACKAGE_ROOT = "pytacheck_packs"
_HEX12 = re.compile(r"^[0-9a-f]{12,64}$")
_IGNORED_DIRS = frozenset({".git", "__pycache__", "data", "tests"})

_lock = threading.RLock()
_state: dict[str, Any] = {}
_KEYS: dict[str, Pack] = {}  # synthetic package key -> pack (installed + path packs)
_BY_PACKAGE: dict[str, Pack] = {}  # python package prefix -> pack (never forgotten)
_snapshots: dict[str, dict[str, tuple[int, int]]] = {}  # path pack key -> .py stats
_warned: set[tuple[str, tuple[str, ...]]] = set()
#: packs made active in one context only (``pack check`` checks a folder this way)
_OVERLAY: contextvars.ContextVar[Mapping[str, Pack]] = contextvars.ContextVar(
    "pytacheck_pack_overlay",
    default={},  # noqa: B039 - never mutated
)


# ---------------------------------------------------------------------------
# Synthetic packages: pytacheck_packs.<key>.<module>
# ---------------------------------------------------------------------------


class _PackageLoader(importlib.abc.Loader):
    """Creates the empty synthetic packages ``pytacheck_packs[.<key>]``."""

    def __init__(self, pack: str | None) -> None:
        self.pack = pack

    def create_module(self, _spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: Any) -> None:
        module.__pytacheck_pack__ = self.pack


class _SourceLoader(importlib.machinery.SourceFileLoader):
    """Compiles pack code from source every time: no bytecode is read or written."""

    def get_code(self, _fullname: str) -> Any:
        return self.source_to_code(self.get_data(self.path), self.path)


class _PackFinder(importlib.abc.MetaPathFinder):
    """Finds ``pytacheck_packs.*`` modules in the registered pack folders.

    It is authoritative for that namespace: a name with no ``.py`` source (or
    package ``__init__.py``) raises instead of falling through to the standard
    path finder, so bytecode, extension modules and namespace packages in a
    pack folder are never imported.
    """

    def find_spec(
        self, fullname: str, path: Any = None, _target: Any = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == PACKAGE_ROOT:
            return importlib.machinery.ModuleSpec(fullname, _PackageLoader(None), is_package=True)
        if not fullname.startswith(PACKAGE_ROOT + "."):
            return None
        parts = fullname.split(".")
        if len(parts) == 2:
            pack = _KEYS.get(parts[1])
            if pack is None:
                raise ModuleNotFoundError(f"No pack is loaded as {fullname!r}", name=fullname)
            spec = importlib.machinery.ModuleSpec(
                fullname, _PackageLoader(pack.name), is_package=True, origin=str(pack.root)
            )
            spec.submodule_search_locations = [str(pack.root)]
            return spec
        leaf = parts[-1]
        for folder in path or ():
            init = os.path.join(folder, leaf, "__init__.py")
            if os.path.isfile(init):
                return importlib.util.spec_from_file_location(
                    fullname,
                    init,
                    loader=_SourceLoader(fullname, init),
                    submodule_search_locations=[os.path.join(folder, leaf)],
                )
            file = os.path.join(folder, leaf + ".py")
            if os.path.isfile(file):
                return importlib.util.spec_from_file_location(
                    fullname, file, loader=_SourceLoader(fullname, file)
                )
        raise ModuleNotFoundError(
            f"No module named {fullname!r} (pack code is imported from .py files only)",
            name=fullname,
        )


def _install_finder() -> None:
    if not any(isinstance(f, _PackFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _PackFinder())


def _purge(package: str) -> None:
    for name in [m for m in sys.modules if m.startswith(package + ".")]:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Building packs
# ---------------------------------------------------------------------------


@cache
def builtin_pack() -> Pack:
    """The built-in ``metacheck`` pack (``src/pytacheck/modules`` + ``pack.json``)."""
    from pytacheck._version import __version__

    root = Path(__file__).resolve().parent.parent / "modules"
    manifest = read_manifest(root, builtin=True)
    return Pack.from_manifest(
        manifest,
        root=root,
        kind="builtin",
        trust="builtin",
        version=__version__,
        source=builtin_source(),
        package="pytacheck.modules",
    )


def pin_rev12(pin: Mapping[str, Any]) -> str:
    """The install folder name for a pin: the first 12 hex digits of its rev.

    A pin without a commit (a local-folder store source) uses its tree hash.
    """
    rev = pin.get("rev")
    if isinstance(rev, str) and _HEX12.match(rev):
        return rev[:12]
    tree = pin.get("tree_sha256")
    if isinstance(tree, str) and _HEX12.match(tree):
        return tree[:12]
    raise PackError("A pack pin needs a 'rev' (commit SHA) or a 'tree_sha256'")


def install_dir(name: str, pin: Mapping[str, Any]) -> Path:
    """``<data>/packs/<name>/<rev12>/``: where a pinned pack is installed."""
    return data_dir() / "packs" / name / pin_rev12(pin)


def _read_record(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _installed_pack(name: str, pin: Mapping[str, Any], origin: str) -> Pack:
    root = install_dir(name, pin)
    rev12 = pin_rev12(pin)
    if not root.is_dir():
        raise PackError(
            f"The pack '{name}' is pinned in {origin} (rev {rev12}) but not installed. "
            "Run `pytacheck pack install` (or pytacheck.pack_install()) to install it."
        )
    manifest = read_manifest(root)
    if manifest["name"] != name:
        raise PackError(f"{root} holds the pack '{manifest['name']}', not '{name}'")
    record = _read_record(root)
    if not record:
        raise PackError(
            f"The pack '{name}' in {root} has no {INSTALL_RECORD}; reinstall it with "
            "`pytacheck pack install`"
        )
    for key in ("rev", "tree_sha256"):
        if pin.get(key) and record.get(key) and pin[key] != record[key]:
            raise PackError(
                f"The installed pack '{name}' ({root}) does not match its pin in {origin} "
                f"({key} {record[key]} != {pin[key]}); reinstall it with `pytacheck pack install`"
            )
    rev = pin.get("rev") or record.get("rev")
    source = dict(pin.get("source") or record.get("source") or {})
    if rev:
        source.setdefault("rev", rev)
    store = pin.get("store") or record.get("store")
    return Pack.from_manifest(
        manifest,
        root=root,
        kind="installed",
        trust="store" if store else "unlisted",
        version=manifest.get("version") or pin.get("version"),
        source=source,
        reviewed=record.get("reviewed") or pin.get("reviewed"),
        rev=rev,
        tree_sha256=pin.get("tree_sha256") or record.get("tree_sha256"),
        store=store,
        origin=origin,
        package=f"{PACKAGE_ROOT}.{name}_{rev12}",
        files=dict(record.get("files") or {}),
    )


def _path_pack(name: str, pin: Mapping[str, Any], origin: str) -> Pack:
    root = Path(pin["path"]).expanduser().resolve()
    if not root.is_dir():
        raise PackError(f"The path pack '{name}' (in {origin}) points to {root}, not a folder")
    if (root / "pack.json").is_file():
        manifest = read_manifest(root)
        if manifest["name"] != name:
            raise PackError(
                f"The path pack '{name}' (in {origin}) holds the pack '{manifest['name']}'"
            )
    else:
        manifest = validate_manifest({"name": name}, where=str(root))
    sha8 = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return Pack.from_manifest(
        manifest,
        root=root,
        kind="path",
        trust="local",
        source={"path": str(root)},
        origin=origin,
        package=f"{PACKAGE_ROOT}.local_{sha8}",
    )


def _dist_pack(ep: Any) -> Pack:
    validate_pack_name(ep.name)
    try:
        spec = importlib.util.find_spec(ep.module)
    except (ImportError, ValueError) as exc:
        raise PackError(f"The installed pack '{ep.name}' cannot be found: {exc}") from exc
    if spec is None or not spec.submodule_search_locations:
        raise PackError(f"The entry point for pack '{ep.name}' must name a Python package")
    root = Path(next(iter(spec.submodule_search_locations)))
    manifest = read_manifest(root)
    if manifest["name"] != ep.name:
        raise PackError(f"{root} holds the pack '{manifest['name']}', not '{ep.name}'")
    dist = getattr(ep, "dist", None)
    dist_version = getattr(dist, "version", None)
    source = {"dist": f"{dist.name} {dist_version}"} if dist is not None else {"dist": ep.module}
    return Pack.from_manifest(
        manifest,
        root=root,
        kind="dist",
        trust="dist",
        version=manifest.get("version") or dist_version,
        source=source,
        package=ep.module,
    )


@dataclass(frozen=True)
class Registry:
    """A snapshot of the active packs."""

    stamp: tuple[Any, ...]
    packs: dict[str, Pack]  # name -> pack, built-in first
    problems: dict[str, str]  # name -> why a configured pack is unavailable
    hidden: frozenset[str]  # dist packs hidden with ``false``
    install_dirs: tuple[str, ...]  # pinned install folders (their existence is re-checked)
    exists: tuple[bool, ...]


def _dist_entry_points() -> list[Any]:
    try:
        return sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)
    except Exception:  # pragma: no cover - broken plugin metadata
        return []


def _build(config: Config) -> Registry:
    packs: dict[str, Pack] = {"metacheck": builtin_pack()}
    problems: dict[str, str] = {}
    hidden: set[str] = set()
    dirs: list[str] = []
    for key, (where, _entry, code) in config.untrusted.items():
        section, _, name = key.partition(".")
        if section == "packs":
            problems[name] = _untrusted_message(name, where, code)
    for name, pin in config.packs.items():
        if pin is False:
            hidden.add(name)
            continue
        origin = (config.source(f"packs.{name}") or ("", "config"))[1]
        try:
            validate_pack_name(name)
            if "path" in pin:
                pack = _path_pack(name, pin, origin)
            else:
                dirs.append(str(install_dir(name, pin)))
                pack = _installed_pack(name, pin, origin)
        except PackError as exc:
            problems[name] = str(exc)
            continue
        packs[name] = pack
    for ep in _dist_entry_points():
        if ep.name in packs or ep.name in hidden or ep.name in problems:
            continue
        try:
            packs[ep.name] = _dist_pack(ep)
        except PackError as exc:
            problems[ep.name] = str(exc)
    for pack in packs.values():
        if pack.kind != "builtin":
            _BY_PACKAGE[pack.package] = pack
    return Registry(
        stamp=config.stamp,
        packs=packs,
        problems=problems,
        hidden=frozenset(hidden),
        install_dirs=tuple(dirs),
        exists=tuple(os.path.isdir(d) for d in dirs),
    )


def _untrusted_message(name: str, where: str, code: tuple[str, ...]) -> str:
    return (
        f"The project config {where} names the local folder {code[0]} as the pack '{name}', "
        "which you have not trusted yet: its code would run with your permissions. Review "
        "it, then run `pytacheck pack install` in that project to trust it."
    )


def registry() -> Registry:
    """The active packs, rebuilt when config or pinned install folders change."""
    config = load_config()
    reg = _state.get("registry")
    if (
        reg is not None
        and reg.stamp == config.stamp
        and reg.exists == tuple(os.path.isdir(d) for d in reg.install_dirs)
    ):
        return reg  # type: ignore[no-any-return]
    with _lock:
        reg = _build(config)
        _state["registry"] = reg
    return reg


def refresh() -> None:
    """Forget cached config, packs, file hashes and imported pack modules."""
    from pytacheck.config import _config_cache
    from pytacheck.module import _builtin_names

    with _lock:
        _config_cache.clear()
        _state.clear()
        _snapshots.clear()
        _warned.clear()
        clear_cache()
        _purge(PACKAGE_ROOT)
        _builtin_names.cache_clear()
        builtin_pack.cache_clear()
        presets = sys.modules.get("pytacheck.presets")
        if presets is not None:
            presets._declared_builtin.cache_clear()
        importlib.invalidate_caches()


@contextlib.contextmanager
def overlay(*packs: Pack) -> Iterator[None]:
    """Make *packs* active inside a ``with`` block, in this context only.

    ``pack check`` uses it to resolve a folder's presets and ``pack::name``
    refs without touching config. An overlay pack wins over a configured
    pack of the same name.
    """
    token = _OVERLAY.set({**_OVERLAY.get(), **{p.name: p for p in packs}})
    try:
        yield
    finally:
        _OVERLAY.reset(token)


def active_packs(*, allow_local: bool = True) -> dict[str, Pack]:
    """Active packs by name (built-in first); path packs only if *allow_local*."""
    packs = {**registry().packs, **_OVERLAY.get()}
    if allow_local:
        return packs
    return {k: p for k, p in packs.items() if p.kind != "path"}


def get_pack(name: str, *, allow_local: bool = True) -> Pack:
    """The active pack *name*, or a :class:`PackError` saying why there is none."""
    if name == "metacheck":
        return builtin_pack()
    reg = registry()
    pack = _OVERLAY.get().get(name) or reg.packs.get(name)
    if pack is not None:
        if pack.kind == "path" and not allow_local:
            raise PackError(
                f"The pack '{name}' is a local path pack, and local modules are not "
                "allowed here (allow_local=False)"
            )
        return pack
    if name in reg.problems:
        raise PackError(reg.problems[name])
    hint = " (hidden with false in config)" if name in reg.hidden else ""
    raise PackError(
        f"There is no active pack named '{name}'{hint}. "
        f"Active packs: {', '.join(active_packs(allow_local=allow_local))}"
    )


def find_module(name: str, *, allow_local: bool = True) -> list[Pack]:
    """The non-built-in active packs that provide a module called *name*."""
    if not MODULE_NAME_RE.match(name):
        return []
    return [
        p
        for p in active_packs(allow_local=allow_local).values()
        if p.kind != "builtin" and p.has_module(name)
    ]


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for folder, dirs, files in os.walk(root):
        skip = _IGNORED_DIRS if folder == str(root) else _IGNORED_DIRS - {"data", "tests"}
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(folder, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out[os.path.relpath(full, root)] = (st.st_mtime_ns, st.st_size)
    return out


def load_module(pack: Pack, name: str) -> ModuleSpec:
    """Import module *name* of *pack* and return its spec (``spec.pack`` set)."""
    if pack.kind == "builtin":
        from pytacheck.module import module_find

        if name not in pack.modules():
            raise PackError(
                f"The pack 'metacheck' has no module '{name}'. "
                f"Its modules are: {', '.join(pack.modules())}"
            )
        return module_find(name)
    if not pack.has_module(name):
        mods = pack.modules()
        raise PackError(
            f"The pack '{pack.name}' has no module '{name}'. "
            + (f"Its modules are: {', '.join(mods)}" if mods else "It has no modules.")
        )
    fullname = f"{pack.package}.{name}"
    with _lock:
        if pack.kind != "dist":
            _install_finder()
            _KEYS[pack.key] = pack
            _BY_PACKAGE[pack.package] = pack
            if pack.kind == "path":
                snap = _snapshot(pack.root)
                if _snapshots.get(pack.key, snap) != snap:
                    _purge(pack.package)
                _snapshots[pack.key] = snap
        try:
            pymod = sys.modules.get(fullname) or importlib.import_module(fullname)
        except Exception as exc:
            from pytacheck.log import logger

            logger(name, {"pack": pack.name, "error": str(exc)})
            raise ModuleError(f"The module '{pack.name}::{name}' has errors: {exc}") from exc
    spec = _spec_from_pymodule(pymod, name)
    if spec.pack != pack.name:
        spec = replace(spec, pack=pack.name)
        func = spec.func
        if getattr(func, "__pytacheck_module__", None) is not None:
            func.__pytacheck_module__ = spec  # type: ignore[attr-defined]
    return spec


def pack_for_spec(spec: ModuleSpec) -> Pack | None:
    """The pack a loaded module came from (the exact revision it was loaded from)."""
    if spec.pack == "metacheck":
        return builtin_pack()
    modname = getattr(spec.func, "__module__", None) or ""
    best: Pack | None = None
    for prefix, pack in _BY_PACKAGE.items():
        if (modname == prefix or modname.startswith(prefix + ".")) and (
            best is None or len(prefix) > len(best.package)
        ):
            best = pack
    if best is not None or not spec.pack:
        return best
    try:
        return get_pack(spec.pack)
    except ModuleError:
        return None


def integrity(pack: Pack) -> list[str]:
    """Files of an installed pack that differ from its install record (warns once)."""
    if pack.kind != "installed" or not pack.files:
        return []
    bad: list[str] = []
    for rel, sha in pack.files.items():
        try:
            if file_sha256(pack.root / rel) != sha:
                bad.append(rel)
        except OSError:
            bad.append(rel)
    try:
        extra = [n for n in os.listdir(pack.root) if n.endswith(".py") and n not in pack.files]
    except OSError:
        extra = []
    bad.extend(sorted(extra))
    if bad:
        key = (pack.package, tuple(bad))
        if key not in _warned:
            _warned.add(key)
            warnings.warn(
                f"The installed pack '{pack.name}' ({pack.root}) was modified after install: "
                f"{', '.join(bad)}. Reinstall it with `pytacheck pack install`.",
                stacklevel=3,
            )
    return bad
