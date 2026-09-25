"""Reference Accuracy (port of ``inst/modules/ref_accuracy.R``)."""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Any

import numpy as np
import pandas as pd

from pytacheck.module import module

__all__ = ["ref_accuracy"]

_KEYS = ["paper_id", "bib_id"]
_COLS = ["paper_id", "bib_id", "doi", "title", "year", "authors", "container"]

# fold accented Latin letters to plain ASCII (R's `accent_map`, then chartr())
_ACCENT_MAP = {
    "a": "àáâãäåāăą",
    "c": "çćčĉċ",
    "d": "ďð",
    "e": "èéêëēėęě",
    "g": "ğĝġģ",
    "i": "ìíîïįĩ",
    "l": "ĺļľ",
    "n": "ñńňņ",
    "o": "òóôõöōőŏ",
    "r": "ŕřŗ",
    "s": "śšşŝș",
    "t": "ťţ",
    "u": "ùúûüūůűŭ",
    "y": "ýÿŷ",
    "z": "źżž",
}
# multi-character cases first (R's gsub() calls, in order)
_MULTI = (("ß", "ss"), ("æ", "ae"), ("œ", "oe"), ("đ", "d"), ("ł", "l"), ("ø", "o"), ("ı", "i"))
_CHARTR = str.maketrans(
    "".join(_ACCENT_MAP.values()),
    "".join(base * len(chars) for base, chars in _ACCENT_MAP.items()),
)
# R's tolower() maps one character to one character: no dotted-i expansion
# ("İ" -> "i") and no final-sigma rule ("Σ" -> "σ" everywhere)
_LOWER_FIX = str.maketrans({"İ": "i", "Σ": "σ"})

# C isspace(): what R's as.numeric() skips around a number
_ISSPACE = " \t\n\v\f\r"

_STOPWORDS = frozenset(["of", "and", "the", "for", "in", "on", "a", "an", "de", ""])

_NO_REFS = "We found no references"
_NO_MATCH = "We found no bib_match entries. You may need to add them with `add_bib_match()`."

_GUIDANCE = (
    "The references below supplied a DOI, but one or more of the cited details (title, "
    "authors, journal, or year) does not match the record that DOI points to. Such an "
    "incoherent is most often an error in reading the reference from the PDF, but it could be "
    "a mistake, ar an AI generated reference. Check each against the original source. "
    "Incoherent references are mostly PDF parsing errors (we are working on improving "
    "reference parsing)."
)
_INC_HEAD = "### Incoherent references\n"
_INC_TEXT = "The cited details of these references do not match the record their DOI points to.\n"
_NODOI_HEAD = "### References without a DOI\n"
_NODOI_TEXT = (
    "These references have no DOI, so they could not be checked here, and they also cannot be "
    "checked by the other reference modules (retraction, PubPeer, and replication checks all "
    "rely on the DOI). We searched CrossRef for a matching DOI; where a confident match was "
    "found it is suggested below. We recommend adding a DOI to every reference that has one: "
    "it lets these checks run and makes each cited work easier to find and verify.\n"
)
_ADD_DOI = (
    " Adding a DOI to every reference that has one lets the other reference checks "
    "(retraction, PubPeer, and replication) run on them too."
)
_STRIKE = '<span style="color:#c00;text-decoration:line-through">'


# ---------------------------------------------------------------------------
# small R helpers


def _na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    return isinstance(x, float) and math.isnan(x)


def _chr(values: Any) -> list[str | None]:
    """A character column as a list of ``str`` / ``None`` (NA)."""
    return [None if _na(v) else str(v) for v in values]


def _tolower(x: str | None) -> str | None:
    """R ``tolower()`` (per-character lower case)."""
    return None if x is None else x.translate(_LOWER_FIX).lower()


