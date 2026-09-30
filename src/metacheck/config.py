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
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs

from metacheck._env import env_get, env_lookup, env_names

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
    "local_code",
    "project_config_path",
    "trust_local",
    "trusted_local",
    "update_config",
    "user_config_path",
    "verbose",
]

_state: dict[str, object] = {}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(address: str | None = None) -> str | None:
    """Get or set the contact email sent to polite APIs (Crossref, OpenAlex).

    Reads ``METACHECK_EMAIL`` or ``PYTACHECK_EMAIL`` when unset.
    """
    if address is not None:
        if not _EMAIL_RE.match(address):
            raise ValueError(f"{address!r} does not look like an email address")
        _state["email"] = address
        return address
    value = _state.get("email") or env_get("EMAIL")
    return str(value) if value else None


def _as_logical(x: Any) -> bool | None:
    """R ``as.logical()`` of a scalar (``None`` for ``NA``)."""
    if hasattr(x, "item") and not isinstance(x, str | bytes):  # numpy scalar
        try:
            x = x.item()
        except (TypeError, ValueError):
            return None
    if isinstance(x, bool):
        return x
    if isinstance(x, int | float | complex):
        return None if x != x else x != 0  # NaN is NA (as.logical(1i) is TRUE)
    if isinstance(x, str):
        # only these spellings, untrimmed (as.logical(" TRUE") is NA)
        if x in ("TRUE", "true", "True", "T"):
            return True
        if x in ("FALSE", "false", "False", "F"):
            return False
    return None


def verbose(value: Any = None) -> bool:
    """Get or set whether progress messages are printed (default ``True``).

    Port of ``verbose()`` (R/svutils-utils.R): a value is converted as R's
    ``as.logical()`` converts it (``"FALSE"``, ``"F"``, ``0`` are false,
    ``"TRUE"``, ``"T"``, any other number true); anything else raises
    ``ValueError("set verbose with TRUE or FALSE")``.
    """
    if value is not None:
        if getattr(value, "ndim", 0) >= 1:  # a numpy array or pandas Series: an R vector
            value = list(value)
        if isinstance(value, list | tuple):  # an R vector: if () needs exactly one value
            if len(value) != 1:
                raise ValueError(
                    "argument is of length zero" if not value else "the condition has length > 1"
                )
            value = value[0]
        flag = _as_logical(value)
        if flag is None:
            raise ValueError("set verbose with TRUE or FALSE")
        _state["verbose"] = flag
    if "verbose" in _state:
        return bool(_state["verbose"])
    env = env_get("VERBOSE")
    return env.lower() not in ("0", "false", "no") if env else True


def cache_dir(subdir: str = "", override: str | os.PathLike[str] | None = None) -> Path:
    """A cache directory (created on demand).

    Root: ``METACHECK_CACHE_DIR`` (or ``PYTACHECK_CACHE_DIR``) or the platform user
    cache directory.
    """
    if override is not None:
        path = Path(override)
    else:
        root = env_get("CACHE_DIR") or platformdirs.user_cache_dir("pytacheck", "scienceverse")
        path = Path(root) / subdir if subdir else Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Data directory and JSON config files (module system v2)
