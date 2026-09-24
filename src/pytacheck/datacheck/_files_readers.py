"""File readers behind :func:`pytacheck.datacheck.files.data_read_head`.

Each reader reproduces the R function metacheck calls, so that column names,
row counts and column types match:

* :func:`read_delim` -- ``utils::read.delim()`` (the fallback when fread
  fails), including ``type.convert()``;
* :func:`read_excel` -- ``readxl::read_excel()`` for ``.xlsx`` (its own XML
  reader, as readxl has) and ``.xls`` (via ``xlrd``): sheet extent, header
  row, type guessing over ``guess_max`` rows, readxl's cell coercions and
  vctrs name repair;
* :func:`read_ods` -- ``readODS::read_ods()`` (content.xml / flat ``.fods``)
  with minty's readr-style type guessing;
* :func:`read_stat_file` -- ``haven::read_sav/read_dta/read_sas/read_por``
  via ``pyreadstat`` (both wrap ReadStat), keeping variable and value labels;
* :func:`read_rds_head` / :func:`read_rdata_first_df` -- ``readRDS()`` and
  ``load()`` through pytacheck's own unserializer.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import warnings
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import as_character
from pytacheck._r.regex import compile_r


def _rx(pattern: str, ignore_case: bool = False) -> Any:
    """A compiled internal grammar (PCRE semantics, cached)."""
    return compile_r(pattern, ignore_case=ignore_case, perl=True)


# -----------------------------------------------------------------------------
# vctrs name repair
# -----------------------------------------------------------------------------


def _is_dotdotint(name: str) -> bool:
    if len(name) < 3 or not name.startswith(".."):
        return False
    rest = name[3:] if name[2] == "." else name[2:]
    m = _rx(r"\s*[+-]?[0-9]+").match(rest)
    return bool(m) and int(m.group(0)) != 0


def _suffix_pos(name: str) -> int:
    """Start of a trailing ``...N`` (possibly repeated) suffix, or -1."""
    suffix_end = -1
    in_dots = 0
    in_digits = False
    i = len(name) - 1
    while i >= 0:
        c = name[i]
        if in_digits:
            if c == ".":
                in_digits = False
                in_dots += 1
                i -= 1
                continue
            if c.isdigit():
                i -= 1
                continue
            break
        if in_dots == 0:
            if c.isdigit():
                in_digits = True
                i -= 1
                continue
            break
        if in_dots in (1, 2):
            if c == ".":
                in_dots += 1
                i -= 1
                continue
            break
        # in_dots == 3
        suffix_end = i + 1
        if c.isdigit():
            in_dots = 0
            in_digits = True
            i -= 1
            continue
        break
    if in_dots == 3 and i < 0:
        suffix_end = 0
    return suffix_end


def vec_as_names_unique(names: Sequence[str | None]) -> list[str]:
    """``vctrs::vec_as_names(names, repair = "unique")``."""
    out: list[str] = []
    for nm in names:
        s = "" if nm is None else nm
        if s == "..." or _is_dotdotint(s):
            s = ""
        else:
            pos = _suffix_pos(s)
            if pos >= 0:
                s = s[:pos]
        out.append(s)
    counts: dict[str, int] = {}
    for s in out:
        counts[s] = counts.get(s, 0) + 1
    return [f"{s}...{i}" if (s == "" or counts[s] > 1) else s for i, s in enumerate(out, 1)]


# -----------------------------------------------------------------------------
# read.delim()
# -----------------------------------------------------------------------------


def read_delim(
    path: str | os.PathLike[str],
    sep: str,
    header: bool,
    nrows: float = math.inf,
    encoding: str | None = None,
) -> pd.DataFrame:
    """``utils::read.delim(path, sep, header, nrows, check.names = FALSE)``.

    ``encoding = "latin1"`` is ``fileEncoding = "latin1"``. See
    :mod:`pytacheck.datacheck._files_readtable`.
    """
    from pytacheck.datacheck._files_readtable import read_table

    return read_table(path, sep, header, nrows, encoding)


# -----------------------------------------------------------------------------
# readxl
# -----------------------------------------------------------------------------

CELL_BLANK, CELL_LOGICAL, CELL_DATE, CELL_NUMERIC, CELL_TEXT = 1, 2, 3, 4, 5


def _is_date_format(code: str) -> bool:
    """readxl's isDateFormat() (including its ``General`` shortcut)."""
    escaped = False
    bracket = False
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        lc = c.lower()
        if lc in "dhmsy":
            if not escaped and not bracket:
                return True
        elif c == '"':
            escaped = not escaped
        elif c in "\\_":
            i += 1
        elif c == "[":
            if not escaped:
                bracket = True
        elif c == "]":
            if not escaped:
                bracket = False
        elif lc == "g" and i + 6 < n and all(code[i + k] != "\0" for k in range(1, 7)):
            return False
        i += 1
    return False


def _is_date_time(fmt_id: int, custom: set[int]) -> bool:
    if (
        14 <= fmt_id <= 22
        or 27 <= fmt_id <= 36
        or 45 <= fmt_id <= 47
        or 50 <= fmt_id <= 58
        or 71 <= fmt_id <= 81
    ):
        return True
    if fmt_id < 164:
        return False
    return fmt_id in custom


