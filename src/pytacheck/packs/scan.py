"""Static (``ast``) scans of pack code: imports, risky calls and module metadata.

Nothing here imports or runs pack code. The install consent card, ``pack
check`` and ``store build`` use these scans to show what a pack's code
reaches for and to read each module's ``@module(...)`` metadata.
"""

from __future__ import annotations

import ast
import os
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "NETWORK_MODULES",
    "RISKY_CALLS",
    "RISKY_MODULES",
    "FileScan",
    "module_metadata",
    "network_imports",
    "scan_file",
    "scan_tree",
]

#: Imports worth a second look, and why (shown on the consent card).
RISKY_MODULES: dict[str, str] = {
    "subprocess": "runs programs",
    "os.system": "runs programs",
    "pty": "runs programs",
    "socket": "network",
    "ssl": "network",
    "httpx": "network (HTTP)",
    "requests": "network (HTTP)",
    "urllib": "network (HTTP)",
    "urllib3": "network (HTTP)",
    "aiohttp": "network (HTTP)",
    "http.client": "network (HTTP)",
    "ftplib": "network",
    "smtplib": "network (mail)",
    "pytacheck.http": "network (HTTP)",
    "ctypes": "native code",
    "cffi": "native code",
    "pickle": "unpickling can run code",
    "marshal": "loads code objects",
    "importlib": "imports code dynamically",
    "multiprocessing": "starts processes",
}
#: Calls worth a second look.
RISKY_CALLS: dict[str, str] = {
    "eval": "evaluates code",
    "exec": "executes code",
    "compile": "compiles code",
    "__import__": "imports code dynamically",
    "os.system": "runs programs",
    "os.popen": "runs programs",
    "os.exec": "runs programs",
    "os.spawn": "runs programs",
    "os.fork": "starts processes",
    "importlib.import_module": "imports code dynamically",
}
#: Imports that mean a module talks to the network (``pack check`` wants ``requires=["network"]``).
NETWORK_MODULES = (
    "pytacheck.http",
    "httpx",
    "requests",
    "urllib",
    "urllib3",
    "aiohttp",
    "http.client",
    "socket",
)
_SKIP_DIRS = frozenset({".git", "__pycache__"})


@dataclass(frozen=True)
class FileScan:
    """What one ``.py`` file imports and calls."""

    path: str  # POSIX path relative to the pack root
    size: int
    imports: tuple[str, ...]  # dotted module names, in order of appearance
    risky: tuple[tuple[str, str], ...]  # (import or call, reason)
    error: str | None = None  # a syntax error, when the file does not parse


def _matches(name: str, prefixes: Iterable[str]) -> str | None:
    for p in prefixes:
        if name == p or name.startswith(p + "."):
            return p
    return None


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _imports(tree: ast.AST) -> tuple[list[str], list[str]]:
    """``(imported modules, imported names)``: ``from os import system`` gives
    ``os`` and ``os.system`` (so ``from pytacheck import http`` is caught)."""
    mods: list[str] = []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            mods.append("." * node.level + (node.module or ""))
            if node.level == 0 and node.module:
                names.extend(f"{node.module}.{a.name}" for a in node.names if a.name != "*")
    return list(dict.fromkeys(mods)), list(dict.fromkeys(names))


def scan_file(path: str | os.PathLike[str], rel: str | None = None) -> FileScan:
    """Scan one Python file (never imported)."""
    p = Path(path)
    rel = rel or p.name
    try:
        data = p.read_bytes()
    except OSError as exc:
        return FileScan(rel, 0, (), (), error=str(exc))
    try:
        tree = ast.parse(data, filename=rel)
    except (SyntaxError, ValueError) as exc:
        return FileScan(rel, len(data), (), (), error=f"{type(exc).__name__}: {exc}")
    imports, names = _imports(tree)
    risky: list[tuple[str, str]] = []
    for name in [*imports, *names]:
        hit = _matches(name, RISKY_MODULES)
        if hit is not None and not any(r == hit for r, _ in risky):
            risky.append((name, RISKY_MODULES[hit]))
    imports = list(dict.fromkeys([*imports, *(n for n, _ in risky if n in names)]))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name is None:
            continue
        for call, reason in RISKY_CALLS.items():
            if name == call or (call.startswith("os.") and name.startswith(call)):
                risky.append((f"{name}()", reason))
                break
    return FileScan(rel, len(data), tuple(imports), tuple(dict.fromkeys(risky)))