# ---------------------------------------------------------------------------
#
# Config is JSON. Scopes, lowest precedence first: built-in defaults, the
# user file (``<user_config_dir>/config.json``), the project file (the nearest
# ``metacheck.json`` or ``pytacheck.json`` at or above the working directory;
# in one folder ``metacheck.json`` is used, and the files are never merged).
# ``METACHECK_CONFIG`` (or ``PYTACHECK_CONFIG``) names the only file to read,
# or ``none`` for no files (hermetic runs).
#
# The search passes over a file that is not a project file (a JSON value that
# is not an object, or an object with none of the project keys), such as saved
# ``check --json`` results: it is never read as settings. It also passes over a
# blank ``metacheck.json``, silently: ``metacheck check --json > metacheck.json``
# creates the file empty before the command runs. (A blank ``pytacheck.json``
# still counts, as it did in 0.4.0a1.)
#
# A project file comes with the folder it is in, which may be a shared folder
# or a cloned repository, so it is not trusted like the user's own config:
# one owned by another user (or writable by others) is ignored, and the local
# code it names (path packs, ``.py`` modules in presets) runs only once the
# user has trusted that code (``trust_local()``; ``pack install`` asks). It
# cannot set ``stores`` at all: a store decides where packs come from, so a
# project's stores are ignored with a warning and stores come from the user
# file (or the file ``METACHECK_CONFIG`` or ``PYTACHECK_CONFIG`` names) only.
# These rules follow the scope, never the file name.

BUILTIN_STORE = "pytacheck"
BUILTIN_STORE_URL = "https://github.com/scienceverse/pytacheck-modules"
#: the project file created when none exists
PROJECT_CONFIG = "metacheck.json"
#: the project file names searched, in this order, in each folder
PROJECT_CONFIGS = (PROJECT_CONFIG, "pytacheck.json")
_SECTIONS = ("stores", "packs", "presets")
#: a non-empty JSON object with none of these keys is not a project file
_PROJECT_KEYS = ("preset", *_SECTIONS)
_ENV_KEYS = (
    *env_names("CONFIG"),
    *env_names("DATA_DIR"),
    *env_names("STORE_URL"),
    *env_names("PRESET"),
)
TRUST_FILE = "trusted.json"
_config_cache: dict[str, Any] = {}
_writes = 0  # bumped by update_config(): mtime_ns alone can miss rapid rewrites
#: A file modified less than this long before a stamp may be rewritten within the
#: same tick of the filesystem clock and keep its mtime and size (git's "racily
#: clean" entries), so its stamp also holds a digest of its bytes (as in
#: provenance.file_sha256).
_RACY_NS = 3_000_000_000
_warned_unsafe: set[tuple[str, str]] = set()
_warned_stores: set[str] = set()
_warned_both: set[str] = set()
_warned_not_project: set[str] = set()
#: path -> (its _candidate_stamp, _file_kind()): a large results file is read
#: once, not at every config_stamp()
_verdicts: dict[str, tuple[tuple[int, int, str | None], str]] = {}
#: the most a candidate's verdict (or the digest in its stamp) reads: the search
#: runs before _unsafe(), so a file planted in a shared folder must not be read
#: in full; above this, the first non-blank byte decides
_VERDICT_CAP = 64 * 1024 * 1024


class ConfigError(ValueError):
    """A config file is malformed or cannot be written."""


def data_dir(subdir: str = "", *, create: bool = False) -> Path:
    """Where installed packs and store indexes live.

    ``METACHECK_DATA_DIR`` (or ``PYTACHECK_DATA_DIR``) or
    ``platformdirs.user_data_dir("pytacheck")``.
    """
    root = Path(env_get("DATA_DIR") or platformdirs.user_data_dir("pytacheck"))
    path = root / subdir if subdir else root
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_path() -> Path:
    """The user config file (it may not exist)."""
    return Path(platformdirs.user_config_dir("pytacheck")) / "config.json"


def project_config_path(start: str | os.PathLike[str] | None = None) -> Path | None:
    """The nearest project file at or above *start* (default: the cwd).

    In each folder ``metacheck.json`` is looked for first, then ``pytacheck.json``;
    the nearest folder that holds either wins. A file that is not a project file,
    and a blank ``metacheck.json``, are passed over. The search does not go above
    the home folder, nor into another filesystem.
    """
    return _project_search(start)[0]