def _posixct_from_serial(x: float, is1904: bool) -> float | None:
    if not is1904 and x < 61:
        if x < 60:
            x += 1
        else:
            return None  # impossible 1900-02-29
    if x < 0:
        return None
    secs = (x - (24107 if is1904 else 25569)) * 86400
    ms = secs * 1000
    ms = math.floor(ms + 0.5) if ms >= 0 else math.ceil(ms - 0.5)
    return ms / 1000


def _trim(s: str) -> str:
    return s.strip(" \t")


def _atoi(s: str) -> int:
    m = _rx(r"\s*([+-]?[0-9]+)").match(s)
    return int(m.group(1)) if m else 0


def _atof(s: str) -> float:
    m = _rx(
        r"\s*([+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[+-]?(?:inf|nan))", True
    ).match(s)
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def _strtod_full(s: str) -> float | None:
    """C ``strtod()`` that must consume the whole string."""
    if s == "" or (s[0].isspace() and s.strip() == ""):
        return None
    m = _rx(
        r"\s*([+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan"
        r"|0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?))",
        True,
    ).fullmatch(s)
    if m is None:
        return None
    tok = m.group(1)
    try:
        if "x" in tok.lower():
            from pytacheck.datacheck.files import _hex_float

            v = _hex_float(tok.lower().lstrip("+-")[2:])
            if v is None:
                return None
            return -v if tok.startswith("-") else v
        v = float(tok)
    except (ValueError, OverflowError):
        return None
    if math.isinf(v) and "inf" not in tok.lower():
        return None  # ERANGE
    return v


class _Cell:
    """One non-empty worksheet cell as readxl sees it."""

    __slots__ = ("col", "kind", "raw", "row", "value")

    def __init__(self, row: int, col: int, kind: int, raw: Any, value: Any) -> None:
        self.row, self.col, self.kind, self.raw, self.value = row, col, kind, raw, value

    def as_text(self, xls: bool) -> str | None:
        k = self.kind
        if k == CELL_BLANK:
            return None
        if k == CELL_LOGICAL:
            return "TRUE" if self.value else "FALSE"
        if k in (CELL_DATE, CELL_NUMERIC):
            if xls:
                d = float(self.value)
                if d == math.floor(d) and math.isfinite(d):
                    return str(int(d))
                return _c_g17(d)
            return str(self.raw)
        s = _trim(str(self.value))
        return s if s else None

    def as_logical(self, xls: bool) -> bool | None:
        k = self.kind
        if k in (CELL_LOGICAL, CELL_NUMERIC):
            if xls:
                return float(self.value) != 0
            return _atoi(str(self.raw)) != 0
        if k == CELL_TEXT:
            return {
                "T": True, "True": True, "TRUE": True, "true": True,
                "F": False, "False": False, "FALSE": False, "false": False,
            }.get(_trim(str(self.value)))  # fmt: skip
        return None

    def as_double(self, xls: bool) -> float | None:
        k = self.kind
        if k in (CELL_LOGICAL, CELL_DATE, CELL_NUMERIC):
            return float(self.value) if xls else _atof(str(self.raw))
        if k == CELL_TEXT:
            return _strtod_full(_trim(str(self.value)))
        return None

    def as_date(self, xls: bool, is1904: bool) -> float | None:
        if self.kind in (CELL_DATE, CELL_NUMERIC):
            v = float(self.value) if xls else _atof(str(self.raw))
            return _posixct_from_serial(v, is1904)
        return None


def _c_g17(x: float) -> str:
    s = f"{x:.17g}"
    if "e" in s:
        mant, exp = s.split("e")
        s = f"{mant}e{exp[0]}{exp[1:].lstrip('0').rjust(2, '0')}"
    return s


def _col_ref(ref: str) -> tuple[int, int]:
    row = col = 0
    for ch in ref:
        if "0" <= ch <= "9":
            row = row * 10 + ord(ch) - 48
        elif "A" <= ch <= "Z":
            col = 26 * col + ord(ch) - 64
        else:
            raise ValueError(f"Invalid character '{ch}' in cell ref '{ref}'")
    return row - 1, col - 1


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_string(node: Any) -> str | None:
    """readxl's parseString(): ``<t>`` plus every ``<r><t>`` run (no phonetics)."""
    found = False
    out = ""
    for child in node:
        name = _local(child.tag)
        if name == "t":
            out = _unescape_x(child.text or "")
            found = True
            break
    for child in node:
        if _local(child.tag) == "r":
            for t in child:
                if _local(t.tag) == "t":
                    out += _unescape_x(t.text or "")
                    found = True
                    break
    return out if found else None


_X_ESCAPE = _rx(r"_x([0-9a-fA-F]{4})_")


def _unescape_x(s: str) -> str:
    return _X_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), s) if "_x" in s else s


