"""Shared statistics helpers (port of ``R/stat_helpers.R``).

``R/stat_helpers.R`` also (re)defines ``.stat_num_to_chr()``,
``.stat_sanitize_id()``, ``.norm_stat_name()``, ``.norm_value()``,
``.norm_interval()``, ``.stat_is_placeholder()``, ``.stat_is_label_col()`` and
``.table_header_ambiguous()``. Their bodies are identical to the definitions
in ``R/stat-output.R``, ``R/stat-tables.R``, ``R/match-reported.R``,
``R/match-table.R`` and ``R/extract-tests.R``, and they are ported there
(``metacheck.statout.*``, ``metacheck.text.extract_tests``; see
``porting/symbols.json``).
"""

from __future__ import annotations

import functools
import math
import re
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r.base import as_character, r_round
from metacheck._r.regex import compile_r, grepl, gsub, sub
from metacheck._values import as_float, is_missing
from metacheck.text.extract import _P_SUBSCRIPTS

__all__ = [
    "_p_clause",
    "_p_not_test",
    "_r_stat_pattern",
    "_reports_test",
    "_sci_text",
    "_stat_display_value",
    "_stat_html_escape",
    "_stato_strip_variant",
]

_NUMBER_TEXT = r"^[-+]?[0-9.]+([eE][-+]?[0-9]+)?$"


# ---- a number as a paper writes it (metacheck 0.4.4.3, #642) -------------------------

_SCI_WS = re.compile(r"\s")
_SCI_MINUS = re.compile("[\u2212\u00c0]")
_SCI_TEN_POWER = re.compile(r"^([<>]?)10\^?(?=-\d)")
_SCI_TIMES = re.compile("[x*\u00d7\u00c2\u00c3]10\\^?(?=[-+]?\\d)")


def _sci_text_one(s: str, ten_power: bool) -> str:
    s = _SCI_WS.sub("", s)
    s = _SCI_MINUS.sub("-", s)
    if ten_power:
        s = _SCI_TEN_POWER.sub(r"\g<1>1e", s)
    return _SCI_TIMES.sub("e", s)


def _sci_text(x: Any, ten_power: bool = False) -> Any:
    """Port of ``.sci_text()``: a number as a paper writes it, in the form R reads.

    Spaces are removed, the Unicode minus and "A with a grave accent" (GROBID's reading of
    the minus in some PDFs) become ``"-"``, and "x 10^", the multiplication sign or "A with a
    circumflex or a tilde" (GROBID's readings of it) before 10 and an exponent become
    ``"e"``: ``"1.8 x 10 -6"`` and ``"2.2Â10 À16"`` are ``"1.8e-6"`` and ``"2.2e-16"``.
    With *ten_power*, 10 to a power with nothing before it (``"10 -15"``, where reading the
    PDF lost the superscript) is ``"1e-15"``: only for a p-value, as elsewhere ``"10-15"``
    can be a range. The one reader of such numbers for ``extract_p_values()`` and
    ``_norm_value()``, so the two cannot disagree (#642). Vectorised: a list gives a list,
    ``NA`` stays ``NA``.
    """
    if isinstance(x, list | tuple):
        return [None if is_missing(v) else _sci_text_one(str(v), ten_power) for v in x]
    return None if is_missing(x) else _sci_text_one(str(x), ten_power)


def _format_whole(x: float) -> str:
    """``format(x, scientific = FALSE, trim = TRUE)`` for a whole number."""
    s = f"{x:.0f}"
    return "0" if s == "-0" else s


def _stat_display_value(x: Any) -> str:
    """Port of ``.stat_display_value()``: a value cell as shown in HTML tables.

    Numbers are rounded to 3 decimals for display, except whole numbers (shown
    without decimals) and values that would round to zero (kept as written).
    ``NA``/``None`` gives ``""``; anything that is not plainly a number is
    returned unchanged.
    """
    if is_missing(x):
        return ""
    if isinstance(x, list | tuple):
        if len(x) != 1:
            raise ValueError("the condition has length > 1")
        x = x[0]
        if is_missing(x):
            return ""
    text = x if isinstance(x, str) else as_character(x)
    assert text is not None
    num = as_float(text)
    if num is None or not math.isfinite(num) or not grepl(_NUMBER_TEXT, text):
        return text
    if num == r_round(num):
        return _format_whole(r_round(num))
    rounded = f"{num:.3f}"
    if num != 0 and float(rounded) == 0:
        return text
    return rounded


