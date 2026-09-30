#!/usr/bin/env python3
"""Rename the import package ``pytacheck`` to ``metacheck`` (RENAME-1).

The script is re-runnable: it moves ``src/pytacheck`` to ``src/metacheck`` (or
only the files a newer main added under the old folder), then rewrites every
occurrence of ``pytacheck`` that a rule says to rewrite. Every other occurrence
must be kept by a rule; an occurrence no rule covers is an error, so a new
``pytacheck`` string is forced into a decision (``scripts/rename_keep.toml``).

    python scripts/rename_to_metacheck.py --check   # report; exit 1 if anything is pending
    python scripts/rename_to_metacheck.py --apply   # move and rewrite; a second run is a no-op

Run it with the project's Python (``uv run --frozen python ...``, or the ``.venv``
of a synced checkout): ``--apply`` sorts the imports with ruff afterwards, and stops
before it changes anything if ruff is not there.

``--rules FILE`` uses another rules file: a branch from before the rename uses
``scripts/rename_keep.pre.toml``, the rules the rename commit was made with.

A branch from before the rename is converted, not rebased onto the renamed main:
convert its merge base and its tip the same way, then carry over only the
difference between the two (a plain rebase lets git's rename detection mix the
branch with the rename)::

    PY="$PWD/.venv/bin/python"   # in a synced checkout of main: a Python with ruff
    git worktree add --detach ../conv-base "$(git merge-base origin/main <branch>)"
    git worktree add --detach ../conv-tip <branch>
    # in each of the two worktrees:
    git checkout origin/main -- scripts/rename_to_metacheck.py scripts/rename_keep.pre.toml
    git commit -m "take the rename script"        # --apply needs a clean tree
    "$PY" scripts/rename_to_metacheck.py --apply --rules scripts/rename_keep.pre.toml
    git add -u && git commit -m "apply the rename (generated)"
    # then, with B and T the new HEADs of conv-base and conv-tip:
    c=$(git commit-tree "T^{tree}" -p B -m "<the branch's change>")
    git switch -c <branch>-renamed origin/main && git cherry-pick "$c"

Occurrences the branch adds show up as UNCLASSIFIED in conv-tip: add rules for
them to its ``rename_keep.pre.toml``, and move them to ``rename_keep.toml``
before the branch merges. Changes to an ``_EXPORTS`` map belong in
``src/metacheck``; run ``scripts/sync_exports.py`` and ``scripts/alias_stubs.py``
afterwards.

Exit status: 0 nothing pending, 1 pending work, unclassified occurrences or
keep-count drift, 2 usage or environment errors.
"""

from __future__ import annotations

import argparse
import ast
import collections
import fnmatch
import io
import json
import re
import shutil
import subprocess
import sys
import tokenize
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

OLD, NEW = "pytacheck", "metacheck"
ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = "scripts/rename_keep.toml"
#: the rules the rename commit was generated with, for branches from before the rename
PRE_RULES_FILE = "scripts/rename_keep.pre.toml"
OLD_DIR, NEW_DIR = f"src/{OLD}", f"src/{NEW}"
#: what src/pytacheck holds once the move is done (the alias package; its generated
#: type stubs are the other *.pyi files, see is_alias)
ALIAS_FILES = frozenset({"__init__.py", "__init__.pyi", "__main__.py", "py.typed"})
#: a file that exists only while the real package is still in src/pytacheck
PACKAGE_MARKER = "module.py"

# ---------------------------------------------------------------------------
# Tiers: which rules apply to a file (first matching glob wins; `*` matches `/`)
# ---------------------------------------------------------------------------

#: never rewritten; every occurrence is kept
EXCLUDED = (
    "upstream/*",
    "docs/design/*",
    "CHANGELOG.md",  # history; the Unreleased entry is written by hand
    "install.sh",
    "install.ps1",  # pinned installers (REF bump is its own PR)
    "deploy/*",  # deployment files, renamed with DOCKER-1
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".github/workflows/docker.yml",  # DOCKER-1
    "parity/golden/*",
    "parity/lock/*",
    "parity/accuracy/golden/*",
    "parity/r/*",  # R runner: id seed, cache folder, image name
    "tests/modsys/fixtures/store/*",  # pinned tree_sha256; the old-form pack
    "rt/*",
    f"{OLD_DIR}/*",  # the alias package (only after the move)
    f"{NEW_DIR}/_alias.py",
    "tests/*test_pytacheck_*.py",  # tests of the old names, written by hand
    f"{NEW_DIR}/resources/status/validation.json",  # its bytes are the registry digest
    f"{NEW_DIR}/resources/templates/*",  # RENAME-4
    f"{NEW_DIR}/report/templates/*",  # RENAME-4
    "scripts/rename_to_metacheck.py",
    RULES_FILE,
    PRE_RULES_FILE,
    "scripts/alias_stubs.py",  # writes the alias stubs, which name both packages
    "tests/scripts/test_rename_guard.py",
)
#: data and docs whose module paths are read by tools: paths, module paths, imports
DOTTED = (
    "parity/cases/*.yaml",
    "porting/*",
    "docs/PORTING.md",
    ".github/prompts/upstream-sync.md",
    "pyproject.toml",
)
#: Python source: tokenized, names classified by the AST
CODE = ("*.py", "*.pyi")
# everything else: only src/pytacheck paths are rewritten (docs, CI, shell, R, ...)