class _XlsxBook:
    def __init__(self, path: str) -> None:
        from lxml import etree

        self.etree = etree
        self.zf = zipfile.ZipFile(path)
        names = set(self.zf.namelist())
        rels = self._parse("_rels/.rels")
        office = ""
        for rel in rels:
            if _local(rel.tag) == "Relationship" and rel.get("Type", "").endswith(
                "/officeDocument"
            ):
                office = rel.get("Target", "").lstrip("/")
        if not office:
            office = "xl/workbook.xml"
        wb = self._parse(office)
        self.sheet_names: list[str] = []
        self.sheet_ids: list[str | None] = []
        self.is1904 = False
        for node in wb.iter():
            name = _local(node.tag) if isinstance(node.tag, str) else ""
            if name == "sheet":
                self.sheet_names.append(str(node.get("name") or ""))
                rid = next((v for k, v in node.attrib.items() if _local(k) == "id"), None)
                self.sheet_ids.append(rid)
            elif name == "workbookPr":
                self.is1904 = _atoi(node.get("date1904", "0") or "0") == 1
        wb_dir = office.rsplit("/", 1)[0] if "/" in office else ""
        rels_path = (
            (wb_dir + "/" if wb_dir else "") + "_rels/" + office.rsplit("/", 1)[-1] + ".rels"
        )
        self.targets: dict[str, str] = {}
        self.parts: dict[str, str] = {}
        if rels_path in names:
            for rel in self._parse(rels_path):
                rid, typ, target = rel.get("Id"), rel.get("Type"), rel.get("Target")
                if not (rid and typ and target):
                    continue
                target = target.lstrip("/")
                if not target.startswith(wb_dir):
                    target = wb_dir + "/" + target
                kind = typ.rsplit("/", 1)[-1]
                if kind == "worksheet":
                    self.targets[rid] = target
                else:
                    self.parts[kind] = target
        self.strings: list[str] = []
        sst = self.parts.get("sharedStrings")
        if sst and sst in names:
            for si in self._parse(sst):
                s = _xml_string(si)
                if s is not None:
                    self.strings.append(s)
        self.date_styles: set[int] = set()
        styles = self.parts.get("styles")
        if styles and styles in names:
            root = self._parse(styles)
            custom: set[int] = set()
            for node in root:
                if _local(node.tag) == "numFmts":
                    for fmt in node:
                        code = fmt.get("formatCode", "")
                        if _is_date_format(code):
                            custom.add(_atoi(fmt.get("numFmtId", "0")))
            for node in root:
                if _local(node.tag) == "cellXfs":
                    for i, xf in enumerate(node):
                        if xf.get("numFmtId") is None:
                            continue
                        if _is_date_time(_atoi(xf.get("numFmtId")), custom):
                            self.date_styles.add(i)

    def _parse(self, name: str) -> Any:
        return self.etree.fromstring(self.zf.read(name))

    def sheet_cells(self, index: int) -> list[_Cell]:
        """Every cell with content (any child element), in document order."""
        if index >= len(self.sheet_names):
            raise ValueError(
                f"Can't retrieve sheet in position {index + 1}, only "
                f"{len(self.sheet_names)} sheet(s) found."
            )
        target = self.targets.get(self.sheet_ids[index] or "")
        if target is None:
            raise ValueError(f"`{self.sheet_ids[index]}` not found")
        cells: list[_Cell] = []
        etree = self.etree
        with self.zf.open(target) as fh:
            i = 0
            for _event, node in etree.iterparse(fh, events=("end",), huge_tree=True):
                name = _local(node.tag)
                if name != "row":
                    continue
                r = node.get("r")
                if r:
                    i = _atoi(r) - 1
                j = 0
                for c in node:
                    if _local(c.tag) != "c":
                        continue
                    ref = c.get("r")
                    if ref:
                        i, j = _col_ref(ref)
                    if len(c):
                        cells.append(self._cell(c, i, j))
                    j += 1
                i += 1
                node.clear()
        return cells

    def _cell(self, c: Any, i: int, j: int) -> _Cell:
        t = c.get("t")
        v_node = None
        is_node = None
        for ch in c:
            nm = _local(ch.tag)
            if nm == "v":
                v_node = ch
            elif nm == "is":
                is_node = ch
        v = None if v_node is None else (v_node.text or "")
        if t is not None and t.startswith("inlineStr"):
            s = _xml_string(is_node) if is_node is not None else None
            if s is None or _trim(s) == "":
                return _Cell(i, j, CELL_BLANK, None, None)
            return _Cell(i, j, CELL_TEXT, s, s)
        if t == "s":
            idx = _atoi(v or "0")
            s = self.strings[idx] if 0 <= idx < len(self.strings) else ""
            if _trim(s) == "":
                return _Cell(i, j, CELL_BLANK, None, None)
            return _Cell(i, j, CELL_TEXT, s, s)
        if v is None or _trim(v) == "":
            return _Cell(i, j, CELL_BLANK, None, None)
        if t is None or t == "n":
            style = _atoi(c.get("s")) if c.get("s") is not None else -1
            kind = CELL_DATE if style in self.date_styles else CELL_NUMERIC
            return _Cell(i, j, kind, v, _atof(v))
        if t == "b":
            return _Cell(i, j, CELL_LOGICAL, v, _atoi(v) != 0)
        if t in ("d", "str"):
            return _Cell(i, j, CELL_TEXT, v, v)
        if t == "e":
            return _Cell(i, j, CELL_BLANK, None, None)
        warnings.warn(f"Unrecognized cell type '{t}'", stacklevel=2)
        return _Cell(i, j, CELL_BLANK, None, None)


