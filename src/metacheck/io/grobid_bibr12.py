"""Grobid TEI to bibr export schema 12.0 (port of ``R/import-grobid-bibr12.R``).

``grobid_to_bibr(schema_version="12.0")`` (pytacheck's default) converts a
Grobid TEI file into a paper in bibr export schema 12.x form, the form
:func:`metacheck.io.bibr12.read_bibr12` gives a bibr 12.0 export:

* ids are 1-based positions in document order;
* cross-reference targets come from the ``xml:id`` each ``<ref>`` points at,
  for bib, figure, table and foot references;
* each caption and footnote is one text row with no section, after the body
  and the reference list, and there are no pseudo-sections; a caption starts
  with its printed label, taken from ``<head>`` when ``<figDesc>`` leaves it
  out;
* the source is the PDF next to the TEI (``published.pdf`` for
  ``published.pdf.tei.xml`` or ``published.xml``), else the TEI, so
  ``source.sha256`` matches a bibr export of the same PDF; ``paper_id`` is the
  source's name without its extension;
* the producer is Grobid, with the version in the TEI header;
  ``completed_at`` is when Grobid extracted the content (from the same
  header), else the time of the conversion;
* the converter's own warning codes start with ``METACHECK_``.

The body sentences are made as the older conversion
(:func:`metacheck.io.grobid._grobid_to_bibr`) makes them, with their
whitespace squished as in captions and footnotes (metacheck keeps the body's
runs of spaces; U27). Each URL's href is printed where its link is, in that
row only (see :func:`metacheck.io.grobid._print_hrefs`; U28), and an
author's ORCID is the first one printed (U28).

Deliberate difference: ``extraction.converter`` names pytacheck and its
version (metacheck names itself), as :func:`metacheck.io.bibr12.paper_to_bibr12`
does when it writes a 12.0 file.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from os import PathLike
from typing import Any

import pandas as pd

from metacheck._r.base import trimws
from metacheck._r.regex import grepl, gsub, regextract, strsplit, sub
from metacheck._values import as_int
from metacheck.io.bibr12 import (
    BIBR12_COLS,
    _bibr12_bib_type,
    _bibr12_columns,
    _bibr12_doi,
    _bibr12_info,
    _bibr12_paper,
    _bibr12_sha256,
    _converter,
    _now_utc,
)
from metacheck.io.grobid import (
    _column,
    _count_sections,
    _extract_eq,
    _header_text,
    _html_refs,
    _na,
    _print_hrefs_at,
    _printed_before,
    _r_max,
    _series_list,
    _tei_authors,
    _tei_bib_columns,
    _tei_table_contents,
    _tei_text_table,
)
from metacheck.io.xml import (
    _xml_find1_text,
    _xml_find_text,
    _xml_read_grobid,
    as_character,
    read_xml,
    xml_attr,
    xml_find_all,
    xml_find_first,
    xml_text,
)
from metacheck.papers.model import Paper
from metacheck.papers.schema import coerce_column

__all__: list[str] = []

# Grobid <div type> values that bibr 12.x names differently
_SECTION_TYPES = {
    "acknowledgement": "acknowledgment",
    "annex": "appendix",
    "availability": "data_availability",
    "conflict": "coi",
    "contribution": "author_contributions",
    "foot": "footnote",
}
# the section_type vocabulary of bibr 12.x
_SECTION_VOCAB = frozenset(
    (
        "title",
        "abstract",
        "intro",
        "method",
        "results",
        "discussion",
        "references",
        "acknowledgment",
        "funding",
        "keywords",
        "endnote",
        "appendix",
        "data_availability",
        "author_contributions",
        "coi",
        "ethics",
        "footnote",
        "table",
        "figure",
        "unknown",
    )
)
# Grobid <ref type> -> 12.x xref_type
_XREF_TYPES = {
    "bibr": "bib",
    "figure": "figure",
    "table": "table",
    "foot": "foot",
    "formula": "equation",
}
# the 12.x comparators, and the spellings extract_eq() finds for some of them
_COMPS = frozenset(("=", "<", ">", "~", "\u2248", "\u2260", "\u2264", "\u2265", "\u226a", "\u226b"))
_COMP_MAP = {
    "<=": "\u2264",
    "=<": "\u2264",
    ">=": "\u2265",
    "=>": "\u2265",
    "<<": "\u226a",
    ">>": "\u226b",
    "~=": "\u2248",
    "~~": "\u2248",
    "==": "=",
}
_ORCID = "[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]"


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _squish(x: Any) -> Any:
    """``trimws(gsub("\\\\s+", " ", x))`` of one string (``None`` stays ``None``)."""
    if x is None:
        return None
    return trimws(gsub(r"\s+", " ", x))


def _na_if_empty(x: Sequence[Any]) -> list[Any]:
    """``x[!is.na(x) & !nzchar(trimws(x))] <- NA``."""
    return [None if v is None or trimws(v) == "" else v for v in x]


def _tolower(s: str) -> str:
    """R's ``tolower()``: one lower-case character per character (``towlower``)."""
    if s.isascii():
        return s.lower()
    return "".join(c.lower()[0] if c.lower() else c for c in s)


