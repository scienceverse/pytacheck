"""Grobid TEI XML import (port of ``R/import-grobid.R``).

``grobid_to_bibr()`` turns a Grobid TEI XML file into a paper object exactly
as metacheck does: the same sentence splitting (ICU sentence boundaries, as
``tidytext::unnest_sentences()`` finds them through stringi), the same text,
section and paragraph ids, header handling, table/figure/footnote rows,
bibliography parsing, cross-references, URL clean-up and equation table.
``convert_grobid()`` is the HTTP client for a Grobid server.

The TEI is parsed with lxml, which wraps the same libxml2 as R's xml2, so
node serialisation (the ``formatted`` column, table HTML) and HTML-to-text
conversion agree byte for byte (see :mod:`pytacheck.io.xml`).

Everything works on plain Python lists; DataFrames are only built for the
final paper tables.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import math
import os
import tempfile
import warnings
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd
import regex

from pytacheck._r.base import as_character as r_as_character
from pytacheck._r.base import plural
from pytacheck._r.regex import grepl, gsub, regexec, regextract, regextract_all, sub
from pytacheck.io.xml import (
    _xml_find1_text,
    _xml_find_text,
    _xml_read_grobid,
    as_character,
    html_text,
    read_html,
    read_xml,
    xml_attr,
    xml_find_all,
    xml_find_first,
    xml_text,
)
from pytacheck.log import logger
from pytacheck.papers.model import Paper, PaperList, is_paper
from pytacheck.papers.schema import coerce_column, load_schema, table_columns

__all__ = ["convert_grobid", "grobid_to_bibr"]

PathLikeStr = str | PathLike[str]


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and math.isnan(x)


def _paste(*parts: Any) -> str:
    """``paste(...)`` of scalars (``NA`` becomes ``"NA"``)."""
    return " ".join("NA" if _na(p) else str(p) for p in parts)


def _as_numeric(x: Any) -> float:
    """``as.numeric()`` of one string (``NaN`` for ``NA`` / unparsable)."""
    if _na(x):
        return math.nan
    value = coerce_column(pd.Series([x], dtype=object), "number").iloc[0]
    return float(value)


def _r_max(values: Sequence[Any]) -> float:
    """``max(c(0, x))``: ``NA`` if any value is ``NA``."""
    out = 0.0
    for v in values:
        if _na(v):
            return math.nan
        out = max(out, float(v))
    return out


def _column(values: Sequence[Any], dtype: str | type) -> pd.Series:
    vals = list(values)
    if dtype == "string":
        return pd.Series([None if _na(v) else str(v) for v in vals], dtype="string")
    if dtype == "Int64":
        return pd.Series(
            [None if _na(v) or math.isinf(float(v)) else int(v) for v in vals], dtype="Int64"
        )
    if dtype == "float64":
        return pd.Series([math.nan if _na(v) else float(v) for v in vals], dtype="float64")
    if dtype == "boolean":
        return pd.Series([None if _na(v) else bool(v) for v in vals], dtype="boolean")
    return pd.Series(vals, dtype=object)


# ---------------------------------------------------------------------------
# ICU sentence boundaries (tokenizers::tokenize_sentences)
# ---------------------------------------------------------------------------

# UAX #29 Sentence_Break classes; ICU's default sentence break iterator (used
# by stringi::stri_split_boundaries(type = "sentence")) implements these rules.
_SB_CLASSES = (
    "CR",
    "LF",
    "Extend",
    "Sep",
    "Format",
    "Sp",
    "Lower",
    "Upper",
    "OLetter",
    "Numeric",
    "ATerm",
    "SContinue",
    "STerm",
    "Close",
)
_SB_RX = regex.compile("|".join(f"(?P<{c}>\\p{{SentenceBreak={c}}})" for c in _SB_CLASSES))
_SB_TERM_RX = regex.compile(r"[\p{SentenceBreak=ATerm}\p{SentenceBreak=STerm}]")
_WHITESPACE_RX = regex.compile(r"\p{White_Space}")
_EF = frozenset(("Extend", "Format"))
_PARASEP = frozenset(("Sep", "CR", "LF"))
_SB8_STOP = frozenset(("OLetter", "Upper", "Lower", "Sep", "CR", "LF", "ATerm", "STerm"))
_SB8A_NEXT = frozenset(("SContinue", "STerm", "ATerm"))


@functools.lru_cache(maxsize=65536)
def _sb(ch: str) -> str:
    m = _SB_RX.match(ch)
    return m.lastgroup if m and m.lastgroup else "Other"


def _sentence_breaks(s: str) -> list[int]:
    """Break positions of UAX #29 rules SB5-SB11 (interior positions only).

    Breaks can only follow a terminator sequence ``SATerm Close* Sp*``
    (``Extend``/``Format`` characters absorbed, SB5); SB6-SB8a then veto
    some of them. Checked against stringi/ICU on the fixtures and fuzzing.
    """
    n = len(s)
    out: list[int] = []
    for m in _SB_TERM_RX.finditer(s):
        t = m.start()
        aterm = _sb(s[t]) == "ATerm"
        i = t + 1
        while i < n and _sb(s[i]) in _EF:
            i += 1
        if i >= n:
            continue
        nxt = _sb(s[i])
        if aterm:
            if nxt == "Numeric":  # SB6
                continue
            if nxt == "Upper":  # SB7
                j = t - 1
                while j >= 0 and _sb(s[j]) in _EF:
                    j -= 1
                if j >= 0 and _sb(s[j]) in ("Upper", "Lower"):
                    continue
        while i < n and _sb(s[i]) == "Close":  # SB9
            i += 1
            while i < n and _sb(s[i]) in _EF:
                i += 1
        while i < n and _sb(s[i]) == "Sp":  # SB10
            i += 1
            while i < n and _sb(s[i]) in _EF:
                i += 1
        if i < n and _sb(s[i]) in _PARASEP:  # SB11: break after a paragraph separator
            i += 2 if s[i] == "\r" and i + 1 < n and s[i + 1] == "\n" else 1
        if i >= n:
            continue
        if aterm:  # SB8
            j = i
            while j < n and _sb(s[j]) not in _SB8_STOP:
                j += 1
            if j < n and _sb(s[j]) == "Lower":
                continue
        if _sb(s[i]) in _SB8A_NEXT:  # SB8a
            continue
        out.append(i)
    return out


def _tokenize_sentences(x: str | None) -> list[str | None]:
    """``tokenizers::tokenize_sentences()`` for one string.

    Whitespace characters become spaces, the text is split at ICU sentence
    boundaries and every piece is trimmed; ``""`` gives no sentences and
    ``NA`` gives ``NA``.
    """
    if x is None:
        return [None]
    # stringi removes a leading byte order mark from every input string, so
    # each of the three stringi calls drops one (replace, split, trim)
    s = _WHITESPACE_RX.sub(" ", _drop_bom(x))
    s = _drop_bom(s)
    if not s:
        return []
    cuts = [0, *_sentence_breaks(s), len(s)]
    return [_drop_bom(s[a:b]).strip(" ") for a, b in itertools.pairwise(cuts) if b > a]


def _drop_bom(s: str) -> str:
    return s[1:] if s.startswith("\ufeff") else s


# ---------------------------------------------------------------------------
# .process_full_text()
# ---------------------------------------------------------------------------

_REF_RX = r"<ref[^>]*>(?:(?!</ref>).)*</ref>"
_TOKEN_RX = regex.compile(r"\{\{ref(\d+)-(\d+)\}\}")
_COLORDER = ("text", "section", "header", "div", "p", "formatted")


def _series_list(s: pd.Series) -> list[Any]:
    return [None if _na(v) else v for v in s.tolist()]


def _pull_and_split(cols: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """The sentence-splitting part of ``.process_full_text()``."""
    fmt: list[Any] = cols["formatted"]
    fmt = gsub(r"\b([A-Z])\.", r"\1$%", fmt)  # initials (put back later)
    fmt = gsub(r"\bp\.\s+(\d)", r"p$% \1", fmt)  # p. # (put back later)
    fmt = gsub("^<p>", "", fmt)
    fmt = gsub("</p>$", "", fmt)
    fmt = gsub("^<figDesc>", "", fmt)
    fmt = gsub("</figDesc>$", "", fmt)
    fmt = [None if f is None else sub("[\t\r\n ]+$", "", sub("^[\t\r\n ]+", "", f)) for f in fmt]

    # protect inline <ref> elements from the sentence splitter: every row's
    # first occurrence of each ref string becomes a {{ref<i>-<j>}} token
    pull_refs: list[list[str]] = regextract_all(_REF_RX, fmt, perl=True)
    with_refs = [k for k, refs in enumerate(pull_refs) if refs]
    tokens: dict[tuple[str, str], str] = {}
    for i, refs in enumerate(pull_refs, 1):
        for j, url in enumerate(refs, 1):
            tok = f"{{{{ref{i}-{j}}}}}"
            tokens[(str(i), str(j))] = url
            for k in with_refs:
                s = fmt[k]
                if s is not None and url in s:
                    fmt[k] = s.replace(url, tok, 1)

    # unnest_sentences(): one row per sentence, other columns repeated
    names = list(cols)
    out: dict[str, list[Any]] = {c: [] for c in names}
    for k, f in enumerate(fmt):
        sentences = _tokenize_sentences(f)
        for c in names:
            if c == "formatted":
                out[c].extend(sentences)
            else:
                out[c].extend([cols[c][k]] * len(sentences))

    # return refs
    if tokens:
        out["formatted"] = [
            f if f is None or "{{ref" not in f else _restore_refs(f, tokens)
            for f in out["formatted"]
        ]
    return out


def _restore_refs(s: str, tokens: dict[tuple[str, str], str]) -> str:
    """R's ``for (i) for (j) fmt <- sub(token_ij, ref_ij, fmt, fixed = TRUE)`` for one sentence.

    Each token's *first* occurrence is replaced, token by token in (i, j)
    order. When every token occurs once and no restored ref contains a
    token, that is one pass of a regex substitution.
    """
    found = [(m.group(1), m.group(2)) for m in _TOKEN_RX.finditer(s)]
    present = [k for k in found if k in tokens]
    if not present:
        return s
    if len(set(present)) == len(present) and not any("{{ref" in tokens[k] for k in present):

        def restore(m: regex.Match[str]) -> str:
            return tokens.get((m.group(1), m.group(2)), m.group(0))

        return _TOKEN_RX.sub(restore, s)
    for key, url in tokens.items():  # insertion order is R's (i, j) loop order
        tok = f"{{{{ref{key[0]}-{key[1]}}}}}"
        if tok in s:
            s = s.replace(tok, url, 1)
    return s


def _html_to_text(x: str | None) -> str:
    """``xml_text(read_html(paste("<p>", x, "<p>"))) |> trimws()`` (``x`` on error)."""
    src = _paste("<p>", x, "<p>")
    try:
        text = html_text(src)
    except Exception:
        return "NA" if x is None else x
    return sub("[\t\r\n ]+$", "", sub("^[\t\r\n ]+", "", text))


def _process_columns(cols: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """``.process_full_text()`` on column lists (the non-empty branch)."""
    ft = _pull_and_split(cols)
    formatted: list[Any] = ft["formatted"]
    text = [_html_to_text(f) for f in formatted]

    # return initials and page
    formatted = gsub(r"\b([A-Z])\$%", r"\1\.", formatted)
    text = gsub(r"\b([A-Z])\$%", r"\1\.", text)
    formatted = gsub(r"\bp\$%", r"p\.", formatted)
    text = gsub(r"\bp\$%", r"p\.", text)
    ft["formatted"] = formatted
    ft["text"] = text

    # merge sentence fragments: a sentence without end punctuation followed by
    # one that does not start with a capital (backwards, so chains merge)
    n = len(text)
    ends = grepl(r"[\.\?\!\"]$", text)
    caps = grepl("^[A-Z]", text)
    merge = [x for x in range(n - 1) if not ends[x] and not caps[x + 1]]
    if merge:
        removed = [False] * n
        for x in reversed(merge):
            text[x] = _paste(text[x], text[x + 1])
            formatted[x] = _paste(formatted[x], formatted[x + 1])
            removed[x + 1] = True
        ft = {c: [v for v, r in zip(vals, removed, strict=True) if not r] for c, vals in ft.items()}
        text, formatted = ft["text"], ft["formatted"]

    # remove redundant formatted
    ft["formatted"] = [
        None if f is not None and t is not None and f == t else f
        for f, t in zip(formatted, text, strict=True)
    ]
    return ft


def _classify(ft: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """The header classification part of ``.process_full_text()``."""
    n = len(ft["text"])
    section_in = ft["section"]
    back = [not _na(s) for s in section_in]
    headers = ft["header"]
    nospace = gsub(r"\s", "", headers)

    def match(pattern: str) -> list[bool]:
        return grepl(pattern, nospace, ignore_case=True)

    abstract = match("abstract")
    intro = match("intro")
    method = match("method|material")
    results = match("result")
    discussion = match("discuss")
    references = match("bibliography|reference")
    section: list[Any] = [None] * n
    for label, hits in (
        ("abstract", abstract),
        ("intro", intro),
        ("method", method),
        ("discussion", discussion),
        ("results", results),
        ("references", references),
    ):
        for k in range(n):
            if hits[k]:
                section[k] = label
    for k in range(n):
        if back[k]:
            s = section_in[k]
            section[k] = s if isinstance(s, str) else r_as_character(s)

    # beginning sections after abstract with no header labelled intro
    non_blanks = [k for k in range(n) if section[k] is not None and section[k] != "abstract"]
    if non_blanks:
        for k in range(non_blanks[0]):
            if not abstract[k]:
                section[k] = "intro"

    # unlabelled sections in the first paragraph that start with Figure/Table
    div = ft["div"]
    p = ft["p"]
    text = ft["text"]
    no_header = [None if h is None else h[:4] == "[div" for h in headers]
    for label, pattern in (("figure", r"^Figure\s*\d+"), ("table", r"^Table\s*\d+")):
        hits = grepl(pattern, text)
        selected: list[Any] = []
        for k in range(n):
            first = None if _na(p[k]) else p[k] == 1
            conds = (first, no_header[k], hits[k])
            if any(c is False for c in conds):
                continue
            selected.append(div[k] if all(c is True for c in conds) else None)
        # `ft$div %in% ft[sel, "div"]` matches against a one-column data
        # frame, which only works when exactly one (non-NA) row is selected
        if len(selected) == 1 and not _na(selected[0]):
            target = r_as_character(selected[0])
            for k in range(n):
                if not _na(div[k]) and r_as_character(div[k]) == target:
                    section[k] = label

    # assume sections are the same class as previous if unclassified
    for i in range(1, n):
        if not abstract[i] and not abstract[i - 1] and section[i] is None:
            section[i] = section[i - 1]

    ft = dict(ft)
    ft["section"] = section
    blank_divs = grepl(r"\[div-\d+\]", text)
    keep = [k for k in range(n) if not blank_divs[k]]
    return {c: [ft[c][k] for k in keep] for c in _COLORDER}


def _process_full_text_columns(cols: dict[str, list[Any]] | None) -> dict[str, list[Any]]:
    if cols is not None and len(cols.get("formatted", [])) > 0:
        ft = _process_columns(cols)
    else:
        ft = {c: [] for c in ("header", "text", "formatted", "section", "div", "p")}
    return _classify(ft)


def _process_full_text(full_text: pd.DataFrame | None) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.process_full_text()``.

    Splits the ``formatted`` column (TEI paragraphs) into sentences, derives
    the plain ``text``, merges sentence fragments, classifies sections from
    their headers and drops placeholder rows. Returns a table with the
    columns ``text, section, header, div, p, formatted``.
    """
    cols = None
    dtypes: dict[str, Any] = {}
    if full_text is not None and len(full_text) > 0:
        missing = [c for c in _COLORDER if c not in full_text.columns and c != "text"]
        if missing:
            raise KeyError(f"Can't subset columns that don't exist: {missing}")
        cols = {c: _series_list(full_text[c]) for c in full_text.columns}
        dtypes = {c: full_text[c].dtype for c in ("div", "p")}
    ft = _process_full_text_columns(cols)

    def num(values: list[Any], dtype: Any) -> pd.Series:
        if dtype is not None and pd.api.types.is_integer_dtype(dtype):
            return _column(values, "Int64")
        return _column(values, "float64")

    return pd.DataFrame(
        {
            "text": _column(ft["text"], "string"),
            "section": _column(ft["section"], "string"),
            "header": _column(ft["header"], "string"),
            "div": num(ft["div"], dtypes.get("div")),
            "p": num(ft["p"], dtypes.get("p", "Int64")),
            "formatted": _column(ft["formatted"], "string"),
        }
    )