def _read_head(path: Path) -> bytes | None:
    """The first ``_VERDICT_CAP + 1`` bytes of *path*, or ``None`` when it cannot be
    opened or is not a regular file (a FIFO is opened without blocking and not read)."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = -1  # the file object closes it
            return fh.read(_VERDICT_CAP + 1)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _file_kind(path: Path) -> str:
    """``"blank"``, ``"other"`` (JSON that is not a project file) or ``"project"``.

    Only what the search needs is read (:data:`_VERDICT_CAP`). A file that cannot
    be read, or is not UTF-8 or not valid JSON, is ``"project"``: reading it as
    settings (after :func:`_unsafe`) reports the error, as before.
    """
    head = _read_head(path)
    if head is None:
        return "project"
    if len(head) > _VERDICT_CAP:  # too large to parse here: the first byte decides
        first = head.lstrip(b" \t\r\n\x0b\x0c")[:1]
        return "other" if first and first != b"{" else "project"
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return "project"
    if not text.strip():
        return "blank"
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return "project"  # a typo in a real project file still gives its error
    if not isinstance(data, dict):
        return "other"
    return "other" if data and not any(key in data for key in _PROJECT_KEYS) else "project"


def _candidate_stamp(path: Path, now: int) -> tuple[int, int, str | None] | None:
    """:func:`_file_stamp` for a file the search looks at: its digest covers at most
    :data:`_VERDICT_CAP` + 1 bytes, and only of a regular file."""
    try:
        st = path.stat()
    except OSError:
        return None
    digest = None
    if now - st.st_mtime_ns < _RACY_NS:
        head = _read_head(path)
        if head is not None:
            digest = hashlib.sha256(head).hexdigest()
    return (st.st_mtime_ns, st.st_size, digest)


def _verdict(path: Path, now: int) -> str:
    """:func:`_file_kind` of *path*, kept until the file's stamp changes."""
    stamp = _candidate_stamp(path, now)
    if stamp is None:
        return "project"
    cached = _verdicts.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    kind = _file_kind(path)
    _verdicts[str(path)] = (stamp, kind)
    return kind


def _project_search(
    start: str | os.PathLike[str] | None = None,
) -> tuple[Path | None, tuple[Path, ...]]:
    """``(the project file found or None, the files passed over)``, in search order.

    See :func:`project_config_path`. A candidate is passed over when it parses as
    JSON and is either not an object or a non-empty object with none of
    ``preset``, ``stores``, ``packs`` and ``presets`` (with a warning), or when it
    is a blank ``metacheck.json`` (silently). ``{}``, invalid JSON and a blank
    ``pytacheck.json`` count as project files. With both names in the folder
    found, ``metacheck.json`` is used and the other is ignored with a warning.
    """
    now = time.time_ns()  # before the stats: later writes get a later mtime
    here = Path(start) if start is not None else Path.cwd()
    try:
        home: Path | None = Path.home().resolve()
    except (OSError, RuntimeError):  # pragma: no cover - no home folder
        home = None
    try:
        device = here.stat().st_dev
    except OSError:
        return None, ()
    passed: list[Path] = []
    for folder in (here, *here.parents):
        for name in PROJECT_CONFIGS:
            candidate = folder / name
            if not candidate.is_file():
                continue
            kind = _verdict(candidate, now)
            if kind == "blank" and name == PROJECT_CONFIG:
                passed.append(candidate)  # made by a shell redirect, say: silently
                continue
            if kind == "other":
                passed.append(candidate)
                if str(candidate) not in _warned_not_project:
                    _warned_not_project.add(str(candidate))
                    warnings.warn(
                        f"Ignoring {candidate}: it is not a metacheck project file "
                        "(it has no preset, stores, packs or presets)",
                        stacklevel=2,
                    )
                continue
            # no merging: two files in one folder would be two trust surfaces
            old = folder / PROJECT_CONFIGS[1]
            if name == PROJECT_CONFIG and old.is_file() and str(folder) not in _warned_both:
                _warned_both.add(str(folder))
                warnings.warn(f"Ignoring {old}: {candidate} is used instead", stacklevel=2)
            return candidate, tuple(passed)
        if home is not None and folder.resolve() == home:
            break
        try:
            if folder.parent.stat().st_dev != device:
                break
        except OSError:
            break
    return None, tuple(passed)


