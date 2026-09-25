"""Read SPSS Viewer (``.spv``) output files.

Port of ``R/spv.R``. A ``.spv`` file is a ZIP archive holding SPSS's output
"structure" XML documents (which analysis produced which item, the SPSS syntax
that was run) plus one binary member per table: the modern "light-binary"
table format (SPSS 21+) or the older "legacy" raw case data paired with a
detail-XML pivot description. Charts are VizML XML paired with legacy case
data. Everything is decoded the way metacheck decodes it (itself a port of GNU
PSPP's decoder), including its quirks.

The binary cursor mirrors R's value semantics: a cursor is immutable and every
``_spvbin_read_*`` returns ``(value, new_cursor)``. As in R, a ``count()``
sub-cursor does not carry the table ``version`` (R builds it as a fresh list),
which changes how version-gated fields inside it are read.

Decoded tables are "tidy" data frames (one row per cell, one column per
dimension, plus ``value``) with R's attributes in :attr:`pandas.DataFrame.attrs`:
``spv_title``, ``spv_footnotes``, ``spv_row_dims``, ``spv_col_dims``,
``spv_layer_dims`` (charts: ``spv_chart_title``, ``spv_chart_xlab``,
``spv_chart_ylab``, ``spv_chart_type`` and ``spv_chart_fits``).
"""

from __future__ import annotations

import base64
import itertools
import math
import os
import re
import struct
import sys
import tempfile
import warnings
import zipfile
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from pytacheck._r import as_character, format_num, grepl, gsub, r_sort_key, strsplit, sub, trimws

if TYPE_CHECKING:
    import pandas as pd
    from lxml import etree

__all__ = ["export_spv_html", "import_spv", "spv_assemble_table"]

_DBL_MAX = sys.float_info.max

# ===========================================================================
# Small R-semantics helpers shared by the statout readers
# ===========================================================================

_NUM_RE = re.compile(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_HEX_RE = re.compile(
    r"([-+]?)0[xX]((?=\.?[0-9a-fA-F])[0-9a-fA-F]*(?:\.[0-9a-fA-F]*)?)(?:[pP]([-+]?[0-9]+))?"
)
_INF_RE = re.compile(r"([-+]?)(?:inf|infinity)", re.IGNORECASE)


def _as_numeric(x: Any) -> float | None:
    """R ``as.numeric()`` of one value (``None`` for ``NA``)."""
    if x is None:
        return None
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, int | float):
        return float(x)
    s = str(x).strip(" \t\n\r\f\v")
    if not s or s == "NA":
        return None
    if _NUM_RE.fullmatch(s):
        return float(s)
    m = _HEX_RE.fullmatch(s)
    if m:
        v = float.fromhex("0x" + m.group(2) + ("p" + m.group(3) if m.group(3) else ""))
        return -v if m.group(1) == "-" else v
    m = _INF_RE.fullmatch(s)
    if m:
        return -math.inf if m.group(1) == "-" else math.inf
    if s.lower() == "nan":
        return math.nan
    return None


def _as_integer(x: Any) -> int | None:
    """R ``as.integer()`` of one value (truncates; ``None`` for ``NA``)."""
    v = _as_numeric(x)
    if v is None or math.isnan(v) or math.isinf(v):
        return None
    iv = int(v)
    if abs(iv) > 2147483647:
        return None
    return iv


def _make_unique(names: Sequence[str], sep: str = ".") -> list[str]:
    """R ``make.unique()``."""
    used = set(names)
    counts: dict[str, int] = {}
    first_seen: set[str] = set()
    out: list[str] = []
    for nm in names:
        if nm not in first_seen:
            first_seen.add(nm)
            out.append(nm)
            continue
        cnt = counts.get(nm, 1)
        while f"{nm}{sep}{cnt}" in used:
            cnt += 1
        new = f"{nm}{sep}{cnt}"
        used.add(new)
        counts[nm] = cnt + 1
        out.append(new)
    return out


_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


def _read_lines(path: str | os.PathLike[str], n: int = -1) -> list[str]:
    """R ``readLines(path, warn = FALSE, encoding = "UTF-8")`` (``n`` lines).

    LF, CRLF and CR all end a line, and a line is cut at an embedded NUL, as
    in R. Invalid UTF-8 is decoded with replacement characters (R keeps the
    bytes, and its regex functions then refuse the strings).
    """
    data = Path(path).read_bytes()
    text = data.decode("utf-8", errors="replace")
    lines = _LINE_SPLIT.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    lines = [ln.split("\x00", 1)[0] for ln in lines] if "\x00" in text else lines
    return lines if n < 0 else lines[:n]


def _write_lines(lines: str | Sequence[str], path: str | os.PathLike[str]) -> None:
    """R ``writeLines(x, path, useBytes = TRUE)``: every element + ``"\\n"``."""
    if isinstance(lines, str):
        lines = [lines]
    with open(path, "wb") as fh:
        for ln in lines:
            fh.write(ln.encode("utf-8", errors="surrogateescape") + b"\n")


def _file_path_sans_ext(x: str) -> str:
    """R ``tools::file_path_sans_ext()``."""
    return str(sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x))


def _r_dirname(path: str) -> str:
    """R ``dirname()`` (``"."`` for a bare file name)."""
    path = os.path.expanduser(path)  # R_ExpandFileName()
    d = os.path.dirname(path.rstrip("/")) if path not in ("/", "") else path
    return d or "."


def _file_path(*parts: str) -> str:
    """R ``file.path()``: the parts pasted with ``"/"`` (an absolute part stays inside)."""
    return "/".join(parts)


def _unzip(path: str, exdir: str) -> list[str]:
    """R ``utils::unzip(path, exdir = exdir)``: extracted file paths, in archive order.

    A file that is not a readable ZIP archive gives an empty list (R warns and
    returns ``NULL``).
    """
    try:
        with zipfile.ZipFile(path) as zf:
            out: list[str] = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                out.append(zf.extract(info, exdir))
            return out
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError):
        return []


def _list_files(dir_path: str) -> list[str]:
    """R ``list.files(dir_path, recursive = TRUE)``: relative paths, collated."""
    out: list[str] = []
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel_root = os.path.relpath(root, dir_path)
        for f in files:
            if f.startswith("."):
                continue
            out.append(f if rel_root == "." else f"{rel_root.replace(os.sep, '/')}/{f}")
    return sorted(out, key=r_sort_key)


def _format_fixed_whole(v: float) -> str:
    """R ``format(v, scientific = FALSE, trim = TRUE)`` for a whole number."""
    s = f"{v:.0f}"
    return "0" if s == "-0" else s


def _stat_num_to_chr_local(v: float) -> str | None:
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    if v == round(v) and abs(v) < 1e15:
        return _format_fixed_whole(v)
    return format_num(v, digits=15)


@cache
def _num_to_chr_fn() -> Callable[[float], Any]:
    try:  # R/stat-tables.R::.stat_num_to_chr (ported by statout_core)
        from pytacheck.statout.stat_tables import _stat_num_to_chr
    except ImportError:  # pragma: no cover - depends on a concurrently ported area
        return _stat_num_to_chr_local
    return _stat_num_to_chr  # type: ignore[no-any-return]


def _stat_num_to_chr(v: float) -> str | None:
    return _num_to_chr_fn()(v)  # type: ignore[no-any-return]


def _chr_dbl(d: float | None) -> str | None:
    """R ``as.character()`` of a double (``NaN`` stays ``"NaN"``)."""
    if d is None:
        return None
    if math.isnan(d):
        return "NaN"
    return as_character(float(d))


def _is_missing(x: Any) -> bool:
    """R ``is.na()`` for one value (``None``, ``pd.NA`` or ``NaN``)."""
    if x is None:
        return True
    if isinstance(x, float):
        return math.isnan(x)
    return type(x).__name__ == "NAType"


def _na_str(x: str | None) -> str:
    """How ``paste()`` renders one value (``NA`` becomes ``"NA"``)."""
    return "NA" if x is None else x


# ===========================================================================
# Low-level binary-cursor primitives (PSPP spvbin-helpers.c)
# ===========================================================================


class SpvBinaryEOF(ValueError):
    """R's ``spv_binary_eof`` condition: a read ran past the cursor limit."""


class _Cursor:
    """R's ``list(raw, pos, limit[, version])`` cursor (1-based positions)."""

    __slots__ = ("limit", "pos", "raw", "version")

    def __init__(self, raw: bytes, pos: int, limit: int, version: int | None = None) -> None:
        self.raw = raw
        self.pos = pos
        self.limit = limit
        self.version = version

    def at(self, pos: int) -> _Cursor:
        return _Cursor(self.raw, pos, self.limit, self.version)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Cursor(pos={self.pos}, limit={self.limit}, version={self.version})"


def _spvbin_cursor(raw: bytes) -> _Cursor:
    """Port of R/spv.R::.spvbin_cursor()."""
    raw = bytes(raw)
    return _Cursor(raw, 1, len(raw) + 1)


def _spvbin_eof(cur: _Cursor, n: int) -> NoReturn:
    """Port of R/spv.R::.spvbin_eof()."""
    raise SpvBinaryEOF(
        f"spv binary: needed {int(n)} bytes at offset {cur.pos}, "
        f"only {cur.limit - cur.pos} available"
    )


def _spvbin_read_bytes(cur: _Cursor, n: int) -> tuple[bytes, _Cursor]:
    """Port of R/spv.R::.spvbin_read_bytes()."""
    if cur.pos + n - 1 >= cur.limit:
        _spvbin_eof(cur, n)
    start = cur.pos - 1
    return cur.raw[start : start + n], cur.at(cur.pos + n)


def _spvbin_read_byte(cur: _Cursor) -> tuple[int, _Cursor]:
    """Port of R/spv.R::.spvbin_read_byte()."""
    b, cur2 = _spvbin_read_bytes(cur, 1)
    return b[0], cur2


def _spvbin_read_bool(cur: _Cursor) -> tuple[bool, _Cursor]:
    """Port of R/spv.R::.spvbin_read_bool()."""
    v, cur2 = _spvbin_read_byte(cur)
    if v not in (0, 1):
        raise ValueError(f"spv binary: bad bool byte {v} at offset {cur.pos}")
    return v == 1, cur2


def _spvbin_read_int(
    cur: _Cursor, size: int, signed: bool = False, endian: str = "little"
) -> tuple[Any, _Cursor]:
    """Port of R/spv.R::.spvbin_read_int().

    As in R, 4-byte reads go through ``readBin(signed = TRUE)`` (so the bit
    pattern ``0x80000000`` is R's ``NA``, which then fails the unsigned
    correction) and 8-byte reads return a double.
    """
    b, cur2 = _spvbin_read_bytes(cur, size)
    e = "<" if endian == "little" else ">"
    if size == 8:
        halves = list(struct.unpack(e + "ii", b))
        if -(2**31) in halves:
            return math.nan, cur2
        halves = [h + 2**32 if h < 0 else h for h in halves]
        if endian == "little":
            return float(halves[0] + halves[1] * 2**32), cur2
        return float(halves[1] + halves[0] * 2**32), cur2
    if size >= 4:
        (value,) = struct.unpack(e + "i", b[:4])
        if value == -(2**31):
            if signed:
                return None, cur2
            raise ValueError("missing value where TRUE/FALSE needed")
        if not signed and value < 0:
            value += 2**32
        return value, cur2
    fmt = {1: "b", 2: "h"}[size] if signed else {1: "B", 2: "H"}[size]
    (value,) = struct.unpack(e + fmt, b)
    return value, cur2


def _spvbin_read_int16(cur: _Cursor) -> tuple[int, _Cursor]:
    """Port of R/spv.R::.spvbin_read_int16()."""
    return _spvbin_read_int(cur, 2, endian="little")


def _spvbin_read_int32(cur: _Cursor) -> tuple[int, _Cursor]:
    """Port of R/spv.R::.spvbin_read_int32()."""
    return _spvbin_read_int(cur, 4, endian="little")


def _spvbin_read_int64(cur: _Cursor) -> tuple[float, _Cursor]:
    """Port of R/spv.R::.spvbin_read_int64()."""
    return _spvbin_read_int(cur, 8, endian="little")


def _spvbin_read_be16(cur: _Cursor) -> tuple[int, _Cursor]:
    """Port of R/spv.R::.spvbin_read_be16()."""
    return _spvbin_read_int(cur, 2, endian="big")


def _spvbin_read_be32(cur: _Cursor) -> tuple[int, _Cursor]:
    """Port of R/spv.R::.spvbin_read_be32()."""
    return _spvbin_read_int(cur, 4, endian="big")


def _spvbin_read_be64(cur: _Cursor) -> tuple[float, _Cursor]:
    """Port of R/spv.R::.spvbin_read_be64()."""
    return _spvbin_read_int(cur, 8, endian="big")


def _spvbin_read_double(cur: _Cursor) -> tuple[float, _Cursor]:
    """Port of R/spv.R::.spvbin_read_double()."""
    b, cur2 = _spvbin_read_bytes(cur, 8)
    return struct.unpack("<d", b)[0], cur2


def _spvbin_read_float(cur: _Cursor) -> tuple[float, _Cursor]:
    """Port of R/spv.R::.spvbin_read_float()."""
    b, cur2 = _spvbin_read_bytes(cur, 4)
    return struct.unpack("<f", b)[0], cur2


def _raw_to_char(b: bytes, errors: str = "replace") -> str:
    """R ``rawToChar()``: trailing NUL bytes are dropped, an embedded one is an error."""
    b = b.rstrip(b"\x00")
    if b"\x00" in b:
        raise ValueError("embedded nul in string")
    return b.decode("utf-8", errors=errors)


def _spvbin_read_string_(cur: _Cursor, be: bool = False) -> tuple[str, _Cursor]:
    """Port of R/spv.R::.spvbin_read_string_(): uint32 length prefix + bytes."""
    length, cur = _spvbin_read_be32(cur) if be else _spvbin_read_int32(cur)
    if length == 0:
        return "", cur
    b, cur = _spvbin_read_bytes(cur, length)
    return _raw_to_char(b), cur


def _spvbin_read_string(cur: _Cursor) -> tuple[str, _Cursor]:
    """Port of R/spv.R::.spvbin_read_string()."""
    return _spvbin_read_string_(cur, be=False)


def _spvbin_read_bestring(cur: _Cursor) -> tuple[str, _Cursor]:
    """Port of R/spv.R::.spvbin_read_bestring()."""
    return _spvbin_read_string_(cur, be=True)


def _as_bytes(x: int | Sequence[int] | bytes) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, int):
        return bytes([x])
    return bytes(x)


def _spvbin_match_bytes(cur: _Cursor, bytes_: int | Sequence[int] | bytes) -> tuple[bool, _Cursor]:
    """Port of R/spv.R::.spvbin_match_bytes(): a non-throwing literal probe."""
    want = _as_bytes(bytes_)
    n = len(want)
    if cur.pos + n - 1 >= cur.limit:
        return False, cur
    start = cur.pos - 1
    if cur.raw[start : start + n] != want:
        return False, cur
    return True, cur.at(cur.pos + n)


def _spvbin_match_byte(cur: _Cursor, byte: int) -> tuple[bool, _Cursor]:
    """Port of R/spv.R::.spvbin_match_byte()."""
    return _spvbin_match_bytes(cur, byte)


def _spvbin_expect_bytes(
    cur: _Cursor, bytes_: int | Sequence[int] | bytes, what: str | None = None
) -> _Cursor:
    """Port of R/spv.R::.spvbin_expect_bytes()."""
    matched, cur2 = _spvbin_match_bytes(cur, bytes_)
    if not matched:
        want = " ".join(str(b) for b in _as_bytes(bytes_))
        label = f" ({what})" if what is not None else ""
        raise ValueError(f"spv binary: expected marker byte(s) {want}{label} at offset {cur.pos}")
    return cur2


