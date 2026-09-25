"""Match statistics reported ONLY in a results table's cells.

Port of ``R/match-table.R``: the table-side counterpart to
:mod:`pytacheck.statout.match_reported`'s text-based matching. A table row
(``paper.table["contents"]``, parsed from Grobid's ``<table>`` -- see
:func:`pytacheck.io.grobid._tei_table_contents`) has no anchor words or
sentence boundaries, so its cells are typed with a 5-tier fallback:

1. HEADER typing: ``_stat_family()`` on the column's own header text (the last
   ``" / "``-joined segment of a folded multi-row header);
2. CAPTION typing: for a truly ambiguous header (blank, a bare running number,
   a dash), the table's caption keyword ("correlation", "regression", ...);
3. VALUE-SHAPE typing: a bracketed interval (``"[.16, .29]"``) is a CI whatever
   its header says (tried first);
4. ROW-AS-TEST: every typed cell of one data row is bundled into ONE test;
5. REPEATED-MATRIX fallback: in a symmetric-matrix-shaped table (numbered
   column headers, e.g. a correlation matrix) each matrix cell is its own
   single-value test.

Column indices (``col``, :func:`_table_matrix_cols`) are 1-based, as in R.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r import grepl, regextract, strsplit, sub, trimws

__all__: list[str] = []

_INT_MAX = 2**31 - 1


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, float | np.floating):
        return bool(np.isnan(x))
    return False


def _tolower(s: str) -> str:
    if s.isascii():
        return s.lower()
    out = []
    for c in s:
        low = c.lower()
        out.append(low if len(low) == 1 else low[0])
    return "".join(out)


def _chr(x: Any) -> str | None:
    """``as.character()`` of a scalar cell; ``None`` for NA."""
    if _is_na(x):
        return None
    if isinstance(x, str):
        return x
    from pytacheck._r import as_character

    return as_character(x)


def _scalar(x: Any) -> Any:
    if _is_na(x):
        return None
    if isinstance(x, np.generic):
        return x.item()
    return x


def _as_rows(content: Any) -> list[list[Any]] | None:
    """A table ``contents`` cell (R list of character vectors) as Python rows."""
    if content is None:
        return None
    if isinstance(content, np.ndarray):
        content = content.tolist()
    if not isinstance(content, list | tuple):
        return None
    rows = []
    for r in content:
        if isinstance(r, np.ndarray):
            r = r.tolist()
        if isinstance(r, str) or not isinstance(r, list | tuple):
            r = [r]
        rows.append(list(r))
    return rows


def _table_caption_family(caption: Any) -> str | None:
    """The statistic family a table's CAPTION suggests for ambiguous columns.

    Port of ``R/match-table.R::.table_caption_family()``: ``"r"`` for a
    correlation table, ``"b"`` for regression/coefficients, ``"alpha"`` for
    reliability, ``"mean"`` for descriptives; ``None`` (R ``NA``) otherwise.
    """
    if caption is None:
        caption = ""
    cap_s = _chr(caption)
    if cap_s is None:
        return None
    cap = _tolower(cap_s)
    if cap == "":
        return None
    if grepl("correlat", cap):
        return "r"
    if grepl("regression|coefficient|fixed.effect", cap):
        return "b"
    if grepl("reliabilit|cronbach|internal consist", cap):
        return "alpha"
    if grepl("descriptive|means? and standard", cap):
        return "mean"
    return None


def _table_caption(paper: Any, section_id: Any) -> str | None:
    """The caption text for a table, looked up via ``paper.text``.

    Port of ``R/match-table.R::.table_caption()``: the text rows sharing the
    table's ``section_id`` (Grobid's figDesc), pasted with spaces; ``None``
    (R ``NA``) when there is none.
    """
    if _is_na(section_id):
        return None
    txt = paper.get("text") if hasattr(paper, "get") else None
    if not isinstance(txt, pd.DataFrame) or len(txt) == 0:
        return None
    if "section_id" not in txt.columns:
        return None
    sid = txt["section_id"]
    hit = (sid == section_id).fillna(False).astype(bool) & sid.notna()
    if not hit.any():
        return None
    if "text" not in txt.columns:
        return ""  # paste(NULL, collapse = " ")
    texts = txt["text"][hit].tolist()
    return " ".join("NA" if _is_na(t) else str(t) for t in texts)


def _table_header_ambiguous(header: Any) -> bool:
    """Is a column header ambiguous (blank, a bare running number, a dash)?

    Port of ``R/match-table.R::.table_header_ambiguous()``.
    """
    if header is None:
        header = ""
    h_s = _chr(header)
    if h_s is None:
        return False  # nzchar(NA) is TRUE and grepl(NA) FALSE
    h = trimws(h_s)
    return h == "" or bool(grepl(r"^[0-9]+\.?$|^-+$", h))


def _last_segment(h: Any, trim: bool = False) -> str | None:
    """The last ``" / "``-joined segment (``strsplit(..., fixed = TRUE)``)."""
    s = _chr(h)
    if s is None:
        return None
    if trim:
        s = trimws(s)
    parts = strsplit(s, " / ", fixed=True)
    return parts[-1] if parts else ""


def _table_typed_cells(content: Any, caption: Any) -> list[list[dict[str, Any]]]:
    """Type every cell in a table's data rows (tiers 1-3).

    Port of ``R/match-table.R::.table_typed_cells()``. Returns one list per
    data row of ``col``/``family``/``name``/``value``/``dec``/``censored``
    dicts, one per cell that resolved to SOME family; untyped cells (a genuine
    label column) are dropped.
    """
    from pytacheck.statout.match_reported import (
        _norm_interval,
        _norm_value,
        _stat_family_one,
    )

    rows = _as_rows(content)
    if rows is None or len(rows) < 2:
        return []
    header = rows[0]
    data_rows = rows[1:]
    w = len(header)

    hdr_last = [_last_segment(h) for h in header]
    hdr_fam = [_stat_family_one(h) for h in hdr_last]
    # an NA header segment is NOT ambiguous (nzchar(NA) is TRUE in R)
    ambiguous = [False if h is None else _table_header_ambiguous(h) for h in hdr_last]
    cap_fam = _table_caption_family(caption)

    out_rows: list[list[dict[str, Any]]] = []
    for cells in data_rows:
        n = min(len(cells), w)
        out: list[dict[str, Any]] = []
        for i in range(n):
            ci = i + 1
            raw = _chr(cells[i])
            val = None if raw is None else trimws(raw)
            if val == "":
                continue
            # Tier 3 (value-shape) first: a bracketed CI is unambiguous.
            ivl = _norm_interval(val)
            if ivl is not None:
                lo, hi = ivl["lo"], ivl["hi"]
                out.append(
                    {
                        "col": ci,
                        "family": "ci_lower",
                        "name": "ci_lower",
                        "value": lo["num"],
                        "dec": lo["dec"],
                        "censored": "",
                    }
                )
                out.append(
                    {
                        "col": ci,
                        "family": "ci_upper",
                        "name": "ci_upper",
                        "value": hi["num"],
                        "dec": hi["dec"],
                        "censored": "",
                    }
                )
                continue
            if hdr_fam[i] is not None:
                fam = hdr_fam[i]
            elif ambiguous[i]:
                fam = cap_fam
            else:
                continue
            if fam is None:
                continue
            # A cell must look like an actual VALUE, not merely start with a digit.
            if val is None or not grepl(r"^[<>]?\s*[-+]?[0-9.]", val):
                continue
            nv = _norm_value(val)
            if nv["num"] is None:
                continue
            # Reject "4. Neuroticism"-shaped cells: a numeric prefix followed
            # by a word.
            stripped = sub(r"^[<>]?\s*", "", val)
            consumed = regextract(r"^[-+]?[0-9.,]+", stripped) or ""
            remainder = stripped[len(consumed) :]
            if trimws(remainder) != "" and grepl("[a-zA-Z]{2,}", remainder):
                continue
            out.append(
                {
                    "col": ci,
                    "family": fam,
                    "name": header[i] if not ambiguous[i] else fam,
                    "value": nv["num"],
                    "dec": nv["dec"],
                    "censored": nv["censored"],
                }
            )
        out_rows.append(out)
    return out_rows


def _table_matrix_cols(content: Any) -> list[int]:
    """Column indices (1-based) of a symmetric-matrix-shaped table.

    Port of ``R/match-table.R::.table_matrix_cols()``: the header columns
    whose last ``" / "`` segment is a bare running number (``"1."``,
    ``"2"``); empty unless there are at least two of them.
    """
    rows = _as_rows(content)
    if rows is None or len(rows) < 2:
        return []
    header = rows[0]
    last_seg = [_last_segment(h, trim=True) for h in header]
    num_cols = [i + 1 for i, s in enumerate(last_seg) if s is not None and grepl(r"^[0-9]+\.?$", s)]
    if len(num_cols) < 2:
        return []
    return num_cols


def _neg_id(x: Any) -> Any:
    """``-(x)`` with R integer overflow (-> NA) for integer inputs."""
    if x is None:
        return None
    if isinstance(x, int) and abs(x) > _INT_MAX:
        return None
    return -x


def _table_tests_one(table_id: Any, content: Any, caption: Any) -> list[dict[str, Any]]:
    """Build ``match_reported_output()``'s test shape from ONE table.

    Port of ``R/match-table.R::.table_tests_one()``: tier 4 (a data row's
    non-matrix cells as one test, ``text_id = -(table_id * 1000000 + row)``)
    plus tier 5 (each matrix cell its own single-value test,
    ``text_id = -(table_id * 1000000 + row * 1000 + i)``); ``grp_id`` is 1.
    """
    rows = _as_rows(content)
    if rows is None or len(rows) < 2:
        return []
    matrix_cols = set(_table_matrix_cols(rows))
    typed = _table_typed_cells(rows, caption)
    tid_base = _scalar(table_id)
    tests: list[dict[str, Any]] = []
    for ri, comps in enumerate(typed, start=1):
        if not comps:
            continue
        tid = None if tid_base is None else _neg_id(tid_base * 1000000 + ri)
        in_matrix = [c["col"] in matrix_cols for c in comps]
        row_comps = [c for c, m in zip(comps, in_matrix, strict=True) if not m]
        matrix_comps = [c for c, m in zip(comps, in_matrix, strict=True) if m]
        if row_comps:
            tests.append(
                {
                    "text_id": tid,
                    "grp_id": 1,
                    "components": [{k: v for k, v in c.items() if k != "col"} for c in row_comps],
                }
            )
        for mi, c in enumerate(matrix_comps, start=1):
            mtid = None if tid_base is None else _neg_id(tid_base * 1000000 + ri * 1000 + mi)
            tests.append(
                {
                    "text_id": mtid,
                    "grp_id": 1,
                    "components": [{k: v for k, v in c.items() if k != "col"}],
                }
            )
    return tests


def _table_tests(paper: Any) -> list[dict[str, Any]]:
    """Build reported-test elements from a paper's table contents.

    Port of ``R/match-table.R::.table_tests()``: for every table with parsed
    ``contents``, types each cell (header, caption, value shape), then bundles
    a row's typed cells into one test -- or, for a matrix-shaped table, each
    matrix cell into its own test. Used by
    :func:`~pytacheck.statout.match_reported.match_reported_output` with
    ``include_tables=True``.
    """
    tab = paper.get("table") if hasattr(paper, "get") else None
    if not isinstance(tab, pd.DataFrame) or len(tab) == 0 or "contents" not in tab.columns:
        return []
    from pytacheck.io.bibr12 import is_bibr12

    contents = tab["contents"].tolist()
    has_sid = "section_id" in tab.columns
    has_tid = "table_id" in tab.columns
    section_ids = tab["section_id"].tolist() if has_sid else [None] * len(tab)
    # a table without a table_id is numbered by its position (R's -(NULL *
    # 1000000L + ri) is integer(0), which fails match_reported_output(); U142)
    table_ids = tab["table_id"].tolist() if has_tid else list(range(1, len(tab) + 1))
    # the table's own caption when it has one (bibr 12.x always does);
    # otherwise the text rows sharing its section_id (Grobid's figDesc). R
    # reads only the latter for older papers (U143), and without a section_id
    # column fails, dropping every table test (U142)
    v12 = is_bibr12(paper)
    captions = tab["caption"].tolist() if "caption" in tab.columns else [None] * len(tab)
    out: list[dict[str, Any]] = []
    for i in range(len(tab)):
        content = contents[i]
        if content is None:
            continue
        caption = _scalar(captions[i])
        if not v12 and (caption is None or _is_na(caption)) and has_sid:
            caption = _table_caption(paper, _scalar(section_ids[i]))
        out.extend(_table_tests_one(table_ids[i], content, caption))
    return out
