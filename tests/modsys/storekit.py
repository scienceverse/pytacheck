"""Helpers for store and install tests: tarballs, index entries and a mocked GitHub store."""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pytacheck.packs.build import _pack_fields
from pytacheck.packs.manifest import read_manifest
from pytacheck.packs.tree import tree_files

STORE_REPO = "example/store"
STORE_URL = f"https://github.com/{STORE_REPO}"
INDEX_URL = f"https://raw.githubusercontent.com/{STORE_REPO}/HEAD/index.json"
REV_C = "c" * 40
REV_D = "d" * 40


def codeload(repo: str, rev: str) -> str:
    return f"https://codeload.github.com/{repo}/tar.gz/{rev}"


def tarball(
    files: Mapping[str, str | bytes],
    top: str = "repo-top",
    extra: Iterable[tuple[tarfile.TarInfo, bytes | None]] = (),
) -> bytes:
    """A gzipped tarball with every file under a single top-level folder (as forges make)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root = tarfile.TarInfo(top)
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        for rel, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def dir_files(folder: Path, prefix: str = "") -> dict[str, bytes]:
    """A folder's hashed files as ``{prefix + rel: bytes}``."""
    return {f"{prefix}{rel}": (folder / rel).read_bytes() for rel in tree_files(folder)}


def entry_for(folder: Path, source: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """The index entry ``store build`` would write for a pack folder."""
    entry = _pack_fields(folder, read_manifest(folder))
    entry["source"] = dict(source)
    entry.update({"reviewed": None, "yanked": None})
    entry.update(extra)
    return entry


class FakeStore:
    """A GitHub-hosted store served by respx: ``index.json`` plus commit tarballs."""

    def __init__(self, router: Any, ms: Any) -> None:
        self.router, self.ms = router, ms
        self.entries: dict[str, dict[str, Any]] = {}
        self.trees: dict[str, dict[str, bytes]] = {}  # rev -> repo files
        self.publish()

    def add(
        self,
        name: str,
        modules: Mapping[str, str] | None = None,
        *,
        rev: str = REV_C,
        reviewed: str | None = None,
        yanked: Any = None,
        tamper: Mapping[str, Any] | None = None,
        **pack: Any,
    ) -> dict[str, Any]:
        folder = self.ms.pack(self.ms.root / "src" / rev[:8] / name, name, modules, **pack)
        repo = self.trees.setdefault(rev, {})
        repo.update(dir_files(folder, f"packs/{name}/"))
        self.router.get(codeload(STORE_REPO, rev), name=f"tar-{rev}").respond(
            content=tarball(repo, top=f"store-{rev}")
        )
        source = {"github": STORE_REPO, "rev": rev, "subdir": f"packs/{name}"}
        entry = entry_for(folder, source, reviewed=reviewed, yanked=yanked)
        entry.update(tamper or {})
        self.entries[name] = entry
        self.publish()
        return entry

    def publish(self) -> None:
        index = {"schema": 1, "name": "pytacheck", "packs": list(self.entries.values())}
        self.router.get(INDEX_URL, name="index").respond(json=index)

    def config(self) -> dict[str, Any]:
        try:
            return json.loads(self.ms.config_file.read_text())  # type: ignore[no-any-return]
        except FileNotFoundError:
            return {}