def _as_numeric(x: Any) -> float | None:
    """R ``as.numeric()`` of one value (``None`` for NA or NaN).

    Strings follow R's ``String2Real()``: C ``isspace`` padding only, ASCII
    digits only (Python's ``float()`` would also take full-width or Arabic-Indic
    digits and Unicode spaces, R gives NA), and C99 hexadecimal numbers
    including binary exponents (``"0x1.f8p10"`` is 2016).
    """
    if _na(x):
        return None
    if isinstance(x, numbers.Real):
        value = float(x)
        return None if math.isnan(value) else value
    s = str(x).strip(_ISSPACE)
    if not s or not s.isascii() or "_" in s:
        return None
    body = s[1:] if s[0] in "+-" else s
    try:
        if body[:2] in ("0x", "0X") and len(body) > 2:
            value = float.fromhex(body)
            value = -value if s[0] == "-" else value
        else:
            value = float(s)
    except ValueError:
        return None
    return None if math.isnan(value) else value


@cache
def _levenshtein() -> Any:
    try:
        from rapidfuzz.distance import Levenshtein
    except ImportError:  # pragma: no cover - rapidfuzz is an optional speed-up
        return None
    return Levenshtein.distance


def _adist(a: str, b: str) -> int:
    """``utils::adist(a, b)[1, 1]``: the (unweighted) Levenshtein distance."""
    fast = _levenshtein()
    if fast is not None:
        return int(fast(a, b))
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# text normalisation (the R closures deaccent(), clean(), journal_tokens(), ...)


def _deaccent(x: str | None) -> str | None:
    if x is None:
        return None
    for a, b in _MULTI:
        x = x.replace(a, b)
    return x.translate(_CHARTR)


def _clean(values: Sequence[str | None]) -> list[str | None]:
    """The module's ``clean()``: lower case, no tags/diacritics/dashes, uniform quotes."""
    from pytacheck._r.regex import gsub

    uniq = list(dict.fromkeys(v for v in values if v is not None))  # clean each value once
    x = [_tolower(v) for v in uniq]
    x = gsub("</?[a-z]+>", "", x)
    x = [_deaccent(v) for v in x]
    x = gsub(r"\p{Pd}", "", x, perl=True)  # remove dashes
    x = gsub(r"\s+", " ", x)
    x = gsub("[\u2018\u2019\u201a\u201b\u0060]", "'", x)  # single quotes
    x = gsub('["\u201c\u201d\u201e\u201f]', "'", x)  # double quotes become single
    x = gsub(r"\.\s*$", "", x)  # remove . at end
    cleaned = dict(zip(uniq, x, strict=True))
    return [None if v is None else cleaned[v] for v in values]


def _journal_tokens(values: Sequence[str | None]) -> list[list[str]]:
    """``journal_tokens()`` for each value (only called on non-missing values)."""
    from pytacheck._r.base import trimws
    from pytacheck._r.regex import gsub, strsplit

    uniq = list(dict.fromkeys(values))  # journal names repeat: tokenise each once
    x = _clean(uniq)
    x = gsub("&amp;|&", " and ", x)
    x = gsub("[^a-z ]", " ", x)
    x = trimws(gsub(r"\s+", " ", x))
    toks = {
        v: [t for t in parts if t not in _STOPWORDS]
        for v, parts in zip(uniq, strsplit(x, " "), strict=True)
    }
    return [toks[v] for v in values]


def _journal_coherent(ta: list[str], tb: list[str]) -> bool:
    """Do the (possibly abbreviated) tokens of one name match the other's in order?"""
    if not ta or not tb:
        return True  # unknown, don't flag
    if len(ta) > len(tb):
        ta, tb = tb, ta
    j = 0
    for tok in ta:
        hit = False
        while j < len(tb):
            if tb[j].startswith(tok) or tok.startswith(tb[j]):
                hit = True
                j += 1
                break
            j += 1
        if not hit:
            return False
    return True


def _norm_title(cleaned: Sequence[str | None]) -> list[str | None]:
    """``norm_title()`` of values already passed through :func:`_clean`."""
    from pytacheck._r.regex import gsub

    uniq = list(dict.fromkeys(v for v in cleaned if v is not None))  # each value once
    x = gsub("<sup>.*?</sup>", "", uniq)  # footnote superscripts
    x = gsub("</?[a-z]+>", "", x)  # any other tags
    x = gsub("[^a-z0-9]", "", x)  # keep only alphanumerics
    normed = dict(zip(uniq, x, strict=True))
    return [None if v is None else normed[v] for v in cleaned]


