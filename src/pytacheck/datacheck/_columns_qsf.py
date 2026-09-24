"""Qualtrics survey-definition (``.qsf``) parsing (part of :mod:`pytacheck.datacheck.columns`).

Port of the ``.qsf`` section of ``R/data_check_helpers.R``: ``parse_qsf()``
and its helpers. A ``.qsf`` is JSON; the question elements (``Element ==
"SQ"``) carry the wording and response options, and the exported data column
names are reconstructed from ``DataExportTag`` / ``ChoiceDataExportTags``.

JSON is parsed as ``jsonlite::fromJSON(simplifyVector = FALSE)`` does: objects
are ordered ``(key, value)`` pairs (:class:`_JsonObject`) and R's ``$``
operator is emulated with its partial matching (``opt$Display`` finds a lone
``DisplayLogic`` when ``Display`` is absent).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import trimws
from pytacheck._r.frames import bind_rows
from pytacheck._r.regex import grepl, gsub
from pytacheck.datacheck._columns_labels import (
    _chr,
    _encode_value_labels,
    _json_loads,
    _json_scalar_chr,
    _JsonObject,
    _tolower,
    chr_frame,
)

#: R's NULL for an argument that was not passed (distinct from NA, ``None``)
_NULL = object()


def _dollar(x: Any, name: str) -> Any:
    """R's ``x$name`` on a jsonlite list: exact match first, else a unique prefix."""
    if x is None:
        return None
    if isinstance(x, _JsonObject):
        for k, v in x:
            if k == name:
                return v
        hits = [v for k, v in x if k.startswith(name)]
        return hits[0] if len(hits) == 1 else None
    if isinstance(x, list):
        return None
    raise TypeError("$ operator is invalid for atomic vectors")


def _dbl_bracket(x: Any, name: str) -> Any:
    """R's ``x[[name]]`` on a named list (exact matching; ``NULL`` when absent)."""
    if isinstance(x, _JsonObject):
        for k, v in x:
            if k == name:
                return v
    return None


def _as_chr1(x: Any) -> str | None:
    """``as.character(x)[1]`` of a jsonlite value."""
    if isinstance(x, _JsonObject):
        vals = x.values_()
        return _as_chr1(vals[0]) if vals else None
    if isinstance(x, list):
        return _as_chr1(x[0]) if x else None
    return _json_scalar_chr(x)


