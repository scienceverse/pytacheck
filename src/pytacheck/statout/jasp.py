"""Read JASP (``.jasp``) data archives.

Port of ``R/jasp.R``. A ``.jasp`` file is a ZIP archive bundling a dataset,
its variable metadata, the analyses that were run and JASP's own rendered
output (``index.html``). Two on-disk formats exist and both are handled: the
legacy BINARY format (``metadata.json`` + ``xdata.json`` + a column-major
``data.bin``) and the modern SQLITE format (one ``internal.sqlite`` entry).

Haven-style labels: R attaches ``label``/``labels`` attributes to each column.
Here they live in the returned data frame's ``attrs``:

* ``df.attrs["label"]``: ``{column: variable label}``;
* ``df.attrs["labels"]``: ``{column: {code: value label}}`` (R's named vector
  ``c(label = code)``, keyed by code as in pyreadstat's
  ``variable_value_labels``; codes are floats, in R's order).

This module also provides :func:`export_jasp_html`, which re-exports the
archive's own ``index.html`` with every referenced plot inlined.
"""

from __future__ import annotations

import base64
import json
import math
import os
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck._r import grepl, regextract_all, sub
from pytacheck.statout.spv import _as_numeric, _unzip, _write_lines

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


def _read_json(path: str) -> Any:
    """``jsonlite::fromJSON(path, simplifyVector = FALSE)``."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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


def _labels_from_list(lst: Any) -> dict[float, str]:
    """Shared body of ``.jasp_binary_labels()`` / ``.omv_labels()``."""
    if not lst:
        return {}
    codes = []
    labs = []
    for entry in lst:
        if isinstance(entry, dict):
            entry = list(entry.values())
        if not isinstance(entry, list) or len(entry) < 2:
            raise IndexError("subscript out of bounds")
        codes.append(_as_numeric(_r_chr(entry[0])))
        labs.append(_r_chr(entry[1]))
    out: dict[float, str] = {}
    for code, lab in zip(codes, labs, strict=True):
        if code is None or math.isnan(code) or lab is None or lab == "":
            continue
        out.setdefault(code, lab)
    return out


def _jasp_binary_labels(field: Any, xdat: Any) -> dict[float, str]:
    """Port of R/jasp.R::.jasp_binary_labels().

    Value labels of one binary-format field (its own ``labels``, else
    ``xdata.json[name]$labels``) as ``{code: label}``.
    """
    lst = _dollar(field, "labels")
    if not lst:
        name = _dollar(field, "name")
        x = xdat.get(name) if isinstance(xdat, dict) and isinstance(name, str) else None
        lst = _dollar(x, "labels") if x is not None else None
    return _labels_from_list(lst)


def _frame_from_columns(cols: list[Any], names: list[str]) -> pd.DataFrame:
    """``as.data.frame(cols)`` + ``setNames()``: positional columns, R recycling."""
    import numpy as np
    import pandas as pd

    lengths = [len(c) for c in cols]
    if lengths and len(set(lengths)) > 1:
        n = max(lengths)
        if any(k == 0 or n % k for k in lengths):
            raise ValueError(
                "arguments imply differing number of rows: "
                + ", ".join(str(k) for k in dict.fromkeys(lengths))
            )
        cols = [pd.array(np.resize(np.asarray(c, dtype=object), n), dtype=c.dtype) for c in cols]
    df = pd.DataFrame({i: c for i, c in enumerate(cols)}) if cols else pd.DataFrame()
    df.columns = list(names)
    return df


def _attach_labels(df: pd.DataFrame, labels: dict[str, dict[float, str]], label: dict[str, str]) -> None:
    df.attrs["label"] = label
    df.attrs["labels"] = labels


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
        ``data.attrs["label"]``/``["labels"]``), ``columns`` (a data frame of
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
    fields = _dollar(ds, "fields") or []
    nrow = _dollar(ds, "rowCount")
    if nrow is None:
        raise ValueError("invalid 'n' argument")

    cols: list[Any] = []
    names: list[str] = []
    types: list[Any] = []
    labels: dict[str, dict[float, str]] = {}
    label: dict[str, str] = {}
    with open(files[base.index("data.bin")], "rb") as fh:
        for f in fields:
            mt = _dollar(f, "measureType")
            if mt is None:
                mt = "Nominal"
            name = _dollar(f, "name")
            if mt == "Continuous":
                vals = _read_doubles(fh, int(nrow))
                cols.append(np.asarray(vals, dtype="float64"))
            else:
                cols.append(pd.array(_read_int32s(fh, int(nrow)), dtype="Int64"))
                labs = _jasp_binary_labels(f, xdat)
                if labs:
                    labels[name] = labs
            title = _dollar(f, "title")
            if title is not None and title != "" and title != name:
                label[name] = _r_chr(title)  # type: ignore[assignment]
            names.append(name)
            types.append(mt)
    df = _frame_from_columns(cols, names)
    _attach_labels(df, labels, label)
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
    if d == "" or "BLOB" in d:
        return "blob"
    if any(k in d for k in ("REAL", "FLOA", "DOUB")):
        return "real"
    return "numeric"


def _rsqlite_column(values: list[Any], decl: str | None) -> Any:
    """How RSQLite types a query result column (declared type, else first value)."""
    import pandas as pd

    aff = _sqlite_affinity(decl)
    first = next((v for v in values if v is not None), None)
    if aff == "numeric" or aff == "blob":
        aff = (
            "integer"
            if isinstance(first, int)
            else "real"
            if isinstance(first, float)
            else "text"
            if isinstance(first, str)
            else "integer"
            if first is None and aff == "numeric"
            else "blob"
        )
    if aff == "integer":
        if all(v is None or (isinstance(v, int) and abs(v) <= 2147483647) for v in values):
            return pd.array(values, dtype="Int64")
        return pd.array(
            [None if v is None else float(v) if not isinstance(v, str) else _as_numeric(v) for v in values],
            dtype="Float64",
        ).astype("float64")
    if aff == "real":
        return pd.array(
            [None if v is None else _as_numeric(v) if isinstance(v, str) else float(v) for v in values],
            dtype="Float64",
        ).astype("float64")
    if aff == "text":
        return pd.array([None if v is None else str(v) for v in values], dtype="string")
    return pd.array(values, dtype=object)


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

        def labels_for(cid: Any) -> dict[float, str]:
            rows = con.execute(
                f"SELECT value, label FROM Labels WHERE columnId = {int(cid)} ORDER BY ordering"
            ).fetchall()
            out: dict[float, str] = {}
            for value, lab in rows:
                # R: keep <- !is.na(value) & nzchar(label); nzchar(NA) is TRUE
                if value is None or lab == "":
                    continue
                code = _as_numeric(value)
                if code is None:
                    continue
                out.setdefault(code, None if lab is None else str(lab))  # type: ignore[arg-type]
            return out

        cols: list[Any] = []
        names: list[Any] = []
        labels: dict[str, dict[float, str]] = {}
        label: dict[str, str] = {}
        for cid, name, ctype, _ in cmeta:
            is_scale = str(ctype).lower() == "scale"
            dblc = f"Column_{int(cid)}_DBL"
            intc = f"Column_{int(cid)}_INT"
            phys_col = dblc if is_scale and dblc in phys else intc if intc in phys else dblc
            raw = [r[0] for r in con.execute(f'SELECT "{phys_col}" AS v FROM "{dtab}" ORDER BY rowNumber')]
            v = _rsqlite_column(raw, decl.get(phys_col))
            if not is_scale:
                nums = [None if pd.isna(x) else _as_numeric(x) for x in v]
                v = pd.array(
                    [None if x is None or x == _JASP_INT_MIN else x for x in nums], dtype="Float64"
                ).astype("float64")
                labs = labels_for(cid)
                if labs:
                    labels[name] = labs
            if name is not None:
                try:
                    ttl = [r[0] for r in con.execute(f"SELECT title FROM Columns WHERE id = {int(cid)}")]
                except sqlite3.Error:
                    ttl = []
                if ttl and ttl[0] is not None and ttl[0] != "" and ttl[0] != name:
                    label[name] = str(ttl[0])
            cols.append(v)
            names.append(name)
        df = _frame_from_columns(cols, names)
        _attach_labels(df, labels, label)
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
    """``readChar(path, size, useBytes = TRUE)``."""
    return Path(path).read_bytes().decode("utf-8", errors="surrogateescape")


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
            raise ValueError(f"Could not open '{os.path.basename(path)}' as a .{label} (zip) archive.")
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
    """R ``utils::URLdecode()`` (``%XX`` escapes only; ``+`` is kept)."""
    b = url.encode("utf-8", errors="surrogateescape")
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] != 0x25:
            out.append(b[i])
            i += 1
            continue
        y = [c for c in b[i + 1 : i + 3]]
        y = [c - 32 if c > 96 else c for c in y]
        y = [c - 7 if c > 57 else c for c in y]
        val = sum((c - 48) * m for c, m in zip(y, (16, 1), strict=False))
        if not 0 <= val <= 255:
            raise ValueError("out of range value in URLdecode")
        out.append(val)
        i += 3
    return bytes(out).decode("utf-8", errors="surrogateescape")


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
        try:
            img_path = os.path.join(root, _url_decode(rel))
        except ValueError:
            raise
        if not os.path.exists(img_path):
            continue
        ext = _file_ext(img_path).lower()
        mime = "image/png" if ext == "png" else "image/gif" if ext == "gif" else "image/jpeg"
        data_uri = f"data:{mime};base64," + base64.b64encode(Path(img_path).read_bytes()).decode("ascii")
        html = html.replace(src, f'src="{data_uri}"', 1)
    return html