def _is_frame_like(a: Any) -> bool:
    """Is a list-column cell what R holds as a data frame (records of an author table)?"""
    if isinstance(a, pd.DataFrame):
        return True
    return isinstance(a, list | tuple) and len(a) > 0 and all(isinstance(r, Mapping) for r in a)


def _families(a: Any) -> list[Any] | None:
    """``a$family`` of a data-frame cell (``None`` when there is no family column)."""
    if isinstance(a, pd.DataFrame):
        return None if "family" not in a.columns else a["family"].tolist()
    if not any("family" in r for r in a):
        return None
    return [r.get("family") for r in a]


def _last_names(a: Any) -> list[str | None]:
    """The author surnames of a retrieved record (R's ``last_names`` element).

    A data frame gives its ``family`` column; a string such as
    ``"Last, F; Other, G"`` is split; anything else (``NULL``, a list, a
    malformed string) is an error in R, caught as ``NA``.
    """
    if _is_frame_like(a):
        fam = _families(a)
        return [] if fam is None else [None if _na(v) else str(v) for v in fam]
    if isinstance(a, list | tuple) and a and all(isinstance(v, str) or _na(v) for v in a):
        a = a[0]  # a character vector: strsplit(a, "; ")[[1]] uses the first
    if isinstance(a, str):
        from pytacheck._r.regex import strsplit

        out: list[str | None] = []
        for name in strsplit(a, "; "):
            parts = strsplit(name, ", ")
            if not parts:
                return [None]  # strsplit("", ", ")[[1]][[1]]: subscript out of bounds
            out.append(parts[0])
        return out
    # NULL, NA, list(), a named list...: an error (or NA) in R, i.e. nothing to check
    return [None]


def _author_string(a: Any, max_authors: int) -> str | None:
    """``author_string()``: the leading family names of a CrossRef author table."""
    if a is None or not _is_frame_like(a):
        return None
    fam = _families(a)
    if fam is None:
        return None
    fam = [str(v) for v in fam if not _na(v)]
    if not fam:
        return None
    return ", ".join(_head(fam, max_authors))


def _head(x: list[Any], n: Any) -> list[Any]:
    """``utils::head(x, n)`` (a negative *n* drops from the end).

    As ``head.default()``: the count is ``min(n, length(x))`` or
    ``max(length(x) + n, 0)`` and ``seq_len()`` truncates it only then, so
    ``head(x, -1.5)`` keeps ``length(x) - 2`` values and ``head(x, Inf)`` all.
    """
    n = float(n)
    k = max(len(x) + n, 0.0) if n < 0 else min(n, float(len(x)))
    return x[: int(k)]


def _key_kind(s: pd.Series) -> str:
    """The vctrs type class of a join key: ``chr``, ``num``, ``lgl`` or ``unspecified``.

    vctrs checks types even for zero-row or all-NA keys (a character column of
    NAs still refuses an integer key), but R's logical ``NA`` joins anything, so
    only untyped missing values (an object column without values, an all-NaN
    numpy float column, an all-NA logical column) are "unspecified".
    """
    dt = s.dtype
    if pd.api.types.is_object_dtype(dt):
        kinds = {
            "lgl"
            if isinstance(v, bool | np.bool_)
            else "num"
            if isinstance(v, numbers.Number)
            else "chr"
            for v in s.dropna().tolist()
        }
        if not kinds:
            return "unspecified"
        return "chr" if "chr" in kinds else "num" if "num" in kinds else "lgl"
    if (
        len(s)
        and not s.notna().any()
        and (pd.api.types.is_bool_dtype(dt) or (isinstance(dt, np.dtype) and dt.kind == "f"))
    ):
        return "unspecified"  # NaN-filled or logical NA; typed string/Int64 keep their type
    if pd.api.types.is_bool_dtype(dt):
        return "lgl"
    return "num" if pd.api.types.is_numeric_dtype(dt) else "chr"