# ---------------------------------------------------------------------------
# .tei_text()
# ---------------------------------------------------------------------------


def _tei_text_columns(xml: Any) -> dict[str, list[Any]]:
    header: list[Any] = []
    formatted: list[Any] = []
    div: list[Any] = []
    section: list[Any] = []

    def add(h: Any, f: Sequence[Any], d: Any, s: Any) -> None:
        header.extend([h] * len(f))
        formatted.extend(f)
        div.extend([d] * len(f))
        section.extend([s] * len(f))

    ## abstract
    add("Abstract", as_character(xml_find_all(xml, ".//abstract //p")), 0.0, "abstract")

    ## body
    divs = xml_find_all(xml, "//text //body //div")
    for i, d in enumerate(divs, 1):
        h = as_character(xml_find_first(d, ".//head"))
        if h is None:
            h = f"[div-{i:02d}]"
        ps = xml_find_all(d, ".//p")
        add(h, as_character(ps) if ps else [h], float(i), None)

    ## back matter
    back = xml_find_all(xml, "//back //div")
    types: list[str] = []
    for t in xml_attr(back, "type"):
        if t is not None and t != "references" and t not in types:
            types.append(t)
    back_rows: list[tuple[Any, int, Any, str]] | None = None
    for t in types:
        bdivs = xml_find_all(xml, f"//back //div[@type='{t}'] //div")
        if not bdivs:
            continue
        back_rows = back_rows or []
        for d in bdivs:
            h = as_character(xml_find_first(d, ".//head"))
            for k, node in enumerate(xml_find_all(d, ".//p"), 1):
                back_rows.append((h, k, as_character(node), t))
    if back_rows is not None:
        # number the back-matter divs after the body divs (one per first <p>)
        cur: float | None = None
        nxt = float(len(divs) + 1)
        for h, k, f, t in back_rows:
            if k == 1:
                cur = nxt
                nxt += 1
            add(h, [f], cur, t)

    ## figures and tables
    for fig in xml_find_all(xml, "//figure"):
        figid = xml_attr(fig, "id")
        f = as_character(xml_find_first(fig, ".//figDesc"))
        h = as_character(xml_find_first(fig, ".//head"))
        s = None if figid is None else sub(r"_\d+$", "", figid)
        d = None if figid is None else _as_numeric(sub("^(fig|tab)_", "", figid))
        add(h, [f], d, s)

    ## footnotes
    for note in xml_find_all(xml, "//note[@place='foot']"):
        noteid = xml_attr(note, "id")
        s = None if noteid is None else sub(r"_\d+$", "", noteid)
        d = None if noteid is None else _as_numeric(sub("^foot_", "", noteid))
        add("", [as_character(note)], d, s)

    keep = [k for k, f in enumerate(formatted) if f is not None]
    rows = [(header[k], formatted[k], div[k], section[k]) for k in keep]
    # re-number p, and renumber figure/table/footnote divs after the rest
    rows_p = [(*r, p) for p, r in enumerate(rows, 1)]
    figtab = [r for r in rows_p if r[3] in ("fig", "tab", "foot")]
    nofigtab = [r for r in rows_p if r[3] not in ("fig", "tab", "foot")]
    if nofigtab:
        divs_known = [r[2] for r in nofigtab if not _na(r[2])]
        divmax = max(divs_known) if divs_known else -math.inf
    else:
        divmax = 0.0
    figtab = [(h, f, divmax + k, s, p) for k, (h, f, _d, s, p) in enumerate(figtab, 1)]
    ordered = nofigtab + figtab
    relabel = {"tab": "table", "fig": "figure"}
    return {
        "header": [r[0] for r in ordered],
        "formatted": [r[1] for r in ordered],
        "div": [r[2] for r in ordered],
        "section": [relabel.get(r[3], r[3]) for r in ordered],
        "p": [r[4] for r in ordered],
    }


