"""Package-wide settings: contact email, verbosity, cache/data locations, config files.

Settings come from (highest priority first) values set at runtime with the
functions below, then environment variables, then defaults. Environment
variable names follow metacheck where one exists so the same shell setup
works for both packages.

The module system's JSON config (stores, pack pins, presets) is read by
:func:`load_config`; see ``docs/design/module-system-v2.md``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs

__all__ = [
    "BUILTIN_STORE",
    "BUILTIN_STORE_URL",
    "Config",
    "ConfigError",
    "cache_dir",
    "config_files",
    "config_path",
    "config_stamp",
    "data_dir",
    "email",
    "load_config",
    "project_config_path",
    "update_config",
    "user_config_path",
    "verbose",
]

_state: dict[str, object] = {}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(address: str | None = None) -> str | None:
    """Get or set the contact email sent to polite APIs (Crossref, OpenAlex).

    Reads ``PYTACHECK_EMAIL`` or ``METACHECK_EMAIL`` when unset.
    """
    if address is not None:
        if not _EMAIL_RE.match(address):
            raise ValueError(f"{address!r} does not look like an email address")
        _state["email"] = address
        return address
    value = (
        _state.get("email")
        or os.environ.get("PYTACHECK_EMAIL")
        or os.environ.get("METACHECK_EMAIL")
    )
    return str(value) if value else None


def verbose(value: bool | None = None) -> bool:
    """Get or set whether progress messages are printed (default ``True``)."""
    if value is not None:
        _state["verbose"] = bool(value)
    if "verbose" in _state:
        return bool(_state["verbose"])
    env = os.environ.get("PYTACHECK_VERBOSE")
    return env.lower() not in ("0", "false", "no") if env else True


def cache_dir(subdir: str = "", override: str | os.PathLike[str] | None = None) -> Path:
    """A cache directory (created on demand).

    Root: ``PYTACHECK_CACHE_DIR`` or the platform user cache directory.
    """
    if override is not None:
        path = Path(override)
    else:
        root = os.environ.get("PYTACHECK_CACHE_DIR") or platformdirs.user_cache_dir(
            "pytacheck", "scienceverse"
        )
        path = Path(root) / subdir if subdir else Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Data directory and JSON config files (module system v2)
# ---------------------------------------------------------------------------
#
# Config is JSON. Scopes, lowest precedence first: built-in defaults, the
# user file (``<user_config_dir>/config.json``), the project file (nearest
# ``pytacheck.json`` at or above the working directory). ``PYTACHECK_CONFIG``
# names the only file to read, or ``none`` for no files (hermetic runs).

BUILTIN_STORE = "pytacheck"
BUILTIN_STORE_URL = "https://github.com/thesanogoeffect/pytacheck-modules"
PROJECT_CONFIG = "pytacheck.json"
_SECTIONS = ("stores", "packs", "presets")
_ENV_KEYS = ("PYTACHECK_CONFIG", "PYTACHECK_DATA_DIR", "PYTACHECK_STORE_URL", "PYTACHECK_PRESET")
_config_cache: dict[str, Any] = {}
_writes = 0  # bumped by update_config(): mtime_ns alone can miss rapid rewrites


class ConfigError(ValueError):
    """A config file is malformed or cannot be written."""


def data_dir(subdir: str = "", *, create: bool = False) -> Path:
    """Where installed packs and store indexes live.

    ``PYTACHECK_DATA_DIR`` or ``platformdirs.user_data_dir("pytacheck")``.
    """
    root = Path(os.environ.get("PYTACHECK_DATA_DIR") or platformdirs.user_data_dir("pytacheck"))
    path = root / subdir if subdir else root
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_path() -> Path:
    """The user config file (it may not exist)."""
    return Path(platformdirs.user_config_dir("pytacheck")) / "config.json"


def project_config_path(start: str | os.PathLike[str] | None = None) -> Path | None:
    """The nearest ``pytacheck.json`` at or above *start* (default: the cwd)."""
    here = Path(start) if start is not None else Path.cwd()
    for folder in (here, *here.parents):
        candidate = folder / PROJECT_CONFIG
        if candidate.is_file():
            return candidate
    return None


def config_files() -> list[tuple[str, Path]]:
    """``(scope, path)`` of the config files in effect, lowest precedence first.

    Scopes are ``"user"`` and ``"project"``, or ``"env"`` for the single file
    named by ``PYTACHECK_CONFIG`` (listed even when it does not exist yet).
    """
    env = os.environ.get("PYTACHECK_CONFIG", "").strip()
    if env:
        if env.lower() == "none":
            return []
        return [("env", Path(env).expanduser().absolute())]
    out: list[tuple[str, Path]] = []
    user = user_config_path()
    if user.is_file():
        out.append(("user", user))
    project = project_config_path()
    if project is not None and project != user:
        out.append(("project", project))
    return out


def config_stamp() -> tuple[Any, ...]:
    """A cheap fingerprint of everything config depends on (for cache invalidation)."""
    files = []
    for scope, path in config_files():
        try:
            st = path.stat()
            files.append((scope, str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            files.append((scope, str(path), None, None))
    env = tuple(os.environ.get(k) for k in _ENV_KEYS)
    return (env, os.getcwd(), tuple(files), _writes)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must hold a JSON object")
    return data


def _is_url(value: str) -> bool:
    return "://" in value or value.startswith("git@")


def _resolve_path(value: str, base: Path) -> str:
    p = Path(value).expanduser()
    return str(p if p.is_absolute() else (base / p).resolve())


def _check_section(data: dict[str, Any], path: Path) -> None:
    preset = data.get("preset")
    if preset is not None and not isinstance(preset, str):
        raise ConfigError(f"{path}: 'preset' must be a string or null")
    for section in _SECTIONS:
        value = data.get(section)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: '{section}' must be an object")
        for key, item in value.items():
            if section == "stores" and item is not None and not isinstance(item, str):
                raise ConfigError(f"{path}: stores.{key} must be a URL, a path or null")
            if section == "packs" and item is not None and not isinstance(item, dict | bool):
                raise ConfigError(f"{path}: packs.{key} must be an object, false or null")
            if section == "packs" and item is True:
                raise ConfigError(f"{path}: packs.{key} may be false (to hide a pack), not true")
            if section == "presets" and item is not None and not isinstance(item, dict):
                raise ConfigError(f"{path}: presets.{key} must be an object or null")


def _resolve_entry(section: str, value: Any, base: Path) -> Any:
    """Resolve relative paths in one config entry against its file's folder."""
    if section == "stores":
        return value if _is_url(value) else _resolve_path(value, base)
    if section == "packs" and isinstance(value, dict):
        value = dict(value)
        if isinstance(value.get("path"), str):
            value["path"] = _resolve_path(value["path"], base)
        src = value.get("source")
        if isinstance(src, dict) and isinstance(src.get("path"), str):
            value["source"] = {**src, "path": _resolve_path(src["path"], base)}
        return value
    if section == "presets":
        value = dict(value)

        def fix(ref: Any) -> Any:
            return _resolve_path(ref, base) if isinstance(ref, str) and ref.endswith(".py") else ref

        if isinstance(value.get("modules"), list):
            value["modules"] = [fix(m) for m in value["modules"]]
        if isinstance(value.get("replace"), dict):
            value["replace"] = {k: fix(v) for k, v in value["replace"].items()}
        return value
    return value


