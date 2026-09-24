"""Text extractors: URLs, p-values, equations and live-data sentences.

Port of ``R/text-extractors.R`` (``extract_urls()``, ``extract_p_values()``,
``extract_eq()`` and ``.detect_live_data()``). These tables feed many modules
(``all_p_values``, ``stat_p_exact``, ``stat_p_nonsig``, ``stat_effect_size``,
``all_urls``, ``ethics_check``...), so rows, row order, columns and dtypes
follow metacheck exactly.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from pytacheck._r.base import trimws
from pytacheck._r.regex import compile_r, grepl, gsub, regextract, strsplit
from pytacheck.text.search import text_search

__all__ = ["extract_eq", "extract_p_values", "extract_urls"]

# Comparison operators recognised by extract_p_values() / extract_eq():
# = < > ~ ≈ ≠ ≤ ≥ ≪ ≫
_OPERATORS = ("=", "<", ">", "~", "≈", "≠", "≤", "≥", "≪", "≫")
_OPS = "".join(_OPERATORS)

_URL_PATTERN = (
    r"\b((doi:)?(https?://)?(([\w.-]+\.[a-z]{2,})|(\d{1,3}(\.\d{1,3}){3}))(:\d+)?(/[^\s]*)?)\b"
)

_P_PATTERN = (
    r"\b[pP]-?(value)?\s*"  # ways to write p (or P)
    f"[{_OPS}]{{1,2}}\\s*"  # 1-2 operators
    r"(n\.?s\.?|\d?\.\d+)"  # ns or valid numbers
    r"\s*(e\s*-\d+)?"  # also match scientific notation
    r"(\s*[x\*]\s*10\s*\^\s*-\d+)?"
)

# A df-parenthetical: digits/commas/periods/whitespace, optionally with an
# "N = <number>" qualifier (see the long comment in R/text-extractors.R).
_DF_INNER = r"(?:[0-9,\.\s]+|[nN]\s*=\s*[0-9]+)*"
_GREEK = "Ͱ-Ͽ"
_EQ_PATTERN = (
    r"(?:(Hedge.{0,3}|Cronbach.{0,2}|Cohen.{0,2}|\d{1,2}%)\s+)?"  # common prefix
    f"[{_GREEK}²a-zA-Z-_\\.0-9\\{{\\}}\\^\\\\]+\\s*"  # statistic name
    f"(?:\\({_DF_INNER}\\))?\\s*"  # optional df-shaped parentheses
    f"[{_OPS}]{{1,3}}\\s*"  # 1-3 operators
    r"([0-9\.,+-]*[0-9]|\[[^\]]+\]|n\.?\s*s\.?)"  # numbers, anything in [], or NS
    r"\s*(e\s*-\d+)?"  # also match scientific notation
    r"(\s*[x\*]\s*10\s*\^\s*-\d+)?"
)

_OP_RUN = f"[{_OPS}]{{1,2}}"
_OP_SPLIT = f"\\s*[{_OPS}]{{1,2}}\\s*"

_LIVE_WORDS = (
    # participant/subject/volunteer actions
    r"(participants?|subjects?|volunteers?)\s+(were|are|had|gave|provided|completed|filled"
    r"|signed|took\s+part|participated|recruited|paid|compensated|randomly\s+assigned"
    r"|assigned\s+to|enrolled|debriefed)",
    # respondents only with strong recruitment verbs
    r"respondents?\s+(were|are)\s+(recruited|interviewed|enrolled|randomly\s+assigned"
    r"|compensated|paid|debriefed)",
    # researcher actively recruiting
    r"we\s+(recruited|enrolled|debriefed|ran\s+(participants?|subjects?))",
    # researcher collecting data from live humans
    r"we\s+collected\s+data\s+from\s+(participants?|subjects?|volunteers?|humans?|patients?"
    r"|children|students?)",
    r"we\s+conducted\s+(the\s+)?(study|survey|interview|clinical\s+trial)",
    r"were\s+recruited",
    r"were\s+enrolled",
    r"were\s+debriefed",
    r"were\s+randomis(ed|ing)",
    r"were\s+randomiz(ed|ing)",
    # consent
    r"(gave|give|given|signed|provided)\s+(written\s+|oral\s+|verbal\s+)?informed\s+consent",
    r"signed\s+(informed\s+)?consent",
    r"consent\s+was\s+obtained",
    r"informed\s+consent\s+was\s+(obtained|provided|given|collected)",
    # online recruitment platforms
    r"\bmturk\b",
    r"mechanical\s+turk",
    r"prolific\s+academic",
    r"\bprolific\b",
    r"cloudresearch",
    r"turkprime",
    r"sona\s+systems?",
    r"research\s+participation\s+scheme",
    r"(subject|participant)\s+pool",
    # student samples
    r"(undergraduate|graduate|university|college)\s+students?\s+(who|were|participated"
    r"|completed|filled|took\s+part)",
    # inclusion/exclusion criteria paired with participant/patient language
    r"(participant|patient|subject)\s+(inclusion|exclusion)\s+criteri",
    r"(inclusion|exclusion)\s+criteri.{0,60}(participants?|patients?|subjects?)",
    # "our participants" / "our subjects"
    r"our\s+(participants?|subjects?|volunteers?)",
    # animal data collection
    r"(mice|rats?|monkeys?|pigeons?|zebrafish|ferrets?|rabbits?|hamsters?|gerbils?"
    r"|guinea\s+pigs?|macaques?|marmosets?)\s+(were|had)\s+(used|housed|trained|tested"
    r"|implanted|injected|anesthetized|anaesthetized|sacrificed|perfused|killed)",
    r"were\s+(implanted|surgically\s+prepared|injected|anesthetized|anaesthetized"
    r"|sacrificed|perfused)",
)


def extract_urls(paper: Any) -> pd.DataFrame:
    """Extract URLs (port of ``R/text-extractors.R::extract_urls()``).

    Returns a :func:`~pytacheck.text.text_search` ``return="match"`` table with
    one row per URL-like string (URLs starting with ``http``, ``doi:``, bare
    domains or IPv4 addresses) found in *paper* (a paper, paper list or
    text table).
    """
    return text_search(paper, _URL_PATTERN, return_="match", perl=True)


def extract_p_values(paper: Any) -> pd.DataFrame:
    """List all p-values in the text (port of ``extract_p_values()``).

    Returns the matched text (e.g. ``"p = 0.04"``) with its document location,
    plus ``p_comp`` (the comparator) and ``p_value`` (the number; ``NaN`` for
    ``"n.s."``). Text such as "the p-value is 0.03" is deliberately not
    matched; scientific notation (``5.0 x 10^-2``, ``5.0e-2``) and the
    comparators ``= < > ~ ≈ ≠ ≤ ≥ ≪ ≫`` are.
    """
    from pytacheck.text.json_expand import as_numeric

    p = text_search(paper, _P_PATTERN, return_="match", perl=True, ignore_case=False)
    texts = [None if pd.isna(t) else str(t) for t in p["text"].tolist()] if "text" in p else []
    comps = regextract(_OP_RUN, texts, perl=True)
    split = strsplit(texts, _OP_SPLIT)
    values: list[str | None] = []
    for parts in split:
        if len(parts) < 2:
            raise IndexError("subscript out of bounds")
        values.append(parts[1])
    values = gsub(r"\s", "", values)
    values = gsub(r"[x*]10\^", "e", values)
    p = p.copy()
    p["p_comp"] = pd.Series(comps, index=p.index, dtype="string")
    p["p_value"] = pd.Series([as_numeric(v) for v in values], index=p.index, dtype="float64")
    return p


def _empty_eq() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "text_id": pd.Series([], dtype="Int64"),
            "grp_id": pd.Series([], dtype="Int64"),
            "lhs": pd.Series([], dtype="string"),
            "df": pd.Series([], dtype="string"),
            "comp": pd.Series([], dtype="string"),
            "rhs": pd.Series([], dtype="string"),
            "paper_id": pd.Series([], dtype="string"),
        }
    )


def _is_missing(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and math.isnan(x)


def _sort_key(x: Any) -> tuple[int, Any]:
    """``dplyr::arrange()`` key: C-locale strings / numbers, ``NA`` last."""
    if _is_missing(x):
        return (1, 0)
    return (0, x)


def extract_eq(paper: Any) -> pd.DataFrame:
    """List all equations in the text (port of ``extract_eq()``).

    This is metacheck's canonical extractor for reported statistics: one row
    per ``name (df) <op> value`` fragment, with the columns ``text_id``,
    ``grp_id`` (the sentence group, numbered per paper in search order),
    ``lhs``, ``df`` (e.g. ``"(2, 57)"`` or ``NA``), ``comp``, ``rhs`` and
    ``paper_id``, sorted by ``paper_id``, ``text_id`` and ``grp_id``.
    """
    eq = text_search(paper, list(_OPERATORS))
    eq = text_search(eq, _EQ_PATTERN, return_="match", perl=True, ignore_case=True)
    if len(eq) == 0:
        return _empty_eq()

    texts: list[str] = [str(t) for t in eq["text"].tolist()]
    df_rx = compile_r(r"(?:\([^)]*\))", perl=True)
    dfs: list[str | None] = []
    for i, t in enumerate(texts):
        m = df_rx.search(t)
        if m is None:
            dfs.append(None)
            continue
        dfs.append(m.group(0))
        texts[i] = gsub(r"\s+", " ", t.replace(m.group(0), "", 1))

    comps = regextract(_OP_RUN, texts, perl=True)
    if any(c is None for c in comps):
        raise IndexError("subscript out of bounds")
    lhs: list[str] = []
    rhs: list[str] = []
    for parts in strsplit(texts, _OP_SPLIT):
        if len(parts) < 2:
            raise IndexError("subscript out of bounds")
        lhs.append(parts[0])
        rhs.append(parts[1])
    lhs = trimws(lhs)
    rhs = trimws(rhs)

    # set group equal to sentence for now (numbered in search order, per paper)
    paper_ids = eq["paper_id"].tolist()
    text_ids = eq["text_id"].tolist()
    grp: list[float] = []
    for i in range(len(eq)):
        if i == 0:
            grp.append(1.0)
            continue
        if _is_missing(paper_ids[i]) or _is_missing(paper_ids[i - 1]):
            raise ValueError("missing value where TRUE/FALSE needed")
        if paper_ids[i] != paper_ids[i - 1]:
            grp.append(1.0)
            continue
        if _is_missing(text_ids[i]) or _is_missing(text_ids[i - 1]):
            raise ValueError("missing value where TRUE/FALSE needed")
        grp.append(grp[-1] if text_ids[i] == text_ids[i - 1] else grp[-1] + 1)

    out = pd.DataFrame(
        {
            "text_id": eq["text_id"].reset_index(drop=True),
            "grp_id": pd.Series(grp, dtype="float64"),
            "lhs": pd.Series(lhs, dtype="string"),
            "df": pd.Series(dfs, dtype="string"),
            "comp": pd.Series(comps, dtype="string"),
            "rhs": pd.Series(rhs, dtype="string"),
            "paper_id": eq["paper_id"].reset_index(drop=True),
        }
    )
    keep = [not v for v in grepl("^[0-9]$", lhs)]
    out = out.loc[keep]
    pids = out["paper_id"].tolist()
    tids = out["text_id"].tolist()
    gids = out["grp_id"].tolist()
    order = sorted(
        range(len(out)),
        key=lambda i: (_sort_key(pids[i]), _sort_key(tids[i]), _sort_key(gids[i])),
    )
    return out.iloc[order].reset_index(drop=True)


def _detect_live_data(paper: Any) -> pd.DataFrame:
    """Sentences indicating live data collection (port of ``.detect_live_data()``).

    Searches for participant recruitment, informed consent, online
    recruitment platforms, student pools, inclusion/exclusion criteria and
    animal data collection. Used by the ``ethics_check`` module.
    """
    return text_search(paper, list(_LIVE_WORDS))
