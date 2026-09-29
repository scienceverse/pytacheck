"""The psychometric scale dictionary (port of ``R/scales.R`` / ``data/scales.rda``).

metacheck ships ``scales``, a curated dictionary of psychometric instruments
used by ``codebook_check`` to identify named scales in shared data by
matching column-name prefixes / acronyms. It combines the OpenScales
community repository with curated additions of widely used instruments;
acronyms that collide (``AQ`` = Autism Spectrum Quotient and Aggression
Questionnaire) are kept and disambiguated at match time.

The data are converted from ``data/scales.rda`` by
``scripts/convert_datacheck_data.R`` into
``metacheck/resources/data/scales.json.gz``.
"""

from __future__ import annotations

import functools
import gzip
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["scales"]

_FORMAT = 1
_DTYPES = {"string": "string", "integer": "Int64", "number": "float64", "boolean": "boolean"}


def _frame(obj: dict[str, Any]) -> pd.DataFrame:
    import pandas as pd

    columns: dict[str, str] = obj["columns"]
    data: dict[str, Any] = obj["data"]
    n = int(obj.get("nrow", 0))
    return pd.DataFrame(
        {
            name: pd.array(
                data.get(name) or [None] * n,  # type: ignore[arg-type]
                dtype=_DTYPES.get(kind, object),  # type: ignore[call-overload]
            )
            for name, kind in columns.items()
        },
        index=pd.RangeIndex(n),
    )


@functools.cache
def _bundled(name: str) -> pd.DataFrame:
    """A data frame from ``metacheck/resources/data/<name>.json.gz`` (read once)."""
    import orjson

    raw = resources.files("metacheck.resources").joinpath(f"data/{name}.json.gz").read_bytes()
    obj = orjson.loads(gzip.decompress(raw))
    if obj.get("format") != _FORMAT:
        raise ValueError(f"unsupported data format {obj.get('format')!r} in {name}.json.gz")
    return _frame(obj)


def scales() -> pd.DataFrame:
    """Psychometric Scale Dictionary (metacheck's ``scales`` data set).

    Port of ``R/scales.R`` (``data/scales.rda``): one row per instrument with
    columns ``name`` (canonical full name), ``acronym`` (short trigger
    acronym, empty when none is safe; may be shared by several instruments),
    ``code`` (OpenScales code, empty for curated additions) and ``source``
    (``"openscales"`` or ``"curated"``). Returns a fresh copy.
    """
    return _bundled("scales").copy()
