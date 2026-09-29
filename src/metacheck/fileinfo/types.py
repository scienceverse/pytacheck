"""``metacheck::file_types``: file extension -> coarse file type.

Port of ``data/file_types.rda`` (built upstream by ``data-raw/file_types.R`` from
the dyne/file-extension-list JSON plus metacheck's own additions). The table is
bundled as ``metacheck/resources/data/file_types.json.gz``, generated from R by
``scripts/convert_data.R``, never edited by hand.

The table has two columns, ``ext`` (lower case, without the leading dot; some
are compound, e.g. ``fasta.gz``) and ``type`` (``code``, ``data``, ``stats``,
``archive``, ``text``, ...), sorted by ``ext`` as in R. An extension can have
several rows (``json`` is both ``code`` and ``data``).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["ext_rows", "ext_types", "file_types"]


@functools.cache
def _pairs() -> tuple[tuple[str, str], ...]:
    """The rows of ``metacheck::file_types`` as ``(ext, type)`` pairs, in order."""
    from metacheck.resources.data import columns

    data = columns("file_types")
    return tuple(zip(data["ext"], data["type"], strict=True))


def file_types() -> pd.DataFrame:
    """``metacheck::file_types`` (``data/file_types.rda``) as a data frame.

    Columns ``ext`` and ``type`` (both ``string``), one row per extension/type
    pair, sorted by ``ext``. A fresh copy is returned, so callers may modify it.
    """
    from metacheck.resources.data import load_data

    return load_data("file_types")


@functools.cache
def ext_types() -> dict[str, str]:
    """Extension -> its types pasted with ``";"`` in table order (``json`` -> ``"code;data"``).

    This is ``left_join(ext, file_types, by = "ext")`` followed by
    ``summarise(type = paste(type, collapse = ";"))``, as in
    :func:`~metacheck.fileinfo.category.filetype`.
    """
    out: dict[str, list[str]] = {}
    for ext, typ in _pairs():
        out.setdefault(ext, []).append(typ)
    return {ext: ";".join(types) for ext, types in out.items()}


@functools.cache
def ext_rows() -> dict[str, tuple[tuple[int, str], ...]]:
    """Extension -> its ``(row index, type)`` pairs (0-based rows of the table)."""
    out: dict[str, list[tuple[int, str]]] = {}
    for i, (ext, typ) in enumerate(_pairs()):
        out.setdefault(ext, []).append((i, typ))
    return {ext: tuple(rows) for ext, rows in out.items()}
