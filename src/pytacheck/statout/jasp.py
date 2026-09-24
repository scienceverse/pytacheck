"""Read JASP (``.jasp``) data archives.

Port of ``R/jasp.R``. A ``.jasp`` file is a ZIP archive bundling a dataset,
its variable metadata, the analyses that were run and JASP's own rendered
output (``index.html``). Two on-disk formats exist and both are handled: the
legacy BINARY format (``metadata.json`` + ``xdata.json`` + a column-major
``data.bin``) and the modern SQLITE format (one ``internal.sqlite`` entry).

Haven-style labels: R attaches ``labels``/``label`` attributes to each column.
Here they follow the convention of :mod:`pytacheck.datacheck` (which reads a
``.jasp``/``.omv`` as a data file): ``df.attrs["col_attrs"][column]`` holds

* ``"labels"``: R's named vector ``c(label = code)`` as ``{label: code}``
  (codes are floats, in R's order; for a repeated label the first entry wins);
* ``"label"``: the variable label. As in R, where ``attr(x, "label")``
  partially matches ``"labels"``, a value-labelled column without a variable
  label of its own gets its value labels here too (an upstream quirk).

Columns with neither attribute have no entry.

This module also provides :func:`export_jasp_html`, which re-exports the
archive's own ``index.html`` with every referenced plot inlined.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck._r import grepl, regextract_all, sub
from pytacheck.statout.spv import _as_numeric, _raw_to_char, _unzip, _write_lines

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["export_jasp_html", "import_jasp"]

_JASP_INT_MIN = -2147483648  # JASP's integer missing-value sentinel


def _dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on a parsed JSON value (exact, then unique partial match)."""
    if isinstance(x, dict):
        if name in x:
            return x[name]
        partial = [k for k in x if isinstance(k, str) and k.startswith(name)]
        return x[partial[0]] if len(partial) == 1 else None
    if x is None or isinstance(x, list):
        return None
    raise TypeError("$ operator is invalid for atomic vectors")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"lexical error: invalid char in json text ({name})")


def _first_key_wins(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        out.setdefault(k, v)
    return out


def _read_json(path: str) -> Any:
    """``jsonlite::fromJSON(path, simplifyVector = FALSE)``.

    Like jsonlite: a UTF-8 byte-order mark is accepted, ``NaN``/``Infinity``
    tokens are errors, and with duplicate keys ``x$key`` sees the first one.
    """
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh, parse_constant=_reject_constant, object_pairs_hook=_first_key_wins)


def _r_chr(x: Any) -> str | None:
    """R ``as.character()`` of one parsed-JSON scalar."""
    from pytacheck._r import as_character

    if x is None:
        return None
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, list | dict):
        if len(x) != 1:
            raise ValueError("values must be length 1")
        return _r_chr(next(iter(x.values())) if isinstance(x, dict) else x[0])
    return as_character(x)


def _labels_from_list(lst: Any) -> dict[str | None, float]:
    """Shared body of ``.jasp_binary_labels()`` / ``.omv_labels()``.

    R's ``setNames(codes[keep], labs[keep])`` as ``{label: code}``.
    """
    if not lst:
        return {}
    codes = []
    labs = []
    for entry in lst:
        if isinstance(entry, dict):
            entry = list(entry.values())
        if not isinstance(entry, list) or len(entry) < 2:
            raise IndexError("subscript out of bounds")
        if entry[0] is None or entry[1] is None:
            # as.character(NULL) is character(0): vapply(..., character(1)) fails
            raise ValueError("values must be length 1")
        codes.append(_as_numeric(_r_chr(entry[0])))
        labs.append(_r_chr(entry[1]))
    out: dict[str | None, float] = {}
    for code, lab in zip(codes, labs, strict=True):
        if code is None or math.isnan(code) or lab == "":
            continue
        out.setdefault(lab, code)
    return out