#: exact whole-line edits, applied before classification (idempotent: NEW never equals OLD)
TRANSFORMS: dict[str, tuple[tuple[str, str], ...]] = {
    "pyproject.toml": (
        (
            'include = ["/src/pytacheck", ',
            'include = ["/src/metacheck", "/src/pytacheck", ',
        ),
        ('packages = ["src/pytacheck"]', 'packages = ["src/metacheck", "src/pytacheck"]'),
        ('known-first-party = ["pytacheck", ', 'known-first-party = ["metacheck", "pytacheck", '),
        ('source = ["pytacheck"]', 'source = ["metacheck", "pytacheck"]'),
        ('packages = ["pytacheck"]', 'packages = ["metacheck", "pytacheck"]'),
    ),
}

# ---------------------------------------------------------------------------
# Generic rules
# ---------------------------------------------------------------------------

OCC = re.compile(OLD, re.IGNORECASE)
#: kept anywhere: upper case (environment variables, constants)
K_UPPER = re.compile(r"PYTACHECK")
#: kept anywhere: the repository and the store repository (URLs and bare slugs)
K_REPO = re.compile(
    r"scienceverse/pytacheck(?:-modules)?\b|pytacheck-modules\b"
    r"|(?:github|githubusercontent)\.com/[\w./-]*pytacheck"
)
#: kept anywhere: compounds (prefixes, markers, file and folder names, dunders)
#: (an escaped \n, \t or \r in a string's source text is not a word character)
K_COMPOUND = re.compile(r"(?<=[\w-])(?<!\\[ntr])pytacheck|pytacheck(?=[-_])", re.IGNORECASE)
#: kept anywhere: file names (pytacheck.json, pytacheck.log.jsonl, workflows/pytacheck.yml)
K_FILE = re.compile(r"pytacheck\.(?:json|jsonl|log\.jsonl|yml|yaml|toml)\b")
#: kept anywhere: format ids, versioned (pytacheck.run/1, pytacheck.module/1) or assigned
#: to a *_FORMAT or *SCHEMA name or a "format" key (pytacheck.repo_info_cache); RENAME-3
K_FORMAT = re.compile(
    r"pytacheck(?:\.\w+)+/\d"
    r"""|(?:_FORMAT|SCHEMA)\s*=\s*b?["']pytacheck\.\w+"""
    r"""|["']format["']\s*:\s*b?["']pytacheck\.\w+"""
)
#: kept anywhere: platformdirs folder names (RENAME-3)
K_DIRS = re.compile(r"""user_\w+_dir\(\s*(["'])pytacheck\1""")
#: kept anywhere: logger names (RENAME-3)
K_LOGGER = re.compile(
    r"""(?:getLogger\(\s*|logger\s*=\s*|at_level\([^"'()]*,\s*)(["'])pytacheck(?:\.\w+)*\1"""
)
#: kept anywhere: the entry-point groups (read as they are, never twinned)
K_GROUP = re.compile(r"""group\s*=\s*(["'])pytacheck\.(?:modules|packs)\1""")
#: rewritten: src/pytacheck as a path, and ROOT / "src" / "pytacheck"
R_PATH = re.compile(r"(?<![\w.-])src([/\\]+)pytacheck(?![\w-])")
R_SPLIT = re.compile(r"""(["'])src\1\s*[/,]\s*(["'])(?P<w>pytacheck)\2""")
#: rewritten: a path inside the package without src/ (pytacheck/resources/data/x.json.gz);
#: the segment must be followed by / or .py, so a store reference (pytacheck/report) is not one
R_PKGPATH = re.compile(r"(?<![\w./\\-])pytacheck/(?P<seg>[A-Za-z_]\w*)(?=/|\.pyi?\b)")
#: the start of a word: nothing word-like before it, or an escaped \n, \t or \r (not \\n)
_WORD_START = r"(?:(?<![\w./\\-])|(?<=(?<!\\)\\[ntr]))"
#: rewritten if the tail resolves in the package: pytacheck.io.read, pytacheck.cli:main
R_DOTTED = re.compile(_WORD_START + r"pytacheck((?:\.[A-Za-z_]\w*)+)(?![\w/-])")
#: unclassified: names of a subpackage that are also a logger or an entry-point group
R_AMBIGUOUS = re.compile(r"""(["'])(?P<w>pytacheck\.(?:api|packs|modules))\1""")
#: rewritten: the bare package name where code imports it
R_IMPORT_CALL = re.compile(
    r"""(?:import_module|__import__|find_spec|importorskip)\(\s*f?\\?(["'])pytacheck\\?\1"""
    r"""|walk_packages\(.*?(["'])pytacheck\.\2"""
)
#: an import statement in text: ``from pytacheck``, or ``import`` and a list of modules
#: (``import sys, pytacheck as pc``: each module of the list counts)
R_IMPORT_STMT = re.compile(
    r"(?<![\w.])(?:from\s+(?P<w>pytacheck)(?![\w-])"
    r"|import\s+(?P<list>[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*))"
)
_IMPORT_ITEM = re.compile(r"(?P<m>[\w.]+)(?:\s+as\s+\w+)?")
#: what may come before an import statement written in text: code, not a sentence
_STMT_START = re.compile(r"""(?:^|["'`(;]|\\n|>>>|\.\.\.)\s*$""")


