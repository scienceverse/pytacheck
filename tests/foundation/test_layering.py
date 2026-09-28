"""The package layers of ARCHITECTURE.md §2.1 hold, derived from the imports (gate G6).

The test parses every file under ``src/pytacheck`` with ``ast`` and collects each
import wherever it sits: at module level, in a function, in a ``TYPE_CHECKING``
block, as ``from . import x``, or as a constant string given to
``importlib.import_module`` or ``__import__``. Lazy-export maps and computed
module names are not imports the parser can see. The graph feeds two rules.

**Foundation.** §2.1 says ``core/**`` imports only ``_r``, ``_values``,
``_json``, ``papers.model``, ``papers.schema`` and ``papers.ids``. ``core/`` and
``papers/ids.py`` do not exist yet. Today the modules that exist are the
foundation the core will build on, and they must already keep that promise:
they import only each other, and nothing else in ``pytacheck``. Attribute
access counts as an import, so ``pytacheck.text_search(...)``, which loads
``text`` through the top-level lazy ``__getattr__``, is an upward edge.

**Core.** Once ``core/**`` exists it may not import the layers §2.1 says it never
imports (``text``, ``modules``, ``report``, ``api``, ``cli``, ``archives``,
``datacheck``, ``codecheck``), nor ``pytacheck.doc``. The rule is the never-list
and not the positive list, because §2.6 puts ``LLMSettings``, the config
setters, ``RepoIndex`` and ``CacheStore`` in ``core/run.py``, and the positive
list forbids them. The two sections disagree, so the lint enforces what both
accept. A package's ``__init__`` is not an import edge of its submodules.

**Migrated modules.** A file that imports ``pytacheck.doc`` counts as migrated
and may not use ``text.search``, ``papers.tables``, ``_r.frames`` or
``_r.regex.grepl``, however it reaches them: a module import, a name imported
from a package that re-exports it (found in the ``__init__`` files), or
attribute access such as ``regex.grepl``. ``pytacheck.doc`` does not exist yet,
so this rule finds nothing today and is exercised on synthetic trees.

There is no rule for YAML modules (decision 2 is (c)).

**The allow-list is a ratchet.** ``ALLOWED`` holds the violations that exist
today, one line each with a reason. A violation that is not in it fails, and an
entry with no matching violation fails too, so the list only shrinks. The
mapping above finds no violation on the current tree, so the list is empty.
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
# what a migrated module may not use; a name is banned along with everything under it
BANNED = _names("text.search", "papers.tables", "_r.frames", "_r.regex.grepl")
# named by §2.1 but not written yet
NOT_YET = frozenset(_names("core", "doc", "papers.ids"))

# (rule, file under src/pytacheck, banned target) -> why it is still there
ALLOWED: dict[tuple[str, str, str], str] = {}


def _under(name: str, prefixes: tuple[str, ...] | str) -> bool:
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    return any(name == p or name.startswith(p + ".") for p in prefixes)


@dataclass(frozen=True)
class Edge:
    path: str  # relative to the package root
    module: str  # dotted name of the importing file
    target: str  # module that is loaded
    symbol: str  # what is named, which may be a name inside the target
    line: int
    note: str = ""  # "type-checking", "importlib" or "attribute"


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str, package: str, known: set[str]) -> None:
        self.path, self.module, self.package, self.known = path, module, package, known
        self.edges: list[Edge] = []
        self.bindings: dict[str, str] = {}
        self.reexports: dict[str, str] = {}  # only for an __init__ file
        self.chains: list[tuple[list[str], int]] = []
        self.type_checking = 0

    def _edge(self, target: str, symbol: str, line: int, note: str = "") -> None:
        if self.type_checking and not note:
            note = "type-checking"
        self.edges.append(Edge(self.path, self.module, target, symbol, line, note))

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

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _under(alias.name, PKG):
                continue
            self._edge(alias.name, alias.name, node.lineno)
            self.bindings[alias.asname or PKG] = alias.name if alias.asname else PKG

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:
            parts = self.package.split(".")
            parts = parts[: len(parts) - (node.level - 1)]
            base = ".".join(parts + ([base] if base else []))
        if not _under(base, PKG):
            return
        for alias in node.names:
            symbol = base if alias.name == "*" else f"{base}.{alias.name}"
            self._edge(symbol if symbol in self.known else base, symbol, node.lineno)
            if alias.name != "*":
                bound = alias.asname or alias.name
                self.bindings[bound] = symbol
                if f"{self.package}.{bound}" != symbol:
                    self.reexports[f"{self.package}.{bound}"] = symbol

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
                self._edge(arg.value, arg.value, node.lineno, "importlib")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = [node.attr]
        inner: ast.expr = node.value
        while isinstance(inner, ast.Attribute):
            chain.append(inner.attr)
            inner = inner.value
        if isinstance(inner, ast.Name):
            self.chains.append(([inner.id, *reversed(chain)], node.lineno))
        self.generic_visit(node)


def build(root: Path) -> tuple[list[Edge], dict[str, str]]:
    """The import edges of every file under *root*, and the names each package re-exports."""
    files = sorted(root.rglob("*.py"))
    modules: dict[Path, tuple[str, bool]] = {}
    for path in files:
        parts = list(path.relative_to(root.parent).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        modules[path] = (".".join(parts[:-1] if is_package else parts), is_package)
    known = {name for name, _ in modules.values()}
    edges: list[Edge] = []
    reexports: dict[str, str] = {}
    for path, (module, is_package) in modules.items():
        package = module if is_package else module.rpartition(".")[0]
        rel = path.relative_to(root).as_posix()
        visitor = _Visitor(rel, module, package, known)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        edges += visitor.edges
        for parts, line in visitor.chains:
            if parts[0] in visitor.bindings:
                symbol = ".".join([visitor.bindings[parts[0]], *parts[1:]])
                edges.append(Edge(rel, module, symbol, symbol, line, "attribute"))
        if is_package:
            reexports.update(visitor.reexports)
    return edges, reexports


def _resolve(symbol: str, reexports: dict[str, str]) -> str:
    """Follow a name through the packages that re-export it to where it is defined."""
    for _ in range(10):
        if symbol not in reexports:
            return symbol
        symbol = reexports[symbol]
    return symbol


def violations(root: Path) -> dict[tuple[str, str, str], Edge]:
    """Every layering violation under *root*, keyed like ``ALLOWED``."""
    edges, reexports = build(root)
    migrated = {e.path for e in edges if e.note != "attribute" and _under(e.symbol, DOC)}
    found: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        symbol = _resolve(edge.symbol, reexports)
        if _under(edge.module, FOUNDATION):
            # a name reached by attribute is judged where it is defined
            target = symbol if edge.note == "attribute" else edge.target
            if _under(target, PKG) and not _under(target, LOWER):
                found.setdefault(("lower", edge.path, target), edge)
        if _under(edge.module, CORE):
            for layer in (*UPPER, DOC):
                if _under(edge.target, layer) or _under(symbol, layer):
                    found.setdefault(("core", edge.path, layer), edge)
        if edge.path in migrated and not _under(edge.module, (DOC, *CORE)):
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
        "new layering violations (fix the import, do not extend ALLOWED):\n" + "\n".join(new)
    )
    assert not stale, "remove these from ALLOWED, the violation is gone:\n" + "\n".join(stale)


def test_allow_list_is_well_formed() -> None:
    for (rule, path, target), reason in ALLOWED.items():
        assert rule in ("lower", "core", "migrated")
        assert (SRC / path).is_file(), path
        assert target.startswith(PKG + "."), target
        assert reason.strip(), f"{path} -> {target} needs a reason"


def test_named_layers_exist() -> None:
    """A rename must not turn a rule into one that checks nothing."""
    for name in (*LOWER, *UPPER, *BANNED):
        if name in NOT_YET:
            continue
        rel = Path(*name.split(".")[1:])
        assert (
            (SRC / rel).is_dir() or (SRC / rel.with_suffix(".py")).is_file() or _is_symbol(name)
        ), f"{name} is gone: update the layer lists"


def _is_symbol(name: str) -> bool:
    """Whether *name* is a function defined in the module above it."""
    module, _, func = name.rpartition(".")
    path = SRC / Path(*module.split(".")[1:]).with_suffix(".py")
    return path.is_file() and f"def {func}(" in path.read_text(encoding="utf-8")


def test_graph_sees_the_real_imports() -> None:
    edges, reexports = build(SRC)
    seen = {(e.module, e.target) for e in edges}
    assert (f"{PKG}.papers.model", f"{PKG}.papers.schema") in seen
    assert any(e.note == "importlib" for e in edges)
    assert reexports[f"{PKG}._r.grepl"] == f"{PKG}._r.regex.grepl"
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
    "module.py": "def module_run(): ...\n",
    "core/doc.py": "",
    "__init__.py": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pytacheck.text import text_search\n"
        "    from pytacheck.module import module_run\n"
    ),
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
        ("from pytacheck.datacheck import x\n", "datacheck"),
        ("from pytacheck.codecheck import x\n", "codecheck"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pytacheck.report\n",
            "report",
        ),
        # reached by attribute through the lazy top-level __getattr__
        ("import pytacheck._r.regex\nx = pytacheck.text_search(1)\n", "text"),
    ],
)
def test_core_may_not_reach_up(tmp_path: Path, source: str, layer: str) -> None:
    found = found_in(tmp_path, {"core/facets.py": source})
    assert ("core", "core/facets.py", f"{PKG}.{layer}") in found


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
        "from . import doc\n"
        "from .doc import Doc\n"
        "import importlib\n"
        "importlib.import_module('pytacheck.core.hits')\n"
        "importlib.import_module(name)\n"
        "import numpy\n"
    )
    assert found_in(tmp_path, {"core/facets.py": source}) == set()


def test_core_may_import_what_the_run_context_needs(tmp_path: Path) -> None:
    """§2.6 gives ``core/run.py`` settings, caches and the module runner."""
    source = (
        "from pytacheck.llm import LLMSettings\n"
        "from pytacheck.config import email\n"
        "from pytacheck.cache import CacheStore\n"
        "from pytacheck.repository import RepoIndex\n"
        "from pytacheck.module import module_run\n"
        "from pytacheck import module_run as run\n"
        "import pytacheck.db\n"
    )
    assert found_in(tmp_path, {"core/run.py": source}) == set()


def test_layer_names_match_whole_components(tmp_path: Path) -> None:
    files = {
        "_values_extra.py": "from pytacheck.config import options\n",
        "core/facets.py": "import pytacheck.textual\nfrom pytacheck._values_extra import x\n",
        "_json.py": "from pytacheck._values_extra import x\n",
        "core_helpers.py": "from pytacheck.text.search import text_search\n",
    }
    assert found_in(tmp_path, files) == {("lower", "_json.py", f"{PKG}._values_extra")}


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


def test_foundation_modules_import_only_each_other(tmp_path: Path) -> None:
    found = found_in(
        tmp_path,
        {
            "_r/base.py": "from pytacheck._r.regex import is_na\n",
            "_values.py": "from pytacheck.config import options\n",
            "papers/schema.py": "def f():\n    from pytacheck.text import search\n",
            "_json.py": "from pytacheck import log\n",
            "_r/base2.py": "import pytacheck._r.regex\nx = pytacheck.text_search(1)\n",
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