def _stat_html_escape(x: Any) -> Any:
    """Port of ``.stat_html_escape()``: escape ``&``, ``<`` and ``>`` (vectorised).

    ``None`` (R ``NULL``) gives ``""``; ``NA`` elements stay ``NA``.
    """
    if x is None:
        x = ""
    scalar = not isinstance(x, list | tuple)
    values = [x] if scalar else list(x)
    chars = [
        None if is_missing(v) else (v if isinstance(v, str) else as_character(v)) for v in values
    ]
    out = gsub("&", "&amp;", chars, fixed=True)
    out = gsub("<", "&lt;", out, fixed=True)
    out = gsub(">", "&gt;", out, fixed=True)
    return out[0] if scalar else out


def _stato_strip_variant(key: Any) -> Any:
    """Port of ``.stato_strip_variant()``: drop a trailing ``[...]`` variant suffix.

    jamovi appends sphericity corrections and test variants in brackets
    (``f[gg]``, ``p[hf]``, ``stat[stud]``); the suffix is stripped before
    STATO lookup. Vectorised like R's ``sub()``.
    """
    return sub(r"\[[^]]*\]$", "", key)


# ---- the decimal point of The Lancet, and the chi of a chi-squared test ---------------------

_DECIMAL_POINT = "(?<=\\d)[\u00b7\u2022\u2219](?=\\d)"
_CHI_JOIN = "\u03c7\\s+([\u00b22\u2082])(?=\\s*[([=<>\u2264\u2265])"
_X_JOIN = "(?<![\\w\u0370-\u03ff])X\\s+([\u00b22])(?=\\s*[([=<>\u2264\u2265])"
_DECIMAL_ANY = re.compile("[\u00b7\u2022\u2219]|\u03c7|X")


def _stat_decimal_point_text(x: Any) -> Any:
    """``gsub("(?<=\\d)[·•∙](?=\\d)", ".", x)`` of strings (``None`` stays)."""
    return [
        s if s is None or not isinstance(s, str) else gsub(_DECIMAL_POINT, ".", s, perl=True)
        for s in x
    ]


def _stat_chi_join_text(x: Any) -> Any:
    """The chi of a chi-squared test joined to its 2 in strings (``.stat_chi_join()``)."""
    out = []
    for s in x:
        if isinstance(s, str):
            s = gsub(_CHI_JOIN, "\u03c7\\1", s, perl=True)
            s = gsub(_X_JOIN, "X\\1", s, perl=True)
        out.append(s)
    return out


def _stat_text_apply(paper: Any, fix: Any, quick: Any = None) -> Any:
    """Apply *fix* to the text of a paper, a paper list or a text table (``.stat_text_apply()``).

    *fix* maps a list of strings to a list of strings. A paper or a paper list is returned as
    the table it is searched as, with the text changed, when the change touches any text, and
    as it is otherwise. *quick* is a pattern that every text *fix* changes contains, so that
    only those texts go through *fix*.
    """
    from metacheck.text.search import sentence_table

    if isinstance(paper, str) or paper is None:
        return paper
    if isinstance(paper, pd.DataFrame):
        if "text" not in paper.columns:
            return paper
        table = paper
    else:
        try:
            table, is_strings = sentence_table(paper)
        except Exception:  # not a paper: left to the search to complain
            return paper
        if is_strings:
            return paper
    if "text" not in table.columns:
        return paper
    texts = table["text"]
    if quick is not None:
        hit = texts.astype("string").str.contains(quick, regex=True, na=False).to_numpy(bool)
    else:
        hit = ~texts.isna().to_numpy(bool)
    if not hit.any():
        return paper
    idx = np.flatnonzero(hit)
    values = list(texts.to_numpy(dtype=object))
    fixed = fix([values[i] for i in idx])
    if all(a == b for a, b in zip(fixed, (values[i] for i in idx), strict=True)):
        return paper
    for i, v in zip(idx, fixed, strict=True):
        values[i] = v
    out = table.copy()
    out["text"] = pd.Series(values, index=out.index, dtype=texts.dtype)
    return out


def _stat_decimal_point(paper: Any) -> Any:
    """A paper's text with a raised dot between two digits read as a decimal point.

    Port of ``.stat_decimal_point()``: The Lancet and medRxiv papers in its style write "p =
    0·0079", which GROBID gives with the middle dot or the bullet, so the p-values and tests of
    such papers were not read at all (#648).
    """
    return _stat_text_apply(paper, _stat_decimal_point_text, "[\u00b7\u2022\u2219]")


