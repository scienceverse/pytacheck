"""Installing, removing, updating, listing and showing packs.

``pack_install(ref)`` accepts:

* ``"psych"`` -- a pack listed by exactly one configured store;
* ``"store/psych"`` -- a pack of that store;
* ``"psych@<rev prefix>"`` -- a store pack at another commit (installed unlisted);
* ``"https://github.com/owner/repo[@ref]"`` (also GitLab and Codeberg URLs,
  ``.../tree/<ref>/<subdir>``, and ``git+<url>`` / ``ssh://`` / ``file://`` for
  git) -- an unlisted pack, its tag or branch resolved to a commit once;
* ``"./folder"`` -- a path pack (live, unpinned, for development);
* ``None`` -- install every pin of the effective config that is missing.

Each install downloads the pinned commit, extracts it safely, checks its
tree hash against the store index, shows a consent card and asks ``[y/N]``
(unless ``yes=True``), then moves it into ``<data>/packs/<name>/<rev12>/``
atomically, writes the install record, makes the files read-only and pins
it in the user or project config. Nothing from the pack is imported during
install. See ``docs/MODULES.md``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck.config import config_path, data_dir, load_config, update_config
from pytacheck.packs import ui
from pytacheck.packs.fetch import HOSTS, describe_source, fetch_source, resolve_rev
from pytacheck.packs.manifest import (
    Pack,
    PackError,
    read_manifest,
    validate_manifest,
    validate_pack_name,
    validation_metrics,
)
from pytacheck.packs.registry import (
    get_pack,
    install_dir,
    integrity,
    pin_rev12,
    registry,
)
from pytacheck.packs.scan import FileScan, module_metadata, scan_file, scan_tree
from pytacheck.packs.tree import INSTALL_RECORD, file_sha256, tree_files, tree_sha256

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "Cancelled",
    "missing_dependencies",
    "pack_install",
    "pack_list",
    "pack_remove",
    "pack_show",
    "pack_update",
]

_STORE_REF = re.compile(
    r"^(?:(?P<store>[a-z][a-z0-9_-]{1,39})/)?(?P<name>[a-z][a-z0-9_-]{1,39})"
    r"(?:@(?P<rev>[0-9a-fA-F]{4,40}))?$"
)


class Cancelled(PackError):
    """The user did not confirm (or could not be asked)."""


@dataclass
class _Candidate:
    """What is about to be installed."""

    name: str | None  # expected pack name (None for URL installs: read from pack.json)
    source: dict[str, Any]  # without "rev"
    rev: str | None
    tree_sha256: str | None = None  # expected (from the store index or the pin)
    version: str | None = None
    store: str | None = None
    reviewed: str | None = None
    yanked: Any = None
    notes: list[str] = field(default_factory=list)
    base: str | None = None  # a local store's folder (relative path sources)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rmtree(path: Path) -> None:
    """Remove a tree even when its files are read-only."""

    def fix(func: Any, p: str, _exc: Any) -> None:
        with contextlib.suppress(OSError):
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(p)

    if not path.exists() and not path.is_symlink():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=fix)


def _read_record(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_matches(root: Path, rev: str | None, tree: str | None) -> bool:
    """Whether *root* is a complete, unmodified install of *rev* / *tree*."""
    if not root.is_dir():
        return False
    record = _read_record(root)
    if not record or (rev and record.get("rev") != rev):
        return False
    if tree and record.get("tree_sha256") != tree:
        return False
    try:
        return all(file_sha256(root / rel) == sha for rel, sha in record["files"].items())
    except (OSError, KeyError, AttributeError, TypeError):
        return False


def missing_dependencies(requirements: Any) -> list[str]:
    """The PEP 508 requirements that are not installed (pytacheck never installs them)."""
    from pytacheck.presets import _dependency_ok

    return [r for r in dict.fromkeys(requirements or ()) if not _dependency_ok(r)]


def _pip_command(missing: list[str]) -> str:
    import shlex

    return "pip install " + " ".join(shlex.quote(d) for d in missing)


def _requires_ok(requires: Mapping[str, str]) -> list[str]:
    """Unmet ``requires`` entries (``{"pytacheck": ">=0.3.1"}``), as readable strings."""
    out = []
    for name, spec in requires.items():
        req = f"{name}{spec}" if spec and spec[0] in "<>=!~" else f"{name} {spec}".strip()
        try:
            ok = not missing_dependencies([req.replace(" ", "")])
        except Exception:
            ok = True  # unparseable specifier: shown on the card, not enforced
        if not ok:
            out.append(req)
    return out


def _source_without_rev(source: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in dict(source or {}).items() if k != "rev" and v not in (None, "")}


# ---------------------------------------------------------------------------
# Parsing refs
# ---------------------------------------------------------------------------


def _is_path_ref(ref: str) -> bool:
    if "://" in ref or ref.startswith("git@") or ref.startswith("git+"):
        return False
    if ref.startswith((".", "/", "~")) or (len(ref) > 1 and ref[1] == ":"):
        return True
    return ("/" in ref or os.sep in ref) and Path(ref).expanduser().is_dir()


def _split_url_ref(url: str) -> tuple[str, str | None]:
    """``https://github.com/x/y@v1`` -> ``(url, "v1")`` (user@host is not a ref)."""
    left, sep, suffix = url.rpartition("@")
    if not sep or not suffix or ":" in suffix:
        return url, None
    after_scheme = left.split("://", 1)[1] if "://" in left else left
    if "/" in after_scheme or (":" in after_scheme and "@" in after_scheme):
        return left, suffix
    return url, None


