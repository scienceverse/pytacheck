"""Provenance of module runs: which module, from which pack, at which commit.

:func:`module_provenance` builds the dict stored in
``ModuleOutput.provenance`` on every :func:`pytacheck.module.module_run`::

    {"id": "psych::apa_df", "name": "apa_df", "pack": "psych", "version": "1.2.0",
     "trust": "store", "reviewed": "2026-09-01",
     "source": {"github": "janedoe/pytacheck-psych", "rev": "<40 hex>"},
     "sha256": "<module file sha256>", "modified": false,
     "args": {"strict": true}, "requires": []}

It is a plain attribute, never part of ``keys()``, ``results()`` or the
parity encoding. Built-in runs never import :mod:`pytacheck.packs`.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import os
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytacheck.module import ModuleSpec

__all__ = [
    "bind_args",
    "builtin_source",
    "file_sha256",
    "json_safe",
    "module_identity",
    "module_provenance",
]

_sha_cache: dict[str, tuple[tuple[int, int, int], str]] = {}


def file_sha256(path: str | os.PathLike[str]) -> str:
    """The sha256 hex digest of a file, cached against its ``(mtime_ns, size, inode)``."""
    key = os.fspath(path)
    st = os.stat(key)
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _sha_cache.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    h = hashlib.sha256()
    with open(key, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _sha_cache[key] = (sig, digest)
    return digest


def builtin_source() -> dict[str, str]:
    """Provenance ``source`` of built-in modules."""
    from pytacheck._version import UPSTREAM, __version__

    return {
        "builtin": f"pytacheck {__version__}",
        "upstream": f"{UPSTREAM['version']}@{UPSTREAM['commit'][:10]}",
    }


def json_safe(value: Any) -> Any:
    """*value* as JSON data; anything that is not JSON is stored as its ``repr``."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, Mapping) and all(isinstance(k, str) for k in value):
        return {k: json_safe(v) for k, v in value.items()}
    return repr(value)


@lru_cache(maxsize=512)
def _signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def bind_args(
    func: Callable[..., Any], paper: Any, kwargs: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The effective arguments of ``func(paper, **kwargs)`` (defaults applied, paper left out).

    ``None`` when the call does not bind (the call itself will then raise).
    """
    sig = _signature(func)
    if sig is None:
        return None
    try:
        bound = sig.bind(paper, **kwargs)
    except TypeError:
        return None
    bound.apply_defaults()
    out: dict[str, Any] = {}
    for i, (name, value) in enumerate(bound.arguments.items()):
        kind = sig.parameters[name].kind
        if i == 0 and kind is not inspect.Parameter.VAR_KEYWORD:
            continue  # the paper
        if kind is inspect.Parameter.VAR_KEYWORD:
            out.update(value)
        elif kind is inspect.Parameter.VAR_POSITIONAL:
            if value:
                out[name] = list(value)
        else:
            out[name] = value
    return out


def _sha(spec: ModuleSpec) -> str | None:
    if not spec.path:
        return None
    try:
        return file_sha256(spec.path)
    except OSError:
        return None


def _is_pack_module(spec: ModuleSpec) -> bool:
    modname = getattr(spec.func, "__module__", None) or ""
    return bool(spec.pack) or modname.startswith("pytacheck_packs.")


def module_provenance(spec: ModuleSpec, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The provenance record of one run of *spec* with effective arguments *args*."""
    pack: str | None = None
    version: str | None = None
    trust = "local"
    reviewed: str | None = None
    modified = False
    sha = _sha(spec)
    if spec.pack == "metacheck":
        from pytacheck._version import __version__

        pack, version, trust, source = "metacheck", __version__, "builtin", builtin_source()
    elif _is_pack_module(spec):
        from pytacheck.packs.registry import integrity, pack_for_spec

        found = pack_for_spec(spec)
        pack = found.name if found is not None else spec.pack
        source = {}
        if found is not None:
            version, trust, reviewed = found.version, found.trust, found.reviewed
            source = dict(found.source)
            modified = bool(integrity(found))
    else:
        modname = getattr(spec.func, "__module__", None) or ""
        local = (
            not spec.path or modname == "__main__" or modname.startswith("pytacheck_user_module_")
        )
        trust = "local" if local else "dist"
        source = {"path": spec.path} if spec.path else {"module": modname}
    return {
        "id": f"{pack}::{spec.name}" if pack else spec.name,
        "name": spec.name,
        "pack": pack,
        "version": version,
        "trust": trust,
        "reviewed": reviewed,
        "source": source,
        "sha256": sha,
        "modified": modified,
        "args": json_safe(dict(args or {})),
        "requires": list(spec.requires),
    }


def module_identity(spec: ModuleSpec, provenance: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two runs of a module the same code: pack, rev, file hash (or function)."""
    source = provenance.get("source") or {}
    rev = source.get("rev") if isinstance(source, Mapping) else None
    code: Any = provenance.get("sha256") or spec.func
    return (provenance.get("pack"), rev or provenance.get("version"), code, spec.name)