def _qsf_strip_html(x: Any) -> str | None:
    """Port of ``.qsf_strip_html()``: Qualtrics display HTML -> plain text (``None`` = NA)."""
    if x is None or (isinstance(x, list) and len(x) == 0):
        return None
    s = _as_chr1(x)
    if s is None:
        return None
    s = gsub("<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = gsub("[[:space:]]+", " ", s)
    s = trimws(s)
    return s if s != "" else None


def _unlist_first(d: Any) -> Any:
    """``unlist(d, use.names = FALSE)[1]``."""
    stack = [d]
    while stack:
        v = stack.pop(0)
        if isinstance(v, _JsonObject):
            stack = list(v.values_()) + stack
        elif isinstance(v, list):
            stack = list(v) + stack
        elif v is not None:
            return v
    return None


def _qsf_option_display(opt: Any) -> str | None:
    """Port of ``.qsf_option_display()``: the display text of a Choices/Answers option."""
    d = _dollar(opt, "Display")
    if d is None:
        d = _dollar(opt, "display")
    if isinstance(d, list):
        d = _unlist_first(d)
    return _qsf_strip_html(d)


def _qsf_value_labels(opts: Any) -> str | None:
    """Port of ``.qsf_value_labels()``: an option list (code -> option) as value-labels JSON."""
    if opts is None or (isinstance(opts, list) and len(opts) == 0):
        return None
    if isinstance(opts, _JsonObject):
        codes: Any = opts.names()
        values = opts.values_()
    elif isinstance(opts, list):
        codes, values = None, list(opts)
    else:
        codes, values = None, [opts]
    labels = [_qsf_option_display(o) for o in values]
    return _encode_value_labels(codes, labels)


def _qsf_export_col(tag: Any, choice_tag: Any, code: Any) -> str:
    """Port of ``.qsf_export_col()``: the export column name of one matrix choice."""
    tag_s = _chr(tag)
    t = None if tag_s is None else trimws(tag_s)
    tp = "NA" if t is None else t  # paste0(NA, "_") is "NA_"
    ct0 = _chr(choice_tag)
    if ct0 is not None and ct0 != "":
        ct = trimws(ct0)
        if _tolower(ct).startswith(_tolower(tp + "_")) or (t is not None and _tolower(ct) == _tolower(t)):
            return ct
        if grepl(r"[A-Za-z].*[._-]", ct, perl=True):
            return ct
        return f"{tp}_{ct}"
    return f"{tp}_{_chr(code)}"


def _is_json_list(x: Any) -> bool:
    return isinstance(x, list)


def _r_length(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, list):
        return len(x)
    return 1


def parse_qsf(path: str | os.PathLike[str]) -> pd.DataFrame | None:
    """Parse a Qualtrics survey-definition file (``.qsf``) into codebook variables.

    Port of ``R/data_check_helpers.R::parse_qsf()``: one row per exported data
    column with the item wording (``label``), the question stem (``question``),
    the coded response options (``value_labels``) and the question's
    ``DataExportTag`` (``scale_group``); ``parse_method`` is ``"qsf"``.
    Returns ``None`` when the file is not a parseable QSF or has no questions.
    """
    p0 = Path(path)
    if not p0.exists():
        return None
    src = _basename(path)
    try:
        text = p0.read_bytes().decode("utf-8", "surrogateescape")
        # jsonlite parses (with a warning) past a UTF-8 byte-order mark
        j = _json_loads(text[1:] if text.startswith("\ufeff") else text)
    except (ValueError, OSError):
        return None
    if j is None:
        return None
    elements = _dollar(j, "SurveyElements")
    if elements is None:
        return None
    elist = elements.values_() if isinstance(elements, _JsonObject) else _as_list(elements)
    sq = [e for e in elist if _dollar(e, "Element") == "SQ"]
    if not sq:
        return None
    rows: list[pd.DataFrame] = []

    def add(
        var: str, label: Any, scale_group: Any, value_labels: Any = None, question: Any = _NULL
    ) -> None:
        v = trimws(var)
        if v == "":
            return
        q = label if question is _NULL else question
        rows.append(
            chr_frame(
                {
                    "codebook_variable": v,
                    "label": label,
                    "codebook_source": src,
                    "group": None,
                    "value_labels": value_labels,
                    "missing_values": None,
                    "question": q,
                    "coding_instructions": None,
                    "scale_group": scale_group,
                },
                1,
            )
        )

    for e in sq:
        p = _dollar(e, "Payload")
        if p is None:
            continue
        tag = _dollar(p, "DataExportTag")
        if tag is None:
            tag = _dollar(p, "QuestionID")
        tag_s = _as_chr1(tag) if tag is not None else None
        if tag is None or (tag_s is not None and trimws(tag_s) == ""):
            continue
        tag_s = trimws(tag_s) if tag_s is not None else "NA"
        qtext = _qsf_strip_html(_dollar(p, "QuestionText"))
        qtype = _as_chr1(_dollar(p, "QuestionType")) or ""
        selector = _as_chr1(_dollar(p, "Selector")) or ""
        choices = _dollar(p, "Choices")
        answers = _dollar(p, "Answers")
        ctags = _dollar(p, "ChoiceDataExportTags")

        def export_col(code: str, _ctags: Any = ctags, _tag: str = tag_s) -> str:
            ct = None
            if _is_json_list(_ctags):
                v = _dbl_bracket(_ctags, code)
                if v is not None:
                    vs = _as_chr1(v)
                    if vs is not None and trimws(vs) != "":
                        ct = trimws(vs)
            return _qsf_export_col(_tag, ct, code)

        is_matrix = grepl("matrix", qtype, ignore_case=True)
        is_multi = grepl("MAVR|MACOL|MSB", selector, ignore_case=True)
        n_choices = _r_length(choices)
        choice_names = choices.names() if isinstance(choices, _JsonObject) else []
        if is_matrix and n_choices:
            vl = _qsf_value_labels(answers)
            for code in choice_names:
                stmt = _qsf_option_display(_dbl_bracket(choices, code))
                add(export_col(code), stmt, tag_s, vl, question=qtext)
        elif is_multi and n_choices:
            for code in choice_names:
                opt = _qsf_option_display(_dbl_bracket(choices, code))
                add(export_col(code), opt, tag_s, question=qtext)
        elif n_choices:
            add(tag_s, qtext, tag_s, _qsf_value_labels(choices))
        else:
            add(tag_s, qtext, tag_s)
    if not rows:
        return None
    out = bind_rows(rows)
    out["parse_method"] = pd.Series(["qsf"] * len(out), dtype="string")
    return out


def _as_list(x: Any) -> list[Any]:
    if isinstance(x, list):
        return list(x)
    return [x]


def _basename(path: str | os.PathLike[str]) -> str:
    """R ``basename()`` (trailing separators removed first)."""
    s = os.fspath(path).rstrip("/")
    return s.rsplit("/", 1)[-1] if s else ""