def _align_key(x: pd.DataFrame, y: pd.DataFrame, key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give *key* one dtype on both sides, as a dplyr join does.

    dplyr joins integer, double and logical keys, but refuses a character key
    against a numeric or logical one (``Can't join `x$bib_id` with `y$bib_id`
    due to incompatible types.``), e.g. character ``xref_id``s.
    """
    kinds = {_key_kind(x[key]), _key_kind(y[key])}
    if "chr" in kinds and kinds & {"num", "lgl"}:
        raise TypeError(f"Can't join `x${key}` with `y${key}` due to incompatible types.")
    dx, dy = x[key].dtype, y[key].dtype
    if dx == dy:
        return x, y
    num = pd.api.types.is_numeric_dtype
    if num(dx) and num(dy):
        target = "Float64" if "float" in str(dx).lower() or "float" in str(dy).lower() else "Int64"
    else:
        target = "string"
    return x.assign(**{key: x[key].astype(target)}), y.assign(**{key: y[key].astype(target)})


def _left_join(
    x: pd.DataFrame, y: pd.DataFrame, suffixes: tuple[str, str] = (".x", ".y")
) -> pd.DataFrame:
    """``dplyr::left_join(x, y, by = c("paper_id", "bib_id"))``."""
    for k in _KEYS:
        x, y = _align_key(x, y, k)
    out = x.merge(y, on=_KEYS, how="left", sort=False, suffixes=suffixes)
    return out.reset_index(drop=True)


def _lgl(values: list[bool | None], index: pd.Index) -> pd.Series:
    return pd.Series(values, index=index, dtype="boolean")


def _as_text(x: Any) -> str:
    """``sprintf("%s", x)`` / ``paste0()`` of one value."""
    from pytacheck._r.base import as_character

    if _na(x):
        return "NA"
    if isinstance(x, str):
        return x
    value = as_character(x)
    return "NA" if value is None else str(value)


def _strike(x: Any) -> str:
    return f"{_STRIKE}{_as_text(x)}</span>"


def _correction(label: str, cited: Any, record: Any) -> str:
    if _na(cited) or _as_text(cited) == "":
        cited = "(missing)"
    return f"<b>{label}:</b> {_strike(cited)} &rarr; {_as_text(record)}"


# ---------------------------------------------------------------------------


@module(
    title="Reference Accuracy",
    description=(
        "This module checks whether each reference with a DOI is internally coherent: it "
        "compares the details the paper cites for a reference against the authoritative record "
        "that the reference's DOI points to, and flags any reference whose cited details do "
        "not match."
    ),
    details="""
        This module looks for *incoherent* references: a reference is incoherent when the title, authors, journal, or year that the paper cites disagree with the record that the reference's own DOI points to. Currently, it will most often be a parsing error where the reference list from the PDF is not extracted perfectly accurately, but the module can point out AI generated references, of mistakes in citations. You will need check the original source.

        The module uses the `bib_match` table, which holds the metadata retrieved from CrossRef for each reference (added or refreshed with `add_bib_match()`, which makes live network calls).

        **Only references that supply their own DOI are checked.** For these, we look up the record that exact DOI points to and compare it field by field against what the paper cites. A reference with no DOI is *not* checked: the only record we could find for it comes from a CrossRef title search, which often returns the wrong paper, so any "mismatch" would be CrossRef's error rather than the citation's. References without a DOI are instead listed separately, with a recommendation to add a DOI where one exists. For those, a suggested DOI is offered only when CrossRef found a high-confidence match (controlled by `suggest_score`).

        For each checked reference we compare five fields against the DOI's record:

        - **DOI** — flagged only when the reference printed a DOI that differs from the record (a blank cited DOI is never a mismatch).
        - **Year** — flagged when the cited year is more than `year_tolerance` years from the record (online-first and print years routinely differ by one year, so this is allowed by default).
        - **Journal** — flagged when the cited journal disagrees with the record. Journal names are matched tolerantly: standard ISO-4 abbreviations (e.g. "J. Pers. Soc. Psychol." for "Journal of Personality and Social Psychology"), "&"/"and", and "the" are treated as equivalent.
        - **Title** — flagged when the cited title is too different from the record's title, judged by character-level similarity (`title_similarity`) after stripping case, punctuation, footnote markers and diacritics. This ignores cosmetic differences (colons, question marks, em-dash spacing, line-break word splits) while still catching a genuinely different title.
        - **Authors** — flagged when the leading author surnames (the first `max_authors`) from the record are not all present in the cited author list. Only the leading authors are required because author lists are routinely truncated with "et al.". Diacritics are ignored, so "Gredebäck" and "Gredeback" match.

        DOI, journal and year are reliable signals and flag a reference on their own. Title and author mismatches are more often caused by imperfect PDF parsing, so these are governed by `min_mismatches`: the default (1) flags a reference when either the title or the authors disagree. Set `min_mismatches = 2` to be more conservative and require both to disagree (fewer false positives, lower recall).

        The report lists each incoherent reference with the specific cited value struck through and the record's value beside it, so the discrepancy is visible at a glance.

        <validation>In validation on psychology references with a DOI, the module flagged genuine, coherent references at a low rate: around 8% of checked references at the default setting (min_mismatches = 1), and about 3% with the more conservative setting (min_mismatches = 2). On deliberately corrupted references it caught journal and year fabrications reliably (about 96-100%) at either setting, and essentially all single-field fabrications (including single title or author changes) at min_mismatches = 1. Importantly, in a hand-review of references flagged across 10 papers from 5 fields, the large majority of flags were imperfect extraction of the reference from the PDF (for example a journal name mis-parsed, or two references merged) rather than genuine citation errors. Real citation mistakes are rare, so even though the false positive rate is low, a flag should be read as "check this reference against the original source", not as a confirmed error.</validation>
    """,
    keywords=["reference"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={
        "paper": "a paper object or paperlist object",
        "max_authors": (
            "how many of the leading authors to compare against the\n"
            'retrieved record. Author lists are routinely truncated with "et al." (APA\n'
            "abbreviates after 7), and GROBID often drops the tail of long lists, so we\n"
            "only require the first `max_authors` surnames to match."
        ),
        "title_similarity": (
            "the minimum character-level similarity (0-1, after\n"
            "stripping case, punctuation, footnote markers and diacritics) for a cited\n"
            "title to be considered a match for the retrieved title. Lower values\n"
            "tolerate more formatting noise; a title below this is flagged."
        ),
        "min_mismatches": (
            "how many of the parsing-sensitive fields (title and\n"
            "author) must disagree with the retrieved record before a reference is\n"
            "flagged as incoherent. The default (1) flags a single title or author\n"
            "mismatch, which catches more genuine errors; the extra false positives this\n"
            "adds are minor compared with those that already come from imperfect PDF\n"
            "parsing. Set to 2 to be more conservative and require two of these fields\n"
            "to disagree. A mismatched DOI, journal or year always flags on its own,\n"
            "regardless of this setting."
        ),
        "year_tolerance": (
            "how many years the cited year may differ from the\n"
            "retrieved record before the year is flagged. The default (1) allows a\n"
            "one-year difference, because the online-first and print publication years\n"
            "of an article routinely differ by a year. Set to 0 to require an exact\n"
            "match, or higher to be more lenient."
        ),
        "suggest_score": (
            "for references with no DOI, the minimum CrossRef\n"
            "relevance score for a DOI found by title search to be offered as a\n"
            "suggested DOI. Low-scoring matches are usually the wrong paper, so they are\n"
            "not suggested. Raise to be more conservative, lower to suggest more.\n"
            "Papers read from bibr 12.x exports score their matches 0-1, so for them\n"
            "the score is compared with `suggest_score / 100`."
        ),
    },
    returns="report list",
)
def ref_accuracy(
    paper: Any,
    max_authors: int = 6,
    title_similarity: float = 0.7,
    min_mismatches: int = 1,
    year_tolerance: float = 1,
    suggest_score: float = 70,
) -> dict[str, Any]:
    """Port of ``inst/modules/ref_accuracy.R::ref_accuracy()``."""
    from pytacheck.papers.tables import paper_table, ref_table

    # table ----
    bib = paper_table(paper, "bib")
    bib_match = paper_table(paper, "bib_match")

    # If there are no rows, return immediately
    if len(bib) == 0:
        return {"traffic_light": "na", "summary_text": _NO_REFS}
    if len(bib_match) == 0:
        return {"traffic_light": "error", "summary_text": _NO_MATCH}

    refs = ref_table(paper).drop(columns="doi")
    # left join so every reference is kept, including those with no CrossRef record
    table = _left_join(bib.loc[:, _COLS], bib_match.loc[:, _COLS], (".orig", ".match"))
    table = _left_join(table, refs)
    # carry the CrossRef relevance score (to decide whether to suggest a DOI)
    table = _left_join(table, bib_match.loc[:, ["paper_id", "bib_id", "score"]])
    # an unmatched list-column cell is NULL in R, not NaN
    auth_cells = np.empty(len(table), dtype=object)
    for i, a in enumerate(table["authors.match"].tolist()):
        auth_cells[i] = None if _na_cell(a) else a
    table["authors.match"] = pd.Series(auth_cells, index=table.index, dtype=object)
    idx = table.index

    doi_o = _chr(table["doi.orig"])
    doi_m = _chr(table["doi.match"])
    title_o = _chr(table["title.orig"])
    title_m = _chr(table["title.match"])
    cont_o = _chr(table["container.orig"])
    cont_m = _chr(table["container.match"])
    auth_o = _chr(table["authors.orig"])
    auth_m = table["authors.match"].tolist()
    texts = _chr(table["text"])

    # DOI: only flag when the reference itself printed a DOI that differs
    doi_mismatch: list[bool | None] = []
    for o, m in zip(doi_o, doi_m, strict=True):
        if o is None or o == "":
            doi_mismatch.append(False)
        elif m is None:
            doi_mismatch.append(None)  # TRUE & TRUE & NA
        else:
            doi_mismatch.append(_tolower(o) != _tolower(m))
    table["doi_mismatch"] = _lgl(doi_mismatch, idx)

    # year: only flag when more than `year_tolerance` years apart
    year_mismatch: list[bool] = []
    for yo, ym in zip(table["year.orig"].tolist(), table["year.match"].tolist(), strict=True):
        a, b = _as_numeric(yo), _as_numeric(ym)
        diff = None if a is None or b is None else abs(a - b)
        year_mismatch.append(diff is not None and not math.isnan(diff) and diff > year_tolerance)
    table["year_mismatch"] = _lgl(year_mismatch, idx)

    # journal/container: tolerant of ISO-4 abbreviations, "&"/"and" and "the"
    from pytacheck._r.base import trimws

    check = [
        not (a is None or b is None or ta == "" or tb == "")
        for a, b, ta, tb in zip(cont_o, cont_m, trimws(cont_o), trimws(cont_m), strict=True)
    ]
    rows = [i for i, c in enumerate(check) if c]
    toks = _journal_tokens([cont_o[i] for i in rows] + [cont_m[i] for i in rows])
    container_mismatch = [False] * len(table)
    for k, i in enumerate(rows):
        container_mismatch[i] = not _journal_coherent(toks[k], toks[len(rows) + k])
    table["container_mismatch"] = _lgl(container_mismatch, idx)  # type: ignore[arg-type]

    # title: character similarity, or the record title verbatim in the reference text
    match_clean = _clean(title_m)
    a_t, b_t = _norm_title(_clean(title_o)), _norm_title(match_clean)
    clean_text = _clean(texts)
    title_mismatch: list[bool] = []
    for x, y, pattern, txt in zip(a_t, b_t, match_clean, clean_text, strict=True):
        if x is None or y is None or x == "" or y == "":
            title_mismatch.append(False)  # charsim is NA
            continue
        charsim = 1 - _adist(x, y) / max(len(x), len(y))
        in_text = pattern is not None and pattern != "" and txt is not None and pattern in txt
        title_mismatch.append(charsim < title_similarity and not in_text)
    table["title_mismatch"] = _lgl(title_mismatch, idx)  # type: ignore[arg-type]

    # authors: are the leading surnames of the record in the cited author list?
    last_names = [_last_names(a) for a in auth_m]
    wanted: list[list[str]] = []
    for names in last_names:
        present = [v for v in names if v is not None]
        wanted.append(_head(present, max_authors) if present else [])
    flat = _clean([n for names in wanted for n in names])
    clean_orig = _clean(auth_o)
    author_mismatch: list[bool] = []
    pos = 0
    for names, o in zip(wanted, clean_orig, strict=True):
        # grepl(clean(x), clean(o), fixed = TRUE) is FALSE for a missing `o`
        found = [o is not None and c is not None and c in o for c in flat[pos : pos + len(names)]]
        pos += len(names)
        author_mismatch.append(bool(names) and not all(found))
    table["author_mismatch"] = _lgl(author_mismatch, idx)  # type: ignore[arg-type]

    # tier: how did we get the record we are checking against?
    has_own_doi = [o is not None and o != "" for o in doi_o]
    has_record = [m is not None and m != "" for m in title_m]
    tier = [
        "provided" if own and rec else "unresolved" if own else "crossref" if rec else "none"
        for own, rec in zip(has_own_doi, has_record, strict=True)
    ]
    table["tier"] = pd.Series(tier, index=idx, dtype="string")
    table["no_match"] = _lgl([not r for r in has_record], idx)  # type: ignore[arg-type]

    # incoherence: cited details disagree with the record the reference's OWN DOI points to
    incoherent: list[bool] = []
    for i, t in enumerate(tier):
        strong = doi_mismatch[i] is True or year_mismatch[i] or container_mismatch[i]
        n_parsing = int(title_mismatch[i]) + int(author_mismatch[i])
        incoherent.append(
            t == "unresolved" or (t == "provided" and (strong or n_parsing >= min_mismatches))
        )
    table["incoherent"] = _lgl(incoherent, idx)  # type: ignore[arg-type]

    # traffic_light ----
    tl = "yellow" if any(incoherent) else "green"

    # summary_table ----
    no_doi = [t == "none" for t in tier]
    table["no_doi"] = _lgl(no_doi, idx)  # type: ignore[arg-type]
    checked = [t in ("provided", "unresolved") for t in tier]
    counts = pd.DataFrame(
        {"refs_checked": checked, "incoherent": incoherent, "no_doi": no_doi}, index=idx
    ).astype(int)
    summary_table = (
        counts.groupby(table["paper_id"], sort=False, dropna=False)
        .sum()
        .rename_axis("paper_id")
        .reset_index()
    )
    summary_table["paper_id"] = summary_table["paper_id"].astype("string")

    # summary_text
    from pytacheck._r.base import plural

    n_checked, n_inc, n_nodoi = sum(checked), sum(incoherent), sum(no_doi)
    summary_text = (
        f"We checked the {n_checked:d} reference{plural(n_checked)} that supplied a DOI "
        f"against the record that DOI points to, and found {n_inc:d} incoherent "
        f"reference{plural(n_inc)} to check for parsing errors or mistakes. {n_nodoi:d} "
        f"reference{plural(n_nodoi)} had no DOI and could not be checked. Incoherent references "
        "are mostly PDF parsing errors (we are working on improving reference parsing)."
        f"{_ADD_DOI if n_nodoi > 0 else ''}"
    )

    guidance = _GUIDANCE if n_inc else ""
    report: list[Any] = [guidance]
    inc_report = _incoherent_report(table, max_authors)
    if inc_report is not None:
        report += [_INC_HEAD, _INC_TEXT, inc_report]
    nodoi_report = _nodoi_report(table, suggest_score, paper)
    if nodoi_report is not None:
        report += [_NODOI_HEAD, _NODOI_TEXT, nodoi_report]

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report[0] if len(report) == 1 else report,
        "summary_text": summary_text,
    }


def _na_cell(a: Any) -> bool:
    """A missing list-column cell (``NULL`` in R), not a data frame or vector."""
    return a is None or a is pd.NA or (isinstance(a, float) and math.isnan(a))


def _discrepancies(r: dict[str, Any], max_authors: int) -> str:
    """The "what is wrong" cell of one incoherent reference."""
    if r["tier"] == "unresolved":
        return f"<b>DOI:</b> the cited DOI {_strike(r['doi.orig'])} could not be found in CrossRef"
    parts = []
    if r["title_mismatch"] is True:
        parts.append(_correction("title", r["title.orig"], r["title.match"]))
    if r["author_mismatch"] is True:
        parts.append(
            _correction(
                "authors", r["authors.orig"], _author_string(r["authors.match"], max_authors)
            )
        )
    if r["container_mismatch"] is True:
        parts.append(_correction("journal", r["container.orig"], r["container.match"]))
    if r["year_mismatch"] is True:
        parts.append(_correction("year", r["year.orig"], r["year.match"]))
    if r["doi_mismatch"] is True:
        parts.append(_correction("DOI", r["doi.orig"], r["doi.match"]))
    return "<br>".join(parts)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """The rows of *df* as dicts (``df[i, ]`` in R)."""
    cols = list(df.columns)
    return [
        dict(zip(cols, values, strict=True))
        for values in zip(*(df[c].tolist() for c in cols), strict=True)
    ]


def _incoherent_report(table: pd.DataFrame, max_authors: int) -> Any:
    from pytacheck.report import link, scroll_table

    mask = table["incoherent"].fillna(False).to_numpy(dtype=bool)
    if not mask.any():
        return None
    inc = table.loc[mask]
    rows = _records(inc)
    # link to the retrieved record; for an unresolved DOI show the cited DOI instead
    rec_doi = [
        r["doi.orig"] if _na(r["doi.match"]) or r["doi.match"] == "" else r["doi.match"]
        for r in rows
    ]
    urls = ["https://doi.org/" + _as_text(d) for d in rec_doi]
    texts = [None if _na(d) else d for d in rec_doi]
    out = pd.DataFrame(
        {
            "Reference": pd.Series(_chr(inc["text"]), dtype="string"),
            "What is wrong (cited → record)": pd.Series(
                [_discrepancies(r, max_authors) for r in rows], dtype="string"
            ),
            "Record": pd.Series(link(urls, texts), dtype="string"),
        }
    )
    return scroll_table(out, maxrows=5, colwidths=[0.45, 0.4, 0.15])


def _nodoi_report(table: pd.DataFrame, suggest_score: float, paper: Any) -> Any:
    from pytacheck.io.bibr12 import _bibr12_paper_ids
    from pytacheck.report import link, scroll_table

    mask = table["no_doi"].fillna(False).to_numpy(dtype=bool) & table["text"].notna().to_numpy()
    if not mask.any():
        return None
    rows = table.loc[mask]
    scores = rows["score"].tolist()
    dois = _chr(rows["doi.match"])
    # only offer a suggested DOI when the title-search match scored high enough;
    # bibr 12.x papers score their matches 0-1
    ids12 = set(_bibr12_paper_ids(paper))
    min_scores = [
        suggest_score / 100 if pid in ids12 else suggest_score for pid in rows["paper_id"].tolist()
    ]
    suggested = []
    for s, d, min_score in zip(scores, dois, min_scores, strict=True):
        if not _na(s) and float(s) >= min_score and d is not None and d != "":
            suggested.append(link("https://doi.org/" + d, d))
        else:
            suggested.append("")
    out = pd.DataFrame(
        {
            "Reference": pd.Series(_chr(rows["text"]), dtype="string"),
            "Suggested DOI": pd.Series(suggested, dtype="string"),
        }
    )
    return scroll_table(out, maxrows=5, colwidths=[0.7, 0.3])
