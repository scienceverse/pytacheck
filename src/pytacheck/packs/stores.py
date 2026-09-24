"""Stores: git repositories whose ``index.json`` lists packs (like plugin marketplaces).

A store is data only: listing, searching and showing never run pack code.
The default store ``pytacheck`` is ``https://github.com/thesanogoeffect/pytacheck-modules``;
config can add others or remove it (see ``docs/MODULES.md``).

Where the index is read from:

* a GitHub repo URL -> ``https://raw.githubusercontent.com/<owner>/<repo>/HEAD/index.json``;
* a GitLab repo URL -> ``<url>/-/raw/HEAD/index.json``;
* a URL ending in ``.json`` -> that file;
* a local folder (or ``file://`` URL) -> ``<folder>/index.json``, read directly;
* any other URL -> ``<url>/index.json``.

Fetched indexes are cached in ``<data>/stores/<name>/index.json`` for an
hour. When a store cannot be reached the cached copy is used with a
warning; with no cached copy the error says so and how to add another store.
"""

from __future__ import annotations

import json
import re
import time
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from pytacheck.config import BUILTIN_STORE, config_path, data_dir, load_config, update_config
from pytacheck.packs.manifest import PACK_NAME_RE, PackError

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "INDEX_SCHEMA",
    "INDEX_TTL",
    "StoreError",
    "find_entry",
    "index_location",
    "store_add",
    "store_index",
    "store_indexes",
    "store_list",
    "store_remove",
    "store_search",
    "store_update",
]

INDEX_SCHEMA = 1
INDEX_TTL = 3600.0  # seconds a fetched index stays fresh
_GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_GITLAB = re.compile(r"^https?://(?:www\.)?gitlab\.com/(.+?)(?:\.git)?/?$")


class StoreError(PackError):
    """A store is unknown, unreachable or has a malformed index."""


# ---------------------------------------------------------------------------
# Locations and the index cache
# ---------------------------------------------------------------------------


def _is_url(value: str) -> bool:
    return "://" in value or value.startswith("git@")