def _tei_text_table(xml: Any) -> dict[str, list[Any]]:
    """``.tei_text()`` as column lists (before building the data frame)."""
    ft = _process_full_text_columns(_tei_text_columns(xml))
    n = len(ft["text"])
    return {
        "text": ft["text"],
        "text_id": list(range(1, n + 1)),
        "paragraph_id": ft["p"],
        "section_id": ft["div"],
        "page_number": [None] * n,
        "header": ft["header"],
        "section_type": ft["section"],
        "formatted": ft["formatted"],
    }


def _tei_text(xml: Any) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.tei_text()``: the full text table of a TEI document.

    One row per sentence, with columns ``text, text_id, paragraph_id,
    section_id, page_number, header, section_type, formatted``.
    """
    ft = _tei_text_table(xml)
    return pd.DataFrame(
        {
            "text": _column(ft["text"], "string"),
            "text_id": _column(ft["text_id"], "Int64"),
            "paragraph_id": _column(ft["paragraph_id"], "Int64"),
            "section_id": _column(ft["section_id"], "float64"),
            "page_number": _column(ft["page_number"], "Int64"),
            "header": _column(ft["header"], "string"),
            "section_type": _column(ft["section_type"], "string"),
            "formatted": _column(ft["formatted"], "string"),
        }
    )


# ---------------------------------------------------------------------------
# authors, xrefs, urls
# ---------------------------------------------------------------------------


def _tei_authors(xml: Any) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.tei_authors()``: the author table."""
    nodes = xml_find_all(xml, "//sourceDesc //author[persName]")
    if not nodes:
        return pd.DataFrame()
    rows = []
    for i, a in enumerate(nodes, 1):
        rows.append(
            (
                i,
                _xml_find1_text(a, ".//forename"),
                _xml_find1_text(a, ".//surname"),
                _xml_find1_text(a, ".//affiliation"),
                _xml_find1_text(a, ".//email"),
                _xml_find1_text(a, ".//idno[@type='ORCID']"),
            )
        )
    return pd.DataFrame(
        {
            "author_id": _column([r[0] for r in rows], "Int64"),
            "given": _column([r[1] for r in rows], "string"),
            "family": _column([r[2] for r in rows], "string"),
            "affiliation": _column([r[3] for r in rows], "string"),
            "email": _column([r[4] for r in rows], "string"),
            "corresponding": _column([False] * len(rows), "boolean"),
            "orcid": _column([r[5] for r in rows], "string"),
            "role": pd.Series([[] for _ in rows], dtype=object),
        }
    )


