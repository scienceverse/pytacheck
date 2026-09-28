"""``store build``: generate a store's ``index.json`` (run by the store's CI).

A store repository holds:

* ``packs/<name>/`` -- a pack submitted as a folder (e.g. through GitHub's web
  editor); its source becomes ``{"github": <store repo>, "rev": <last commit
  touching packs/<name>>, "subdir": "packs/<name>"}``;
* ``packs/<name>.json`` -- a pack in its author's own repository:
  ``{"name": ..., "source": {"github": "owner/repo", "rev": "<40 hex>"}}``.
  Next to a folder, the same file may only carry ``reviewed`` / ``yanked``;
* ``store.json`` (optional) -- the store's ``name``, ``description`` and extra ``fields``.

Everything else in an index entry is computed: the tree hash, whether the
pack has code, its languages, each module's metadata (read statically from
the literal ``@module(...)`` arguments -- nothing is imported), preset
descriptions and validation-derived PPV and sensitivity. Maintainers set
``yanked`` and record a review in the entry files as ``reviewed`` (the date)
plus ``reviewed_tree_sha256`` (the tree hash of the files they read), so a
review never carries over to changed files. The build otherwise keeps both
from the existing index (``reviewed`` only while the tree hash is unchanged).
A review that no longer matches the pack is cleared, and is an error under
``--check`` so CI fails until a maintainer reviews the new files.

A pack or module name that is new to the index may not be one typo from a
name already in use (``clinical_trial`` next to ``clinical_trials``), so a
typo cannot pick up someone else's code. That too is an error under ``--check``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pytacheck.packs.check import CheckIssue, _file_issues
from pytacheck.packs.fetch import HOSTS, fetch_source
from pytacheck.packs.manifest import (
    FIELDS,
    MODULE_NAME_RE,
    PackError,
    read_manifest,
    validate_pack_name,
    validation_metrics,
)
from pytacheck.packs.scan import module_metadata
from pytacheck.packs.tree import tree_sha256

__all__ = ["check_entry_file", "store_build"]

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ENTRY_KEYS = frozenset({"name", "source", "reviewed", "reviewed_tree_sha256", "yanked", "version"})
_GITHUB_REMOTE = re.compile(
    r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([^/]+/[^/]+?)(?:\.git)?/?$"
)


def _err(where: str, message: str) -> CheckIssue:
    return CheckIssue("error", "store", where, message)


def _warn(where: str, message: str) -> CheckIssue:
    return CheckIssue("warning", "store", where, message)


def _git(root: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        res = subprocess.run(  # noqa: S603 - local, read-only git queries
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def _repo_slug(root: Path, repo: str | None) -> tuple[str | None, bool]:
    """``(owner/repo, has_git_history)`` of a store folder."""
    top = _git(root, "rev-parse", "--show-toplevel")
    has_git = top is not None and Path(top).resolve() == root.resolve()
    if repo:
        return repo.strip().removeprefix("https://github.com/").strip("/"), has_git
    if not has_git:
        return None, False
    url = _git(root, "remote", "get-url", "origin") or ""
    m = _GITHUB_REMOTE.match(url)
    return (m.group(1) if m else None), has_git


def _pack_fields(folder: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The computed part of an index entry."""
    names = sorted(
        p.stem
        for p in folder.glob("*.py")
        if not p.name.startswith("_") and MODULE_NAME_RE.fullmatch(p.stem)
    )
    modules = []
    for n in names:
        meta = module_metadata(folder / f"{n}.py") or {}
        modules.append(
            {
                "name": n,
                "title": meta.get("title") or "",
                "description": meta.get("description") or "",
                "section": meta.get("section") or "general",
                "requires": list(meta.get("requires") or []),
                "validation": validation_metrics(meta.get("validation")) or None,
            }
        )
    code = any(
        p.suffix == ".py" and p.relative_to(folder).parts[0] not in ("tests", "data")
        for p in folder.rglob("*.py")
        if "__pycache__" not in p.parts
    )
    languages = []
    if names:
        languages.append("python")
    if any(folder.glob("*.R")):
        languages.append("r")
    return {
        "name": manifest["name"],
        "version": manifest.get("version"),
        "title": manifest.get("title") or "",
        "description": manifest.get("description") or "",
        "fields": list(manifest.get("fields") or []),
        "keywords": list(manifest.get("keywords") or []),
        "license": manifest.get("license"),
        "authors": list(manifest.get("authors") or []),
        "homepage": manifest.get("homepage"),
        "tree_sha256": tree_sha256(folder),
        "code": code,
        "languages": languages,
        "requires": dict(manifest.get("requires") or {}),
        "dependencies": list(manifest.get("dependencies") or []),
        "modules": modules,
        "presets": {k: v.get("description", "") for k, v in manifest.get("presets", {}).items()},
    }