def _refuse_credentials(url: str) -> None:
    """Pins, install records and run records are shared: a URL may not carry a credential.

    A password or user info, a secret query or fragment parameter
    (``?access_token=``...) or a token value from the environment (see
    :func:`pytacheck.packs.auth.has_credentials`); the error shows it redacted.
    """
    from pytacheck.packs.auth import credentials_help, has_credentials, redact

    if has_credentials(url):
        raise PackError(
            f"The URL {redact(url)} contains credentials, which would be written to your "
            f"config, install record and run records; use the plain URL and "
            f"{credentials_help(url)}"
        )


def _check_source(source: Mapping[str, Any]) -> None:
    """Refuse a source (from a URL, a pin, a store index or a run record) with credentials.

    Every source that is installed is written to the install record, a pin and
    the run records of its modules, so none may carry a credential.
    """
    for key, value in source.items():
        if isinstance(value, str) and (key == "git" or "://" in value or "@" in value):
            _refuse_credentials(value)


def _url_source(url: str) -> tuple[dict[str, Any], str | None]:
    """A source dict and ref for a URL install."""
    force_git = url.startswith("git+")
    if force_git:
        url = url[4:]
    _refuse_credentials(url)
    url, ref = _split_url_ref(url)
    if not force_git:
        for host, domain in HOSTS.items():
            m = re.match(
                rf"^https://(?:www\.)?{re.escape(domain)}/(.+?)(?:\.git)?/?$", url, re.IGNORECASE
            )
            if not m:
                continue
            path = m.group(1)
            subdir = ""
            tree = re.match(r"^([^/]+/[^/]+)/(?:-/)?tree/([^/]+)(?:/(.+))?$", path)
            if tree:
                path, tree_ref, subdir = tree.group(1), tree.group(2), tree.group(3) or ""
                ref = ref or tree_ref
            elif host != "gitlab" and path.count("/") != 1:
                raise PackError(f"Expected https://{domain}/OWNER/REPO, got {url}")
            source: dict[str, Any] = {host: path}
            if subdir:
                source["subdir"] = subdir.strip("/")
            return source, ref
    return {"git": url}, ref


def _parse_ref(ref: str) -> tuple[str, Any]:
    ref = ref.strip()
    if not ref:
        raise PackError("An empty pack reference")
    if _is_path_ref(ref):
        return "path", ref
    if "://" in ref or ref.startswith("git@") or ref.startswith("git+"):
        return "url", ref
    m = _STORE_REF.match(ref)
    if m is None:
        raise PackError(
            f"Cannot read the pack reference {ref!r}. Use NAME, STORE/NAME, NAME@REV, "
            "a repository URL or ./FOLDER"
        )
    return "store", (m.group("store"), m.group("name"), (m.group("rev") or "").lower() or None)


def _store_path(store: str, name: str, path: str, base: str | None) -> Path:
    """The folder of a store entry's ``{"path": ...}`` source: inside a local store only."""
    if not base:
        raise PackError(
            f"The store '{store}' lists '{name}' with the local-folder source {path!r}; only "
            "a store that is itself a local folder may (a remote store's CI lists commits)"
        )
    root = Path(base).resolve()
    folder = (root / path).resolve()
    if Path(path).is_absolute() or not folder.is_relative_to(root):
        raise PackError(
            f"The store '{store}' lists '{name}' with the source {path!r}, which is outside "
            f"the store's folder {root}"
        )
    return folder