def _unsafe(path: Path) -> str | None:
    """Why a project config file must not be read (like git's ``safe.directory``), or ``None``."""
    if sys.platform == "win32" or not hasattr(os, "geteuid"):
        return None
    uid, gid = os.geteuid(), os.getegid()
    for what, target in (("file", path), ("folder", path.parent)):
        try:
            st = target.stat()
        except OSError:
            return None
        if st.st_uid != uid:
            return f"its {what} is owned by another user"
        mode = st.st_mode
        if mode & stat.S_IWOTH and not (what == "folder" and mode & stat.S_ISVTX):
            return f"its {what} is writable by everyone"
        if mode & stat.S_IWGRP and st.st_gid != gid:
            return f"its {what} is writable by a group"
    return None


def _config_env() -> tuple[str, str]:
    """``(name, value)`` of ``METACHECK_CONFIG`` or ``PYTACHECK_CONFIG`` (stripped),
    or ``("", "")`` when neither is set to a non-blank value."""
    hit = env_lookup("CONFIG")
    return (hit[0], hit[1].strip()) if hit is not None else ("", "")


def config_files() -> list[tuple[str, Path]]:
    """``(scope, path)`` of the config files in effect, lowest precedence first.

    Scopes are ``"user"`` and ``"project"``, or ``"env"`` for the single file
    named by ``METACHECK_CONFIG`` or ``PYTACHECK_CONFIG`` (listed even when it
    does not exist yet).
    """
    return _config_scan()[0]


def _config_scan() -> tuple[list[tuple[str, Path]], tuple[Path, ...]]:
    """:func:`config_files`, and the files the project search passed over."""
    env = _config_env()[1]
    if env:
        if env.lower() == "none":
            return [], ()
        return [("env", Path(env).expanduser().absolute())], ()
    out: list[tuple[str, Path]] = []
    user = user_config_path()
    if user.is_file():
        out.append(("user", user))
    try:
        project, passed = _project_search()
    except OSError:  # the working directory was removed
        project, passed = None, ()
    # an unsafe file is ignored, with no fallback to the other name in its folder
    if project is not None and project != user:
        why = _unsafe(project)
        if why is None:
            out.append(("project", project))
        elif (str(project), why) not in _warned_unsafe:
            _warned_unsafe.add((str(project), why))
            warnings.warn(
                f"Ignoring the project config {project}: {why}. If you trust it, "
                f"use it explicitly with PYTACHECK_CONFIG={project}",
                stacklevel=2,
            )
    return out, passed


def _trust_file() -> Path:
    return data_dir() / TRUST_FILE


def trusted_local() -> frozenset[str]:
    """Local code (folders and ``.py`` files) the user has trusted project configs to run."""
    try:
        data = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    paths = data.get("paths") if isinstance(data, dict) else None
    return frozenset(p for p in paths or () if isinstance(p, str))


def trust_local(paths: Iterable[str | os.PathLike[str]]) -> None:
    """Trust local code named by project configs (path packs, ``.py`` modules in presets)."""
    global _writes
    new = {str(Path(p).expanduser().resolve()) for p in paths}
    have = trusted_local()
    if new <= have:
        return
    target = _trust_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".trusted-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"paths": sorted(have | new)}, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _writes += 1
    _config_cache.clear()


def local_code(section: str, value: Any) -> list[str]:
    """The local code a resolved config entry runs: a path pack's folder, ``.py`` modules."""
    if section == "packs" and isinstance(value, dict) and isinstance(value.get("path"), str):
        return [value["path"]]
    if section == "presets" and isinstance(value, dict):
        refs = [*(value.get("modules") or []), *(value.get("replace") or {}).values()]
        return [r for r in refs if isinstance(r, str) and r.endswith(".py")]
    return []