_REF_TAG_RX = regex.compile(r"<ref", regex.IGNORECASE)


def _html_refs(formatted: Sequence[Any], xpath: str) -> list[tuple[int, Any]]:
    """``read_html(paste0("<p>", f, "</p>")) |> xml_find_all(xpath)`` per row."""
    out: list[tuple[int, Any]] = []
    for k, f in enumerate(formatted):
        if f is None or not _REF_TAG_RX.search(f):
            continue
        root = read_html(f"<p>{f}</p>", noblanks=True)
        out.extend((k, node) for node in root.xpath(xpath))
    return out


def _tei_xrefs_columns(
    formatted: Sequence[Any], text_ids: Sequence[Any]
) -> dict[str, list[Any]] | None:
    if len(formatted) == 0:
        return None
    rows = []
    for k, node in _html_refs(formatted, "//ref"):
        xtype = xml_attr(node, "type")
        if xtype is None or xtype == "url":  # dplyr::filter(xref_type != "url")
            continue
        rows.append((xml_attr(node, "target"), xtype, xml_text(node), text_ids[k]))
    ids = gsub(r"\D", "", [r[0] for r in rows])
    return {
        "xref_id": list(coerce_column(pd.Series(ids, dtype=object), "integer")),
        "xref_type": [r[1] for r in rows],
        "contents": [r[2] for r in rows],
        "text_id": [r[3] for r in rows],
    }


def _xref_frame(cols: dict[str, list[Any]] | None) -> pd.DataFrame:
    if cols is None:
        return pd.DataFrame(
            {
                "xref_id": _column([], "string"),
                "xref_type": _column([], "string"),
                "contents": _column([], "string"),
                "text_id": _column([], "Int64"),
            }
        )
    return pd.DataFrame(
        {
            "xref_id": _column(cols["xref_id"], "Int64"),
            "xref_type": _column(cols["xref_type"], "string"),
            "contents": _column(cols["contents"], "string"),
            "text_id": _column(cols["text_id"], "Int64"),
        }
    )


def _tei_xrefs(text_table: pd.DataFrame) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.tei_xrefs()``: cross-references in ``formatted``."""
    if len(text_table) == 0:
        return _xref_frame(None)
    cols = _tei_xrefs_columns(
        _series_list(text_table["formatted"]), _series_list(text_table["text_id"])
    )
    return _xref_frame(cols)


def _tei_url_columns(formatted: Sequence[Any], text_ids: Sequence[Any]) -> dict[str, list[Any]]:
    rows = [
        (xml_attr(node, "target"), xml_text(node), text_ids[k])
        for k, node in _html_refs(formatted, "//ref[@type='url']")
    ]
    return {
        "href": [r[0] for r in rows],
        "link_text": [r[1] for r in rows],
        "text_id": [r[2] for r in rows],
    }


def _url_frame(cols: dict[str, list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "href": _column(cols["href"], "string"),
            "link_text": _column(cols["link_text"], "string"),
            "text_id": _column(cols["text_id"], "Int64"),
        }
    )


def _tei_url(text_table: pd.DataFrame) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.tei_url()``: URL references in ``formatted``."""
    if len(text_table) == 0:
        return _url_frame({"href": [], "link_text": [], "text_id": []})
    return _url_frame(
        _tei_url_columns(_series_list(text_table["formatted"]), _series_list(text_table["text_id"]))
    )


# ---------------------------------------------------------------------------
# bibliography
# ---------------------------------------------------------------------------


def _persons(ref: Any, xpath: str) -> str:
    names = []
    for a in xml_find_all(ref, xpath):
        forename = _xml_find_text(a, ".//forename", join=" ")
        surname = _xml_find_text(a, ".//surname", join=" ")
        names.append(f"{surname}, {forename}")
    return "; ".join(names)


