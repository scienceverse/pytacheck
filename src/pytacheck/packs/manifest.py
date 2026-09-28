"""Pack manifests (``pack.json``) and the :class:`Pack` record.

A pack is a folder holding ``pack.json`` and ordinary ``@module`` files,
laid out like ``src/pytacheck/modules/``: every ``*.py`` in the root whose
name does not start with ``_`` is a module, ``_*.py`` files are helpers,
and everything else (``data/``, ``tests/``, ``.R`` files...) is ignored by
the loader. See ``docs/design/module-system-v2.md``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytacheck.module import ModuleError

__all__ = [
    "FIELDS",
    "MODULE_NAME_RE",
    "PACK_NAME_RE",
    "PRESET_KEYS",
    "RESERVED_PACK_NAMES",
    "RESERVED_PACK_PREFIXES",
    "Pack",
    "PackError",
    "read_manifest",
    "validate_manifest",
    "validate_pack_name",
    "validate_preset",
    "validation_metrics",
]

SCHEMA = 1
PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
MODULE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PRESET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
RESERVED_PACK_NAMES = frozenset({"metacheck", "local", "pytacheck", "modules"})
#: No pack name may start with these (ignoring ``-`` and ``_``), so none looks official.
RESERVED_PACK_PREFIXES = ("official", "scienceverse")
#: Controlled vocabulary for ``fields`` (the store may extend it).
FIELDS = (
    "general",
    "psychology",
    "medicine",
    "neuroscience",
    "economics",
    "education",
    "biology",
    "ecology",
    "sociology",
    "political-science",
    "computer-science",
    "physics",
)
PRESET_KEYS = frozenset(
    {"description", "extends", "exclude", "replace", "modules", "args", "dependencies"}
)
_STR_FIELDS = ("version", "title", "description", "license", "homepage")
_LIST_FIELDS = ("authors", "fields", "keywords", "dependencies")


class PackError(ModuleError):
    """A pack is missing, malformed, not installed or not allowed."""


def validate_pack_name(name: Any, *, allow_reserved: bool = False) -> str:
    """Check a pack name against ``^[a-z][a-z0-9_-]{1,39}$`` and the reserved names and prefixes."""
    if not isinstance(name, str) or not PACK_NAME_RE.match(name):
        raise PackError(
            f"Invalid pack name {name!r}: use 2-40 lowercase letters, digits, '_' or '-', "
            "starting with a letter"
        )
    if allow_reserved:
        return name
    if name in RESERVED_PACK_NAMES:
        raise PackError(f"The pack name '{name}' is reserved")
    bare = name.replace("-", "").replace("_", "")
    for prefix in RESERVED_PACK_PREFIXES:
        if bare.startswith(prefix):
            raise PackError(
                f"The pack name '{name}' is reserved: no pack name may start with '{prefix}' "
                "(ignoring '-' and '_'), so that none looks official"
            )
    return name


def _str_list(value: Any, what: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PackError(f"{what} must be a list of strings")
    return list(value)


def validate_preset(name: str, preset: Any, *, where: str = "preset") -> dict[str, Any]:
    """Check one preset definition and return a normalised copy.

    Keys: ``description``, ``extends``, ``exclude``, ``modules`` and
    ``dependencies`` (lists of strings; a single string is accepted),
    ``replace`` (label -> ref) and ``args`` (module -> dict of arguments).
    """
    what = f"{where} '{name}'"
    if not isinstance(name, str) or not PRESET_NAME_RE.match(name):
        raise PackError(f"Invalid preset name {name!r} in {where}")
    if not isinstance(preset, Mapping):
        raise PackError(f"{what} must be an object")
    unknown = sorted(set(preset) - PRESET_KEYS)
    if unknown:
        raise PackError(f"{what} has unknown keys {unknown}; allowed: {sorted(PRESET_KEYS)}")
    out: dict[str, Any] = {}
    desc = preset.get("description", "")
    if not isinstance(desc, str):
        raise PackError(f"{what}: description must be a string")
    out["description"] = desc
    for key in ("extends", "exclude", "modules", "dependencies"):
        out[key] = _str_list(preset.get(key, []), f"{what}: {key}")
    replace = preset.get("replace", {})
    if not isinstance(replace, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in replace.items()
    ):
        raise PackError(f"{what}: replace must map module names to module refs")
    out["replace"] = dict(replace)
    args = preset.get("args", {})
    if not isinstance(args, Mapping) or not all(
        isinstance(k, str) and isinstance(v, Mapping) for k, v in args.items()
    ):
        raise PackError(f"{what}: args must map module names to objects of arguments")
    out["args"] = {k: dict(v) for k, v in args.items()}
    return out


def validate_manifest(
    data: Any, *, where: str = "pack.json", builtin: bool = False
) -> dict[str, Any]:
    """Check a parsed ``pack.json`` and return a normalised copy."""
    if not isinstance(data, Mapping):
        raise PackError(f"{where} must hold a JSON object")
    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise PackError(f"{where} uses schema {schema!r}; this pytacheck reads schema {SCHEMA}")
    out = dict(data)
    out["schema"] = SCHEMA
    try:
        out["name"] = validate_pack_name(data.get("name"), allow_reserved=builtin)
    except PackError as exc:
        raise PackError(f"{where}: {exc}") from None
    for key in _STR_FIELDS:
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise PackError(f"{where}: {key} must be a string")
    for key in _LIST_FIELDS:
        out[key] = _str_list(data.get(key, []), f"{where}: {key}")
    requires = data.get("requires", {})
    if not isinstance(requires, Mapping) or not all(isinstance(v, str) for v in requires.values()):
        raise PackError(f"{where}: requires must map names to version specifiers")
    out["requires"] = dict(requires)
    presets = data.get("presets", {})
    if not isinstance(presets, Mapping):
        raise PackError(f"{where}: presets must be an object")
    out["presets"] = {k: validate_preset(k, v, where=f"{where} preset") for k, v in presets.items()}
    return out


def read_manifest(path: str | os.PathLike[str], *, builtin: bool = False) -> dict[str, Any]:
    """Read and validate ``pack.json`` (*path* may be the file or the pack folder)."""
    p = Path(path)
    if p.is_dir():
        p = p / "pack.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackError(f"No pack.json in {p.parent}") from None
    except (OSError, ValueError) as exc:
        raise PackError(f"Cannot read {p}: {exc}") from exc
    return validate_manifest(data, where=str(p), builtin=builtin)


def validation_metrics(validation: Mapping[str, Any] | None) -> dict[str, Any]:
    """A module's ``validation=`` record plus derived ``ppv`` and ``sensitivity``."""
    if not validation:
        return {}
    out = dict(validation)
    tp, fp, fn = (validation.get(k) for k in ("tp", "fp", "fn"))
    if isinstance(tp, int | float) and isinstance(fp, int | float) and tp + fp > 0:
        out.setdefault("ppv", round(tp / (tp + fp), 3))
    if isinstance(tp, int | float) and isinstance(fn, int | float) and tp + fn > 0:
        out.setdefault("sensitivity", round(tp / (tp + fn), 3))
    return out