class _XlsBook:
    def __init__(self, path: str) -> None:
        import xlrd

        self.xlrd = xlrd
        self.book = xlrd.open_workbook(path, formatting_info=True, on_demand=True)
        self.sheet_names = self.book.sheet_names()
        self.is1904 = bool(self.book.datemode)
        custom = {
            key
            for key, fmt in self.book.format_map.items()
            if key >= 164 and _is_date_format(fmt.format_str)
        }
        self.date_styles = {
            i for i, xf in enumerate(self.book.xf_list) if _is_date_time(xf.format_key, custom)
        }

    def sheet_cells(self, index: int) -> list[_Cell]:
        if index >= len(self.sheet_names):
            raise ValueError(
                f"Can't retrieve sheet in position {index + 1}, only "
                f"{len(self.sheet_names)} sheet(s) found."
            )
        xlrd = self.xlrd
        sh = self.book.sheet_by_index(index)
        cells: list[_Cell] = []
        for i in range(sh.nrows):
            for j in range(sh.row_len(i)):
                ct = sh.cell_type(i, j)
                if ct in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                v = sh.cell_value(i, j)
                if ct == xlrd.XL_CELL_TEXT:
                    s = str(v)
                    kind = CELL_BLANK if _trim(s) == "" else CELL_TEXT
                    cells.append(_Cell(i, j, kind, s, s))
                elif ct in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE):
                    xf = sh.cell_xf_index(i, j)
                    kind = CELL_DATE if xf in self.date_styles else CELL_NUMERIC
                    cells.append(_Cell(i, j, kind, v, float(v)))
                elif ct == xlrd.XL_CELL_BOOLEAN:
                    cells.append(_Cell(i, j, CELL_LOGICAL, v, bool(v)))
                elif ct == xlrd.XL_CELL_ERROR:
                    cells.append(_Cell(i, j, CELL_BLANK, None, None))
        return cells


def _sheet_index(names: list[str], sheet: str | int | None) -> int:
    if sheet is None:
        return 0
    if isinstance(sheet, str):
        if sheet not in names:
            raise ValueError(f"Sheet '{sheet}' not found")
        return names.index(sheet)
    if sheet < 1:
        raise ValueError("`sheet` must be positive")
    return math.floor(sheet) - 1


def read_excel(
    path: str | os.PathLike[str],
    sheet: str | int | None = None,
    col_names: bool = True,
    col_types: str | None = None,
    skip: int = 0,
    n_max: float = math.inf,
    name_repair: str = "unique",
) -> pd.DataFrame:
    """``readxl::read_excel()`` (``.xlsx`` or ``.xls``), ``trim_ws = TRUE``, ``na = ""``.

    *col_types* is ``None`` (guess) or ``"text"``; *name_repair* is
    ``"unique"`` or ``"minimal"``.
    """
    path = str(path)
    xls = path.lower().endswith(".xls")
    book: _XlsBook | _XlsxBook = _XlsBook(path) if xls else _XlsxBook(path)
    index = _sheet_index(book.sheet_names, sheet)
    all_cells = book.sheet_cells(index)

    # limits (0-based rows); n_max counts data rows after the header
    n_read = n_max + 1 if col_names else n_max
    if n_read == 0:
        cells: list[_Cell] = []
    else:
        min_row = skip
        max_row = -1 if n_read == math.inf else int(skip + n_read - 1)
        needs_check = max_row >= 0
        cells = []
        for c in all_cells:
            if c.row < min_row:
                continue
            if needs_check:
                if c.row > min_row:  # implicit skip of leading empty rows
                    max_row = c.row + max_row - min_row
                    min_row = c.row
                needs_check = False
            if max_row >= 0 and c.row > max_row:
                continue
            cells.append(c)
    if not cells:
        return pd.DataFrame()
    min_col = min(c.col for c in cells)
    max_col = max(c.col for c in cells)
    last_row = max(c.row for c in cells)
    ncol = max_col - min_col + 1
    first_row = cells[0].row

    names: list[str | None] = [""] * ncol
    if col_names:
        for c in cells:
            if c.row != first_row:
                break
            names[c.col - min_col] = c.as_text(xls)
    body = [c for c in cells if c.row > first_row] if col_names else cells
    base = first_row + (1 if col_names else 0)
    n = 0 if not body else last_row - base + 1

    if col_types == "text":
        types = [CELL_TEXT] * ncol
    else:
        guess_max = int(min(1000, n_max)) if math.isfinite(n_max) else 1000
        types = [0] * ncol
        if not body:
            types = [CELL_BLANK] * ncol
        for c in body:
            if c.row - base >= guess_max:
                break
            j = c.col - min_col
            if types[j] == CELL_TEXT:
                continue
            types[j] = max(types[j], c.kind)
        types = [CELL_LOGICAL if t in (0, CELL_BLANK) else t for t in types]

    columns: list[list[Any]] = [[None] * n for _ in range(ncol)]
    for c in body:
        j = c.col - min_col
        row = c.row - base
        t = types[j]
        if t == CELL_LOGICAL:
            columns[j][row] = c.as_logical(xls)
        elif t == CELL_DATE:
            columns[j][row] = c.as_date(xls, book.is1904)
        elif t == CELL_NUMERIC:
            columns[j][row] = c.as_double(xls)
        else:
            columns[j][row] = c.as_text(xls)
    series = []
    for t, vals in zip(types, columns, strict=True):
        if t == CELL_LOGICAL:
            series.append(pd.Series(vals, dtype="boolean"))
        elif t == CELL_NUMERIC:
            series.append(pd.Series([math.nan if v is None else v for v in vals], dtype="float64"))
        elif t == CELL_DATE:
            from pytacheck.datacheck._files_time import posixct_series

            series.append(posixct_series([math.nan if v is None else v for v in vals]))
        else:
            series.append(pd.Series(vals, dtype="string"))
    out = pd.DataFrame(dict(enumerate(series)))
    trimmed = [None if nm is None else _trim(nm) for nm in names]
    final = (
        vec_as_names_unique(trimmed)
        if name_repair == "unique"
        else ["" if nm is None else nm for nm in trimmed]
    )
    out.columns = pd.Index(final, dtype=object)
    return out


