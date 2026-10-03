"""The structure of a data package: folder tree, depth, layout and empty folders.

The four checks behind ``datapackage::package_structure`` live here so that a
pack with its own rules (an institution's required layout, say) can call them
directly with its own options:

* :func:`check_folder_tree` (``folder_tree``): a readable snapshot of the folders;
* :func:`check_folder_depth` (``folder_depth``): folders nested too deeply;
* :func:`check_folder_layout` (``folder_layout``): the top-level folders follow a
  recognised layout, from a list of layout specs (see below);
* :func:`check_empty_folders` (``empty_folders``): folders without any file.

:func:`check_structure` runs all four and :func:`structure_report` writes the
report blocks. Each check returns its findings (see
:mod:`metacheck.datapackage._findings`) and one checklist row.

**Layout specs** are dicts (or lists of them, or paths to JSON files holding
them)::

    {
      "id": "data-code-docs",
      "title": "Data, code and documentation",
      "description": "...",
      "parts": [
        {"id": "data", "title": "Data", "match": ["^data$"], "required": true,
         "parts": [{"id": "raw", "title": "Raw data", "match": ["^raw( data)?$"]}]},
        {"id": "figure", "title": "Figure", "match": ["^fig(ure)? ?\\\\d+$"],
         "repeat": true, "min": 1, "example": "Fig 1"}
      ],
      "root_files": [{"id": "readme", "title": "README", "match": ["^read ?me"],
                      "required": true}]
    }

A *part* is a folder: ``match`` lists regular expressions tried (case
insensitive, anywhere in the name unless anchored) against the normalised folder
name (:func:`normalise_name`); ``required`` parts must be present (``min`` says
how many folders, one by default); ``repeat`` marks a part with one folder per
item (figures), and ``example`` is a name to show for it; ``parts`` are the
folders expected inside it (only checked when the folder itself is there). A
folder is given to the first part that matches it. ``root_files`` are files at
the top of the package, matched against the file name without its extension.

A layout *matches* when all its required parts are present and at least
``min_coverage`` of the top-level folders belong to some part.
"""

from __future__ import annotations

import os
import re
import stat
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from metacheck.datapackage._findings import (
    SEVERITIES,
    checklist_frame,
    findings_frame,
    status_from_findings,
    traffic_light,
)
from metacheck.datapackage._open import OpenedPackage

__all__ = [
    "DEFAULT_SEVERITY",
    "Check",
    "LayoutMatch",
    "LayoutResult",
    "Missing",
    "PartHit",
    "StructureResult",
    "check_empty_folders",
    "check_folder_depth",
    "check_folder_layout",
    "check_folder_tree",
    "check_structure",
    "default_layouts",
    "folder_tree",
    "load_layouts",
    "load_severity",
    "match_layouts",
    "normalise_name",
    "structure_report",
]

#: Default severity of each finding rule (override with the ``severity`` option).
DEFAULT_SEVERITY: dict[str, str] = {
    "no-files": "problem",
    "too-deep": "suggestion",
    "layout-not-recognised": "suggestion",
    "layout-part-missing": "suggestion",
    "layout-folder-unmatched": "suggestion",
    "layout-recognised": "info",
    "layout-extra-folder": "info",
    "flat-package": "info",
    "empty-folder": "suggestion",
}

#: Top-level folders that never count as part of the structure (junk made by tools).
_IGNORED_TOP = frozenset({"__macosx", "__pycache__"})

#: Option files bigger than this are refused.
_MAX_JSON_BYTES = 1024 * 1024

_LAYOUT_KEYS = {"id", "title", "description", "parts", "root_files"}
_PART_KEYS = {"id", "title", "match", "required", "repeat", "min", "example", "parts"}


# --------------------------------------------------------------------------------------
# options: a dict or list, or the path to a JSON file holding one
# --------------------------------------------------------------------------------------


def _read_json(path: str | os.PathLike[str]) -> Any:
    from metacheck._json import loads

    p = Path(path).expanduser()
    try:
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_JSON_BYTES:
            raise ValueError(f"{p.name} is not a JSON file of reasonable size")
        raw = p.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read {os.fspath(path)}: {exc.strerror or exc}") from exc
    try:
        return loads(raw)
    except ValueError as exc:
        raise ValueError(f"{p.name} is not valid JSON: {exc}") from exc


def default_layouts() -> list[dict[str, Any]]:
    """The layouts a package is compared with by default (``data/structure_layouts.json``)."""
    from metacheck._json import loads

    raw = resources.files("metacheck.datapackage").joinpath("data", "structure_layouts.json")
    return load_layouts(loads(raw.read_bytes()))


def load_layouts(layouts: Any = None) -> list[dict[str, Any]]:
    """Layout specs, checked and with every default filled in.

    *layouts* is ``None`` (the :func:`default_layouts`), one layout (a dict), a
    list of layouts, a path to a JSON file holding either, or a ``{"layouts":
    [...]}`` dict; list items may themselves be paths. Raises ``ValueError``
    naming the layout and part when a spec is wrong (an unknown key, a
    regular expression that does not compile, a repeated id).
    """
    if layouts is None:
        return default_layouts()
    found: list[dict[str, Any]] = []
    _collect(layouts, found)
    ids = [spec["id"] for spec in found]
    for i in ids:
        if ids.count(i) > 1:
            raise ValueError(f"Layout id {i!r} is used twice")
    return found


def _collect(value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, str | os.PathLike):
        _collect(_read_json(value), out)
    elif isinstance(value, Mapping):
        if "parts" not in value and "root_files" not in value and "layouts" in value:
            _collect(value["layouts"], out)
        else:
            out.append(_layout_spec(value))
    elif isinstance(value, Sequence):
        for item in value:
            _collect(item, out)
    else:
        raise ValueError(
            f"A layout is a dict, a list of dicts or a path to a JSON file, not {type(value).__name__}"
        )


