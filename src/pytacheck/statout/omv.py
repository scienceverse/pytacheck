"""Read jamovi (``.omv``) data archives.

Port of ``R/omv.R``, the jamovi counterpart of :mod:`pytacheck.statout.jasp`
returning the same contract. An ``.omv`` is a ZIP archive with
``metadata.json`` (fields, row count), ``xdata.json`` (value labels), a
column-major ``data.bin`` (Decimal: little-endian double, Integer: int32,
Text: int32 index into ``strings.bin``), ``strings.bin`` (a NUL-separated
string pool) and one protobuf blob per analysis (``"NN <name>/analysis"``),
from which the reproducible R call (``jmv::ttestIS(...)``) is recovered as
text.

Variable and value labels live in ``data.attrs["col_attrs"][column]``
(``"label"``, and ``"labels"`` as ``(label, code)`` pairs), as for
:func:`pytacheck.statout.jasp.import_jasp`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from pytacheck._r import gregexpr_all, grepl, gsub, r_sort_key, trimws
from pytacheck.statout.jasp import (
    _check_field_names,
    _column_attrs,
    _dollar,
    _export_archive_html,
    _field_list,
    _frame_from_columns,
    _labels_from_list,
    _r_chr,
    _read_doubles,
    _read_int32s,
    _read_json,
)
from pytacheck.statout.spv import _unzip

__all__ = ["export_omv_html", "import_omv"]

_OMV_INT_MIN = -2147483648  # jamovi/JASP integer missing-value sentinel
# raw[!(raw >= 32 & raw <= 126)] <- as.raw(32): every non-printable byte becomes a space
_PRINTABLE = bytes(b if 32 <= b <= 126 else 32 for b in range(256))


def import_omv(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a jamovi (.omv) file.

    Port of R/omv.R::import_omv(). The jamovi counterpart of
    :func:`pytacheck.statout.jasp.import_jasp`.

    Args:
        path: path to a ``.omv`` file.

    Returns:
        A dict with ``data`` (a data frame; labels in ``data.attrs``),
        ``columns`` (a data frame of ``name`` and ``type``), ``analyses`` (one
        string per analysis: its folder name and, when recoverable, the
        reproducible R syntax), ``format`` (``"jamovi"``) and
        ``data_file_path`` (``None``: jamovi does not record it).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if it is not a readable ``.omv`` archive.
    """
    import numpy as np
    import pandas as pd

    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.omv$", path, ignore_case=True):
        raise ValueError(f"Not a .omv file: {path}")
    with tempfile.TemporaryDirectory(prefix="omv_") as tmp:
        files = _unzip(path, tmp)
        if not files:
            raise ValueError(f"Could not open '{os.path.basename(path)}' as a .omv (zip) archive.")
        base = [os.path.basename(f) for f in files]
        if "metadata.json" not in base or "data.bin" not in base:
            raise ValueError(
                "Unrecognised .omv: no 'metadata.json'/'data.bin' entry in "
                f"{os.path.basename(path)}."
            )
        meta = _read_json(files[base.index("metadata.json")])
        xdat = _read_json(files[base.index("xdata.json")]) if "xdata.json" in base else {}
        ds = _dollar(meta, "dataSet")
        fields = _field_list(_dollar(ds, "fields"))
        nrow = _dollar(ds, "rowCount")
        if nrow is None:
            nrow = 0

        pool: list[str] = []
        if "strings.bin" in base:
            raw = Path(files[base.index("strings.bin")]).read_bytes()
            pool = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00")]

        cols: list[Any] = []
        names: list[str] = []
        types: list[Any] = []
        col_attrs: list[dict[str, Any]] = []
        with open(files[base.index("data.bin")], "rb") as fh:
            for f in fields:
                dt = _dollar(f, "dataType")
                if dt is None:
                    dt = "Integer"
                mt = _dollar(f, "measureType")
                if mt is None:
                    mt = "Nominal"
                name = _dollar(f, "name")
                labs = None
                if dt == "Decimal":
                    cols.append(np.asarray(_read_doubles(fh, int(nrow)), dtype="float64"))
                else:
                    idx = _read_int32s(fh, int(nrow))
                    if dt == "Text":
                        cols.append(
                            pd.array(
                                [
                                    pool[i] if i is not None and 0 <= i < len(pool) else None
                                    for i in idx
                                ],
                                dtype="string",
                            )
                        )
                    else:
                        cols.append(pd.array(idx, dtype="Int64"))
                        labs = _omv_labels(f, xdat)
                ttl = _dollar(f, "description")
                if ttl is None:
                    ttl = _dollar(f, "title")
                if ttl is None:
                    ttl = ""
                label = _r_chr(ttl) if ttl != "" and ttl != name else None
                col_attrs.append(_column_attrs(labs, label))
                names.append(name)
                types.append(mt)
        _check_field_names(names)
        df = _frame_from_columns(cols, names, col_attrs)
        analyses = _omv_analyses_summary(files)
    columns = (
        pd.DataFrame(
            {"name": pd.array(names, dtype="string"), "type": pd.array(types, dtype="string")}
        )
        if fields
        else None
    )
    return {
        "data": df,
        "columns": columns,
        "analyses": analyses,
        "format": "jamovi",
        "data_file_path": None,
    }