# -----------------------------------------------------------------------------
# readODS
# -----------------------------------------------------------------------------

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _t(name: str) -> str:
    return f"{{{_TABLE_NS}}}{name}"


def _ods_parse_p(node: Any) -> str:
    out = node.text or ""
    for child in node:
        name = _local(child.tag)
        if name == "s":
            c = child.get(f"{{{_TEXT_NS}}}c")
            out += " " * (int(c) if c else 1)
        elif name == "line-break":
            out += "\n"
        elif name == "a":
            if not any(_local(g.tag) == "a" for g in child):
                out += _ods_parse_p(child)
        else:
            out += child.text or ""
        out += child.tail or ""
    return out


def _ods_cell_value(cell: Any) -> str:
    ps = [ch for ch in cell if _local(ch.tag) == "p"]
    out = ""
    i = 0
    for p in ps:
        if i > 0:
            out += "\n"
        if len(p) or p.text:
            out += _ods_parse_p(p)
            i += 1
    vtype = cell.get(f"{{{_OFFICE_NS}}}value-type")
    value = cell.get(f"{{{_OFFICE_NS}}}value")
    if vtype and (
        (out == "" and value is not None) or vtype in ("float", "currency", "percentage")
    ):
        out = value if value is not None else ""
    return out


def _has_content(node: Any) -> bool:
    return len(node) > 0


def _ods_rows(sheet: Any, start_row: int, stop_row: int) -> list[list[Any]]:
    """readODS's find_rows(): cell pointers per nominal row (1-based range)."""
    start_row = max(start_row, 1)
    rows: list[list[Any]] = []
    row_nodes = list(sheet.iter(_t("table-row")))
    if not row_nodes or not any(_local(c.tag) == "table-cell" for c in row_nodes[0]):
        return [[]]
    i = 1
    for k, row in enumerate(row_nodes):
        if not (stop_row < 1 or i <= stop_row):
            break
        rep = int(row.get(_t("number-rows-repeated"), "1"))
        children = list(row)
        last_row = k == len(row_nodes) - 1
        for _ in range(rep):
            if not (stop_row < 1 or i <= stop_row):
                break
            while len(rows) < i - start_row + 1:
                rows.append([])
            blank_row = len(children) <= 1 and (not children or not _has_content(children[0]))
            if blank_row:
                if last_row:
                    break
            elif i >= start_row:
                cells: list[Any] = []
                last_non_blank = 0
                for ci, cell in enumerate(children):
                    if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                        continue
                    crep = int(cell.get(_t("number-columns-repeated"), "1"))
                    is_last = ci == len(children) - 1
                    for _r in range(crep):
                        blank = not _has_content(cell)
                        if blank and is_last:
                            break
                        cells.append(cell)
                        if not blank:
                            last_non_blank = len(cells)
                rows[i - start_row] = cells[:last_non_blank]
            i += 1
    size = 0
    for idx, r in enumerate(rows):
        if r:
            size = idx
    return rows[: size + 1]


def _ods_strings(path: str, start_row: int, stop_row: int, flat: bool) -> list[list[str]]:
    from lxml import etree

    if flat:
        root = etree.parse(path, parser=etree.XMLParser(huge_tree=True)).getroot()
    else:
        with zipfile.ZipFile(path) as zf:
            root = etree.fromstring(zf.read("content.xml"), parser=etree.XMLParser(huge_tree=True))
    body = root.find(f"{{{_OFFICE_NS}}}body")
    if body is None:
        raise ValueError(f"{path} is not a correct ODS file")
    sheet = body.find(f"{{{_OFFICE_NS}}}spreadsheet/{_t('table')}")
    if sheet is None:
        return []
    rows = _ods_rows(sheet, start_row, stop_row)
    width = max((len(r) for r in rows), default=0)
    if width * len(rows) == 0:
        return []
    return [[_ods_cell_value(c) for c in r] + [""] * (width - len(r)) for r in rows]