def import_stmt_hit(line: str, col: int) -> bool:
    """Whether the occurrence at *col* is a module of an import statement in text."""
    for m in R_IMPORT_STMT.finditer(line):
        if not _STMT_START.search(line[: m.start()]):
            continue
        if m.group("w") is not None:
            if m.start("w") == col:
                return True
            continue
        for item in _IMPORT_ITEM.finditer(line, m.start("list"), m.end("list")):
            if (
                item.start("m") == col
                and item.group("m") == OLD
                and line[item.end("m") :][:1] != "-"
            ):
                return True
    return False


#: kept: the word on its own in prose (comments, docstrings, messages)
PROSE = re.compile(_WORD_START + r"pytacheck(?![\w/\\-]|\.[A-Za-z_])", re.IGNORECASE)


@dataclass
class Occ:
    path: str
    line: int  # 1-based
    col: int  # 0-based, characters
    verdict: str  # rewrite | keep | unclassified
    rule: str
    text: str = ""


@dataclass
class Rule:
    verb: str
    files: str
    match: re.Pattern[str]
    count: int | None
    why: str
    hits: int = 0
    #: ``path:line:col`` of each occurrence the entry covered (shown on drift)
    where: list[str] = field(default_factory=list)


@dataclass
class Report:
    occs: list[Occ] = field(default_factory=list)
    changed: dict[str, str] = field(default_factory=dict)
    moved: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def glob_match(path: str, patterns: tuple[str, ...] | str) -> bool:
    pats = (patterns,) if isinstance(patterns, str) else patterns
    return any(fnmatch.fnmatchcase(path, p) for p in pats)


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout


# ---------------------------------------------------------------------------
# The move
# ---------------------------------------------------------------------------


class MoveError(Exception):
    """The move cannot be done safely; nothing has been moved."""


def tracked(prefix: str) -> list[str]:
    return [p for p in git("ls-files", "-z", "--", prefix).split("\0") if p]


def untracked(prefix: str) -> list[str]:
    """Files under *prefix* that are neither tracked nor ignored."""
    out = git("ls-files", "-z", "--others", "--exclude-standard", "--", prefix)
    return [p for p in out.split("\0") if p]


def is_alias(rel: str) -> bool:
    """Whether *rel* (a path relative to src/pytacheck) belongs to the alias package."""
    return rel in ALIAS_FILES or rel.endswith(".pyi")


def _rel_old(p: str) -> str:
    return p[len(OLD_DIR) + 1 :]


def move_state() -> str:
    """``pending`` (package still in the old folder), ``partial`` (new files there) or ``done``."""
    if not (ROOT / NEW_DIR / PACKAGE_MARKER).exists():
        return "pending" if (ROOT / OLD_DIR / PACKAGE_MARKER).exists() else "missing"
    extra = [p for p in tracked(OLD_DIR + "/") if not is_alias(_rel_old(p))]
    return "partial" if extra else "done"


def _only_bytecode(folder: Path) -> list[Path]:
    """The files in *folder* that are not bytecode (``__pycache__`` or ``*.pyc``)."""
    return [
        p
        for p in folder.rglob("*")
        if p.is_file() and "__pycache__" not in p.relative_to(folder).parts and p.suffix != ".pyc"
    ]