def _jasp_binary_labels(field: Any, xdat: Any) -> dict[str | None, float]:
    """Port of R/jasp.R::.jasp_binary_labels().

    Value labels of one binary-format field (its own ``labels``, else
    ``xdata.json[name]$labels``) as ``{label: code}``.
    """
    lst = _dollar(field, "labels")
    if not lst:
        name = _dollar(field, "name")
        x = xdat.get(name) if isinstance(xdat, dict) and isinstance(name, str) else None
        lst = _dollar(x, "labels") if x is not None else None
    return _labels_from_list(lst)


def _field_list(fields: Any) -> list[Any]:
    """``for (j in seq_along(fields)) fields[[j]]`` over parsed JSON (an object by value)."""
    if fields is None:
        return []
    if isinstance(fields, dict):
        return list(fields.values())
    return fields if isinstance(fields, list) else [fields]


def _check_field_names(names: list[Any]) -> None:
    """``vapply(fields, function(f) f$name, character(1))`` stops on a missing name."""
    for nm in names:
        if nm is None or (isinstance(nm, list | dict) and len(nm) != 1):
            raise ValueError("values must be length 1")
        if not isinstance(nm, str):
            raise ValueError("values must be type 'character'")


def _column_attrs(labels: dict[Any, float] | None, label: Any) -> dict[str, Any]:
    """The ``labels``/``label`` attributes R ends up attaching to one column.

    R re-attaches ``attr(cols[[j]], "label")``, and ``attr()`` partially
    matches ``"labels"``: a value-labelled column without its own variable
    label therefore gets ``label`` = its value-label vector (upstream quirk).
    """
    out: dict[str, Any] = {}
    if labels:
        out["labels"] = labels
    if label is not None:
        out["label"] = label
    elif labels:
        out["label"] = dict(labels)
    return out


def _frame_from_columns(
    cols: list[Any], names: list[Any], col_attrs: list[dict[str, Any]] | None = None
) -> pd.DataFrame:
    """``setNames(as.data.frame(cols), names)`` and the re-attached attributes.

    Columns of unequal length follow ``data.frame()``: a shorter column is
    recycled only when its length divides the longest one and it carries no
    attributes (``is.vector()``); otherwise R stops with "arguments imply
    differing number of rows". Attributes go to ``df.attrs["col_attrs"]``.
    """
    import numpy as np
    import pandas as pd

    attrs = col_attrs if col_attrs is not None else [{} for _ in cols]
    lengths = [len(c) for c in cols]
    if lengths and len(set(lengths)) > 1:
        n = max(lengths)
        if any(k < n and (k == 0 or n % k or a) for k, a in zip(lengths, attrs, strict=True)):
            raise ValueError(
                "arguments imply differing number of rows: "
                + ", ".join(str(k) for k in dict.fromkeys(lengths))
            )
        cols = [
            c if len(c) == n else pd.array(np.resize(np.asarray(c, dtype=object), n), dtype=c.dtype)
            for c in cols
        ]
    df = pd.DataFrame(dict(enumerate(cols))) if cols else pd.DataFrame()
    df.columns = list(names)
    kept: dict[Any, dict[str, Any]] = {}
    for name, a in zip(names, attrs, strict=True):
        if a:
            kept.setdefault(name, a)
    if kept:
        df.attrs["col_attrs"] = kept
    return df


def _read_int32s(fh: Any, n: int) -> list[int | None]:
    b = fh.read(4 * n) if n > 0 else b""
    k = len(b) // 4
    vals = struct.unpack(f"<{k}i", b[: 4 * k])
    return [None if v == _JASP_INT_MIN else v for v in vals]


def _read_doubles(fh: Any, n: int) -> list[float]:
    b = fh.read(8 * n) if n > 0 else b""
    k = len(b) // 8
    return list(struct.unpack(f"<{k}d", b[: 8 * k]))