@dataclass(frozen=True)
class Config:
    """The merged configuration (see :func:`load_config`)."""

    values: dict[str, Any]
    #: dotted key (``"preset"``, ``"packs.psych"``...) -> ``(scope, location)``
    sources: dict[str, tuple[str, str]]
    files: tuple[tuple[str, Path], ...] = ()
    stamp: tuple[Any, ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def source(self, key: str) -> tuple[str, str] | None:
        """``(scope, location)`` that set *key*, e.g. ``("project", "/x/pytacheck.json")``."""
        return self.sources.get(key)

    @property
    def preset(self) -> str | None:
        return self.values.get("preset")

    @property
    def stores(self) -> dict[str, str]:
        return self.values["stores"]

    @property
    def packs(self) -> dict[str, Any]:
        return self.values["packs"]

    @property
    def presets(self) -> dict[str, dict[str, Any]]:
        return self.values["presets"]


def load_config() -> Config:
    """The effective configuration, cached until :func:`config_stamp` changes.

    Scalars: the higher scope wins. ``stores``, ``packs`` and ``presets`` are
    merged by key; ``null`` removes an entry and ``false`` hides a dist pack.
    Relative paths are resolved against the file that holds them. The store
    ``pytacheck`` is built in; ``PYTACHECK_STORE_URL`` overrides its URL.
    """
    stamp = config_stamp()
    cached = _config_cache.get("config")
    if cached is not None and cached.stamp == stamp:
        return cached  # type: ignore[no-any-return]
    values: dict[str, Any] = {
        "stores": {BUILTIN_STORE: BUILTIN_STORE_URL},
        "packs": {},
        "presets": {},
    }
    sources: dict[str, tuple[str, str]] = {f"stores.{BUILTIN_STORE}": ("builtin", "builtin")}
    files = tuple(config_files())
    for scope, path in files:
        data = _read_json(path)
        _check_section(data, path)
        where = (scope, str(path))
        for key, value in data.items():
            if key in _SECTIONS:
                for name, item in (value or {}).items():
                    if item is None:
                        values[key].pop(name, None)
                        sources.pop(f"{key}.{name}", None)
                    else:
                        values[key][name] = _resolve_entry(key, item, path.parent)
                        sources[f"{key}.{name}"] = where
            elif value is None:
                values.pop(key, None)
                sources.pop(key, None)
            else:
                values[key] = value
                sources[key] = where
    url = os.environ.get("PYTACHECK_STORE_URL")
    if url:
        values["stores"][BUILTIN_STORE] = url
        sources[f"stores.{BUILTIN_STORE}"] = ("env", "PYTACHECK_STORE_URL")
    config = Config(values=values, sources=sources, files=files, stamp=stamp)
    _config_cache["config"] = config
    return config


def config_path(scope: str = "user") -> Path:
    """The file :func:`update_config` writes for *scope* (``"user"`` or ``"project"``)."""
    env = os.environ.get("PYTACHECK_CONFIG", "").strip()
    if env.lower() == "none":
        raise ConfigError("Config files are disabled (PYTACHECK_CONFIG=none)")
    if env:
        return Path(env).expanduser().absolute()
    if scope == "user":
        return user_config_path()
    if scope == "project":
        return project_config_path() or Path.cwd() / PROJECT_CONFIG
    raise ValueError(f"scope must be 'user' or 'project', not {scope!r}")


def update_config(scope: str, fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> Path:
    """Edit one config file atomically and return its path.

    *fn* receives the file's current content (``{}`` when it does not exist)
    and may change it in place or return a replacement. With
    ``PYTACHECK_CONFIG=<file>`` every scope writes that file.
    """
    global _writes
    path = config_path(scope)
    data = _read_json(path)
    result = fn(data)
    if result is not None:
        data = result
    if not isinstance(data, dict):
        raise ConfigError("update_config(): the new config must be a dict")
    _check_section(data, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pytacheck-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _writes += 1
    _config_cache.clear()
    return path