def _store_candidate(
    store: str | None, name: str, rev_prefix: str | None, *, refresh: bool = False
) -> _Candidate:
    from pytacheck.packs.stores import find_entry

    store_name, entry = find_entry(name, store=store, refresh=refresh)
    source = _source_without_rev(entry.get("source"))
    if not source:
        raise PackError(f"The store '{store_name}' entry for '{name}' has no source")
    base = entry.get("_location")  # set only for a store that is a local folder
    if "path" in source:
        source["path"] = str(_store_path(store_name, name, str(source["path"]), base))
    index_rev = (entry.get("source") or {}).get("rev")
    cand = _Candidate(
        name=name,
        source=source,
        rev=index_rev,
        tree_sha256=entry.get("tree_sha256"),
        version=entry.get("version"),
        store=store_name,
        reviewed=entry.get("reviewed"),
        yanked=entry.get("yanked"),
        base=entry.get("_location"),
    )
    if rev_prefix and not (index_rev and str(index_rev).startswith(rev_prefix)):
        rev = resolve_rev(source, rev_prefix)
        listed = str(index_rev)[:12] if index_rev else "no commit"
        cand = replace(
            cand, rev=rev, tree_sha256=None, version=None, store=None, reviewed=None, yanked=None
        )
        cand.notes.append(
            f"This is not the revision the store '{store_name}' lists ({listed}): it is "
            "installed as an unlisted, unreviewed pack."
        )
    return cand


# ---------------------------------------------------------------------------
# The consent card
# ---------------------------------------------------------------------------


def _file_rows(root: Path) -> list[tuple[str, int]]:
    rows = []
    for rel in tree_files(root):
        try:
            rows.append((rel, (root / rel).stat().st_size))
        except OSError:
            rows.append((rel, 0))
    return rows


def _file_diff(root: Path, old: Mapping[str, str]) -> tuple[list[str], list[str], list[str]]:
    new = {rel: file_sha256(root / rel) for rel in tree_files(root)}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(r for r in set(new) & set(old) if new[r] != old[r])
    return added, removed, changed


def _card(
    cand: _Candidate,
    stage: Path,
    manifest: Mapping[str, Any],
    tree: str,
    *,
    action: str = "Install",
    previous: Mapping[str, Any] | None = None,
) -> None:
    """Print what is about to be installed (rich)."""
    from rich.markup import escape
    from rich.table import Table

    con = ui.console()
    name = manifest["name"]
    version = manifest.get("version") or "?"
    con.print()
    con.rule(f"[bold]{action} pack '{escape(name)}' {escape(version)}[/]")
    if manifest.get("title"):
        con.print(f"[bold]{escape(str(manifest['title']))}[/]")
    if manifest.get("description"):
        con.print(escape(str(manifest["description"])))
    info = Table.grid(padding=(0, 2))
    info.add_column(style="dim")
    info.add_column()
    if cand.store:
        reviewed = f"reviewed {cand.reviewed}" if cand.reviewed else "[yellow]not reviewed yet[/]"
        info.add_row("store", f"{escape(cand.store)} ({reviewed})")
    else:
        info.add_row("store", "[yellow]not listed in any store (unlisted)[/]")
    info.add_row("source", escape(describe_source(cand.source)))
    info.add_row("rev", escape(cand.rev or "(a local folder: no commit)"))
    check = "matches the store index" if cand.tree_sha256 else "computed"
    info.add_row("tree", f"{tree} [dim]({check})[/]")
    if manifest.get("license"):
        info.add_row("license", escape(str(manifest["license"])))
    else:
        info.add_row("license", "[yellow]none declared[/]")
    if manifest.get("homepage"):
        info.add_row("homepage", escape(str(manifest["homepage"])))
    con.print(info)
    if cand.yanked:
        why = cand.yanked if isinstance(cand.yanked, str) else "no reason given"
        con.print(f"[bold red]YANKED by the store:[/] {escape(str(why))}")
    for note in cand.notes:
        con.print(f"[yellow]{escape(note)}[/]")
    if previous:
        old_v = previous.get("version") or "?"
        old_r = str(previous.get("rev") or previous.get("tree_sha256") or "?")[:12]
        new_r = str(cand.rev or tree)[:12]
        con.print(f"[bold]Update:[/] {escape(old_v)} ({old_r}) -> {escape(version)} ({new_r})")
        added, removed, changed = _file_diff(stage, previous.get("files") or {})
        for label, items, style in (
            ("added", added, "green"),
            ("removed", removed, "red"),
            ("changed", changed, "yellow"),
        ):
            if items:
                con.print(f"  [{style}]{label}:[/] {escape(', '.join(items))}")
        if not (added or removed or changed):
            con.print("  [dim]no file changes[/]")

    files = Table(title="Files", title_justify="left", show_edge=False, pad_edge=False)
    files.add_column("path")
    files.add_column("size", justify="right")
    rows = _file_rows(stage)
    for rel, size in rows:
        files.add_row(escape(rel), ui.human_size(size))
    con.print(files)

    scans = scan_tree(stage)
    code = [s for s in scans if not s.path.startswith(("tests/", "data/"))]
    if not code:
        con.print("[green]This pack contains no code (presets only).[/]")
    else:
        _print_scan(code)
    caps: dict[str, list[str]] = {}
    for mod in sorted(p.stem for p in stage.glob("*.py") if not p.name.startswith("_")):
        meta = module_metadata(stage / f"{mod}.py") or {}
        for cap in meta.get("requires") or []:
            caps.setdefault(str(cap), []).append(mod)
    reqs = dict(manifest.get("requires") or {})
    if reqs:
        unmet = _requires_ok(reqs)
        text = ", ".join(f"{k} {v}" for k, v in reqs.items())
        con.print(f"[dim]requires:[/] {escape(text)}" + (" [red](not met)[/]" if unmet else ""))
    if caps:
        con.print(
            "[dim]capabilities:[/] "
            + "; ".join(f"{escape(c)} ({escape(', '.join(m))})" for c, m in caps.items())
        )
    deps = list(manifest.get("dependencies") or [])
    if deps:
        missing = missing_dependencies(deps)
        con.print(f"[dim]dependencies:[/] {escape(', '.join(deps))}")
        if missing:
            con.print(
                f"[yellow]Missing (pytacheck never installs them):[/] {escape(_pip_command(missing))}"
            )
    con.print(
        "[dim]Installed packs run with your permissions, like any Python package "
        "(there is no sandbox).[/]"
    )