def plan_move(state: str) -> list[tuple[str, str]]:
    """Every ``git mv`` the move needs, checked before anything is moved (MoveError)."""
    problems: list[str] = []
    stray = [p for p in untracked(OLD_DIR + "/") if not is_alias(_rel_old(p))]
    if stray:
        problems.append(f"untracked files in {OLD_DIR} would be left behind: {stray[:5]}")
    moves: list[tuple[str, str]] = []
    if state == "pending":
        if not tracked(OLD_DIR + "/"):
            problems.append(f"{OLD_DIR} holds no tracked files")
        new = ROOT / NEW_DIR
        if new.exists() and (left := _only_bytecode(new)):
            problems.append(f"{NEW_DIR} exists and holds files: {left[:5]}")
        moves.append((OLD_DIR, NEW_DIR))
    elif state == "partial":  # a rebased branch: newer main added files in the old folder
        for p in tracked(OLD_DIR + "/"):
            rel = _rel_old(p)
            if is_alias(rel):
                continue
            dest = f"{NEW_DIR}/{rel}"
            if (ROOT / dest).exists() or (ROOT / dest).is_symlink():
                problems.append(f"{p} and {dest} both exist; merge them by hand")
            moves.append((p, dest))
    for src, _ in moves:
        if Path(src).is_absolute() or ".." in Path(src).parts:
            problems.append(f"{src} is not a path inside the work tree")
    if problems:
        raise MoveError("; ".join(problems))
    return moves