def read_ods(
    path: str | os.PathLike[str],
    col_names: bool = True,
    col_types: Any = None,
    skip: int = 0,
    n_max: float = math.inf,
    name_repair: str = "unique",
) -> pd.DataFrame:
    """``readODS::read_ods(path, sheet = 1)`` (``col_types = NA`` keeps text)."""
    path = str(path)
    flat = path.lower().endswith(".fods")
    stop_row = -1 if not math.isfinite(n_max) else int(n_max) + 1
    grid = _ods_strings(path, skip + 1, stop_row, flat)
    if not grid:
        return pd.DataFrame()
    if len(grid) == 1 and col_names:
        names = list(grid[0])
        final = vec_as_names_unique(names) if name_repair == "unique" else names
        final = [s.strip() for s in final]
        if name_repair == "unique":
            final = vec_as_names_unique(final)
        out = pd.DataFrame({j: pd.Series([], dtype="string") for j in range(len(final))})
        out.columns = pd.Index(final, dtype=object)
        return out
    header = grid[0] if col_names else [""] * len(grid[0])
    data = grid[1:] if col_names else grid
    names = vec_as_names_unique(header) if name_repair == "unique" else list(header)
    cols = [[r[j] for r in data] for j in range(len(header))]
    na_col_types = col_types is not None and (
        col_types is pd.NA or (isinstance(col_types, float) and math.isnan(col_types))
    )
    if na_col_types or not data:
        series = [pd.Series(c, dtype="string") for c in cols]
    else:
        guess_max = int(min(1000, n_max)) if math.isfinite(n_max) else 1000
        series = [_minty_convert(c, guess_max) for c in cols]
    names = [s.strip() for s in names]
    if name_repair == "unique":
        names = vec_as_names_unique(names)
    out = pd.DataFrame(dict(enumerate(series)))
    out.columns = pd.Index(names, dtype=object)
    return out


_MINTY_LOGICAL = {"T": True, "F": False, "TRUE": True, "FALSE": False, "true": True,
                  "false": False, "True": True, "False": False}  # fmt: skip
_RX_DOUBLE = _rx(r"[+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[Ii]nf|NaN|NA)")
_RX_NUMBER = _rx(r"-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]*)?|-?[0-9]+(?:,[0-9]+)+(?:\.[0-9]*)?")
_RX_DATE = _rx(r"([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})")
_RX_DATETIME = _rx(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):?([0-9]{2})(?::?([0-9]{2}(?:\.[0-9]*)?))?"
    r"(Z|[+-][0-9]{2}:?[0-9]{2})?"
)
_RX_TIME = _rx(r"([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}(?:\.[0-9]*)?))?(?: ?([AaPp][Mm]))?")


def _minty_is_double(s: str) -> bool:
    if len(s) > 1 and s[0] == "0" and s[1] != ".":
        return False
    if s.startswith("-0") and len(s) > 2 and s[2] != ".":
        return False
    return _RX_DOUBLE.fullmatch(s) is not None and s != "NA"


def _minty_guess(values: Sequence[str | None]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "logical"
    if all(v in _MINTY_LOGICAL for v in vals):
        return "logical"
    if all(_minty_is_double(v) for v in vals):
        return "double"
    if all(_minty_is_double(v) or _RX_NUMBER.fullmatch(v) for v in vals):
        return "number"
    if all(_RX_TIME.fullmatch(v) for v in vals):
        return "time"
    if all(_valid_date(_RX_DATE.fullmatch(v)) for v in vals):
        return "date"
    if all(_RX_DATETIME.fullmatch(v) or _valid_date(_RX_DATE.fullmatch(v)) for v in vals):
        return "datetime"
    return "character"


def _valid_date(m: Any) -> bool:
    if m is None:
        return False
    try:
        dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return True


def _minty_convert(values: list[str], guess_max: int) -> pd.Series:
    """minty::type_convert() of one character column (``na = ""``, trimmed)."""
    vals = [None if v is None or v.strip() == "" else v.strip() for v in values]
    kind = _minty_guess(vals[:guess_max])
    if kind == "logical":
        return pd.Series(
            [None if v is None else _MINTY_LOGICAL.get(v) for v in vals], dtype="boolean"
        )
    if kind in ("double", "number"):
        out = []
        for v in vals:
            if v is None:
                out.append(math.nan)
                continue
            try:
                out.append(float(v.replace(",", "")) if kind == "number" else float(v))
            except ValueError:
                out.append(math.nan)
        return pd.Series(out, dtype="float64")
    if kind == "date":
        dates: list[Any] = []
        for v in vals:
            m = _RX_DATE.fullmatch(v) if v is not None else None
            dates.append(
                dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if m is not None and _valid_date(m)
                else None
            )
        return pd.Series(dates, dtype=object)
    if kind == "datetime":
        stamps = pd.to_datetime(
            pd.Series(vals, dtype=object), utc=True, errors="coerce", format="ISO8601"
        )
        return stamps
    if kind == "time":
        secs = []
        for v in vals:
            m = _RX_TIME.fullmatch(v) if v is not None else None
            if m is None:
                secs.append(math.nan)
                continue
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3) or 0)
            ampm = (m.group(4) or "").lower()
            if ampm == "pm" and h < 12:
                h += 12
            if ampm == "am" and h == 12:
                h = 0
            secs.append(h * 3600 + mi * 60 + s)
        return pd.Series(secs, dtype="float64")
    return pd.Series(vals, dtype="string")


# -----------------------------------------------------------------------------
# data_read_head()'s spreadsheet branch
# -----------------------------------------------------------------------------