def _review_fields(
    name: str,
    tree: str,
    entry_file: Mapping[str, Any] | None,
    old: Mapping[str, Any] | None,
    issues: list[CheckIssue],
    *,
    check: bool = False,
) -> dict[str, Any]:
    """``reviewed`` / ``yanked`` of an index entry: a review holds only for the files read."""
    out: dict[str, Any] = {"reviewed": None, "yanked": None}
    stale: str | None = None
    if old:
        out["yanked"] = old.get("yanked")
        if old.get("reviewed"):
            if old.get("tree_sha256") == tree:
                out["reviewed"] = old["reviewed"]
            else:
                stale = f"the review of {old['reviewed']} was for tree {old.get('tree_sha256')}"
    if entry_file:
        if "yanked" in entry_file:
            out["yanked"] = entry_file["yanked"]
        date = entry_file.get("reviewed")
        reviewed_tree = entry_file.get("reviewed_tree_sha256")
        if date and reviewed_tree == tree:
            out["reviewed"], stale = date, None
        elif date and not reviewed_tree:
            out["reviewed"] = None
            stale = (
                f"'reviewed' ({date}) is not tied to the files that were read: add "
                f'"reviewed_tree_sha256": "{tree}" to packs/{name}.json if they are these'
            )
        elif date:
            out["reviewed"] = None
            stale = f"the review of {date} was for tree {reviewed_tree}"
        elif "reviewed" in entry_file:  # an explicit null withdraws a review
            out["reviewed"], stale = None, None
    if stale is not None:
        message = (
            f"changed since it was reviewed ({stale}; the pack is now tree {tree}): "
            "'reviewed' is cleared until a maintainer reviews these files and records "
            f'"reviewed" and "reviewed_tree_sha256" in packs/{name}.json'
        )
        issues.append(_err(f"packs/{name}", message) if check else _warn(f"packs/{name}", message))
    return out


def _within_one_edit(a: str, b: str) -> bool:
    """Whether *a* and *b* are at most one typo apart.

    A typo is one character added, removed or changed, or two neighbouring
    characters swapped.
    """
    if a == b:
        return True
    if len(a) > len(b):
        a, b = b, a
    if len(b) - len(a) > 1:
        return False
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) < len(b):
        return a[i:] == b[i + 1 :]
    return a[i + 1 :] == b[i + 1 :] or (
        a[i : i + 2] == b[i : i + 2][::-1] and a[i + 2 :] == b[i + 2 :]
    )


def _module_names(entry: Mapping[str, Any] | None) -> list[str]:
    modules = (entry or {}).get("modules") or []
    return [m["name"] for m in modules if isinstance(m, Mapping) and isinstance(m.get("name"), str)]


def _name_issues(
    entries: Mapping[str, Mapping[str, Any]],
    old: Mapping[Any, Mapping[str, Any]],
    *,
    check: bool = False,
) -> list[CheckIssue]:
    """New pack and module names one typo away from a name in use.

    A new pack is compared with the other packs and ``metacheck``; a new module
    with the built-in modules and the other packs' modules. Case, ``-`` and
    ``_`` do not count. Names in the previous index are not checked again, so a
    new built-in module never fails a store that already lists a close name.
    """
    from pytacheck.module import _builtin_names
    from pytacheck.presets import _declared_builtin

    def fold(name: str) -> str:
        return name.lower().replace("-", "").replace("_", "")

    rule = "rename it (a name new to the store must differ by more than one typo)"
    issue = _err if check else _warn
    issues: list[CheckIssue] = []
    for name in sorted(set(entries) - set(old)):
        for other in ("metacheck", *entries):
            if other != name and _within_one_edit(fold(name), fold(other)):
                message = f"the pack name '{name}' is one typo from the pack '{other}'"
                issues.append(issue(f"packs/{name}", f"{message}: {rule}"))
    modules = [("metacheck", m) for m in sorted({*_builtin_names(), *_declared_builtin()})]
    modules += [(name, m) for name, entry in entries.items() for m in _module_names(entry)]
    for name, mod in modules:
        if name == "metacheck" or mod in _module_names(old.get(name)):
            continue
        for pack, other in modules:
            if pack != name and _within_one_edit(fold(mod), fold(other)):
                how = "the same as" if mod == other else "one typo from"
                message = f"the module name '{mod}' is {how} '{pack}::{other}'"
                issues.append(issue(f"packs/{name}", f"{message}: {rule}"))
    return issues


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_checks(folder: Path, name: str, where: str, issues: list[CheckIssue]) -> Any:
    issues.extend(
        CheckIssue(i.level, i.code, f"{where}/{i.where}", i.message) for i in _file_issues(folder)
    )
    try:
        manifest = read_manifest(folder)
    except PackError as exc:
        issues.append(_err(where, str(exc)))
        return None
    if manifest["name"] != name:
        issues.append(_err(where, f"pack.json names the pack '{manifest['name']}', not '{name}'"))
        return None
    raw = _read_json(folder / "pack.json")
    issues.extend(
        _err(where, f"pack.json may not set '{key}' (store maintainers do)")
        for key in ("reviewed", "yanked")
        if key in raw
    )
    return manifest