def _xml2bib(ref: Any) -> dict[str, Any]:
    """Port of ``R/import-grobid.R::.xml2bib()``: one ``<biblStruct>`` as a named list."""
    b: dict[str, Any] = {"bib_type": "misc"}
    b["doi"] = _xml_find1_text(ref, ".//idno[@type='DOI']")
    b["title"] = _xml_find1_text(ref, ".//title[@level='a']")
    b["authors"] = _persons(ref, ".//author //persName")
    b["editors"] = _persons(ref, ".//editor //persName")
    b["journal"] = _xml_find1_text(ref, ".//title[@level='j']")
    b["booktitle"] = _xml_find1_text(ref, ".//title[@level='m']")

    imprint = xml_find_first(ref, ".//imprint")
    b["publisher"] = _xml_find1_text(imprint, ".//publisher")
    b["year"] = _xml_find1_text(imprint, ".//date[@type='published']")
    b["volume"] = _xml_find1_text(imprint, ".//biblScope[@unit='volume']")
    b["issue"] = _xml_find1_text(imprint, ".//biblScope[@unit='issue']")
    page_unit = xml_find_first(imprint, ".//biblScope[@unit='page']")
    if page_unit is not None:
        pages = xml_text(page_unit)
        if pages == "":
            attrs = {
                (k.rsplit("}", 1)[-1] if k.startswith("{") else k): v
                for k, v in reversed(page_unit.attrib.items())
            }
            if "from" not in attrs or "to" not in attrs:
                raise IndexError("subscript out of bounds")
            b["pages"] = f"{attrs['from']}-{attrs['to']}"
            b["first_page"] = attrs["from"]
            b["last_page"] = attrs["to"]
        else:
            b["pages"] = pages
            b["first_page"] = pages

    if b["journal"] != "":
        b["bib_type"] = "article"
        b["container"] = b["journal"]
        if b.get("year") is None or b["year"] == "":
            b["year"] = xml_text(xml_find_first(ref, ".//note"))
    elif b["booktitle"] != "":
        if b.get("title") is None or b["title"] == "":
            b["bib_type"] = "book"
            b["title"] = b["booktitle"]
        else:
            b["bib_type"] = "incollection"
            b["container"] = b["booktitle"]

    for key in ("booktitle", "journal", "pages"):
        b.pop(key, None)
    return b


def _tei_bib_columns(xml: Any) -> dict[str, list[Any]]:
    refs = xml_find_all(xml, "//listBibl //biblStruct")
    if not refs:
        return {"bib_id": [], "bib_text": []}
    records = [_xml2bib(r) for r in refs]
    names: list[str] = []
    for r in records:
        for k in r:
            if k not in names:
                names.append(k)
    cols: dict[str, list[Any]] = {k: [r.get(k) for r in records] for k in names}
    n = len(records)

    ids = gsub("b", "", xml_attr(refs, "id"))
    cols["bib_id"] = list(coerce_column(pd.Series(ids, dtype=object), "integer"))

    raw = _xml_find_text(refs, ".//note[@type='raw_reference']")
    raw = [sub("[\t\r\n ]+$", "", sub("^[\t\r\n ]+", "", t)) for t in gsub(r"\s+", " ", raw)]
    if len(raw) == 1:
        raw = raw * n
    elif len(raw) != n:
        raise ValueError(
            f"Assigned data `value` must be compatible with existing data. "
            f"Existing data has {n} rows. Assigned data has {len(raw)} rows."
        )
    cols["bib_text"] = raw

    # extract first occurrence of year from string
    year = regextract(r"\b[12]\d{3}[a-z]?\b", cols["year"])
    digits = gsub(r"\D", "", year)
    cols["year"] = list(coerce_column(pd.Series(digits, dtype=object), "integer"))
    cols["year_suffix"] = gsub(r"\d", "", year)
    return cols


def _tei_bib(xml: Any) -> pd.DataFrame:
    """Port of ``R/import-grobid.R::.tei_bib()``: the bibliography table."""
    return _bib_frame(_tei_bib_columns(xml))


def _bib_frame(cols: dict[str, list[Any]]) -> pd.DataFrame:
    types = dict(table_columns("bib"))
    data: dict[str, pd.Series] = {}
    for name, values in cols.items():
        typ = types.get(name, "string")
        if name == "bib_id" and not values:
            typ = "string"  # R: bib_id = character(0) when there are no refs
        dtype = {"integer": "Int64", "number": "float64", "boolean": "boolean"}.get(typ, "string")
        data[name] = _column(values, dtype)
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def _is_data_shaped(g: list[str]) -> bool:
    nz = [c for c in g if c != ""]
    if not nz:
        return False
    is_ordinal = grepl(r"^[0-9]{1,2}\.?$", nz)
    looks_numeric = [
        a or b or c == "-"
        for a, b, c in zip(grepl("^[<>]?[-+]?[.0-9]", nz), grepl(r"^\[.*\]$", nz), nz, strict=True)
    ]
    n_non_ord = sum(not o for o in is_ordinal)
    if n_non_ord == 0:
        return False
    numeric = sum(ln and not o for ln, o in zip(looks_numeric, is_ordinal, strict=True))
    return numeric / n_non_ord >= 0.5 and sum(is_ordinal) / len(nz) < 0.5


def _tei_table_contents(tab_node: Any) -> list[list[str]] | None:
    """Port of ``R/import-grobid.R::.tei_table_contents()``.

    The rows of a Grobid ``<table>`` as ``[header, *data_rows]``: spanning
    cells (``cols``) are repeated across their columns, ragged rows padded
    with ``""``, and leading header-like rows folded into one header
    (joined with ``" / "``). ``None`` when the node is missing or empty.
    """
    if tab_node is None:
        return None
    rows = xml_find_all(tab_node, ".//row")
    if not rows:
        return None
    grids: list[list[str]] = []
    for row in rows:
        cells = xml_find_all(row, ".//cell")
        grid: list[str] = []
        for cell in cells:
            txt = gsub(r"\s+", " ", xml_text(cell, trim=True))
            span_attr = xml_attr(cell, "cols")
            span = pd.NA
            if span_attr is not None:
                span = coerce_column(pd.Series([span_attr], dtype=object), "integer").iloc[0]
            n = 1 if _na(span) or int(span) < 1 else int(span)
            grid.extend([txt] * n)
        grids.append(grid)
    width = max(len(g) for g in grids)
    if width == 0:
        return None
    grids = [g + [""] * (width - len(g)) for g in grids]

    header_end = 1
    for i, g in enumerate(grids, 1):
        if _is_data_shaped(g):
            header_end = i - 1
            break
        header_end = i
    header_end = max(header_end, 1)
    if header_end == 1:
        header = grids[0]
    else:
        header = []
        for ci in range(width):
            parts = [g[ci] for g in grids[:header_end] if g[ci] != ""]
            header.append(" / ".join(parts))
    return [header, *grids[header_end:]]


