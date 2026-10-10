"""Text extractors: URLs, p-values, equations and live-data sentences.

Port of ``R/text-extractors.R`` (``extract_urls()``, ``extract_p_values()``,
``extract_eq()`` and ``.detect_live_data()``). These tables feed many modules
(``all_p_values``, ``stat_p_exact``, ``stat_p_nonsig``, ``stat_effect_size``,
``all_urls``, ``ethics_check``...), so rows, row order, columns and dtypes
follow metacheck exactly.
"""

from __future__ import annotations

import re
from typing import Any, cast

import pandas as pd

from metacheck._r.base import trimws
from metacheck._r.regex import compile_r, grepl, gsub, regextract, regextract_all, strsplit
from metacheck._values import is_missing
from metacheck.core.doc import Doc, bits
from metacheck.core.patterns import Pat, patterns
from metacheck.text.search import _check_pattern, _Search, search_doc, text_search

__all__ = ["extract_eq", "extract_p_values", "extract_urls"]

# Comparison operators recognised by extract_p_values() / extract_eq():
# = < > ~ ≈ ≠ ≤ ≥ ≪ ≫
_OPERATORS = ("=", "<", ">", "~", "≈", "≠", "≤", "≥", "≪", "≫")
_OPS = "".join(_OPERATORS)

# An e-mail address is matched whole, so that neither its local part ("k.aristovich") nor
# its domain ("gmail.com") is listed as a host name; extract_urls() drops these matches (U206)
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_EMAIL_RX = re.compile(_EMAIL)

# "et al." glued to the next word ("et al.Premotor") is not a host name (U158)
_URL_PATTERN = (
    r"\b(?<!\bet )"
    f"(?:{_EMAIL}|"
    r"((doi:)?(https?://)?(([\w.-]+\.[a-z]{2,})|(\d{1,3}(\.\d{1,3}){3}))(:\d+)?(/[^\s]*)?))\b"
)

# Differs from metacheck (U204, U205): "ps", "p's" and "p-values" are ways to
# write p, a minus sign may be the Unicode one (U+2212, as text from PDFs has
# it), and "x 10^-5" may be written with "×" and without "^" ("1.8 × 10 -6",
# "6.1 × 10−5", which metacheck read as p = 1.8 and p = 6.1)
_MINUS_SIGN = "\u2212"  # U+2212, which text from PDFs often has for "-"
_MINUS = f"-{_MINUS_SIGN}"
_SCI_E = f"(e\\s*[{_MINUS}]\\d+)?"
_SCI_10 = f"(\\s*[x\\*×]\\s*10\\s*\\^?\\s*[{_MINUS}]\\d+)?"
_P_PATTERN = (
    r"\b[pP]-?(values?|s|['’]s)?\s*"  # ways to write p (or P)
    f"[{_OPS}]{{1,2}}\\s*"  # 1-2 operators
    r"(n\.?s\.?|\d?\.\d+)"  # ns or valid numbers
    f"\\s*{_SCI_E}"  # also match scientific notation
    f"{_SCI_10}"
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
    # numbers (a Unicode minus may have a space after it: "r = − 0.12"),
    # anything in [], or NS
    f"((?:{_MINUS_SIGN}\\s)?[0-9\\.,+\\-{_MINUS_SIGN}]*[0-9]|\\[[^\\]]+\\]|n\\.?\\s*s\\.?)"
    f"\\s*{_SCI_E}"  # also match scientific notation
    f"{_SCI_10}"
)

_OP_RUN = f"[{_OPS}]{{1,2}}"
_OP_SPLIT = f"\\s*[{_OPS}]{{1,2}}\\s*"