def _external_source(data: Mapping[str, Any], where: str, issues: list[CheckIssue]) -> Any:
    source = data.get("source")
    if not isinstance(source, Mapping):
        issues.append(_err(where, "an entry file needs a 'source' object"))
        return None
    kinds = [k for k in (*HOSTS, "git") if k in source]
    if len(kinds) != 1:
        issues.append(_err(where, "the source needs exactly one of github, gitlab, codeberg, git"))
        return None
    rev = source.get("rev")
    if not isinstance(rev, str) or not _SHA.match(rev):
        issues.append(_err(where, f"source.rev must be a full 40-hex commit SHA, not {rev!r}"))
        return None
    return dict(source)


def check_entry_file(path: str | os.PathLike[str]) -> list[CheckIssue]:
    """``pack check`` for a pack listed from its own repository (``packs/<name>.json``).

    Fetches the entry's source at its ``rev`` into a temporary folder and
    checks it there, as for a folder pack (so store CI checks both kinds).
    """
    from pytacheck.packs.check import pack_check

    file = Path(path)
    name = file.stem
    where = f"packs/{file.name}"
    issues: list[CheckIssue] = []
    try:
        data = _read_json(file)
    except (OSError, ValueError) as exc:
        return [_err(where, f"not valid JSON: {exc}")]
    if not isinstance(data, dict):
        return [_err(where, "must hold a JSON object")]
    source = _external_source(data, where, issues)
    if source is None:
        return issues
    with tempfile.TemporaryDirectory(prefix="pytacheck-check-") as tmp:
        dest = Path(tmp) / name
        try:
            fetch_source(source, source["rev"], dest)
        except PackError as exc:
            return [*issues, _err(where, f"cannot fetch its source: {exc}")]
        manifest = _manifest_checks(dest, name, where, issues)
        if manifest is None:
            return issues
        return [*issues, *pack_check(dest)]