def _spvbin_read_count(cur: _Cursor, be: bool = False) -> tuple[_Cursor, _Cursor]:
    """Port of R/spv.R::.spvbin_read_count(): ``(inner, after)`` cursors.

    The inner cursor is a fresh cursor without the table version, as in R.
    """
    length, cur = _spvbin_read_be32(cur) if be else _spvbin_read_int32(cur)
    end = cur.pos + length
    if end - 1 >= cur.limit:
        _spvbin_eof(cur, length)
    inner = _Cursor(cur.raw, cur.pos, end)
    return inner, cur.at(end)


# ===========================================================================
# Legacy raw case-data decoder (PSPP old-binary.grammar / spv-legacy-data.c)
# ===========================================================================


def _spvob_read_metadata(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvob_read_metadata()."""
    n_values, cur = _spvbin_read_int32(cur)
    n_vars, cur = _spvbin_read_int32(cur)
    data_offset, cur = _spvbin_read_int32(cur)
    name_raw, cur = _spvbin_read_bytes(cur, 28)
    ext_name_raw, cur = _spvbin_read_bytes(cur, 36)
    _, cur = _spvbin_read_int32(cur)
    name0 = _spvob_fixed_string(name_raw)
    # nchar(name0, "bytes") >= 28: the 28-byte field holds no NUL terminator
    name = name0 + _spvob_fixed_string(ext_name_raw) if b"\x00" not in name_raw else name0
    return {
        "n_values": n_values,
        "n_vars": n_vars,
        "data_offset": data_offset,
        "source_name": name,
    }, cur


def _spvob_fixed_string(raw_bytes: bytes) -> str:
    """Port of R/spv.R::.spvob_fixed_string(): content up to the first NUL."""
    nul = raw_bytes.find(b"\x00")
    n = len(raw_bytes) if nul < 0 else nul
    if n == 0:
        return ""
    return _raw_to_char(raw_bytes[:n])


def _spv_decode_legacy_data(raw: bytes) -> list[dict[str, Any]] | None:
    """Port of R/spv.R::.spv_decode_legacy_data().

    Decodes a legacy ``dataPath`` member into a list of sources, each
    ``{"source_name", "variables": [{"var_name", "values": [{"d", "s"}]}]}``
    (``d`` numeric or ``None``, ``s`` a value label or ``None``). Returns
    ``None`` (with a warning) when the member cannot be decoded.
    """
    try:
        raw = bytes(raw)
        cur = _spvbin_cursor(raw)
        cur = _spvbin_expect_bytes(cur, 0x00, "LegacyBinary marker")
        _, cur = _spvbin_read_byte(cur)
        n_sources, cur = _spvbin_read_int16(cur)
        _, cur = _spvbin_read_int32(cur)

        metas = []
        for _ in range(n_sources):
            m, cur = _spvob_read_metadata(cur)
            metas.append(m)

        sources: list[dict[str, Any]] = []
        max_end = cur.pos
        for md in metas:
            var_size = 288 + md["n_values"] * 8
            source_size = md["n_vars"] * var_size
            if md["data_offset"] + source_size > len(raw):
                raise ValueError(
                    f"spv legacy data: source '{md['source_name']}' runs past end of member"
                )
            pos = md["data_offset"]  # 0-based
            variables = []
            for _v in range(md["n_vars"]):
                vname = _spvob_fixed_string(raw[pos : pos + 288])
                pos += 288
                n = md["n_values"]
                ds = struct.unpack(f"<{n}d", raw[pos : pos + 8 * n])
                pos += 8 * n
                variables.append({"var_name": vname, "values": [{"d": d, "s": None} for d in ds]})
            sources.append({"source_name": md["source_name"], "variables": variables})
            max_end = max(max_end, md["data_offset"] + source_size + 1)

        if max_end - 1 < len(raw):
            cur2 = _Cursor(raw, max_end, len(raw) + 1)
            try:
                strings = _spvob_read_strings(cur2)
            except Exception:
                strings = None
            if strings is not None:
                sources = _spvob_apply_strings(sources, strings)
        return sources
    except Exception as e:
        warnings.warn(f"spv: could not decode legacy data: {e}", stacklevel=2)
        return None


def _spvob_read_strings(cur: _Cursor) -> dict[str, Any]:
    """Port of R/spv.R::.spvob_read_strings() (the trailing Strings section)."""
    n_maps, cur = _spvbin_read_int32(cur)
    maps = []
    for _ in range(n_maps):
        sname, cur = _spvbin_read_string(cur)
        n_vars, cur = _spvbin_read_int32(cur)
        vmaps = []
        for _j in range(n_vars):
            vname, cur = _spvbin_read_string(cur)
            n_data, cur = _spvbin_read_int32(cur)
            datum_maps = []
            for _k in range(n_data):
                vidx, cur = _spvbin_read_int32(cur)
                lidx, cur = _spvbin_read_int32(cur)
                datum_maps.append({"value_idx": vidx, "label_idx": lidx})
            vmaps.append({"variable_name": vname, "data": datum_maps})
        maps.append({"source_name": sname, "variables": vmaps})
    n_labels, cur = _spvbin_read_int32(cur)
    labels = []
    for _ in range(n_labels):
        freq, cur = _spvbin_read_int32(cur)
        lbl, cur = _spvbin_read_string(cur)
        labels.append({"frequency": freq, "label": lbl})
    return {"maps": maps, "labels": labels}


def _spvob_apply_strings(
    sources: list[dict[str, Any]], strings: dict[str, Any]
) -> list[dict[str, Any]]:
    """Port of R/spv.R::.spvob_apply_strings(): relabel values with labels."""
    labels = strings["labels"]
    for sm in strings["maps"]:
        si = next((i for i, s in enumerate(sources) if s["source_name"] == sm["source_name"]), None)
        if si is None:
            continue
        src = sources[si]
        for vm in sm["variables"]:
            var = next((v for v in src["variables"] if v["var_name"] == vm["variable_name"]), None)
            if var is None:
                continue
            for dm in vm["data"]:
                k = dm["value_idx"] + 1
                li = dm["label_idx"] + 1
                if 1 <= k <= len(var["values"]) and 1 <= li <= len(labels):
                    var["values"][k - 1] = {"d": None, "s": labels[li - 1]["label"]}
    return sources


# ===========================================================================
# XML helpers (xml2 semantics on lxml)
# ===========================================================================


def _xml_parse(xml_raw: bytes | str) -> etree._Element:
    """``xml2::read_xml()`` (``NOBLANKS``) and ``xml_root()``."""
    from lxml import etree

    if isinstance(xml_raw, str):
        xml_raw = xml_raw.encode("utf-8")
    parser = etree.XMLParser(remove_blank_text=True, huge_tree=True, resolve_entities=False)
    return etree.fromstring(bytes(xml_raw), parser)


def _xml_parse_file(path: str) -> etree._Element:
    return _xml_parse(Path(path).read_bytes())


def _localname(el: Any) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _first(nodes: Any) -> Any:
    return nodes[0] if nodes else None


def _find_first(node: Any, xpath: str) -> Any:
    """``xml_find_first()`` (``None`` for ``xml_missing``)."""
    if node is None:
        return None
    return _first(node.xpath(xpath))


def _find_all(node: Any, xpath: str) -> list[Any]:
    """``xml_find_all()`` (empty for a missing node)."""
    if node is None:
        return []
    return list(node.xpath(xpath))


def _xml_attr(node: Any, name: str) -> str | None:
    """``xml_attr()`` (``None`` for ``NA``)."""
    if node is None:
        return None
    v = node.get(name)
    return None if v is None else str(v)


def _xml_text(node: Any) -> str | None:
    """``xml_text()``: the concatenated text content (``None`` for missing)."""
    if node is None:
        return None
    return str(node.xpath("string()"))


def _xml_root(node: Any) -> Any:
    return node.getroottree().getroot()


def _sh_quote(x: str | None) -> str:
    """R ``shQuote(x, type = "sh")``."""
    s = "NA" if x is None else x
    if "'" not in s:
        return f"'{s}'"
    return '"' + re.sub(r'(["$`\\])', r"\\\1", s) + '"'


def _find_by_id(root: Any, id_: str | None) -> Any:
    return _find_first(root, f".//*[@id={_sh_quote(id_)}]")


def _remove_node(el: Any) -> None:
    """``xml2::xml_remove()``: unlink a node, keeping its following text."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


# ===========================================================================
# Legacy table decoder (detail-xml + raw case data)
# ===========================================================================


def _spvdx_read_source_variable(node: Any, data: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Port of R/spv.R::.spvdx_read_source_variable()."""
    source_name = _xml_attr(node, "sourceName")
    source = _xml_attr(node, "source")
    src = next((s for s in data if source is not None and s["source_name"] == source), None)
    if src is None:
        return None
    var = next(
        (v for v in src["variables"] if source_name is not None and v["var_name"] == source_name),
        None,
    )
    if var is None:
        return None
    label_var_id = _xml_attr(node, "labelVariable")
    values = var["values"]
    if label_var_id is not None:
        label_ref = _find_by_id(_xml_root(node), label_var_id)
        if label_ref is not None:
            lbl_source_name = _xml_attr(label_ref, "sourceName")
            lbl_var = next(
                (
                    v
                    for v in src["variables"]
                    if lbl_source_name is not None and v["var_name"] == lbl_source_name
                ),
                None,
            )
            if lbl_var is not None and len(lbl_var["values"]) == len(values):
                values = [
                    values[i] if _is_na_num(values[i]["d"]) else lbl_var["values"][i]
                    for i in range(len(values))
                ]
    return {"id": _xml_attr(node, "id"), "values": values}


def _is_na_num(d: float | None) -> bool:
    return d is None or math.isnan(d)


def _spvdx_read_derived_variable(node: Any, n_values: int) -> dict[str, Any]:
    """Port of R/spv.R::.spvdx_read_derived_variable() (``constant(N)`` only)."""
    value_expr = _xml_attr(node, "value")
    if value_expr is None or not value_expr.startswith("constant("):
        raise ValueError(f"spv legacy: unsupported derivedVariable value '{_na_str(value_expr)}'")
    return {"id": _xml_attr(node, "id"), "values": [{"d": 0.0, "s": None} for _ in range(n_values)]}


def _spvdx_read_all_series(root: Any, data: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """Port of R/spv.R::.spvdx_read_all_series(): resolve variables to a fixed point."""
    var_nodes = _find_all(
        root, "./*[local-name()='sourceVariable' or local-name()='derivedVariable']"
    )
    series: dict[Any, dict[str, Any]] = {}
    remaining = list(range(len(var_nodes)))
    while True:
        progressed = False
        still: list[int] = []
        for i in remaining:
            node = var_nodes[i]
            try:
                if _localname(node) == "sourceVariable":
                    s = _spvdx_read_source_variable(node, data)
                elif series:
                    n = len(next(iter(series.values()))["values"])
                    s = _spvdx_read_derived_variable(node, n)
                else:
                    s = None
            except Exception:
                s = None
            if s is not None:
                series[s["id"]] = s
                progressed = True
            else:
                still.append(i)
        remaining = still
        if not remaining or not progressed:
            break
    return series


def _series_get(series: dict[Any, dict[str, Any]], key: str | None) -> dict[str, Any] | None:
    """R ``series[[key]]`` on a named list: ``NULL`` for ``NA`` or ``""``."""
    if key is None or key == "":
        return None
    return series.get(key)


def _spvdx_nest_series_ids(nest_node: Any) -> list[str | None]:
    """Port of R/spv.R::.spvdx_nest_series_ids()."""
    return [
        _xml_attr(r, "ref") for r in _find_all(nest_node, "./*[local-name()='variableReference']")
    ]


def _string_frame(columns: list[list[Any]], names: list[str]) -> pd.DataFrame:
    """A data frame of character columns built positionally (names may repeat)."""
    import pandas as pd

    df = pd.DataFrame({i: pd.array(col, dtype="string") for i, col in enumerate(columns)})
    df.columns = list(names)
    return df


def _spv_decode_legacy_table(
    xml_raw: bytes | str, data: list[dict[str, Any]], title: str | None = None
) -> pd.DataFrame | None:
    """Port of R/spv.R::.spv_decode_legacy_table().

    Cross-references a legacy table's detail-XML (``<visualization>``) with its
    decoded case data (:func:`_spv_decode_legacy_data`). Returns the tidy table
    or ``None`` (with a warning) for an unsupported construct.
    """
    try:
        root = _xml_parse(xml_raw)
        series = _spvdx_read_all_series(root, data)

        graph = _find_first(root, "./*[local-name()='graph']")
        cross = _find_first(graph, ".//*[local-name()='cross']")
        nests = _find_all(cross, "./*[local-name()='nest']")
        if len(nests) != 2:
            raise ValueError(
                f"spv legacy: expected exactly 2 nests (rows, columns), found {len(nests)}"
            )
        col_ids = _spvdx_nest_series_ids(nests[0])
        row_ids = _spvdx_nest_series_ids(nests[1])

        def is_real_dim(id_: str | None) -> bool:
            node = _find_by_id(root, id_)
            return node is not None and _localname(node) == "sourceVariable"

        col_ids = [i for i in col_ids if is_real_dim(i)]
        row_ids = [i for i in row_ids if is_real_dim(i)]
        if not col_ids and not row_ids:
            raise ValueError("spv legacy: no real dimension series found")

        labeling = _find_first(graph, ".//*[local-name()='interval']/*[local-name()='labeling']")
        cell_id = _xml_attr(labeling, "variable")
        cell_series = _series_get(series, cell_id)
        if cell_series is None:
            raise ValueError(f"spv legacy: no cell series '{_na_str(cell_id)}'")

        dim_ids = col_ids + row_ids
        dims = [_series_get(series, i) for i in dim_ids]
        if any(d is None for d in dims):
            raise ValueError("spv legacy: missing dimension series")
        n_cells = len(cell_series["values"])
        if any(len(d["values"]) != n_cells for d in dims):  # type: ignore[index]
            raise ValueError("spv legacy: dimension/cell series length mismatch")

        names = []
        for i, id_ in enumerate(dim_ids, 1):
            nm = _xml_attr(_find_by_id(root, id_), "label")
            names.append(nm if nm is not None and nm != "" else f"dim{i}")
        dim_names = _make_unique(names)

        if n_cells == 0:
            raise ValueError(
                f"'names' attribute [{len(dim_names) + 1}] must be the same length as the vector [0]"
            )
        columns = [
            [_spvdx_data_value_text(v) for v in d["values"]]  # type: ignore[index]
            for d in dims
        ]
        columns.append([_spvdx_data_value_text(v) for v in cell_series["values"]])
        df = _string_frame(columns, [*dim_names, "value"])
        df.attrs["spv_title"] = title
        df.attrs["spv_col_dims"] = dim_names[: len(col_ids)]
        df.attrs["spv_row_dims"] = dim_names[len(col_ids) : len(col_ids) + len(row_ids)]
        return df
    except Exception as e:
        warnings.warn(f"spv: could not decode legacy table: {e}", stacklevel=2)
        return None


def _spvdx_data_value_text(v: dict[str, Any]) -> str | None:
    """Port of R/spv.R::.spvdx_data_value_text()."""
    s = v.get("s")
    if s is not None:
        return str(s)
    d = v.get("d")
    if _is_na_num(d):
        return None
    return _stat_num_to_chr(float(d))  # type: ignore[arg-type]


# ===========================================================================
# Chart decoder (VizML chart.xml + legacy case data)
# ===========================================================================


def _spvviz_resolve_variable(
    root: Any, var_id: str | None, data: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Port of R/spv.R::.spvviz_resolve_variable()."""
    node = _find_by_id(root, var_id)
    if node is None or _localname(node) != "sourceVariable":
        return None
    source_name = _xml_attr(node, "sourceName")
    source = _xml_attr(node, "source")
    src = next((s for s in data if source is not None and s["source_name"] == source), None)
    if src is None:
        return None
    var = next(
        (v for v in src["variables"] if source_name is not None and v["var_name"] == source_name),
        None,
    )
    if var is None:
        return None
    return {"id": var_id, "source_name": source_name, "values": var["values"]}


# ---------------------------------------------------------------------------
# SPSS fitted-curve expressions: R's parse(text = ) and a small evaluator
# ---------------------------------------------------------------------------
#
# .spvviz_function_guide() keeps a function guide whenever R's parser accepts
# its text, and .spv_chart_html() evaluates it for the fitted line: an R error
# skips that line, while a value that is not a vector as long as `x` makes
# graphics::lines() stop ("'x' and 'y' lengths differ"). A recursive-descent
# port of R's grammar decides the first question; the evaluator covers R's
# vector arithmetic, comparisons, logic, a set of base math functions and
# closures, and treats anything else as an R error.


class _RParseError(ValueError):
    """``parse(text = )`` failed."""


class _REvalError(ValueError):
    """Evaluating a fitted-curve expression raised an R error."""


_R_RESERVED = frozenset(
    [
        "if",
        "else",
        "repeat",
        "while",
        "function",
        "for",
        "next",
        "break",
        "in",
        "TRUE",
        "FALSE",
        "NULL",
        "Inf",
        "NaN",
        "NA",
        "NA_integer_",
        "NA_real_",
        "NA_character_",
        "NA_complex_",
    ]
)
_R_NUMBER = re.compile(
    r"(0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?[0-9]+)?"
    r"|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)([Li]?)"
)
_R_OPS = (
    "<<-", "->>", ":::", "|>", "::", ":=", "<-", "->", "<=", ">=", "==", "!=", "&&", "||",
    "**", "[[", "+", "-", "*", "/", "^", "<", ">", "!", "&", "|", "~", "?", ":", "$", "@",
    "=", "(", ")", "{", "}", "[", "]", ",", ";", "\\",
)  # fmt: skip
_R_ESCAPES = set("nrtbafv\\'\"` \n01234567")
_HEXDIGITS = set("0123456789abcdefABCDEF")


def _r_ident_char(c: str, first: bool) -> bool:
    if c.isalpha() or c == ".":
        return True
    return not first and (c == "_" or "0" <= c <= "9")


def _r_scan_string(s: str, i: int, quote: str) -> int:
    """End index (past the closing quote) of an R string literal starting at *i*."""
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == quote:
            return j + 1
        if c != "\\":
            j += 1
            continue
        j += 1
        e = s[j] if j < len(s) else ""
        if e in ("x", "u", "U"):
            limit = {"x": 2, "u": 4, "U": 8}[e]
            j += 1
            braced = e != "x" and j < len(s) and s[j] == "{"
            j += braced
            k = 0
            while k < limit and j < len(s) and s[j] in _HEXDIGITS:
                j += 1
                k += 1
            if k == 0 or (braced and (j >= len(s) or s[j] != "}")):
                raise _RParseError(f"malformed '\\{e}' escape")
            j += braced
        elif e and e in _R_ESCAPES:
            j += 1
        else:
            raise _RParseError(f"'\\{e}' is an unrecognized escape")
    raise _RParseError("unexpected INCOMPLETE_STRING")


def _r_tokenize(s: str) -> list[tuple[str, Any]]:
    """R's lexer: ``NUM``/``STR``/``SYM``/``KW``/``OP``/``NL`` tokens, then ``EOF``."""
    toks: list[tuple[str, Any]] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\f\r":
            i += 1
        elif c == "\n":
            toks.append(("NL", None))
            i += 1
        elif c == "#":
            while i < n and s[i] != "\n":
                i += 1
        elif c in "rR" and i + 1 < n and s[i + 1] in "'\"":
            m = re.match(r"[rR](['\"])(-*)([(\[{])", s[i:])
            if m is None:
                raise _RParseError("malformed raw string literal")
            closer = {"(": ")", "[": "]", "{": "}"}[m.group(3)] + m.group(2) + m.group(1)
            end = s.find(closer, i + m.end())
            if end < 0:
                raise _RParseError("unexpected INCOMPLETE_STRING")
            toks.append(("STR", s[i + m.end() : end]))
            i = end + len(closer)
        elif c in "'\"":
            end = _r_scan_string(s, i, c)
            toks.append(("STR", s[i + 1 : end - 1]))
            i = end
        elif c == "`":
            end = s.find("`", i + 1)
            if end <= i + 1:
                raise _RParseError("attempt to use zero-length variable name")
            toks.append(("SYM", s[i + 1 : end]))
            i = end + 1
        elif c.isdigit() or (c == "." and i + 1 < n and s[i + 1].isdigit()):
            m = _R_NUMBER.match(s, i)
            if m is None:  # pragma: no cover - the class check above guarantees a match
                raise _RParseError("unexpected numeric constant")
            toks.append(("NUM", (m.group(1), m.group(2))))
            i = m.end()
        elif _r_ident_char(c, True):
            j = i + 1
            while j < n and _r_ident_char(s[j], False):
                j += 1
            word = s[i:j]
            toks.append(("KW" if word in _R_RESERVED else "SYM", word))
            i = j
        elif c == "%":
            j = i + 1
            while j < n and s[j] not in "%\n":
                j += 1
            if j >= n or s[j] != "%":
                raise _RParseError("unexpected input")
            toks.append(("OP", s[i : j + 1]))
            i = j + 1
        else:
            op = next((o for o in _R_OPS if s.startswith(o, i)), None)
            if op is None:
                raise _RParseError("unexpected input")
            toks.append(("OP", "^" if op == "**" else op))
            i += len(op)
    toks.append(("EOF", None))
    return toks


# (left binding power, right binding power) of R's binary operators
_R_INFIX: dict[str, tuple[int, int]] = {
    "?": (1, 2),
    "=": (4, 3),
    "<-": (6, 5),
    "<<-": (6, 5),
    ":=": (6, 5),
    "->": (7, 8),
    "->>": (7, 8),
    "~": (9, 10),
    "||": (11, 12),
    "|": (11, 12),
    "&&": (13, 14),
    "&": (13, 14),
    "==": (17, 18),
    "!=": (17, 18),
    "<": (17, 18),
    ">": (17, 18),
    "<=": (17, 18),
    ">=": (17, 18),
    "+": (19, 20),
    "-": (19, 20),
    "*": (21, 22),
    "/": (21, 22),
    "|>": (23, 24),
    ":": (25, 26),
    "^": (30, 29),
    "$": (31, 32),
    "@": (31, 32),
    "(": (33, 0),
    "[": (33, 0),
    "[[": (33, 0),
}
_R_COMPARE = frozenset(("==", "!=", "<", ">", "<=", ">="))
_R_LOW = 2  # the body of if/function/for/while/repeat extends this far


class _RParser:
    """A recursive-descent (Pratt) port of R's grammar, used to validate and build ASTs."""

    def __init__(self, text: str) -> None:
        self.toks = _r_tokenize(text)
        self.i = 0
        self.ctx = ["top"]

    def peek(self, k: int = 0) -> tuple[str, Any]:
        j, seen = self.i, -1
        while True:
            t = self.toks[j]
            if t[0] == "NL" and self.ctx[-1] in ("(", "["):
                j += 1
                continue
            seen += 1
            if seen == k or t[0] == "EOF":
                return t
            j += 1

    def take(self) -> tuple[str, Any]:
        while self.toks[self.i][0] == "NL" and self.ctx[-1] in ("(", "["):
            self.i += 1
        t = self.toks[self.i]
        if t[0] != "EOF":
            self.i += 1
        return t

    def skip_nl(self) -> None:
        while self.toks[self.i][0] == "NL":
            self.i += 1

    def expect(self, op: str) -> None:
        if self.take() != ("OP", op):
            raise _RParseError(f"expected '{op}'")

    def program(self) -> list[Any]:
        exprs = []
        self.skip_nl()
        while self.peek()[0] != "EOF":
            exprs.append(self.expr(0))
            t = self.take()
            if t == ("OP", ";"):
                continue
            if t[0] == "NL":
                self.skip_nl()
            elif t[0] != "EOF":
                raise _RParseError("unexpected token")
        return exprs

    def expr(self, rbp: int) -> Any:
        left = self.nud(self.take())
        while True:
            t = self.peek()
            if t[0] != "OP" or (t[1] not in _R_INFIX and not t[1].startswith("%")):
                return left
            lbp, nbp = _R_INFIX.get(t[1], (23, 24))
            if lbp <= rbp:
                return left
            self.take()
            left = self.led(t[1], nbp, left)

    def operand(self, rbp: int) -> Any:
        self.skip_nl()
        return self.expr(rbp)

    def nud(self, t: tuple[str, Any]) -> Any:
        kind, val = t
        if kind in ("SYM", "STR") and self.peek() in (("OP", "::"), ("OP", ":::")):
            self.take()
            name = self.take()
            if name[0] not in ("SYM", "STR"):
                raise _RParseError("unexpected token after '::'")
            return ("ns", val, name[1])
        if kind == "NUM":
            return ("num", val)
        if kind == "STR":
            return ("str", val)
        if kind == "SYM":
            return ("sym", val)
        if kind == "KW":
            if val in ("function", "if", "for", "while", "repeat", "break", "next"):
                return getattr(self, "kw_" + val)()
            if val in ("else", "in"):
                raise _RParseError(f"unexpected '{val}'")
            return ("const", val)
        if kind == "OP":
            if val == "\\":
                return self.kw_function()
            if val == "(":
                self.ctx.append("(")
                inner = self.operand(0)
                self.ctx.pop()
                self.expect(")")
                return ("paren", inner)
            if val == "{":
                return self.braces()
            if val in ("-", "+"):
                return ("unop", val, self.operand(27))
            if val == "!":
                return ("unop", "!", self.operand(15))
            if val == "~":
                return ("formula", None, self.operand(10))
            if val == "?":
                return ("unop", "?", self.operand(2))
        raise _RParseError("unexpected token")

    def led(self, op: str, rbp: int, left: Any) -> Any:
        if op == "(":
            return ("call", left, self.args(")"))
        if op in ("[", "[["):
            args = self.args("]")
            if op == "[[":
                self.expect("]")
            return ("index", left, args, op == "[[")
        if op in ("$", "@"):
            name = self.take()
            if name[0] not in ("SYM", "STR"):
                raise _RParseError(f"unexpected token after '{op}'")
            return ("dollar", op, left, name[1])
        right = self.operand(rbp)
        if op in _R_COMPARE and self.peek()[0] == "OP" and self.peek()[1] in _R_COMPARE:
            raise _RParseError("unexpected comparison")  # %nonassoc
        if op == "|>":
            if right[0] != "call":
                raise _RParseError("The pipe operator requires a function call as RHS")
            return ("call", right[1], [(None, left), *right[2]])
        if op == "~":
            return ("formula", left, right)
        if op in ("<-", "<<-", "=", ":="):
            return ("assign", op, left, right)
        if op in ("->", "->>"):
            return ("assign", op, right, left)
        return ("binop", op, left, right)

    def args(self, closer: str) -> list[tuple[Any, Any]]:
        self.ctx.append("(" if closer == ")" else "[")
        out: list[tuple[Any, Any]] = []
        while True:
            t = self.peek()
            if t == ("OP", closer) or t == ("OP", ","):
                out.append((None, None))
            elif (t[0] in ("SYM", "STR") or t == ("KW", "NULL")) and self.peek(1) == ("OP", "="):
                self.take()
                self.take()
                nxt = self.peek()
                value = None if nxt in (("OP", closer), ("OP", ",")) else self.expr(4)
                out.append((t[1], value))
            else:
                out.append((None, self.expr(4)))
            t = self.take()
            if t == ("OP", closer):
                break
            if t != ("OP", ","):
                raise _RParseError("unexpected token in argument list")
        self.ctx.pop()
        return [] if out == [(None, None)] else out

    def braces(self) -> Any:
        self.ctx.append("{")
        body = []
        while True:
            self.skip_nl()
            t = self.peek()
            if t == ("OP", "}"):
                self.take()
                break
            if t == ("OP", ";"):
                self.take()
                continue
            body.append(self.expr(0))
            if self.peek() not in (("OP", ";"), ("OP", "}")) and self.peek()[0] != "NL":
                raise _RParseError("unexpected token in braces")
        self.ctx.pop()
        return ("brace", body)

    def kw_function(self) -> Any:
        self.expect("(")
        self.ctx.append("(")
        formals: list[tuple[str, Any]] = []
        if self.peek() != ("OP", ")"):
            while True:
                t = self.take()
                if t[0] != "SYM":
                    raise _RParseError("unexpected token in formals")
                if any(f[0] == t[1] for f in formals):
                    raise _RParseError(f"repeated formal argument '{t[1]}'")
                default = None
                if self.peek() == ("OP", "="):
                    self.take()
                    default = self.expr(4)
                formals.append((t[1], default))
                if self.peek() != ("OP", ","):
                    break
                self.take()
        self.ctx.pop()
        self.expect(")")
        return ("function", formals, self.operand(_R_LOW))

    def kw_if(self) -> Any:
        cond = self.condition()
        then = self.operand(_R_LOW)
        save = self.i
        if self.ctx[-1] != "top":
            self.skip_nl()
        if self.peek() == ("KW", "else"):
            self.take()
            return ("if", cond, then, self.operand(_R_LOW))
        self.i = save
        return ("if", cond, then, None)

    def condition(self) -> Any:
        self.expect("(")
        self.ctx.append("(")
        cond = self.expr(0)
        self.ctx.pop()
        self.expect(")")
        return cond

    def kw_for(self) -> Any:
        self.expect("(")
        self.ctx.append("(")
        var = self.take()
        if var[0] != "SYM" or self.take() != ("KW", "in"):
            raise _RParseError("unexpected token in for()")
        seq = self.expr(0)
        self.ctx.pop()
        self.expect(")")
        return ("for", var[1], seq, self.operand(_R_LOW))

    def kw_while(self) -> Any:
        return ("while", self.condition(), self.operand(_R_LOW))

    def kw_repeat(self) -> Any:
        return ("repeat", self.operand(_R_LOW))

    def kw_break(self) -> Any:
        return ("break",)

    def kw_next(self) -> Any:
        return ("next",)


def _r_parse(text: str) -> list[Any]:
    """R ``parse(text = text)``: the expressions, or :class:`_RParseError`."""
    return _RParser(text).program()


# -- evaluation (R vector semantics) ------------------------------------------
# Values: ("num", [float|complex]), ("chr", [str|None]), ("null",),
# ("closure", formals, body, env) or ("obj", length) for other R objects.

_NA = math.nan


def _r_num_literal(text: str, suffix: str) -> Any:
    low = text.lower()
    if low.startswith("0x"):
        v = float.fromhex(text) if ("." in text or "p" in low) else float(int(text, 16))
    else:
        v = float(text)
    return ("num", [complex(0, v)]) if suffix == "i" else ("num", [v])


_R_CONST_VALUES: dict[str, Any] = {
    "TRUE": ("num", [1.0]),
    "FALSE": ("num", [0.0]),
    "NULL": ("null",),
    "Inf": ("num", [math.inf]),
    "NaN": ("num", [math.nan]),
    "NA": ("num", [_NA]),
    "NA_integer_": ("num", [_NA]),
    "NA_real_": ("num", [_NA]),
    "NA_complex_": ("num", [_NA]),
    "NA_character_": ("chr", [None]),
}


def _r_len(v: Any) -> int:
    kind = v[0]
    if kind in ("num", "chr"):
        return len(v[1])
    if kind == "null":
        return 0
    if kind == "closure":
        return 1
    return int(v[1])


def _r_nums(v: Any, what: str = "non-numeric argument to binary operator") -> list[Any]:
    if v[0] == "num":
        return list(v[1])
    if v[0] == "null":
        return []
    raise _REvalError(what)


def _r_isna(a: Any) -> bool:
    return isinstance(a, float) and math.isnan(a)


def _r_arith(op: str, a: Any, b: Any) -> Any:
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0 and not isinstance(a, complex) and not isinstance(b, complex):
                return (
                    _NA
                    if a == 0 or _r_isna(a)
                    else math.copysign(math.inf, a) * (-1.0 if math.copysign(1.0, b) < 0 else 1.0)
                )
            return a / b
        if op == "^":
            if a == 1 or b == 0:
                return 1.0
            if isinstance(a, complex) or isinstance(b, complex):
                return complex(a) ** complex(b)
            if a < 0 and b != int(b):
                return _NA
            return math.pow(a, b)
        if isinstance(a, complex) or isinstance(b, complex):
            raise _REvalError("invalid operation on complex numbers")
        if op == "%%":
            return _NA if b == 0 else a - math.floor(a / b) * b
        if op == "%/%":
            return float(math.floor(a / b)) if b != 0 else _r_arith("/", a, b)
    except OverflowError:
        return math.inf
    except (ValueError, ZeroDivisionError):
        return _NA
    raise _REvalError(f'could not find function "{op}"')


def _r_recycle(a: list[Any], b: list[Any]) -> tuple[list[Any], list[Any]]:
    if not a or not b:
        return [], []
    n = max(len(a), len(b))
    return [a[i % len(a)] for i in range(n)], [b[i % len(b)] for i in range(n)]


def _r_truth(v: Any, what: str) -> bool:
    """A length-one condition, as ``if``/``&&`` require it."""
    vals = v[1] if v[0] in ("num", "chr") else None
    if vals is None or len(vals) == 0:
        raise _REvalError(f"argument is of length zero in {what}")
    if len(vals) > 1:
        raise _REvalError("the condition has length > 1")
    a = vals[0]
    if isinstance(a, str):
        if a in ("TRUE", "true", "True", "T"):
            return True
        if a in ("FALSE", "false", "False", "F"):
            return False
        a = None
    if a is None or _r_isna(a):
        raise _REvalError("missing value where TRUE/FALSE needed")
    return bool(a)


def _r_math1(fn: Callable[[float], float]) -> Callable[[list[Any]], Any]:
    def run(args: list[Any]) -> Any:
        if len(args) != 1:
            raise _REvalError("wrong number of arguments")
        out = []
        for a in _r_nums(args[0], "non-numeric argument to mathematical function"):
            if isinstance(a, complex):
                raise _REvalError("unimplemented complex function")
            try:
                out.append(_NA if _r_isna(a) else float(fn(a)))
            except OverflowError:
                out.append(math.inf)
            except ValueError:
                out.append(_NA)
        return ("num", out)

    return run


def _r_log(args: list[Any]) -> Any:
    if not 1 <= len(args) <= 2:
        raise _REvalError("wrong number of arguments")
    base = _r_nums(args[1])[0] if len(args) == 2 else math.e
    out = []
    for a in _r_nums(args[0], "non-numeric argument to mathematical function"):
        if isinstance(a, complex):
            raise _REvalError("unimplemented complex function")
        if _r_isna(a) or a < 0:
            out.append(_NA)
        elif a == 0:
            out.append(-math.inf)
        else:
            out.append(math.log(a) / math.log(base))
    return ("num", out)


def _r_reduce(fn: Callable[[list[float]], float]) -> Callable[[list[Any]], Any]:
    def run(args: list[Any]) -> Any:
        vals = [a for v in args for a in _r_nums(v, "invalid 'type' of argument")]
        return ("num", [fn(vals)])

    return run


def _r_minmax(pick: Callable[..., float], empty: float) -> Callable[[list[float]], float]:
    def run(vals: list[float]) -> float:
        if any(_r_isna(v) for v in vals):
            return _NA
        return pick(vals) if vals else empty

    return run


_R_BUILTINS: dict[str, Callable[[list[Any]], Any]] = {
    "exp": _r_math1(math.exp),
    "sqrt": _r_math1(math.sqrt),
    "abs": _r_math1(abs),
    "sin": _r_math1(math.sin),
    "cos": _r_math1(math.cos),
    "tan": _r_math1(math.tan),
    "asin": _r_math1(math.asin),
    "acos": _r_math1(math.acos),
    "atan": _r_math1(math.atan),
    "sinh": _r_math1(math.sinh),
    "cosh": _r_math1(math.cosh),
    "tanh": _r_math1(math.tanh),
    "log10": _r_math1(math.log10),
    "log2": _r_math1(math.log2),
    "log1p": _r_math1(math.log1p),
    "expm1": _r_math1(math.expm1),
    "floor": _r_math1(math.floor),
    "ceiling": _r_math1(math.ceil),
    "trunc": _r_math1(math.trunc),
    "sign": _r_math1(lambda a: float((a > 0) - (a < 0))),
    "gamma": _r_math1(math.gamma),
    "lgamma": _r_math1(math.lgamma),
    "log": _r_log,
    "c": lambda args: ("num", [a for v in args for a in _r_nums(v, "unsupported c()")]),
    "sum": _r_reduce(lambda v: math.fsum(v) if not any(_r_isna(a) for a in v) else _NA),
    "prod": _r_reduce(lambda v: math.prod(v)),
    "mean": _r_reduce(lambda v: math.fsum(v) / len(v) if v else _NA),
    "min": _r_reduce(_r_minmax(min, math.inf)),
    "max": _r_reduce(_r_minmax(max, -math.inf)),
    "length": lambda args: ("num", [float(_r_len(args[0]))]) if len(args) == 1 else _r_bad(),
    "identity": lambda args: args[0] if len(args) == 1 else _r_bad(),
    "invisible": lambda args: args[0] if len(args) == 1 else _r_bad(),
    "list": lambda args: ("obj", len(args)),
}


def _r_bad() -> Any:
    raise _REvalError("unused or missing argument")


def _r_count(args: list[Any]) -> int:
    if len(args) > 1:
        _r_bad()
    vals = _r_nums(args[0], "invalid 'length' argument") if args else [0.0]
    if len(vals) != 1 or _r_isna(vals[0]) or vals[0] < 0:
        raise _REvalError("invalid 'length' argument")
    return int(vals[0])


def _r_rep(args: list[Any]) -> Any:
    if not 1 <= len(args) <= 2 or args[0][0] not in ("num", "chr"):
        _r_bad()
    times = _r_count(args[1:]) if len(args) == 2 else 1
    return (args[0][0], list(args[0][1]) * times)


def _r_round(args: list[Any]) -> Any:
    from pytacheck._r import r_round

    digits = int(_r_nums(args[1])[0]) if len(args) == 2 else 0
    if not 1 <= len(args) <= 2:
        _r_bad()
    vals = _r_nums(args[0], "non-numeric argument to mathematical function")
    return ("num", [a if _r_isna(a) else float(r_round(a, digits)) for a in vals])


_R_BUILTINS.update(
    {
        "numeric": lambda args: ("num", [0.0] * _r_count(args)),
        "double": lambda args: ("num", [0.0] * _r_count(args)),
        "integer": lambda args: ("num", [0.0] * _r_count(args)),
        "logical": lambda args: ("num", [0.0] * _r_count(args)),
        "character": lambda args: ("chr", [""] * _r_count(args)),
        "seq_len": lambda args: ("num", [float(k) for k in range(1, _r_count(args) + 1)]),
        "seq_along": lambda args: (
            ("num", [float(k) for k in range(1, _r_len(args[0]) + 1)])
            if len(args) == 1
            else _r_bad()
        ),
        "rep": _r_rep,
        "round": _r_round,
        "as.numeric": lambda args: (
            ("num", _r_nums(args[0], "cannot coerce")) if len(args) == 1 else _r_bad()
        ),
        "as.double": lambda args: (
            ("num", _r_nums(args[0], "cannot coerce")) if len(args) == 1 else _r_bad()
        ),
        "as.character": lambda args: (
            ("chr", [None] * _r_len(args[0])) if len(args) == 1 else _r_bad()
        ),
    }
)


class _REval:
    """Evaluates a parsed expression with R's vector semantics."""

    def __init__(self, env: dict[str, Any]) -> None:
        self.env = env

    def ev(self, node: Any) -> Any:
        return getattr(self, "e_" + node[0])(node)

    def e_num(self, node: Any) -> Any:
        return _r_num_literal(*node[1])

    def e_str(self, node: Any) -> Any:
        return ("chr", [node[1]])

    def e_const(self, node: Any) -> Any:
        return _R_CONST_VALUES[node[1]]

    def e_sym(self, node: Any) -> Any:
        name = node[1]
        if name in self.env:
            return self.env[name]
        if name in ("T", "F"):
            return ("num", [1.0 if name == "T" else 0.0])
        if name == "pi":
            return ("num", [math.pi])
        raise _REvalError(f"object '{name}' not found")

    def e_paren(self, node: Any) -> Any:
        return self.ev(node[1])

    def e_brace(self, node: Any) -> Any:
        out: Any = ("null",)
        for e in node[1]:
            out = self.ev(e)
        return out

    def e_unop(self, node: Any) -> Any:
        op, v = node[1], self.ev(node[2])
        if op == "?":
            raise _REvalError("help is not available")
        vals = _r_nums(v, "invalid argument to unary operator")
        if op == "-":
            return ("num", [-a for a in vals])
        if op == "+":
            return ("num", vals)
        if any(isinstance(a, complex) for a in vals):
            raise _REvalError("invalid argument type")
        return ("num", [a if _r_isna(a) else float(not a) for a in vals])

    def e_binop(self, node: Any) -> Any:
        op = node[1]
        if op in ("&&", "||"):
            left = _r_truth(self.ev(node[2]), op)
            if (op == "&&" and not left) or (op == "||" and left):
                return ("num", [float(left)])
            return ("num", [float(_r_truth(self.ev(node[3]), op))])
        a, b = self.ev(node[2]), self.ev(node[3])
        if op == ":":
            lo, hi = _r_nums(a)[:1], _r_nums(b)[:1]
            if not lo or not hi or _r_isna(lo[0]) or _r_isna(hi[0]):
                raise _REvalError("NA/NaN argument")
            count = int(abs(hi[0] - lo[0]) + 1e-10) + 1
            if count > 10**7:
                raise _REvalError("result would be too long a vector")
            step = 1.0 if hi[0] >= lo[0] else -1.0
            return ("num", [lo[0] + step * k for k in range(count)])
        if op in _R_COMPARE:
            if a[0] not in ("num", "chr", "null") or b[0] not in ("num", "chr", "null"):
                raise _REvalError("comparison is possible only for atomic types")
            n = 0 if not _r_len(a) or not _r_len(b) else max(_r_len(a), _r_len(b))
            if a[0] == "chr" or b[0] == "chr":
                return ("num", [_NA] * n)
            x, y = _r_recycle(_r_nums(a), _r_nums(b))
            cmp = {
                "==": lambda p, q: p == q,
                "!=": lambda p, q: p != q,
                "<": lambda p, q: p < q,
                ">": lambda p, q: p > q,
                "<=": lambda p, q: p <= q,
                ">=": lambda p, q: p >= q,
            }[op]
            return (
                "num",
                [
                    _NA if _r_isna(p) or _r_isna(q) else float(cmp(p, q))
                    for p, q in zip(x, y, strict=True)
                ],
            )
        x, y = _r_recycle(_r_nums(a), _r_nums(b))
        if op in ("&", "|"):
            out = []
            for p, q in zip(x, y, strict=True):
                pv = None if _r_isna(p) else bool(p)
                qv = None if _r_isna(q) else bool(q)
                if op == "&":
                    r = (
                        False
                        if pv is False or qv is False
                        else (None if pv is None or qv is None else True)
                    )
                else:
                    r = True if pv or qv else (None if pv is None or qv is None else False)
                out.append(_NA if r is None else float(r))
            return ("num", out)
        if op.startswith("%") and op not in ("%%", "%/%"):
            raise _REvalError(f'could not find function "{op}"')
        return ("num", [_r_arith(op, p, q) for p, q in zip(x, y, strict=True)])

    def e_formula(self, node: Any) -> Any:
        return ("obj", 3 if node[1] is not None else 2)

    def e_function(self, node: Any) -> Any:
        return ("closure", node[1], node[2], self.env)

    def e_assign(self, node: Any) -> Any:
        op, target, value = node[1], node[2], node[3]
        if op == ":=":
            raise _REvalError('could not find function ":="')
        if target[0] not in ("sym", "str"):
            raise _REvalError("invalid assignment target")
        v = self.ev(value)
        self.env[target[1]] = v
        return v

    def e_if(self, node: Any) -> Any:
        if _r_truth(self.ev(node[1]), "if"):
            return self.ev(node[2])
        return self.ev(node[3]) if node[3] is not None else ("null",)

    def e_for(self, _node: Any) -> Any:
        raise _REvalError("loops are not evaluated")

    e_while = e_repeat = e_break = e_next = e_for

    def e_ns(self, _node: Any) -> Any:
        raise _REvalError("namespace objects are not evaluated")

    def e_dollar(self, _node: Any) -> Any:
        raise _REvalError("$ operator is invalid for atomic vectors")

    def e_index(self, node: Any) -> Any:
        v = self.ev(node[1])
        if v[0] not in ("num", "chr"):
            raise _REvalError("object is not subsettable")
        vals = v[1]
        args = node[2]
        if len(args) > 1:
            raise _REvalError("incorrect number of dimensions")
        if not args or args[0][1] is None:
            if node[3]:
                raise _REvalError("invalid subscript")
            return v
        idx = self.ev(args[0][1])
        if idx[0] != "num":
            raise _REvalError("invalid subscript type")
        iv = idx[1]
        if node[3]:
            if len(iv) != 1 or _r_isna(iv[0]) or not 1 <= int(iv[0]) <= len(vals):
                raise _REvalError("subscript out of bounds")
            return (v[0], [vals[int(iv[0]) - 1]])
        na = _NA if v[0] == "num" else None
        pos = [int(a) for a in iv if not _r_isna(a) and int(a) != 0]
        if pos and all(p < 0 for p in pos):
            drop = {-p for p in pos}
            return (v[0], [a for k, a in enumerate(vals, 1) if k not in drop])
        if any(p < 0 for p in pos):
            raise _REvalError("can't mix positive and negative subscripts")
        return (v[0], [vals[p - 1] if p <= len(vals) else na for p in pos])

    def e_call(self, node: Any) -> Any:
        fn_node, args = node[1], node[2]
        if any(a[1] is None for a in args):
            raise _REvalError("argument is missing")
        if fn_node[0] in ("sym", "str") and fn_node[1] not in self.env:
            builtin = _R_BUILTINS.get(fn_node[1])
            if builtin is None or any(a[0] is not None for a in args):
                raise _REvalError(f'could not find function "{fn_node[1]}"')
            return builtin([self.ev(a[1]) for a in args])
        fn = self.ev(fn_node)
        if fn[0] != "closure":
            raise _REvalError("attempt to apply non-function")
        _, formals, body, env = fn
        local = dict(env)
        names = [f[0] for f in formals]
        assigned: set[str] = set()
        for name, value in args:
            if name is not None:
                if name not in names:
                    raise _REvalError(f"unused argument ({name})")
                local[name] = self.ev(value)
                assigned.add(name)
        remaining = [n for n in names if n not in assigned and n != "..."]
        positional = [value for name, value in args if name is None]
        if len(positional) > len(remaining) and "..." not in names:
            raise _REvalError("unused argument")
        for name, value in zip(remaining, positional, strict=False):
            local[name] = self.ev(value)
            assigned.add(name)
        for name, default in formals:
            if name in assigned:
                continue
            if default is not None:
                local[name] = _REval(local).ev(default)
            else:
                local.pop(name, None)  # a missing argument: an error once used
        return _REval(local).ev(body)


def _r_function(expr_txt: str) -> Callable[[Any], Any] | None:
    """``f <- function(x) NULL; body(f) <- parse(text = expr_txt)[[1]]``.

    ``None`` when R cannot parse the text or it holds no expression. The
    returned function evaluates the first expression with ``x`` bound to the
    given values and returns its value as a list (``None`` for ``NULL``); an R
    error raises :class:`_REvalError`. A value that is not a vector (a
    function, a formula, a list) comes back as a list of its R length.
    """
    try:
        exprs = _r_parse(expr_txt)
    except (_RParseError, RecursionError):
        return None
    if not exprs:
        return None
    body = exprs[0]

    def fn(x: Any) -> Any:
        xs = [float(v) for v in x] if isinstance(x, list | tuple) else [float(x)]
        try:
            value = _REval({"x": ("num", xs)}).ev(body)
        except RecursionError as e:
            raise _REvalError("evaluation nested too deeply") from e
        if value[0] == "null":
            return None
        if value[0] in ("num", "chr"):
            return list(value[1])
        return [None] * _r_len(value)

    return fn


def _spvviz_function_guide(node: Any) -> dict[str, Any] | None:
    """Port of R/spv.R::.spvviz_function_guide(): an SPSS-fitted trend line."""
    expr_txt = _xml_attr(node, "value")
    if expr_txt is None or expr_txt == "":
        return None
    fn = _r_function(expr_txt)
    if fn is None:
        return None
    return {"name": _xml_attr(node, "name"), "expr": expr_txt, "fn": fn}


def _na_or(a: str | None, b: str | None) -> str | None:
    """R's ``%NA%``: ``b`` when ``a`` is ``NA`` or empty."""
    return b if a is None or a == "" else a


def _spvviz_axis_label(
    root: Any, axis_id_suffix: str, var_id: str | None, var_source_name: str | None
) -> str | None:
    """Port of R/spv.R::.spvviz_axis_label()."""
    ax = _find_first(
        root, f".//*[local-name()='axis' and contains(@id, {_sh_quote(axis_id_suffix)})]"
    )
    from_axis = None
    if ax is not None:
        lbl = _find_first(ax, ".//*[local-name()='label']//*[local-name()='text']")
        from_axis = _xml_text(lbl)
    var_node = _find_by_id(root, var_id)
    from_var = None
    if var_node is not None:
        from_var = _na_or(_xml_attr(var_node, "label"), _xml_attr(var_node, "shortLabel"))
    return _na_or(_na_or(from_axis, from_var), var_source_name)


def _spvviz_chart_title(root: Any, fallback: str | None) -> str | None:
    """Port of R/spv.R::.spvviz_chart_title()."""
    node = _find_first(root, ".//*[local-name()='labelFrame']//*[local-name()='text']")
    return _xml_text(node) if node is not None else fallback


def _relabel_map(relabels: list[Any]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for r in relabels:
        frm = _xml_attr(r, "from")
        if frm is not None and frm != "" and frm not in out:  # R: x[""] never matches
            out[frm] = _xml_attr(r, "to")
    return out


def _spvviz_decode_boxplot_databin(
    root: Any,
    x_ref: str | None,
    y_ref: str | None,
    x_node: Any,
    y_node: Any,  # noqa: ARG001 - kept for R's signature
    data: list[dict[str, Any]],
) -> pd.DataFrame | None:
    """Port of R/spv.R::.spvviz_decode_boxplot_databin().

    The box plot's category (relabelled code) and value per case; cases with
    a missing category or value (SPSS's system-missing ``-DBL_MAX``) are
    dropped. R's ``is_na_like()`` uses the vectorised ``&``, so ``abs()`` of
    the character category column always errors and a box plot backed by
    case data never decodes (UPSTREAM_ISSUES U144).
    """
    import pandas as pd

    x_var = _spvviz_resolve_variable(root, x_ref, data)
    y_var = _spvviz_resolve_variable(root, y_ref, data)
    if x_var is None or y_var is None:
        return None
    if len(x_var["values"]) != len(y_var["values"]):
        return None
    code_to_label = _relabel_map(_find_all(x_node, ".//*[local-name()='relabel']"))

    def code(v: dict[str, Any]) -> str | None:
        d = v.get("d")
        if d is None or (isinstance(d, float) and math.isnan(d)):
            return None
        d = float(d)
        return str(int(d)) if d.is_integer() and abs(d) < 1e15 else format(d, ".15g")

    cats = [code(v) for v in x_var["values"]]
    category = [code_to_label.get(c, c) if c is not None else None for c in cats]
    category = [lbl if lbl is not None else c for lbl, c in zip(category, cats, strict=True)]
    value = [math.nan if v.get("d") is None else float(v["d"]) for v in y_var["values"]]
    big = sys.float_info.max
    keep = [
        c is not None and not math.isnan(v) and abs(v) < big
        for c, v in zip(category, value, strict=True)
    ]
    if not any(keep):
        return None
    return pd.DataFrame(
        {
            "category": pd.array(
                [c for c, k in zip(category, keep, strict=True) if k], dtype="string"
            ),
            "value": pd.array([v for v, k in zip(value, keep, strict=True) if k], dtype="float64"),
        }
    )


def _spvviz_decode_boxplot_source(root: Any, source_id: str | None) -> pd.DataFrame | None:
    """Port of R/spv.R::.spvviz_decode_boxplot_source() (inline embeddedSource)."""
    import pandas as pd

    es = _find_first(root, f".//*[local-name()='embeddedSource' and @id={_sh_quote(source_id)}]")
    if es is None:
        return None
    names_txt = _xml_text(_find_first(es, ".//*[local-name()='names']"))
    if names_txt is None:
        return None
    col_names = strsplit(names_txt, ";", fixed=True)
    if set(col_names) != {"Category", "Label", "Tooltips", "Value"}:
        return None
    rows = _find_all(es, ".//*[local-name()='row']")
    if not rows:
        return None
    cells = [strsplit(_xml_text(r) or "", ";", fixed=True) for r in rows]
    if any(len(c) != len(col_names) for c in cells):
        return None
    ci = col_names.index("Category")
    vi = col_names.index("Value")
    cat_var = _find_first(root, ".//*[local-name()='sourceVariable' and @sourceName='Category']")
    code_to_label = _relabel_map(_find_all(cat_var, ".//*[local-name()='relabel']"))
    raw_cat = [c[ci] for c in cells]
    category = [code_to_label.get(c) for c in raw_cat]
    category = [c if c is not None else raw_cat[i] for i, c in enumerate(category)]
    value = [_as_numeric(c[vi].replace(",", ".")) for c in cells]
    return pd.DataFrame(
        {
            "category": pd.array(category, dtype="string"),
            "value": pd.array([math.nan if v is None else v for v in value], dtype="float64"),
        }
    )


def _spvviz_decode_xy(mark_node: Any, root: Any, data: list[dict[str, Any]]) -> pd.DataFrame | None:
    """Port of R/spv.R::.spvviz_decode_xy() (``<point>``/``<interval>`` marks)."""
    import pandas as pd

    x_ref = _xml_attr(_find_first(mark_node, "./*[local-name()='x']"), "variable")
    y_ref = _xml_attr(_find_first(mark_node, "./*[local-name()='y']"), "variable")
    if x_ref is None or y_ref is None:
        return None
    x_var = _spvviz_resolve_variable(root, x_ref, data)
    y_var = _spvviz_resolve_variable(root, y_ref, data)
    if x_var is None or y_var is None:
        return None
    if len(x_var["values"]) != len(y_var["values"]):
        return None
    xs = [math.nan if v["d"] is None else v["d"] for v in x_var["values"]]
    ys = [math.nan if v["d"] is None else v["d"] for v in y_var["values"]]

    def na_like(v: float) -> bool:
        return math.isnan(v) or abs(v) >= _DBL_MAX

    keep = [not na_like(x) and not na_like(y) for x, y in zip(xs, ys, strict=True)]
    if not any(keep):
        return None
    df = pd.DataFrame(
        {
            "x": pd.array([x for x, k in zip(xs, keep, strict=True) if k], dtype="float64"),
            "y": pd.array([y for y, k in zip(ys, keep, strict=True) if k], dtype="float64"),
        }
    )
    df.attrs["spv_chart_xlab"] = _spvviz_axis_label(root, "axisx", x_ref, x_var["source_name"])
    df.attrs["spv_chart_ylab"] = _spvviz_axis_label(root, "axisy", y_ref, y_var["source_name"])
    return df


def _spv_decode_chart(
    xml_raw: bytes | str, data: list[dict[str, Any]], title: str | None = None
) -> pd.DataFrame | None:
    """Port of R/spv.R::.spv_decode_chart(): a ``<point>``, ``<interval>`` or ``<schema>`` chart."""
    try:
        root = _xml_parse(xml_raw)
        point = _find_first(root, ".//*[local-name()='point']")
        interval = _find_first(root, ".//*[local-name()='interval']")
        schema = _find_first(root, ".//*[local-name()='schema']")

        if point is not None:
            df = _spvviz_decode_xy(point, root, data)
            if df is None:
                raise ValueError("spv chart: could not resolve <point> x/y series")
            fits = [
                _spvviz_function_guide(n)
                for n in _find_all(root, ".//*[local-name()='functionGuide']")
            ]
            df.attrs["spv_chart_fits"] = [f for f in fits if f is not None]
            df.attrs["spv_chart_type"] = "point"
        elif interval is not None:
            df = _spvviz_decode_xy(interval, root, data)
            if df is None:
                raise ValueError("spv chart: could not resolve <interval> x/y series")
            df.attrs["spv_chart_type"] = "interval"
        elif schema is not None:
            x_ref = _xml_attr(_find_first(schema, "./*[local-name()='x']"), "variable")
            y_ref = _xml_attr(_find_first(schema, "./*[local-name()='y']"), "variable")
            if x_ref is None or y_ref is None:
                raise ValueError("spv chart: <schema> missing x/y variable reference")
            x_node = _find_by_id(root, x_ref)
            y_node = _find_by_id(root, y_ref)
            if x_node is None or y_node is None:
                raise ValueError("spv chart: <schema> variable id not found")
            source_id = _xml_attr(x_node, "source")
            df = _spvviz_decode_boxplot_source(root, source_id)
            if df is None:
                df = _spvviz_decode_boxplot_databin(root, x_ref, y_ref, x_node, y_node, data)
            if df is None:
                raise ValueError("spv chart: <schema> data not in a supported shape")
            df.attrs["spv_chart_xlab"] = _spvviz_axis_label(
                root, "axisx", x_ref, _xml_attr(x_node, "sourceName")
            )
            df.attrs["spv_chart_ylab"] = _spvviz_axis_label(
                root, "axisy", y_ref, _xml_attr(y_node, "sourceName")
            )
            df.attrs["spv_chart_type"] = "boxplot"
        else:
            raise ValueError(
                "spv chart: no <point>, <interval>, or <schema> mark (out of this port's scope)"
            )
        df.attrs["spv_chart_title"] = _spvviz_chart_title(root, title)
        return df
    except Exception as e:
        warnings.warn(f"spv: could not decode chart: {e}", stacklevel=2)
        return None


# ===========================================================================
# Modern light-binary table decoder (PSPP light-binary.grammar)
# ===========================================================================


def _spvlb_read_value_mod(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_value_mod(): footnote refs kept, styling skipped."""
    matched, c31 = _spvbin_match_bytes(cur, 0x31)
    if not matched:
        cur = _spvbin_expect_bytes(cur, 0x58, "ValueMod absent marker")
        return {"footnote_refs": []}, cur
    cur = c31
    n_refs, cur = _spvbin_read_int32(cur)
    refs: list[int] = []
    for _ in range(n_refs):
        r, cur = _spvbin_read_int16(cur)
        refs.append(r)
    n_sub, cur = _spvbin_read_int32(cur)
    for _ in range(n_sub):
        _, cur = _spvbin_read_string(cur)
    if cur.version == 1:
        cur = _spvbin_expect_bytes(cur, 0x00, "ValueMod v1 marker")
        tag, cur = _spvbin_read_int32(cur)
        if tag not in (1, 2):
            raise ValueError(f"spv binary: bad ValueMod v1 tag {tag}")
        cur = _spvbin_match_bytes(cur, 0x00)[1]
        cur = _spvbin_match_bytes(cur, 0x00)[1]
        _, cur = _spvbin_read_int32(cur)
        cur = _spvbin_match_bytes(cur, 0x00)[1]
        cur = _spvbin_match_bytes(cur, 0x00)[1]
    else:
        inner, after = _spvbin_read_count(cur)
        inner = _spvlb_skip_template_string(inner)
        inner = _spvlb_skip_style_pair(inner)
        cur = after
    return {"footnote_refs": refs}, cur


def _spvlb_skip_template_string(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_template_string()."""
    inner, after = _spvbin_read_count(cur)
    if inner.pos < inner.limit:
        in2, after2 = _spvbin_read_count(inner)
        if in2.pos < in2.limit:
            _, in2 = _spvbin_read_int32(in2)
            matched, c = _spvbin_match_bytes(in2, 0x31)
            if matched:
                in2 = _spvbin_expect_bytes(c, 0x55, "template string 55 marker")
            else:
                in2 = _spvbin_expect_bytes(in2, 0x58, "template string absent marker")
        inner = after2
        matched, c = _spvbin_match_bytes(inner, 0x31)
        if matched:
            inner = _spvbin_read_string(c)[1]
        else:
            inner = _spvbin_expect_bytes(inner, 0x58, "template id absent marker")
    return after


def _spvlb_skip_style_pair(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_style_pair()."""
    matched, c = _spvbin_match_bytes(cur, 0x31)
    cur = (
        _spvlb_skip_font_style(c)
        if matched
        else _spvbin_expect_bytes(cur, 0x58, "font style absent marker")
    )
    matched, c = _spvbin_match_bytes(cur, 0x31)
    return (
        _spvlb_skip_cell_style(c)
        if matched
        else _spvbin_expect_bytes(cur, 0x58, "cell style absent marker")
    )


def _spvlb_skip_font_style(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_font_style()."""
    for _ in range(4):
        cur = _spvbin_read_bool(cur)[1]
    for _ in range(3):
        cur = _spvbin_read_string(cur)[1]
    return _spvbin_read_byte(cur)[1]


def _spvlb_skip_cell_style(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_cell_style()."""
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_double(cur)[1]
    for _ in range(4):
        cur = _spvbin_read_int16(cur)[1]
    return cur


def _spvlb_read_argument(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_argument()."""
    tag, cur = _spvbin_read_int32(cur)
    if tag == 0:
        v, cur = _spvlb_read_value(cur)
        return {"values": [v]}, cur
    _, cur = _spvbin_read_int32(cur)
    vals = []
    for _i in range(tag):
        v, cur = _spvlb_read_value(cur)
        vals.append(v)
    return {"values": vals}, cur


def _spvlb_read_value(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_value(): one pivot-table Value (6 variants + template)."""
    for _ in range(4):
        cur = _spvbin_match_bytes(cur, 0x00)[1]
    vtype = -1
    for t in range(1, 7):
        matched, c = _spvbin_match_bytes(cur, t)
        if matched:
            vtype, cur = t, c
            break

    if vtype == 1:
        vm, cur = _spvlb_read_value_mod(cur)
        fmt, cur = _spvbin_read_int32(cur)
        x, cur = _spvbin_read_double(cur)
        out: dict[str, Any] = {
            "type": "numeric",
            "x": x,
            "format": fmt,
            "footnote_refs": vm["footnote_refs"],
        }
    elif vtype == 2:
        vm, cur = _spvlb_read_value_mod(cur)
        fmt, cur = _spvbin_read_int32(cur)
        x, cur = _spvbin_read_double(cur)
        var_name, cur = _spvbin_read_string(cur)
        value_label, cur = _spvbin_read_string(cur)
        _, cur = _spvbin_read_byte(cur)
        out = {
            "type": "numeric",
            "x": x,
            "format": fmt,
            "var_name": var_name,
            "value_label": value_label,
            "footnote_refs": vm["footnote_refs"],
        }
    elif vtype == 3:
        _, cur = _spvbin_read_string(cur)
        vm, cur = _spvlb_read_value_mod(cur)
        id_, cur = _spvbin_read_string(cur)
        c_, cur = _spvbin_read_string(cur)
        _, cur = _spvbin_read_bool(cur)
        out = {"type": "text", "s": c_, "id": id_, "footnote_refs": vm["footnote_refs"]}
    elif vtype == 4:
        vm, cur = _spvlb_read_value_mod(cur)
        fmt, cur = _spvbin_read_int32(cur)
        value_label, cur = _spvbin_read_string(cur)
        var_name, cur = _spvbin_read_string(cur)
        _, cur = _spvbin_read_byte(cur)
        s, cur = _spvbin_read_string(cur)
        out = {
            "type": "string",
            "s": s,
            "format": fmt,
            "var_name": var_name,
            "value_label": value_label,
            "footnote_refs": vm["footnote_refs"],
        }
    elif vtype == 5:
        vm, cur = _spvlb_read_value_mod(cur)
        var_name, cur = _spvbin_read_string(cur)
        var_label, cur = _spvbin_read_string(cur)
        _, cur = _spvbin_read_byte(cur)
        out = {
            "type": "variable",
            "s": var_name,
            "var_label": var_label,
            "footnote_refs": vm["footnote_refs"],
        }
    elif vtype == 6:
        _, cur = _spvbin_read_string(cur)
        vm, cur = _spvlb_read_value_mod(cur)
        id_, cur = _spvbin_read_string(cur)
        c_, cur = _spvbin_read_string(cur)
        out = {"type": "text", "s": c_, "id": id_, "footnote_refs": vm["footnote_refs"]}
    else:
        vm, cur = _spvlb_read_value_mod(cur)
        template, cur = _spvbin_read_string(cur)
        n_args, cur = _spvbin_read_int32(cur)
        args = []
        for _i in range(n_args):
            a, cur = _spvlb_read_argument(cur)
            args.append(a)
        out = {
            "type": "template",
            "s": template,
            "args": args,
            "footnote_refs": vm["footnote_refs"],
        }
    return out, cur


def _spvlb_read_leaf(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_leaf()."""
    for _ in range(3):
        cur = _spvbin_expect_bytes(cur, 0x00, "Leaf padding")
    _, cur = _spvbin_read_int32(cur)
    idx, cur = _spvbin_read_int32(cur)
    _, cur = _spvbin_read_int32(cur)
    return {"leaf_index": idx}, cur


def _spvlb_read_group(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_group()."""
    merge, cur = _spvbin_read_bool(cur)
    cur = _spvbin_expect_bytes(cur, 0x00, "Group padding")
    cur = _spvbin_expect_bytes(cur, 0x01, "Group padding")
    _, cur = _spvbin_read_int32(cur)
    _, cur = _spvbin_read_int32(cur)
    n_sub, cur = _spvbin_read_int32(cur)
    subs = []
    for _ in range(n_sub):
        c_, cur = _spvlb_read_category(cur)
        subs.append(c_)
    return {"merge": merge, "subcategories": subs}, cur


def _spvlb_read_category(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_category(): a Leaf or a Group."""
    name, cur = _spvlb_read_value(cur)
    probe, _ = _spvbin_match_bytes(cur, b"\x00\x00\x00")
    if probe:
        leaf, cur = _spvlb_read_leaf(cur)
        return {"name": name, "is_leaf": True, "leaf_index": leaf["leaf_index"]}, cur
    grp, cur = _spvlb_read_group(cur)
    return {"name": name, "is_leaf": False, "subcategories": grp["subcategories"]}, cur


def _spvlb_read_dim_properties(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_dim_properties()."""
    cur = _spvbin_read_byte(cur)[1]
    cur = _spvbin_read_byte(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    hide_dim_label, cur = _spvbin_read_bool(cur)
    hide_all_labels, cur = _spvbin_read_bool(cur)
    cur = _spvbin_expect_bytes(cur, 0x01, "DimProperties marker")
    cur = _spvbin_read_int32(cur)[1]
    return {"hide_dim_label": hide_dim_label, "hide_all_labels": hide_all_labels}, cur


def _leaf_key(v: Any) -> str | None:
    """``as.character()`` of an index as R holds it (int32 integer, else double)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, int) and abs(v) <= 2147483647:
        return str(v)
    return as_character(float(v))


def _spvlb_read_dimension(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_dimension(): leaves indexed by leaf_index."""
    name, cur = _spvlb_read_value(cur)
    _, cur = _spvlb_read_dim_properties(cur)
    n_cat, cur = _spvbin_read_int32(cur)
    cats = []
    for _ in range(n_cat):
        c_, cur = _spvlb_read_category(cur)
        cats.append(c_)

    leaves: list[tuple[Any, list[str | None]]] = []

    def walk(cat: dict[str, Any], path: list[str | None]) -> None:
        here = [*path, _spvlb_value_text(cat["name"])]
        if cat.get("is_leaf"):
            leaves.append((cat["leaf_index"], here))
        else:
            for sub_ in cat["subcategories"]:
                walk(sub_, here)

    for c_ in cats:
        walk(c_, [])
    by_index: dict[str | None, list[str | None]] = {}
    for idx, path in leaves:
        by_index.setdefault(_leaf_key(idx), path)
    return {
        "name": _spvlb_value_text(name),
        "leaves": by_index,
        "n_leaves": len(leaves),
    }, cur


def _spvlb_value_text(v: dict[str, Any] | None) -> str | None:
    """Port of R/spv.R::.spvlb_value_text(): a Value as plain text (``^N`` filled)."""
    if v is None:
        return None
    t = v.get("type")
    if t == "numeric":
        x = v["x"]
        if math.isnan(x) or abs(x) >= _DBL_MAX:
            return ""
        return _stat_num_to_chr(x)
    if t in ("string", "variable", "text"):
        s = v.get("s")
        return "" if s is None else s
    if t == "template":
        txt = v.get("s") or ""
        for i, arg in enumerate(v.get("args") or [], 1):
            vals = arg.get("values") or []
            rendered = ", ".join(_na_str(_spvlb_value_text(x)) for x in vals)
            txt = gsub(rf"\^{i}\b", rendered, txt)
        return txt
    return ""


def _spvlb_read_header(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_header()."""
    cur = _spvbin_expect_bytes(cur, 0x01, "Header marker")
    cur = _spvbin_expect_bytes(cur, 0x00, "Header marker")
    version, cur = _spvbin_read_int32(cur)
    for _ in range(5):
        cur = _spvbin_read_bool(cur)[1]
    for _ in range(5):
        cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_int64(cur)[1]
    return {"version": version}, cur


def _spvlb_read_titles(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_titles()."""
    title, cur = _spvlb_read_value(cur)
    cur = _spvbin_match_bytes(cur, 0x01)[1]
    subtype, cur = _spvlb_read_value(cur)
    cur = _spvbin_match_bytes(cur, 0x01)[1]
    cur = _spvbin_expect_bytes(cur, 0x31, "Titles marker")
    user_title, cur = _spvlb_read_value(cur)
    cur = _spvbin_match_bytes(cur, 0x01)[1]
    matched, c = _spvbin_match_bytes(cur, 0x31)
    corner_text = None
    if matched:
        corner_text, cur = _spvlb_read_value(c)
    else:
        cur = _spvbin_expect_bytes(cur, 0x58, "corner-text absent marker")
    matched, c = _spvbin_match_bytes(cur, 0x31)
    caption = None
    if matched:
        caption, cur = _spvlb_read_value(c)
    else:
        cur = _spvbin_expect_bytes(cur, 0x58, "caption absent marker")
    return {
        "title": title,
        "subtype": subtype,
        "user_title": user_title,
        "corner_text": corner_text,
        "caption": caption,
    }, cur


def _spvlb_read_footnotes(cur: _Cursor) -> tuple[list[dict[str, Any]], _Cursor]:
    """Port of R/spv.R::.spvlb_read_footnotes()."""
    n, cur = _spvbin_read_int32(cur)
    out = []
    for _ in range(n):
        text, cur = _spvlb_read_value(cur)
        matched, c = _spvbin_match_bytes(cur, 0x31)
        marker = None
        if matched:
            marker, cur = _spvlb_read_value(c)
        else:
            cur = _spvbin_expect_bytes(cur, 0x58, "footnote marker absent")
        _, cur = _spvbin_read_int32(cur)
        out.append(
            {
                "text": _spvlb_value_text(text),
                "marker": _spvlb_value_text(marker) if marker is not None else None,
            }
        )
    return out, cur


def _spvlb_skip_area(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_area() (styling, consumed for alignment)."""
    cur = _spvbin_read_byte(cur)[1]
    cur = _spvbin_expect_bytes(cur, 0x31, "Area marker")
    cur = _spvbin_read_string(cur)[1]
    cur = _spvbin_read_float(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_bool(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_string(cur)[1]
    cur = _spvbin_read_string(cur)[1]
    cur = _spvbin_read_bool(cur)[1]
    cur = _spvbin_read_string(cur)[1]
    cur = _spvbin_read_string(cur)[1]
    if cur.version == 3:
        for _ in range(4):
            cur = _spvbin_read_int32(cur)[1]
    return cur


def _spvlb_skip_areas(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_areas()."""
    cur = _spvbin_match_bytes(cur, 0x00)[1]
    for _ in range(8):
        cur = _spvlb_skip_area(cur)
    return cur


def _spvlb_skip_borders(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_borders()."""
    return _spvbin_read_count(cur)[1]


def _spvlb_skip_print_settings(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_print_settings()."""
    return _spvbin_read_count(cur)[1]


def _spvlb_skip_table_settings(cur: _Cursor) -> _Cursor:
    """Port of R/spv.R::.spvlb_skip_table_settings()."""
    return _spvbin_read_count(cur)[1]


def _spvlb_read_formats(cur: _Cursor) -> tuple[dict[str, Any], _Cursor]:
    """Port of R/spv.R::.spvlb_read_formats() (only the locale is kept)."""
    n_widths, cur = _spvbin_read_int32(cur)
    for _ in range(n_widths):
        cur = _spvbin_read_int32(cur)[1]
    locale, cur = _spvbin_read_string(cur)
    cur = _spvbin_read_int32(cur)[1]
    for _ in range(3):
        cur = _spvbin_read_bool(cur)[1]
    cur = _spvbin_read_int32(cur)[1]
    cur = _spvbin_read_byte(cur)[1]
    cur = _spvbin_read_byte(cur)[1]
    n_cc, cur = _spvbin_read_int32(cur)
    for _ in range(n_cc):
        cur = _spvbin_read_string(cur)[1]
    _, after = _spvbin_read_count(cur)
    return {"locale": locale}, after


def _spvlb_read_axes(cur: _Cursor) -> tuple[dict[str, list[int]], _Cursor]:
    """Port of R/spv.R::.spvlb_read_axes()."""
    n_layers, cur = _spvbin_read_int32(cur)
    n_rows, cur = _spvbin_read_int32(cur)
    n_cols, cur = _spvbin_read_int32(cur)

    def read_n(cur: _Cursor, n: int) -> tuple[list[int], _Cursor]:
        out = []
        for _ in range(n):
            v, cur = _spvbin_read_int32(cur)
            out.append(v)
        return out, cur

    layers, cur = read_n(cur, n_layers)
    rows, cur = read_n(cur, n_rows)
    cols, cur = read_n(cur, n_cols)
    return {"layers": layers, "rows": rows, "columns": cols}, cur


def _spvlb_read_cells(cur: _Cursor) -> tuple[list[dict[str, Any]], _Cursor]:
    """Port of R/spv.R::.spvlb_read_cells()."""
    n, cur = _spvbin_read_int32(cur)
    out = []
    for _ in range(n):
        idx, cur = _spvbin_read_int64(cur)
        if cur.version == 1:
            cur = _spvbin_match_bytes(cur, 0x00)[1]
        v, cur = _spvlb_read_value(cur)
        out.append({"index": idx, "value": v})
    return out, cur


def _spv_decode_light_table(raw: bytes) -> pd.DataFrame | None:
    """Port of R/spv.R::.spv_decode_light_table().

    Decodes one ``<n>_lightTableData.bin`` member into a tidy table (one row per
    cell, one column per dimension plus ``value``), or ``None`` (with a
    warning) when the member cannot be decoded.
    """
    try:
        cur = _spvbin_cursor(raw)
        header, cur = _spvlb_read_header(cur)
        version = header["version"]
        if version not in (1, 3):
            raise ValueError(f"spv binary: unsupported light-table version {version}")
        cur = _Cursor(cur.raw, cur.pos, cur.limit, version)

        titles, cur = _spvlb_read_titles(cur)
        footnotes, cur = _spvlb_read_footnotes(cur)
        cur = _spvlb_skip_areas(cur)
        cur = _spvlb_skip_borders(cur)
        cur = _spvlb_skip_print_settings(cur)
        cur = _spvlb_skip_table_settings(cur)
        _, cur = _spvlb_read_formats(cur)

        n_dims, cur = _spvbin_read_int32(cur)
        dims = []
        for _ in range(n_dims):
            d, cur = _spvlb_read_dimension(cur)
            dims.append(d)

        axes, cur = _spvlb_read_axes(cur)
        cells, cur = _spvlb_read_cells(cur)
        return spv_assemble_table(
            dims=dims,
            axes=axes,
            cells=cells,
            title=_spvlb_value_text(titles["user_title"]),
            footnotes=footnotes,
        )
    except Exception as e:
        warnings.warn(f"spv: could not decode light-binary table: {e}", stacklevel=2)
        return None


def _r_mod(a: float, b: int) -> float:
    if math.isnan(a):
        return math.nan
    return a % b


def _r_intdiv(a: float, b: int) -> float:
    if math.isnan(a):
        return math.nan
    return float(a // b)


def spv_assemble_table(
    dims: list[dict[str, Any]],
    axes: dict[str, list[int]],
    cells: list[dict[str, Any]],
    title: str | None = None,
    footnotes: list[dict[str, Any]] | None = None,
) -> pd.DataFrame | None:
    """Port of R/spv.R::spv_assemble_table().

    Unflattens each cell's mixed-radix ``index`` into per-dimension leaf labels
    and builds the tidy table (one row per cell, one column per dimension
    holding the cell's category label path joined by ``" / "``, plus a
    ``value`` column). SPSS's axis assignment is recorded in ``attrs``
    (``spv_row_dims``, ``spv_col_dims``, ``spv_layer_dims``) for pivoting.

    Args:
        dims: decoded dimensions, each ``{"name", "leaves", "n_leaves"}``
            (``leaves`` maps ``str(leaf_index)`` to the label path).
        axes: ``{"layers", "rows", "columns"}``: 0-based dimension positions.
        cells: ``{"index", "value"}`` per cell; ``value`` a decoded Value.
        title: the table title (``attrs["spv_title"]``).
        footnotes: the table footnotes (``attrs["spv_footnotes"]``).

    Returns:
        The table, or ``None`` when there are no dimensions or no cells.
    """
    if footnotes is None:
        footnotes = []
    if not dims or not cells:
        return None
    n_leaves = [max(1, int(d.get("n_leaves") or 0)) for d in dims]

    def decode_index(flat: float) -> list[float]:
        out = [0.0] * len(dims)
        remainder = float(flat)
        for i in reversed(range(len(dims))):
            if n_leaves[i] > 0:
                out[i] = _r_mod(remainder, n_leaves[i])
                remainder = _r_intdiv(remainder, n_leaves[i])
        return out

    names = [
        "dim" if d.get("name") is None or d.get("name") == "" else str(d["name"]) for d in dims
    ]
    dim_names = _make_unique(names)

    columns: list[list[str | None]] = [[] for _ in range(len(dims) + 1)]
    for cell in cells:
        idx = decode_index(cell["index"])
        for j, d in enumerate(dims):
            # the leaf index as the leaves are keyed ("100000"; R's
            # as.character(1e5) is "1e+05", which finds no leaf; U148)
            key = _leaf_key(int(idx[j]) if math.isfinite(idx[j]) else idx[j])
            leaf = (d.get("leaves") or {}).get(key) if key is not None else None
            columns[j].append(" / ".join(_na_str(p) for p in leaf) if leaf is not None else None)
        v = cell["value"]
        if v.get("type") == "numeric":
            x = v["x"]
            val = None if (math.isnan(x) or abs(x) >= _DBL_MAX) else _stat_num_to_chr(x)
        elif v.get("type") == "template":
            # the rendered text ("3 of 5"), not the raw template ("^1 of ^2")
            # R puts in the cell (UPSTREAM_ISSUES U148)
            val = _spvlb_value_text(v)
        else:
            val = v.get("s")
        columns[-1].append(val)

    df = _string_frame(columns, [*dim_names, "value"])

    def pick(ix: list[int]) -> list[str | None]:
        # dim_names[ix + 1L]: index 0 (ix = -1) selects nothing, past the end is NA
        return [dim_names[i] if 0 <= i < len(dim_names) else None for i in ix if i != -1]

    df.attrs["spv_title"] = title
    df.attrs["spv_footnotes"] = footnotes
    df.attrs["spv_row_dims"] = pick(axes.get("rows", []))
    df.attrs["spv_col_dims"] = pick(axes.get("columns", []))
    df.attrs["spv_layer_dims"] = pick(axes.get("layers", []))
    return df


# ===========================================================================
# Structure-XML reader (dispatch across tables)
# ===========================================================================


def _spv_read_structure(dir_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Port of R/spv.R::.spv_read_structure().

    Reads every ``outputViewerNNNN[_heading].xml`` member of an unzipped
    archive in numeric order, threading the command name and SPSS syntax
    across documents. Returns one dict per table/chart item: ``command_name``,
    ``syntax``, ``subtype``, ``bin_member``, ``xml_member``, ``is_legacy``,
    ``is_graph``.
    """
    dir_path = str(dir_path)
    members = _list_files(dir_path)
    docs = [
        m for m in members if grepl(r"^outputViewer[0-9]+(_heading)?\.xml$", os.path.basename(m))
    ]
    ords = [_as_integer(gsub(r"\D", "", os.path.basename(d))) for d in docs]
    order = sorted(range(len(docs)), key=lambda i: (ords[i] is None, ords[i] or 0))
    docs = [docs[i] for i in order]

    out: list[dict[str, Any]] = []
    command_name: str | None = None
    syntax: str | None = None
    for rel in docs:
        try:
            root = _xml_parse_file(_file_path(dir_path, rel))
        except Exception:  # noqa: S112 - R skips an unparsable document
            continue
        walked = _spvsx_walk_heading(root, command_name, syntax)
        out.extend(walked["rows"])
        command_name = walked["command_name"]
        syntax = walked["syntax"]
    return out


def _spvsx_walk_heading(node: Any, command_name: str | None, syntax: str | None) -> dict[str, Any]:
    """Port of R/spv.R::.spvsx_walk_heading().

    A sub-heading, table or chart without a ``commandName`` attribute keeps
    the enclosing command name, and a log item without readable text keeps
    the syntax seen before it, as metacheck intends: its ``%||%`` fallbacks
    never apply to ``xml_attr()``'s ``NA``, so the command name was reset to
    ``NA``, and ``nzchar(trimws(NA))`` being ``TRUE`` reset the syntax
    (UPSTREAM_ISSUES U139).
    """
    out: list[dict[str, Any]] = []
    children = _find_all(node, "./*[local-name()='container' or local-name()='heading']")
    for child in children:
        tag = _localname(child)
        if tag == "heading":
            sub_cmd = _xml_attr(child, "commandName")
            sub_ = _spvsx_walk_heading(
                child, sub_cmd if sub_cmd is not None else command_name, syntax
            )
            out.extend(sub_["rows"])
            command_name = sub_["command_name"]
            syntax = sub_["syntax"]
            continue
        content = _find_first(child, "./*[local-name()!='label']")
        if content is None:
            continue
        ctag = _localname(content)
        if ctag == "text":
            if _xml_attr(content, "type") == "log":
                txt = _spvsx_html_text(content)
                if txt is not None and trimws(txt) != "":
                    syntax = txt
            continue
        if ctag == "table":
            ts = _find_first(content, "./*[local-name()='tableStructure']")
            dp = _find_first(ts, "./*[local-name()='dataPath']")
            p = _find_first(ts, "./*[local-name()='path']")
            bin_member = _xml_text(dp)
            xml_member = _xml_text(p)
            out.append(
                {
                    "command_name": _xml_attr(content, "commandName") or command_name,
                    "syntax": syntax,
                    "subtype": _xml_attr(content, "subType"),
                    "bin_member": bin_member,
                    "xml_member": xml_member,
                    "is_legacy": xml_member is not None and xml_member != "",
                    "is_graph": False,
                }
            )
            continue
        if ctag == "graph":
            dp = _find_first(content, "./*[local-name()='dataPath']")
            p = _find_first(content, "./*[local-name()='path']")
            out.append(
                {
                    "command_name": _xml_attr(content, "commandName") or command_name,
                    "syntax": syntax,
                    "subtype": None,
                    "bin_member": _xml_text(dp),
                    "xml_member": _xml_text(p),
                    "is_legacy": False,
                    "is_graph": True,
                }
            )
            continue
    return {"rows": out, "command_name": command_name, "syntax": syntax}


def _spvsx_html_text(text_node: Any) -> str | None:
    """Port of R/spv.R::.spvsx_html_text(): visible text of an embedded HTML block."""
    from lxml import etree

    html_node = _find_first(text_node, "./*[local-name()='html']")
    if html_node is None:
        return None
    raw = _xml_text(html_node) or ""
    if trimws(raw) == "":
        return None
    try:
        parser = etree.HTMLParser(
            recover=True, no_network=True, remove_blank_text=True, encoding="utf-8"
        )
        doc = etree.fromstring(("<div>" + raw + "</div>").encode("utf-8"), parser)
    except Exception:
        doc = None
    if doc is None:
        return str(trimws(raw))
    div = _find_first(doc, "//div")
    if div is None:
        return None
    for st in _find_all(div, ".//style"):
        _remove_node(st)
    txt = _xml_text(div) or ""
    return str(trimws(gsub("[ \t]+\n", "\n", txt)))


# ===========================================================================
# Top-level entry points
# ===========================================================================


def _check_input(path: str, ext: str, label: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(rf"\.{ext}$", path, ignore_case=True):
        raise ValueError(f"Not a .{label} file: {path}")


def import_spv(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read the statistical result tables from an SPSS Viewer (.spv) file.

    Port of R/spv.R::import_spv(). Ties the structure reader and the
    light-binary / legacy decoders together; a table that fails to decode is
    skipped (with a warning) rather than aborting the file.

    Args:
        path: path to a ``.spv`` file.

    Returns:
        A list of result tables, each a dict ``analysis``, ``title``, ``data``
        (a data frame), ``syntax`` (the SPSS syntax that produced it, when
        recoverable), ``is_chart`` and ``table_index``. Empty when the archive
        has no structure XML or no table decodes.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if ``path`` is not a ``.spv`` file or not a ZIP archive.
    """
    path = os.fspath(path)
    _check_input(path, "spv", "spv")
    with tempfile.TemporaryDirectory(prefix="spv_") as tmp:
        files = _unzip(path, tmp)
        if not files:
            raise ValueError(f"Could not open '{os.path.basename(path)}' as a .spv (zip) archive.")
        return _spv_read(tmp)


def _read_raw(path: str) -> bytes:
    return Path(path).read_bytes()


def _spv_read(dir_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Port of R/spv.R::.spv_read(): the worker behind :func:`import_spv`.

    Takes an already-unzipped archive directory (as ``read_stat_tables()``
    also calls it).
    """
    dir_path = os.fspath(dir_path)
    try:
        rows = _spv_read_structure(dir_path)
    except Exception:
        rows = []
    if not rows:
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        bin_member = r["bin_member"]
        if bin_member is None or bin_member == "":
            continue
        bin_path = _file_path(dir_path, bin_member)
        if not os.path.exists(bin_path):
            continue
        xml_path = _file_path(dir_path, _na_str(r["xml_member"]))

        if r["is_graph"]:
            df = None
            if os.path.exists(xml_path):
                try:
                    data = _spv_decode_legacy_data(_read_raw(bin_path))
                except Exception:
                    data = None
                if data is not None:  # R: readBin() errors here are not caught
                    df = _spv_decode_chart(_read_raw(xml_path), data)
            if df is None or len(df) == 0:
                continue
            out.append(
                {
                    "analysis": r["command_name"],
                    "title": df.attrs.get("spv_chart_title"),
                    "data": df,
                    "syntax": r["syntax"],
                    "is_chart": True,
                }
            )
            continue

        if r["is_legacy"]:
            df = None
            if os.path.exists(xml_path):
                try:
                    data = _spv_decode_legacy_data(_read_raw(bin_path))
                except Exception:
                    data = None
                if data is not None:  # R: readBin() errors here are not caught
                    df = _spv_decode_legacy_table(_read_raw(xml_path), data, title=r["subtype"])
        else:
            try:
                df = _spv_decode_light_table(_read_raw(bin_path))
            except Exception:
                df = None
        if df is None or len(df) == 0:
            continue
        subtype = r["subtype"]
        out.append(
            {
                "analysis": r["command_name"],
                "title": subtype
                if subtype is not None and subtype != ""
                else df.attrs.get("spv_title"),
                "data": df,
                "syntax": r["syntax"],
                "is_chart": False,
            }
        )
    for i, tb in enumerate(out, 1):
        tb["table_index"] = i
    return out


def _spv_export_syntax(spv_path: str | os.PathLike[str], code_dir_name: str = "code") -> str | None:
    """Port of R/spv.R::.spv_export_syntax().

    Writes the SPSS syntax recovered from a ``.spv`` file (consecutive repeats
    collapsed) to ``<dir>/<code_dir_name>/<name>.sps``. Returns that path, or
    ``None`` when the archive has no recoverable syntax.
    """
    spv_path = os.fspath(spv_path)
    if not os.path.exists(spv_path):
        raise FileNotFoundError(f"File not found: {spv_path}")
    with tempfile.TemporaryDirectory(prefix="spvsyntax_") as tmp:
        _unzip(spv_path, tmp)
        try:
            rows = _spv_read_structure(tmp)
        except Exception:
            rows = []
    if not rows:
        return None
    syntaxes = [r.get("syntax") for r in rows]
    keep: list[bool | None] = [True]
    for prev, cur in itertools.pairwise(syntaxes):
        if prev is None and cur is None:
            keep.append(None)
        elif prev is None or cur is None:
            keep.append(True)
        else:
            keep.append(cur != prev)
    kept = [s for s, k in zip(syntaxes, keep, strict=True) if k and s is not None and s != ""]
    if not kept:
        return None
    code_dir = _file_path(_r_dirname(spv_path), code_dir_name)
    os.makedirs(code_dir, exist_ok=True)
    out_path = _file_path(code_dir, _file_path_sans_ext(os.path.basename(spv_path)) + ".sps")
    _write_lines("\n\n".join(kept), out_path)
    return out_path


def _html_page(name: str, heading_tag: str, body: str) -> str:
    """The standalone page shared by the ``export_*_html()`` functions."""
    style = (
        "body { font-family: sans-serif; margin: 2em; }\n"
        f"{heading_tag} {{ border-bottom: 1px solid #888; margin-top: 2em; }}\n"
        "table { border-collapse: collapse; margin-bottom: 1.5em; }\n"
        "th, td { border: 1px solid #ccc; padding: 4px 10px; font-size: 90%; text-align: right; }\n"
        "th { background: #f0f0f0; text-align: center; }\n"
        "td:first-child, th:first-child { text-align: left; }\n"
    )
    esc = _spv_html_escape(name)
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{esc}</title>\n"
        f"<style>\n{style}</style>\n</head>\n<body>\n<h1>{esc}</h1>\n{body}\n</body>\n</html>\n"
    )


def export_spv_html(path: str | os.PathLike[str], out: str | os.PathLike[str] | None = None) -> str:
    """Export an SPSS Viewer (.spv) file's tables and charts as standalone HTML.

    Port of R/spv.R::export_spv_html(). Builds a page from what
    :func:`import_spv` decodes: one heading per analysis, one ``<table>`` per
    result table (pivoted back into SPSS's row/column layout) and, for a chart,
    an inline image rendered from its decoded data.

    Charts are drawn as SVG (R draws a PNG with base graphics), so the embedded
    image differs from metacheck's; the rest of the page is identical.

    Args:
        path: path to a ``.spv`` file.
        out: path to write the HTML file to; defaults to ``path`` with its
            extension replaced by ``.html``.

    Returns:
        The path written to.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    out = str(sub(r"\.spv$", ".html", path, ignore_case=True)) if out is None else os.fspath(out)
    tables = import_spv(path)
    if not tables:
        body = "<p>No result tables could be recovered from this .spv file.</p>"
    else:
        sections = []
        last_analysis: str | None = None
        for tb in tables:
            heading = ""
            analysis = tb.get("analysis")
            if analysis is not None and analysis != last_analysis:
                heading = f"<h2>{_spv_html_escape(analysis)}</h2>"
                last_analysis = analysis
            ttl = tb.get("title")
            title = f"<h3>{_spv_html_escape(ttl)}</h3>" if ttl is not None and ttl != "" else ""
            body_html = (
                _spv_chart_html(tb["data"]) if tb.get("is_chart") else _spv_table_html(tb["data"])
            )
            sections.append(heading + title + body_html)
        body = "\n".join(sections)
    _write_lines(_html_page(os.path.basename(path), "h2", body), out)
    return out


def _spv_html_escape(x: Any) -> str:
    """Port of R/spv.R::.spv_html_escape() (``&``, ``<``, ``>`` only)."""
    s = "" if x is None else (x if isinstance(x, str) else _na_str(as_character(x)))
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Chart rendering (SVG instead of R's base-graphics PNG)
# ---------------------------------------------------------------------------

_FIT_COLORS = ["#5596E6", "#D70033", "#298626", "#F3672A", "#E3D710"]
_W, _H = 640, 480
_ML, _MR, _MT, _MB = 70, 20, 20, 60


def _xml_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pretty(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round axis ticks (a small version of R's ``pretty()``)."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return []
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 12))
        t += step
    return ticks


def _svg_axes(
    xlab: str, ylab: str, yticks: list[float], ymap: Callable[[float], float]
) -> list[str]:
    out = [
        f'<line x1="{_ML}" y1="{_H - _MB}" x2="{_W - _MR}" y2="{_H - _MB}" stroke="black"/>',
        f'<line x1="{_ML}" y1="{_MT}" x2="{_ML}" y2="{_H - _MB}" stroke="black"/>',
        f'<text x="{(_ML + _W - _MR) / 2}" y="{_H - 15}" text-anchor="middle" '
        f'font-size="14">{_xml_esc(xlab)}</text>',
        f'<text x="18" y="{(_MT + _H - _MB) / 2}" text-anchor="middle" font-size="14" '
        f'transform="rotate(-90 18 {(_MT + _H - _MB) / 2})">{_xml_esc(ylab)}</text>',
    ]
    for t in yticks:
        y = ymap(t)
        out.append(f'<line x1="{_ML - 5}" y1="{y:.1f}" x2="{_ML}" y2="{y:.1f}" stroke="black"/>')
        out.append(
            f'<text x="{_ML - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11">'
            f"{_xml_esc(format_num(t))}</text>"
        )
    return out


def _scale(lo: float, hi: float, a: float, b: float) -> Callable[[float], float]:
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    return lambda v: a + (v - lo) / (hi - lo) * (b - a)


def _fivenum(v: list[float]) -> list[float]:
    """R ``fivenum()`` (Tukey's hinges)."""
    x = sorted(v)
    n = len(x)
    n4 = math.floor((n + 3) / 2) / 2
    d = [1, n4, (n + 1) / 2, n + 1 - n4, n]
    return [0.5 * (x[math.floor(di) - 1] + x[math.ceil(di) - 1]) for di in d]


def _fit_y(v: Any) -> float:
    """One fitted-curve value as ``xy.coords()`` coerces it (complex: real part)."""
    if v is None:
        return math.nan
    if isinstance(v, complex):
        return v.real
    if isinstance(v, str):
        num = _as_numeric(v)
        return math.nan if num is None else num
    return float(v)


def _svg_chart(df: pd.DataFrame) -> str:
    chart_type = df.attrs.get("spv_chart_type") or "point"
    xlab = df.attrs.get("spv_chart_xlab") or "x"
    ylab = df.attrs.get("spv_chart_ylab") or "y"
    parts: list[str] = []
    if chart_type == "boxplot":
        groups: dict[str, list[float]] = {}
        for c, v in zip(df["category"].tolist(), df["value"].tolist(), strict=True):
            if c is None or v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            groups.setdefault(str(c), []).append(float(v))
        if not groups:  # boxplot(value ~ category): no complete row left
            raise ValueError("invalid first argument")
        levels = sorted(groups, key=r_sort_key)
        allv = [v for g in groups.values() for v in g]
        lo, hi = (min(allv), max(allv)) if allv else (0.0, 1.0)
        ymap = _scale(lo, hi, _H - _MB - 10, _MT + 10)
        parts += _svg_axes(xlab, ylab, _pretty(lo, hi), ymap)
        width = (_W - _ML - _MR) / max(1, len(levels))
        for k, lev in enumerate(levels):
            vals = groups[lev]
            q = _fivenum(vals)
            iqr = q[3] - q[1]
            lo_w = min(v for v in vals if v >= q[1] - 1.5 * iqr)
            hi_w = max(v for v in vals if v <= q[3] + 1.5 * iqr)
            cx = _ML + width * (k + 0.5)
            bw = width * 0.4
            parts.append(
                f'<rect x="{cx - bw:.1f}" y="{ymap(q[3]):.1f}" width="{2 * bw:.1f}" '
                f'height="{max(0.5, ymap(q[1]) - ymap(q[3])):.1f}" fill="#5596E6" stroke="black"/>'
            )
            parts.append(
                f'<line x1="{cx - bw:.1f}" y1="{ymap(q[2]):.1f}" x2="{cx + bw:.1f}" '
                f'y2="{ymap(q[2]):.1f}" stroke="black" stroke-width="3"/>'
            )
            for a, b in ((q[3], hi_w), (q[1], lo_w)):
                parts.append(
                    f'<line x1="{cx:.1f}" y1="{ymap(a):.1f}" x2="{cx:.1f}" y2="{ymap(b):.1f}" '
                    'stroke="black" stroke-dasharray="4 3"/>'
                )
            for v in vals:
                if v < lo_w or v > hi_w:
                    parts.append(
                        f'<circle cx="{cx:.1f}" cy="{ymap(v):.1f}" r="3" fill="none" stroke="black"/>'
                    )
            parts.append(
                f'<text x="{cx:.1f}" y="{_H - _MB + 16}" text-anchor="middle" font-size="11">'
                f"{_xml_esc(lev)}</text>"
            )
    elif chart_type == "interval":
        agg: dict[float, float] = {}
        for x, y in zip(df["x"].tolist(), df["y"].tolist(), strict=True):
            agg[x] = agg.get(x, 0.0) + y
        xs = sorted(agg)
        heights = [agg[x] for x in xs]
        top = max([0.0, *heights])
        bottom = min([0.0, *heights])
        ymap = _scale(bottom, top, _H - _MB, _MT + 10)
        parts += _svg_axes(xlab, ylab, _pretty(bottom, top), ymap)
        width = (_W - _ML - _MR) / max(1, len(xs))
        for k, (x, h) in enumerate(zip(xs, heights, strict=True)):
            x0 = _ML + width * k + width * 0.1
            y0, y1 = sorted((ymap(0.0), ymap(h)))
            parts.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{width * 0.8:.1f}" '
                f'height="{y1 - y0:.1f}" fill="#5596E6"/>'
            )
            parts.append(
                f'<text x="{x0 + width * 0.4:.1f}" y="{_H - _MB + 16}" text-anchor="middle" '
                f'font-size="11">{_xml_esc(_spv_display_value(x))}</text>'
            )
    else:
        xs = [float(v) for v in df["x"].tolist()]
        ys = [float(v) for v in df["y"].tolist()]
        xmap = _scale(min(xs), max(xs), _ML + 10, _W - _MR - 10)
        ymap = _scale(min(ys), max(ys), _H - _MB - 10, _MT + 10)
        parts += _svg_axes(xlab, ylab, _pretty(min(ys), max(ys)), ymap)
        for x, y in zip(xs, ys, strict=True):
            parts.append(
                f'<circle cx="{xmap(x):.1f}" cy="{ymap(y):.1f}" r="3" fill="black" fill-opacity="0.6"/>'
            )
        fits = df.attrs.get("spv_chart_fits") or []
        if fits:
            lo, hi = min(xs), max(xs)
            xr = [lo + (hi - lo) * i / 199 for i in range(200)]
            for i, f in enumerate(fits):
                try:
                    val = f["fn"](xr)
                except _REvalError:  # R: tryCatch(fits[[i]]$fn(xr), error = NULL)
                    continue
                if val is None:
                    continue
                # a constant fit is a flat line; any other value that is not
                # one y per x is skipped (R's lines() stops, failing the whole
                # export; UPSTREAM_ISSUES U148)
                if len(val) == 1:
                    val = list(val) * len(xr)
                if len(val) != len(xr):
                    continue
                yr = [_fit_y(v) for v in val]
                pts = " ".join(
                    f"{xmap(x):.1f},{ymap(y):.1f}"
                    for x, y in zip(xr, yr, strict=True)
                    if math.isfinite(y)
                )
                if not pts:
                    continue
                parts.append(
                    f'<polyline points="{pts}" fill="none" stroke="{_FIT_COLORS[i % len(_FIT_COLORS)]}" '
                    'stroke-width="2"/>'
                )
            for i, f in enumerate(fits):
                label = f["expr"] if not f.get("name") else f["name"]
                y = _MT + 15 + 16 * i
                col = _FIT_COLORS[i] if i < len(_FIT_COLORS) else "black"
                parts.append(
                    f'<line x1="{_W - 220}" y1="{y - 4}" x2="{_W - 200}" y2="{y - 4}" '
                    f'stroke="{col}" stroke-width="2"/>'
                )
                parts.append(
                    f'<text x="{_W - 195}" y="{y}" font-size="11">{_xml_esc(label)}</text>'
                )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}"><rect width="100%" height="100%" fill="white"/>'
        + "".join(parts)
        + "</svg>"
    )


def _spv_chart_html(df: pd.DataFrame | None) -> str:
    """Port of R/spv.R::.spv_chart_html(): a decoded chart as an inline image.

    R draws a PNG with base graphics; this draws an equivalent SVG (scatter
    plot with SPSS's fitted lines, summed bar chart, or box plot), so the
    embedded image bytes and MIME type differ from metacheck's.
    """
    if df is None or len(df) == 0:
        return ""
    try:
        svg = _svg_chart(df)
    except ValueError:
        # nothing to draw (a box plot without a complete category/value
        # row): the chart is left out; R's boxplot() stops the whole export
        # (UPSTREAM_ISSUES U148)
        return ""
    data_uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="{data_uri}" alt="chart" style="max-width: 100%;">'


def _spv_display_value(x: Any) -> str:
    """Port of R/spv.R::.spv_display_value(): a cell rounded to 3 decimals for display."""
    if _is_missing(x):
        return ""
    s = x if isinstance(x, str) else _na_str(as_character(x))
    num = _as_numeric(s)
    if num is None or not math.isfinite(num) or not grepl(r"^[-+]?[0-9.]+([eE][-+]?[0-9]+)?$", s):
        return s
    if num == round(num):
        return _format_fixed_whole(num)
    rounded = f"{num:.3f}"
    if num != 0 and float(rounded) == 0:
        return s
    return rounded


def _spv_table_html(df: pd.DataFrame | None) -> str:
    """Port of R/spv.R::.spv_table_html(): a decoded table as an HTML ``<table>``.

    Pivots into a cross-tab using ``attrs["spv_row_dims"]``/``["spv_col_dims"]``
    when present, else lists one column per data-frame column.
    """
    if df is None or len(df) == 0 or df.shape[1] == 0:
        return ""
    names = [str(c) for c in df.columns]
    row_dims = [d for d in (df.attrs.get("spv_row_dims") or []) if d is not None and d in names]
    col_dims = [d for d in (df.attrs.get("spv_col_dims") or []) if d is not None and d in names]
    if (row_dims or col_dims) and len(row_dims) + len(col_dims) < df.shape[1]:
        return _spv_table_html_pivot(df, row_dims, col_dims)

    headers = "".join(f"<th>{_spv_html_escape(n)}</th>" for n in names)
    columns = [_column_values(df, j) for j in range(df.shape[1])]
    rows = []
    for i in range(len(df)):
        cells = "".join(
            f"<td>{_spv_html_escape(_spv_display_value(col[i]))}</td>" for col in columns
        )
        rows.append(f"<tr>{cells}</tr>")
    return (
        f"<table>\n<thead><tr>{headers}</tr></thead>\n<tbody>\n"
        + "\n".join(rows)
        + "\n</tbody>\n</table>"
    )


def _column_values(df: pd.DataFrame, j: int) -> list[Any]:
    return [
        None if _is_missing(v) and not isinstance(v, float) else v for v in df.iloc[:, j].tolist()
    ]


def _column_by_name(df: pd.DataFrame, name: str) -> list[Any]:
    return _column_values(df, [str(c) for c in df.columns].index(name))


def _paste_str(v: Any) -> str:
    """One value as ``paste()`` renders it (``NA`` as ``"NA"``, numbers as ``as.character()``)."""
    if isinstance(v, str):
        return v
    if _is_missing(v):
        return "NA"
    return _na_str(as_character(v))


def _rle_lengths(x: list[str]) -> list[int]:
    out: list[int] = []
    prev: object = object()
    for v in x:
        if out and v == prev:
            out[-1] += 1
        else:
            out.append(1)
            prev = v
    return out


def _spv_table_html_pivot(df: pd.DataFrame, row_dims: list[str], col_dims: list[str]) -> str:
    """Port of R/spv.R::.spv_table_html_pivot(): nested row stub and column headers.

    Each row and column level keeps its labels as a tuple, one per dimension;
    R pastes them together and splits them again with ``strsplit()``, which
    drops a trailing empty label, so the export failed ("subscript out of
    bounds", UPSTREAM_ISSUES U148).
    """
    n = len(df)

    def keys(dims: list[str], default: str) -> list[tuple[str, ...]]:
        if not dims:
            return [(default,)] * n
        cols = [_column_by_name(df, d) for d in dims]
        return [tuple(_paste_str(c[i]) for c in cols) for i in range(n)]

    row_key = keys(row_dims, "")
    col_key = keys(col_dims, "value")
    row_levels = list(dict.fromkeys(row_key))
    col_levels = list(dict.fromkeys(col_key))

    if col_dims:
        header_row_cells = []
        for d in range(len(col_dims)):
            labels = [part[d] for part in col_levels]
            cells = []
            pos = 0
            for k in _rle_lengths(labels):
                if k > 1:
                    cells.append(f'<th colspan="{k}">{_spv_html_escape(labels[pos])}</th>')
                else:
                    cells.append(f"<th>{_spv_html_escape(labels[pos])}</th>")
                pos += k
            header_row_cells.append(cells)
    else:
        header_row_cells = [["<th>value</th>"]]

    n_levels = len(header_row_cells)
    header_rows = []
    for d in range(1, n_levels + 1):
        if row_dims:
            if d < n_levels:
                left = f'<th colspan="{len(row_dims)}"></th>' if len(row_dims) > 1 else "<th></th>"
            else:
                left = "".join(f"<th>{_spv_html_escape(r)}</th>" for r in row_dims)
        else:
            left = ""
        header_rows.append("<tr>" + left + "".join(header_row_cells[d - 1]) + "</tr>")

    grid: list[list[Any]] = [[None] * len(col_levels) for _ in row_levels]
    ri = {k: i for i, k in reversed(list(enumerate(row_levels)))}
    ci = {k: i for i, k in reversed(list(enumerate(col_levels)))}
    values = _column_by_name(df, "value")
    for i in range(n):
        grid[ri[row_key[i]]][ci[col_key[i]]] = values[i]

    body_rows = []
    for i in range(len(row_levels)):
        stub = (
            "".join(f"<td>{_spv_html_escape(p)}</td>" for p in row_levels[i]) if row_dims else ""
        )
        cells = "".join(f"<td>{_spv_html_escape(_spv_display_value(v))}</td>" for v in grid[i])
        body_rows.append(f"<tr>{stub}{cells}</tr>")
    return (
        "<table>\n<thead>\n"
        + "\n".join(header_rows)
        + "\n</thead>\n<tbody>\n"
        + "\n".join(body_rows)
        + "\n</tbody>\n</table>"
    )