def _stat_chi_join(paper: Any) -> Any:
    """A paper's text with the chi of a chi-squared test joined to its 2 (``.stat_chi_join()``).

    Reading a PDF often puts a space between them ("χ 2 (10) = 9.61", "X 2 (1) = 3.20"), and
    the statistic was then not read (#351). Only where a test follows.
    """
    return _stat_text_apply(paper, _stat_chi_join_text, "\u03c7\\s|X\\s")


# ---- p-values below 0 or above 1 (#351) ----------------------------------------------------
#
# Rules from all 237,000 "p <comparison> number" in 18 corpora (16,847 papers), each case read:
# 50 were negative and 180 were read above 1.

# The power of ten of a p-value in scientific notation, lost or garbled when the PDF was read,
# right after the number: a multiplication sign ("2.4 x", GROBID's "9 10 À5" in some journals,
# "5.0.10 -12"), "10" with an exponent, a minus with digits ("1.3 -4"), a superscript or an
# "e". The number written is then not the p-value (99 of the 230).
_P_LOST_POWER = (
    "^\\s*(?:[x×X·•⋅�ÃÂ*]|9\\s+10\\b|\\.\\s*10\\b"
    "|10\\s*(?:\\^|[-−À⁻]|\\s+[-−À]?\\d|E|e|$)"
    "|[-−À⁻]\\s*\\d|[⁰¹²³⁴-⁹]|[eE]\\s*[-−+À]?\\s*\\d)"
)

_P_UNIT = (
    "^\\s*(?i:%|‰|[µμnmck]?m\\b|[kmg]?hz\\b|[mun]?s\\b|ps\\b|[km]?pa\\b|bar\\b|"
    "[mk]?w\\b|[mk]?v\\b|k\\b|°|[km]?ev\\b|m?g\\b|kg\\b|g/ml|m?l\\b|mmhg\\b|dbm?\\b|"
    "h\\b|hr\\b|min\\b|years?\\b|days?\\b|atm\\b)"
)
_P_LOG = "(?i:log|\\bln)\\s*(?:\\(?\\s*10\\s*\\)?|₁₀)?\\s*\\(?\\s*$"
_P_FORMULA = "(?:/|(?<!\\s):|\\d-)$"


def _p_not_a_value(before: Any, after: Any) -> list[str | None]:
    """Why a "p" with a value below 0 or above 1 is not a p-value at all (``.p_not_a_value()``).

    From the text right before it (*before*) and right after its number (*after*); ``None``
    when there is no such reason: "unit" (a unit follows the number, so P is a physical
    quantity: "P = 2.9 µm", "P = 6.02 MPa"), "log" (a logarithm of p: "-log10 P = 7.5") or
    "formula" (the p is part of a name or a formula, written against "/" or ":" or after a number
    and a hyphen: "Ca/P = 1.67", "N:P = -0.41", "1-p"). 50 of the 230 were one of these, none a
    p-value.
    """
    unit = compile_r(_P_UNIT, perl=True)
    logp = compile_r(_P_LOG, perl=True)
    formula = compile_r(_P_FORMULA, perl=True)
    b = ["" if is_missing(x) else str(x) for x in before]
    a = ["" if is_missing(x) else str(x) for x in after]
    out: list[str | None] = [None] * len(b)
    for i in range(len(b)):
        if formula.search(b[i]):
            out[i] = "formula"
        if logp.search(b[i]):
            out[i] = "log"
        if unit.search(a[i]):
            out[i] = "unit"
    return out


_OPERATORS = ("=", "<", ">", "~", "≈", "≠", "≤", "≥", "≪", "≫")


def _r_stat_pattern() -> dict[str, str]:
    """Port of ``.r_stat_pattern()``: the ``<name> [(df)] <op> <value>`` pattern.

    Returns ``{"op": <comparison characters>, "pattern": <TRE regex>}``;
    shared by ``extract_eq()`` and ``read_r_output()``.
    """
    op = "".join(_OPERATORS)
    gr = "Ͱ-Ͽ"
    pattern = (
        f"([{gr}²a-zA-Z][{gr}²a-zA-Z0-9._-]*)\\s*"  # statistic name
        "(\\([^)]*\\))?\\s*"  # optional (df)
        f"([{op}]{{1,3}})\\s*"  # comparator
        # value: a number (no trailing comma), scientific notation, or "< .001".
        "(<\\s*[.0-9]+|-?[0-9]+(?:\\.[0-9]+)?(?:e[-+]?[0-9]+)?)"
    )
    return {"op": op, "pattern": pattern}