def _layout_spec(raw: Mapping[str, Any]) -> dict[str, Any]:
    bad = set(raw) - _LAYOUT_KEYS
    if bad:
        raise ValueError(f"Layout {raw.get('id', '?')!r}: unknown key(s) {sorted(bad)}")
    lid = raw.get("id")
    if not isinstance(lid, str) or not lid:
        raise ValueError("Every layout needs an 'id'")
    spec: dict[str, Any] = {
        "id": lid,
        "title": str(raw.get("title") or lid),
        "description": str(raw.get("description") or ""),
        "parts": _parts(raw.get("parts") or [], lid, root=False),
        "root_files": _parts(raw.get("root_files") or [], lid, root=True),
    }
    return spec


def _parts(raw: Any, where: str, *, root: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError(f"{where}: 'parts' must be a list")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"{where}: every part is a dict, not {type(item).__name__}")
        pid = item.get("id")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"{where}: every part needs an 'id'")
        here = f"{where}/{pid}"
        bad = set(item) - _PART_KEYS
        if root:
            bad |= set(item) & {"parts", "repeat"}
        if bad:
            raise ValueError(f"{here}: unknown key(s) {sorted(bad)}")
        match = item.get("match")
        if isinstance(match, str):
            match = [match]
        if (
            not isinstance(match, Sequence)
            or not match
            or not all(isinstance(m, str) for m in match)
        ):
            raise ValueError(f"{here}: 'match' must be a list of regular expressions")
        for pattern in match:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{here}: bad regular expression {pattern!r}: {exc}") from exc
        required = bool(item.get("required", False))
        need = item.get("min")
        if need is None:
            need = 1 if required else 0
        if isinstance(need, bool) or not isinstance(need, int) or need < 0:
            raise ValueError(f"{here}: 'min' must be a whole number, 0 or more")
        required = required or need > 0
        part: dict[str, Any] = {
            "id": pid,
            "title": str(item.get("title") or pid),
            "match": list(match),
            "required": required,
            "min": max(need, 1) if required else 0,
            "example": str(item.get("example") or ""),
        }
        if not root:
            part["repeat"] = bool(item.get("repeat", False))
            part["parts"] = _parts(item.get("parts") or [], here, root=False)
        out.append(part)
    ids = [p["id"] for p in out]
    for i in ids:
        if ids.count(i) > 1:
            raise ValueError(f"{where}: part id {i!r} is used twice")
    return out


def load_severity(severity: Any = None) -> dict[str, str]:
    """The severity overrides as a dict: rule id to ``problem``, ``suggestion`` or ``info``.

    *severity* is ``None``, a dict or the path to a JSON file holding one. Rule
    ids that no check here uses are kept (a pack may share one dict between
    several checks); a bad severity raises ``ValueError``.
    """
    if severity is None:
        return {}
    if isinstance(severity, str | os.PathLike):
        severity = _read_json(severity)
    if not isinstance(severity, Mapping):
        raise ValueError(
            "severity must be a dict of rule id to severity (or a path to a JSON file)"
        )
    out = {str(k): str(v) for k, v in severity.items()}
    for rule, value in out.items():
        if value not in SEVERITIES:
            raise ValueError(f"Severity of {rule!r} must be one of {SEVERITIES}, not {value!r}")
    return out


def _sev(overrides: Mapping[str, str], rule: str) -> str:
    return overrides.get(rule, DEFAULT_SEVERITY[rule])


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------

_LEADING_NUMBER = re.compile(r"^\(?\d+[\s._\-)]+(?=\S)")
_HAS_LETTER = re.compile(r"[^\W\d_]")