def _omv_labels(field: Any, xdat: Any) -> list[tuple[str | None, float]]:
    """Port of R/omv.R::.omv_labels(): ``(label, code)`` pairs for one field."""
    lst = _dollar(field, "labels")
    if not lst:
        name = _dollar(field, "name")
        x = xdat.get(name) if isinstance(xdat, dict) and isinstance(name, str) else None
        lst = _dollar(x, "labels") if x is not None else None
    return _labels_from_list(lst)


def _omv_analyses_summary(files: list[str]) -> list[str]:
    """Port of R/omv.R::.omv_analyses_summary(): one line per analysis blob."""
    entries = [f for f in files if os.path.basename(f) == "analysis"]
    if not entries:
        return []
    entries = sorted(entries, key=lambda e: r_sort_key(os.path.basename(os.path.dirname(e))))
    out = []
    for i, entry in enumerate(entries, 1):
        name = os.path.basename(os.path.dirname(entry))
        try:
            raw = Path(entry).read_bytes()
        except OSError:
            raw = b""
        txt = raw.translate(_PRINTABLE).decode("ascii")
        syntax = _omv_extract_syntax(txt)
        out.append(f"{i}. {name}  |  {syntax}" if syntax != "" else f"{i}. {name}")
    return out


def _is_pkg_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == ".")


def _omv_extract_syntax(txt: str) -> str:
    """Port of R/omv.R::.omv_extract_syntax().

    Finds the first ``<pkg>::<fn>(`` call, walks left over the package name
    and right to the balancing parenthesis. ``""`` when there is none.
    """
    m = gregexpr_all("::[A-Za-z0-9_.]+[ \t]*\\(", txt)  # R "[ \t]": a real tab
    if not m:
        return ""
    colons, length = m[0]
    left = colons - 1
    while left >= 1 and _is_pkg_char(txt[left - 1]):
        left -= 1
    start = left + 1
    open_ = colons + length - 1
    depth = 0
    end = open_
    for k in range(open_, len(txt) + 1):
        ch = txt[k - 1]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = k
                break
    if depth != 0:
        return ""
    return str(gsub("[[:space:]]+", " ", trimws(txt[start - 1 : end])))


def export_omv_html(path: str | os.PathLike[str], out: str | os.PathLike[str] | None = None) -> str:
    """Export a jamovi (.omv) file's own rendered output as standalone HTML.

    Port of R/omv.R::export_omv_html(). Extracts the archive's ``index.html``
    and inlines every plot it references as a base64 ``data:`` URI.

    Args:
        path: path to a ``.omv`` file.
        out: path to write the HTML file to; defaults to ``path`` with its
            extension replaced by ``.html``.

    Returns:
        The path written to.
    """
    return _export_archive_html(
        os.fspath(path), None if out is None else os.fspath(out), "omv", "omv", "omvexport_"
    )