# ---- p-values that are not the result of a test ---------------------------------------
#
# Which p-values in sentences are clearly not the result of a test: a threshold or a
# legend, not a finding (metacheck #604). A table note ("* p < .05, ** p < .01"), a
# figure legend ("Asterisks indicate significant differences (p < .05)") or a stated
# threshold ("p < .05 was considered significant", "thresholded at P < .001") gives the
# level that marks significance, and is not a p-value reported imprecisely. GROBID often
# puts such notes and legends into the running text, so the section cannot tell them
# apart.
#
# Each p-value is judged in its own clause: the text around it up to a ";" or a full stop
# followed by a space. That keeps the items of a note apart ("p < .05. ** p < .01.") and
# keeps a note apart from a result in the same sentence. A clause that holds a test
# statistic or an estimate (t(20) =, F =, r =, b =, M =, a confidence interval) is never
# taken as a note: the p-value then belongs to a result ("F(1, 7) = 8.03, # p < .05").
#
# A p-value is taken as not a test when its clause has, and no statistic:
#   marker     a symbol right before it (*, a dagger, #, ^, +, ~ or a repeat of them), or
#              "ns" with ":" or "=" after it
#   threshold  words that state a threshold: a significance level, alpha, "threshold",
#              "cut-off", "criterion", "considered as significant", "set at"; not
#              "reached", which reports a result
#   legend     a marker that is the subject of a verb of meaning ("Asterisks indicate"),
#              the passive ("indicated by an asterisk"), or "in bold" / "in grey"

_GREEK_RANGE = "Ͱ-Ͽ"

# A test statistic with a value: t(20) =, F =, chi2 =, z =, W =, OR =, b =. Not a fit index, R
# squared, a size or another number, which a table note can follow (``.test_stat_head``). A
# chi-squared test in any of its written forms, also with a space before its 2, which reading a
# PDF often adds ("χ 2 (10) =", "X 2 (1) =", "chi-square(2) =") (#351)
_TEST_STAT_HEAD = (
    "(?<![\\w\u0370-\u03ff-])(?:t|F|z|Z|r|rs|R(?![\u00b22])|W|U|H|D|V|Q|T|B|b|d|g|OR|HR|RR|"
    "\u03c7\\s?[\u00b22\u2082]?|X\\s?[2\u00b2]|chi[- ]?squared?|chi\\s?[2\u00b2]|\u03c1|rho|\u03c4|\u03b2|\u03b7\u00b2?p?|\u03c9\u00b2?)"
    "[0-9]{0,4}\\s*(?:\\([^()]{0,40}\\)|\\[[^\\]\\[]{0,40}\\])?\\s*"
    "(?:=|<|>|\u2264|\u2265|\u00bc)\\s*[-\u2212\u00c0]?\\s*"
)
_TEST_STAT_PATTERN = _TEST_STAT_HEAD + "\\.?\\d"
# the same, with a value that is not 0 ("d = 0", "T = 0.00" are left out): a p-value of exactly 0
# needs a large statistic (#642)
_TEST_STAT_NONZERO = _TEST_STAT_HEAD + "(?!(?:0+\\.?0*|\\.0+)(?![\\d.]))\\.?\\d"

# The symbols of a table note: *, the asterisk operator, a dagger or double dagger, a section or
# paragraph sign, #, ^, ~ and the replacement character; also the low asterisk, "@", and A with
# a tilde, which is how GROBID reads an asterisk in some journals (#642)
_P_NOTE_SYMBOL = "[*\u2217\u2020\u2021\u00a7#\u00b6^~\ufffd\u00c3\u204e@]"