# ---------------------------------------------------------------------------
# .grobid_to_bibr()
# ---------------------------------------------------------------------------


@functools.cache
def _bibr_version() -> str:
    m = regexec(r"(?<=\(v)[\d\.]+", str(load_schema().get("description", "")), perl=True)
    return m[0] if m else ""


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()[:16]


def _header_text(h: Any) -> Any:
    if h is None:
        return None
    doc = read_xml(f"<p>{h}</p>")
    head = xml_find_first(doc, "//head")
    if head is None:
        return xml_text(doc)
    text = xml_text(head)
    n = xml_attr(head, "n")
    return text if n is None else f"{n} {text}"


def _count_sections(
    section_id: list[Any], header: list[Any], section_type: list[Any]
) -> list[tuple[Any, Any, Any]]:
    """Distinct ``(section_id, header, section_type)`` as ``dplyr::count()`` sorts them."""
    keys = list(dict.fromkeys(zip(section_id, header, section_type, strict=True)))

    def key(k: tuple[Any, Any, Any]) -> tuple[Any, ...]:
        sid, h, st = k
        return (
            _na(sid),
            0.0 if _na(sid) else float(sid),
            h is None,
            "" if h is None else h,
            st is None,
            "" if st is None else st,
        )

    return sorted(keys, key=key)


def _empty_eq() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "text_id": _column([], "Int64"),
            "grp_id": _column([], "Int64"),
            "lhs": _column([], "string"),
            "df": _column([], "string"),
            "comp": _column([], "string"),
            "rhs": _column([], "string"),
        }
    )


def _extract_eq(paper: Paper) -> pd.DataFrame:
    try:
        from pytacheck.text.extract import extract_eq
    except ImportError:  # pragma: no cover - until pytacheck.text.extract is ported
        warnings.warn(
            "pytacheck.text.extract.extract_eq() is not available; the eq table is empty",
            stacklevel=3,
        )
        return _empty_eq()
    eq = extract_eq(paper)
    if eq is None:
        return _empty_eq()
    eq = eq.copy()
    if "paper_id" in eq.columns:
        del eq["paper_id"]
    return eq.reset_index(drop=True)


def _grobid_to_bibr(xml_path: PathLikeStr, pb: Any = None, schema_version: Any = None) -> Paper:
    """Port of ``R/import-grobid.R::.grobid_to_bibr()``: one TEI XML file to a paper.

    ``schema_version=None`` (the default, as in metacheck) converts as
    metacheck always has; ``"12.0"`` makes a paper in bibr export schema 12.x
    form (:func:`pytacheck.io.grobid_bibr12._grobid_to_bibr12`).
    ``pb`` (R's progress bar) is accepted for signature compatibility and ignored.
    """
    from pytacheck.papers.io import coerce_paper

    del pb
    if schema_version is not None:
        from pytacheck.io.grobid_bibr12 import _grobid_to_bibr12

        return _grobid_to_bibr12(xml_path, schema_version)

    path_str = os.fspath(xml_path)
    path = Path(path_str)
    xml = _xml_read_grobid(path)

    file_hash = _file_hash(path)
    paper_id = sub(r"\.xml", "", path.name)
    p = Paper(paper_id)

    # info ----
    title = _xml_find1_text(xml, ".//titleStmt/title")
    keywords: list[str] | None = _xml_find_text(xml, ".//textClass/keywords/term")
    if keywords is not None and keywords[0] == "":
        keywords = None
    doi = _xml_find1_text(xml, ".//idno[@type='DOI']")
    grobid_version = xml_attr(xml_find_first(xml, ".//application[@ident='GROBID']"), "version")
    input_format = "Unknown TEI XML" if grobid_version is None else f"grobid {grobid_version}"
    p.info = pd.DataFrame(
        {
            "title": _column([title], "string"),
            "keywords": pd.Series([keywords], dtype=object),
            "doi": _column([doi], "string"),
            "file_hash": _column([file_hash], "string"),
            "input_format": _column([input_format], "string"),
            "file_name": _column([path_str], "string"),
            "bibr_version": _column([_bibr_version()], "string"),
            "paper_type": _column(["unknown"], "string"),
            "paper_type_confidence": _column([0.0], "float64"),
            "oecd_l1": _column([None], "string"),
            "oecd_l2": _column([None], "string"),
            "oecd_confidence": _column([None], "float64"),
        }
    )

    # author ----
    p.author = _tei_authors(xml)

    # text ----
    tt = _tei_text_table(xml)

    # section ----
    sec = _count_sections(tt["section_id"], tt["header"], tt["section_type"])

    # remove header-only rows (formatted identical to the raw header)
    keep = [
        k
        for k, (f, h) in enumerate(zip(tt["formatted"], tt["header"], strict=True))
        if not (f is not None and h is not None and f == h)
    ]
    text_cols = ("text", "text_id", "paragraph_id", "section_id", "page_number", "formatted")
    text: dict[str, list[Any]] = {c: [tt[c][k] for k in keep] for c in text_cols}

    section: dict[str, list[Any]] = {
        "section_id": [s[0] for s in sec],
        "header": [_header_text(s[1]) for s in sec],
        "parent_section_id": [None] * len(sec),
        "section_type": [s[2] for s in sec],
        "classification_score": [None] * len(sec),
    }

    # figure / table ----
    def sections_of(kind: str) -> list[Any]:
        return [
            None if st is None else sid
            for sid, st in zip(section["section_id"], section["section_type"], strict=True)
            if st is None or st == kind
        ]

    fig_sec = sections_of("figure")
    p.figure = pd.DataFrame(
        {
            "figure_id": _column(range(1, len(fig_sec) + 1), "Int64"),
            "section_id": _column(fig_sec, "Int64"),
            "image": _column([None] * len(fig_sec), "string"),
            "page_number": _column([None] * len(fig_sec), "Int64"),
        }
    )

    tab_sec = sections_of("table")
    html: list[Any] = [None] * len(tab_sec)
    contents: list[Any] = [None] * len(tab_sec)
    tabs = xml_find_all(xml, "//figure[@type='table']")
    if len(tabs) == len(tab_sec):
        n_tab = len(tab_sec)
        for i, tab in enumerate(tabs):
            tab_node = xml_find_first(tab, ".//table")
            html[i] = "NA" if tab_node is None else as_character(tab_node)
            value = _tei_table_contents(tab_node)
            if value is not None:
                contents[i] = value
                continue
            # R: `paper$table$contents[[i]] <- NULL` drops element i; the
            # tibble then recycles a length-1 list and rejects other lengths
            shortened = contents[:i] + contents[i + 1 :]
            if len(shortened) != 1:
                raise ValueError(
                    "Assigned data `*vtmp*` must be compatible with existing data. "
                    f"Existing data has {n_tab} rows. Assigned data has {len(shortened)} rows."
                )
            contents = shortened * n_tab
    p.table = pd.DataFrame(
        {
            "table_id": _column(range(1, len(tab_sec) + 1), "Int64"),
            "section_id": _column(tab_sec, "Int64"),
            "html": _column(html, "string"),
            "contents": pd.Series(contents, dtype=object),
            "page_number": _column([None] * len(tab_sec), "Int64"),
        }
    )

    # bib ----
    bib = _tei_bib_columns(xml)
    n_bib = len(bib["bib_text"])
    if n_bib > 0:
        if not sec:
            # R: sapply() over zero headers returns list(), and bind_rows()
            # cannot combine that list column with the "References" row
            raise ValueError("Can't combine `..1$header` <list> and `..2$header` <character>.")
        section_id = _r_max(section["section_id"]) + 1
        section["section_id"].append(section_id)
        section["header"].append("References")
        section["parent_section_id"].append(None)
        section["section_type"].append("references")
        section["classification_score"].append(None)
        start_t = _r_max(text["text_id"])
        start_p = _r_max(text["paragraph_id"])
        text_ids = [start_t + k for k in range(1, n_bib + 1)]
        text["text"].extend(bib["bib_text"])
        text["text_id"].extend(text_ids)
        text["paragraph_id"].extend(start_p + k for k in range(1, n_bib + 1))
        text["section_id"].extend([section_id] * n_bib)
        text["page_number"].extend([None] * n_bib)
        text["formatted"].extend([None] * n_bib)
        bib["text_id"] = text_ids
    del bib["bib_text"]
    p.bib = _bib_frame(bib)

    p.section = pd.DataFrame(
        {
            "section_id": _column(section["section_id"], "Int64"),
            "header": _column(section["header"], "string"),
            "parent_section_id": _column(section["parent_section_id"], "Int64"),
            "section_type": _column(section["section_type"], "string"),
            "classification_score": _column(section["classification_score"], "float64"),
        }
    )

    # xref ----
    formatted = text["formatted"]
    text_id = text["text_id"]
    p.xref = _xref_frame(_tei_xrefs_columns(formatted, text_id))

    # url ----
    url = _tei_url_columns(formatted, text_id)
    href = gsub(r"\.$", "", gsub(r"\s", "", url["href"]))
    link_text = url["link_text"]
    strip_scheme = gsub("^https?://", "", href)
    link_nospace = gsub("^https?://", "", gsub(r"\s", "", link_text))
    body = text["text"]
    for lt, h in zip(link_text, href, strict=True):
        if lt is None:
            body = [None] * len(body)
            continue
        if lt == "":
            raise ValueError("zero-length pattern")
        body = [
            t if t is None or lt not in t else (None if h is None else t.replace(lt, h))
            for t in body
        ]
    text["text"] = body
    link_text = [
        None if (a is not None and b is not None and a == b) else lt
        for a, b, lt in zip(strip_scheme, link_nospace, link_text, strict=True)
    ]
    p.url = _url_frame({"href": href, "link_text": link_text, "text_id": url["text_id"]})

    p.text = pd.DataFrame(
        {
            "text": _column(text["text"], "string"),
            "text_id": _column(text["text_id"], "Int64"),
            "paragraph_id": _column(text["paragraph_id"], "Int64"),
            "section_id": _column(text["section_id"], "Int64"),
            "page_number": _column(text["page_number"], "Int64"),
            "formatted": _column(text["formatted"], "string"),
        }
    )

    # eq ----
    p.eq = _extract_eq(p)

    return coerce_paper(p)