def _print_scan(scans: list[FileScan]) -> None:
    from rich.markup import escape
    from rich.table import Table

    con = ui.console()
    table = Table(title="Imports and calls", title_justify="left", show_edge=False)
    table.add_column("file")
    table.add_column("imports")
    for s in scans:
        if s.error:
            table.add_row(escape(s.path), f"[red]does not parse: {escape(s.error)}[/]")
            continue
        risky = {name for name, _ in s.risky}
        shown = [
            f"[bold red]{escape(i)}[/]" if i in risky else escape(i)
            for i in s.imports
            if not i.startswith("pytacheck.") or i in risky
        ]
        calls = [f"[bold red]{escape(n)}[/]" for n, _ in s.risky if n.endswith("()")]
        table.add_row(escape(s.path), ", ".join(shown + calls) or "[dim]-[/]")
    con.print(table)
    flagged = [(s.path, n, why) for s in scans for n, why in s.risky]
    if flagged:
        con.print("[bold yellow]Look closely at:[/]")
        for path, name, why in flagged:
            con.print(f"  [red]{escape(name)}[/] in {escape(path)}: {escape(why)}")


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def _write_pin(name: str, pin: Mapping[str, Any], scope: str) -> Path:
    def edit(cfg: dict[str, Any]) -> None:
        packs = cfg.get("packs")
        if not isinstance(packs, dict):
            packs = cfg["packs"] = {}
        packs[name] = dict(pin)

    return update_config(scope, edit)


def _pin_for(cand: _Candidate, tree: str, version: str | None) -> dict[str, Any]:
    pin: dict[str, Any] = {"source": dict(cand.source)}
    if cand.rev:
        pin["rev"] = cand.rev
    pin["tree_sha256"] = tree
    if version:
        pin["version"] = version
    if cand.store:
        pin["store"] = cand.store
    return pin


def _installed(name: str, pin: Mapping[str, Any]) -> Pack:
    from pytacheck.packs.registry import _installed_pack

    try:
        pack = get_pack(name)
        if pack.kind == "installed" and pack.root == install_dir(name, pin):
            return pack
    except PackError:
        pass
    return _installed_pack(name, pin, origin="install")