def do_move(report: Report, moves: list[tuple[str, str]]) -> None:
    """Carry out a move planned by :func:`plan_move`."""
    for src, dest in moves:
        if src == OLD_DIR:
            new = ROOT / NEW_DIR
            if new.exists():  # bytecode left from checking out a renamed commit
                shutil.rmtree(new)
                report.problems.append(f"removed {NEW_DIR}, which held only bytecode")
        else:
            (ROOT / dest).parent.mkdir(parents=True, exist_ok=True)
        git("mv", src, dest)
        report.moved.append((src, dest))
    # leftover bytecode would make stale subpackages importable under the old name
    old = ROOT / OLD_DIR
    if old.exists():
        caches = sorted(old.rglob("__pycache__"), reverse=True)
        for d in caches:
            shutil.rmtree(d)
        if caches:
            report.problems.append(f"removed {len(caches)} bytecode folder(s) under {OLD_DIR}")
        for d in sorted((p for p in old.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        left = [
            p.relative_to(ROOT).as_posix()
            for p in old.rglob("*")
            if p.is_file() and not is_alias(p.relative_to(old).as_posix())
        ]
        if left:
            report.problems.append(f"ignored files left in {OLD_DIR}: {left[:5]}")


# ---------------------------------------------------------------------------
# Module index (static, no imports)
# ---------------------------------------------------------------------------


def _module_name(rel: str) -> str:
    """``io/read.py`` -> ``io.read``; ``io/__init__.py`` -> ``io``; ``__init__.py`` -> ``""``."""
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


class Index:
    """Module names and top-level bindings of the package on disk."""

    def __init__(self, files: dict[str, Path]) -> None:
        self.bindings: dict[str, set[str]] = {}
        for name, f in sorted(files.items()):
            self.bindings[name] = self._names(f)
        for name in list(self.bindings):  # a subpackage is a binding of its parent
            if "." in name:
                parent, _, leaf = name.rpartition(".")
                self.bindings.setdefault(parent, set()).add(leaf)
            elif name:
                self.bindings.setdefault("", set()).add(name)

    @classmethod
    def of_state(cls, state: str) -> Index:
        """The package as it is after the move: in ``partial``, the new files still in the
        old folder are indexed under their new names too."""
        base = ROOT / (OLD_DIR if state == "pending" else NEW_DIR)
        files: dict[str, Path] = {}
        for f in sorted(base.rglob("*.py")):
            if "__pycache__" not in f.parts:
                files[_module_name(f.relative_to(base).as_posix())] = f
        if state == "partial":
            for p in tracked(OLD_DIR + "/"):
                rel = _rel_old(p)
                if p.endswith(".py") and not is_alias(rel):
                    files.setdefault(_module_name(rel), ROOT / p)
        return cls(files)

    @staticmethod
    def _names(f: Path) -> set[str]:
        try:
            tree = ast.parse(f.read_bytes())
        except (SyntaxError, ValueError):
            return set()
        out: set[str] = set()

        def visit(body: list[ast.stmt]) -> None:
            for node in body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    out.add(node.name)
                elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        for n in ast.walk(t):
                            if isinstance(n, ast.Name):
                                out.add(n.id)
                    value = node.value
                    named = any(isinstance(t, ast.Name) and "EXPORT" in t.id for t in targets)
                    if isinstance(value, ast.Dict):  # lazy export maps: name -> module
                        for k, v in zip(value.keys, value.values, strict=True):
                            if (
                                isinstance(k, ast.Constant)
                                and isinstance(k.value, str)
                                and isinstance(v, ast.Constant)
                                and isinstance(v.value, str)
                                and (named or re.match(rf"({OLD}|{NEW})\.", v.value))
                            ):
                                out.add(k.value)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        out.add(a.asname or a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        out.add(a.asname or a.name)
                elif isinstance(node, ast.If | ast.Try | ast.With):
                    visit(node.body)
                    for h in getattr(node, "handlers", []):
                        visit(h.body)
                    visit(getattr(node, "orelse", []))
                    visit(getattr(node, "finalbody", []))

        visit(tree.body)
        return out

    def resolves(self, tail: str) -> bool:
        """Whether ``pytacheck<tail>`` (tail like ``.io.read``) names a module or a binding."""
        parts = tail.lstrip(".").split(".")
        for i in range(len(parts), -1, -1):
            mod = ".".join(parts[:i])
            if mod in self.bindings:
                rest = parts[i:]
                if not rest:
                    return True
                first = rest[0]
                return first in self.bindings[mod] or (
                    first.startswith("__") and first.endswith("__")
                )
        return False


# ---------------------------------------------------------------------------
# Rules file
# ---------------------------------------------------------------------------


class RulesError(Exception):
    """The rules file cannot be read (a usage error)."""


def load_rules(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RulesError(f"{path}: {exc}") from None
    rules = []
    for n, entry in enumerate(data.get("rule", []), 1):
        where = f"{path}: rule {n}"
        if not isinstance(entry, dict) or entry.get("action") not in ("keep", "rewrite"):
            raise RulesError(f"{where}: action must be keep or rewrite: {entry}")
        count = entry.get("count")
        if count is not None and (not isinstance(count, int) or isinstance(count, bool)):
            raise RulesError(f"{where}: count must be a whole number: {count!r}")
        try:
            files, match = entry["files"], entry["match"]
            if not isinstance(files, str) or not isinstance(match, str):
                raise RulesError(f"{where}: files and match must be strings")
            rx = re.compile(match)
        except KeyError as exc:
            raise RulesError(f"{where}: missing {exc.args[0]!r}") from None
        except re.error as exc:
            raise RulesError(f"{where}: bad regex {match!r}: {exc}") from None
        rules.append(Rule(entry["action"], files, rx, count, str(entry.get("why", ""))))
    return rules


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def span_hit(rx: re.Pattern[str], line: str, col: int, group: int | str = 0) -> bool:
    for m in rx.finditer(line):
        s, e = m.span(group)
        if s <= col < e:
            return True
    return False


def code_contexts(text: str) -> tuple[dict[tuple[int, int], str], set[int]] | None:
    """Map (line, char col) of each NAME ``pytacheck`` to its AST role, and the
    rows that hold string or comment tokens with the occurrence. None if unparsable."""
    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, ValueError, tokenize.TokenError, IndentationError):
        return None
    lines = io.StringIO(text).readlines()  # breaks at \n only, as tokenize and ast count

    def char_col(lineno: int, byte_col: int) -> int:
        raw = lines[lineno - 1].encode("utf-8")
        return len(raw[:byte_col].decode("utf-8", "replace"))

    roles: dict[tuple[int, int], str] = {}
    binds = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == OLD or a.name.startswith(OLD + "."):
                    roles[(a.lineno, char_col(a.lineno, a.col_offset))] = "import"
                    binds = binds or a.asname is None
        elif isinstance(node, ast.Name) and node.id == OLD:
            role = "name-load" if isinstance(node.ctx, ast.Load) else "name-store"
            roles[(node.lineno, char_col(node.lineno, node.col_offset))] = role
        elif isinstance(node, ast.keyword) and node.arg == OLD:
            roles[(node.lineno, char_col(node.lineno, node.col_offset))] = "kwarg"
        elif isinstance(node, ast.arg) and node.arg == OLD:
            roles[(node.lineno, char_col(node.lineno, node.col_offset))] = "param"
        elif isinstance(node, ast.Attribute) and node.attr == OLD and node.end_lineno:
            end = char_col(node.end_lineno, node.end_col_offset or 0)
            roles[(node.end_lineno, end - len(OLD))] = "attr"
    # from pytacheck... import: the NAME right after `from`
    prev = None
    for tok in tokens:
        if (
            tok.type == tokenize.NAME
            and tok.string == OLD
            and prev is not None
            and prev.type == tokenize.NAME
            and prev.string == "from"
        ):
            roles[tok.start] = "import"
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok
    name_rows = {pos for pos, r in roles.items()}
    for pos, role in list(roles.items()):
        if role == "name-load":
            roles[pos] = "name" if binds else "name-unbound"
    kinds: dict[tuple[int, int], str] = {}
    for tok in tokens:
        if tok.type == tokenize.NAME:
            if tok.string == OLD:
                kinds[tok.start] = roles.get(tok.start, "name-other")
            continue
        if tok.type in (tokenize.STRING, tokenize.COMMENT) or tok.type == getattr(
            tokenize, "FSTRING_MIDDLE", -1
        ):
            kind = "comment" if tok.type == tokenize.COMMENT else "string"
            srow, scol = tok.start
            body = tok.string
            bare = kind == "string" and re.fullmatch(r"[a-zA-Z]*(['\"]{1,3})pytacheck\1", body)
            spaced = kind == "comment" or bool(re.search(r"\s", body))
            tag = "bare" if bare else (f"{kind}-spaced" if spaced else f"{kind}-tight")
            for m in OCC.finditer(body):
                before = body[: m.start()]
                row = srow + before.count("\n")
                col = (scol + len(before)) if row == srow else len(before) - before.rfind("\n") - 1
                kinds[(row, col)] = tag
    del name_rows
    return kinds, set()


def classify_text(
    path: str,
    text: str,
    tier: str,
    rules: list[Rule],
    index: Index,
    report: Report,
) -> str:
    """Classify every occurrence in *text*; return the rewritten text."""
    lines = io.StringIO(text).readlines()  # breaks at \n only, as tokenize and ast count
    kinds: dict[tuple[int, int], str] | None = None
    if tier == "code":
        ctx = code_contexts(text)
        if ctx is None:
            report.problems.append(f"{path}: does not parse; text rules used")
            tier = "dotted"
        else:
            kinds = ctx[0]
    out_lines = []
    for n, line in enumerate(lines, 1):
        edits: list[int] = []
        for m in OCC.finditer(line):
            col = m.start()
            verdict, rule = decide(path, line, col, tier, rules, index, kinds, n)
            report.occs.append(Occ(path, n, col, verdict, rule, line.strip()[:160]))
            if verdict == "rewrite":
                edits.append(col)
        if edits:
            chars = list(line)
            for col in edits:
                word = line[col : col + len(OLD)]
                repl = (
                    NEW.upper()
                    if word.isupper()
                    else (NEW.capitalize() if word[0].isupper() else NEW)
                )
                chars[col : col + len(OLD)] = list(repl)
            line = "".join(chars)
        out_lines.append(line)
    return "".join(out_lines)


def decide(
    path: str,
    line: str,
    col: int,
    tier: str,
    rules: list[Rule],
    index: Index,
    kinds: dict[tuple[int, int], str] | None,
    lineno: int,
) -> tuple[str, str]:
    if tier == "excluded":
        return "keep", "excluded-path"
    # the rules see the line without its terminator, so `$` also matches in a CRLF checkout
    line = line.rstrip("\r\n")
    for r in rules:
        if glob_match(path, r.files) and span_hit(r.match, line, col):
            r.hits += 1
            r.where.append(f"{path}:{lineno}:{col + 1}")
            return r.verb, f"{r.verb}-file"
    if line[col : col + len(OLD)] == "PYTACHECK" or span_hit(K_UPPER, line, col):
        return "keep", "upper"
    if span_hit(K_REPO, line, col):
        return "keep", "repo"
    if span_hit(K_COMPOUND, line, col):
        return "keep", "compound"
    for rx, rule in (
        (K_FILE, "file-name"),
        (K_FORMAT, "format-id"),
        (K_DIRS, "platformdirs"),
        (K_LOGGER, "logger"),
        (K_GROUP, "group"),
    ):
        if span_hit(rx, line, col):
            return "keep", rule
    if span_hit(R_PATH, line, col) or span_hit(R_SPLIT, line, col, "w"):
        return "rewrite", "path"
    for m in R_PKGPATH.finditer(line):
        if m.start() == col and m.group("seg") in index.bindings.get("", set()) | {"__init__"}:
            return "rewrite", "path"
    if tier == "paths":
        return "keep", "deferred"
    kind = kinds.get((lineno, col)) if kinds is not None else None
    if tier == "code" and kind is not None and not kind.startswith(("string", "comment", "bare")):
        if kind in ("import", "name"):
            return "rewrite", f"code-{kind}"
        return "unclassified", f"code-{kind}"
    # a subpackage name that is also a logger or an entry-point group: a rule decides
    if (
        not (kind or "").startswith("comment")
        and span_hit(R_AMBIGUOUS, line, col, "w")
        and not span_hit(R_IMPORT_CALL, line, col)
    ):
        return "unclassified", "ambiguous-name"
    # strings, comments and text files
    for m in R_DOTTED.finditer(line):
        if m.start() == col:
            if index.resolves(m.group(1)):
                return "rewrite", "dotted"
            return "unclassified", "dotted-unresolved"
    if span_hit(R_IMPORT_CALL, line, col) or import_stmt_hit(line, col):
        return "rewrite", "import-text"
    if kind == "bare":
        return "unclassified", "bare-string"
    if span_hit(PROSE, line, col) and kind != "string-tight":
        return "keep", "prose"
    return "unclassified", f"text-{kind or tier}"


def tier_of(path: str) -> str:
    if glob_match(path, EXCLUDED):
        return "excluded"
    if glob_match(path, DOTTED):
        return "dotted"
    if glob_match(path, CODE):
        return "code"
    return "paths"


def _ruff_missing() -> str | None:
    """Why ruff cannot run under this Python, if it cannot; asked before anything changes."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"], cwd=ROOT, capture_output=True, check=False
        )
    except OSError as exc:
        return f"ruff could not run: {exc}"
    if done.returncode != 0:
        return f"ruff is not installed for {sys.executable}"
    return None


def _ruff(files: list[str]) -> str | None:
    """Sort imports and format *files*; the reason if ruff is missing or fails."""
    base = [sys.executable, "-m", "ruff"]
    for args in (["check", "--select", "I", "--fix", "-q"], ["format", "-q"]):
        try:
            done = subprocess.run([*base, *args, *files], cwd=ROOT, check=False)
        except OSError as exc:
            return f"ruff could not run: {exc}"
        if done.returncode != 0:
            return f"ruff {args[0]} exited with status {done.returncode}"
    return None


def run(ns: argparse.Namespace) -> int:
    apply: bool = ns.apply
    verbose: bool = ns.verbose
    strict: bool = ns.strict
    rules_path = Path(ns.rules).resolve() if ns.rules else ROOT / RULES_FILE
    if ns.rules and not rules_path.is_file():
        print(f"error: no rules file {ns.rules}", file=sys.stderr)
        return 2
    try:
        rules_name = rules_path.relative_to(ROOT).as_posix()
    except ValueError:
        rules_name = str(rules_path)
    report = Report()
    state = move_state()
    if state == "missing":
        print(f"error: neither {OLD_DIR}/{PACKAGE_MARKER} nor {NEW_DIR}/{PACKAGE_MARKER} exists")
        return 2
    if apply:
        if sys.version_info < (3, 12):
            print("error: --apply needs Python 3.12 or newer", file=sys.stderr)
            return 2
        # untracked files too: their old content is in no git object, so it cannot be restored
        # (every untracked file, whatever status.showUntrackedFiles says)
        if git("status", "--porcelain", "--untracked-files=all").strip():
            print(
                "error: the work tree is not clean (commit or remove every change and "
                "untracked file first)",
                file=sys.stderr,
            )
            return 2
    index = Index.of_state(state)
    try:
        rules = load_rules(rules_path)
    except RulesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def logical(p: str) -> str:
        """Where *p* lives after the move (files are classified as if moved)."""
        if p.startswith(OLD_DIR + "/"):
            rel = _rel_old(p)
            if state == "pending" or not is_alias(rel):
                return f"{NEW_DIR}/{rel}"
        return p

    # -z: paths verbatim (git quotes non-ASCII paths otherwise)
    files = [
        p
        for p in git("grep", "--untracked", "-z", "-l", "-I", "-i", OLD, "--", ".").split("\0")
        if p
    ]
    for p in sorted(set(files) | set(TRANSFORMS)):
        full = ROOT / p
        if not full.is_file():
            continue
        raw = full.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.occs.append(Occ(p, 0, 0, "keep", "binary"))
            continue
        lp = logical(p)
        new = text
        for old_pre, new_pre in TRANSFORMS.get(lp, ()):
            new = "\n".join(
                new_pre + ln[len(old_pre) :] if ln.startswith(old_pre) else ln
                for ln in new.split("\n")
            )
        new = classify_text(lp, new, tier_of(lp), rules, index, report)
        if new != text:
            report.changed[lp] = new
    for p in git("grep", "--untracked", "-z", "-l", "-i", "-a", OLD, "--", ".").split("\0"):
        if p and p not in files:
            report.occs.append(Occ(logical(p), 0, 0, "keep", "binary"))

    # a rewrite entry's count holds before it is applied; afterwards it covers nothing
    drifted = [
        r
        for r in rules
        if r.count is not None and r.count != r.hits and not (r.verb == "rewrite" and r.hits == 0)
    ]
    drift = [f"{r.files} {r.match.pattern!r}: expected {r.count}, found {r.hits}" for r in drifted]
    # a keep entry that matches nothing is stale (an error with --strict); a rewrite
    # entry matches nothing once it has been applied, so that is only reported
    unused = [
        f"{r.verb} {r.files} {r.match.pattern!r}" for r in rules if r.hits == 0 and r.verb == "keep"
    ]
    spent = [
        f"{r.verb} {r.files} {r.match.pattern!r}" for r in rules if r.hits == 0 and r.verb != "keep"
    ]
    unclassified = [o for o in report.occs if o.verdict == "unclassified"]
    rewrites = [o for o in report.occs if o.verdict == "rewrite"]
    moving = state in ("pending", "partial")
    blocked = bool(unclassified or drift or (strict and unused))

    ruff_error = None
    if apply and not blocked:
        missing = None if ns.no_ruff else _ruff_missing()
        if missing:
            print(
                f"error: {missing} (use the project's Python, or --no-ruff); "
                "nothing was moved or rewritten",
                file=sys.stderr,
            )
            return 2
        try:
            moves = plan_move(state)
        except MoveError as exc:
            print(f"error: {exc}; nothing was moved or rewritten", file=sys.stderr)
            return 2
        do_move(report, moves)
        for lp, text in report.changed.items():
            (ROOT / lp).write_bytes(text.encode("utf-8"))  # line endings stay as they were
        py = sorted(p for p in report.changed if p.endswith((".py", ".pyi")))
        if not ns.no_ruff and py:
            ruff_error = _ruff(py)

    if ns.dump:
        with open(ns.dump, "w", encoding="utf-8") as fh:
            for o in report.occs:
                fh.write(json.dumps(o.__dict__) + "\n")
    by_rule = collections.Counter((o.verdict, o.rule) for o in report.occs)
    print(f"move: {state}" + (f" ({len(report.moved)} paths moved)" if report.moved else ""))
    print(f"occurrences: {len(report.occs)} in {len({o.path for o in report.occs})} files")
    for (verdict, rule), n in sorted(by_rule.items()):
        print(f"  {verdict:12} {rule:22} {n}")
    done = "rewritten" if apply and not blocked else "to rewrite"
    print(f"files {done}: {len(report.changed)}")
    for msg in report.problems:
        print(f"note: {msg}")
    limit = None if verbose else 300
    for r, d in zip(drifted, drift, strict=True):
        print(f"DRIFT {d}")
        for w in r.where[:limit]:  # what the entry covers now, to find the new or lost one
            print(f"  covers {w}")
    for u in unused:
        print(f"{'UNUSED' if strict else 'unused'} {u}")
    if verbose:
        for u in spent:
            print(f"applied {u}")
    if not apply:
        for o in rewrites[:limit]:
            print(f"REWRITE {o.path}:{o.line}:{o.col + 1} [{o.rule}] {o.text}")
    for o in unclassified[:limit]:
        print(f"UNCLASSIFIED {o.path}:{o.line}:{o.col + 1} [{o.rule}] {o.text}")
    if unclassified:
        print(f"{len(unclassified)} unclassified occurrence(s): add a rule to {rules_name}")
    if apply and blocked:
        print("nothing was moved or rewritten", file=sys.stderr)
        return 1
    if ruff_error:
        print(f"error: {ruff_error} (the files were moved and rewritten)", file=sys.stderr)
        return 2
    if apply:
        return 0
    return 1 if (blocked or rewrites or moving) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--check", action="store_true", help="report only; exit 1 if anything is pending"
    )
    g.add_argument("--apply", action="store_true", help="move and rewrite")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--no-ruff", action="store_true", help="do not sort imports and format afterwards"
    )
    ap.add_argument(
        "--strict", action="store_true", help="a keep entry that matches nothing is an error"
    )
    ap.add_argument("--dump", metavar="FILE", help="write every occurrence as JSON lines")
    ap.add_argument("--rules", metavar="FILE", help=f"the rules file (default: {RULES_FILE})")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