def import_jasp(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a JASP (.jasp) file.

    Port of R/jasp.R::import_jasp(). Extracts the dataset, its variable
    metadata (measurement level, value labels) and the stored analyses, from
    either the legacy binary format or the modern embedded-SQLite format.

    Args:
        path: path to a ``.jasp`` file.

    Returns:
        A dict with ``data`` (a data frame; variable and value labels in
        ``data.attrs["col_attrs"][column]``, see the module docstring),
        ``columns`` (a data frame of
        ``name`` and ``type``), ``format`` (``"binary"`` or ``"sqlite"``),
        ``data_file_path`` (the original source path recorded in the archive,
        or ``None``) and, when the archive has an ``analyses.json``,
        ``analyses`` (that JSON, parsed).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if it is not a readable ``.jasp`` archive.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.jasp$", path, ignore_case=True):
        raise ValueError(f"Not a .jasp file: {path}")
    with tempfile.TemporaryDirectory(prefix="jasp_") as tmp:
        files = _unzip(path, tmp)
        if not files:
            raise ValueError(f"Could not open '{os.path.basename(path)}' as a .jasp (zip) archive.")
        base = [os.path.basename(f) for f in files]

        analyses = None
        aj = [f for f, b in zip(files, base, strict=True) if b == "analyses.json"]
        if aj:
            try:
                analyses = _read_json(aj[0])
            except Exception:
                analyses = None

        if "internal.sqlite" in base:
            out = _read_jasp_sqlite(files[base.index("internal.sqlite")])
        elif "data.bin" in base:
            out = _read_jasp_binary(files)
        else:
            raise ValueError(
                "Unrecognised .jasp: no 'data.bin' or 'internal.sqlite' entry in "
                f"{os.path.basename(path)}."
            )
    if analyses is not None:
        out["analyses"] = analyses
    return out


def _read_jasp_binary(files: list[str]) -> dict[str, Any]:
    """Port of R/jasp.R::.read_jasp_binary() (JASP <= 0.16)."""
    import numpy as np
    import pandas as pd

    base = [os.path.basename(f) for f in files]
    if "metadata.json" not in base:
        raise IndexError("subscript out of bounds")
    meta = _read_json(files[base.index("metadata.json")])
    xdat = _read_json(files[base.index("xdata.json")]) if "xdata.json" in base else {}
    ds = _dollar(meta, "dataSet")
    fields = _field_list(_dollar(ds, "fields"))
    nrow = _dollar(ds, "rowCount")
    if nrow is None:
        raise ValueError("invalid 'n' argument")

    cols: list[Any] = []
    names: list[str] = []
    types: list[Any] = []
    col_attrs: list[dict[str, Any]] = []
    with open(files[base.index("data.bin")], "rb") as fh:
        for f in fields:
            mt = _dollar(f, "measureType")
            if mt is None:
                mt = "Nominal"
            name = _dollar(f, "name")
            labs = None
            if mt == "Continuous":
                vals = _read_doubles(fh, int(nrow))
                cols.append(np.asarray(vals, dtype="float64"))
            else:
                cols.append(pd.array(_read_int32s(fh, int(nrow)), dtype="Int64"))
                labs = _jasp_binary_labels(f, xdat)
            title = _dollar(f, "title")
            label = None
            if title is not None and title != "" and title != name:
                label = _r_chr(title)
            col_attrs.append(_column_attrs(labs, label))
            names.append(name)
            types.append(mt)
    _check_field_names(names)
    df = _frame_from_columns(cols, names, col_attrs)
    columns = (
        pd.DataFrame(
            {
                "name": pd.array(names, dtype="string"),
                "type": pd.array(types, dtype="string"),
            }
        )
        if fields
        else None
    )
    return {
        "data": df,
        "columns": columns,
        "format": "binary",
        "data_file_path": _dollar(meta, "dataFilePath"),
    }


def _sqlite_affinity(decl: str | None) -> str:
    """SQLite's column affinity rules for a declared type."""
    d = (decl or "").upper()
    if "INT" in d:
        return "integer"
    if any(k in d for k in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if d == "":
        return "none"
    if "BLOB" in d:
        return "blob"
    if any(k in d for k in ("REAL", "FLOA", "DOUB")):
        return "real"
    return "numeric"


_SQL_INT_PREFIX = re.compile(r"[ \t\n\r\f\v]*([+-]?)0*([0-9]*)")
_SQL_REAL_PREFIX = re.compile(
    r"[ \t\n\r\f\v]*([+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
)


def _sqlite_as_int(v: Any) -> int:
    """``sqlite3_column_int64()`` of a stored value (text: its leading integer)."""
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    text = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    m = _SQL_INT_PREFIX.match(text)
    value = int(m.group(2)) if m and m.group(2) else 0
    value = -value if m and m.group(1) == "-" else value
    return max(-(2**63), min(2**63 - 1, value))


def _sqlite_as_real(v: Any) -> float:
    """``sqlite3_column_double()`` of a stored value (text: its leading number)."""
    if isinstance(v, int | float):
        return float(v)
    text = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    m = _SQL_REAL_PREFIX.match(text)
    try:
        return float(m.group(1)) if m else 0.0
    except OverflowError:  # pragma: no cover - float() saturates to inf
        return math.inf


def _sqlite_as_text(v: Any) -> str:
    """``sqlite3_column_text()`` of a stored value (reals as SQLite's ``%!.15g``)."""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, int):
        return str(v)
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    txt = f"{v:.15g}"
    mant, sep, exp = txt.partition("e")
    if "." not in mant:
        mant += ".0"
    return mant + sep + exp


def _rsqlite_column(values: list[Any], decl: str | None) -> Any:
    """How RSQLite types one query result column.

    The column takes the storage class of its first non-NULL value (the
    declared type only matters when every value is NULL); an integer column is
    upgraded to double when a real value follows, and any other mismatched
    value is coerced the way SQLite's ``sqlite3_column_*()`` accessors coerce
    it (text to its leading number, numbers to text). An integer outside R's
    integer range makes the column a double (RSQLite's ``integer64``), and
    ``-2^31`` is R's ``NA_integer_``.
    """
    import pandas as pd

    kind = None
    out: list[Any] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        if kind is None:
            kind = {int: "int", float: "real", str: "text"}.get(type(v), "blob")
            if kind == "int" and not -(2**31) <= v <= 2**31 - 1:
                kind = "real"
        elif kind == "int" and isinstance(v, float):
            kind = "real"
            out = [None if o is None else float(o) for o in out]
        if kind == "int":
            iv = _sqlite_as_int(v)
            if not -(2**31) <= iv <= 2**31 - 1:
                kind = "real"
                out = [None if o is None else float(o) for o in out]
                out.append(float(iv))
            else:
                out.append(iv)
        elif kind == "real":
            out.append(_sqlite_as_real(v))
        elif kind == "text":
            out.append(_sqlite_as_text(v))
        else:
            out.append(v if isinstance(v, bytes) else _sqlite_as_text(v).encode("utf-8"))
    if kind is None:
        kind = {
            "integer": "int",
            "real": "real",
            "numeric": "real",
            "text": "text",
            "blob": "blob",
        }.get(_sqlite_affinity(decl), "lgl")
    if kind == "int":
        return pd.array([None if o == -(2**31) else o for o in out], dtype="Int64")
    if kind == "real":
        return pd.array(out, dtype="Float64").astype("float64")
    if kind == "text":
        return pd.array(out, dtype="string")
    if kind == "lgl":
        return pd.array(out, dtype="boolean")
    return pd.array(out, dtype=object)


def _read_jasp_sqlite(sqlite_path: str) -> dict[str, Any]:
    """Port of R/jasp.R::.read_jasp_sqlite() (JASP >= 0.17)."""
    import pandas as pd

    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        cmeta = con.execute(
            "SELECT id, name, columnType, colIdx FROM Columns ORDER BY colIdx"
        ).fetchall()
        tabs = [
            r[0]
            for r in con.execute(
                "SELECT name FROM (SELECT * FROM sqlite_master UNION ALL "
                "SELECT * FROM sqlite_temp_master) WHERE type = 'table' OR type = 'view' "
                "ORDER BY name"
            )
        ]
        dtabs = [t for t in tabs if grepl("^DataSet_", t)]
        if not dtabs:
            raise ValueError("No DataSet_* table in internal.sqlite.")
        dtab = dtabs[0]
        info = con.execute(f'PRAGMA table_info("{dtab}")').fetchall()
        phys = [r[1] for r in info]
        decl = {r[1]: r[2] for r in info}

        def labels_for(cid: Any) -> dict[str | None, float]:
            rows = con.execute(
                f"SELECT value, label FROM Labels WHERE columnId = {int(cid)} ORDER BY ordering"  # noqa: S608
            ).fetchall()
            out: dict[str | None, float] = {}
            for value, lab in rows:
                # R: keep <- !is.na(value) & nzchar(label); nzchar(NA) is TRUE, and
                # as.numeric() of a non-numeric value is a kept NA code
                if value is None or lab == "":
                    continue
                code = _as_numeric(value)
                out.setdefault(
                    None if lab is None else _r_chr(lab), math.nan if code is None else code
                )
            return out

        cols: list[Any] = []
        names: list[Any] = []
        col_attrs: list[dict[str, Any]] = []
        for cid, name, ctype, _ in cmeta:
            if ctype is None:
                # is_scale <- tolower(NA) == "scale" is NA; `if (!is_scale)` stops
                raise ValueError("missing value where TRUE/FALSE needed")
            is_scale = _r_chr(ctype).lower() == "scale"  # type: ignore[union-attr]
            dblc = f"Column_{int(cid)}_DBL"
            intc = f"Column_{int(cid)}_INT"
            phys_col = dblc if is_scale and dblc in phys else intc if intc in phys else dblc
            query = f'SELECT "{phys_col}" AS v FROM "{dtab}" ORDER BY rowNumber'  # noqa: S608
            raw = [r[0] for r in con.execute(query)]
            v = _rsqlite_column(raw, decl.get(phys_col))
            labs = None
            if not is_scale:
                if v.dtype == object:  # an RSQLite blob column is a list
                    raise TypeError("(list) object cannot be coerced to type 'double'")
                nums = [None if pd.isna(x) else _as_numeric(x) for x in v]
                v = pd.array(
                    [None if x is None or x == _JASP_INT_MIN else x for x in nums], dtype="Float64"
                ).astype("float64")
                labs = labels_for(cid)
            label = None
            if name is not None:
                try:
                    query = f"SELECT title FROM Columns WHERE id = {int(cid)}"  # noqa: S608
                    ttl = [r[0] for r in con.execute(query)]
                except sqlite3.Error:
                    ttl = []
                if ttl and ttl[0] is not None and ttl[0] != "" and ttl[0] != name:
                    label = _r_chr(ttl[0])
            cols.append(v)
            names.append(name)
            col_attrs.append(_column_attrs(labs, label))
        df = _frame_from_columns(cols, names, col_attrs)
        try:
            dfp_rows = con.execute("SELECT dataFilePath FROM DataSets LIMIT 1").fetchall()
            dfp = dfp_rows[0][0] if dfp_rows else None
        except sqlite3.Error:
            dfp = None
    finally:
        con.close()
    return {
        "data": df,
        "columns": pd.DataFrame(
            {
                "name": pd.array([c[1] for c in cmeta], dtype="string"),
                "type": pd.array([c[2] for c in cmeta], dtype="string"),
            }
        ),
        "format": "sqlite",
        "data_file_path": dfp,
    }


def _jasp_analyses_summary(analyses: Any) -> list[str]:
    """Port of R/jasp.R::.jasp_analyses_summary(): one line per stored analysis.

    ``analyses`` is the parsed ``analyses.json`` (the whole document or its
    ``analyses`` list).
    """
    if analyses is None:
        return []
    inner = _dollar(analyses, "analyses")
    al = inner if inner is not None else analyses
    if isinstance(al, dict):
        al = list(al.values())
    if not isinstance(al, list) or not al:
        return []
    out = []
    for i, a in enumerate(al, 1):
        if not isinstance(a, dict | list):
            if a is None:
                raise ValueError("values must be length 1")
            out.append(f"{i}. {_r_chr(a)}")
            continue
        title = _dollar(a, "title")
        if title is None:
            title = _dollar(a, "name")
        if title is None:
            title = "analysis"
        module = _dollar(a, "module")
        mod = f"  [module: {_r_chr(module)}]" if module is not None else ""
        out.append(f"{i}. {_r_chr(title)}{mod}")
    return out


def _read_html_file(path: str) -> str:
    """``readChar(path, size, useBytes = TRUE)`` (truncated at an embedded NUL)."""
    raw = Path(path).read_bytes()
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("utf-8", errors="surrogateescape")


def _export_archive_html(path: str, out: str | None, ext: str, label: str, prefix: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(rf"\.{ext}$", path, ignore_case=True):
        raise ValueError(f"Not a .{label} file: {path}")
    if out is None:
        out = str(sub(rf"\.{ext}$", ".html", path, ignore_case=True))
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        files = _unzip(path, tmp)
        if not files:
            raise ValueError(
                f"Could not open '{os.path.basename(path)}' as a .{label} (zip) archive."
            )
        base = [os.path.basename(f) for f in files]
        if "index.html" not in base:
            raise ValueError(f"No 'index.html' in {os.path.basename(path)}; nothing to export.")
        html = _read_html_file(files[base.index("index.html")])
        _write_lines(_html_inline_images(html, tmp), out)
    return out


def export_jasp_html(
    path: str | os.PathLike[str], out: str | os.PathLike[str] | None = None
) -> str:
    """Export a JASP (.jasp) file's own rendered output as standalone HTML.

    Port of R/jasp.R::export_jasp_html(). Extracts the archive's
    ``index.html`` and inlines every plot it references as a base64 ``data:``
    URI.

    Args:
        path: path to a ``.jasp`` file.
        out: path to write the HTML file to; defaults to ``path`` with its
            extension replaced by ``.html``.

    Returns:
        The path written to.
    """
    return _export_archive_html(
        os.fspath(path), None if out is None else os.fspath(out), "jasp", "jasp", "jaspexport_"
    )


def _url_decode(url: str) -> str:
    """R ``utils::URLdecode()`` (``%XX`` escapes only; ``+`` is kept).

    As in R, an escape whose two characters are missing or do not form a byte
    decodes to a NUL byte (R's ``as.raw()`` of an out-of-range value); trailing
    NULs are dropped by ``rawToChar()`` and an embedded one is an error.
    """
    b = url.encode("utf-8", errors="surrogateescape")
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] != 0x25:
            out.append(b[i])
            i += 1
            continue
        y = [b[i + k] if i + k < len(b) else None for k in (1, 2)]
        val = None
        if None not in y:
            y = [c - 32 if c > 96 else c for c in y]  # type: ignore[operator]
            y = [c - 7 if c > 57 else c for c in y]  # type: ignore[operator]
            val = (y[0] - 48) * 16 + (y[1] - 48)  # type: ignore[operator]
        out.append(val if val is not None and 0 <= val <= 255 else 0)
        i += 3
    return _raw_to_char(bytes(out), errors="surrogateescape")


def _file_ext(x: str) -> str:
    """R ``tools::file_ext()``."""
    m = regextract_all(r"\.([[:alnum:]]+)$", x)
    return m[0][1:] if m else ""


def _html_inline_images(html: str, root: str) -> str:
    """Port of R/jasp.R::.html_inline_images() (also R/omv.R).

    Inlines each distinct ``src="....png|jpg|jpeg|gif"`` that resolves to a file
    under ``root`` as a base64 ``data:`` URI. As in R, only the first
    occurrence of each distinct ``src`` is replaced.
    """
    srcs = regextract_all(r'src="([^"]+\.(png|jpe?g|gif))"', html, ignore_case=True)
    for src in dict.fromkeys(srcs):
        rel = str(sub(r'^src="(.*)"$', r"\1", src, ignore_case=True))
        if grepl(r"^(https?:)?//|^data:", rel, ignore_case=True):
            continue
        img_path = root + "/" + _url_decode(rel)  # file.path(): no special case for "/..."
        if not os.path.exists(img_path):
            continue
        ext = _file_ext(img_path).lower()
        mime = "image/png" if ext == "png" else "image/gif" if ext == "gif" else "image/jpeg"
        data_uri = f"data:{mime};base64," + base64.b64encode(Path(img_path).read_bytes()).decode(
            "ascii"
        )
        html = html.replace(src, f'src="{data_uri}"', 1)
    return html