@functools.cache
def _pnt() -> SimpleNamespace:
    """The compiled patterns of :func:`_p_not_test` (built on first use)."""

    def rx(pattern: str, icase: bool = False) -> Any:
        return compile_r(pattern, ignore_case=icase, perl=True)

    g = _GREEK_RANGE
    # a statistic or an estimate: a short name, an optional (df), a comparison and a
    # number; not p (or q, an adjusted p), n, k, df or alpha, which thresholds and notes use
    # too. The fraction one quarter and A with a grave accent are how GROBID reads "="
    # and the minus sign in some journals.
    stat_name = (
        f"(?<![\\w.{g}-])(?!(?:p|P|ps|Ps|q|n|N|k|K|df|alpha|value|values|pvalue|pvalues)(?![\\w{g}]))"
        f"(?![pP]_?{_P_SUBSCRIPTS})"  # pFWE, padj: a p-value (#642)
        f"(?!α(?![\\w{g}]))"
        f"(?:[A-Za-z{g}][A-Za-z{g}0-9²_]{{0,5}}|χ\\s?[²2₂]?|X\\s[2²])"
        "\\s*(?:\\([^()]{0,40}\\)|\\[[^][]{0,40}\\])?\\s*"
        "(?:=|<|>|≤|≥|¼)\\s*[-−À]?\\s*\\.?\\d"
    )
    ci = "\\bCI\\b\\s*[\\[(=:]?\\s*[-−À]?\\.?\\d|\\d+\\s*%\\s*CI\\b"
    # a test statistic, which a p-value right after it belongs to: not a fit index, R
    # squared, a size or another number, which a table note can follow
    test_stat = _TEST_STAT_PATTERN
    # a p with a correction name is a p-value too ("pFWE < .05", #642)
    p_value = f"(?<![\\w])[pP]s?\\s*(?:-?values?)?(?:[\\s_-]?{_P_SUBSCRIPTS})?\\s*(?:=|<|>|≤|≥|¼)"
    symbol = _P_NOTE_SYMBOL
    marker = f"(?<![\\w)\\]/])(?:(?:{symbol}|(?<![\\w/+])\\++)\\s*)+[:=,]?\\s*$"
    # a marker in brackets, before the p-value ("(*) p < .05", "(**) = p < .01") or after it
    # ("p < .01 (**)") (#642)
    marker_br_before = f"\\(\\s*(?:{symbol}\\s*)+\\)\\s*[:=,]?\\s*$"
    marker_br_after = f"^\\s*\\(\\s*(?:{symbol}\\s*)+\\)"
    ns_marker = "(?<![\\w.])n\\.?s\\.?\\s*[:=]\\s*$"
    ns_plain = "(?<![\\w.])n\\.?s\\.?\\s*,?\\s*$"
    # threshold words, each in a form that states a threshold: "threshold" or "alpha"
    # alone also name perception thresholds and alpha power
    thresh = (
        "thresholded|threshold(?:s|ing)?\\s+(?:of|at|was|were|is|set|for\\s+(?:statistical\\s+)?significance|level)\\b|"
        "(?:significance|statistical|voxel-?(?:wise|level)|cluster(?:-|\\s)?(?:level|wise|forming|extent)?|"
        "height|extent|uncorrected|corrected|FWE|FDR|peak|whole-?brain|lenient|liberal|conservative|stringent)"
        "\\s+thresholds?\\b|threshold-free|"
        "significance\\s+(?:level|criterion|cut-?off)|levels?\\s+of\\s+(?:statistical\\s+)?significance|"
        "\\balpha\\s*(?:level|value|criterion|of|was|is|=|set|\\()|α\\s*(?:level|value|=|<|\\()|"
        "(?:corrected|adjusted|Bonferroni|nominal)\\s+(?:alpha|α)\\b|"
        "(?:considered|regarded|accepted|deemed|defined|taken|treated|interpreted)\\s+(?:as\\s+)?(?:to\\s+be\\s+)?"
        "(?:statistically\\s+)?significan|(?:set|fixed)\\s+(?:at|to)\\s+(?:an?\\s+|the\\s+)?(?:alpha|α|p\\b|P\\b|two|one|\\.|0)|"
        "criteri(?:on|a)\\s+(?:for|of)\\s+(?:statistical\\s+)?significan|as\\s+(?:the|a|our)\\s+criterion|"
        "cut-?off\\s+(?:value\\s+)?(?:of|for|at)\\b|as\\s+(?:the|a)\\s+cut-?off|cut-?off\\s*:|"
        "significan(?:t|ce)\\s+(?:was|were|is|are)\\s+(?:set|defined|accepted|considered|determined|assumed)"
    )
    # words that report a result against the level: "did not reach significance levels",
    # "was found above the level of statistical significance"
    reach = (
        "\\b(?:reach|exceed|surpass|surviv)\\w*|\\bpass(?:ed|es|ing)?\\b|"
        "\\b(?:above|below|beyond)\\s+(?:the\\s+)?(?:\\w+\\s+)?(?:level|threshold|significan)"
    )
    vis = (
        "asterisks?|stars?|daggers?|symbols?|markers?|letters|superscripts?|"
        "(?:(?:filled|open|solid|dashed|dotted|horizontal|vertical|black|white|gr[ae]y|red|blue|green|"
        "orange|yellow|purple|pink|colou?red|bold|thick|shaded)\\s+)+"
        "(?:dots?|circles?|squares?|triangles?|diamonds?|lines?|bars?|arrows?|symbols?|markers?|"
        "boxes|cells?|areas?|regions?|channels?|clusters?|voxels?|points?|values?|entries|estimates|numbers|fonts?|text)"
        "|bold(?:ed|face)?\\s+(?:values?|entries|estimates|numbers|coefficients|correlations|cells?|text|font)"
    )
    verb = "(?:indicat|denot|represent|mark|show|highlight|depict|signif|correspond|identif)\\w*"
    legend = (
        f"\\b(?:{vis})\\s+(?:\\S+\\s+){{0,3}}?{verb}|"
        "(?:indicated|denoted|marked|highlighted|shown|represented|labell?ed|depicted|flagged|printed|"
        "presented|displayed|set|written|given)\\s+(?:by|with|in|as|using)\\s+(?:an?\\s+|the\\s+)?"
        "(?:asterisks?|stars?|daggers?|bold|boldface|italics?|colou?rs?|gr[ae]y|red|blue|green|black|white|"
        f"symbols?|letters|superscripts?|lines?|bars?|dots|circles|arrows|shading|boxes|{symbol})"
        "|\\bin\\s+(?:bold|boldface|gr[ae]y|colou?r)\\b"
        # "* and ** indicate p < .05 and p < .01", "red dotted line: corrected p < .05"
        f"|(?:{symbol}\\s*)+(?:(?:and|or|,)\\s*(?:{symbol}\\s*)+)*(?:indicat|denot|represent|mark|correspond)\\w*"
        f"|\\b(?:{vis})\\s*:"
    )
    # the end of a clause: ";", or a full stop with a space or a marker after it
    boundary = f";|\\.\\s+|\\.(?={symbol})|:\\s+(?=[A-Z])"
    comparative = (
        "(?:higher|lower|greater|smaller|larger|increased|decreased|elevated|reduced|raised)"
    )
    return SimpleNamespace(
        stat_name=rx(stat_name),
        ci=rx(ci),
        test_stat=rx(test_stat),
        p_value=rx(p_value),
        symbol=rx(symbol),
        marker=rx(marker),
        marker_br_before=rx(marker_br_before),
        marker_br_after=rx(marker_br_after),
        ns_marker=rx(ns_marker, True),
        ns_plain=rx(ns_plain, True),
        symbol_p=rx(f"{symbol}\\s*[pP]\\b"),
        next_p=rx(f"{p_value}|{symbol}"),
        thresh=rx(thresh, True),
        reach=rx(reach, True),
        legend=rx(legend, True),
        boundary=rx(boundary),
        p_plural=rx("^[pP]\\s*['’]?s\\b"),
        all_both=rx("\\b(?:all|both|each)\\s*\\(?\\s*$", True),
        attached_before=rx(
            "(?:threshold(?:ed|s)?|alpha|α|cut-?off|criterion|significance\\s+level)"
            "\\s*(?:of|at|=|:|\\(|was\\s+set\\s+(?:at|to)|set\\s+(?:at|to))?\\s*"
            "(?:an?\\s+|the\\s+)?(?:(?:two|one)-?\\s*tailed\\s+|(?:un)?corrected\\s+)?$",
            True,
        ),
        attached_after=rx(
            "^\\s*\\)?\\s*(?:\\(?(?:uncorrected|corrected|FWE|FDR|two-tailed|one-tailed)\\)?,?\\s*)*"
            "threshold",
            True,
        ),
        # a threshold that is measured, not set: "participants showed a higher exit
        # threshold (P < 0.05)", "lower thresholds for CA than DYS". A threshold that is set
        # can be "lower" too ("using a lower statistical threshold of P < .005"): a measured
        # one is compared ("than") or someone has it ("showed a higher threshold").
        comparative=rx(f"\\b{comparative}\\s+(?:\\w+\\s+){{0,2}}thresholds?\\b", True),
        than=rx("\\bthan\\b", True),
        has=rx(
            "\\b(?:showed|shows?|had|has|have|exhibited|displayed|revealed|demonstrated|yielded)\\s+"
            f"(?:an?\\s+|the\\s+)?(?:\\w+\\s+){{0,1}}{comparative}",
            True,
        ),
    )