def store_build(
    path: str | os.PathLike[str] = ".",
    *,
    check: bool = False,
    repo: str | None = None,
) -> tuple[dict[str, Any], list[CheckIssue]]:
    """Build (or with ``check=True`` only validate) a store's ``index.json``.

    Returns ``(index, issues)``; the file is written only when not checking
    and there are no errors. *repo* (``owner/repo``) overrides the GitHub
    repository taken from ``git remote get-url origin``. Without git history
    for the store folder, in-repo packs get ``{"path": "packs/<name>"}``
    sources (installable from a local copy of the store; the store's CI
    regenerates the index with commits).
    """
    root = Path(path).expanduser().resolve()
    issues: list[CheckIssue] = []
    packs_dir = root / "packs"
    if not packs_dir.is_dir():
        return {}, [_err(str(root), "no packs/ folder: not a store")]
    index_path = root / "index.json"
    old_index: dict[str, Any] = {}
    if index_path.is_file():
        try:
            old_index = _read_json(index_path)
        except (OSError, ValueError) as exc:
            issues.append(_warn("index.json", f"the existing index is unreadable: {exc}"))
    old = {e.get("name"): e for e in old_index.get("packs", []) if isinstance(e, Mapping)}
    meta: dict[str, Any] = {}
    if (root / "store.json").is_file():
        try:
            meta = _read_json(root / "store.json")
        except (OSError, ValueError) as exc:
            issues.append(_err("store.json", f"unreadable: {exc}"))
    slug, has_git = _repo_slug(root, repo)
    if not has_git:
        issues.append(
            _warn(
                str(root),
                "no git history for this folder: in-repo packs get path sources "
                "(the store's CI regenerates the index with commits)",
            )
        )
    elif slug is None:
        issues.append(_warn(str(root), "no GitHub origin: pass --repo OWNER/REPO for sources"))

    entries: dict[str, dict[str, Any]] = {}
    folders = sorted(p for p in packs_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    files = sorted(p for p in packs_dir.glob("*.json"))
    entry_files: dict[str, dict[str, Any]] = {}
    for f in files:
        where = f"packs/{f.name}"
        try:
            data = _read_json(f)
        except (OSError, ValueError) as exc:
            issues.append(_err(where, f"not valid JSON: {exc}"))
            continue
        if not isinstance(data, dict):
            issues.append(_err(where, "must hold a JSON object"))
            continue
        unknown = sorted(set(data) - _ENTRY_KEYS)
        if unknown:
            issues.append(_err(where, f"unknown keys {unknown}; allowed: {sorted(_ENTRY_KEYS)}"))
        if data.get("name", f.stem) != f.stem:
            issues.append(_err(where, f"names the pack '{data.get('name')}', not '{f.stem}'"))
            continue
        entry_files[f.stem] = data

    for folder in folders:
        name = folder.name
        where = f"packs/{name}"
        try:
            validate_pack_name(name)
        except PackError as exc:
            issues.append(_err(where, str(exc)))
            continue
        extra = entry_files.pop(name, None)
        if extra is not None and "source" in extra:
            issues.append(_err(where, f"has both a folder and a source in packs/{name}.json"))
            continue
        manifest = _manifest_checks(folder, name, where, issues)
        if manifest is None:
            continue
        entry = _pack_fields(folder, manifest)
        rev = _git(root, "log", "-1", "--format=%H", "--", f"packs/{name}") if has_git else None
        if rev and slug:
            entry["source"] = {"github": slug, "rev": rev, "subdir": f"packs/{name}"}
            dirty = _git(root, "status", "--porcelain", "--", f"packs/{name}")
            if dirty:
                issues.append(_warn(where, "has uncommitted changes: commit before building"))
        else:
            if has_git and not rev:
                issues.append(_warn(where, "is not committed yet: it gets a path source"))
            entry["source"] = {"path": f"packs/{name}"}
        entry.update(
            _review_fields(name, entry["tree_sha256"], extra, old.get(name), issues, check=check)
        )
        entries[name] = entry

    for name, data in entry_files.items():
        where = f"packs/{name}.json"
        try:
            validate_pack_name(name)
        except PackError as exc:
            issues.append(_err(where, str(exc)))
            continue
        source = _external_source(data, where, issues)
        if source is None:
            continue
        with tempfile.TemporaryDirectory(prefix="pytacheck-store-") as tmp:
            dest = Path(tmp) / "pack"
            try:
                fetch_source(source, source["rev"], dest)
            except PackError as exc:
                prior = old.get(name)
                if prior and prior.get("source") == source:
                    issues.append(_warn(where, f"cannot fetch ({exc}); kept the previous entry"))
                    entries[name] = dict(prior)
                else:
                    issues.append(_err(where, f"cannot fetch its source: {exc}"))
                continue
            manifest = _manifest_checks(dest, name, where, issues)
            if manifest is None:
                continue
            if data.get("version") is not None and data["version"] != manifest.get("version"):
                issues.append(
                    _err(
                        where,
                        f"declares version {data['version']!r} but its pack.json has "
                        f"{manifest.get('version')!r}",
                    )
                )
            entry = _pack_fields(dest, manifest)
        entry["source"] = source
        entry.update(
            _review_fields(name, entry["tree_sha256"], data, old.get(name), issues, check=check)
        )
        entries[name] = entry

    issues.extend(_name_issues(entries, old, check=check))
    fields = list(dict.fromkeys([*FIELDS, *(meta.get("fields") or [])]))
    packs = [entries[n] for n in sorted(entries)]
    index: dict[str, Any] = {
        "schema": 1,
        "name": meta.get("name") or old_index.get("name") or root.name,
        "description": meta.get("description") or old_index.get("description") or "",
        "generated": old_index.get("generated"),
        "fields": fields,
        "packs": packs,
    }
    unchanged = {k: v for k, v in index.items() if k != "generated"} == {
        k: v for k, v in old_index.items() if k != "generated"
    }
    if not unchanged or not index["generated"]:
        index["generated"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not check and not any(i.level == "error" for i in issues):
        tmp_path = index_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp_path.replace(index_path)
    return index, issues