def _file_stamp(path: Path, now: int) -> tuple[int, int, str | None] | None:
    """``(mtime_ns, size, digest)`` of a file, the digest only while it is racy."""
    try:
        st = path.stat()
    except OSError:
        return None
    digest = None
    if now - st.st_mtime_ns < _RACY_NS:
        with contextlib.suppress(OSError):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (st.st_mtime_ns, st.st_size, digest)


def config_stamp() -> tuple[Any, ...]:
    """A cheap fingerprint of everything config depends on (for cache invalidation)."""
    now = time.time_ns()  # before the stats: later writes get a later mtime
    found, passed = _config_scan()
    files = tuple((scope, str(path), _file_stamp(path, now)) for scope, path in found)
    # a passed-over file that becomes a project file is seen at once
    files += tuple(("passed", str(p), _candidate_stamp(p, now)) for p in passed)
    env = tuple(os.environ.get(k) for k in _ENV_KEYS)
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    trust = None
    if any(f[0] == "project" for f in files):
        trust = _file_stamp(_trust_file(), now)
    return (env, cwd, files, trust, _writes)


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


def _check_section(data: dict[str, Any], path: Path, *, skip: tuple[str, ...] = ()) -> None:
    """Check the shape of a config file; sections named in *skip* are not looked at."""
    preset = data.get("preset")
    if preset is not None and not isinstance(preset, str):
        raise ConfigError(f"{path}: 'preset' must be a string or null")
    for section in _SECTIONS:
        value = data.get(section)
        if value is None or section in skip:
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
    #: dotted key -> ``(project file, entry, untrusted local code)`` for project
    #: entries left out because they run local code the user has not trusted
    untrusted: dict[str, tuple[str, Any, tuple[str, ...]]] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def source(self, key: str) -> tuple[str, str] | None:
        """``(scope, location)`` that set *key*, e.g. ``("project", "/x/metacheck.json")``."""
        return self.sources.get(key)

    @property
    def preset(self) -> str | None:
        return self.values.get("preset")

    @property
    def stores(self) -> dict[str, str]:
        return self.values["stores"]  # type: ignore[no-any-return]

    @property
    def packs(self) -> dict[str, Any]:
        return self.values["packs"]  # type: ignore[no-any-return]

    @property
    def presets(self) -> dict[str, dict[str, Any]]:
        return self.values["presets"]  # type: ignore[no-any-return]