def scan_tree(root: str | os.PathLike[str]) -> list[FileScan]:
    """Scan every ``.py`` file under *root* (sorted by path)."""
    base = Path(root)
    out: list[FileScan] = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                full = Path(folder) / name
                if full.is_symlink():
                    continue
                out.append(scan_file(full, full.relative_to(base).as_posix()))
    return sorted(out, key=lambda s: s.path)


def network_imports(scan: FileScan) -> list[str]:
    """The imports of a scanned file that reach the network."""
    return [name for name in scan.imports if _matches(name, NETWORK_MODULES)]


# ---------------------------------------------------------------------------
# @module(...) metadata, read without importing
# ---------------------------------------------------------------------------

_META_KEYS = (
    "name",
    "title",
    "description",
    "details",
    "keywords",
    "author",
    "requires",
    "validation",
    "params",
    "returns",
)


def _is_module_decorator(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _dotted(node.func)
    return name is not None and (name == "module" or name.endswith(".module"))


def _literal(node: ast.expr) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def module_metadata(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """The literal ``@module(...)`` arguments of a module file, or ``None``.

    Only literal values are read (``ast.literal_eval``); anything computed is
    left out. ``keywords`` and ``requires`` are split as the decorator does
    (``"llm"`` / ``"network"`` keywords move to ``requires``). When a file
    decorates several functions, the one named like the file wins.
    """
    from pytacheck.module import CAPABILITIES, SECTION_LEVELS

    p = Path(path)
    try:
        tree = ast.parse(p.read_bytes(), filename=p.name)
    except (OSError, SyntaxError, ValueError):
        return None
    found: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not _is_module_decorator(deco):
                continue
            assert isinstance(deco, ast.Call)
            meta: dict[str, Any] = {"function": node.name}
            if deco.args:
                meta["name"] = _literal(deco.args[0])
            for kw in deco.keywords:
                if kw.arg in _META_KEYS:
                    meta[kw.arg] = _literal(kw.value)
            found.append(meta)
    if not found:
        return None
    chosen = next((m for m in found if (m.get("name") or m["function"]) == p.stem), found[0])
    keywords = chosen.get("keywords") or []
    keywords = [keywords] if isinstance(keywords, str) else list(keywords)
    requires = chosen.get("requires") or []
    requires = [requires] if isinstance(requires, str) else list(requires)
    requires = list(dict.fromkeys([*requires, *(k for k in keywords if k in CAPABILITIES)]))
    keywords = [k for k in keywords if k not in CAPABILITIES]
    section = next((k for k in keywords if k in SECTION_LEVELS), "general")
    author = chosen.get("author") or []
    return {
        "name": chosen.get("name") or chosen["function"],
        "function": chosen["function"],
        "title": chosen.get("title") if isinstance(chosen.get("title"), str) else "",
        "description": textwrap.dedent(chosen.get("description") or "").strip()
        if isinstance(chosen.get("description"), str)
        else "",
        "details": textwrap.dedent(chosen.get("details") or "").strip()
        if isinstance(chosen.get("details"), str)
        else "",
        "keywords": keywords,
        "section": section,
        "author": [author] if isinstance(author, str) else list(author),
        "requires": requires,
        "validation": chosen.get("validation")
        if isinstance(chosen.get("validation"), dict)
        else None,
    }