@dataclass(frozen=True, eq=False)
class Pack:
    """An active (or loadable) pack.

    ``kind`` is how it is loaded: ``builtin``, ``installed`` (store and
    git/URL packs under ``<data>/packs``), ``path`` or ``dist``. ``trust``
    is the label shown to users: ``builtin``, ``store``, ``unlisted``,
    ``local`` or ``dist``.
    """

    name: str
    root: Path
    kind: str
    trust: str
    version: str | None = None
    source: Mapping[str, Any] = field(default_factory=dict)
    title: str = ""
    description: str = ""
    fields: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    presets: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    requires: Mapping[str, str] = field(default_factory=dict)
    reviewed: str | None = None
    rev: str | None = None
    tree_sha256: str | None = None
    store: str | None = None
    #: config file that pins the pack (``None`` for built-in and dist packs)
    origin: str | None = None
    #: Python package its modules are imported under (``pytacheck_packs.<key>``,
    #: ``pytacheck.modules`` or a dist package)
    package: str = ""
    #: installed files -> sha256, from ``.pytacheck-install.json``
    files: Mapping[str, str] = field(default_factory=dict)
    manifest: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any], **kwargs: Any) -> Pack:
        """Build a pack from a validated manifest plus loader details."""
        kwargs.setdefault("name", manifest["name"])
        kwargs.setdefault("version", manifest.get("version"))
        return cls(
            title=manifest.get("title") or "",
            description=manifest.get("description") or "",
            fields=tuple(manifest.get("fields", ())),
            keywords=tuple(manifest.get("keywords", ())),
            presets=dict(manifest.get("presets", {})),
            dependencies=tuple(manifest.get("dependencies", ())),
            requires=dict(manifest.get("requires", {})),
            manifest=dict(manifest),
            **kwargs,
        )

    @property
    def key(self) -> str:
        """The synthetic package key (``<name>_<rev12>`` or ``local_<sha8>``)."""
        return self.package.rsplit(".", 1)[-1]

    def modules(self) -> list[str]:
        """Module names: the pack root's ``*.py`` files not starting with ``_``."""
        if self.kind == "builtin":
            from pytacheck.module import _builtin_names

            return list(_builtin_names())
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        return sorted(
            n[:-3]
            for n in names
            if n.endswith(".py") and not n.startswith("_") and MODULE_NAME_RE.match(n[:-3])
        )

    def has_module(self, name: str) -> bool:
        if self.kind == "builtin":
            return name in self.modules()
        return MODULE_NAME_RE.match(name) is not None and (self.root / f"{name}.py").is_file()

    def module_path(self, name: str) -> Path:
        return self.root / f"{name}.py"

    def __repr__(self) -> str:
        rev = f"@{self.rev[:12]}" if self.rev else ""
        return f"<Pack {self.name} {self.version or ''}{rev} ({self.trust}) {self.root}>"