def _install(
    cand: _Candidate,
    *,
    scope: str | None,
    yes: bool,
    action: str = "Install",
    previous: Mapping[str, Any] | None = None,
) -> Pack:
    """Fetch, verify, confirm, move into place, record and (with *scope*) pin."""
    _check_source(cand.source)
    if cand.name and cand.rev:
        existing = install_dir(cand.name, {"rev": cand.rev})
        if _record_matches(existing, cand.rev, cand.tree_sha256):
            done = _read_record(existing)
            manifest = read_manifest(existing)
            pin = _pin_for(cand, done["tree_sha256"], manifest.get("version"))
            if scope is not None:
                _write_pin(cand.name, pin, scope)
                ui.console().print(
                    f"The pack '{cand.name}' ({cand.rev[:12]}) is already installed; pinned it."
                )
            return _installed(cand.name, pin)
    root = data_dir()
    created = [p for p in (root, root / "packs", root / "packs" / ".staging") if not p.exists()]
    staging = root / "packs" / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="install-", dir=staging))
    final: Path | None = None
    try:
        stage = tmp / "pack"
        fetch_source(cand.source, cand.rev, stage, base=cand.base)
        manifest = read_manifest(stage)
        name = manifest["name"]
        if cand.name and name != cand.name:
            raise PackError(f"The fetched pack is named '{name}', not '{cand.name}'")
        validate_pack_name(name)
        tree = tree_sha256(stage)
        if cand.tree_sha256 and tree != cand.tree_sha256:
            raise PackError(
                f"Refusing to install '{name}': its tree hash {tree} does not match the "
                f"{'store index' if cand.store else 'pin'} ({cand.tree_sha256}). The source "
                "changed after it was listed, or the download was tampered with."
            )
        if cand.yanked and yes:
            warnings.warn(f"The pack '{name}' was yanked by its store: {cand.yanked}", stacklevel=3)
        _card(cand, stage, manifest, tree, action=action, previous=previous)
        if not yes and not ui.confirm(f"{action} '{name}'?"):
            raise Cancelled(f"{action} of '{name}' cancelled; nothing was changed")
        pin = _pin_for(cand, tree, manifest.get("version"))
        final = install_dir(name, pin)
        record: dict[str, Any] = {
            "source": dict(cand.source),
            "rev": cand.rev,
            "tree_sha256": tree,
            "installed": _now(),
            "store": cand.store,
        }
        if cand.reviewed:
            record["reviewed"] = cand.reviewed
        record["files"] = {rel: file_sha256(stage / rel) for rel in tree_files(stage)}
        (stage / INSTALL_RECORD).write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for folder, _dirs, files in os.walk(stage):
            for f in files:
                os.chmod(os.path.join(folder, f), 0o444)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            os.replace(final, tmp / "previous")  # an incomplete or modified earlier install
        os.replace(stage, final)
    except BaseException:
        _cleanup(tmp, created)
        raise
    _cleanup(tmp, created)
    if scope is not None:
        where = _write_pin(name, pin, scope)
        ui.console().print(f"Installed '{name}' into {final} and pinned it in {where}")
    else:
        ui.console().print(f"Installed '{name}' into {final}")
    missing = missing_dependencies(manifest.get("dependencies"))
    if missing:
        ui.console().print(
            f"[yellow]'{name}' needs packages that are not installed:[/] {_pip_command(missing)}"
        )
    return _installed(name, pin)


def _cleanup(tmp: Path, created: list[Path]) -> None:
    _rmtree(tmp)
    for folder in reversed(created):
        with contextlib.suppress(OSError):
            folder.rmdir()  # only if empty: leaves no trace of a cancelled install
    staging = tmp.parent
    with contextlib.suppress(OSError):
        staging.rmdir()


def _install_path(ref: str, *, scope: str, yes: bool) -> Pack:
    root = Path(ref).expanduser().resolve()
    if not root.is_dir():
        raise PackError(f"{root} is not a folder")
    if (root / "pack.json").is_file():
        name = read_manifest(root)["name"]
    else:
        try:
            name = validate_pack_name(root.name)
        except PackError:
            raise PackError(
                f"{root} has no pack.json and its folder name is not a valid pack name; "
                "add a pack.json (pytacheck pack new) or rename the folder"
            ) from None
        validate_manifest({"name": name}, where=str(root))
    target = config_path(scope)
    value = str(root)
    project_dir = target.parent.resolve()
    if scope == "project" and root.is_relative_to(project_dir.parent):
        # under or next to the project: a relative path keeps the shared lock file
        # working for teammates and when the project folder moves
        value = Path(os.path.relpath(root, project_dir)).as_posix()
    _local_card(name, root)
    if not yes and not ui.confirm(f"Add the path pack '{name}' to {target}?"):
        raise Cancelled(f"Adding '{name}' cancelled; nothing was changed")
    where = _write_pin(name, {"path": value}, scope)
    if scope == "project":
        from pytacheck.config import trust_local

        trust_local([root])  # the user just agreed to run it
    ui.console().print(f"Pinned the path pack '{name}' in {where}")
    try:
        return get_pack(name)
    except PackError:
        from pytacheck.packs.registry import _path_pack

        return _path_pack(name, {"path": str(root)}, str(where))