# ---------------------------------------------------------------------------
# grobid_to_bibr()
# ---------------------------------------------------------------------------


def _list_files(path: PathLikeStr, pattern: str, recursive: bool = False) -> list[str]:
    """``list.files(path, pattern, full.names = TRUE, ignore.case = TRUE)`` (sorted).

    As with ``all.files = FALSE``, names starting with a dot are skipped (and
    hidden directories are not searched). Without *recursive*, directories
    whose name matches are listed too, as in R.
    """

    def walk(d: Path) -> list[Path]:
        out: list[Path] = []
        for p in d.iterdir():
            if p.name.startswith("."):
                continue
            if recursive and p.is_dir():
                out.extend(walk(p))
            elif grepl(pattern, p.name, ignore_case=True):
                out.append(p)
        return out

    root = os.fspath(path)  # full.names pastes the directory as given
    base = Path(root)
    rel = sorted(p.relative_to(base).as_posix() for p in walk(base))
    return [f"{root}/{r}" for r in rel]


def grobid_to_bibr(
    xml_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr | None = ".",
    crossref_lookup: bool = False,
    schema_version: str | None = "12.0",
) -> Any:
    """Port of ``R/import-grobid.R::grobid_to_bibr()``: convert Grobid TEI XML to papers.

    Parameters
    ----------
    xml_path:
        A TEI XML file, a list of them, or a directory of ``.xml`` files.
    save_path:
        Directory to save the JSON files in; ``None`` returns paper objects
        instead.
    crossref_lookup:
        Whether to add a ``bib_match`` table from CrossRef.
    schema_version:
        ``"12.0"`` (the default) makes papers in bibr export schema 12.x form
        and saves bibr 12.0 files (see :func:`pytacheck.paper_write`). Their
        source is the PDF Grobid read when it is next to the XML
        (``published.pdf`` for ``published.pdf.tei.xml`` or ``published.xml``),
        else the XML file. ``None`` converts as metacheck does by default (its
        older paper format, saved as a paper object).

        Deliberate difference: metacheck's default is ``NULL`` (the older
        format); bibr export schema 12.0 is the schema pytacheck targets.

    Returns
    -------
    With ``save_path=None``: a :class:`Paper` for one file, else a
    :class:`PaperList` (failed files dropped). Otherwise the saved JSON
    path(s) (``None`` for files that failed).
    """
    if schema_version is not None and (
        not isinstance(schema_version, str) or schema_version != "12.0"
    ):
        raise ValueError('schema_version must be None or "12.0"')

    if isinstance(xml_path, str | PathLike):
        paths: list[PathLikeStr] = [xml_path]
    elif isinstance(xml_path, Sequence):
        paths = list(xml_path)
    else:
        raise TypeError("invalid filename argument")
    if len(paths) == 1 and Path(paths[0]).is_dir():
        paths = list(_list_files(paths[0], r"\.xml$"))

    errors = 0
    results: list[Any] = []
    for xp in paths:
        try:
            p: Paper | None = _grobid_to_bibr(xp, None, schema_version)
        except Exception as exc:
            errors += 1
            logger("grobid_to_bibr", {"xml_path": os.fspath(xp), "error": str(exc)})
            p = None
        if not is_paper(p):
            results.append(None)
            continue
        if crossref_lookup is True or (  # isTRUE(crossref_lookup)
            type(crossref_lookup).__name__ == "bool_" and bool(crossref_lookup)
        ):
            from pytacheck.db.crossref import add_bib_match

            p = add_bib_match(p)
        if save_path is None:
            results.append(p)
            continue
        from pytacheck.papers.io import paper_write

        file_name = gsub(r"\.xml$", "", Path(xp).name)
        results.append(str(paper_write(p, file_name, save_path, schema_version)))

    if errors > 0:
        e = "" if errors == 1 else f"1:{errors}"
        warnings.warn(
            f"There {plural(errors, 'was', 'were')} {errors} error{plural(errors)}; "
            f"use lastlog({e})",
            stacklevel=2,
        )

    if save_path is not None:
        return results[0] if len(paths) == 1 else results
    if len(results) > 1:
        return PaperList([r for r in results if r is not None])
    if not results:
        raise IndexError("subscript out of bounds")
    return results[0]


