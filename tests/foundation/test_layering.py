"""The package layers of ARCHITECTURE.md §2.1 hold, derived from the imports (gate G6).

The test parses every file under ``src/pytacheck`` with ``ast`` and collects each
import wherever it sits: at module level, in a function, in a ``TYPE_CHECKING``
block, as ``from . import x``, or as a constant string given to
``importlib.import_module`` or ``__import__``. Lazy-export maps and computed
module names are not imports the parser can see.

**Names resolve as in Python.** ``from pkg import name`` gives what
``pkg/__init__`` binds to ``name`` at module level (a from-import, ``import x as
name``, or ``name = other`` or ``name: T = other`` for an imported ``other``,
also in a ``TYPE_CHECKING`` block), and only otherwise the submodule
``pkg.name``. So ``from pytacheck import module`` is the ``module()`` decorator,
which the top level imports under the name of its module. A name the package
binds is still read from the package, so the edge goes to the package whose
``__init__`` runs, whatever the name resolves to. Only a submodule that the
package does not bind, or binds to itself, is an edge to the submodule. ``import
pytacheck.module``, with or without ``as``, is the module, and so is an
attribute chain through it: a chain reads a submodule before a name the package
binds. A chain such as ``pytacheck.papers.model.Paper`` is one edge, judged by
its longest prefix that is a module or a name a package binds, like ``from
pytacheck.papers.model import Paper``. The edge also keeps each module below the
top level that a bound name is read from: ``llm`` for ``pytacheck.llm.Paper``,
``papers.model`` for ``pytacheck.papers.model.Paper``. An import inside a
function or class of an ``__init__`` binds nothing in the package. The graph
feeds three rules.

**Foundation.** §2.1 says ``core/**`` imports only ``_r``, ``_values``,
``_json``, ``papers.model``, ``papers.schema`` and ``papers.ids``. ``core/`` and
``papers/ids.py`` do not exist yet. Today the modules that exist are the
foundation the core will build on, and they must already keep that promise: they
import only each other, and nothing else in ``pytacheck``. Attribute access
counts as an import, so ``pytacheck.text_search(...)``, which loads ``text``
through the top-level lazy ``__getattr__``, is an upward edge. A chain is judged
by the name it resolves to and by each module it reads a bound name from, so
``pytacheck.llm.Paper`` and ``pytacheck.llm.r.bind_rows`` are edges to ``llm``.
The top level is judged by the name alone, so ``pytacheck.Paper`` is the
foundation's ``Paper``.

**Core.** Once ``core/**`` exists it keeps the positive list of §2.1: it imports
only the foundation above and the core itself. §2.6 adds what ``core/run.py``
needs for settings and caches: ``config``, ``llm``, the cache store (``cache``),
``repository``, the options overlay in ``utils`` (L6-1), the packs overlay in
``packs.registry``, and from ``pytacheck.module`` the resolver and ``use()``
(L5-7). From ``utils`` and ``pytacheck.module`` only those names are allowed,
and from ``packs`` only ``registry``, so run.py may not import a runner built on
``execute()``, such as ``run_session`` or ``pack check``. It may import
``pytacheck.module`` or ``pytacheck.utils`` as a namespace; each name it then
uses is judged on its own. Where §2.1 and §2.6 collide, §2.6 wins, but only
for run.py and only for those names. The positive list judges a name as it is
written. The top level is a façade: ``from pytacheck import x`` is reported
under x's layer when that layer is banned below, and as an import of the top
level otherwise, even when x is defined in the foundation. In any core file,
run.py too, the lint also names the layers §2.1 says the core never imports
(``text``, ``modules``, ``report``, ``api``, ``cli``, ``archives``,
``datacheck``, ``codecheck``), ``pytacheck.doc``, and the permanent façades of
§2.10, because the façades import the core, never the reverse. These are found
through re-exports too, both ways: ``from pytacheck.report import config`` is
reported as ``report``, and ``from pytacheck.llm import thing`` as ``report``
when ``llm`` binds ``thing`` to something in ``report``. The façades in ``pytacheck.module`` are banned as names
(``module``, ``module_run`` and ``get_prev_outputs``). A package's ``__init__``
is not an import edge of its submodules.

**Migrated modules.** A check module (a file under ``modules/``) that imports
``pytacheck.doc`` counts as migrated. It may not use ``text.search``,
``papers.tables``, ``_r.frames`` or ``_r.regex.grepl``, however it reaches them:
a module import, a name imported from a package that re-exports it (found in the
``__init__`` files), or attribute access such as ``regex.grepl``. Only an import
statement marks a module as migrated, also one in a ``TYPE_CHECKING`` block;
attribute access to ``pytacheck.doc`` does not. Façades such as ``compat`` or
the top-level ``__init__`` re-export the old names on purpose, so the rule does
not apply to them. ``pytacheck.doc`` does not exist yet, so this rule finds
nothing today and is exercised on synthetic trees.

There is no rule for YAML modules (decision 2 is (c)).

**The allow-list is a ratchet.** ``ALLOWED`` holds the violations that exist
today, one line each with a reason. A violation that is not in it fails, and an
entry with no matching violation fails too, so the list only shrinks. The
mapping above finds no violation on the current tree, so the list is empty. A
new need does not go in ``ALLOWED``: change the lists in ARCHITECTURE.md §2.1 or
§2.6 first, then the lists here.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PKG = "pytacheck"
SRC = Path(__file__).resolve().parents[2] / "src" / PKG


def _names(*relative: str) -> tuple[str, ...]:
    return tuple(f"{PKG}.{name}" for name in relative)


FOUNDATION = _names("_r", "_values", "_json", "papers.model", "papers.schema", "papers.ids")
CORE = _names("core")
LOWER = FOUNDATION + CORE
UPPER = _names("text", "modules", "report", "api", "cli", "archives", "datacheck", "codecheck")
DOC = f"{PKG}.doc"
# §2.6: what core/run.py may import on top of the positive list; a name inside a
# module allows that name alone
RUN = f"{PKG}.core.run"
RUN_CONTEXT = _names(
    "config",
    "llm",
    "cache",  # the CacheStore, not written yet
    "repository",  # RepoIndex, not written yet
    # the options overlay (L6-1); the snapshot pair is not written yet
    "utils.get_option",
    "utils.options",
    "utils.local_options",
    "utils.options_snapshot",
    "utils.options_restore",
    # the packs overlay and the pack loader, not pack check
    "packs.registry",
    # the resolver and use() (L5-7); the snapshot pair is not written yet
    "module.ModuleSpec",
    "module.ModuleError",
    "module.module_find",
    "module.use",
    "module.use_setting",
    "module.use_snapshot",
    "module.use_restore",
)
# modules run.py may import whole, for the names above that live in them
RUN_NAMESPACES = _names("utils", "module")
# the permanent façades (§2.10); the ones in pytacheck.module are banned as names
FACADES = _names(
    "papers.tables",
    "papers.io.test_paper",
    "papers.io.demopaper",
    "stats",
    "io.read",
    "compat",
    "module.module",
    "module.module_run",
    "module.get_prev_outputs",
)
CHECK_MODULES = f"{PKG}.modules"
# what a migrated module may not use; a name is banned along with everything under it
BANNED = _names("text.search", "papers.tables", "_r.frames", "_r.regex.grepl")
# named by §2.1 but not written yet
NOT_YET = frozenset(_names("core", "doc", "papers.ids", "compat"))

# (rule, file under src/pytacheck, banned target) -> why it is still there. It
# holds today's violations and only shrinks; a new need changes the design first.
ALLOWED: dict[tuple[str, str, str], str] = {}


def _under(name: str, prefixes: tuple[str, ...] | str) -> bool:
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    return any(name == p or name.startswith(p + ".") for p in prefixes)


# (module, name): what ``from module import name`` gives, or the module itself when
# name is ""
Ref = tuple[str, str]


@dataclass(frozen=True)
class Edge:
    path: str  # relative to the package root
    module: str  # dotted name of the importing file
    # module whose code runs: for a from-import, the package unless the name is a
    # submodule it does not bind; for attribute access, the name as written
    target: str
    symbol: str  # what is named, as written, which may be a name inside the target
    resolved: str  # what that name is, followed through the __init__ files
    line: int
    note: str = ""  # "type-checking", "importlib" or "attribute"
    # for attribute access, each module below the top level that a bound name is read from
    via: tuple[str, ...] = ()


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Visitor(ast.NodeVisitor):
    def __init__(self, package: str, is_package: bool) -> None:
        self.package, self.is_package = package, is_package
        self.refs: dict[str, Ref] = {}  # local name -> what it is bound to
        self.exports: dict[str, Ref] = {}  # what an __init__ binds at module level
        self.uses: list[tuple[Ref, int, str]] = []
        self.chains: list[tuple[str, tuple[str, ...], int]] = []
        self.type_checking = 0
        self.scope = 0

    def _use(self, ref: Ref, line: int, note: str = "") -> None:
        if self.type_checking and not note:
            note = "type-checking"
        self.uses.append((ref, line, note))

    def _bind(self, name: str, ref: Ref) -> None:
        self.refs[name] = ref
        if self.is_package and not self.scope:
            self.exports[f"{self.package}.{name}"] = ref

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        self.type_checking += 1
        for child in node.body:
            self.visit(child)
        self.type_checking -= 1
        for child in node.orelse:
            self.visit(child)

    def _visit_scope(self, node: ast.AST) -> None:
        self.scope += 1
        self.generic_visit(node)
        self.scope -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scope

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _under(alias.name, PKG):
                continue
            self._use((alias.name, ""), node.lineno)
            self._bind(alias.asname or PKG, (alias.name if alias.asname else PKG, ""))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:
            parts = self.package.split(".")
            parts = parts[: len(parts) - (node.level - 1)]
            base = ".".join(parts + ([base] if base else []))
        if not _under(base, PKG):
            return
        for alias in node.names:
            if alias.name == "*":
                self._use((base, ""), node.lineno)
                continue
            self._use((base, alias.name), node.lineno)
            self._bind(alias.asname or alias.name, (base, alias.name))

    def _alias(self, targets: list[ast.expr], value: ast.expr | None) -> None:
        # an alias of an imported name binds that name
        if isinstance(value, ast.Name) and value.id in self.refs:
            for target in targets:
                if isinstance(target, ast.Name):
                    self._bind(target.id, self.refs[value.id])

    def visit_Assign(self, node: ast.Assign) -> None:
        self._alias(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._alias([node.target], node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in ("import_module", "__import__") and node.args:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _under(arg.value, PKG)
            ):
                self._use((arg.value, ""), node.lineno, "importlib")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # the whole chain is one use; its prefixes are not visited on their own
        chain = [node.attr]
        inner: ast.expr = node.value
        while isinstance(inner, ast.Attribute):
            chain.append(inner.attr)
            inner = inner.value
        if isinstance(inner, ast.Name):
            self.chains.append((inner.id, tuple(reversed(chain)), node.lineno))
        self.visit(inner)


@dataclass(frozen=True)
class _Names:
    """How Python resolves a name, given the modules and what each ``__init__`` binds."""

    known: frozenset[str]
    exports: dict[str, str]

    def imported(self, ref: Ref) -> str:
        """``from module import name`` gives what the package binds, else the submodule."""
        module, name = ref
        return self.exports.get(f"{module}.{name}", f"{module}.{name}") if name else module

    def attribute(self, value: str, is_module: bool, name: str) -> str | None:
        """``value.name`` is a submodule first, then what the package binds.

        ``None`` means the attribute is read from a value, not from a module.
        """
        full = f"{value}.{name}"
        if full in self.known:
            return full
        if full in self.exports:
            return self.exports[full]
        return full if is_module else None

    def edge(self, rel: str, module: str, ref: Ref, line: int, note: str) -> Edge:
        symbol = f"{ref[0]}.{ref[1]}" if ref[1] else ref[0]
        resolved = self.imported(ref)
        # a name the package binds runs its __init__, whatever the name resolves to
        target = symbol if resolved == symbol and symbol in self.known else ref[0]
        return Edge(rel, module, target, symbol, resolved, line, note)

    def chain(self, rel: str, module: str, ref: Ref, attrs: tuple[str, ...], line: int) -> Edge:
        """An attribute chain, judged once by its longest prefix that is a module or a bound name."""
        written = f"{ref[0]}.{ref[1]}" if ref[1] else ref[0]
        resolved = self.imported(ref)
        # what an import statement names is a module
        is_module = not ref[1] or resolved in self.known
        via: tuple[str, ...] = ()
        for attr in attrs:
            value = self.attribute(resolved, is_module, attr)
            if value is None:
                break
            if f"{resolved}.{attr}" not in self.known and resolved != PKG:
                via += (resolved,)
            written, resolved = f"{written}.{attr}", value
            is_module = value in self.known
        return Edge(rel, module, written, written, resolved, line, "attribute", via)


def _exports(bound: dict[str, Ref]) -> dict[str, str]:
    """Where each name a package binds resolves to, followed through the other packages."""

    def follow(key: str, seen: frozenset[str]) -> str:
        module, name = bound[key]
        if not name:
            return module
        full = f"{module}.{name}"
        # ``from . import x`` in an __init__ binds the submodule
        return follow(full, seen | {full}) if full in bound and full not in seen else full

    return {key: follow(key, frozenset({key})) for key in bound}


def build(root: Path) -> tuple[list[Edge], dict[str, str]]:
    """The import edges of every file under *root*, and what the ``__init__`` files bind."""
    files = sorted(root.rglob("*.py"))
    visited: list[tuple[str, str, _Visitor]] = []
    bound: dict[str, Ref] = {}
    for path in files:
        parts = list(path.relative_to(root.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        visitor = _Visitor(module if is_package else module.rpartition(".")[0], is_package)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        visited.append((path.relative_to(root).as_posix(), module, visitor))
        bound.update(visitor.exports)
    exports = _exports(bound)
    names = _Names(frozenset(module for _, module, _ in visited), exports)
    edges: list[Edge] = []
    for rel, module, visitor in visited:
        edges += [names.edge(rel, module, ref, line, note) for ref, line, note in visitor.uses]
        edges += [
            names.chain(rel, module, visitor.refs[name], attrs, line)
            for name, attrs, line in visitor.chains
            if name in visitor.refs
        ]
    return edges, exports


def violations(root: Path) -> dict[tuple[str, str, str], Edge]:
    """Every layering violation under *root*, keyed like ``ALLOWED``."""
    edges, _ = build(root)
    migrated = {
        e.path
        for e in edges
        if e.note != "attribute" and _under(e.module, CHECK_MODULES) and _under(e.symbol, DOC)
    }
    found: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        symbol = edge.resolved
        if _under(edge.module, FOUNDATION):
            # a name reached by attribute is judged where it is defined, and by each
            # module it is read from; the top level is judged by the name alone
            targets = [symbol, *edge.via] if edge.note == "attribute" else [edge.target]
            for target in targets:
                if _under(target, PKG) and not _under(target, LOWER):
                    found.setdefault(("lower", edge.path, target), edge)
        if _under(edge.module, CORE):
            layers = [
                layer
                for layer in (*UPPER, *FACADES, DOC)
                if _under(edge.target, layer) or _under(symbol, layer)
            ]
            for layer in layers:
                found.setdefault(("core", edge.path, layer), edge)
            # the positive list judges the name as written, not where it resolves
            run = edge.module == RUN
            allowed = LOWER + RUN_CONTEXT if run else LOWER
            name = edge.symbol
            if (
                not layers
                and _under(name, PKG)
                and not _under(name, allowed)
                and not (run and name in RUN_NAMESPACES)
            ):
                found.setdefault(("core", edge.path, edge.target), edge)
        if edge.path in migrated:
            for banned in BANNED:
                if _under(symbol, banned):
                    found.setdefault(("migrated", edge.path, banned), edge)
    return found


def ratchet(
    found: dict[tuple[str, str, str], Edge], allowed: dict[tuple[str, str, str], str]
) -> tuple[list[str], list[str]]:
    """The violations nobody allowed, and the allowed entries that no longer occur."""
    new = [
        f"{rule}: {path}:{edge.line} imports {edge.symbol}"
        + (f" ({edge.note})" if edge.note else "")
        for (rule, path, target), edge in sorted(found.items())
        if (rule, path, target) not in allowed
    ]
    stale = [
        f"{rule}: {path} -> {target}"
        for rule, path, target in sorted(allowed)
        if (rule, path, target) not in found
    ]
    return new, stale


def test_layers_hold_on_the_package() -> None:
    new, stale = ratchet(violations(SRC), ALLOWED)
    assert not new, (
        "new layering violations (fix the import; a new need changes ARCHITECTURE.md §2.1"
        " or §2.6 first, not ALLOWED):\n" + "\n".join(new)
    )
    assert not stale, "remove these from ALLOWED, the violation is gone:\n" + "\n".join(stale)


def test_allow_list_is_well_formed() -> None:
    for (rule, path, target), reason in ALLOWED.items():
        assert rule in ("lower", "core", "migrated")
        assert (SRC / path).is_file(), path
        assert target.startswith(PKG + "."), target
        assert reason.strip(), f"{path} -> {target} needs a reason"


# the names a rule matches on; a rename must not turn a rule into one that checks nothing
LAYERS = (*LOWER, *UPPER, *FACADES, *BANNED, CHECK_MODULES, DOC)


def layer_problems(root: Path) -> list[str]:
    """Named layers that are gone, and names in ``NOT_YET`` that exist now."""
    problems = []
    for name in LAYERS:
        if name in NOT_YET:
            if _exists(root, name):
                problems.append(f"{name} exists now: remove it from NOT_YET")
        elif not _exists(root, name):
            problems.append(f"{name} is gone: update the layer lists")
    return problems


def test_named_layers_exist() -> None:
    assert set(LAYERS) >= NOT_YET, "every NOT_YET name must be checked"
    assert layer_problems(SRC) == []


def _exists(root: Path, name: str) -> bool:
    rel = Path(*name.split(".")[1:])
    return (
        (root / rel).is_dir() or (root / rel.with_suffix(".py")).is_file() or _is_symbol(root, name)
    )


def _is_symbol(root: Path, name: str) -> bool:
    """Whether *name* is a function defined in the module above it."""
    module, _, func = name.rpartition(".")
    if module == PKG:
        return False
    path = root / Path(*module.split(".")[1:]).with_suffix(".py")
    return path.is_file() and f"def {func}(" in path.read_text(encoding="utf-8")


def test_layer_problems_are_seen_in_both_directions(tmp_path: Path) -> None:
    root = tree(tmp_path, {**BASE, "doc/__init__.py": "", "compat.py": ""})
    problems = layer_problems(root)
    for name in sorted(NOT_YET):
        assert f"{name} exists now: remove it from NOT_YET" in problems
    assert f"{PKG}.stats is gone: update the layer lists" in problems
    assert f"{PKG}.module.get_prev_outputs is gone: update the layer lists" in problems
    assert not any(p.startswith(f"{PKG}.module.module_run ") for p in problems)


def test_graph_sees_the_real_imports() -> None:
    edges, exports = build(SRC)
    seen = {(e.module, e.target) for e in edges}
    assert (f"{PKG}.papers.model", f"{PKG}.papers.schema") in seen
    assert any(e.note == "importlib" for e in edges)
    assert exports[f"{PKG}._r.grepl"] == f"{PKG}._r.regex.grepl"
    # the root binds the module() decorator to the name of its module
    assert exports[f"{PKG}.module"] == f"{PKG}.module.module"
    # report/__init__ imports its own submodule report
    assert exports[f"{PKG}.report.report"] == f"{PKG}.report.report"
    # an import inside a function binds nothing in the package
    assert f"{PKG}.archives.report_message" not in exports
    # function-level imports are in the graph
    assert any(e.path == "papers/io.py" and e.target == f"{PKG}.io.bibr12" for e in edges)


# ---- synthetic trees: every rule flags what it should, and only that ----


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src" / PKG
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        for parent in path.relative_to(root).parents[:-1]:
            (root / parent / "__init__.py").touch()
    return root


BASE = {
    "text/search.py": "def text_search(): ...\n",
    "text/json_expand.py": "def as_numeric(): ...\n",
    "text/__init__.py": "from pytacheck.text.search import text_search\n",
    "papers/tables.py": "",
    "papers/model.py": "",
    "papers/schema.py": "",
    "papers/ids.py": "",
    "_values.py": "",
    "_json.py": "",
    "text/extract.py": "",
    "_r/frames.py": "def bind_rows(): ...\n",
    "_r/regex.py": "def grepl(): ...\n",
    "_r/__init__.py": (
        "from pytacheck._r.frames import bind_rows\nfrom pytacheck._r.regex import grepl\n"
    ),
    "config.py": "",
    "log.py": "",
    "module.py": "def module(): ...\ndef module_run(): ...\n",
    "papers/io.py": "def test_paper(): ...\ndef demopaper(): ...\n",
    "core/doc.py": "",
    "__init__.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pytacheck.text import text_search\n"
        # as in the real package: the decorator has the name of its module
        "    from pytacheck.module import module, module_run\n"
        "    from pytacheck.io.read import read\n"
        "    from pytacheck.papers.io import demopaper, test_paper\n"
        "    from pytacheck.papers.model import Paper\n"
    ),
    "io/read.py": "def read(): ...\n",
}


def found_in(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str]]:
    return set(violations(tree(tmp_path, {**BASE, **files})))


@pytest.mark.parametrize(
    ("source", "layer"),
    [
        # the two the design review found in the first core
        ("from pytacheck.text.search import _legacy_text_frame\n", "text"),
        ("from pytacheck.text.json_expand import as_numeric\n", "text"),
        ("import pytacheck.modules.ethics_check\n", "modules"),
        ("def f():\n    from pytacheck.report import x\n", "report"),
        ("from ..text import search\n", "text"),
        ("from .. import text\n", "text"),
        ("from pytacheck import text_search\n", "text"),
        ("import pytacheck.api as api\n", "api"),
        ("import importlib\nimportlib.import_module('pytacheck.archives.osf')\n", "archives"),
        ("__import__('pytacheck.cli')\n", "cli"),
        # a call or a subscript inside an attribute chain is still read
        (
            "import importlib\nimportlib.import_module('pytacheck.text.search').text_search('x')\n",
            "text",
        ),
        (
            "import importlib\n(importlib.import_module('pytacheck.report'),)[0].render()\n",
            "report",
        ),
        ("from pytacheck.text import *\n", "text"),
        ("from pytacheck.datacheck import x\n", "datacheck"),
        ("from pytacheck.codecheck import x\n", "codecheck"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pytacheck.report\n",
            "report",
        ),
        # reached by attribute through the lazy top-level __getattr__
        ("import pytacheck._r.regex\nx = pytacheck.text_search(1)\n", "text"),
        ("import pytacheck\nx = pytacheck.text_search.__name__\n", "text"),
        # the façades import the core, never the reverse
        ("from pytacheck.papers.tables import paper_table\n", "papers.tables"),
        ("from pytacheck.papers import tables\n", "papers.tables"),
        ("from pytacheck.stats import stats\n", "stats"),
        ("from pytacheck import stats\n", "stats"),
        ("from pytacheck.io.read import read\n", "io.read"),
        ("from pytacheck import read\n", "io.read"),
        ("from pytacheck.compat import paper_table\n", "compat"),
        ("from pytacheck.module import module_run\n", "module.module_run"),
        ("from pytacheck import module_run as run\n", "module.module_run"),
        ("import pytacheck.module as m\nm.get_prev_outputs('a', 'b')\n", "module.get_prev_outputs"),
        ("from pytacheck.module import module\n", "module.module"),
        ("from pytacheck import test_paper\n", "papers.io.test_paper"),
        ("from pytacheck.papers.io import demopaper\n", "papers.io.demopaper"),
        # anything not on the positive list
        ("from pytacheck.presets import preset\n", "presets"),
        ("import pytacheck.fileinfo\n", "fileinfo"),
        ("from pytacheck.io.grobid import x\n", "io.grobid"),
        ("from pytacheck.provenance import module_provenance\n", "provenance"),
        ("from pytacheck import not_reexported\n", ""),
        ("import pytacheck\n", ""),
        # the top level is a façade, even for a name defined in the foundation
        ("from pytacheck import Paper\n", ""),
        ("import pytacheck._r\nx = pytacheck.Paper\n", "Paper"),
        # the run context is for core/run.py alone
        ("from pytacheck.config import email\n", "config"),
        ("from pytacheck.module import module_find\n", "module"),
        ("import pytacheck.module\n", "module"),
        ("from pytacheck.utils import local_options\n", "utils"),
        ("from pytacheck.packs.registry import overlay\n", "packs.registry"),
    ],
)
def test_core_may_not_reach_up(tmp_path: Path, source: str, layer: str) -> None:
    found = found_in(tmp_path, {"core/facets.py": source})
    assert ("core", "core/facets.py", f"{PKG}.{layer}" if layer else PKG) in found


@pytest.mark.parametrize(
    ("source", "layer"),
    [
        ("from pytacheck.text.search import text_search\n", "text"),
        ("from pytacheck.module import module_run\n", "module.module_run"),
        ("from pytacheck.module import module\n", "module.module"),
        # the root binds the module() decorator to the name of its module
        ("from pytacheck import module\n\n@module('x')\ndef f(): ...\n", "module.module"),
        ("from pytacheck import module\nspec = module.module_find('x')\n", "module.module"),
        ("import pytacheck.module as m\nm.get_prev_outputs('a', 'b')\n", "module.get_prev_outputs"),
        ("from pytacheck import test_paper\n", "papers.io.test_paper"),
        ("from pytacheck import demopaper\n", "papers.io.demopaper"),
        ("from pytacheck.stats import stats\n", "stats"),
        ("import pytacheck.db\n", "db"),
        ("from pytacheck.presets import preset\n", "presets"),
        ("from pytacheck.archives import osf_pat\n", "archives"),
        ("import pytacheck\n", ""),
        # runners built on execute() are not in the run context
        ("from pytacheck.batch import run_batch\n", "batch"),
        ("from pytacheck.packs.check import pack_check\n", "packs.check"),
        ("from pytacheck.packs import check\n", "packs"),
        ("from pytacheck.packs import pack_check\n", "packs"),
        ("from pytacheck.module import run_session\n", "module"),
        ("from pytacheck.module import module_run_each\n", "module"),
        ("import pytacheck.module as m\nm.run_session()\n", "module.run_session"),
        # utils is there for the options overlay alone
        ("from pytacheck.utils import left_join\n", "utils"),
        ("import pytacheck.utils as u\nu.online()\n", "utils.online"),
    ],
)
def test_run_py_may_not_go_beyond_the_run_context(tmp_path: Path, source: str, layer: str) -> None:
    found = found_in(tmp_path, {"core/run.py": source})
    assert ("core", "core/run.py", f"{PKG}.{layer}" if layer else PKG) in found


@pytest.mark.parametrize(
    "layer",
    [
        "text",
        "modules",
        "report",
        "api",
        "cli",
        "archives",
        "datacheck",
        "codecheck",
        "doc",
        "papers.tables",
        "papers.io.test_paper",
        "papers.io.demopaper",
        "stats",
        "io.read",
        "compat",
        "module.module",
        "module.module_run",
        "module.get_prev_outputs",
    ],
)
def test_run_py_may_not_reach_a_banned_layer_through_a_reexport(tmp_path: Path, layer: str) -> None:
    package, _, name = f"{PKG}.{layer}".rpartition(".")
    files = {
        "llm/__init__.py": f"from {package} import {name} as thing\n",
        "core/run.py": "from pytacheck.llm import thing\n",
    }
    assert found_in(tmp_path, files) == {("core", "core/run.py", f"{PKG}.{layer}")}


@pytest.mark.parametrize(
    "init", ["from pytacheck import config\n", "import pytacheck.config as config\n"]
)
def test_a_reexport_is_reported_under_the_banned_layer(tmp_path: Path, init: str) -> None:
    """``config`` is in the run context, but reading it from ``report`` runs ``report``."""
    files = {"report/__init__.py": init, "core/run.py": "from pytacheck.report import config\n"}
    assert found_in(tmp_path, files) == {("core", "core/run.py", f"{PKG}.report")}


def test_only_an_init_file_reexports_names(tmp_path: Path) -> None:
    files = {
        "llm/__init__.py": "def render(): ...\n",
        "llm/providers.py": "from pytacheck.report import render\n",
        "core/run.py": "from pytacheck.llm import render\n",
    }
    assert found_in(tmp_path, files) == set()


def test_core_may_import_the_foundation(tmp_path: Path) -> None:
    source = (
        "import pytacheck._r.regex\n"
        "from pytacheck._r import regex, frames\n"
        "from pytacheck._values import as_float\n"
        "from pytacheck import _json\n"
        "from pytacheck.papers.model import Paper\n"
        "from pytacheck.papers import model, schema, ids\n"
        "from pytacheck.papers.ids import paper_id\n"
        "from pytacheck.core.doc import Doc\n"
        "import pytacheck.papers.model\n"
        "x = pytacheck.papers.model.Paper\n"
        "from . import doc\n"
        "from .doc import Doc\n"
        "import importlib\n"
        "importlib.import_module('pytacheck.core.hits')\n"
        "importlib.import_module(name)\n"
        "import numpy\n"
    )
    assert found_in(tmp_path, {"core/facets.py": source}) == set()


def test_core_may_import_what_the_run_context_needs(tmp_path: Path) -> None:
    """§2.6 gives ``core/run.py`` settings, caches and the resolved module specs."""
    source = (
        "from pytacheck.llm import LLMSettings\n"
        "from pytacheck.config import email\n"
        "from pytacheck.cache import CacheStore\n"
        "from pytacheck.repository import RepoIndex\n"
        "from pytacheck.utils import get_option, options, local_options\n"
        "from pytacheck.utils import options_snapshot, options_restore\n"
        "import pytacheck.utils as u\n"
        "u.local_options({})\n"
        "from pytacheck.packs import registry\n"
        "from pytacheck.packs.registry import overlay\n"
        "import pytacheck.packs.registry\n"
        "pytacheck.packs.registry.registry()\n"
        "from pytacheck.module import ModuleSpec, ModuleError, module_find\n"
        "from pytacheck.module import use, use_setting, use_snapshot, use_restore\n"
        "from pytacheck._r.regex import detector\n"
        "from pytacheck.core.output import Result\n"
    )
    files = {"packs/registry.py": "def registry(): ...\n", "core/run.py": source}
    assert found_in(tmp_path, files) == set()


# a name that is a module, however it is spelled, and the key it is flagged under
MODULE_SPELLINGS = [
    ("import pytacheck.module\n", "module"),
    ("import pytacheck.module as m\nspec = m.module_find('x')\n", "module"),
    ("import pytacheck.config\nspec = pytacheck.module.module_find('x')\n", "module.module_find"),
]


@pytest.mark.parametrize(("source", "key"), MODULE_SPELLINGS)
def test_a_module_name_is_the_module_not_a_reexport(tmp_path: Path, source: str, key: str) -> None:
    assert found_in(tmp_path / "run", {"core/run.py": source}) == set()
    found = found_in(tmp_path / "other", {"core/facets.py": source})
    assert ("core", "core/facets.py", f"{PKG}.{key}") in found
    assert ("core", "core/facets.py", f"{PKG}.module.module") not in found


# How each spelling resolves, as Python does it. A from-import gives what the
# package's __init__ binds, and only otherwise the submodule. An import statement
# and an attribute chain give the submodule first. A chain is one edge, judged by
# its longest prefix that is a module or a name a package binds. Each row lists
# the edges of the source as (target, symbol, resolved).
SPELLING_FILES = {
    "packs/registry.py": "def registry(): ...\n",
    "llm/__init__.py": (
        "from pytacheck import text as words\n"
        "from pytacheck.module import module_run as _run\n"
        "run = _run\n"
        "stage: object = _run\n"
        "import pytacheck.report as paint\n"
        "def f():\n    from pytacheck.report import render\n"
        "class C:\n    from pytacheck.report import shade\n"
        "async def g():\n    from pytacheck.report import tint\n"
    ),
}
SPELLINGS = [
    # import, and import as
    ("import pytacheck.module\n", [("module", "module", "module")]),
    ("import pytacheck.module as m\n", [("module", "module", "module")]),
    # from-import of a submodule the package does not bind
    ("from pytacheck.papers import model\n", [("papers.model", "papers.model", "papers.model")]),
    ("from pytacheck._r import regex\n", [("_r.regex", "_r.regex", "_r.regex")]),
    # from-import of a name the package binds, also when it shadows a submodule
    ("from pytacheck import module\n", [("", "module", "module.module")]),
    ("from pytacheck import text_search\n", [("", "text_search", "text.search.text_search")]),
    ("from pytacheck._r import grepl as g\n", [("_r", "_r.grepl", "_r.regex.grepl")]),
    ("from pytacheck.llm import words\n", [("llm", "llm.words", "text")]),
    ("from pytacheck.llm import run\n", [("llm", "llm.run", "module.module_run")]),
    ("from pytacheck.llm import stage\n", [("llm", "llm.stage", "module.module_run")]),
    ("from pytacheck.llm import paint\n", [("llm", "llm.paint", "report")]),
    # an import in a function or class of an __init__ binds nothing in the package
    ("from pytacheck.llm import render\n", [("llm", "llm.render", "llm.render")]),
    ("from pytacheck.llm import shade\n", [("llm", "llm.shade", "llm.shade")]),
    ("from pytacheck.llm import tint\n", [("llm", "llm.tint", "llm.tint")]),
    ("from pytacheck import unbound\n", [("", "unbound", "unbound")]),
    # attribute chains
    (
        "import pytacheck.module as m\nm.module_find('x')\n",
        [
            ("module", "module", "module"),
            ("module.module_find", "module.module_find", "module.module_find"),
        ],
    ),
    (
        "import pytacheck\npytacheck.module.module_find('x')\n",
        [("", "", ""), ("module.module_find", "module.module_find", "module.module_find")],
    ),
    (
        "from pytacheck import module\nmodule.module_find('x')\n",
        [("", "module", "module.module"), ("module", "module", "module.module")],
    ),
    (
        "import pytacheck\npytacheck.papers.model.Paper.x\n",
        [("", "", ""), ("papers.model.Paper", "papers.model.Paper", "papers.model.Paper")],
    ),
    (
        "import pytacheck\npytacheck.packs.registry.registry()\n",
        [
            ("", "", ""),
            ("packs.registry.registry", "packs.registry.registry", "packs.registry.registry"),
        ],
    ),
    (
        "import pytacheck\npytacheck.text_search.__name__\n",
        [("", "", ""), ("text_search", "text_search", "text.search.text_search")],
    ),
]


def _dotted(name: str) -> str:
    return f"{PKG}.{name}" if name else PKG


@pytest.mark.parametrize(("source", "expected"), SPELLINGS)
def test_each_spelling_resolves_as_python_does(
    tmp_path: Path, source: str, expected: list[tuple[str, str, str]]
) -> None:
    edges, _ = build(tree(tmp_path, {**BASE, **SPELLING_FILES, "probe.py": source}))
    found = [(e.target, e.symbol, e.resolved) for e in edges if e.path == "probe.py"]
    assert found == [tuple(map(_dotted, row)) for row in expected]


def test_layer_names_match_whole_components(tmp_path: Path) -> None:
    files = {
        "_values_extra.py": "from pytacheck.config import options\n",
        "core/facets.py": "import pytacheck.textual\nfrom pytacheck._values_extra import x\n",
        "_json.py": "from pytacheck._values_extra import x\n",
        "core_helpers.py": "from pytacheck.text.search import text_search\n",
    }
    # the positive list flags them under their own names, not as text or _values
    assert found_in(tmp_path, files) == {
        ("lower", "_json.py", f"{PKG}._values_extra"),
        ("core", "core/facets.py", f"{PKG}.textual"),
        ("core", "core/facets.py", f"{PKG}._values_extra"),
    }


def test_type_checking_else_branch_is_runtime(tmp_path: Path) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    pass\nelse:\n    import pytacheck.report\n"
    )
    found = violations(tree(tmp_path, {**BASE, "core/facets.py": source}))
    assert found[("core", "core/facets.py", f"{PKG}.report")].note == ""


def test_facades_may_import_the_core(tmp_path: Path) -> None:
    files = {
        "text/extract.py": "from pytacheck.core.doc import Doc\nfrom pytacheck.core import doc\n"
    }
    assert found_in(tmp_path, files) == set()


@pytest.mark.parametrize(
    "source",
    [
        "from pytacheck.llm import r\n",
        "import pytacheck._r\nx = pytacheck.llm.r\n",
        "import pytacheck._r\nx = pytacheck.llm.r.frames\n",
        # r.bind_rows is read from _r, after r is read from llm
        "import pytacheck._r\nx = pytacheck.llm.r.bind_rows\n",
        "from pytacheck.llm import Paper\n",
        "import pytacheck._r\nx = pytacheck.llm.Paper\n",
    ],
)
def test_foundation_sees_the_package_a_name_is_read_from(tmp_path: Path, source: str) -> None:
    """A name that ``llm`` binds runs ``llm/__init__``, even when it is in the foundation."""
    files = {
        "llm/__init__.py": "from pytacheck import _r as r\nfrom pytacheck.papers.model import Paper\n",
        "_json.py": source,
    }
    assert found_in(tmp_path, files) == {("lower", "_json.py", f"{PKG}.llm")}


def test_foundation_sees_each_package_a_chain_reads_from(tmp_path: Path) -> None:
    """``up`` is read from ``_r``, then ``Paper`` from ``llm``, which is above it."""
    files = {
        "_r/__init__.py": "from pytacheck import llm as up\n",
        "llm/__init__.py": "from pytacheck.papers.model import Paper\n",
        "_json.py": "import pytacheck._r\nx = pytacheck._r.up.Paper\n",
    }
    assert ("lower", "_json.py", f"{PKG}.llm") in found_in(tmp_path, files)


def test_foundation_modules_import_only_each_other(tmp_path: Path) -> None:
    found = found_in(
        tmp_path,
        {
            "_r/base.py": "from pytacheck._r.regex import is_na\n",
            "_values.py": "from pytacheck.config import options\n",
            "papers/schema.py": "def f():\n    from pytacheck.text import search\n",
            "_json.py": "from pytacheck import log\n",
            "_r/base2.py": "import pytacheck._r.regex\nx = pytacheck.text_search(1)\n",
            "_r/base3.py": "import pytacheck.papers.model\nx = pytacheck.papers.model.Paper\n",
            # papers.tables is above the foundation, so it may import text
            "papers/tables.py": "from pytacheck.text.search import text_search\n",
        },
    )
    assert found == {
        ("lower", "_values.py", f"{PKG}.config"),
        ("lower", "papers/schema.py", f"{PKG}.text.search"),
        ("lower", "_json.py", f"{PKG}.log"),
        ("lower", "_r/base2.py", f"{PKG}.text.search.text_search"),
    }


MIGRATED = "from pytacheck.doc import Doc\n"


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("from pytacheck.text.search import text_search\n", "text.search"),
        ("from pytacheck.text import search\n", "text.search"),
        ("import pytacheck.text.search as s\n", "text.search"),
        ("from pytacheck.text import text_search\n", "text.search"),
        ("from pytacheck.papers.tables import paper_table\n", "papers.tables"),
        ("from pytacheck.papers import tables\n", "papers.tables"),
        ("from pytacheck._r.frames import bind_rows\n", "_r.frames"),
        ("from pytacheck._r import frames\n", "_r.frames"),
        ("from pytacheck._r import bind_rows\n", "_r.frames"),
        ("from pytacheck._r.regex import grepl\n", "_r.regex.grepl"),
        ("from pytacheck._r import grepl\n", "_r.regex.grepl"),
        ("from pytacheck._r.regex import grepl as g\n", "_r.regex.grepl"),
        ("from pytacheck._r import regex\nregex.grepl('a', 'b')\n", "_r.regex.grepl"),
        ("import pytacheck._r.regex as rx\nrx.grepl('a', 'b')\n", "_r.regex.grepl"),
        ("import pytacheck\npytacheck._r.regex.grepl('a', 'b')\n", "_r.regex.grepl"),
        ("def f():\n    from pytacheck._r.frames import count\n", "_r.frames"),
        ("import importlib\nimportlib.import_module('pytacheck._r.frames')\n", "_r.frames"),
    ],
)
def test_migrated_module_may_not_use_the_old_helpers(
    tmp_path: Path, source: str, target: str
) -> None:
    found = found_in(tmp_path, {"modules/m.py": MIGRATED + source})
    assert ("migrated", "modules/m.py", f"{PKG}.{target}") in found


@pytest.mark.parametrize(
    "source",
    [
        MIGRATED,
        MIGRATED + "from pytacheck._r.regex import gsub, regexec\n",
        MIGRATED + "from pytacheck._r import regex\nregex.gsub('a', 'b', 'c')\n",
        MIGRATED + "from pytacheck.text import extract\n",
        MIGRATED + "from pytacheck.papers.model import Paper\n",
        "from pytacheck import doc\n",
        "from pytacheck import doc\nfrom pytacheck.core.doc import Doc\n",
        "from pytacheck.text.search import text_search\nfrom pytacheck._r.regex import grepl\n",
    ],
)
def test_migrated_rule_is_derived_from_the_doc_import(tmp_path: Path, source: str) -> None:
    assert found_in(tmp_path, {"modules/m.py": source}) == set()


def test_a_type_checking_doc_import_marks_a_module_as_migrated(tmp_path: Path) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from pytacheck.doc import Doc\n"
        "from pytacheck._r.frames import count\n"
    )
    assert ("migrated", "modules/m.py", f"{PKG}._r.frames") in found_in(
        tmp_path, {"modules/m.py": source}
    )


def test_doc_attribute_access_does_not_mark_a_module_as_migrated(tmp_path: Path) -> None:
    source = "import pytacheck\nx = pytacheck.doc.Doc\nfrom pytacheck._r.frames import count\n"
    assert found_in(tmp_path, {"modules/m.py": source}) == set()


def test_facades_that_import_doc_are_not_migrated_modules(tmp_path: Path) -> None:
    reexports = (
        "from pytacheck.text.search import text_search\n"
        "from pytacheck.papers.tables import paper_table\n"
        "from pytacheck._r import bind_rows\n"
    )
    files = {
        "compat.py": "from pytacheck.doc import Result\n" + reexports,
        "__init__.py": (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    from pytacheck.doc import Doc\n"
            "    from pytacheck.text import text_search\n"
            "    from pytacheck.module import module_run\n"
        ),
        "report/blocks.py": "from pytacheck.doc import Result\n" + reexports,
    }
    assert found_in(tmp_path, files) == set()


def test_from_pytacheck_import_doc_marks_a_module_as_migrated(tmp_path: Path) -> None:
    source = "from pytacheck import doc\nfrom pytacheck._r.frames import count\n"
    assert ("migrated", "modules/m.py", f"{PKG}._r.frames") in found_in(
        tmp_path, {"modules/m.py": source}
    )


def test_doc_and_core_files_are_not_migrated_modules(tmp_path: Path) -> None:
    files = {
        "doc/__init__.py": "from pytacheck._r.frames import count\nfrom pytacheck.core.doc import Doc\n",
        "core/hits.py": "from pytacheck.doc import Doc\nfrom pytacheck._r.frames import count\n",
    }
    found = found_in(tmp_path, files)
    assert {key for key in found if key[0] == "migrated"} == set()
    # core importing the author API is still a layering violation
    assert ("core", "core/hits.py", f"{PKG}.doc") in found


def test_ratchet_fails_on_a_new_violation(tmp_path: Path) -> None:
    root = tree(tmp_path, {**BASE, "core/facets.py": "from pytacheck.text.search import x\n"})
    new, stale = ratchet(violations(root), {})
    assert new == [f"core: core/facets.py:1 imports {PKG}.text.search.x"]
    assert stale == []


def test_ratchet_accepts_an_allowed_violation(tmp_path: Path) -> None:
    root = tree(tmp_path, {**BASE, "core/facets.py": "from pytacheck.text.search import x\n"})
    allowed = {("core", "core/facets.py", f"{PKG}.text"): "moves in CORE-1b"}
    assert ratchet(violations(root), allowed) == ([], [])


def test_ratchet_fails_on_an_entry_that_is_no_longer_needed(tmp_path: Path) -> None:
    root = tree(tmp_path, {**BASE, "core/facets.py": "from pytacheck._values import as_float\n"})
    allowed = {("core", "core/facets.py", f"{PKG}.text"): "moves in CORE-1b"}
    new, stale = ratchet(violations(root), allowed)
    assert new == []
    assert stale == [f"core: core/facets.py -> {PKG}.text"]