def _local_card(name: str, root: Path) -> None:
    """What a path pack is, before the user agrees to run it."""
    con = ui.console()
    con.print()
    con.rule(f"[bold]Use the local folder as pack '{name}'[/]")
    con.print(f"folder: {root}\ntrust: local (live: edits take effect at once, nothing is pinned)")
    code = [s for s in scan_tree(root) if not s.path.startswith(("tests/", "data/"))]
    if code:
        _print_scan(code)


def _trust_project_code(config: Any, *, yes: bool) -> int:
    """Ask about the local code a project config names and the user has not trusted yet.

    Returns how many entries were asked about; declined ones stay inactive.
    """
    from rich.markup import escape

    from pytacheck.config import trust_local

    asked = 0
    for key, (where, _entry, code) in config.untrusted.items():
        section, _, name = key.partition(".")
        asked += 1
        if section == "packs":
            _local_card(name, Path(code[0]))
            question = f"Trust the path pack '{name}' named by {where}?"
        else:
            con = ui.console()
            con.print()
            con.rule(f"[bold]Local modules in the preset '{escape(name)}'[/]")
            con.print(f"project config: {escape(where)}")
            for file in code:
                con.print(f"  {escape(file)}")
            scans = [scan_file(f, rel=f) for f in code if Path(f).is_file()]
            if scans:
                _print_scan(scans)
            question = f"Trust the local modules of the preset '{name}' in {where}?"
        if yes or ui.confirm(question):
            trust_local(code)
            ui.console().print(f"Trusted: {', '.join(code)}")
        else:
            ui.console().print(f"[yellow]Not trusted: '{escape(name)}' stays inactive[/]")
    return asked


def _sync(*, yes: bool) -> list[Pack]:
    config = load_config()
    done: list[Pack] = []
    problems: list[str] = []
    todo = _trust_project_code(config, yes=yes)
    for name, pin in config.packs.items():
        if not isinstance(pin, Mapping) or "path" in pin:
            continue
        try:
            validate_pack_name(name)
            target = install_dir(name, pin)
            if _record_matches(target, pin.get("rev"), pin.get("tree_sha256")):
                continue
            todo += 1
            source = _source_without_rev(pin.get("source"))
            if not source:
                raise PackError(f"The pin of '{name}' has no source to install from")
            cand = _Candidate(
                name=name,
                source=source,
                rev=pin.get("rev"),
                tree_sha256=pin.get("tree_sha256"),
                version=pin.get("version"),
                store=pin.get("store"),
            )
            _enrich_from_store(cand)
            done.append(_install(cand, scope=None, yes=yes))
        except PackError as exc:
            problems.append(f"{name}: {exc}")
    if not todo:
        ui.console().print("Every pinned pack is installed.")
    if problems:
        raise PackError("Some pinned packs were not installed:\n" + "\n".join(problems))
    return done


def _enrich_from_store(cand: _Candidate) -> None:
    """Add the store's review date / yanked notice for the pinned revision (best effort)."""
    if not cand.store or not cand.name:
        return
    from pytacheck.packs.stores import find_entry

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, entry = find_entry(cand.name, store=cand.store)
    except PackError:
        return
    source = _source_without_rev(entry.get("source"))
    if "path" in source:
        try:
            source["path"] = str(
                _store_path(cand.store, cand.name, source["path"], entry.get("_location"))
            )
        except PackError:
            return
    same = (
        (entry.get("source") or {}).get("rev") == cand.rev
        and source == cand.source
        and entry.get("tree_sha256") in (None, cand.tree_sha256)
    )
    if same:  # the store lists exactly this pin: show its review and any yank
        cand.reviewed = entry.get("reviewed")
        cand.yanked = entry.get("yanked")
        cand.base = cand.base or entry.get("_location")


def pack_install(ref: str | None = None, *, scope: str = "user", yes: bool = False) -> Any:
    """Install a pack and pin it in the *scope* config (``"user"`` or ``"project"``).

    Returns the installed :class:`~pytacheck.packs.manifest.Pack`, or with no
    *ref* the list of packs installed to satisfy existing pins. Without
    ``yes=True`` a consent card is shown and the user is asked ``[y/N]``;
    :class:`Cancelled` is raised when they decline (or cannot be asked).
    """
    if scope not in ("user", "project"):
        raise ValueError(f"scope must be 'user' or 'project', not {scope!r}")
    if ref is None:
        return _sync(yes=yes)
    kind, info = _parse_ref(str(ref))
    if kind == "path":
        return _install_path(info, scope=scope, yes=yes)
    config_path(scope)  # fail before downloading when config cannot be written
    if kind == "url":
        source, sub_ref = _url_source(info)
        rev = resolve_rev(source, sub_ref)
        cand = _Candidate(name=None, source=source, rev=rev)
        cand.notes.append("This pack is not listed in any store (unlisted, not reviewed).")
    else:
        cand = _store_candidate(*info)
    return _install(cand, scope=scope, yes=yes)