def load_config() -> Config:
    """The effective configuration, cached until :func:`config_stamp` changes.

    Scalars: the higher scope wins. ``stores``, ``packs`` and ``presets`` are
    merged by key; ``null`` removes an entry and ``false`` hides a dist pack.
    Relative paths are resolved against the file that holds them. The store
    ``pytacheck`` is built in; ``METACHECK_STORE_URL`` (or ``PYTACHECK_STORE_URL``)
    overrides its URL.
    Project entries that run local code the user has not trusted
    (:func:`trust_local`) are left out and listed in ``Config.untrusted``.
    A project file's ``stores`` are ignored, with a warning: a cloned
    repository must not add a store or replace the built-in one.
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
    untrusted: dict[str, tuple[str, Any, tuple[str, ...]]] = {}
    files = tuple(config_files())
    trusted = trusted_local() if any(scope == "project" for scope, _ in files) else frozenset()
    for scope, path in files:
        data = _read_json(path)
        # a project's stores are ignored (warned about below), so they need no check
        _check_section(data, path, skip=("stores",) if scope == "project" else ())
        where = (scope, str(path))
        for key, value in data.items():
            if key == "stores" and scope == "project":
                if value and str(path) not in _warned_stores:
                    _warned_stores.add(str(path))
                    warnings.warn(
                        f"Ignoring the stores in the project config {path}: a project config "
                        "cannot add, change or remove stores. Put them in your user config "
                        "with `pytacheck store add NAME URL`",
                        stacklevel=2,
                    )
                continue
            if key in _SECTIONS:
                for name, item in (value or {}).items():
                    if item is None:
                        values[key].pop(name, None)
                        sources.pop(f"{key}.{name}", None)
                        continue
                    entry = _resolve_entry(key, item, path.parent)
                    if scope == "project":
                        code = [c for c in local_code(key, entry) if c not in trusted]
                        if code:
                            untrusted[f"{key}.{name}"] = (str(path), entry, tuple(code))
                            continue
                    untrusted.pop(f"{key}.{name}", None)
                    values[key][name] = entry
                    sources[f"{key}.{name}"] = where
            elif value is None:
                values.pop(key, None)
                sources.pop(key, None)
            else:
                values[key] = value
                sources[key] = where
    hit = env_lookup("STORE_URL")
    if hit is not None:
        values["stores"][BUILTIN_STORE] = hit[1]
        sources[f"stores.{BUILTIN_STORE}"] = ("env", hit[0])
    config = Config(values=values, sources=sources, files=files, stamp=stamp, untrusted=untrusted)
    _config_cache["config"] = config
    return config


def config_path(scope: str = "user") -> Path:
    """The file :func:`update_config` writes for *scope* (``"user"`` or ``"project"``).

    For ``"project"``: the project file found (edited in place, never renamed, so
    a ``pytacheck.json`` stays one), else ``./metacheck.json``. A file the search
    passed over is never written, except a blank ``./metacheck.json`` when no
    project file is found above it (so ``touch metacheck.json`` and then
    ``init --project`` writes into it; with a project file above, that one is
    edited).
    """
    name, env = _config_env()
    if env.lower() == "none":
        raise ConfigError(f"Config files are disabled ({name}=none)")
    if env:
        return Path(env).expanduser().absolute()
    if scope == "user":
        return user_config_path()
    if scope == "project":
        found, passed = _project_search()
        if found is not None:
            return found
        path = Path.cwd() / PROJECT_CONFIG
        if path in passed and _verdict(path, time.time_ns()) != "blank":
            raise ConfigError(
                f"{path} is not a metacheck project file; move it, "
                "or set METACHECK_CONFIG to the file to write"
            )
        return path
    raise ValueError(f"scope must be 'user' or 'project', not {scope!r}")


def update_config(scope: str, fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> Path:
    """Edit one config file atomically and return its path.

    *fn* receives the file's current content (``{}`` when it does not exist)
    and may change it in place or return a replacement. With
    ``METACHECK_CONFIG=<file>`` (or ``PYTACHECK_CONFIG``) every scope writes that
    file. Local code that this edit adds to a project file is trusted (the user
    added it); code the file already named keeps its trust status.
    """
    global _writes
    path = config_path(scope)
    data = _read_json(path)
    before = _local_code_of(data, path) if scope == "project" else set()
    result = fn(data)
    if result is not None:
        data = result
    if not isinstance(data, dict):
        raise ConfigError("update_config(): the new config must be a dict")
    # a project file's stores are ignored, so they need no check (unless METACHECK_CONFIG
    # or PYTACHECK_CONFIG names it)
    own_project = scope == "project" and not _config_env()[1]
    _check_section(data, path, skip=("stores",) if own_project else ())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=".pytacheck-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _writes += 1
    _config_cache.clear()
    if own_project:
        added = _local_code_of(data, path) - before
        if added:
            trust_local(added)
    return path


def _local_code_of(data: Any, path: Path) -> set[str]:
    """Every piece of local code a config file's content names (resolved)."""
    out: set[str] = set()
    if not isinstance(data, dict):
        return out
    for section in ("packs", "presets"):
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for item in entries.values():
            if isinstance(item, dict):
                out.update(local_code(section, _resolve_entry(section, item, path.parent)))
    return out