def normalise_name(name: str) -> str:
    """A folder or file name as layouts are matched against it.

    Lower case; leading numbering (``1 ``, ``01_``, ``1.``, ``2-``, ``3)``)
    removed; ``_``, ``-`` and ``.`` become spaces, ``&`` becomes ``and``;
    runs of spaces collapse. ``3 Code & Script`` gives ``code and script``,
    ``Fig.1`` and ``fig_01`` give ``fig 1`` and ``fig 01``.
    """
    s = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    while (m := _LEADING_NUMBER.match(s)) and _HAS_LETTER.search(s[m.end() :]):
        s = s[m.end() :]
    s = re.sub(r"[_\-.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _stem(name: str) -> str:
    stem = name.lstrip(".")
    base, dot, ext = stem.rpartition(".")
    return base if dot and base and 0 < len(ext) <= 5 and ext.isalnum() else stem


def _size(n: int) -> str:
    """``1536`` -> ``"1.5 KB"``."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"  # pragma: no cover


def _count(n: int, noun: str) -> str:
    """``1 file``, ``2 files``, ``3 branches``."""
    plural = f"{noun}es" if noun.endswith(("ch", "sh", "s", "x")) else f"{noun}s"
    return f"{n:,} {noun if n == 1 else plural}"


def _listing(items: Sequence[str], limit: int = 4) -> str:
    """``a, b and c``; past *limit* items ``a, b, c, d and 3 more``."""
    items = list(items)
    if len(items) > limit:
        rest = len(items) - limit
        items = [*items[:limit], f"{rest} more"]
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _quoted(items: Iterable[str]) -> list[str]:
    return [f'"{i}"' for i in items]


def _finding(
    overrides: Mapping[str, str], check: str, rule: str, path: str, kind: str, detail: str
) -> dict[str, str]:
    """One finding; its severity is the rule's default unless *overrides* has one."""
    return _row(path, kind, check, rule, _sev(overrides, rule), detail)


def _row(path: str, kind: str, check: str, rule: str, severity: str, detail: str) -> dict[str, str]:
    return {
        "path": path,
        "kind": kind,
        "check": check,
        "rule": rule,
        "severity": severity,
        "detail": detail,
    }


@dataclass
class Check:
    """What one structure check found: its findings and its checklist row."""

    findings: list[dict[str, str]] = field(default_factory=list)
    item: dict[str, str] = field(default_factory=dict)
    count: int = 0  # how many things it found (branches too deep, empty folders)


def _checked(
    rows: list[dict[str, str]],
    check: str,
    title: str,
    detail: str,
    status: str | None = None,
    count: int | None = None,
) -> Check:
    if status is None:
        status = status_from_findings(findings_frame(rows), check)
    item = {"item": check, "title": title, "status": status, "detail": detail}
    return Check(rows, item, len(rows) if count is None else count)


def _in_base_files(pkg: OpenedPackage) -> list[tuple[str, int, bool]]:
    files = pkg.files()
    return [
        (str(rel), int(size), bool(link))
        for rel, size, link, in_base in zip(
            files["rel"].tolist(),
            files["size"].tolist(),
            files["link"].tolist(),
            files["in_base"].tolist(),
            strict=True,
        )
        if in_base
    ]


def _in_base_dirs(pkg: OpenedPackage) -> list[dict[str, Any]]:
    dirs = pkg.dirs()
    out = []
    for rel, depth, hidden, link, empty, in_base in zip(
        dirs["rel"].tolist(),
        dirs["depth"].tolist(),
        dirs["hidden"].tolist(),
        dirs["link"].tolist(),
        dirs["empty"].tolist(),
        dirs["in_base"].tolist(),
        strict=True,
    ):
        if in_base and rel != "":
            out.append(
                {
                    "rel": str(rel),
                    "depth": int(depth),
                    "hidden": bool(hidden),
                    "link": bool(link),
                    "empty": bool(empty),
                }
            )
    return out


# --------------------------------------------------------------------------------------
# 1. the folder tree
# --------------------------------------------------------------------------------------

#: Folder levels drawn at most, however deep the package is.
_MAX_LEVELS = 12
#: How much a too-long tree is shrunk: (items per folder, folder levels).
_SHRINK = (
    (15, _MAX_LEVELS),
    (8, _MAX_LEVELS),
    (4, _MAX_LEVELS),
    (2, _MAX_LEVELS),
    (2, 4),
    (2, 3),
    (2, 2),
    (1, 1),
    (0, 1),
)


@dataclass
class _Node:
    name: str = ""
    dirs: dict[str, _Node] = field(default_factory=dict)
    files: list[tuple[str, int, bool]] = field(default_factory=list)
    link: bool = False
    n_files: int = 0  # files in it and below
    size: int = 0  # bytes in it and below


def _build_tree(files: Iterable[tuple[str, int, bool]], dirs: Iterable[tuple[str, bool]]) -> _Node:
    root = _Node()

    def descend(rel: str, add_files: int = 0, add_size: int = 0) -> _Node:
        node = root
        node.n_files += add_files
        node.size += add_size
        for part in rel.split("/") if rel else []:
            if part not in node.dirs:
                node.dirs[part] = _Node(part)
            node = node.dirs[part]
            node.n_files += add_files
            node.size += add_size
        return node

    for rel, link in dirs:
        descend(rel).link = link
    for rel, size, link in files:
        parent, _, name = rel.rpartition("/")
        descend(parent, 1, size).files.append((name, size, link))
    return root


def _name_key(name: str) -> tuple[bool, list[int | str], str]:
    """Sort order of the tree: hidden names last, numbers in order (``f2`` before ``f10``)."""
    parts = re.split(r"(\d+)", name.casefold())
    return (name.startswith("."), [int(p) if i % 2 else p for i, p in enumerate(parts)], name)


def _ext_summary(files: Sequence[tuple[str, int, bool]]) -> str:
    counts: Counter[str] = Counter()
    for name, _, _ in files:
        stem = name.lstrip(".")
        counts[f".{stem.rsplit('.', 1)[1].lower()}" if "." in stem else "no extension"] += 1
    top = counts.most_common(4)
    parts = [f"{ext} ×{n}" for ext, n in top]
    other = sum(counts.values()) - sum(n for _, n in top)
    if other:
        parts.append(f"other ×{other}")
    return ", ".join(parts)


def _render(
    root: _Node, label: str | None, max_items: int, max_levels: int
) -> list[tuple[str, str]]:
    """The tree as (left, right) text pairs: the drawing and a note (count, size)."""
    lines: list[tuple[str, str]] = []
    if label is not None:
        lines.append((label, _folder_note(root)))

    def walk(node: _Node, prefix: str, level: int) -> None:
        dirs = sorted(node.dirs.values(), key=lambda n: _name_key(n.name))
        files = sorted(node.files, key=lambda f: _name_key(f[0]))
        entries: list[tuple[str, Any]] = [("dir", d) for d in dirs[:max_items]]
        if len(dirs) > max_items:
            entries.append(("more_dirs", dirs[max_items:]))
        entries += [("file", f) for f in files[:max_items]]
        if len(files) > max_items:
            entries.append(("more_files", files[max_items:]))
        for i, (kind, payload) in enumerate(entries):
            last = i == len(entries) - 1
            branch = prefix + ("└── " if last else "├── ")
            if kind == "dir":
                collapsed = level >= max_levels and bool(payload.dirs or payload.files)
                lines.append(
                    (f"{branch}{payload.name}/{' …' if collapsed else ''}", _folder_note(payload))
                )
                if not collapsed:
                    walk(payload, prefix + ("    " if last else "│   "), level + 1)
            elif kind == "more_dirs":
                n_files = sum(d.n_files for d in payload)
                size = sum(d.size for d in payload)
                lines.append(
                    (
                        f"{branch}… and {_count(len(payload), 'more folder')}",
                        f"{_count(n_files, 'file')}, {_size(size)}",
                    )
                )
            elif kind == "file":
                name, size, link = payload
                lines.append((f"{branch}{name}", f"{_size(size)}{' (link)' if link else ''}"))
            else:
                lines.append(
                    (
                        f"{branch}… and {len(payload)} more ({_ext_summary(payload)})",
                        _size(sum(f[1] for f in payload)),
                    )
                )

    walk(root, "", 1)
    return lines


def _folder_note(node: _Node) -> str:
    if node.link:
        return "link"
    if node.n_files == 0:
        return "empty"
    return f"{_count(node.n_files, 'file')}, {_size(node.size)}"


def _draw(lines: Sequence[tuple[str, str]]) -> list[str]:
    width = min(max((len(left) for left, _ in lines), default=0), 64)
    out = []
    for left, right in lines:
        out.append(f"{left.ljust(width)}  {right}".rstrip() if right else left)
    return out


def _shrunk(root: _Node, label: str | None, max_items: int, max_lines: int) -> list[str]:
    """The tree as lines, shrunk step by step until it fits *max_lines*."""
    schedule = [(max_items, _MAX_LEVELS), *(step for step in _SHRINK if step[0] < max_items)]
    lines: list[tuple[str, str]] = []
    for items, levels in schedule:
        lines = _render(root, label, items, levels)
        if len(lines) <= max_lines:
            break
    drawn = _draw(lines)
    if len(drawn) > max_lines:
        drawn = [*drawn[: max_lines - 1], f"… {len(drawn) - max_lines + 1} more lines not shown"]
    return drawn


def folder_tree(pkg: OpenedPackage, *, max_items: int = 15, max_lines: int = 400) -> str:
    """A text tree of the package: folders first, then files, with sizes.

    A folder with more than *max_items* files (or folders) shows the first
    ones and then ``… and 180 more (.csv ×170, .txt ×10)``. The whole tree is
    kept to *max_lines* lines, collapsing more and more of it when it is
    longer. Files outside the package's base folder (such as ``__MACOSX``
    next to an archive's wrapper folder) are listed after the tree.
    """
    inside = _build_tree(_in_base_files(pkg), [(d["rel"], d["link"]) for d in _in_base_dirs(pkg)])
    outside_files, outside_dirs = _outside(pkg)
    outside = _build_tree(outside_files, outside_dirs)
    has_outside = bool(outside.n_files or outside.dirs)
    budget = min(40, max(max_lines // 5, 3)) if has_outside else 0
    spare = budget + 2 if has_outside else 0  # the outside part, a blank line and its heading
    lines = _shrunk(inside, pkg.name + "/", max_items, max(max_lines - spare, 1))
    if has_outside:
        lines += [
            "",
            "Outside the package folder (next to it in the archive):",
            *_shrunk(outside, None, max_items, budget),
        ]
    return "\n".join(lines)


def _outside(pkg: OpenedPackage) -> tuple[list[tuple[str, int, bool]], list[tuple[str, bool]]]:
    files = pkg.files()
    out_files = [
        (str(p), int(s), bool(k))
        for p, s, k, ib in zip(
            files["path"].tolist(),
            files["size"].tolist(),
            files["link"].tolist(),
            files["in_base"].tolist(),
            strict=True,
        )
        if not ib
    ]
    dirs = pkg.dirs()
    out_dirs = [
        (str(p), bool(k))
        for p, k, ib in zip(
            dirs["path"].tolist(), dirs["link"].tolist(), dirs["in_base"].tolist(), strict=True
        )
        if not ib
    ]
    return out_files, out_dirs


def check_folder_tree(
    pkg: OpenedPackage,
    *,
    max_items: int = 15,
    max_lines: int = 400,
    severity: Any = None,
) -> tuple[Check, str]:
    """The ``folder_tree`` item: the tree text (see :func:`folder_tree`) and its check."""
    overrides = load_severity(severity)
    files = _in_base_files(pkg)
    outside_files, _ = _outside(pkg)
    dirs = _in_base_dirs(pkg)
    tree = folder_tree(pkg, max_items=max_items, max_lines=max_lines)
    title = "Folder structure snapshot"
    rows: list[dict[str, str]] = []
    if not files:
        rows.append(
            _finding(
                overrides, "folder_tree", "no-files", "", "package", "The package has no files."
            )
        )
        return _checked(rows, "folder_tree", title, "The package has no files."), tree
    total = sum(size for _, size, _ in files)
    detail = f"{_count(len(files), 'file')} in {_count(len(dirs), 'folder')} ({_size(total)})."
    if outside_files:
        detail += f" {_count(len(outside_files), 'more file')} outside the package folder."
    return _checked(rows, "folder_tree", title, detail, "pass"), tree


# --------------------------------------------------------------------------------------
# 2. folder depth
# --------------------------------------------------------------------------------------


def check_folder_depth(pkg: OpenedPackage, max_depth: int = 3, *, severity: Any = None) -> Check:
    """The ``folder_depth`` item: branches with more than *max_depth* folder levels.

    Levels are counted from the package's base (``4 Data/Raw data/x.csv`` is two
    levels). Each branch that is too deep is reported once, at its deepest
    folder. Hidden folders and files outside the base are ignored.
    """
    overrides = load_severity(severity)
    levels = f"{max_depth} folder level{'' if max_depth == 1 else 's'}"
    title = f"No more than {levels}"
    folders = [d for d in _in_base_dirs(pkg) if not d["hidden"]]
    deepest = max((d["depth"] for d in folders), default=0)
    branches: dict[str, tuple[str, int]] = {}
    for d in folders:
        if d["depth"] > max_depth:
            branch = "/".join(d["rel"].split("/")[: max_depth + 1])
            if branch not in branches or d["depth"] > branches[branch][1]:
                branches[branch] = (d["rel"], d["depth"])
    rows = [
        _finding(
            overrides,
            "folder_depth",
            "too-deep",
            rel,
            "folder",
            f"{depth} folder levels down (at most {max_depth} are recommended). "
            "Move the files up a level or merge folders.",
        )
        for rel, depth in sorted(branches.values(), key=lambda t: t[0].casefold())
    ]
    if rows:
        n = len(rows)
        detail = (
            f"{_count(n, 'branch')} go{'es' if n == 1 else ''} deeper than {levels} "
            f"(the deepest folder is {deepest} levels down)."
        )
    elif folders:
        detail = f"The deepest folder is {deepest} level{'' if deepest == 1 else 's'} down."
    else:
        detail = "The package has no folders."
    return _checked(rows, "folder_depth", title, detail)


# --------------------------------------------------------------------------------------
# 3. layouts
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PartHit:
    """A folder (or top-level file) that belongs to a part of a layout."""

    path: str  # relative to the package's base
    part: str  # the part's id, nested ones joined with "/" ("data/raw")
    title: str
    kind: str  # "folder" or "file"


@dataclass(frozen=True)
class Missing:
    """A required part of a layout that the package does not have (enough of)."""

    part: str
    title: str
    parent: str  # the folder it should be in ("" for the top level)
    kind: str  # "folder" or "file"
    need: int
    found: int
    repeat: bool = False
    example: str = ""

    def phrase(self) -> str:
        """``a "Data" folder`` / ``at least 2 "Figure" folders`` / ``a README``."""
        if self.kind == "file":
            what = self.title
            noun = f"a {what}"
        elif self.repeat or self.need > 1:
            e = f' (such as "{self.example}")' if self.example else ""
            noun = f'at least {self.need} "{self.title}" folder{"" if self.need == 1 else "s"}{e}'
        else:
            noun = f'a "{self.title}" folder'
        where = f' inside "{self.parent}"' if self.parent else ""
        return noun + where


@dataclass(frozen=True)
class LayoutMatch:
    """How well a package fits one layout."""

    layout: dict[str, Any]
    matched: bool
    coverage: float  # share of the top-level folders that belong to a part
    folders: int  # top-level folders
    required_met: int
    required_total: int
    missing: tuple[Missing, ...]
    unmatched: tuple[str, ...]  # top-level folders that belong to no part
    hits: tuple[PartHit, ...]

    @property
    def id(self) -> str:
        return str(self.layout["id"])

    @property
    def title(self) -> str:
        return str(self.layout["title"])


@dataclass
class _Shape:
    children: dict[str, list[str]]  # folder (rel, "" = base) -> its visible subfolders
    top: list[str]  # the visible top-level folders that hold files (empty ones are reported apart)
    root_files: list[str]  # visible files directly in the base
    n_files: int  # visible files in the base, any depth


def _shape(pkg: OpenedPackage) -> _Shape:
    children: dict[str, list[str]] = {"": []}
    top: list[str] = []
    for d in _in_base_dirs(pkg):
        if d["hidden"]:
            continue
        parent, _, name = d["rel"].rpartition("/")
        if not parent and name.casefold() in _IGNORED_TOP:
            continue
        children.setdefault(parent, []).append(name)
        if not parent and not d["empty"]:
            top.append(name)
    files = pkg.files()
    root_files: list[str] = []
    n_files = 0
    for name, rel, hidden, in_base in zip(
        files["name"].tolist(),
        files["rel"].tolist(),
        files["hidden"].tolist(),
        files["in_base"].tolist(),
        strict=True,
    ):
        if not in_base or hidden:
            continue
        n_files += 1
        if "/" not in rel:
            root_files.append(str(name))
    return _Shape(children, top, root_files, n_files)


def _any_match(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _match_layout(layout: dict[str, Any], shape: _Shape, min_coverage: float) -> LayoutMatch:
    hits: list[PartHit] = []
    missing: list[Missing] = []
    counts = [0, 0]  # required met, required total

    def place(parts: list[dict[str, Any]], parent: str, prefix: str) -> None:
        taken: set[str] = set()
        for part in parts:
            names = [
                n
                for n in shape.children.get(parent, [])
                if n not in taken and _any_match(part["match"], normalise_name(n))
            ]
            taken.update(names)
            pid = f"{prefix}/{part['id']}" if prefix else part["id"]
            if part["required"]:
                counts[1] += 1
                if len(names) >= part["min"]:
                    counts[0] += 1
                else:
                    missing.append(
                        Missing(
                            pid,
                            part["title"],
                            parent,
                            "folder",
                            part["min"],
                            len(names),
                            part["repeat"],
                            part["example"],
                        )
                    )
            for n in names:
                path = f"{parent}/{n}" if parent else n
                hits.append(PartHit(path, pid, part["title"], "folder"))
                place(part["parts"], path, pid)

    place(layout["parts"], "", "")
    taken_files: set[str] = set()
    for spec in layout["root_files"]:
        names = [
            f
            for f in shape.root_files
            if f not in taken_files and _any_match(spec["match"], normalise_name(_stem(f)))
        ]
        taken_files.update(names)
        if spec["required"]:
            counts[1] += 1
            if len(names) >= spec["min"]:
                counts[0] += 1
            else:
                missing.append(
                    Missing(spec["id"], spec["title"], "", "file", spec["min"], len(names))
                )
        hits.extend(PartHit(f, spec["id"], spec["title"], "file") for f in names)

    top = shape.top
    fitted = {h.path for h in hits if h.kind == "folder" and "/" not in h.path}
    coverage = len(fitted & set(top)) / len(top) if top else 0.0
    return LayoutMatch(
        layout=layout,
        matched=not missing and coverage >= min_coverage,
        coverage=coverage,
        folders=len(top),
        required_met=counts[0],
        required_total=counts[1],
        missing=tuple(missing),
        unmatched=tuple(n for n in top if n not in fitted),
        hits=tuple(hits),
    )


def _rank(matches: Sequence[LayoutMatch]) -> list[LayoutMatch]:
    def key(item: tuple[int, LayoutMatch]) -> tuple[Any, ...]:
        index, m = item
        n_parts = len({h.part for h in m.hits})
        if m.matched:
            return (0, -m.coverage, -n_parts, index)
        share = m.required_met / m.required_total if m.required_total else 1.0
        return (1, -(share + m.coverage), -n_parts, index)

    return [m for _, m in sorted(enumerate(matches), key=key)]


def match_layouts(
    pkg: OpenedPackage, layouts: Any = None, *, min_coverage: float = 0.6
) -> list[LayoutMatch]:
    """How the package fits each layout, best first.

    Layouts that match come first (the one that covers most of the top-level
    folders first), then the others, closest first. See the module docstring
    for what matching means.
    """
    shape = _shape(pkg)
    return _rank([_match_layout(spec, shape, min_coverage) for spec in load_layouts(layouts)])


@dataclass
class LayoutResult(Check):
    """The ``folder_layout`` item plus how the package fits each layout."""

    matches: list[LayoutMatch] = field(default_factory=list)
    best: LayoutMatch | None = None  # the layout that matched, else the closest one
    recognised: bool = False
    flat: bool = False  # a small package with no folders
    parts: Any = None  # table: the folders and files that belong to a part of `best`


def _parts_frame(best: LayoutMatch | None) -> Any:
    import pandas as pd

    hits = best.hits if best else ()
    cols: dict[str, Any] = {
        "path": [h.path for h in hits],
        "kind": [h.kind for h in hits],
        "layout": [best.id if best else "" for _ in hits],
        "part": [h.part for h in hits],
        "title": [h.title for h in hits],
        "recognised": [bool(best and best.matched) for _ in hits],
    }
    dtypes = dict.fromkeys(cols, "string") | {"recognised": "boolean"}
    return pd.DataFrame({c: pd.Series(v, dtype=dtypes[c]) for c, v in cols.items()})


def _found_titles(m: LayoutMatch) -> list[str]:
    seen: list[str] = []
    for h in m.hits:
        if "/" not in h.path and h.title not in seen:
            seen.append(h.title)
    return seen


def check_folder_layout(
    pkg: OpenedPackage,
    layouts: Any = None,
    *,
    min_coverage: float = 0.6,
    flat_max_files: int = 10,
    severity: Any = None,
) -> LayoutResult:
    """The ``folder_layout`` item: does the package follow a recognised layout?

    *layouts* are layout specs (see the module docstring; ``None`` means
    :func:`default_layouts`). A package with no folders and at most
    *flat_max_files* files passes as a small flat package.
    """
    if not 0 <= min_coverage <= 1:
        raise ValueError("min_coverage is a share between 0 and 1")
    overrides = load_severity(severity)
    specs = load_layouts(layouts)
    shape = _shape(pkg)
    check = "folder_layout"
    title = "Organised in a recognised layout"
    rows: list[dict[str, str]] = []

    def done(detail: str, status: str | None = None, **extra: Any) -> LayoutResult:
        chk = _checked(rows, check, title, detail, status)
        return LayoutResult(chk.findings, chk.item, chk.count, parts=_parts_frame(None), **extra)

    if shape.n_files == 0:
        return done("The package has no files.", "na")
    if not specs:
        return done("No layouts to compare the package with.", "na")

    def add(rule: str, path: str, kind: str, text: str) -> None:
        rows.append(_finding(overrides, check, rule, path, kind, text))

    if not shape.top and shape.n_files <= flat_max_files:
        n = _count(shape.n_files, "file")
        add(
            "flat-package",
            "",
            "package",
            f"A small package: all {n} are at the top level, so no folder layout is needed.",
        )
        return done(
            f"A small package ({n}, no folders): no folder layout is needed.", "pass", flat=True
        )

    ranked = _rank([_match_layout(spec, shape, min_coverage) for spec in specs])
    best = ranked[0]
    about = best.layout["description"].strip().rstrip(".")
    about = f": {about}" if about else ""
    if best.matched:
        found = _found_titles(best)
        also = [m.title for m in ranked[1:] if m.matched]
        detail = f'Follows the "{best.title}" layout (found {_listing(_quoted(found))}).'
        if also:
            detail += f" It also fits {_listing(_quoted(also), 2)}."
        add(
            "layout-recognised",
            "",
            "package",
            f'The folders follow the "{best.title}" layout{about}.',
        )
        for name in best.unmatched:
            add(
                "layout-extra-folder",
                name,
                "folder",
                f'Not part of the "{best.title}" layout. Fine if it belongs to the package; '
                "otherwise tidy it up.",
            )
        if best.unmatched:
            detail += f" Not part of the layout: {_listing(_quoted(best.unmatched))}."
    else:
        reasons = []
        if best.missing:
            reasons.append(
                f"The package is missing {_listing([m.phrase() for m in best.missing])}."
            )
        if best.folders == 0:
            reasons.append(
                f"It has no folders: all {_count(shape.n_files, 'file')} sit at the top level."
            )
        elif best.coverage < min_coverage:
            fit = round(best.coverage * best.folders)
            reasons.append(f"Only {fit} of {_count(best.folders, 'top-level folder')} fit it.")
        detail = " ".join([f'No recognised layout. The closest is "{best.title}".', *reasons])
        if best.unmatched and best.folders:
            detail += f" These folders do not fit: {_listing(_quoted(best.unmatched))}."
        add(
            "layout-not-recognised",
            "",
            "package",
            f'The folders do not follow a recognised layout. The closest is "{best.title}"{about}.',
        )
        for miss in best.missing:
            add(
                "layout-part-missing",
                miss.parent,
                "folder" if miss.parent else "package",
                _missing_sentence(miss),
            )
        for name in best.unmatched:
            add(
                "layout-folder-unmatched",
                name,
                "folder",
                f'Does not fit the "{best.title}" layout. Rename it, or move its '
                "contents into one of the layout's folders.",
            )
    chk = _checked(rows, check, title, detail)
    return LayoutResult(
        chk.findings,
        chk.item,
        chk.count,
        matches=ranked,
        best=best,
        recognised=best.matched,
        parts=_parts_frame(best),
    )


def _missing_sentence(miss: Missing) -> str:
    where = "inside it" if miss.parent else "at the top level"
    if miss.kind == "file":
        return f"No {miss.title} {where}."
    if miss.repeat or miss.need > 1:
        e = f' (such as "{miss.example}")' if miss.example else ""
        have = f" (only {miss.found} found)" if miss.found else ""
        return f'Needs at least {miss.need} "{miss.title}" folder{"" if miss.need == 1 else "s"}{e}{have} {where}.'
    return f'No "{miss.title}" folder {where}.'


# --------------------------------------------------------------------------------------
# 4. empty folders
# --------------------------------------------------------------------------------------


def check_empty_folders(pkg: OpenedPackage, *, severity: Any = None) -> Check:
    """The ``empty_folders`` item: folders with no file in them or below.

    A chain of empty folders is reported once, at its top. Hidden folders and
    links are left out.
    """
    overrides = load_severity(severity)
    folders = [d for d in _in_base_dirs(pkg) if not d["hidden"] and not d["link"]]
    empties = [d["rel"] for d in folders if d["empty"]]
    found = set(empties)

    def top_of(rel: str) -> str:
        parts = rel.split("/")
        return next(p for i in range(1, len(parts) + 1) if (p := "/".join(parts[:i])) in found)

    tops_of = {rel: top_of(rel) for rel in empties}
    nested = Counter(top for rel, top in tops_of.items() if top != rel)
    rows = []
    for rel in [r for r in empties if tops_of[r] == r]:
        extra = f" It holds {_count(nested[rel], 'more empty folder')}." if nested[rel] else ""
        rows.append(
            _finding(
                overrides,
                "empty_folders",
                "empty-folder",
                rel,
                "folder",
                f"Empty folder. Remove it, or add what belongs in it.{extra}",
            )
        )
    detail = (
        f"{_count(len(empties), 'empty folder')} found." if empties else "No empty folders found."
    )
    return _checked(rows, "empty_folders", "No empty folders", detail, count=len(empties))


# --------------------------------------------------------------------------------------
# everything together
# --------------------------------------------------------------------------------------


@dataclass
class StructureResult:
    """What :func:`check_structure` found."""

    findings: Any  # table: path, kind, check, rule, severity, detail
    checklist: Any  # table: item, title, status, detail
    tree: str
    dirs: Any
    parts: Any  # table: path, kind, layout, part, title, recognised
    layout: LayoutResult
    layouts: list[dict[str, Any]]
    package: str
    n_files: int
    n_folders: int
    size: int
    max_depth: int
    min_coverage: float
    summary_text: str
    deepest: int = 0

    @property
    def traffic_light(self) -> str:
        return traffic_light(self.checklist)

    @property
    def layout_id(self) -> str:
        """The id of the layout that matched, or ``""``."""
        best = self.layout.best
        return best.id if best is not None and best.matched else ""


def check_structure(
    pkg: OpenedPackage,
    *,
    max_depth: int = 3,
    layouts: Any = None,
    min_coverage: float = 0.6,
    flat_max_files: int = 10,
    severity: Any = None,
    max_items: int = 15,
    max_lines: int = 400,
) -> StructureResult:
    """Run the four structure checks on *pkg* (see the module docstring)."""
    overrides = load_severity(severity)
    specs = load_layouts(layouts)
    tree_check, tree = check_folder_tree(
        pkg, max_items=max_items, max_lines=max_lines, severity=overrides
    )
    depth_check = check_folder_depth(pkg, max_depth, severity=overrides)
    layout_check = check_folder_layout(
        pkg, specs, min_coverage=min_coverage, flat_max_files=flat_max_files, severity=overrides
    )
    empty_check = check_empty_folders(pkg, severity=overrides)
    checks: list[Check] = [tree_check, depth_check, layout_check, empty_check]
    findings = findings_frame([row for c in checks for row in c.findings])
    checklist = checklist_frame([c.item for c in checks])

    files = _in_base_files(pkg)
    folders = _in_base_dirs(pkg)
    deepest = max((d["depth"] for d in folders if not d["hidden"]), default=0)
    size = sum(s for _, s, _ in files)
    result = StructureResult(
        findings=findings,
        checklist=checklist,
        tree=tree,
        dirs=pkg.dirs(),
        parts=layout_check.parts,
        layout=layout_check,
        layouts=specs,
        package=pkg.name,
        n_files=len(files),
        n_folders=len(folders),
        size=size,
        max_depth=max_depth,
        min_coverage=min_coverage,
        summary_text="",
        deepest=deepest,
    )
    result.summary_text = _summary_text(result, depth_check, empty_check)
    return result


def _summary_text(res: StructureResult, depth: Check, empty: Check) -> str:
    if res.n_files == 0:
        return "The package has no files."
    parts = [f"{_count(res.n_files, 'file')} in {_count(res.n_folders, 'folder')}"]
    layout = res.layout
    if layout.flat:
        parts.append("a small package with no folders")
    elif layout.recognised and layout.best is not None:
        parts.append(f'follows the "{layout.best.title}" layout')
    elif layout.best is not None:
        parts.append("no recognised layout")
    if depth.item["status"] != "pass":
        parts.append(
            f"{_count(depth.count, 'branch')} deeper than {_count(res.max_depth, 'folder level')}"
        )
    elif res.n_folders:
        parts.append(f"at most {_count(res.deepest, 'folder level')} deep")
    if empty.count:
        parts.append(_count(empty.count, "empty folder"))
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------------------


def _code_list(items: Sequence[str], limit: int = 15) -> str:
    return _listing([f"`{i}`" for i in items], limit)


def _bullets(rows: Sequence[Mapping[str, str]], limit: int = 15) -> str:
    lines = []
    for r in rows[:limit]:
        path = r["path"]
        lines.append(f"- `{path}`: {r['detail']}" if path else f"- {r['detail']}")
    if len(rows) > limit:
        lines.append(f"- … and {len(rows) - limit} more")
    return "\n".join(lines)


def _outline(layout: Mapping[str, Any]) -> str:
    def part_lines(parts: Sequence[Mapping[str, Any]], depth: int) -> list[str]:
        out = []
        for p in parts:
            notes = ["required"] if p["required"] else []
            if p["repeat"]:
                notes.append("one folder for each")
            label = p["title"] + (f" ({', '.join(notes)})" if notes else "")
            out.append(f"{'  ' * depth}- {label}")
            out += part_lines(p["parts"], depth + 1)
        return out

    lines = part_lines(layout["parts"], 0)
    for f in layout["root_files"]:
        lines.append(f"- {f['title']} at the top level" + (" (required)" if f["required"] else ""))
    about = str(layout["description"]).strip().rstrip(".")
    head = f"**{layout['title']}**" + (f": {about}." if about else "")
    return head + "\n\n" + "\n".join(lines)


def structure_report(res: StructureResult) -> list[Any]:
    """Report blocks (markdown and tables) for a :class:`StructureResult`."""
    import pandas as pd

    from metacheck.report import collapse_section, scroll_table

    f = res.findings
    by_check: dict[str, list[dict[str, str]]] = {}
    for rec in f.to_dict("records"):
        by_check.setdefault(str(rec["check"]), []).append({k: str(v) for k, v in rec.items()})

    blocks: list[Any] = [
        f"We looked at the folder structure of **{res.package}**: "
        f"{_count(res.n_files, 'file')} in {_count(res.n_folders, 'folder')} ({_size(res.size)})."
    ]
    checklist = pd.DataFrame(
        {
            "Item": res.checklist["title"].astype(object),
            "Status": res.checklist["status"].astype(object),
            "Detail": res.checklist["detail"].astype(object),
        }
    )
    blocks += ["#### Checklist", scroll_table(checklist, maxrows=10)]

    deep = by_check.get("folder_depth", [])
    if deep:
        blocks += [
            "#### Folder depth",
            "Deeply nested folders are hard to find your way around in. "
            f"These branches go deeper than {res.max_depth} levels:",
            _bullets(deep),
        ]

    lay = by_check.get("folder_layout", [])
    lead_rules = {"layout-recognised", "layout-not-recognised", "flat-package"}
    lead = [r["detail"] for r in lay if r["rule"] in lead_rules]
    if lead:
        blocks += ["#### Folder layout", *lead]
        missing = [r for r in lay if r["rule"] == "layout-part-missing"]
        unmatched = [r["path"] for r in lay if r["rule"] == "layout-folder-unmatched"]
        extra = [r["path"] for r in lay if r["rule"] == "layout-extra-folder"]
        if missing:
            blocks.append(_bullets(missing))
        if unmatched:
            these = (
                "These top-level folders do not"
                if len(unmatched) > 1
                else "This top-level folder does not"
            )
            blocks.append(
                f"{these} fit the layout: {_code_list(unmatched)}. Rename "
                f"{'them' if len(unmatched) > 1 else 'it'}, or move "
                f"{'their' if len(unmatched) > 1 else 'its'} contents into one of the "
                "layout's folders."
            )
        if extra:
            blocks.append(
                f"Not part of the layout: {_code_list(extra)}. Fine if "
                f"{'they belong' if len(extra) > 1 else 'it belongs'} to the package; "
                "otherwise tidy up."
            )
    if res.layouts:
        blocks.append(
            collapse_section(
                [
                    "These are the layouts the folders are compared with. Numbers in front of a "
                    'folder name ("1 Data") and differences in capitals, spaces, "-" and "_" '
                    "are ignored.",
                    *[_outline(spec) for spec in res.layouts],
                ],
                title="Recognised layouts",
                callout="note",
            )
        )

    empty = by_check.get("empty_folders", [])
    if empty:
        blocks += ["#### Empty folders", _bullets(empty)]

    blocks += [
        "#### Folder structure",
        "Folders first, then files, with sizes. Big folders are shortened.",
        f"```\n{res.tree}\n```",
    ]
    return blocks
