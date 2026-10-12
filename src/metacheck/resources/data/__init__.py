"""metacheck's bundled lookup data (``data/*.rda``), converted for pytacheck.

The files here are generated from R, never edited by hand:

* ``file_types.json.gz`` -- ``metacheck::file_types`` (``data/file_types.rda``),
  written by ``scripts/convert_data.R``; read by :mod:`metacheck.fileinfo.types`;
* ``scales.json.gz`` / ``tasks.json.gz`` -- the ``data_check`` dictionaries,
  written by ``scripts/convert_datacheck_data.R``.

Each file is gzip-compressed UTF-8 JSON::

    {"format": 1, "name": "...", "date": null, "nrow": n,
     "columns": {"col": "string" | "integer" | "number" | "boolean", ...},
     "data": {"col": [value | null, ...], ...}}

:func:`load_data` turns one into a data frame with pandas nullable dtypes.
"""

from __future__ import annotations

import functools
import gzip
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["load_data"]

_FORMAT = 1
_DTYPES = {"string": "string", "integer": "Int64", "number": "float64", "boolean": "boolean"}


@functools.cache
def _read(name: str) -> dict[str, Any]:
    import json

    raw = resources.files(__name__).joinpath(f"{name}.json.gz").read_bytes()
    obj: dict[str, Any] = json.loads(gzip.decompress(raw).decode("utf-8"))
    if obj.get("format") != _FORMAT:
        raise ValueError(f"unsupported data format {obj.get('format')!r} in {name}.json.gz")
    return obj


@functools.cache
def _frame(name: str) -> pd.DataFrame:
    import pandas as pd

    obj = _read(name)
    n = int(obj.get("nrow", 0))
    data: dict[str, Any] = obj["data"]
    return pd.DataFrame(
        {
            col: pd.array(
                data.get(col) or [None] * n,  # type: ignore[arg-type]
                dtype=_DTYPES.get(kind, object),  # type: ignore[call-overload]
            )
            for col, kind in obj["columns"].items()
        },
        index=pd.RangeIndex(n),
    )


def load_data(name: str) -> pd.DataFrame:
    """The bundled data set *name* (``data/<name>.rda`` in metacheck) as a data frame.

    Read once per process; a fresh copy is returned, so callers may modify it.
    """
    return _frame(name).copy()


def columns(name: str) -> dict[str, list[Any]]:
    """The raw column values of data set *name* (``None`` for missing), read once."""
    return _read(name)["data"]  # type: ignore[no-any-return]