# the searches of extract_urls() (every URL contains ".xx", a domain, or "d.d", an
# IPv4 address) and of extract_eq() (sentences with any operator, then each operator)
_URL = Pat.from_r(_URL_PATTERN, perl=True)
_URL_ANY = Pat.from_r(r"\.[a-z]{2}|\d\.\d", perl=True)
_OPS_ANY = Pat(f"[{_OPS}]")
_OPERATOR_PATS = patterns(_OPERATORS)

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

    Returns a :func:`~metacheck.text.text_search` ``return="match"`` table with
    one row per URL-like string (URLs starting with ``http``, ``doi:``, bare
    domains or IPv4 addresses) found in *paper* (a paper, paper list or
    text table).
    """
    _check_pattern(_URL.src, _URL.icase, _URL.perl, _URL.fixed)
    doc, is_vector = search_doc(paper)
    s = _Search(doc, is_vector, False)
    if not is_vector and "text" not in doc.missing:
        # every URL match contains a match of _URL_ANY, so only those rows are searched
        s.within = doc.mask(_URL_ANY, 0, s.within)
    urls = s.finish(s.search(_URL, "match", False, False))
    if not isinstance(urls, pd.DataFrame) or "text" not in urls.columns:
        return cast(pd.DataFrame, urls)  # character input: the search result as it is
    is_email = urls["text"].map(lambda t: isinstance(t, str) and _EMAIL_RX.fullmatch(t) is not None)
    return urls.loc[~is_email.to_numpy(dtype=bool)].reset_index(drop=True)


def extract_p_values(paper: Any) -> pd.DataFrame:
    """List all p-values in the text (port of ``extract_p_values()``).

    Returns the matched text (e.g. ``"p = 0.04"``) with its document location,
    plus ``p_comp`` (the comparator) and ``p_value`` (the number; ``NaN`` for
    ``"n.s."``). Text such as "the p-value is 0.03" is deliberately not
    matched; scientific notation (``5.0 x 10^-2``, ``5.0e-2``) and the
    comparators ``= < > ~ ≈ ≠ ≤ ≥ ≪ ≫`` are. Strings are searched as a text
    table (metacheck fails on a character vector; U150).
    """
    from metacheck._values import as_float

    p = text_search(
        _strings_table(paper), _P_PATTERN, return_="match", perl=True, ignore_case=False
    )
    if not isinstance(p, pd.DataFrame):  # pragma: no cover - strings became a table
        raise TypeError("$ operator is invalid for atomic vectors")
    texts = [None if pd.isna(t) else str(t) for t in p["text"].tolist()]
    comps = regextract(_OP_RUN, texts, perl=True)
    split = strsplit(texts, _OP_SPLIT)
    values: list[str | None] = []
    for parts in split:
        if len(parts) < 2:
            raise IndexError("subscript out of bounds")
        values.append(parts[1])
    values = gsub(r"\s", "", values)
    # a Unicode minus is a minus, and "×" and a missing "^" are allowed (U204, U205)
    values = gsub(_MINUS_SIGN, "-", values, fixed=True)
    values = gsub(r"[x*×]10\^?", "e", values)
    p = p.copy()
    p["p_comp"] = pd.Series(comps, index=p.index, dtype="string")
    p["p_value"] = pd.Series([as_float(v) for v in values], index=p.index, dtype="float64")
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


def _sort_key(x: Any) -> tuple[int, Any]:
    """``dplyr::arrange()`` key: C-locale strings / numbers, ``NA`` last."""
    if is_missing(x):
        return (1, 0)
    return (0, x)


def _strings_table(paper: Any) -> Any:
    """A string or a sequence of strings as a text table (other input unchanged)."""
    if isinstance(paper, str):
        return pd.DataFrame({"text": pd.Series([paper], dtype="string")})
    if isinstance(paper, list | tuple) and all(isinstance(x, str) for x in paper):
        return pd.DataFrame({"text": pd.Series(list(paper), dtype="string")})
    return paper


_ROW = ".pytacheck_row."


def extract_eq(paper: Any) -> pd.DataFrame:
    """List all equations in the text (port of ``extract_eq()``).

    This is metacheck's canonical extractor for reported statistics: one row
    per ``name (df) <op> value`` fragment, with the columns ``text_id``,
    ``grp_id`` (the sentence group, numbered per paper in search order), ``lhs``, ``df``
    (e.g. ``"(2, 57)"`` or ``NA``), ``comp``, ``rhs`` and ``paper_id``,
    sorted by ``paper_id``, ``text_id`` and ``grp_id``. *paper* can also be a
    text table or strings (``text_id`` and ``paper_id`` are then ``NA`` when
    the table has none).

    Differs from metacheck (U10, U150): in a paper list each sentence of a
    paper has its own ``grp_id``, the one it gets when the paper is searched
    alone (metacheck restarted the count whenever its search order moved to
    another paper, so different sentences shared a ``grp_id``); a table without
    ``paper_id`` or ``text_id`` works (metacheck failed on ``NA`` comparisons
    once there were two equations); and strings are accepted (metacheck
    fails).
    """
    table = _strings_table(paper)
    if isinstance(table, pd.DataFrame):
        doc = Doc.from_frame(table.copy(deep=False))
    else:
        doc = search_doc(table)[0]
    if "text" in doc.missing:  # its first column is searched instead, as text_search() does
        return _eq_of_table(doc.base_frame())
    return eq_table(doc)


def eq_table(doc: Doc) -> pd.DataFrame:
    """:func:`extract_eq` of the sentences of *doc* (a paper's, or a table's).

    metacheck searches the sentences that have an operator (``text_search()``
    with the list of operators: pattern by pattern, references skipped), then
    the statistics in the text that search returned (cleaned once). The source
    sentence of each statistic is its row, so no sentence is dropped as a
    duplicate. A sentence without a statistic gives no row, so the statistics
    are extracted without first detecting them (``text_search()`` did both).
    """
    rank: dict[int, int] = {}
    found = doc.any(_OPERATOR_PATS, 0, doc.mask(_OPS_ANY, 0, doc.body), False, rank)
    rows = sorted(bits(found), key=lambda i: (rank[i], i))
    hits = regextract_all(_EQ_PATTERN, doc.stage_text(rows, 1), True, True)
    source = [i for i, h in zip(rows, hits, strict=True) for _ in h]
    if not source:
        return _empty_eq()
    pids = doc.values("paper_id")
    return _eq_frame(
        [h for each in hits for h in each],
        doc.take_series("text_id", source),
        doc.take_series("paper_id", source),
        [None if is_missing(pids[i]) else pids[i] for i in source],
        source,
    )


def _eq_of_table(table: pd.DataFrame) -> pd.DataFrame:
    """:func:`extract_eq` of a table without a ``text`` column, searched as metacheck does."""
    # the source row of each match, to tell sentences apart without text_id
    table = table.assign(**{_ROW: range(len(table))})
    eq = text_search(table, list(_OPERATORS))
    eq = text_search(eq, _EQ_PATTERN, return_="match", perl=True, ignore_case=True)
    if not isinstance(eq, pd.DataFrame):  # pragma: no cover - strings became a table
        raise TypeError("argument is of length zero")
    if len(eq) == 0:
        return _empty_eq()
    return _eq_frame(
        [str(t) for t in eq["text"].tolist()],
        eq["text_id"].reset_index(drop=True),
        eq["paper_id"].reset_index(drop=True),
        [None if is_missing(v) else v for v in eq["paper_id"].tolist()],
        eq[_ROW].tolist(),
    )


def _eq_frame(
    texts: list[str],
    text_id: pd.Series,
    paper_id: pd.Series,
    paper_ids: list[Any],
    rows: list[Any],
) -> pd.DataFrame:
    """The eq table of the statistics *texts*, found in rows *rows* of papers *paper_ids*."""
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
    # a Unicode minus becomes "-", without the space a leading one may have
    # ("− 0.12" gives "-0.12"); metacheck matched no such value (U205)
    rhs = gsub(f"^{_MINUS_SIGN}\\s?", "-", rhs, perl=True)
    rhs = gsub(_MINUS_SIGN, "-", rhs, fixed=True)

    # set group equal to sentence for now: the matches of one source sentence
    # share a group, numbered per paper in search order (metacheck's numbering
    # for a single paper). Differs from metacheck (U10): a paper's count goes
    # on where it left off when a paper list's search order moves between
    # papers (metacheck restarted at 1 each time, so different sentences of a
    # paper shared a grp_id), and sentences are told apart by their row, so a
    # table without text_id or paper_id works (metacheck fails on NA
    # comparisons)
    grp: list[float] = []
    count: dict[Any, int] = {}
    last_row: dict[Any, Any] = {}
    for pid, row in zip(paper_ids, rows, strict=True):
        if pid not in last_row or last_row[pid] != row:
            count[pid] = count.get(pid, 0) + 1
            last_row[pid] = row
        grp.append(float(count[pid]))

    out = pd.DataFrame(
        {
            "text_id": text_id,
            "grp_id": pd.Series(grp, dtype="float64"),
            "lhs": pd.Series(lhs, dtype="string"),
            "df": pd.Series(dfs, dtype="string"),
            "comp": pd.Series(comps, dtype="string"),
            "rhs": pd.Series(rhs, dtype="string"),
            "paper_id": paper_id,
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
    return cast(pd.DataFrame, text_search(paper, list(_LIVE_WORDS)))