def index_location(url: str) -> tuple[str, str]:
    """Where a store's ``index.json`` is: ``("file", path)`` or ``("http", url)``."""
    if url.startswith("file://"):
        url = unquote(urlsplit(url).path)
    if not _is_url(url):
        path = Path(url).expanduser()
        return ("file", str(path if path.suffix == ".json" else path / "index.json"))
    if url.startswith("git@") or url.startswith("ssh://"):
        raise StoreError(
            f"The store URL {url} uses ssh; point it at an https:// repository or index.json"
        )
    m = _GITHUB.match(url)
    if m:
        owner, repo = m.groups()
        return ("http", f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/index.json")
    if url.rstrip("/").endswith(".json"):
        return ("http", url)
    m = _GITLAB.match(url)
    if m:
        return ("http", f"{url.rstrip('/').removesuffix('.git')}/-/raw/HEAD/index.json")
    return ("http", url.rstrip("/") + "/index.json")


def _cache(name: str) -> Path:
    return data_dir("stores") / name


def _read_cached(name: str, url: str) -> tuple[dict[str, Any], float] | None:
    folder = _cache(name)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("url") != url or not isinstance(data, dict):
        return None
    return data, float(meta.get("fetched") or 0)


def _write_cache(name: str, url: str, data: dict[str, Any]) -> None:
    folder = _cache(name)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "index.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / "index.json")
    (folder / "meta.json").write_text(
        json.dumps({"url": url, "fetched": time.time()}), encoding="utf-8"
    )


def validate_index(data: Any, where: str) -> dict[str, Any]:
    """Check the shape of an ``index.json`` (tolerant of extra keys)."""
    if not isinstance(data, Mapping):
        raise StoreError(f"{where} is not a store index (a JSON object)")
    schema = data.get("schema", INDEX_SCHEMA)
    if schema != INDEX_SCHEMA:
        raise StoreError(f"{where} uses index schema {schema!r}; this pytacheck reads schema 1")
    packs = data.get("packs", [])
    if not isinstance(packs, list) or not all(
        isinstance(p, Mapping) and isinstance(p.get("name"), str) for p in packs
    ):
        raise StoreError(f"{where}: 'packs' must be a list of objects with a 'name'")
    return dict(data)


def _fetch(url: str) -> dict[str, Any]:
    import httpx

    from pytacheck.http import request

    try:
        resp = request(
            "GET", url, max_tries=2, headers={"Accept": "application/json"}, timeout=30.0
        )
    except httpx.HTTPError as exc:  # pragma: no cover - request() retries these
        raise StoreError(f"cannot connect ({exc})") from exc
    if resp is None:
        raise StoreError("cannot connect")
    if resp.status_code != 200:
        raise StoreError(f"HTTP {resp.status_code}")
    try:
        return json.loads(resp.content)  # type: ignore[no-any-return]
    except ValueError as exc:
        raise StoreError(f"the index is not valid JSON ({exc})") from exc


def _store_url(name: str) -> str:
    stores = load_config().stores
    if name not in stores:
        have = ", ".join(stores) or "none"
        raise StoreError(
            f"There is no store named '{name}' (stores: {have}). "
            "Add one with `pytacheck store add NAME URL`."
        )
    return stores[name]


def store_index(name: str, *, refresh: bool = False, offline: bool = False) -> dict[str, Any]:
    """A store's index: fetched (or read from a local folder), cached for an hour.

    ``refresh=True`` ignores the cache's age; ``offline=True`` only reads the
    cache. An unreachable store falls back to its cached copy with a warning.
    """
    url = _store_url(name)
    kind, where = index_location(url)
    if kind == "file":
        try:
            data = json.loads(Path(where).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StoreError(
                f"The store '{name}' has no index: {where} does not exist "
                "(build it with `pytacheck store build`)"
            ) from None
        except (OSError, ValueError) as exc:
            raise StoreError(f"Cannot read the index of store '{name}' ({where}): {exc}") from exc
        data = validate_index(data, where)
        data["_location"] = str(Path(where).parent)
        return data
    cached = _read_cached(name, where)
    if cached is not None and (offline or (not refresh and time.time() - cached[1] < INDEX_TTL)):
        return validate_index(cached[0], where)
    if offline:
        raise StoreError(f"The store '{name}' has no cached index (offline)")
    try:
        data = validate_index(_fetch(where), where)
    except StoreError as exc:
        if cached is not None:
            when = datetime.fromtimestamp(cached[1], UTC).strftime("%Y-%m-%d %H:%M UTC")
            warnings.warn(
                f"The store '{name}' is unreachable ({exc}); using its index cached at {when}",
                stacklevel=2,
            )
            return validate_index(cached[0], where)
        raise StoreError(
            f"The store '{name}' ({url}) is unreachable and there is no cached copy of its "
            f"index: {exc}. Check your connection, or add another store with "
            "`pytacheck store add NAME URL` (see `pytacheck store list`)."
        ) from None
    _write_cache(name, where, data)
    return data


def store_indexes(*, refresh: bool = False) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """``(indexes, problems)`` for every configured store; unreachable ones go in *problems*."""
    indexes: dict[str, dict[str, Any]] = {}
    problems: dict[str, str] = {}
    for name in load_config().stores:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                indexes[name] = store_index(name, refresh=refresh)
            for w in caught:
                warnings.warn(w.message, stacklevel=2)
        except PackError as exc:
            problems[name] = str(exc)
    return indexes, problems


# ---------------------------------------------------------------------------
# Config: list, add, remove, update
# ---------------------------------------------------------------------------


def _validate_store_name(name: Any) -> str:
    if not isinstance(name, str) or not PACK_NAME_RE.match(name):
        raise StoreError(
            f"Invalid store name {name!r}: use 2-40 lowercase letters, digits, '_' or '-'"
        )
    return name


def store_list() -> pd.DataFrame:
    """The configured stores: ``name``, ``url``, ``defined_in``, ``packs``, ``updated``.

    Nothing is fetched: ``packs`` and ``updated`` come from the local cache.
    """
    import pandas as pd

    config = load_config()
    rows = []
    for name, url in config.stores.items():
        src = config.source(f"stores.{name}") or ("", "")
        n: Any = pd.NA
        updated: Any = pd.NA
        try:
            kind, where = index_location(url)
        except StoreError:
            kind, where = "", ""
        if kind == "file":
            try:
                n = len(json.loads(Path(where).read_text(encoding="utf-8")).get("packs", []))
            except (OSError, ValueError, AttributeError):
                pass
        elif kind == "http":
            cached = _read_cached(name, where)
            if cached is not None:
                n = len(cached[0].get("packs", []))
                updated = datetime.fromtimestamp(cached[1], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append((name, url, src[1] if src[0] != "builtin" else "builtin", n, updated))
    return pd.DataFrame(
        {
            "name": pd.Series([r[0] for r in rows], dtype="string"),
            "url": pd.Series([r[1] for r in rows], dtype="string"),
            "defined_in": pd.Series([r[2] for r in rows], dtype="string"),
            "packs": pd.Series([r[3] for r in rows], dtype="Int64"),
            "updated": pd.Series([r[4] for r in rows], dtype="string"),
        }
    )


def store_add(name: str, url: str, *, scope: str = "user") -> Path:
    """Add (or change) a store in the *scope* config file; returns that file."""
    _validate_store_name(name)
    if not isinstance(url, str) or not url.strip():
        raise StoreError("A store needs a URL or a folder")
    url = url.strip()
    if not _is_url(url):
        url = str(Path(url).expanduser().resolve())
    index_location(url)  # rejects unsupported URLs early

    def edit(cfg: dict[str, Any]) -> None:
        stores = cfg.get("stores")
        if not isinstance(stores, dict):
            stores = cfg["stores"] = {}
        stores[name] = url

    return update_config(scope, edit)


def store_remove(name: str, *, scope: str = "user") -> Path:
    """Remove a store: drop it from the *scope* file, or mask it there with ``null``."""
    _validate_store_name(name)
    config = load_config()
    if name not in config.stores:
        raise StoreError(f"There is no store named '{name}'")
    src = config.source(f"stores.{name}") or ("", "")
    here = src[1] == str(config_path(scope))

    def edit(cfg: dict[str, Any]) -> None:
        stores = cfg.get("stores")
        if not isinstance(stores, dict):
            stores = cfg["stores"] = {}
        stores.pop(name, None)
        if name == BUILTIN_STORE or not here:
            stores[name] = None  # mask the built-in store or another file's entry

    path = update_config(scope, edit)
    if name in load_config().stores:
        where = (load_config().source(f"stores.{name}") or ("", "?"))[1]
        warnings.warn(f"The store '{name}' is still defined in {where}", stacklevel=2)
    return path


def store_update(name: str | None = None) -> pd.DataFrame:
    """Fetch the index of one store (or all), ignoring the cache's age.

    Returns ``name``, ``url``, ``packs`` and ``status`` (``"ok"`` or the
    problem). With a *name*, an unreachable store without a cache raises.
    """
    import pandas as pd

    names = [name] if name is not None else list(load_config().stores)
    rows: list[tuple[str, str, int | None, str]] = []
    for n in names:
        url = _store_url(n)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                data = store_index(n, refresh=True)
            status = "ok" if not caught else f"cached copy used: {caught[0].message}"
            rows.append((n, url, len(data.get("packs", [])), status))
        except StoreError as exc:
            if name is not None:
                raise
            rows.append((n, url, None, str(exc)))
    return pd.DataFrame(
        {
            "name": pd.Series([r[0] for r in rows], dtype="string"),
            "url": pd.Series([r[1] for r in rows], dtype="string"),
            "packs": pd.Series([r[2] for r in rows], dtype="Int64"),
            "status": pd.Series([r[3] for r in rows], dtype="string"),
        }
    )


# ---------------------------------------------------------------------------
# Search and lookup
# ---------------------------------------------------------------------------


def _haystack(entry: Mapping[str, Any]) -> str:
    parts: list[str] = [
        str(entry.get(k) or "") for k in ("name", "title", "description", "homepage")
    ]
    parts += [str(x) for x in entry.get("keywords") or []]
    parts += [str(x) for x in entry.get("fields") or []]
    for m in entry.get("modules") or []:
        if isinstance(m, Mapping):
            parts += [str(m.get(k) or "") for k in ("name", "title", "description", "section")]
    presets = entry.get("presets") or {}
    if isinstance(presets, Mapping):
        parts += [f"{k} {v}" for k, v in presets.items()]
    return "\n".join(parts).lower()


def _pinned_revs() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, pin in load_config().packs.items():
        if isinstance(pin, Mapping):
            out[name] = str(pin.get("rev") or pin.get("tree_sha256") or pin.get("path") or "")
    return out


def store_search(text: str = "", field: str | None = None) -> pd.DataFrame:
    """Packs listed by the configured stores, filtered by *text* and *field*.

    Every word of *text* must appear (case-insensitively) in a pack's name,
    title, description, keywords, fields, module names or descriptions, or
    preset names. *field* must be one of the pack's ``fields``. Unreachable
    stores are skipped with a warning.
    """
    import pandas as pd

    from pytacheck.packs.manifest import validation_metrics

    indexes, problems = store_indexes()
    for name, problem in problems.items():
        warnings.warn(f"Skipping store '{name}': {problem}", stacklevel=2)
    words = [w for w in str(text or "").lower().split() if w]
    fld = field.lower() if field else None
    pinned = _pinned_revs()
    rows = []
    for store, index in indexes.items():
        for entry in index.get("packs", []):
            fields = [str(f) for f in entry.get("fields") or []]
            if fld is not None and fld not in (f.lower() for f in fields):
                continue
            hay = _haystack(entry)
            if any(w not in hay for w in words):
                continue
            modules = [m for m in entry.get("modules") or [] if isinstance(m, Mapping)]
            validated = sum(1 for m in modules if validation_metrics(m.get("validation")))
            rev = str((entry.get("source") or {}).get("rev") or entry.get("tree_sha256") or "")
            name = str(entry["name"])
            rows.append(
                {
                    "store": store,
                    "name": name,
                    "version": entry.get("version"),
                    "title": entry.get("title") or "",
                    "description": entry.get("description") or "",
                    "fields": ", ".join(fields),
                    "code": bool(entry.get("code", bool(modules))),
                    "modules": ", ".join(str(m.get("name")) for m in modules),
                    "validated": f"{validated}/{len(modules)}" if modules else "",
                    "presets": ", ".join(entry.get("presets") or {}),
                    "reviewed": entry.get("reviewed"),
                    "yanked": entry.get("yanked"),
                    "installed": name in pinned and bool(rev) and pinned[name] == rev,
                }
            )
    cols = [
        "store", "name", "version", "title", "description", "fields", "code", "modules",
        "validated", "presets", "reviewed", "yanked", "installed",
    ]  # fmt: skip
    df = pd.DataFrame(rows, columns=cols)
    for col in cols:
        if col in ("code", "installed"):
            df[col] = df[col].astype("boolean")
        elif col != "yanked":
            df[col] = df[col].astype("string")
    return df


def find_entry(
    name: str, *, store: str | None = None, refresh: bool = False
) -> tuple[str, dict[str, Any]]:
    """``(store, index entry)`` for a pack listed by exactly one store.

    With *store*, only that store is read. A pack listed by several stores
    needs ``store/name``. A name missing from a cached index is looked up
    again in a freshly fetched one; ``refresh=True`` always fetches.
    """
    if not refresh:
        try:
            return _find_entry(name, store, refresh=False)
        except StoreError:
            pass  # perhaps listed since the index was cached
    return _find_entry(name, store, refresh=True)


def _find_entry(name: str, store: str | None, *, refresh: bool) -> tuple[str, dict[str, Any]]:
    if store is not None:
        index = store_index(store, refresh=refresh)
        for entry in index.get("packs", []):
            if entry.get("name") == name:
                return store, _with_location(dict(entry), index)
        raise StoreError(f"The store '{store}' does not list a pack named '{name}'")
    indexes, problems = store_indexes(refresh=refresh)
    hits = [
        (s, _with_location(dict(e), idx))
        for s, idx in indexes.items()
        for e in idx.get("packs", [])
        if e.get("name") == name
    ]
    if len(hits) > 1:
        refs = ", ".join(f"{s}/{name}" for s, _ in hits)
        raise StoreError(f"Several stores list a pack named '{name}'; use one of {refs}")
    if hits:
        return hits[0]
    msg = f"No store lists a pack named '{name}'"
    if indexes:
        msg += f" (searched: {', '.join(indexes)})"
    if problems:
        msg += "\nUnreachable stores:\n" + "\n".join(f"* {v}" for v in problems.values())
    raise StoreError(msg)


def _with_location(entry: dict[str, Any], index: Mapping[str, Any]) -> dict[str, Any]:
    if "_location" in index:
        entry["_location"] = index["_location"]
    return entry