# ---------------------------------------------------------------------------
# Grobid server
# ---------------------------------------------------------------------------


def _url_with_path(api_url: str, path: str) -> str:
    """``httr2::req_url_path(request(api_url), path)``: replace the URL path."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(api_url)
    if not parts.scheme:
        return api_url.rstrip("/") + path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _grobid_isalive(api_url: str, error: bool = True) -> bool:
    """Port of ``R/import-grobid.R::.grobid_isalive()``: is a Grobid server up?"""
    from pytacheck import http

    url = _url_with_path(api_url, "/api/isalive")
    failure = None
    try:
        resp = http.request("GET", url, max_tries=3, retry_statuses=(429, 503), timeout=15)
    except Exception as exc:
        resp, failure = None, str(exc)
    if resp is None:
        if error:
            reason = failure or "Could not connect to server"
            raise ConnectionError(
                "Connection to the GROBID server failed! Please check your connection "
                f"or the URL: {api_url} ({reason})"
            )
        return False
    status = resp.status_code
    if status != 200 and error:
        raise RuntimeError(
            f"GROBID server does not appear up and running on the provided URL. Status: {status}"
        )
    return status == 200


def _reason(status: int) -> str:
    """``stop(httr2::resp_status_desc(resp))``: the message (``"NA"`` if unknown)."""
    from pytacheck.io.bibr_convert import _status_desc

    return _status_desc(status) or "NA"


def convert_grobid(
    file_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr | Sequence[PathLikeStr] | None = ".",
    api_url: str = "https://grobid.hti.ieis.tue.nl",
    start_page: int = -1,
    end_page: int = -1,
    consolidate_citations: int = 0,
    consolidate_header: int = 0,
    consolidate_funders: int = 0,
) -> Any:
    """Port of ``R/import-grobid.R::convert_grobid()``: convert PDF(s) to Grobid TEI XML.

    Sends each PDF to a Grobid server (by default the GDPR-compliant server
    of Eindhoven University of Technology) and saves the TEI XML. With
    ``save_path=None`` the XML is read and a paper object returned. Several
    files (or a directory) give a list of XML paths, ``None`` for failures.
    """
    paths: list[str] = (
        [os.fspath(file_path)]
        if isinstance(file_path, str | PathLike)
        else [os.fspath(f) for f in file_path]
    )
    if not all(Path(f).exists() for f in paths):
        raise FileNotFoundError("Files do not exist")

    _grobid_isalive(api_url)

    if len(paths) > 1:
        if save_path is None:
            save_path = "."
        if isinstance(save_path, str | PathLike):
            Path(save_path).mkdir(exist_ok=True)
            save_paths: list[Any] = [os.fspath(save_path)] * len(paths)
        else:
            save_paths = [os.fspath(s) for s in save_path]
        if len(save_paths) != len(paths):
            raise ValueError(
                "The argument save_path must be a single directory name or a vector of file "
                "names with the same length as the number of files to convert."
            )
        xmls: list[str | None] = []
        messages: list[str] = []
        for pdf, sp in zip(paths, save_paths, strict=True):
            try:
                xml = convert_grobid(
                    pdf,
                    sp,
                    api_url,
                    start_page,
                    end_page,
                    consolidate_citations,
                    consolidate_header,
                    consolidate_funders,
                )
                messages.append("")
            except Exception as exc:
                logger("convert_grobid", {"error": str(exc)})
                xml, messages = None, [*messages, str(exc)]
            xmls.append(xml)
        errors = [x is None or not Path(x).exists() for x in xmls]
        if any(errors):
            detail = "\n".join(
                f" * {f}: {m}" for f, m, e in zip(paths, messages, errors, strict=True) if e
            )
            warnings.warn(
                f"{sum(errors)} of {len(xmls)} files did not convert: \n{detail}", stacklevel=2
            )
            xmls = [None if e else x for x, e in zip(xmls, errors, strict=True)]
        return xmls

    if Path(paths[0]).is_dir():
        pdfs = _list_files(paths[0], r"\.pdf", recursive=True)
        if not pdfs:
            warnings.warn(f"There are no PDF files in the directory {paths[0]}", stacklevel=2)
        return convert_grobid(pdfs, save_path, api_url)

    pdf = Path(paths[0])
    from pytacheck import http

    # the multipart fields, in the order httr2 sends them
    fields: list[tuple[str, Any]] = [
        ("input", (pdf.name, pdf.read_bytes(), "application/pdf")),
        ("start", (None, r_as_character(start_page))),
        ("end", (None, r_as_character(end_page))),
        ("consolidateCitations", (None, r_as_character(consolidate_citations))),
        ("consolidateHeader", (None, r_as_character(consolidate_header))),
        ("consolidateFunders", (None, r_as_character(consolidate_funders))),
        ("includeRawCitations", (None, "1")),
    ]
    try:
        resp = http.request(
            "POST",
            _url_with_path(api_url, "/api/processFulltextDocument"),
            max_tries=1,
            files=fields,
            timeout=180,
        )
    except Exception as exc:  # invalid URL etc.
        raise ConnectionError(f"Failed to perform HTTP request: {exc}") from exc
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {api_url}")
    if resp.status_code >= 400:
        raise RuntimeError(_reason(resp.status_code))
    content = resp.content

    if save_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        save_file = tmp
    elif Path(save_path).is_dir():
        base = sub(r"\.pdf", "", pdf.name, ignore_case=True) + ".xml"
        save_file = os.path.join(os.fspath(save_path), base)
    else:
        sp = os.fspath(save_path)
        Path(sp).parent.mkdir(parents=True, exist_ok=True)
        save_file = sub(r"\.xml", "", sp, ignore_case=True) + ".xml"

    Path(save_file).write_bytes(content)

    if save_path is None:
        from pytacheck.io.read import read

        try:
            return read(save_file)
        finally:
            Path(save_file).unlink(missing_ok=True)
    return save_file