def _reports_test(sentence: Any, nonzero: Any = False) -> list[bool]:
    """Port of ``.reports_test()``: whether each sentence reports a statistical test.

    A test statistic with a value, another p-value written with a decimal point or an
    exponent, or a word that names a test or its outcome. ``extract_p_values()`` reads a
    whole "p = 0" or "p = 1" as a p-value only in such a sentence; otherwise it is often a
    probability or a model parameter ("with p = 1", "ARIMA values p = 0") (#642). With
    *nonzero* true (for "p = 0"), a statistic counts only with a value that is not 0: "ARIMA
    values p = 0, d = 0" has no test. *sentence* and *nonzero* are vectorised (a scalar
    *nonzero* is recycled); ``NA`` is an empty sentence.
    """
    sentences = [sentence] if isinstance(sentence, str) or is_missing(sentence) else list(sentence)
    texts = ["" if is_missing(x) else str(x) for x in sentences]
    flags = list(nonzero) if isinstance(nonzero, list | tuple) else [nonzero] * len(texts)
    if len(flags) < len(texts):  # rep_len()
        flags = (
            [flags[i % len(flags)] for i in range(len(texts))] if flags else [False] * len(texts)
        )
    stat = compile_r(_TEST_STAT_PATTERN, perl=True)
    stat_nz = compile_r(_TEST_STAT_NONZERO, perl=True)
    other_p = compile_r(_OTHER_P, perl=True)
    words = compile_r(_TEST_WORDS, ignore_case=True, perl=True)
    return [
        bool(
            (stat_nz if (flags[i] is True or flags[i] == 1) else stat).search(t)
            or other_p.search(t)
            or words.search(t)
        )
        for i, t in enumerate(texts)
    ]


