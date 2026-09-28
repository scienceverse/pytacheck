"""Helpers for the module system v2 tests: module sources and pack builders."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pytacheck.config import update_config
from pytacheck.packs.tree import INSTALL_RECORD, file_sha256, tree_files, tree_sha256

REV_A = "a" * 40
REV_B = "b" * 40


def mod_src(
    name: str,
    text: str = "ok",
    *,
    keywords: tuple[str, ...] = ("results",),
    requires: tuple[str, ...] = (),
    header: str = "",
    body: str | None = None,
    args: str = "x=1",
) -> str:
    """Source of a small @module file; the default body reports *text* and its args."""
    body = body or f'return {{"summary_text": "{text}", "table": None, "x": x}}'
    return textwrap.dedent(
        f'''\
        from pytacheck.module import module, get_prev_outputs
        {header}

        @module(title="{name.title()}", description="The {name} module",
                keywords={list(keywords)!r}, requires={list(requires)!r})
        def {name}(paper, {args}):
            {body}
        '''
    )


class ModSys:
    """Helpers bound to one test's temporary config file, data dir and cwd."""

    def __init__(self, root: Path, config: Path, data: Path, work: Path) -> None:
        self.root, self.config_file, self.data, self.work = root, config, data, work

    def config(self, data: Mapping[str, Any]) -> None:
        update_config("user", lambda _: dict(data))

    def pin(self, name: str, pin: Any) -> None:
        def edit(cfg: dict[str, Any]) -> None:
            cfg.setdefault("packs", {})[name] = pin

        update_config("user", edit)

    def pack(
        self,
        folder: Path,
        name: str,
        modules: Mapping[str, str] | None = None,
        *,
        presets: Mapping[str, Any] | None = None,
        files: Mapping[str, str] | None = None,
        **manifest: Any,
    ) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        data = {"schema": 1, "name": name, "version": "1.0.0", **manifest}
        if presets is not None:
            data["presets"] = dict(presets)
        (folder / "pack.json").write_text(json.dumps(data, indent=2))
        for mod, src in (modules or {}).items():
            (folder / f"{mod}.py").write_text(src)
        for rel, text in (files or {}).items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return folder

    def install(
        self,
        name: str,
        modules: Mapping[str, str] | None = None,
        *,
        rev: str = REV_A,
        store: str | None = "pytacheck",
        reviewed: str | None = None,
        pin: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Lay out an installed pack under the data dir (as `pack install` will) and pin it."""
        folder = self.data / "packs" / name / rev[:12]
        self.pack(folder, name, modules, **kwargs)
        tree = tree_sha256(folder)
        record = {
            "source": {"github": f"someone/{name}"},
            "rev": rev,
            "tree_sha256": tree,
            "installed": "2026-09-24T00:00:00Z",
            "store": store,
            "reviewed": reviewed,
            "files": {rel: file_sha256(folder / rel) for rel in tree_files(folder)},
        }
        (folder / INSTALL_RECORD).write_text(json.dumps(record))
        entry = {
            "source": {"github": f"someone/{name}"},
            "rev": rev,
            "tree_sha256": tree,
            "version": kwargs.get("version", "1.0.0"),
        }
        if store:
            entry["store"] = store
        if pin:
            self.pin(name, entry)
        return entry