# ---------------------------------------------------------------------------
# Remove, update
# ---------------------------------------------------------------------------


def pack_remove(name: str, *, scope: str = "user", keep_files: bool = False) -> Path:
    """Unpin a pack from the *scope* config and delete its installed files.

    A pip-installed (dist) pack cannot be uninstalled here: it is hidden with
    ``false`` instead. Returns the config file that was changed.
    """
    config = load_config()
    pin = config.packs.get(name)
    target = config_path(scope)
    src = config.source(f"packs.{name}")
    dist = False
    if pin is None:
        try:
            dist = get_pack(name).kind == "dist"
        except PackError:
            dist = False
        if not dist:
            raise PackError(f"The pack '{name}' is not pinned in any config file")
    elif src is not None and src[1] != str(target):
        raise PackError(
            f"The pack '{name}' is pinned in {src[1]}, not in {target}"
            + (" (use --project)" if src[0] == "project" else "")
        )

    def edit(cfg: dict[str, Any]) -> None:
        packs = cfg.get("packs")
        if not isinstance(packs, dict):
            packs = cfg["packs"] = {}
        if dist:
            packs[name] = False
        else:
            packs.pop(name, None)

    path = update_config(scope, edit)
    if isinstance(pin, Mapping) and "path" not in pin and not keep_files:
        try:
            folder = install_dir(name, pin)
        except PackError:
            folder = None
        still = load_config().packs.get(name)
        keep = (
            isinstance(still, Mapping)
            and "path" not in still
            and pin_rev12(still) == (pin_rev12(pin))
        )
        if folder is not None and folder.is_dir() and not keep:
            _rmtree(folder)
            with contextlib.suppress(OSError):
                folder.parent.rmdir()
    ui.console().print(f"Removed the pack '{name}' from {path}")
    return path


def pack_update(name: str | None = None, *, yes: bool = False) -> list[dict[str, Any]]:
    """Re-pin packs to their newest listed (or default-branch) revision, after confirmation.

    Shows the version and rev change and a file-level diff summary. Store
    packs follow their store's index; unlisted git packs follow their default
    branch; path and dist packs are not updated. Returns one row per pack:
    ``name``, ``from``, ``to`` and ``updated`` (plus ``status`` ``"cancelled"``
    or ``"error"`` for a pack that was not updated because of that).
    """
    config = load_config()
    if name is not None:
        pin = config.packs.get(name)
        if not isinstance(pin, Mapping):
            raise PackError(f"The pack '{name}' is not pinned (install it first)")
        if "path" in pin:
            raise PackError(f"'{name}' is a path pack: it is live, there is nothing to update")
        names = [name]
    else:
        names = [n for n, p in config.packs.items() if isinstance(p, Mapping) and "path" not in p]
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for n in names:
        pin = config.packs[n]
        scope = (config.source(f"packs.{n}") or ("user", ""))[0]
        scope = scope if scope in ("user", "project") else "user"
        old_rev = pin.get("rev") or pin.get("tree_sha256")
        try:
            if pin.get("store"):
                cand = _store_candidate(pin["store"], n, None, refresh=True)
            else:
                source = _source_without_rev(pin.get("source"))
                _check_source(source)  # before git or the API sees it
                cand = _Candidate(name=n, source=source, rev=resolve_rev(source, None))
                cand.notes.append("Unlisted pack: updating to its default branch.")
            same = (cand.rev and cand.rev == pin.get("rev")) or (
                not cand.rev and cand.tree_sha256 == pin.get("tree_sha256")
            )
            if same:
                ui.console().print(f"'{n}' is up to date ({str(old_rev)[:12]})")
                rows.append({"name": n, "from": old_rev, "to": old_rev, "updated": False})
                continue
            old_root = install_dir(n, pin)
            record = _read_record(old_root)
            files = record.get("files") or (
                {rel: file_sha256(old_root / rel) for rel in tree_files(old_root)}
                if old_root.is_dir()
                else {}
            )
            previous = {
                "version": pin.get("version"),
                "rev": pin.get("rev"),
                "tree_sha256": pin.get("tree_sha256"),
                "files": files,
            }
            pack = _install(cand, scope=scope, yes=yes, action="Update", previous=previous)
            rows.append({"name": n, "from": old_rev, "to": pack.rev or pack.tree_sha256,
                         "updated": True})  # fmt: skip
        except PackError as exc:
            if name is not None:
                raise
            problems.append(f"{n}: {exc}")
            status = "cancelled" if isinstance(exc, Cancelled) else "error"
            rows.append(
                {"name": n, "from": old_rev, "to": None, "updated": False, "status": status}
            )
    for p in problems:
        warnings.warn(p, stacklevel=2)
    return rows