_OTHER_P = (
    "(?<![\\w])[pP]s?(?:-?values?)?\\s*[=<>\u2264\u2265\u2248~\u00bc]\\s*"
    "(?:\\d?\\.\\d|\\d+\\s*(?:[eE]|[x\u00d7*\u00c2\u00c3]\\s*10)\\s*[-\u2212\u00c0]"
    "|10\\s*\\^?\\s*[-\u2212\u00c0]\\s*\\d)"
)
_TEST_WORDS = (
    "\\btest(?:s|ed|ing)?\\b|significan|\\bdiffer|Fisher|Wilcoxon|Mann.?Whitney|"
    "Kruskal|Kolmogorov|binomial|chi.?squ|AN(?:C|M)?OVA|permut|bootstrap|"
    "Bonferroni|\\bHolm\\b|Tukey|Dunn|Sidak|adjusted|corrected|post.?hoc|"
    "pairwise|comparisons?\\b|likelihood.ratio|\\bLRT\\b|regression|correlat|\\bexact\\b"
)


def _p_clause(p: Any) -> Any:
    """Port of ``.p_clause()``: the clause of each p-value in its sentence.

    The text before it back to the last ";" or full stop (at most 120 characters), and the
    text after it on to the next (at most 80). The k-th row of a p-value in a sentence is its
    k-th place there, and "p < .05" is not found inside "p < .051". *p* has the columns
    ``paper_id``, ``text_id``, ``text`` and ``expanded``. Returns a data frame with ``before``
    and ``after``, ``NA`` where the p-value is not found. Used by ``_p_not_test()`` and
    ``stat_p_exact`` (#604, #642).
    """
    before, after = _p_clause_lists(p)
    return pd.DataFrame(
        {
            "before": pd.Series(before, dtype="string"),
            "after": pd.Series(after, dtype="string"),
        }
    )