def read_sheet_head(
    path: str,
    ext: str,
    n_rows: float,
    sheet: str | int | None,
    is_qualtrics: Callable[[pd.DataFrame], bool],
    strip_qualtrics: Callable[[pd.DataFrame], pd.DataFrame],
    promote: Callable[[pd.DataFrame, pd.DataFrame], dict[str, Any]],
) -> pd.DataFrame | None:
    """The ``xlsx``/``xls`` and ``ods``/``fods`` branches of ``data_read_head()``.

    Read, strip Qualtrics header rows, and -- when a mis-placed header is
    detected in the first raw rows -- re-read with ``skip = k``.
    """
    nmax = n_rows if math.isfinite(n_rows) else math.inf
    excel = ext in ("xlsx", "xls")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if excel:
            df = read_excel(path, sheet=sheet if sheet is not None else 1, n_max=nmax)
        else:
            df = read_ods(path, n_max=nmax)
    if df is not None and is_qualtrics(df):
        df = strip_qualtrics(df)
    if df is not None and df.shape[1] > 1:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if excel:
                    raw = read_excel(
                        path,
                        sheet=sheet if sheet is not None else 1,
                        col_names=False,
                        n_max=6,
                        col_types="text",
                        name_repair="minimal",
                    )
                else:
                    raw = read_ods(
                        path, col_names=False, n_max=6, col_types=math.nan, name_repair="minimal"
                    )
        except Exception:
            raw = None
        if raw is not None and len(raw) >= 2:
            prom = promote(df, raw)
            k = int(prom.get("promoted", 0) or 0)
            if k > 0:
                nmax2 = nmax + k if math.isfinite(nmax) else math.inf
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        if excel:
                            reread = read_excel(
                                path, sheet=sheet if sheet is not None else 1, skip=k, n_max=nmax2
                            )
                        else:
                            reread = read_ods(path, skip=k, n_max=nmax2)
                except Exception:
                    reread = None
                df = reread if reread is not None and reread.shape[1] > 0 else prom["df"]
    return df


# -----------------------------------------------------------------------------
# haven (SPSS / Stata / SAS)
# -----------------------------------------------------------------------------


def _haven_format(ext: str, fmt: str | None) -> tuple[str, str] | None:
    if fmt is None:
        return None
    if ext in ("sav", "por"):
        return "format.spss", fmt
    if ext == "dta":
        return "format.stata", fmt
    m = _rx(r"\$?[A-Za-z_]*").match(fmt)
    name = fmt if m is None else (fmt[: m.end()] or fmt)
    return "format.sas", name


_HAVEN_VENDOR = {"sav": "spss", "por": "spss", "dta": "stata", "sas7bdat": "sas"}

# haven_types.cpp numType(): format prefix -> Date / hms / POSIXct
_HAVEN_NUM_TYPES = {
    "sas": (
        ("DATETIME", "datetime"), ("IS8601DT", "datetime"), ("E8601DT", "datetime"),
        ("B8601DT", "datetime"), ("IS8601DA", "date"), ("E8601DA", "date"),
        ("B8601DA", "date"), ("WEEKDATE", "date"), ("MMDDYY", "date"), ("DDMMYY", "date"),
        ("YYMMDD", "date"), ("DATE", "date"), ("TIME", "time"), ("HHMM", "time"),
        ("IS8601TM", "time"), ("E8601TM", "time"), ("B8601TM", "time"),
    ),
    "spss": (
        ("DATETIME", "datetime"), ("DATE", "date"), ("ADATE", "date"), ("EDATE", "date"),
        ("JDATE", "date"), ("SDATE", "date"), ("TIME", "time"), ("DTIME", "time"),
    ),
    "stata": (("%tC", "datetime"), ("%tc", "datetime"), ("%td", "date"), ("%d", "date")),
}  # fmt: skip
# daysOffset(): the vendor's epoch in days before 1970-01-01
_HAVEN_DAYS_OFFSET = {"sas": 3653, "stata": 3653, "spss": 141428}


def _haven_num_type(vendor: str, fmt: str | None) -> str:
    if not fmt:
        return "default"
    for prefix, kind in _HAVEN_NUM_TYPES[vendor]:
        if fmt.startswith(prefix):
            return kind
    return "default"


