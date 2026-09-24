"""Codebook file parsing (part of :mod:`pytacheck.datacheck.columns`).

Port of ``parse_codebook()`` and its file-format back-ends in
``R/data_check_helpers.R``: delimited text (``utils::read.delim()`` via
:mod:`pytacheck.datacheck._files_readtable`), JSON, markdown pipe tables,
Excel / OpenDocument spreadsheets (every sheet), haven / JASP / jamovi
embedded labels, Qualtrics ``.qsf``, and plain-text extraction of docx / pdf /
rtf / odt for the LLM tier.

PDF text is read with poppler's ``pdftotext -layout`` (what ``pdftools``
wraps), found on ``PATH``, via ``$PYTACHECK_PDFTOTEXT``, or next to
``$PYTACHECK_RSCRIPT``; without it ``pypdf``'s layout mode is used.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import trimws
from pytacheck._r.frames import bind_rows
from pytacheck._r.regex import grep, gsub, regextract, strsplit, sub
from pytacheck.datacheck._columns_labels import (
    _cb_is_definition_line,
    _chr,
    _chr_vec,
    _encode_value_labels,
    _extract_codebook_positional,
    _extract_haven_labels,
    _extract_structured_codebook,
    _find_codebook_cols,
    _json_loads,
    _json_scalar_chr,
    _JsonObject,
    _missing_from_value_labels,
    _na,
    _tolower,
    _trim,
    _unlist_scalars,
    _vl_split_pairs,
    chr_frame,
)

# -----------------------------------------------------------------------------
# Small R emulations
# -----------------------------------------------------------------------------


def _file_ext(path: str) -> str:
    """``tools::file_ext()``."""
    m = regextract(r"\.([[:alnum:]]+)$", path)
    return m[1:] if m is not None else ""


def _basename(path: str | os.PathLike[str]) -> str:
    """R ``basename()`` (trailing separators removed first)."""
    s = os.fspath(path).rstrip("/")
    return s.rsplit("/", 1)[-1] if s else ""


def _split_lines(data: bytes) -> list[bytes]:
    """Lines as R ``readLines()`` splits them (LF, CRLF, CR; NUL ends a line)."""
    out: list[bytes] = []
    i, n = 0, len(data)
    start = 0
    while i < n:
        c = data[i]
        if c == 0x0A or c == 0x0D:
            out.append(data[start:i])
            if c == 0x0D and i + 1 < n and data[i + 1] == 0x0A:
                i += 1
            start = i + 1
        i += 1
    if start < n:
        out.append(data[start:])
    return [ln.split(b"\0", 1)[0] for ln in out]


def _read_lines_bytes(path: str | os.PathLike[str], n: int = -1) -> list[bytes]:
    data = Path(path).read_bytes()
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    lines = _split_lines(data)
    return lines if n < 0 else lines[:n]


def _read_lines(path: str | os.PathLike[str], n: int = -1) -> list[str]:
    """R ``readLines(path, warn = FALSE)`` in a UTF-8 locale (bytes kept as-is)."""
    return [ln.decode("utf-8", "surrogateescape") for ln in _read_lines_bytes(path, n)]


def _r_split(s: str, sep: str = "\n") -> list[str]:
    """``strsplit(s, sep)[[1]]`` for a literal separator (no trailing empty piece)."""
    return [p for p in strsplit(s, sep, fixed=True) if p is not None]


# -----------------------------------------------------------------------------
# Markdown pipe tables
# -----------------------------------------------------------------------------

_RX_MD_RULE = "^[[:space:]]*[|][[:space:]:-]*[-][[:space:]:|-]*$"


def _md_cells(x: str) -> list[str]:
    x = sub("^[[:space:]]*[|]", "", x)
    x = sub("[|][[:space:]]*$", "", x)
    return [trimws(p) for p in strsplit(x, "[|]") if p is not None]


def _extract_markdown_codebook(
    path: str | os.PathLike[str], src: str, observed: Any = None
) -> pd.DataFrame | None:
    """Parse a GitHub-flavoured markdown pipe table into a codebook.

    Port of ``.extract_markdown_codebook()``: every separator rule whose
    preceding row is a codebook header starts a table, read until the first
    line without a ``|``.
    """
    try:
        raw = _read_lines_bytes(path)
    except OSError:
        return None
    if not raw:
        return None
    ln = [b.decode("utf-8", "ignore") for b in raw]
    rules = [i for i in grep(_RX_MD_RULE, ln) if i > 0]
    if not rules:
        return None
    out: list[pd.DataFrame] = []
    for i in rules:
        hdr = [h for h in _md_cells(ln[i - 1]) if h != ""]
        if len(hdr) < 2 or _find_codebook_cols(hdr) is None:
            continue
        body: list[list[str | None]] = []
        j = i + 1
        while j < len(ln) and "|" in ln[j]:
            rw: list[str | None] = list(_md_cells(ln[j]))
            if any(c != "" for c in rw if c is not None):
                rw = (rw + [None] * len(hdr))[: len(hdr)]
                body.append(rw)
            j += 1
        if not body:
            continue
        cols = {k: pd.Series(["" if r[k] is None else r[k] for r in body], dtype="string")
                for k in range(len(hdr))}  # fmt: skip
        df = pd.DataFrame(cols)
        df.columns = pd.Index(hdr, dtype=object)
        one = _extract_structured_codebook(df, src, observed)
        if one is not None and len(one) > 0:
            out.append(one)
    if not out:
        return None
    return bind_rows(out)


# -----------------------------------------------------------------------------
# JSON codebooks
# -----------------------------------------------------------------------------


def _json_field(entry: Any, names_want: list[str]) -> str | None:
    """Port of ``.json_field()``: first non-empty scalar among candidate field names."""
    if not isinstance(entry, _JsonObject):
        return None
    lowered = [k.lower() for k in entry.names()]
    for w in names_want:
        if w not in lowered:
            continue
        v = entry[lowered.index(w)][1]
        if v is None or isinstance(v, list):
            continue
        s = _json_scalar_chr(v)
        s = None if s is None else trimws(s)
        if s is not None and s != "":
            return s
    return None


def _json_value_labels(vl: Any) -> str | None:
    """Port of ``.json_value_labels()``: a JSON codebook's value-label field as JSON.

    Accepts ``[{"name": "1", "label": "Male"}, ...]``, ``{"1": "Male"}`` and
    ``["1 = Male", ...]``; an empty per-entry label falls back to the code.
    """
    if vl is None or (isinstance(vl, list) and len(vl) == 0):
        return None
    if isinstance(vl, _JsonObject) and not any(isinstance(v, list) for v in vl.values_()):
        return _encode_value_labels(vl.names(), _unlist_scalars(vl.values_()))
    items = vl.values_() if isinstance(vl, _JsonObject) else vl if isinstance(vl, list) else [vl]
    codes: list[str] = []
    labs: list[str] = []
    for v in items:
        if isinstance(v, list):
            cd = _json_field(v, ["name", "value", "code", "level"])
            lb = _json_field(v, ["label", "meaning", "text"])
            if cd is None:
                continue
            codes.append(cd)
            labs.append(cd if lb is None else lb)
        elif v is not None:
            pr = _vl_split_pairs(_json_scalar_chr(v))  # type: ignore[arg-type]
            if pr is not None and pr["lhs"]:
                codes += pr["lhs"]
                labs += pr["rhs"]
    if not codes:
        return None
    return _encode_value_labels(codes, labs)


def _read_json_file(path: str | os.PathLike[str]) -> Any:
    """``jsonlite::fromJSON(path, simplifyVector = FALSE)`` (a UTF-8 BOM is tolerated)."""
    text = Path(path).read_bytes().decode("utf-8", "surrogateescape")
    if text.startswith("\ufeff"):
        text = text[1:]
    return _json_loads(text)


_JSON_CONTAINERS = ("variableMeasured", "variables", "columns", "fields", "codebook", "items")


def _extract_json_codebook(path: str | os.PathLike[str], src: str) -> pd.DataFrame | None:
    """Parse a JSON codebook (an array of per-variable objects, or a wrapper object).

    Port of ``.extract_json_codebook()``; field names are matched leniently.
    """
    try:
        j = _read_json_file(path)
    except (ValueError, OSError):
        return None
    if j is None:
        return None
    if isinstance(j, _JsonObject) and j:
        names = [k.lower() for k in j.names()]
        for k in _JSON_CONTAINERS:
            if k.lower() in names:
                val = j[names.index(k.lower())][1]
                if isinstance(val, list):
                    j = val
                    break
    if not isinstance(j, list) or len(j) == 0:
        return None
    items = j.values_() if isinstance(j, _JsonObject) else j
    entries = [e for e in items if isinstance(e, _JsonObject)]
    if len(entries) < 2:
        return None
    var = [_json_field(e, ["name", "variable", "variable_name", "varname", "column", "id"])
           for e in entries]  # fmt: skip
    lab = [_json_field(e, ["label", "description", "title", "variable_label", "definition"])
           for e in entries]  # fmt: skip
    itm = [_json_field(e, ["item_text", "question", "question_text", "wording", "prompt"])
           for e in entries]  # fmt: skip
    lab = [i if lb is None and i is not None else lb for lb, i in zip(lab, itm, strict=True)]
    keep = [v is not None and v != "" and lb is not None and lb != ""
            for v, lb in zip(var, lab, strict=True)]  # fmt: skip
    if sum(keep) < 2:
        return None
    vl_keys = ("value_label", "value_labels", "values", "levels", "categories")
    vl: list[str | None] = []
    for e in entries:
        f = [k for k in e.names() if k.lower() in vl_keys]
        if not f:
            vl.append(None)
            continue
        val = next(v for k, v in e if k == f[0])
        vl.append(_json_value_labels(val))
    idx = [i for i, k in enumerate(keep) if k]
    kept_vl = [vl[i] for i in idx]
    return chr_frame(
        {
            "codebook_variable": [var[i] for i in idx],
            "label": [lab[i] for i in idx],
            "codebook_source": src,
            "group": None,
            "value_labels": kept_vl,
            "missing_values": [_missing_from_value_labels(v) for v in kept_vl],
            "question": [itm[i] for i in idx],
            "coding_instructions": None,
        },
        len(idx),
    )


# -----------------------------------------------------------------------------
# Spreadsheets (Excel / OpenDocument)
# -----------------------------------------------------------------------------


def _unlist_row(df: pd.DataFrame, k: int) -> list[str | None]:
    """``as.character(unlist(df[k, ]))``: NA stays NA, numbers as R prints them."""
    return [_chr(v) for v in df.iloc[k].tolist()]


def _extract_spreadsheet_codebook(
    sheets: Callable[[], list[str]],
    read: Callable[[str | None, bool], pd.DataFrame | None],
    src: str,
    observed: Any,
    header_lookahead: int,
) -> pd.DataFrame | None:
    """Parse a multi-sheet spreadsheet codebook, format-agnostically.

    Port of ``.extract_spreadsheet_codebook()``: per sheet, the named-header
    parser, then a header-row lookahead, then the positional fallback.
    *sheets* returns the sheet names; ``read(sheet, header)`` one sheet
    (``sheet = None`` is the default sheet).
    """
    try:
        sh_names: list[str | None] = list(sheets())
    except Exception:
        sh_names = []
    if not sh_names:
        sh_names = [None]
    parsed: list[pd.DataFrame] = []
    for sh in sh_names:
        try:
            df = read(sh, True)
        except Exception:
            df = None
        if df is None:
            continue
        ssrc = src if sh is None else f"{src} [{sh}]"
        one = _extract_structured_codebook(df, ssrc, observed)
        if one is None:
            try:
                hdrless = read(sh, False)
            except Exception:
                hdrless = None
            if hdrless is not None and len(hdrless) > 1:
                for k in range(min(len(hdrless) - 1, header_lookahead)):
                    hdr = [_trim(v) for v in _unlist_row(hdrless, k)]
                    if _find_codebook_cols(hdr) is None:
                        continue
                    sub_df = hdrless.iloc[k + 1 :].reset_index(drop=True)
                    sub_df.columns = pd.Index(hdr, dtype=object)
                    one = _extract_structured_codebook(sub_df, ssrc, observed)
                    if one is not None:
                        break
        if one is None:
            one = _extract_codebook_positional(df, ssrc)
        if one is not None and len(one) > 0:
            parsed.append(one)
    return bind_rows(parsed) if parsed else None


def _excel_sheets(path: str) -> list[str]:
    from pytacheck.datacheck._files_readers import _XlsBook, _XlsxBook

    book = _XlsBook(path) if path.lower().endswith(".xls") else _XlsxBook(path)
    try:
        return list(book.sheet_names)
    finally:
        zf = getattr(book, "zf", None)
        if zf is not None:
            zf.close()


def _ods_root(path: str) -> Any:
    from lxml import etree

    parser = etree.XMLParser(huge_tree=True)
    if path.lower().endswith(".fods"):
        return etree.parse(path, parser=parser).getroot()
    with zipfile.ZipFile(path) as zf:
        return etree.fromstring(zf.read("content.xml"), parser=parser)


def _ods_tables(path: str) -> list[Any]:
    from pytacheck.datacheck._files_readers import _OFFICE_NS, _t

    root = _ods_root(path)
    body = root.find(f"{{{_OFFICE_NS}}}body")
    if body is None:
        raise ValueError(f"{path} is not a correct ODS file")
    return list(body.iterfind(f"{{{_OFFICE_NS}}}spreadsheet/{_t('table')}"))


def _ods_sheets(path: str) -> list[str]:
    """``readODS::list_ods_sheets()``."""
    from pytacheck.datacheck._files_readers import _t

    return [str(t.get(_t("name")) or "") for t in _ods_tables(path)]


def _read_ods_sheet(path: str, sheet: str | None, col_names: bool) -> pd.DataFrame:
    """``readODS::read_ods(path, sheet, col_names, .name_repair = "unique_quiet")``."""
    from pytacheck.datacheck._files_readers import (
        _minty_convert,
        _ods_cell_value,
        _ods_rows,
        _t,
        vec_as_names_unique,
    )

    tables = _ods_tables(path)
    if sheet is None:
        idx = 0
    else:
        names = [str(t.get(_t("name")) or "") for t in tables]
        if sheet not in names:
            raise ValueError(f"No sheet found with name '{sheet}'")
        idx = names.index(sheet)
    if idx >= len(tables):
        return pd.DataFrame()
    rows = _ods_rows(tables[idx], 1, -1)
    width = max((len(r) for r in rows), default=0)
    if width * len(rows) == 0:
        return pd.DataFrame()
    grid = [[_ods_cell_value(c) for c in r] + [""] * (width - len(r)) for r in rows]
    if len(grid) == 1 and col_names:
        final = [s.strip() for s in vec_as_names_unique(list(grid[0]))]
        final = vec_as_names_unique(final)
        out = pd.DataFrame({j: pd.Series([], dtype="string") for j in range(len(final))})
        out.columns = pd.Index(final, dtype=object)
        return out
    header = grid[0] if col_names else [""] * len(grid[0])
    data = grid[1:] if col_names else grid
    names_ = vec_as_names_unique(header)
    cols = [[r[j] for r in data] for j in range(len(header))]
    series = (
        [_minty_convert(c, 1000) for c in cols]
        if data
        else [pd.Series(c, dtype="string") for c in cols]
    )
    names_ = vec_as_names_unique([s.strip() for s in names_])
    out = pd.DataFrame(dict(enumerate(series)))
    out.columns = pd.Index(names_, dtype=object)
    return out


# -----------------------------------------------------------------------------
# PDF / rich text
# -----------------------------------------------------------------------------


def _pdftotext() -> str | None:
    """The ``pdftotext`` executable (PATH, ``$PYTACHECK_PDFTOTEXT``, next to Rscript)."""
    exe = os.environ.get("PYTACHECK_PDFTOTEXT")
    if exe and Path(exe).exists():
        return exe
    found = shutil.which("pdftotext")
    if found:
        return found
    rscript = os.environ.get("PYTACHECK_RSCRIPT")
    if rscript:
        cand = Path(rscript).parent / "pdftotext"
        if cand.exists():
            return str(cand)
    return None


def _pdf_text(path: str | os.PathLike[str]) -> list[str] | None:
    """``pdftools::pdf_text()``: one string per page (``None`` on failure)."""
    exe = _pdftotext()
    if exe is not None:
        try:
            res = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                [exe, "-layout", "-enc", "UTF-8", "-q", os.fspath(path), "-"],
                capture_output=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode != 0:
            return None
        pages = res.stdout.decode("utf-8", "replace").split("\f")
        if pages and pages[-1] == "":
            pages = pages[:-1]
        return pages
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - optional dependency
        return None
    try:
        reader = PdfReader(os.fspath(path))
        return [p.extract_text(extraction_mode="layout") + "\n" for p in reader.pages]
    except Exception:
        return None


def _nonblank_lines(text: str) -> list[str]:
    return [ln for ln in _r_split(text, "\n") if trimws(ln) != ""]


def _pdf_codebook_lines(
    path: str | os.PathLike[str], probe_pages: int = 5, min_defs: int = 5, max_pages: int = 60
) -> list[str]:
    """Decide whether a PDF is worth sending to the LLM, and return its lines if so.

    Port of ``.pdf_codebook_lines()``: at most *max_pages* pages, and one of the
    first *probe_pages* pages must hold at least *min_defs* definition-looking
    lines. Returns the non-blank lines, or ``[]`` (R ``character(0)``).
    """
    pages = _pdf_text(path)
    if not pages:
        return []
    if len(pages) > max_pages:
        return []
    n_def = []
    for t in pages[:probe_pages]:
        ln = _nonblank_lines(t)
        n_def.append(sum(_cb_is_definition_line(ln)) if ln else 0)
    if max([0, *n_def]) < min_defs:
        return []
    return _nonblank_lines("\n".join(pages))


def _strip_rtf(text: str) -> str:
    """Port of ``.strip_rtf()``: strip RTF control words and groups."""
    text = gsub(r"\\[a-z]+\-?[0-9]*\s?", " ", text)
    text = gsub("\\\\[^a-z\n]", " ", text)
    text = gsub("[{}]", "", text)
    text = gsub(r"\s+", " ", text)
    return trimws(text)


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_text(path: str | os.PathLike[str]) -> str:
    """``officer::docx_summary(read_docx(path))$text`` joined as ``.extract_rich_text()`` does.

    One entry per paragraph (runs' ``w:t`` text, tabs, breaks, hyphens, field
    codes): body paragraphs in document order, then table-cell paragraphs.
    """
    from lxml import etree

    with zipfile.ZipFile(path) as zf:
        root = etree.fromstring(
            zf.read("word/document.xml"), parser=etree.XMLParser(huge_tree=True)
        )
    p_tag, r_tag, rpr_tag = f"{{{_W}}}p", f"{{{_W}}}r", f"{{{_W}}}rPr"
    doc_index = {p: i + 1 for i, p in enumerate(root.iter(p_tag))}
    in_cell = set(root.xpath("//w:tbl/w:tr/w:tc/w:p", namespaces={"w": _W}))
    pieces: dict[int | None, list[str]] = {}
    for r in root.iter(r_tag):
        parent = r.getparent()
        gp = parent.getparent() if parent is not None else None
        if parent is not None and parent.tag == p_tag:
            di: int | None = doc_index[parent]
        elif gp is not None and gp.tag == p_tag:
            di = doc_index[gp]
        else:
            di = None
        for child in r:
            if not isinstance(child.tag, str) or child.tag == rpr_tag:
                continue
            name = etree.QName(child).localname
            if name == "t" or name == "instrText":
                txt = "".join(child.itertext())
            elif name == "noBreakHyphen":
                txt = "-"
            elif name in ("br", "cr"):
                txt = "\n"
            elif name == "tab":
                txt = "\t"
            elif name == "sym":
                txt = next((v for k, v in child.attrib.items() if k.endswith("char")), "") or ""
            else:
                txt = ""
            pieces.setdefault(di, []).append(txt)
    cell_idx = {doc_index[p] for p in in_cell}
    par = sorted(k for k in pieces if k is not None and k not in cell_idx)
    if None in pieces:
        par.append(None)  # type: ignore[arg-type]
    tbl = sorted(k for k in pieces if k is not None and k in cell_idx)
    texts = ["".join(pieces[k]) for k in [*par, *tbl]]
    return "\n".join(t for t in texts if trimws(t) != "")


def _odt_text(path: str | os.PathLike[str]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with zipfile.ZipFile(path) as zf:
                if "content.xml" not in zf.namelist():
                    return ""
                zf.extract("content.xml", tmp)
            xml_path = Path(tmp) / "content.xml"
            if not xml_path.exists():
                return ""
            raw = "\n".join(_read_lines(xml_path))
        except Exception:
            return ""
        txt = gsub("<[^>]+>", " ", raw)
        for a, b in (
            ("&amp;", "&"),
            ("&lt;", "<"),
            ("&gt;", ">"),
            ("&apos;", "'"),
            ("&quot;", '"'),
        ):
            txt = txt.replace(a, b)
        return trimws(gsub(r"\s+", " ", txt))


def _extract_rich_text(path: str | os.PathLike[str], ext: str) -> str:
    """Port of ``.extract_rich_text()``: plain text of a docx / pdf / rtf / odt file.

    Returns ``""`` on any failure.
    """
    try:
        if ext == "docx":
            return _docx_text(path)
        if ext == "pdf":
            pages = _pdf_text(path)
            if pages is None:
                return ""
            return "\n".join(pages)
        if ext == "rtf":
            return _strip_rtf("\n".join(_read_lines(path)))
        if ext == "odt":
            return _odt_text(path)
    except Exception:
        return ""
    return ""


# -----------------------------------------------------------------------------
# Delimited text
# -----------------------------------------------------------------------------

_WIDE_STATS = frozenset({"mean", "sd", "se", "min", "max", "median", "n"})


def _is_character(s: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(s.dtype) or (
        s.dtype == object and all(isinstance(v, str) or _na(v) for v in s.tolist())
    )


def _row_as_character(df: pd.DataFrame, k: int) -> list[str | None]:
    """``as.character(df[k, ])``: a character NA stays NA, any other NA is ``"NA"``."""
    out: list[str | None] = []
    for j in range(df.shape[1]):
        col = df.iloc[:, j]
        v = col.iloc[k]
        if _na(v):
            out.append(None if _is_character(col) else "NA")
        else:
            out.append(_chr(v))
    return out


def _format_column(col: pd.Series) -> list[str | None]:
    """``format()`` of a non-character column inside ``as.matrix()`` (NA kept)."""
    vals = col.tolist()
    if pd.api.types.is_bool_dtype(col.dtype):
        txt = [None if _na(v) else ("TRUE" if v else "FALSE") for v in vals]
    elif pd.api.types.is_integer_dtype(col.dtype):
        txt = [None if _na(v) else str(int(v)) for v in vals]
    else:
        from pytacheck._r.base import format_num

        fin = [float(v) for v in vals if not _na(v)]
        decimals = 0
        for x in fin:
            s = format_num(x, 7)
            if "e" not in s and "." in s:
                decimals = max(decimals, len(s.split(".")[1]))
        txt = [None if _na(v) else f"{float(v):.{decimals}f}" for v in vals]
    width = max((len(t) for t in txt if t is not None), default=0)
    return [None if t is None else t.rjust(width) for t in txt]


def _transpose_wide(raw: pd.DataFrame) -> pd.DataFrame:
    """The wide-format (stats-as-rows) transposition of ``parse_codebook()``."""
    var_names = _row_as_character(raw, 0)
    stat_names = _chr_vec(raw.iloc[:, 0])
    rest = raw.iloc[:, 1:]
    numeric = all(
        pd.api.types.is_numeric_dtype(rest.iloc[:, j].dtype) for j in range(rest.shape[1])
    )
    if numeric:
        mat = [
            [None if _na(v) else v for v in rest.iloc[:, j].tolist()] for j in range(rest.shape[1])
        ]
        mk: Callable[[list[Any]], pd.Series] = lambda v: pd.Series(v, dtype="float64")  # noqa: E731
    else:
        mat = [
            _chr_vec(rest.iloc[:, j])
            if _is_character(rest.iloc[:, j])
            else _format_column(rest.iloc[:, j])
            for j in range(rest.shape[1])
        ]
        mk = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    nrow = len(mat)
    ncol = len(raw)
    tcols = [mk([mat[r][c] for r in range(nrow)]) for c in range(ncol)]
    out = pd.DataFrame({0: pd.Series(var_names[1:], dtype="string")})
    for c in range(ncol):
        out[c + 1] = tcols[c].values
        if not numeric:
            out[c + 1] = out[c + 1].astype("string")
    tnames = (stat_names[1:] + [None] * ncol)[:ncol]  # R pads short names with NA
    out.columns = pd.Index(["variable", *tnames], dtype=object)
    return out


def _read_delim_codebook(path: str, sep: str, encoding: str | None) -> pd.DataFrame | None:
    """``read.delim(path, sep, header = FALSE, check.names = FALSE, fileEncoding)``."""
    from pytacheck.datacheck._files_readtable import read_table

    try:
        if encoding == "UTF-8-BOM":
            data = Path(path).read_bytes()[3:]
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "codebook.txt"
                p.write_bytes(data)
                return read_table(p, sep, False)
        return read_table(path, sep, False, encoding=encoding)
    except Exception:
        return None


def _has_invalid_utf8(df: pd.DataFrame) -> bool:
    for j in range(df.shape[1]):
        col = df.iloc[:, j]
        if not _is_character(col):
            continue
        for v in col.tolist():
            if isinstance(v, str) and any("\udc80" <= ch <= "\udcff" for ch in v):
                return True
    return False


def _parse_delimited(path: str, ext: str, src: str, observed: Any, header_lookahead: int) -> Any:
    from pytacheck.datacheck.files import _sniff_delimiter

    try:
        h = [trimws(x) for x in _read_lines(path, 5)]
        h = [x for x in h if x != ""]
        first = h[0][:1] if h else ""
    except Exception:
        first = ""
    if first in ("[", "{"):
        jres = _extract_json_codebook(path, src)
        if jres is not None:
            jres = jres.copy()
            jres["parse_method"] = pd.Series(["structured"] * len(jres), dtype="string")
            return ("return", jres)
    sep = "\t" if ext == "tsv" else _sniff_delimiter(path)
    try:
        b = Path(path).read_bytes()[:3]
    except OSError:
        b = b""
    enc = "UTF-8-BOM" if b == b"\xef\xbb\xbf" else None
    raw = _read_delim_codebook(path, sep, enc)
    if raw is None or len(raw) == 0:
        return None
    if _has_invalid_utf8(raw):
        raw = _read_delim_codebook(path, sep, "latin1")
    if raw is None or len(raw) == 0:
        return None
    col1 = [_tolower(trimws(v)) if v is not None else None for v in _chr_vec(raw.iloc[:, 0])]
    col1 = [v for v in col1 if v is None or v != ""]
    if col1 and sum(v in _WIDE_STATS for v in col1 if v is not None) / len(col1) >= 0.5:
        raw = _transpose_wide(raw)
    header_row = None
    for k in range(min(len(raw), header_lookahead)):
        hdr = [_trim(v) for v in _row_as_character(raw, k)]
        if _find_codebook_cols(hdr) is not None:
            header_row = k
            break
    if header_row is None:
        return None
    names = [_trim(v) for v in _row_as_character(raw, header_row)]
    nrow = len(raw)
    if header_row + 1 < nrow:
        df = raw.iloc[header_row + 1 :]
    else:
        # R: raw[seq(n + 1, n), ] is an all-NA row followed by the header row
        na_row = pd.DataFrame(
            {j: pd.Series([None], dtype=raw.iloc[:, j].dtype) for j in range(raw.shape[1])}
        )
        last = raw.iloc[[header_row]].copy()
        last.columns = range(raw.shape[1])
        df = pd.concat([na_row, last], ignore_index=True)
    df = df.reset_index(drop=True).copy()
    df.columns = pd.Index(names, dtype=object)
    return _extract_structured_codebook(df, src, observed)


# -----------------------------------------------------------------------------
# parse_codebook()
# -----------------------------------------------------------------------------


def _haven_frame(path: str, ext: str) -> pd.DataFrame:
    from pytacheck.datacheck._files_readers import read_stat_file

    return read_stat_file(path, ext, math.inf)


def _import_data(path: str, ext: str) -> pd.DataFrame | None:
    """``import_jasp(path)$data`` / ``import_omv(path)$data`` (``None`` on error)."""
    try:
        if ext == "jasp":
            from pytacheck.statout.jasp import import_jasp as importer
        else:
            from pytacheck.statout.omv import import_omv as importer
        res = importer(path)
    except Exception:
        return None
    df = res.get("data") if isinstance(res, Mapping) else getattr(res, "data", None)
    return df if isinstance(df, pd.DataFrame) else None


def _dispatch(path: str, ext: str, src: str, observed: Any, header_lookahead: int) -> Any:
    """The ``switch(ext, ...)`` of ``parse_codebook()``: ``(kind, result)``."""
    if ext == "json":
        return ("value", _extract_json_codebook(path, src))
    if ext in ("md", "markdown", "rmd", "qmd"):
        return ("value", _extract_markdown_codebook(path, src, observed))
    if ext in ("csv", "tsv", "dat"):
        res = _parse_delimited(path, ext, src, observed, header_lookahead)
        if isinstance(res, tuple):
            return res
        return ("value", res)
    if ext in ("ods", "fods"):
        return ("value", _extract_spreadsheet_codebook(
            sheets=lambda: _ods_sheets(path),
            read=lambda sh, header: _read_ods_sheet(path, sh, header),
            src=src, observed=observed, header_lookahead=header_lookahead,
        ))  # fmt: skip
    if ext in ("xlsx", "xls"):
        from pytacheck.datacheck._files_readers import read_excel

        return ("value", _extract_spreadsheet_codebook(
            sheets=lambda: _excel_sheets(path),
            read=lambda sh, header: read_excel(path, sheet=sh, col_names=header),
            src=src, observed=observed, header_lookahead=header_lookahead,
        ))  # fmt: skip
    if ext in ("sav", "dta", "sas7bdat"):
        res = _extract_haven_labels(_haven_frame(path, ext), src)
        return ("haven", res)
    if ext in ("jasp", "omv"):
        df = _import_data(path, ext)
        return ("haven", None if df is None else _extract_haven_labels(df, src))
    if ext == "qsf":
        from pytacheck.datacheck._columns_qsf import parse_qsf

        return ("value", parse_qsf(path))
    if ext == "pdf":
        return ("value", _pdf_codebook_lines(path))
    if ext in ("docx", "rtf", "odt"):
        text = _extract_rich_text(path, ext)
        if len(trimws(text)) < 10:
            return ("value", None)
        return ("value", _r_split(text, "\n"))
    return ("value", None)


def parse_codebook(
    path: str | os.PathLike[str],
    header_lookahead: int = 5,
    observed: Mapping[str, Any] | None = None,
    group: str | None = None,
) -> pd.DataFrame | list[str] | None:
    """Parse a codebook file into variable definitions.

    Port of ``R/data_check_helpers.R::parse_codebook()``: a rule-based reader
    for structured tables (CSV/TSV/Excel/ODS with a variable-name and a label
    column, wide-format transposition, header-row lookahead), JSON and
    markdown codebooks, embedded haven / JASP / jamovi labels and Qualtrics
    ``.qsf`` files. Rich-text formats (docx/pdf/rtf/odt) and files without
    structured definitions give their text lines (for the LLM tier).

    *observed* maps a variable name to a sample of its data values (resolves
    text-coded value labels); *group* scopes the definitions to one study.

    Returns a data frame of variable definitions, a list of text lines, or
    ``None``.
    """
    p = os.fspath(path)
    if not Path(p).exists():
        return None
    ext = _tolower(_file_ext(p)) or ""
    src = _basename(p)
    obs = observed or {}
    try:
        kind, result = _dispatch(p, ext, src, obs, int(header_lookahead))
    except Exception:
        kind, result = "value", None
    if kind == "return":
        return result  # type: ignore[no-any-return]
    if isinstance(result, list):
        return result
    if isinstance(result, pd.DataFrame) and len(result) > 0:
        result = result.copy()
        if "parse_method" not in result.columns or result["parse_method"].isna().all():
            method = "haven" if kind == "haven" else "structured"
            result["parse_method"] = pd.Series([method] * len(result), dtype="string")
        if "group" in result.columns and group is not None and not _na(group) and group != "":
            result["group"] = pd.Series([group] * len(result), dtype="string")
        return result
    try:
        return _read_lines(p)
    except OSError:
        return None