def _p_clause_lists(p: Any) -> tuple[list[str | None], list[str | None]]:
    n = len(p)
    before: list[str | None] = [None] * n
    after: list[str | None] = [None] * n
    if not n:
        return before, after
    cols = set(p.columns)
    sentence = [
        "" if is_missing(x) else str(x)
        for x in (p["expanded"] if "expanded" in cols else p["text"])
    ]
    ptext = [str(x).strip() if not is_missing(x) else "" for x in p["text"]]
    paper_ids = [str(x) for x in (p["paper_id"] if "paper_id" in cols else [None] * n)]
    text_ids = [str(x) for x in (p["text_id"] if "text_id" in cols else [None] * n)]

    # the k-th row of a p-value in a sentence is its k-th place there
    seen: dict[tuple[str, str, str, str], int] = {}
    ks: list[int] = []
    for i in range(n):
        key = (paper_ids[i], text_ids[i], sentence[i], ptext[i])
        seen[key] = seen.get(key, 0) + 1
        ks.append(seen[key])

    boundary = _pnt().boundary
    for i in range(n):
        s = sentence[i]
        # not inside a longer value: "p < .05" is not found in "p < .051"
        esc = re.sub(r"([\]\[{}()+*^$|\\.?])", r"\\\1", ptext[i])
        at = [m.start() for m in compile_r(esc + "(?![0-9])", perl=True).finditer(s)]
        if not at or ks[i] > len(at):
            continue  # not found: may be a result
        start = at[ks[i] - 1]
        end = start + len(ptext[i])
        b = s[max(0, start - 120) : start]
        a = s[end : end + 80]
        # the clause: back to the last boundary, on to the next
        cuts = list(boundary.finditer(b))
        if cuts:
            b = b[cuts[-1].end() :]
        cut_a = boundary.search(a)
        if cut_a:
            a = a[: cut_a.start()]
        before[i] = b
        after[i] = a
    return before, after


def _p_not_test(p: pd.DataFrame) -> list[str | None]:
    """Why each p-value of *p* is clearly not the result of a test (port of ``.p_not_test()``).

    *p* is the table of ``extract_p_values()`` with ``expanded`` (the sentence): the
    columns ``paper_id``, ``text_id``, ``text`` (the p-value as matched) and ``expanded``.
    Returns one entry per row: ``"marker"``, ``"threshold"``, ``"legend"``, or ``None`` for
    a p-value that may be a result.
    """
    n = len(p)
    out: list[str | None] = [None] * n
    if n == 0:
        return out
    sentences = [
        "" if is_missing(s) else str(s) for s in p["expanded" if "expanded" in p else "text"]
    ]
    ptexts = [("" if is_missing(t) else str(t)).strip(" \t\r\n") for t in p["text"]]
    ctx_before, ctx_after = _p_clause_lists(p)

    r = _pnt()
    for i in range(n):
        s = sentences[i]
        ptext = ptexts[i]
        before, after = ctx_before[i], ctx_after[i]
        if before is None or after is None:
            continue  # not found: may be a result
        clause = f"{before} {after}"

        # a marker note: unless a test statistic comes right before the p-value, with no
        # other p-value between them ("t52 = 6.92, ***p < .001"), or right after it
        # ("*p < .05 [z = 2.38]")
        if (
            r.marker.search(before)
            or r.ns_marker.search(before)
            or r.marker_br_before.search(before)
            or r.marker_br_after.search(after)
            or (r.ns_plain.search(before) and r.symbol_p.search(s))
        ):
            stats = list(r.test_stat.finditer(before))
            owned = False
            if stats:
                owned = not r.p_value.search(before[stats[-1].end() :])
            nxt = r.next_p.search(after)
            soon = after[: nxt.start()] if nxt else after[:30]
            if not owned and not r.test_stat.search(soon[:30]):
                out[i] = "marker"
            continue

        # the words of a threshold or a legend: never with a statistic or an estimate
        # anywhere in the clause, and never for "ps" or "all p", which report results
        # ("remained significant when thresholded at varying levels, all Ps < .002")
        if r.stat_name.search(clause) or r.ci.search(clause):
            continue
        if r.p_plural.search(ptext) or r.all_both.search(before):
            continue
        # the p-value is the threshold itself: "a threshold of p < .05", "the p < .05
        # uncorrected threshold", even where clusters "survived" it
        attached = bool(r.attached_before.search(before) or r.attached_after.search(after))
        measured = bool(r.comparative.search(clause)) and bool(
            r.than.search(clause) or r.has.search(clause)
        )
        if measured:
            continue
        if attached:
            out[i] = "threshold"
            continue
        if r.thresh.search(clause) and not r.reach.search(clause):
            out[i] = "threshold"
        elif r.legend.search(clause):
            out[i] = "legend"
    return out