def _haven_numbers(values: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = math.nan
        out.append(f)
    return out


def _haven_dates(days: list[float]) -> pd.Series:
    epoch = dt.date(1970, 1, 1)
    out: list[dt.date | None] = []
    for d in days:
        try:
            out.append(None if math.isnan(d) else epoch + dt.timedelta(days=math.floor(d)))
        except OverflowError:  # beyond datetime.date's years 1-9999
            out.append(None)
    return pd.Series(out, dtype=object)


def read_stat_file(path: str, ext: str, n_rows: float) -> pd.DataFrame:
    """``haven::read_sav/read_dta/read_sas/read_por(path, n_max = n_rows)``.

    Follows haven's ``DfReader``: a column's type comes from the file's
    variable type (numbers are always doubles, strings character) and its
    class from the display format (``numType()``: ``Date`` as
    :class:`datetime.date`, ``POSIXct`` as UTC timestamps, ``hms`` as seconds),
    never from the values; user-defined missing values become ``NA``. The
    variable label, ``format.*``, SPSS ``display_width`` (when not 8) and value
    labels (``haven_labelled`` unless the column already has a date/time
    class) go to ``attrs["col_attrs"]``; names get vctrs "unique" repair.
    """
    import pyreadstat

    readers: dict[str, Callable[..., Any]] = {
        "sav": pyreadstat.read_sav,
        "dta": pyreadstat.read_dta,
        "sas7bdat": pyreadstat.read_sas7bdat,
        "por": pyreadstat.read_por,
    }
    vendor = _HAVEN_VENDOR[ext]
    limit = 0 if not math.isfinite(n_rows) else max(int(n_rows), 0)
    kwargs: dict[str, Any] = {"row_limit": limit, "disable_datetime_conversion": True}
    if limit == 0 and math.isfinite(n_rows):
        kwargs["metadataonly"] = True
    df, meta = readers[ext](path, **kwargs)
    nrow = 0 if kwargs.get("metadataonly") else len(df)
    raw_names = list(meta.column_names)
    names = vec_as_names_unique(raw_names)
    value_labels = meta.variable_value_labels or {}
    var_labels = meta.column_names_to_labels or {}
    formats = meta.original_variable_types or {}
    rs_types = getattr(meta, "readstat_variable_types", None) or {}
    widths = getattr(meta, "variable_display_width", None) or {}
    col_attrs: dict[str, dict[str, Any]] = {}
    series: dict[int, pd.Series] = {}
    offset = _HAVEN_DAYS_OFFSET[vendor]
    for j, name in enumerate(raw_names):
        values = df[name].tolist() if name in df.columns and nrow else []
        is_string = rs_types.get(name) == "string"
        attrs: dict[str, Any] = {}
        if var_labels.get(name):
            attrs["label"] = var_labels[name]
        fmt = _haven_format(ext, formats.get(name))
        kind = "default" if is_string else _haven_num_type(vendor, fmt[1] if fmt else None)
        if is_string:
            series[j] = pd.Series(
                [None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)
                 for v in values],
                dtype="string",
            )  # fmt: skip
        else:
            nums = _haven_numbers(values)
            if kind == "datetime":
                secs = [x / 1000 if vendor == "stata" else x for x in nums]
                secs = [x - offset * 86400 for x in secs]
                from pytacheck.datacheck._files_time import posixct_series

                series[j] = posixct_series(secs)
                attrs["class"] = ["POSIXct", "POSIXt"]
                attrs["tzone"] = "UTC"
            elif kind == "date":
                days = [x / 86400 if vendor == "spss" else x for x in nums]
                series[j] = _haven_dates([x - offset for x in days])
                attrs["class"] = "Date"
            else:
                series[j] = pd.Series(nums, dtype="float64")
                if kind == "time":
                    attrs["class"] = ["hms", "difftime"]
                    attrs["units"] = "secs"
        if fmt:
            attrs[fmt[0]] = fmt[1]
        width = widths.get(name)
        if vendor == "spss" and isinstance(width, int) and width != 8:
            attrs["display_width"] = width  # haven skips the default width 8
        labels = value_labels.get(name)
        if labels:
            if "class" not in attrs:
                attrs["class"] = [
                    "haven_labelled",
                    "vctrs_vctr",
                    "character" if is_string else "double",
                ]
            attrs["labels"] = {
                str(lab): (str(code) if is_string else float(code)) for code, lab in labels.items()
            }
        if attrs:
            col_attrs[names[j]] = attrs
    out = pd.DataFrame(series, index=range(nrow))
    out.columns = pd.Index(names, dtype=object)
    if col_attrs:
        out.attrs["col_attrs"] = col_attrs
    return out


def read_jasp_omv(path: str, ext: str, n_rows: float) -> pd.DataFrame | None:
    """``import_jasp(path)$data`` / ``import_omv(path)$data``, head of *n_rows*."""
    if ext == "jasp":
        from pytacheck.statout.jasp import import_jasp as importer
    else:
        from pytacheck.statout.omv import import_omv as importer
    res = importer(path)
    df = res.get("data") if isinstance(res, dict) else getattr(res, "data", None)
    if isinstance(df, pd.DataFrame) and math.isfinite(n_rows):
        return df.head(int(n_rows))
    return df


# -----------------------------------------------------------------------------
# R data
# -----------------------------------------------------------------------------


def read_rds_head(path: str, n_rows: float) -> pd.DataFrame | None:
    """``obj <- readRDS(path); if (is.data.frame(obj)) head(obj, n_rows) else NULL``."""
    from pytacheck.datacheck._files_rdata import is_data_frame, r_frame_to_pandas, read_rds

    obj = read_rds(path)
    if not is_data_frame(obj):
        return None
    return r_frame_to_pandas(obj, n_rows)


def read_rdata_first_df(path: str | os.PathLike[str], n_rows: float) -> pd.DataFrame | None:
    """The first data frame ``as.list()`` lists after ``load()``, head of *n_rows*."""
    from pytacheck.datacheck._files_rdata import r_frame_to_pandas, workspace_first_data_frame

    obj = workspace_first_data_frame(Path(path))
    if obj is None:
        return None
    # The R child process only calls head() for a finite n_rows, and it has no
    # vctrs methods loaded (so haven-labelled columns lose their attributes).
    return r_frame_to_pandas(obj, n_rows, subset=math.isfinite(n_rows), vctrs=False)


__all__ = [
    "as_character",
    "read_delim",
    "read_excel",
    "read_jasp_omv",
    "read_ods",
    "read_rdata_first_df",
    "read_rds_head",
    "read_sheet_head",
    "read_stat_file",
    "vec_as_names_unique",
]
