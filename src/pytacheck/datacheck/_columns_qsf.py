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
    """R's ``x[[name]]`` on a named list (exact matching; ``NULL`` when absent or ``""``)."""
    if isinstance(x, _JsonObject) and name != "":
        for k, v in x:
            if k == name:
                return v
    return None


_RESERVED = frozenset({
    "if", "else", "repeat", "while", "function", "for", "next", "break", "TRUE", "FALSE",
    "NULL", "Inf", "NaN", "NA", "NA_integer_", "NA_real_", "NA_character_", "NA_complex_",
    "in", "...",
})  # fmt: skip


def _is_syntactic(name: str) -> bool:
    """R's ``isValidName()``: may *name* appear unquoted in deparsed code?"""
    if name in _RESERVED or name == "":
        return False
    if name.startswith("..") and name[2:].isdigit():
        return False
    first = name[0]
    if not (first.isalpha() or first == "."):
        return False
    if first == "." and len(name) > 1 and name[1].isdigit():
        return False
    return all(c.isalnum() or c in "._" for c in name)


_DEPARSE_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r",
                    "\a": "\\a", "\b": "\\b", "\f": "\\f", "\v": "\\v"}  # fmt: skip


def _deparse(x: Any) -> str:
    """``deparse1line(x)`` (R's SIMPLEDEPARSE) of a jsonlite ``simplifyVector = FALSE`` value."""
    if x is None:
        return "NULL"
    if isinstance(x, _JsonObject):
        parts = [_deparse(v) if k == "" else
                 f"{k if _is_syntactic(k) else '`' + k + '`'} = {_deparse(v)}" for k, v in x]  # fmt: skip
        return "list(" + ", ".join(parts) + ")"
    if isinstance(x, list):
        return "list(" + ", ".join(_deparse(v) for v in x) + ")"
    if isinstance(x, str):
        out = []
        for ch in x:
            esc = _DEPARSE_ESCAPES.get(ch)
            if esc is not None:
                out.append(esc)
            elif ord(ch) < 0x20 or ord(ch) == 0x7F:
                out.append(f"\\{ord(ch):03o}")
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    return _json_scalar_chr(x) or "NA"


def _as_character(x: Any) -> list[str | None]:
    """R ``as.character(x)`` of a jsonlite value (a list deparses non-string elements)."""
    if x is None:
        return []
    if isinstance(x, list):
        vals = x.values_() if isinstance(x, _JsonObject) else x
        return [v if isinstance(v, str) else _deparse(v) for v in vals]
    return [_json_scalar_chr(x)]


def _as_chr1(x: Any) -> str | None:
    """``as.character(x)[1]`` of a jsonlite value."""
    chars = _as_character(x)
    return chars[0] if chars else None


def _scalar(v: list[bool | None], what: str = "logical(1)") -> bool | None:
    """A logical vector used by ``&&`` / ``||``: length 1, else NA (0) or an error (> 1)."""
    if len(v) > 1:
        raise ValueError(f"'length = {len(v)}' in coercion to '{what}'")
    return v[0] if v else None


def _if(cond: bool | None) -> bool:
    """R ``if (cond)``: an NA condition is an error."""
    if cond is None:
        raise ValueError("missing value where TRUE/FALSE needed")
    return cond


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
    """``unlist(d, use.names = FALSE)[1]``: leaves coerced to their common type."""
    leaves: list[Any] = []
    stack = [d]
    while stack:
        v = stack.pop()
        if isinstance(v, list):
            stack.extend(reversed(v.values_() if isinstance(v, _JsonObject) else v))
        elif v is not None:
            leaves.append(v)
    if not leaves:
        return None
    first = leaves[0]
    if any(isinstance(v, str) for v in leaves):
        return first if isinstance(first, str) else _json_scalar_chr(first)
    if any(isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool)
           and not -2147483647 <= v <= 2147483647) for v in leaves):  # fmt: skip
        return float(first)
    if any(not isinstance(v, bool) for v in leaves):
        return int(first)
    return first


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
        ct: str = trimws(ct0)
        if _tolower(ct).startswith(_tolower(tp + "_")) or (
            t is not None and _tolower(ct) == _tolower(t)
        ):
            return ct
        if grepl(r"[A-Za-z].*[._-]", ct, perl=True):
            return ct
        return f"{tp}_{ct}"
    return f"{tp}_{_chr(code)}"


def _and(lhs: list[bool], rest: bool) -> bool:
    """``if (lhs && rest)`` with a logical vector *lhs* and a scalar *rest*."""
    first = _scalar(lhs)  # type: ignore[arg-type]
    if first is False or not rest:
        return False
    return _if(first)


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
        if tag is None:
            continue
        # R: if (is.null(tag) || !nzchar(trimws(as.character(tag)))) next
        tag_chr = _as_character(tag)
        if _if(_scalar([trimws("NA" if c is None else c) == "" for c in tag_chr])):
            continue
        tag_s = trimws("NA" if tag_chr[0] is None else tag_chr[0])
        qtext = _qsf_strip_html(_dollar(p, "QuestionText"))
        qt = _dollar(p, "QuestionType")
        sel = _dollar(p, "Selector")
        qtype = _as_character(qt) if qt is not None else [""]
        selector = _as_character(sel) if sel is not None else [""]
        choices = _dollar(p, "Choices")
        answers = _dollar(p, "Answers")
        ctags = _dollar(p, "ChoiceDataExportTags")

        def export_col(code: str, _ctags: Any = ctags, _tag: str = tag_s) -> str:
            ct = None
            if isinstance(_ctags, list):
                v = _dbl_bracket(_ctags, code)
                if v is not None:
                    vchr = _as_character(v)
                    if _if(_scalar([trimws("NA" if c is None else c) != "" for c in vchr])):
                        ct = trimws("NA" if vchr[0] is None else vchr[0])
            return _qsf_export_col(_tag, ct, code)

        is_matrix = grepl("matrix", qtype, ignore_case=True)
        is_multi = grepl("MAVR|MACOL|MSB", selector, ignore_case=True)
        has_choices = choices is not None and _r_length(choices) > 0
        choice_names = choices.names() if isinstance(choices, _JsonObject) else []
        if _and(is_matrix, has_choices):
            vl = _qsf_value_labels(answers)
            for code in choice_names:
                stmt = _qsf_option_display(_dbl_bracket(choices, code))
                add(export_col(code), stmt, tag_s, vl, question=qtext)
        elif _and(is_multi, has_choices):
            for code in choice_names:
                opt = _qsf_option_display(_dbl_bracket(choices, code))
                add(export_col(code), opt, tag_s, question=qtext)
        elif has_choices:
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