def _file_path_sans_ext(x: str) -> str:
    """``tools::file_path_sans_ext()``."""
    return str(sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x))


def _xml_remove(node: Any) -> None:
    """``xml2::xml_remove()``: unlink *node*, keeping the text that follows it."""
    parent = node.getparent()
    if parent is None:
        return
    tail = node.tail
    if tail:
        prev = node.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(node)


def _records(columns: Mapping[str, Sequence[Any]], tbl: str) -> list[dict[str, Any]]:
    """The rows ``.bibr12_df(columns, .bibr12_cols[[tbl]])`` makes, as records."""
    cols = BIBR12_COLS[tbl]
    n = max([0, *(len(v) for v in columns.values())])
    typed = _bibr12_columns({k: list(v) for k, v in columns.items()}, cols, n)
    names = list(cols)
    return [
        dict(zip(names, row, strict=True)) for row in zip(*(typed[c] for c in names), strict=True)
    ]


# ---------------------------------------------------------------------------
# .bibr12_utc(): R's strptime()/as.POSIXct() for two ISO 8601 formats
# ---------------------------------------------------------------------------


def _r_strtod(s: str, i: int) -> tuple[float, int]:
    """``R_strtod()`` from position *i*: ``(value, end)``; ``(nan, i)`` if nothing parses."""
    n = len(s)
    p = i
    while p < n and s[p] in " \t\n\v\f\r":
        p += 1
    sign = 1.0
    if p < n and s[p] in "+-":
        sign = -1.0 if s[p] == "-" else 1.0
        p += 1
    head = s[p : p + 3].lower()
    if head == "nan":
        return math.nan, p + 3
    if head == "inf":  # R accepts "Inf" (so "infinity" stops after "Inf")
        return sign * math.inf, p + 3
    if n - p > 2 and s[p] == "0" and s[p + 1] in "xX":
        ans, exph = 0.0, -1
        p += 2
        while p < n:
            c = s[p]
            if c in "0123456789abcdefABCDEF":
                ans = 16 * ans + int(c, 16)
                if exph >= 0:
                    exph += 4
            elif c == "." and exph < 0:
                exph = 0
            else:
                break
            p += 1
        expn = 0
        if p < n and s[p] in "pP":
            p += 1
            esign = 1
            if p < n and s[p] in "+-":
                esign = -1 if s[p] == "-" else 1
                p += 1
            e = 0
            while p < n and s[p].isascii() and s[p].isdigit():
                e = e * 10 + int(s[p])
                p += 1
            expn = esign * e
        if exph > 0:
            expn -= exph
        return sign * ans * 2.0**expn if ans else sign * ans, p
    start = p
    while p < n and "0" <= s[p] <= "9":
        p += 1
    ndigits = p - start
    mant = s[start:p]
    if p < n and s[p] == ".":
        p += 1
        f0 = p
        while p < n and "0" <= s[p] <= "9":
            p += 1
        ndigits += p - f0
        mant += "." + s[f0:p]
    if ndigits == 0:
        return math.nan, i  # back out
    expn = 0
    if p < n and s[p] in "eE":
        p += 1
        esign = 1
        if p < n and s[p] in "+-":
            esign = -1 if s[p] == "-" else 1
            p += 1
        e = 0
        while p < n and "0" <= s[p] <= "9":
            e = min(e * 10 + int(s[p]), 9999)
            p += 1
        expn = esign * e
    try:
        value = float(f"{mant.rstrip('.') or '0'}e{expn}")
    except OverflowError:  # pragma: no cover - float() returns inf instead
        value = math.inf
    return sign * value, p


