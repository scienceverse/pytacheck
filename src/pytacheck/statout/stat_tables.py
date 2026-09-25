"""Extract rendered statistical result tables from JASP, jamovi and notebooks.

Port of ``R/stat-tables.R``: :func:`read_stat_tables` reads a ``.jasp``
(its structured ``analyses.json``), a ``.omv`` (jamovi's protobuf
``AnalysisResponse`` blobs, decoded natively), a ``.spv`` (via
:mod:`pytacheck.statout.spv`), a ``.ipynb`` (saved cell outputs) or, as a
fallback, any archive's rendered ``index.html``, and returns every result
table as a tidy data frame with the analysis it belongs to.

Each table is a ``dict`` with ``analysis``, ``title``, ``data`` (a
:class:`pandas.DataFrame` of ``string`` columns) and ``table_index`` (plus
``analysis_id`` for the structured JASP/jamovi paths and ``call_fn`` for
parsed notebook statistic lines). jamovi's declared column roles are kept in
``data.attrs["col_roles"]`` (R's ``attr(df, "col_roles")``).
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character, format_num
from pytacheck._r.regex import compile_r, grepl, gsub, regexec, regextract_all, strsplit, sub
from pytacheck.statout.r_output import (
    _chr_frame,
    _make_unique,
    _r_as_numeric,
    _r_dollar,
    _r_output_oneline,
    _r_output_tables,
    _r_values,
    _RError,
    _RNamedList,
    _trimws,
)

__all__ = ["read_stat_tables"]


# ---------------------------------------------------------------------------
# read_stat_tables()
# ---------------------------------------------------------------------------


def read_stat_tables(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read the statistical result tables from a JASP, jamovi or notebook file.

    Port of ``R/stat-tables.R::read_stat_tables()``. Tries, in order, a
    ``.ipynb`` notebook's saved outputs, a ``.jasp`` archive's
    ``analyses.json``, a ``.omv`` archive's protobuf analyses, a ``.spv``
    archive's own table structure, and finally the archive's rendered
    ``index.html``.

    Returns
    -------
    list of dict
        One element per result table with ``analysis``, ``title``, ``data``
        and ``table_index``; empty when nothing is found.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    if grepl("\\.ipynb$", path, ignore_case=True):
        return _ipynb_read_tables(path)

    with tempfile.TemporaryDirectory(prefix="stattbl_") as tmp:
        files = _unzip(path, tmp)
        base = [os.path.basename(f) for f in files]

        aj = [f for f, b in zip(files, base, strict=True) if b == "analyses.json"]
        if aj:
            try:
                structured = _jasp_structured_tables(aj[0])
            except ImportError:
                raise
            except Exception:
                structured = None
            if structured:
                return structured

        af = [f for f, b in zip(files, base, strict=True) if b == "analysis"]
        if af:

            def order_key(f: str) -> int:
                folder = os.path.basename(os.path.dirname(f))
                num = sub("^\\s*(\\d+).*$", "\\1", folder)
                val = _r_as_numeric(num)
                if val is None or math.isnan(val) or abs(val) >= 2**31:
                    return 2**31 - 1
                return int(val)

            af = sorted(af, key=order_key)
            try:
                structured = _jmv_structured_tables(af)
            except ImportError:
                raise
            except Exception:
                structured = None
            if structured:
                return structured

        sv = [
            f
            for f, b in zip(files, base, strict=True)
            if grepl("^outputViewer[0-9]+(_heading)?\\.xml$", b)
        ]
        if sv:
            from pytacheck.statout.spv import _spv_read  # type: ignore[import-not-found]

            try:
                structured = _spv_read(tmp)
            except Exception:
                structured = None
            if structured:
                return list(structured)

        hp = [f for f, b in zip(files, base, strict=True) if b == "index.html"]
        if not hp:
            return []
        doc = _read_html_file(hp[0])
        if doc is None:
            return []
        tables = doc.xpath("//table")
        if not tables:
            return []
        out: list[dict[str, Any]] = []
        for i, tb in enumerate(tables, start=1):
            heads = tb.xpath("preceding::*[self::h1 or self::h2 or self::h3 or self::h4]")
            analysis = _trimws(_xml_text(heads[-1])) if heads else None
            parsed = _stat_table_parse(tb)
            if parsed is None:
                continue
            out.append(
                {
                    "analysis": analysis,
                    "title": parsed["title"],
                    "data": parsed["data"],
                    "table_index": i,
                }
            )
        return out


def _unzip(path: str, exdir: str) -> list[str]:
    """``utils::unzip(path, exdir = exdir)``: the extracted file paths (zip order).

    A path that is not a zip archive (a directory, another file, or a zip
    without any entry) has nothing to extract: ``[]``, so
    ``read_stat_tables()`` finds no tables. R's ``unzip()`` returns ``NULL``
    there and ``basename(NULL)`` fails ("a character vector argument
    expected", UPSTREAM_ISSUES U137). Directory entries are created but not
    returned, and extraction stops at the first unreadable entry, keeping what
    was extracted before it.
    """
    if not os.path.isfile(path) or not zipfile.is_zipfile(path):
        return []
    out: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if not infos:
                return []
            root = os.path.realpath(exdir)
            for info in infos:
                target = os.path.realpath(os.path.join(exdir, info.filename))
                if not target.startswith(root + os.sep):
                    continue
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src:
                    payload = src.read()
                with open(target, "wb") as dst:
                    dst.write(payload)
                out.append(os.path.join(exdir, info.filename))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError, EOFError):
        pass
    return out


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _stat_num_to_chr(v: Any) -> str | None:
    """A stored number as the text the pipeline works in.

    Port of ``R/stat-tables.R::.stat_num_to_chr()``: whole numbers below
    1e15 print in full (``113``), everything else with 15 significant
    digits (R's ``format(v, digits = 15)``); ``Inf``/``NaN`` keep their names.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"  # format(TRUE) in R
    if isinstance(v, int) and abs(v) < 2**31:  # an R integer
        return str(v)
    v = float(v)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    if v == math.floor(v) and abs(v) < 1e15:
        return str(int(v))
    return format_num(v, digits=15)


# ---------------------------------------------------------------------------
# JASP (analyses.json)
# ---------------------------------------------------------------------------


def _jasp_clean_colname(x: Any) -> str:
    """Strip JASP's ``JaspColumn_.21._Encoded_`` column-name prefix.

    Port of ``R/stat-tables.R::.jasp_clean_colname()``.
    """
    if x is None:
        x = ""
    s = as_character(x)
    x = "NA" if s is None else s
    out = str(sub("^JaspColumn_.*?_Encoded_", "", x))
    return x if not _trimws(out) else out


def _r_list_get(node: Any, key: str) -> Any:
    """R ``node[[key]]`` for a parsed JSON value (``None`` for ``NULL``)."""
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get(key)
    if isinstance(node, list):
        return None
    raise _RError("subscript out of bounds")


def _jasp_structured_tables(analyses_json: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a ``.jasp`` archive's structured ``analyses.json`` results.

    Port of ``R/stat-tables.R::.jasp_structured_tables()``.
    """
    j = _read_json(analyses_json)
    analyses = _r_dollar(j, "analyses")
    if not analyses:
        return []
    out: list[dict[str, Any]] = []
    for an in _r_iter(analyses):
        an_title = _trimws(_chr1(_r_dollar(an, "title"), "")) or ""
        an_name = _trimws(_chr1(_r_dollar(an, "name"), "")) or ""
        label = an_title if an_title else (an_name if an_name else None)
        an_id: Any = _r_dollar(an, "id")
        if isinstance(an_id, list | dict):
            # as.character(list(x)) of a length-1 list (JSON [x] / {"k": x})
            ids = _r_values(an_id)
            an_id = _chr_list_elt(ids[0]) if len(ids) == 1 else None
        elif an_id is not None:
            an_id = as_character(an_id)

        def collect(node: Any, label: str | None = label, an_id: str | None = an_id) -> None:
            if not isinstance(node, dict | list):
                return
            fields = _r_list_get(_r_list_get(node, "schema"), "fields")
            data = _r_list_get(node, "data")
            if fields is not None and data is not None:
                df = _jasp_table_to_df(fields, data)
                if df is not None and len(df) and df.shape[1]:
                    ttl = _trimws(_chr1(_r_dollar(node, "title"), "")) or ""
                    out.append(
                        {
                            "analysis": label,
                            "analysis_id": an_id,
                            "title": ttl if ttl else None,
                            "data": df,
                        }
                    )
                return
            for child in _r_iter(node):
                collect(child)

        collect(_r_dollar(an, "results"))
    for i, tb in enumerate(out, start=1):
        tb["table_index"] = i
    return out


def _jasp_table_to_df(fields: Any, data_rows: Any) -> pd.DataFrame | None:
    """One JASP result table (schema fields + data rows) as a data frame.

    Port of ``R/stat-tables.R::.jasp_table_to_df()``: columns named by the
    fields' machine ``name``; numbers kept at full precision as text; a
    missing cell becomes ``""``.
    """
    fields_l = _r_iter(fields)
    raw_nms = [_chr1(_r_dollar(f, "name"), "") for f in fields_l]
    raw_nms = [n for n in raw_nms if n]
    if not raw_nms or not _r_len(data_rows):
        return None
    nms = [_jasp_clean_colname(n) for n in raw_nms]
    rows = _r_iter(data_rows)

    def cell(row: Any, n: str) -> str | None:
        # an atomic row (row[n] is NA) and an unnamed list give "" like NULL
        v = row.get(n) if isinstance(row, dict) else None
        if v is None:
            return ""
        if isinstance(v, list | dict):
            vals = _r_values(v)
            if len(vals) != 1:
                raise _RError("values must be length 1")
            return _chr_list_elt(vals[0])
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, int | float):
            return _stat_num_to_chr(v)
        return str(v)

    cols = [[cell(row, n) for row in rows] for n in raw_nms]
    return _chr_frame(_make_unique(nms), cols)


_INT32_MAX = 2147483647


def _json_int(s: str) -> int | float:
    """jsonlite's integer rule: 32-bit integers stay integer, others are doubles."""
    n = int(s)
    return n if -_INT32_MAX <= n <= _INT32_MAX else float(n)


def _reject_constant(const: str) -> Any:
    raise ValueError(f"invalid JSON constant {const}")


def _parse_json_text(text: str) -> Any:
    """``jsonlite::fromJSON(<text>, simplifyVector = FALSE)``.

    Objects become :class:`_RNamedList` (duplicate names kept, the first one
    wins for ``$``/``[[``); a leading UTF-8 byte-order mark is accepted with
    jsonlite's warning; ``NaN``/``Infinity`` literals are rejected.
    """
    if text.startswith("\ufeff"):
        import warnings

        warnings.warn("JSON string contains (illegal) UTF8 byte-order-mark!", stacklevel=3)
        text = text[1:]
    return json.loads(
        text,
        object_pairs_hook=_RNamedList,
        parse_constant=_reject_constant,
        parse_int=_json_int,
    )


def _read_json(path: str | os.PathLike[str]) -> Any:
    """``jsonlite::fromJSON(<path>, simplifyVector = FALSE)`` of a UTF-8 file."""
    with open(path, encoding="utf-8") as f:
        return _parse_json_text(f.read())


def _r_iter(x: Any) -> list[Any]:
    """The elements of a parsed JSON value, as ``for (e in x)`` sees them."""
    return _r_values(x)


def _r_len(x: Any) -> int:
    """R ``length()`` of a parsed JSON value."""
    if x is None:
        return 0
    if isinstance(x, _RNamedList):
        return len(x.pairs)
    if isinstance(x, dict | list):
        return len(x)
    return 1


def _chr_list_elt(v: Any) -> str:
    """``as.character(list(v))`` for one list element (``NULL`` -> ``"NULL"``)."""
    if v is None:
        return "NULL"
    if isinstance(v, list | dict):
        vals = _r_values(v)
        if not vals:
            return "list()"
        raise _RError("values must be length 1")
    s = as_character(v)
    return "NA" if s is None else s


def _chr1(x: Any, default: str) -> str:
    """``as.character(x %||% default)`` for a scalar-ish JSON value."""
    if x is None:
        return default
    if isinstance(x, list | dict):
        vals = _r_iter(x)
        if len(vals) != 1:
            raise _RError("values must be length 1")
        x = vals[0]
    s = as_character(x)
    return "NA" if s is None else s


# ---------------------------------------------------------------------------
# Minimal protobuf wire-format reader (jamovi .omv)
# ---------------------------------------------------------------------------


def _pb_bytes(raw: Any) -> bytes | list[int]:
    """The byte values ``as.integer(raw[pos])`` reads (``bytes`` index to ints).

    A number (a varint/fixed value handed back to ``.pb_fields()``) is a
    length-1 vector whose ``as.integer()`` truncates; outside the 32-bit
    range it is ``NA`` and R's ``bitwAnd()`` test then errors.
    """
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray | memoryview):
        return bytes(raw)
    if isinstance(raw, float | int) and not isinstance(raw, bool):
        if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
            raise _RError("missing value where TRUE/FALSE needed")
        if not -_INT32_MAX <= int(raw) <= _INT32_MAX:
            raise _RError("missing value where TRUE/FALSE needed")
        return [int(raw)]
    raise _RError("invalid raw input")


def _pb_varint(raw: Any, pos: int) -> dict[str, Any]:
    """Read one base-128 varint at 1-based *pos*; ``{"value", "pos"}``.

    Port of ``R/stat-tables.R::.pb_varint()`` (value accumulated as a double).
    """
    b_all = raw if isinstance(raw, list | bytes) else _pb_bytes(raw)
    res = 0.0
    shift = 0
    while True:
        if pos > len(b_all):
            return {"value": res, "pos": pos}
        b = b_all[pos - 1]
        pos += 1
        res = res + (b & 127) * (2.0**shift)
        if (b & 128) == 0:
            break
        shift += 7
        if shift > 63:
            break
    return {"value": res, "pos": pos}


def _pb_fields(raw: Any) -> list[dict[str, Any]] | None:
    """Split one protobuf message into ``{"field", "wire", "value"}`` entries.

    Port of ``R/stat-tables.R::.pb_fields()``; ``None`` on malformed input.
    A length-delimited value is returned as ``bytes``.
    """
    data = _pb_bytes(raw)
    out: list[dict[str, Any]] = []
    pos = 1
    n = len(data)
    while pos <= n:
        v = _pb_varint(data, pos)
        key = v["value"]
        pos = v["pos"]
        if key == 0:
            return None
        if key >= 2**31:
            raise _RError("missing value where TRUE/FALSE needed")
        ikey = int(key)
        fld = ikey >> 3
        wt = ikey & 7
        val: Any
        if wt == 0:
            v = _pb_varint(data, pos)
            val = v["value"]
            pos = v["pos"]
        elif wt == 2:
            v = _pb_varint(data, pos)
            length = v["value"]
            pos = v["pos"]
            if length < 0 or pos + length - 1 > n:
                return None
            ln = int(length)
            val = bytes(data[pos - 1 : pos - 1 + ln])
            pos = pos + ln
        elif wt == 1:
            if pos + 7 > n:
                return None
            val = struct.unpack("<d", bytes(data[pos - 1 : pos + 7]))[0]
            pos += 8
        elif wt == 5:
            if pos + 3 > n:
                return None
            val = struct.unpack("<f", bytes(data[pos - 1 : pos + 3]))[0]
            pos += 4
        else:
            return None
        out.append({"field": fld, "wire": wt, "value": val})
    return out


def _pb_all(fields: Sequence[Mapping[str, Any]], n: int) -> list[Any]:
    """All values for field number *n*.

    Port of ``R/stat-tables.R::.pb_all()``.
    """
    return [x["value"] for x in fields if x["field"] == n]


def _pb_get(fields: Sequence[Mapping[str, Any]], n: int) -> Any:
    """The first value for field number *n*, or ``None``.

    Port of ``R/stat-tables.R::.pb_get()``.
    """
    for x in fields:
        if x["field"] == n:
            return x["value"]
    return None


def _pb_str(x: Any) -> str:
    """A length-delimited payload as a string; ``""`` on failure.

    Port of ``R/stat-tables.R::.pb_str()``.
    """
    if x is None:
        return ""
    if not isinstance(x, bytes | bytearray):
        return ""
    # rawToChar() drops trailing nuls and errors on an embedded one.
    x = bytes(x).rstrip(b"\x00")
    if b"\x00" in x:
        return ""
    return x.decode("utf-8", errors="replace")


# jamovi field numbers (``.JMV_F``; see inst/schema/jamovi/PROVENANCE.md).
_JMV_F = {
    "resp_name": 3,
    "resp_results": 7,
    "el_name": 1,
    "el_title": 2,
    "el_table": 6,
    "el_group": 8,
    "el_array": 9,
    "container_elements": 1,
    "tbl_columns": 1,
    "col_name": 1,
    "col_title": 2,
    "col_type": 3,
    "col_format": 4,
    "col_cells": 7,
    "cell_i": 1,
    "cell_d": 2,
    "cell_s": 3,
    "cell_o": 4,
}


def _num_value_chr(v: Any) -> str | None:
    if isinstance(v, bytes | bytearray):
        if len(v) != 1:
            raise _RError("the condition has length > 1")
        return f"{v[0]:02x}"
    return _stat_num_to_chr(v)


def _jmv_cell(cell_raw: Any) -> str | None:
    """One jamovi ``ResultsCell`` as text; an explicit MISSING is ``""``.

    Port of ``R/stat-tables.R::.jmv_cell()``.
    """
    f = _pb_fields(cell_raw)
    if f is None:
        return ""
    for x in f:
        if x["field"] in (_JMV_F["cell_i"], _JMV_F["cell_d"]):
            return _num_value_chr(x["value"])
        if x["field"] == _JMV_F["cell_s"]:
            return _pb_str(x["value"])
        if x["field"] == _JMV_F["cell_o"]:
            return ""
    return ""


_BRACKET = "\\[[^]]+\\]$"


def _jmv_is_wide_descriptives(nms: Sequence[str], nrow_df: int) -> bool:  # noqa: ARG001
    """Is this jamovi's wide ``<variable>[<statistic>]`` layout?

    Port of ``R/stat-tables.R::.jmv_is_wide_descriptives()``.
    """
    from pytacheck.statout.stato_map import stato_type_column  # type: ignore[import-not-found]

    nms = list(nms)
    if len(nms) < 6:
        return False
    has_br = grepl(_BRACKET, nms)
    if sum(has_br) / len(has_br) < 0.6:
        return False
    br = [n for n, h in zip(nms, has_br, strict=True) if h]
    prefix = sub(_BRACKET, "", br)
    suffix = sub(".*\\[([^]]+)\\]$", "\\1", br)
    np_ = len(dict.fromkeys(prefix))
    ns_ = len(dict.fromkeys(suffix))
    if np_ < 2 or ns_ < 2:
        return False

    def typed_frac(x: Sequence[str]) -> float:
        keys = list(dict.fromkeys((_trimws(v) or "").lower() for v in x))
        if not keys:
            return 0.0
        typed = [bool(_typ(stato_type_column(k), "termSource")) for k in keys]
        return sum(typed) / len(typed)

    if typed_frac(prefix) > typed_frac(suffix):
        return False
    return len(br) >= 0.75 * np_ * ns_


def _typ(typ: Any, key: str) -> str:
    """A field of :func:`stato_type_column`'s result (``typ$key``)."""
    if isinstance(typ, Mapping):
        val = typ.get(key, "")
    else:
        val = getattr(typ, key, "")
    return "" if val is None else str(val)


def _jmv_pivot_wide_descriptives(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot jamovi's wide descriptives/crosstab layout to one row per variable.

    Port of ``R/stat-tables.R::.jmv_pivot_wide_descriptives()``.
    """
    nms = [str(c) for c in df.columns]
    has_br = grepl(_BRACKET, nms)
    prefix = sub(_BRACKET, "", nms)
    suffix = sub(".*\\[([^]]+)\\]$", "\\1", nms)
    keep = [h and p != "stat" and p != "" for h, p in zip(has_br, prefix, strict=True)]
    if not any(keep):
        return df
    vars_ = list(dict.fromkeys(p for p, k in zip(prefix, keep, strict=True) if k))
    stats = list(dict.fromkeys(s for s, k in zip(suffix, keep, strict=True) if k))
    label_cols = [i for i, (h, n) in enumerate(zip(has_br, nms, strict=True)) if not h and n != ""]
    nr = len(df)
    grid = [(r, v) for v in vars_ for r in range(nr)]
    cols: dict[str, list[Any]] = {"name": [v for _, v in grid]}
    values = [df.iloc[:, i].tolist() for i in range(df.shape[1])]
    for lc in label_cols:
        col = [_cell_chr(values[lc][r]) for r, _ in grid]
        cols[nms[lc]] = col
    for s in stats:
        col = []
        for r, v in grid:
            hit = [i for i in range(len(nms)) if keep[i] and prefix[i] == v and suffix[i] == s]
            col.append("" if not hit else _cell_chr(values[hit[0]][r]))
        cols[s] = col
    return _chr_frame(list(cols), list(cols.values()))


def _cell_chr(v: Any) -> str | None:
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return None
    return as_character(v)


def _jmv_table_to_df(tbl_raw: Any) -> pd.DataFrame | None:
    """One jamovi ``ResultsTable`` as a data frame of machine column names.

    Port of ``R/stat-tables.R::.jmv_table_to_df()``; the declared column
    roles are kept in ``df.attrs["col_roles"]``.
    """
    tf = _pb_fields(tbl_raw)
    if tf is None:
        return None
    cols = _pb_all(tf, _JMV_F["tbl_columns"])
    if not cols:
        return None
    parsed: list[dict[str, Any]] = []
    for cb in cols:
        cf = _pb_fields(cb)
        if cf is None:
            continue
        nm = _pb_str(_pb_get(cf, _JMV_F["col_name"]))
        ti = _pb_str(_pb_get(cf, _JMV_F["col_title"]))
        cells = _pb_all(cf, _JMV_F["col_cells"])
        parsed.append(
            {
                "name": nm if nm else ti,
                "type": _pb_str(_pb_get(cf, _JMV_F["col_type"])),
                "format": _pb_str(_pb_get(cf, _JMV_F["col_format"])),
                "values": [_jmv_cell(c) for c in cells],
            }
        )
    parsed = [p for p in parsed if p["name"]]
    if not parsed:
        return None
    nrows = max(len(p["values"]) for p in parsed)
    if nrows == 0:
        return None
    cols_out = [p["values"] + [""] * (nrows - len(p["values"])) for p in parsed]
    final_names = _make_unique([p["name"] for p in parsed])
    df = _chr_frame(final_names, cols_out)
    df.attrs["col_roles"] = {
        n: {"type": p["type"], "format": p["format"]}
        for n, p in zip(final_names, parsed, strict=True)
    }
    if _jmv_is_wide_descriptives(final_names, len(df)):
        df = _jmv_pivot_wide_descriptives(df)
        df.attrs.pop("col_roles", None)
    return df


def _jmv_collect(
    el_raw: Any,
    analysis_label: str | None,
    acc: list[dict[str, Any]],
    analysis_id: str | None = None,
) -> list[dict[str, Any]]:
    """Walk one ``ResultsElement`` tree, collecting every table into *acc*.

    Port of ``R/stat-tables.R::.jmv_collect()``.
    """
    f = _pb_fields(el_raw)
    if f is None:
        return acc
    title = _pb_str(_pb_get(f, _JMV_F["el_title"]))
    tbl = _pb_get(f, _JMV_F["el_table"])
    if tbl is not None:
        df = _jmv_table_to_df(tbl)
        if df is not None and len(df) and df.shape[1]:
            acc.append(
                {
                    "analysis": analysis_label,
                    "analysis_id": analysis_id,
                    "title": title if title else None,
                    "data": df,
                }
            )
    for fld in (_JMV_F["el_group"], _JMV_F["el_array"]):
        sub_el = _pb_get(f, fld)
        if sub_el is None:
            continue
        sf = _pb_fields(sub_el)
        if sf is None:
            continue
        for child in _pb_all(sf, _JMV_F["container_elements"]):
            acc = _jmv_collect(child, analysis_label, acc, analysis_id)
    return acc


def _jmv_structured_tables(analysis_files: Sequence[str]) -> list[dict[str, Any]]:
    """Decode every ``<n> <name>/analysis`` blob of a ``.omv`` archive.

    Port of ``R/stat-tables.R::.jmv_structured_tables()``.
    """
    out: list[dict[str, Any]] = []
    for p in analysis_files:
        try:
            raw = Path(p).read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        top = _pb_fields(raw)
        if top is None:
            continue
        label = _pb_str(_pb_get(top, _JMV_F["resp_name"]))
        results = _pb_get(top, _JMV_F["resp_results"])
        if results is None:
            continue
        aid_raw = _pb_get(top, 2)
        if isinstance(aid_raw, bytes | bytearray):
            aid = f"{aid_raw[0]:02x}" if len(aid_raw) == 1 else os.path.basename(os.path.dirname(p))
        elif aid_raw is not None:
            aid_s = as_character(aid_raw)
            aid = "NA" if aid_s is None else aid_s
        else:
            aid = os.path.basename(os.path.dirname(p))
        out = _jmv_collect(results, label if label else None, out, aid)
    for i, tb in enumerate(out, start=1):
        tb["table_index"] = i
    return out


# ---------------------------------------------------------------------------
# HTML tables
# ---------------------------------------------------------------------------


def _html_parser() -> Any:
    from lxml import etree

    # xml2::read_html()'s options: RECOVER, NOERROR, NOBLANKS. The text is
    # decoded before parsing (see _html_encoding()), so the parser reads UTF-8.
    return etree.HTMLParser(recover=True, remove_blank_text=True, encoding="utf-8")


_META_CHARSET = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.I)


def _html_encoding(data: bytes) -> str:
    """The encoding of an HTML file: its byte-order mark or ``<meta charset>``, else UTF-8.

    The declaration is looked for in the first 1024 bytes, as browsers do,
    and resolved as the HTML standard says (a declared UTF-16 is UTF-8, and
    ``iso-8859-1``/``ascii`` are windows-1252). A file without one is UTF-8.
    metacheck's result depends on the xml2 build: with libxml2 2.15 the
    declaration is ignored, so a windows-1252 export (``café``, ``±``) is
    read with U+FFFD in place of its accented letters and symbols
    (UPSTREAM_ISSUES U141).
    """
    import codecs

    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    m = _META_CHARSET.search(data[:1024])
    if m is None:
        return "utf-8"
    try:
        name = codecs.lookup(m.group(1).decode("ascii")).name
    except LookupError:
        return "utf-8"
    if name.startswith("utf-16") or name.startswith("utf-32"):
        return "utf-8"
    if name in ("latin-1", "iso8859-1", "ascii"):
        return "cp1252"
    return name


def _read_html_file(path: str) -> Any:
    """``xml2::read_html(<file>)`` (``None`` on failure), in the file's declared encoding."""
    from lxml import etree

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    text = data.decode(_html_encoding(data), errors="replace")
    try:
        root = etree.fromstring(text.encode("utf-8"), _html_parser())
    except (etree.LxmlError, ValueError):
        return None
    return None if root is None else root.getroottree()


def _read_html_string(x: str) -> Any:
    """``xml2::read_html(<string>)``: a literal only when it contains ``<``/``>``."""
    from lxml import etree

    if "<" not in x and ">" not in x:
        return None
    try:
        root = etree.fromstring(
            x.encode("utf-8"),
            etree.HTMLParser(recover=True, remove_blank_text=True, encoding="utf-8"),
        )
    except (etree.LxmlError, ValueError):
        return None
    return None if root is None else root.getroottree()


def _xml_text(node: Any) -> str:
    """``xml2::xml_text()``: the concatenated descendant text."""
    return str(node.xpath("string()"))


def _grid_of(cells: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for c in cells:
        txt = _trimws(gsub("[[:space:]]+", " ", _xml_text(c))) or ""
        cs_raw = c.get("colspan")
        cs_num = _r_as_numeric(cs_raw) if cs_raw is not None else None
        if cs_num is None or math.isnan(cs_num) or abs(cs_num) >= 2**31:
            span = 1
        else:
            span = int(cs_num)
            if span < 1:
                span = 1
        out.extend([txt] * span)
    return out


def _stat_table_parse(tb: Any) -> dict[str, Any] | None:
    """Parse one JASP/jamovi result ``<table>`` (multi-row header).

    Port of ``R/stat-tables.R::.stat_table_parse()``: the header row is the
    last all-``<th>`` row with more than one distinct label; data rows are
    aligned to it on the colspan-expanded grid; footnote rows and spacer
    columns are dropped. ``None`` when there is no header or no data.
    """
    rows = tb.xpath(".//tr")
    if not rows:
        return None
    row_cells = [r.xpath("./th | ./td") for r in rows]
    is_th_row = [len(r.xpath("./td")) == 0 and len(r.xpath("./th")) > 0 for r in rows]

    title: str | None = None
    if is_th_row[0] and len(row_cells[0]) == 1:
        title = _trimws(_xml_text(row_cells[0][0]))

    header_idx: int | None = None
    for j, th in enumerate(is_th_row):
        if not th:
            continue
        labs = [v for v in _grid_of(row_cells[j]) if v]
        if len(set(labs)) > 1:
            header_idx = j
    if header_idx is None:
        return None
    header_grid = _grid_of(row_cells[header_idx])
    gw = len(header_grid)

    data_rows = [k for k, th in enumerate(is_th_row) if not th and k > header_idx]
    if not data_rows:
        return None
    mat: list[list[str | None]] = []
    for k in data_rows:
        v: list[str | None] = list(_grid_of(row_cells[k]))
        v = v[:gw] + [None] * (gw - len(v))
        mat.append(v)

    runs: list[tuple[str, int, int]] = []
    for idx, h in enumerate(header_grid):
        if runs and runs[-1][0] == h:
            runs[-1] = (h, runs[-1][1], idx)
        else:
            runs.append((h, idx, idx))
    names = [r[0] for r in runs]
    cols: list[list[str | None]] = []
    for _, a, b in runs:
        col: list[str | None] = []
        for row in mat:
            nz = [x for x in row[a : b + 1] if x is None or x != ""]
            col.append(nz[0] if nz else "")
        cols.append(col)

    ncol = len(cols)
    nrow = len(mat)
    keep_rows = []
    for ri in range(nrow):
        vals = [_trimws(cols[c][ri]) for c in range(ncol)]
        vals = [v for v in vals if v is None or v != ""]
        if not vals:
            keep_rows.append(False)
            continue
        uniq = list(dict.fromkeys(vals))
        if len(uniq) == 1:
            v1 = uniq[0]
            if v1 is None:
                # no content (a row shorter than the header grid, its cells
                # missing or empty) is dropped like an empty row; in R the
                # footnote test is NA and df[NA, ] adds a row of NAs
                # (UPSTREAM_ISSUES U141)
                keep_rows.append(False)
                continue
            is_note = (
                bool(grepl("^Note", v1, ignore_case=True))
                or bool(grepl("^[*a-z]?\\s*p\\s*[<>=]", v1))
                or len(v1) > 40
            )
            keep_rows.append(not is_note)
            continue
        keep_rows.append(True)
    cols = [[v for v, k in zip(col, keep_rows, strict=True) if k] for col in cols]

    kept = [
        (n, col)
        for n, col in zip(names, cols, strict=True)
        if n != "" or any(v is None or v != "" for v in col)
    ]
    nm = [n if n != "" else f"V{i}" for i, (n, _) in enumerate(kept, start=1)]
    nrow_out = sum(keep_rows)
    if nrow_out == 0:
        return None
    df = _chr_frame(_make_unique(nm), [col for _, col in kept])
    return {"title": title, "data": df}


# ---------------------------------------------------------------------------
# Jupyter notebooks (.ipynb)
# ---------------------------------------------------------------------------


def _ipynb_is_noise(lines: Sequence[str]) -> bool:
    """Is this notebook text output machinery rather than a result?

    Port of ``R/stat-tables.R::.ipynb_is_noise()``.
    """
    lines = list(lines)
    txt = _trimws(" ".join("NA" if v is None else v for v in lines)) or ""
    if not txt:
        return True
    if len(lines) <= 2:
        if grepl("^<Figure size .*Axes>$", txt):
            return True
        if grepl("^plot without title$", txt, ignore_case=True):
            return True
        if grepl("^<[^>]*>$", txt):
            return True
        if grepl("\\|.*\\|.*(it/s|s/it|\\?it/s)", txt):
            return True
        if grepl("^[0-9]+%\\|", txt):
            return True
    if grepl(
        "(FutureWarning|DeprecationWarning|RuntimeWarning|UserWarning|"
        "SettingWithCopyWarning|ConvergenceWarning):",
        txt,
    ):
        return True
    return bool(grepl("^Traceback \\(most recent call last\\)", txt))


def _ipynb_result_class(line: str) -> str | None:
    """The ``...Result`` class a printed Python result names, or ``None``.

    Port of ``R/stat-tables.R::.ipynb_result_class()``.
    """
    m = compile_r("\\b([A-Za-z_][A-Za-z0-9_]*Result)\\s*\\(", perl=True).search(line)
    if m is None:
        return None
    return str(sub("\\s*\\($", "", m.group(0)))


def _ipynb_strip_numpy_scalars(line: str) -> str:
    """Unwrap numpy scalar reprs (``np.float64(5.98)`` -> ``5.98``).

    Port of ``R/stat-tables.R::.ipynb_strip_numpy_scalars()``.
    """
    return str(
        gsub("\\bnp\\.(?:float|int|uint)(?:8|16|32|64)?\\(([^()]*)\\)", "\\1", line, perl=True)
    )


def _ipynb_stat_line(lines: Sequence[str] | str) -> list[dict[str, Any]] | None:
    """Parse ``name [(df)] op value`` fragments line by line.

    Port of ``R/stat-tables.R::.ipynb_stat_line()``; each result carries
    the printed Python result class as ``call_fn`` (``""`` when absent, as
    metacheck intends; its ``%||% ""`` never replaces the ``NA`` that
    ``.ipynb_result_class()`` returns, UPSTREAM_ISSUES U139). ``None`` when
    no line parses.
    """
    if isinstance(lines, str):
        lines = [lines]
    out: list[dict[str, Any]] = []
    for ln in lines:
        cls = _ipynb_result_class(ln) or ""
        stripped = _ipynb_strip_numpy_scalars(ln)
        parsed = _r_output_oneline([stripped], source_label=None)
        for p in parsed:
            p["call_fn"] = cls
        out.extend(parsed)
    return out or None


def _ipynb_stat_table(lines: Sequence[str]) -> list[dict[str, Any]] | None:
    """Parse a statsmodels ``.summary()`` block (coefficients + fit statistics).

    Port of ``R/stat-tables.R::.ipynb_stat_table()``.
    """
    lines = list(lines)
    tabs = _r_output_tables(lines)

    def is_coef_table(tb: dict[str, Any]) -> bool:
        hdr = [(_trimws(str(c)) or "").lower() for c in tb["data"].columns]
        return "coef" in hdr and any(h in hdr for h in ("std err", "t", "p>|t|", "p>|z|"))

    tabs = [t for t in tabs if is_coef_table(t)]
    kv = _ipynb_stat_kv(lines)
    if kv is not None:
        tabs.append(kv)
    return tabs or None


_KV_PAT = "([A-Za-z][A-Za-z0-9 .()/_-]*:)\\s*(.*?)(?=\\s{2,}[A-Za-z][A-Za-z0-9 .()/_-]*:|$)"
_KV_METADATA = (
    "Dep. Variable",
    "Model",
    "Method",
    "Date",
    "Time",
    "Covariance Type",
    "No. Iterations",
)


def _ipynb_stat_kv(lines: Sequence[str]) -> dict[str, Any] | None:
    """Extract statsmodels' two-column ``Label: value`` summary header.

    Port of ``R/stat-tables.R::.ipynb_stat_kv()``; ``None`` unless both
    ``Model:`` and ``Method:`` are present.
    """
    rx = compile_r(_KV_PAT, perl=True)
    pairs: dict[str, str] = {}
    for ln in lines:
        if ln is None:
            continue
        for mm in regextract_all(_KV_PAT, ln, perl=True):
            m = rx.search(mm)
            if m is None:
                continue
            key = _trimws(sub(":$", "", m.group(1))) or ""
            val = _trimws(m.group(2) or "") or ""
            if not key or not val:
                continue
            pairs[key] = val
    if "Model" not in pairs or "Method" not in pairs:
        return None
    for k in _KV_METADATA:
        pairs.pop(k, None)
    if not pairs:
        return None
    df = _chr_frame(list(pairs), [[v] for v in pairs.values()])
    return {"analysis": None, "title": "Model fit statistics", "data": df}


def _split_lines(text: str) -> list[str]:
    """``unlist(strsplit(text, "\\n"))``."""
    return [s for s in strsplit(text, "\n") if s is not None]


def _ipynb_text(x: Any) -> str:
    """``paste(unlist(x), collapse = "")`` of a notebook text field.

    ``unlist()`` coerces every leaf to the highest type present (logical <
    integer < double < character), so ``[1, true, 2.5]`` pastes as
    ``"112.5"``; ``NULL`` leaves are dropped.
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    leaves: list[Any] = []

    def walk(v: Any) -> None:
        if isinstance(v, list | dict):
            for e in _r_values(v):
                walk(e)
        elif v is not None:
            leaves.append(v)

    walk(x)

    def rank(v: Any) -> int:
        if isinstance(v, bool):
            return 0
        if isinstance(v, int):
            return 1
        if isinstance(v, float):
            return 2
        return 3

    top = max((rank(v) for v in leaves), default=0)
    parts: list[str] = []
    for v in leaves:
        if top == 3 or top == 0:
            sv = as_character(v)
        elif top == 2:
            sv = as_character(float(v))
        else:
            sv = str(int(v))
        parts.append("NA" if sv is None else sv)
    return "".join(parts)


def _ipynb_read_tables(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read the result tables saved in a Jupyter notebook's cell outputs.

    Port of ``R/stat-tables.R::.ipynb_read_tables()``.
    """
    try:
        nb = _read_json(path)
    except (OSError, ValueError, UnicodeDecodeError):
        nb = None
    cells = _r_dollar(nb, "cells")
    if not _r_len(cells):
        return []

    out: list[dict[str, Any]] = []
    ti = 0
    for ci, cl in enumerate(_r_iter(cells), start=1):
        if _r_dollar(cl, "cell_type") != "code":
            continue
        outs = _r_dollar(cl, "outputs")
        if not _r_len(outs):
            continue
        ec = _r_dollar(cl, "execution_count")
        ec1 = ec[0] if isinstance(ec, list) and ec else (None if isinstance(ec, list) else ec)
        if isinstance(ec, dict):
            vals = list(ec.values())
            ec1 = vals[0] if vals else None
        analysis = f"Cell {ci} [{as_character(ec1)}]" if ec1 is not None else f"Cell {ci}"

        def add_text_block(lines: list[str], title: str, analysis: str = analysis) -> None:
            nonlocal ti
            parsed = _ipynb_stat_line(lines)
            if parsed is not None:
                for p in parsed:
                    ti += 1
                    out.append(
                        {
                            "analysis": analysis,
                            "title": title,
                            "data": p["data"],
                            "table_index": ti,
                            "call_fn": p["call_fn"],
                        }
                    )
                return
            tabs = _ipynb_stat_table(lines)
            if tabs is not None:
                for tb in tabs:
                    ti += 1
                    out.append(
                        {
                            "analysis": analysis,
                            "title": title,
                            "data": tb["data"],
                            "table_index": ti,
                        }
                    )
                return
            ti += 1
            out.append(
                {
                    "analysis": analysis,
                    "title": title,
                    "data": _chr_frame(["text"], [lines]),
                    "table_index": ti,
                }
            )

        for o in _r_iter(outs):
            otype = _r_dollar(o, "output_type")
            otype = "" if otype is None else otype

            if otype == "stream":
                lines = _split_lines(_ipynb_text(_r_dollar(o, "text")))
                lines = [s for s in lines if _trimws(s)]
                if not lines or _ipynb_is_noise(lines):
                    continue
                name = _r_dollar(o, "name")
                name_s = "stdout" if name is None else _chr1(name, "stdout")
                add_text_block(lines, f"{name_s} (cell {ci})")
                continue

            if otype not in ("execute_result", "display_data"):
                continue
            d = _r_dollar(o, "data")
            if not _r_len(d):
                continue

            html = _r_list_get(d, "text/html")
            if html is not None and _r_len(html):
                doc = _read_html_string(_ipynb_text(html))
                tbs = doc.xpath("//table") if doc is not None else []
                if tbs:
                    for tb in tbs:
                        try:
                            parsed_tb = _stat_table_parse(tb)
                        except Exception:
                            parsed_tb = None
                        if parsed_tb is None:
                            continue
                        ti += 1
                        out.append(
                            {
                                "analysis": analysis,
                                "title": parsed_tb["title"],
                                "data": parsed_tb["data"],
                                "table_index": ti,
                            }
                        )
                    continue

            plain = _r_list_get(d, "text/plain")
            if plain is None or not _r_len(plain):
                continue
            lines = _split_lines(_ipynb_text(plain))
            lines = [s for s in lines if _trimws(s)]
            if not lines or _ipynb_is_noise(lines):
                continue
            add_text_block(lines, f"Output (cell {ci})")

    return out


# Unused-import guard for helpers re-exported to sibling modules.
_ = regexec