# ---------------------------------------------------------------------------
# List, show
# ---------------------------------------------------------------------------


def pack_list() -> pd.DataFrame:
    """Active and configured packs: ``name``, ``version``, ``kind``, ``trust``, ``rev``,
    ``store``, ``reviewed``, ``modules``, ``presets``, ``defined_in`` and ``status``."""
    import pandas as pd

    reg = registry()
    rows: list[tuple[Any, ...]] = []
    for pack in reg.packs.values():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            modified = integrity(pack)
        status = f"modified: {', '.join(modified)}" if modified else "ok"
        rows.append(
            (
                pack.name,
                pack.version,
                pack.kind,
                pack.trust,
                (pack.rev or "")[:12] or None,
                pack.store,
                pack.reviewed,
                len(pack.modules()),
                len(pack.presets),
                pack.origin or ("builtin" if pack.kind == "builtin" else pack.source.get("dist")),
                status,
            )
        )
    config = load_config()
    for name, problem in reg.problems.items():
        where = (config.source(f"packs.{name}") or ("", None))[1]
        rows.append((name, None, None, None, None, None, None, None, None, where, problem))
    cols = [
        "name", "version", "kind", "trust", "rev", "store", "reviewed", "modules", "presets",
        "defined_in", "status",
    ]  # fmt: skip
    df = pd.DataFrame(rows, columns=cols)
    for col in cols:
        df[col] = df[col].astype("Int64" if col in ("modules", "presets") else "string")
    return df


def _module_rows(root: Path, names: list[str]) -> list[dict[str, Any]]:
    out = []
    for n in names:
        meta = module_metadata(root / f"{n}.py") or {"name": n}
        out.append(
            {
                "name": n,
                "title": meta.get("title") or "",
                "description": meta.get("description") or "",
                "section": meta.get("section") or "general",
                "requires": list(meta.get("requires") or []),
                "validation": validation_metrics(meta.get("validation")) or None,
                "validated_text": "<validation>" in (meta.get("details") or ""),
            }
        )
    return out


def pack_show(name: str) -> dict[str, Any]:
    """Everything about a pack, read without importing its code.

    An active pack is described from its folder; a pack that is only listed
    by a store is described from the store index (``installed: False``).
    """
    try:
        pack: Pack | None = get_pack(name)
    except PackError:
        pack = None
    if pack is None:
        from pytacheck.packs.stores import find_entry

        store, entry = find_entry(name)
        entry = {k: v for k, v in entry.items() if not k.startswith("_")}
        deps = list(entry.get("dependencies") or [])
        missing = missing_dependencies(deps)
        return {
            **entry,
            "store": store,
            "installed": False,
            "missing_dependencies": missing,
            "pip": _pip_command(missing) if missing else None,
        }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        modified = integrity(pack)
    deps = list(pack.dependencies)
    missing = missing_dependencies(deps)
    manifest = dict(pack.manifest)
    return {
        "name": pack.name,
        "version": pack.version,
        "title": pack.title,
        "description": pack.description,
        "installed": True,
        "kind": pack.kind,
        "trust": pack.trust,
        "store": pack.store,
        "reviewed": pack.reviewed,
        "source": dict(pack.source),
        "rev": pack.rev,
        "tree_sha256": pack.tree_sha256,
        "root": str(pack.root),
        "defined_in": pack.origin,
        "license": manifest.get("license"),
        "homepage": manifest.get("homepage"),
        "authors": list(manifest.get("authors") or []),
        "fields": list(pack.fields),
        "keywords": list(pack.keywords),
        "requires": dict(pack.requires),
        "dependencies": deps,
        "missing_dependencies": missing,
        "pip": _pip_command(missing) if missing else None,
        "modules": _module_rows(pack.root, pack.modules()),
        "presets": {k: v.get("description", "") for k, v in pack.presets.items()},
        "modified": modified,
    }