def _get_number(s: str, p: int, lo: int, hi: int, width: int) -> tuple[int, int] | None:
    """glibc/R strptime ``get_number()``: at most *width* digits, stopping early
    when another digit would exceed *hi*."""
    n = len(s)
    while p < n and s[p] == " ":
        p += 1
    if p >= n or not "0" <= s[p] <= "9":
        return None
    val = 0
    k = width
    while True:
        val = val * 10 + int(s[p])
        p += 1
        k -= 1
        if not (k > 0 and val * 10 <= hi and p < n and "0" <= s[p] <= "9"):
            break
    if val < lo or val > hi:
        return None
    return val, p


def _strptime_iso(x: str, with_seconds: bool) -> tuple[Any, ...] | None:
    """R's ``strptime(x, "%Y-%m-%dT%H:%M[:%OS]%z")``: the fields, or ``None``.

    Returns ``(year, month, day, hour, minute, second, fraction, offset_s)``.
    Trailing input is ignored, as in R.
    """
    p = 0
    fields: list[int] = []
    for lit, lo, hi, width in (
        ("", 0, 9999, 4),  # %Y
        ("-", 1, 12, 2),  # %m
        ("-", 1, 31, 2),  # %d
        ("T", 0, 24, 2),  # %H
        (":", 0, 59, 2),  # %M
    ):
        if lit:
            if p >= len(x) or x[p] != lit:
                return None
            p += 1
        got = _get_number(x, p, lo, hi, width)
        if got is None:
            return None
        val, p = got
        fields.append(val)
    sec, frac = 0, 0.0
    if with_seconds:
        if p >= len(x) or x[p] != ":":
            return None
        p += 1
        sval, p = _r_strtod(x, p)
        if 0.0 <= sval <= 61.0:
            sec = int(sval)
            frac = sval - math.floor(sval)
    # %z: [+-]hhmm, after optional spaces
    while p < len(x) and x[p] == " ":
        p += 1
    if p >= len(x) or x[p] not in "+-":
        return None
    neg = x[p] == "-"
    p += 1
    digits = ""
    while len(digits) < 4 and p < len(x) and "0" <= x[p] <= "9":
        digits += x[p]
        p += 1
    if len(digits) != 4:
        return None
    hh, mm = int(digits[:2]), int(digits[2:])
    if mm >= 60 or hh * 100 + (mm * 50) // 30 > 1400:
        return None
    offset = (hh * 3600 + mm * 60) * (-1 if neg else 1)
    return (*fields, sec, frac, offset)


def _days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(z: int) -> tuple[int, int, int]:
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return yoe + era * 400 + (m <= 2), m, d


def _month_days(y: int, m: int) -> int:
    if m == 2:
        return 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def _bibr12_utc(x: Any) -> str | None:
    """Port of ``R/import-grobid-bibr12.R::.bibr12_utc()``.

    An ISO 8601 date and time with a UTC offset (e.g. Grobid's
    ``"2025-07-10T14:15+0000"``) as the UTC ``"YYYY-MM-DDTHH:MM:SSZ"`` of bibr
    12.x; ``None`` when *x* has no time or offset. Parsed as R's
    ``as.POSIXct(format = "%Y-%m-%dT%H:%M:%OS%z")``, then ``"%Y-%m-%dT%H:%M%z"``.
    """
    if x is None:
        return None
    s = str(sub("Z$", "+0000", trimws(str(x))))
    s = str(sub("([+-][0-9]{2}):([0-9]{2})$", r"\1\2", s))
    for with_seconds in (True, False):
        f = _strptime_iso(s, with_seconds)
        if f is None:
            continue
        year, month, day, hour, minute, sec, frac, offset = f
        # R's validation: the day of the month, a leap second, 24:00:00
        if day > _month_days(year, month) or sec > 60:
            continue
        if hour == 24 and (minute > 0 or sec > 0):
            continue
        whole = (
            _days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + sec - offset
        )
        # a POSIXct is a double; format() shows its whole seconds
        total = math.floor(float(whole) + frac)
        days, rem = divmod(total, 86400)
        y, m, d = _civil_from_days(days)
        if not 0 <= y <= 9999:
            # not a 12.0 date-time (metacheck writes "-1-12-31T23:00:30Z"; U26)
            return None
        hh, rem = divmod(rem, 3600)
        mi, ss = divmod(rem, 60)
        # four-digit years (metacheck drops the zero padding: "26-05-27..."; U26)
        return f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mi:02d}:{ss:02d}Z"
    return None


