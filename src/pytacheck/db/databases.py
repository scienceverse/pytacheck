"""Bundled reference databases (metacheck's ``inst/databases/*.Rds``).

metacheck ships three small databases as ``.Rds`` files: the RetractionWatch
DOI list, the FORRT replication database (FLoRA) and a (proof-of-concept)
database of commonly miscited papers. pytacheck ships the same data,
converted by ``scripts/convert_databases.R`` into gzip-compressed,
column-major JSON with explicit column types (no pyarrow, no R needed)::

    {"format": 1, "name": "...", "date": "YYYY-MM-DD" | null, "nrow": n,
     "columns": {"col": "string" | "integer" | "number" | "boolean"},
     "data": {"col": [...]}}

Like metacheck, a refreshed copy downloaded by ``rw_update()`` /
``FLoRA_update()`` is written to the user data directory and used instead of
the bundled one when its ``date`` is newer. Files are parsed once and cached;
callers get a cheap (copy-on-write) copy they may modify freely.
"""

from __future__ import annotations

import datetime as dt
import functools
import gzip
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck.db._utils import user_data_dir

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "database_date",
    "load_database",
    "miscite",
    "read_database",
    "user_database_path",
    "write_database",
]

_FORMAT = 1
_DTYPES = {"string": "string", "integer": "Int64", "number": "float64", "boolean": "boolean"}


def _frame(obj: dict[str, Any]) -> pd.DataFrame:
    import pandas as pd

    columns: dict[str, str] = obj["columns"]
    data: dict[str, Any] = obj["data"]
    n = int(obj.get("nrow", 0))
    frame = pd.DataFrame(
        {
            name: pd.array(
                data.get(name) or [None] * n,  # type: ignore[arg-type]
                dtype=_DTYPES.get(kind, object),  # type: ignore[call-overload]
            )
            for name, kind in columns.items()
        },
        index=pd.RangeIndex(n),
    )
    date = obj.get("date")
    frame.attrs["date"] = dt.date.fromisoformat(date) if date else None
    return frame


def _parse(raw: bytes) -> pd.DataFrame:
    import orjson

    obj = orjson.loads(gzip.decompress(raw))
    if obj.get("format") != _FORMAT:
        raise ValueError(f"unsupported database format {obj.get('format')!r}")
    return _frame(obj)


@functools.cache
def _bundled(name: str) -> pd.DataFrame:
    raw = resources.files("pytacheck.resources.databases").joinpath(f"{name}.json.gz").read_bytes()
    return _parse(raw)


@functools.lru_cache(maxsize=8)
def _read_cached(path: str, _mtime_ns: int, _size: int) -> pd.DataFrame:
    return _parse(Path(path).read_bytes())


def read_database(path: str | Path) -> pd.DataFrame:
    """Read a database file written by ``scripts/convert_databases.R`` / :func:`write_database`.

    The ``date`` attribute is available as ``frame.attrs["date"]``.
    """
    p = Path(path)
    st = p.stat()
    return _read_cached(str(p.resolve()), st.st_mtime_ns, st.st_size).copy(deep=False)


def user_database_path(name: str) -> Path:
    """Where a refreshed copy of database *name* lives (the user data directory)."""
    return user_data_dir() / f"{name}.json.gz"


def load_database(name: str) -> pd.DataFrame:
    """The newest available copy of database *name* (bundled or refreshed).

    Mirrors ``retractionwatch()`` / ``FLoRA()``: the refreshed copy in the
    user data directory wins only when its ``date`` is later than the
    bundled one's.
    """
    return _newest_database(name).copy(deep=False)


def _newest_database(name: str) -> pd.DataFrame:
    """:func:`load_database` without the copy: the process-wide cached frame itself,
    the same object until the newest copy changes (so it can key derived caches).
    Private because a caller that wrote into it would change every later lookup."""
    internal = _bundled(name)
    ext = user_database_path(name)
    if ext.exists():
        try:
            st = ext.stat()
            external = _read_cached(str(ext.resolve()), st.st_mtime_ns, st.st_size)
        except (OSError, ValueError, KeyError):
            external = None
        if external is not None:
            ext_date = external.attrs.get("date")
            int_date = internal.attrs.get("date")
            if ext_date is not None and (int_date is None or ext_date > int_date):
                return external
    return internal


def database_date(name: str) -> dt.date | None:
    """The ``date`` attribute of the database *name* currently in use."""
    date: dt.date | None = load_database(name).attrs.get("date")
    return date


def _json_value(v: Any) -> Any:
    import pandas as pd

    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _kind(s: pd.Series) -> str:
    import pandas as pd

    dtype = s.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "number"
    return "string"


def write_database(
    frame: pd.DataFrame, path: str | Path, name: str, date: dt.date | None = None
) -> Path:
    """Write *frame* in the bundled database format (gzip JSON) and return *path*."""
    import orjson

    columns = {str(c): _kind(frame[c]) for c in frame.columns}
    data: dict[str, list[Any]] = {}
    for c, kind in columns.items():
        values = [_json_value(v) for v in frame[c].tolist()]
        if kind == "string":
            values = [None if v is None else str(v) for v in values]
        data[c] = values
    obj = {
        "format": _FORMAT,
        "name": name,
        "date": date.isoformat() if date else None,
        "nrow": len(frame),
        "columns": columns,
        "data": data,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(gzip.compress(orjson.dumps(obj), compresslevel=9, mtime=0))
    tmp.replace(p)
    return p


def miscite() -> pd.DataFrame:
    """The database of commonly miscited papers (metacheck's ``inst/databases/miscite.Rds``).

    Columns ``doi``, ``reftext`` and ``warning``. metacheck reads it with
    ``readRDS(system.file("databases/miscite.Rds", package = "metacheck"))``
    as the default ``db`` of the ``ref_miscitation`` module; it is built by
    ``data-raw/miscite.R`` and is a proof of concept (three entries).
    """
    return load_database("miscite")