# ---------------------------------------------------------------------------
# .grobid_to_bibr12()
# ---------------------------------------------------------------------------


def _grobid_to_bibr12(xml_path: str | PathLike[str], schema_version: Any = "12.0") -> Paper:
    """Port of ``R/import-grobid-bibr12.R::.grobid_to_bibr12()``.

    Converts one Grobid TEI file to a paper in bibr export schema 12.x form
    (called by ``_grobid_to_bibr(schema_version="12.0")``). The body text is
    split into sentences as the older conversion does; captions and footnotes
    are one text row each, with no section, after the body and the reference
    list. Ids are 1-based positions in document order, and cross-reference
    targets are looked up from the ``xml:id`` each ``<ref>`` points at.

    The source is the PDF Grobid read when it sits next to the TEI
    (``published.pdf`` for ``published.pdf.tei.xml`` or ``published.xml``),
    else the TEI file. The producer is Grobid (the version in the TEI header),
    which also says when it extracted the content; the converter is pytacheck
    (deliberate difference: metacheck names itself). Warning codes start with
    ``METACHECK_``, as metacheck's do.
    """
    if not isinstance(schema_version, str) or schema_version != "12.0":
        raise ValueError('schema_version must be None or "12.0"')
    path = os.fspath(xml_path)
    xml = _xml_read_grobid(path)

    warns: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        warns.append({"code": code, "message": message})

    # source: the PDF Grobid read, when it is next to the TEI ----
    stem = str(sub(r"\.tei\.xml$", "", path, ignore_case=True))
    pdfs = [stem, str(sub(r"\.xml$", ".pdf", path, ignore_case=True))]
    pdfs = [p for p in pdfs if grepl(r"\.pdf$", p, ignore_case=True) and os.path.exists(p)]
    src_path = pdfs[0] if pdfs else path
    if not pdfs:
        warn(
            "METACHECK_SOURCE_IS_TEI",
            "The PDF Grobid read was not found next to the TEI file, so source is the TEI file.",
        )
    source = {
        "file_name": os.path.basename(src_path),
        "sha256": _bibr12_sha256(src_path),
        "input_format": "pdf" if pdfs else "tei",
    }
    if source["sha256"] is None:
        # metacheck: "SHA-256 needs R >= 4.5 or the digest package."
        warn("METACHECK_SOURCE_SHA256_UNAVAILABLE", "The source file could not be read.")

    # floats, notes and references, in document (TEI) order ----
    floats = xml_find_all(xml, "//figure")
    is_tab = [xml_attr(f, "type") == "table" for f in floats]
    notes = xml_find_all(xml, "//note[@place='foot']")
    bibs = xml_find_all(xml, "//listBibl //biblStruct")

    # body: sentences as the older conversion makes them, without floats and notes
    body_xml = read_xml(as_character(xml))
    for node in xml_find_all(body_xml, "//figure | //note[@place='foot']"):
        _xml_remove(node)
    tt = _tei_text_table(body_xml)
    sec = _count_sections(tt["section_id"], tt["header"], tt["section_type"])
    keep = [
        k
        for k, (f, h) in enumerate(zip(tt["formatted"], tt["header"], strict=True))
        if not (f is not None and h is not None and f == h)
    ]
    body = {
        c: [tt[c][k] for k in keep] for c in ("text", "paragraph_id", "section_id", "formatted")
    }
    # whitespace squished, as in captions, footnotes and the abstract (and in
    # bibr's own exports); metacheck keeps the body's runs of spaces (U27)
    body["text"] = [_squish(t) for t in body["text"]]

    # the same header text the older conversion makes
    sec_header = [_header_text(s[1]) for s in sec]
    sec_header = [None if h is not None and grepl(r"^\[div-\d+\]$", h) else h for h in sec_header]

    sec_type: list[Any] = []
    for s in sec:
        t = s[2]
        if t is not None:
            t = _SECTION_TYPES.get(t, t)
            if t not in _SECTION_VOCAB:
                t = "unknown"
        sec_type.append(t)

    sections: dict[str, list[Any]] = {
        "section_id": list(range(1, len(sec) + 1)),
        "header": _na_if_empty(sec_header),
        "level": [1] * len(sec),
        "section_type": sec_type,
    }
    first_sec: dict[Any, int] = {}
    for i, s in enumerate(sec, 1):
        first_sec.setdefault(None if _na(s[0]) else float(s[0]), i)
    body_section = [first_sec.get(None if _na(v) else float(v)) for v in body["section_id"]]

    # references: one text row each, in a References section ----
    bib = _tei_bib_columns(xml)
    bib_text: list[Any] = list(bib.get("bib_text") or [])
    n_bib = len(bib_text)
    ref_section = len(sections["section_id"]) + 1
    if n_bib > 0:
        sections["section_id"].append(ref_section)
        sections["header"].append("References")
        sections["level"].append(1)
        sections["section_type"].append("references")

    # captions and footnotes: one text row each, with no section ----
    desc = [xml_find_first(f, ".//figDesc") for f in floats]
    caption = _na_if_empty([None if d is None else _squish(xml_text(d)) for d in desc])
    # a caption starts with its printed label, which Grobid can leave out of
    # figDesc but keeps in <head> ("Figure 1 :")
    float_head = [
        str(sub(r"\s+([:.])$", r"\1", _squish(_xml_find1_text(f, "./head")))) for f in floats
    ]
    label_rx = r"^(fig(ure)?|tab(le)?)\.?\s*[A-Z]{0,2}[0-9]"
    for i, (c, h) in enumerate(zip(caption, float_head, strict=True)):
        if c is None or h == "":
            continue
        if grepl(label_rx, c, ignore_case=True) or _tolower(c).startswith(_tolower(h)):
            continue
        caption[i] = f"{h} {c}"
    cap_idx = [i for i, c in enumerate(caption) if c is not None]
    note_text = [_squish(xml_text(n)) for n in notes]

    # the text table, and the TEI markup of each row to find <ref>s in
    n_body = len(body["text"])
    n_cap = len(cap_idx)
    text: list[Any] = [*body["text"], *bib_text, *(caption[i] for i in cap_idx), *note_text]
    markup: list[Any] = [
        *body["formatted"],
        *([None] * n_bib),
        *(as_character(desc[i]) for i in cap_idx),
        *(as_character(n) for n in notes),
    ]
    text_id = list(range(1, len(text) + 1))
    para = _r_max(body["paragraph_id"])
    paragraph_id = [
        *body["paragraph_id"],
        *(para + k for k in range(1, n_bib + n_cap + len(notes) + 1)),
    ]
    text_section = [*body_section, *([ref_section] * n_bib), *([None] * (n_cap + len(notes)))]
    ref_text_id = [n_body + k for k in range(1, n_bib + 1)]
    cap_text_id: list[int | None] = [None] * len(floats)
    for j, i in enumerate(cap_idx, 1):
        cap_text_id[i] = n_body + n_bib + j
    note_text_id = [n_body + n_bib + n_cap + k for k in range(1, len(notes) + 1)]

    # figures, tables and footnotes: 1-based positions in document order ----
    float_label = _na_if_empty([gsub(r"\s", "", _xml_find1_text(f, "./label")) for f in floats])
    float_page: list[int | None] = []
    for f in floats:
        coords = xml_attr(f, "coords")
        if coords is None:
            coords = xml_attr(xml_find_first(f, ".//graphic"), "coords")
        page = None if coords is None else _as_integer([sub(",.*", "", coords)])[0]
        float_page.append(page if page is not None and page >= 1 else None)
    if floats:
        warn(
            "METACHECK_FLOAT_SECTION_UNKNOWN",
            "Grobid lists figures and tables after the body, so the section each is "
            "printed in is unknown: their section_id is null.",
        )

    figs = [i for i, t in enumerate(is_tab) if not t]
    tabs = [i for i, t in enumerate(is_tab) if t]
    figure = {
        "figure_id": list(range(1, len(figs) + 1)),
        "label": [float_label[i] for i in figs],
        "text_id": [cap_text_id[i] for i in figs],
        "caption": [caption[i] for i in figs],
        "page_number": [float_page[i] for i in figs],
    }
    table = {
        "table_id": list(range(1, len(tabs) + 1)),
        "label": [float_label[i] for i in tabs],
        "text_id": [cap_text_id[i] for i in tabs],
        "contents": [
            _tei_table_contents(xml_find_first(floats[i], ".//table")) or [] for i in tabs
        ],
        "caption": [caption[i] for i in tabs],
        "page_number": [float_page[i] for i in tabs],
    }
    footnote = {
        "footnote_id": list(range(1, len(notes) + 1)),
        "label": _na_if_empty([xml_attr(n, "n") for n in notes]),
        "text_id": note_text_id,
    }

    # references ----
    def bib_col(col: str) -> list[Any]:
        vals = bib.get(col)
        return [None] * n_bib if vals is None else [None if _na(v) else v for v in vals]

    bib_doi = _na_if_empty(bib_col("doi"))
    year = [as_int(v) for v in bib_col("year")]
    bib_tbl: dict[str, Sequence[Any]] = {
        "bib_id": list(range(1, n_bib + 1)),
        "text_id": ref_text_id,
        "bib_type": _bibr12_bib_type(bib_col("bib_type")),
        "doi": _bibr12_doi(bib_doi),
        "title": _na_if_empty(bib_col("title")),
        "authors": _na_if_empty(bib_col("authors")),
        "editors": _na_if_empty(bib_col("editors")),
        "publisher": _na_if_empty(bib_col("publisher")),
        "year": year,
        "year_suffix": _na_if_empty(bib_col("year_suffix")),
        "published_date": [
            f"{y:04d}" if y is not None and 1000 <= y <= 2999 else None for y in year
        ],
        "container": _na_if_empty(bib_col("container")),
        "volume": _na_if_empty(bib_col("volume")),
        "issue": _na_if_empty(bib_col("issue")),
        "first_page": _na_if_empty(bib_col("first_page")),
        "last_page": _na_if_empty(bib_col("last_page")),
        "is_in_press": [False] * n_bib,
    }

    # cross-references: the xml:id each <ref> targets, by type ----
    def ids(nodes: Sequence[Any]) -> dict[str, int]:
        # R: setNames(seq_along(nodes), xml_attr(nodes, "id"))[name] -- the
        # first node of that name; NA and "" names never match
        out: dict[str, int] = {}
        for i, node in enumerate(nodes, 1):
            name = xml_attr(node, "id")
            if name is not None and name != "":
                out.setdefault(name, i)
        return out

    targets = {
        "bib": ids(bibs),
        "figure": ids([floats[i] for i in figs]),
        "table": ids([floats[i] for i in tabs]),
        "foot": ids(notes),
    }

    # the printed contents squished, as the text they are found in (U27)
    refs: list[tuple[Any, Any, Any, int]] = []
    url_nodes: list[Any] = []
    for k, node in _html_refs(markup, "//ref"):
        refs.append(
            (xml_attr(node, "type"), xml_attr(node, "target"), _squish(xml_text(node)), text_id[k])
        )
        if refs[-1][0] == "url":
            url_nodes.append(node)
    urls = [r for r in refs if r[0] == "url"]
    refs = [r for r in refs if r[0] != "url"]

    unmapped = [r[0] for r in refs if r[0] not in _XREF_TYPES]
    if unmapped:
        kinds = ", ".join("NA" if t is None else t for t in dict.fromkeys(unmapped))
        warn(
            "METACHECK_XREF_TYPE_UNMAPPED",
            f"{len(unmapped):d} reference(s) of Grobid type {kinds} have no xref_type "
            "and were left out.",
        )
        refs = [r for r in refs if r[0] in _XREF_TYPES]
    # a <ref> naming several targets is one row per target
    xrefs: list[tuple[str, Any, Any, int]] = []
    for (rtype, _target, contents, tid), parts in zip(
        refs, strsplit([r[1] for r in refs], r"\s+"), strict=True
    ):
        xrefs.extend((_XREF_TYPES[rtype], t, contents, tid) for t in parts)
    target_id: list[int | None] = []
    for xtype, target, _c, _t in xrefs:
        lookup = targets.get(xtype)
        if target is None or lookup is None:
            target_id.append(None)
        else:
            target_id.append(lookup.get(str(sub("^#", "", target))))

    # URLs, cleaned up as the older conversion does ----
    href: list[Any] = gsub(r"\.$", "", gsub(r"\s", "", [u[1] for u in urls]))
    link_text: list[Any] = [u[2] for u in urls]
    strip_scheme = gsub("^https?://", "", href)
    link_nospace = gsub("^https?://", "", gsub(r"\s", "", link_text))
    # each link is printed at its own place, in its own row (U28)
    occurrence = [
        _printed_before(node, u[2], squish=True) for node, u in zip(url_nodes, urls, strict=True)
    ]
    text, shown = _print_hrefs_at(text, text_id, link_text, href, [u[3] for u in urls], occurrence)
    link_text = [
        None if (a is not None and b is not None and a == b) else lt
        for a, b, lt in zip(strip_scheme, link_nospace, link_text, strict=True)
    ]

    # where each item is printed in its sentence, when that is unambiguous
    def span(tids: Sequence[Any], xs: Sequence[Any]) -> tuple[list[Any], list[Any]]:
        starts: list[Any] = []
        ends: list[Any] = []
        for tid, x in zip(tids, xs, strict=True):
            t = text[tid - 1] if tid is not None and 1 <= tid <= len(text) else None
            if t is None or x is None or x == "" or t.count(x) != 1:
                starts.append(None)
                ends.append(None)
                continue
            at = t.find(x)
            starts.append(at)
            ends.append(at + len(x))
        return starts, ends

    xref_start, xref_end = span([x[3] for x in xrefs], [x[2] for x in xrefs])
    # the text now prints each href (a relative link: as the paper prints it)
    url_start, url_end = span(
        [u[3] for u in urls], [h if p is None else p for p, h in zip(shown, href, strict=True)]
    )

    # authors and their affiliations ----
    au = _tei_authors(xml)
    n_au = len(au)

    def au_col(col: str) -> list[Any]:
        return _series_list(au[col]) if col in au.columns else [None] * n_au

    aff = _na_if_empty(au_col("affiliation"))
    aff_text = list(dict.fromkeys(a for a in aff if a is not None))
    orcid_raw = au_col("orcid")
    # the first ORCID printed (metacheck's greedy ".*(" keeps the last; U28)
    orcid = [
        None if o is None or not grepl(_ORCID, o) else f"https://orcid.org/{regextract(_ORCID, o)}"
        for o in orcid_raw
    ]
    author = {
        "author_id": list(range(1, n_au + 1)),
        "given": _na_if_empty(au_col("given")),
        "family": _na_if_empty(au_col("family")),
        "email": _na_if_empty(au_col("email")),
        "corresponding": [False] * n_au,
        "orcid": orcid,
        "role": [[] for _ in range(n_au)],
        "credit_roles": [[] for _ in range(n_au)],
    }
    affiliation = {
        "affiliation_id": list(range(1, len(aff_text) + 1)),
        "text": aff_text,
        "author_ids": [[i for i, a in enumerate(aff, 1) if a == t] for t in aff_text],
    }

    # metadata ----
    keywords = list(_xml_find_text(xml, ".//textClass/keywords/term"))
    if keywords == [""]:
        keywords = []
    abstract = " ".join(str(_squish(t)) for t in xml_text(xml_find_all(xml, ".//abstract //p")))
    doi = _na_if_empty([_xml_find1_text(xml, "//teiHeader//idno[@type='DOI']")])[0]
    dois = [doi, *bib_doi]
    bad_doi = [
        d for d, ok in zip(dois, _bibr12_doi(dois), strict=True) if d is not None and ok is None
    ]
    if bad_doi:
        warn(
            "METACHECK_DOI_NOT_VALID",
            f"{len(bad_doi):d} DOI(s) are not bare DOIs (10.xxxx/...) and were left out: "
            + "; ".join(bad_doi[:3]),
        )
    metadata = {
        "title": _na_if_empty([_xml_find1_text(xml, ".//titleStmt/title")])[0],
        "abstract": _na_if_empty([abstract])[0],
        "keywords": keywords,
        "doi": _bibr12_doi([doi])[0],
    }

    # extraction: Grobid produced it, pytacheck converted it ----
    app = xml_find_first(xml, "//teiHeader//application[@ident='GROBID']")
    if app is None:
        app = xml_find_first(xml, "//teiHeader//application")
    ident = xml_attr(app, "ident")
    app_name = _na_if_empty([None if ident is None else _tolower(ident)])[0]
    app_version = _na_if_empty([xml_attr(app, "version")])[0]
    producer = {
        "name": "unknown" if app_name is None else app_name,
        "version": "unknown" if app_version is None else app_version,
        "build_sha": None,
    }
    # when Grobid extracted the content, else when pytacheck converted it
    completed_at = _bibr12_utc(xml_attr(app, "when"))
    if completed_at is None:
        completed_at = _now_utc()

    # statistics, as the older conversion finds them, searched in the paper's
    # sentences indexed now (the paper keeps the index) ----
    paper_id = _file_path_sans_ext(os.path.basename(src_path))
    text_records = _records(
        {
            "text": text,
            "text_id": text_id,
            "paragraph_id": [as_int(v) for v in paragraph_id],
            "section_id": text_section,
        },
        "text",
    )
    section_records = _records(sections, "section")
    doc = _read_doc(paper_id, text_records, section_records)
    if doc is not None:
        from metacheck.text.extract import eq_table

        eq = eq_table(doc)
    else:
        tmp = Paper(paper_id)
        tmp.text = pd.DataFrame(
            {
                "text": _column(text, "string"),
                "text_id": _column(text_id, "Int64"),
                "paragraph_id": _column(paragraph_id, "Int64"),
                "section_id": _column(text_section, "Int64"),
            }
        )
        tmp.section = pd.DataFrame(
            {
                "section_id": _column(sections["section_id"], "Int64"),
                "header": _column(sections["header"], "string"),
                "section_type": _column(sections["section_type"], "string"),
            }
        )
        eq = _extract_eq(tmp)
    comp = [None if _na(c) else _COMP_MAP.get(c, c) for c in eq["comp"].tolist()]
    off_vocab = [c for c in comp if c not in _COMPS]
    keep_eq = [i for i, c in enumerate(comp) if c in _COMPS]
    if off_vocab:
        kinds = ", ".join("NA" if c is None else c for c in dict.fromkeys(off_vocab))
        warn(
            "METACHECK_EQ_COMP_UNMAPPED",
            f"{len(off_vocab):d} statistic(s) with comparator {kinds} were left out.",
        )

    def eq_col(col: str) -> list[Any]:
        vals = _series_list(eq[col])
        return [vals[i] for i in keep_eq]

    tables = {
        "author": _records(author, "author"),
        "affiliation": _records(affiliation, "affiliation"),
        "text": text_records,
        "section": section_records,
        "url": _records(
            {
                "url_id": list(range(1, len(href) + 1)),
                "href": href,
                "link_text": link_text,
                "text_id": [u[3] for u in urls],
                "start": url_start,
                "end": url_end,
            },
            "url",
        ),
        "bib": _records(bib_tbl, "bib"),
        "xref": _records(
            {
                "xref_id": list(range(1, len(xrefs) + 1)),
                "target_id": target_id,
                "xref_type": [x[0] for x in xrefs],
                "contents": [x[2] for x in xrefs],
                "text_id": [x[3] for x in xrefs],
                "start": xref_start,
                "end": xref_end,
            },
            "xref",
        ),
        "figure": _records(figure, "figure"),
        "table": _records(table, "table"),
        "footnote": _records(footnote, "footnote"),
        "eq": _records(
            {
                "eq_id": list(range(1, len(keep_eq) + 1)),
                "text_id": eq_col("text_id"),
                "grp_id": eq_col("grp_id"),
                "lhs": eq_col("lhs"),
                "df": eq_col("df"),
                "comp": [comp[i] for i in keep_eq],
                "rhs": eq_col("rhs"),
            },
            "eq",
        ),
    }

    extraction = {
        "producer": producer,
        "converter": _converter(),
        "completed_at": completed_at,
        "ocr": None,
        "llm": None,
        "warnings": warns,
    }

    # the metadata are strings (keywords a list of them): nothing for
    # .paper_coerce() to stop at
    info = _bibr12_info(metadata, source, "12.0", extraction)
    paper = _bibr12_paper(paper_id, info, tables, extraction)
    if doc is not None:
        from metacheck.core.doc import Doc

        Doc.attach(paper, doc)
    return paper


def _read_doc(paper_id: str, text: list[dict[str, Any]], section: list[dict[str, Any]]) -> Any:
    """The Doc of the paper being read, from its text and section records (``None``
    when they need the older path: no sentences, or section IDs that repeat)."""
    from metacheck.core.doc import Doc

    return Doc.from_records(
        paper_id, text, list(BIBR12_COLS["text"]), section, list(BIBR12_COLS["section"])
    )


def _as_integer(x: Sequence[Any]) -> list[int | None]:
    """``as.integer()`` of a character vector (``None`` for NA)."""
    values = coerce_column(pd.Series(list(x), dtype=object), "integer").tolist()
    return [None if _na(v) else int(v) for v in values]
