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

import math
import re
from typing import Any

from metacheck._r.base import as_character, r_round
from metacheck._r.regex import grepl, gsub, sub
from metacheck._values import as_float, is_missing
from metacheck.text.extract import _P_SUBSCRIPTS

__all__ = [
    "_p_clause",
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


# ---- p-values that are not the result of a test (metacheck 0.4.3.19, #604) --------


def _re(pattern: str, ignore_case: bool = False) -> Any:
    from metacheck._r.regex import compile_r

    return compile_r(pattern, ignore_case=ignore_case, perl=True)


# a statistic or an estimate: a short name, an optional (df), a comparison and a number; not p
# (or q, an adjusted p), n, k, df or alpha, which thresholds and notes use too. The fraction one
# quarter and A with a grave accent are how GROBID reads "=" and the minus sign in some journals.
_NT_STAT_NAME = (
    "(?<![\\w.\u0370-\u03ff-])(?!(?:p|P|ps|Ps|q|n|N|k|K|df|alpha|value|values|pvalue|pvalues)(?![\\w\u0370-\u03ff]))"
    "(?![pP]_?" + _P_SUBSCRIPTS + ")"  # pFWE, padj: a p-value (#642)
    "(?!\u03b1(?![\\w\u0370-\u03ff]))"
    "(?:[A-Za-z\u0370-\u03ff][A-Za-z\u0370-\u03ff0-9\u00b2_]{0,5}|\u03c7\u00b2?)"
    "\\s*(?:\\([^()]{0,40}\\)|\\[[^\\]\\[]{0,40}\\])?\\s*"
    "(?:=|<|>|\u2264|\u2265|\u00bc)\\s*[-\u2212\u00c0]?\\s*\\.?\\d"
)
_NT_CI = "\\bCI\\b\\s*[\\[(=:]?\\s*[-\u2212\u00c0]?\\.?\\d|\\d+\\s*%\\s*CI\\b"
# A test statistic with a value: t(20) =, F =, chi2 =, z =, W =, OR =, b =. Not a fit index, R
# squared, a size or another number, which a table note can follow (``.test_stat_head``)
_TEST_STAT_HEAD = (
    "(?<![\\w\u0370-\u03ff-])(?:t|F|z|Z|r|rs|R(?![\u00b22])|W|U|H|D|V|Q|T|B|b|d|g|OR|HR|RR|"
    "\u03c7\u00b2?|\u03c72|X2|chi2|chi\u00b2|\u03c1|rho|\u03c4|\u03b2|\u03b7\u00b2?p?|\u03c9\u00b2?)"
    "[0-9]{0,4}\\s*(?:\\([^()]{0,40}\\)|\\[[^\\]\\[]{0,40}\\])?\\s*"
    "(?:=|<|>|\u2264|\u2265|\u00bc)\\s*[-\u2212\u00c0]?\\s*"
)
_TEST_STAT_PATTERN = _TEST_STAT_HEAD + "\\.?\\d"
# the same, with a value that is not 0 ("d = 0", "T = 0.00" are left out): a p-value of exactly 0
# needs a large statistic (#642)
_TEST_STAT_NONZERO = _TEST_STAT_HEAD + "(?!(?:0+\\.?0*|\\.0+)(?![\\d.]))\\.?\\d"
_NT_TEST_STAT = _TEST_STAT_PATTERN
# a p with a correction name is a p-value too ("pFWE < .05", #642)
_NT_P_VALUE = (
    "(?<![\\w])[pP]s?\\s*(?:-?values?)?(?:[\\s_-]?" + _P_SUBSCRIPTS + ")?"
    "\\s*(?:=|<|>|\u2264|\u2265|\u00bc)"
)
# The symbols of a table note: *, the asterisk operator, a dagger or double dagger, a section or
# paragraph sign, #, ^, ~ and the replacement character; also the low asterisk, "@", and A with
# a tilde, which is how GROBID reads an asterisk in some journals (#642)
_P_NOTE_SYMBOL = "[*\u2217\u2020\u2021\u00a7#\u00b6^~\ufffd\u00c3\u204e@]"
_NT_SYMBOL = _P_NOTE_SYMBOL
_NT_MARKER = "(?<![\\w)\\]/])(?:(?:" + _NT_SYMBOL + "|(?<![\\w/+])\\++)\\s*)+[:=,]?\\s*$"
# a marker in brackets, before the p-value ("(*) p < .05", "(**) = p < .01") or after it
# ("p < .01 (**)") (#642)
_NT_MARKER_BR_BEFORE = "\\(\\s*(?:" + _NT_SYMBOL + "\\s*)+\\)\\s*[:=,]?\\s*$"
_NT_MARKER_BR_AFTER = "^\\s*\\(\\s*(?:" + _NT_SYMBOL + "\\s*)+\\)"
_NT_NS_MARKER = "(?<![\\w.])n\\.?s\\.?\\s*[:=]\\s*$"
_NT_NS_PLAIN = "(?<![\\w.])n\\.?s\\.?\\s*,?\\s*$"
_NT_THRESH = (
    "thresholded|threshold(?:s|ing)?\\s+(?:of|at|was|were|is|set|for\\s+(?:statistical\\s+)?significance|level)\\b|"
    "(?:significance|statistical|voxel-?(?:wise|level)|cluster(?:-|\\s)?(?:level|wise|forming|extent)?|"
    "height|extent|uncorrected|corrected|FWE|FDR|peak|whole-?brain|lenient|liberal|conservative|stringent)"
    "\\s+thresholds?\\b|threshold-free|"
    "significance\\s+(?:level|criterion|cut-?off)|levels?\\s+of\\s+(?:statistical\\s+)?significance|"
    "\\balpha\\s*(?:level|value|criterion|of|was|is|=|set|\\()|\u03b1\\s*(?:level|value|=|<|\\()|"
    "(?:corrected|adjusted|Bonferroni|nominal)\\s+(?:alpha|\u03b1)\\b|"
    "(?:considered|regarded|accepted|deemed|defined|taken|treated|interpreted)\\s+(?:as\\s+)?(?:to\\s+be\\s+)?"
    "(?:statistically\\s+)?significan|(?:set|fixed)\\s+(?:at|to)\\s+(?:an?\\s+|the\\s+)?(?:alpha|\u03b1|p\\b|P\\b|two|one|\\.|0)|"
    "criteri(?:on|a)\\s+(?:for|of)\\s+(?:statistical\\s+)?significan|as\\s+(?:the|a|our)\\s+criterion|"
    "cut-?off\\s+(?:value\\s+)?(?:of|for|at)\\b|as\\s+(?:the|a)\\s+cut-?off|cut-?off\\s*:|"
    "significan(?:t|ce)\\s+(?:was|were|is|are)\\s+(?:set|defined|accepted|considered|determined|assumed)"
)
_NT_REACH = (
    "\\b(?:reach|exceed|surpass|surviv)\\w*|\\bpass(?:ed|es|ing)?\\b|"
    "\\b(?:above|below|beyond)\\s+(?:the\\s+)?(?:\\w+\\s+)?(?:level|threshold|significan)"
)
_NT_VIS = (
    "asterisks?|stars?|daggers?|symbols?|markers?|letters|superscripts?|"
    "(?:(?:filled|open|solid|dashed|dotted|horizontal|vertical|black|white|gr[ae]y|red|blue|green|"
    "orange|yellow|purple|pink|colou?red|bold|thick|shaded)\\s+)+"
    "(?:dots?|circles?|squares?|triangles?|diamonds?|lines?|bars?|arrows?|symbols?|markers?|"
    "boxes|cells?|areas?|regions?|channels?|clusters?|voxels?|points?|values?|entries|estimates|numbers|fonts?|text)"
    "|bold(?:ed|face)?\\s+(?:values?|entries|estimates|numbers|coefficients|correlations|cells?|text|font)"
)
_NT_VERB = "(?:indicat|denot|represent|mark|show|highlight|depict|signif|correspond|identif)\\w*"
_NT_LEGEND = (
    "\\b(?:" + _NT_VIS + ")\\s+(?:\\S+\\s+){0,3}?" + _NT_VERB + "|"
    "(?:indicated|denoted|marked|highlighted|shown|represented|labell?ed|depicted|flagged|printed|"
    "presented|displayed|set|written|given)\\s+(?:by|with|in|as|using)\\s+(?:an?\\s+|the\\s+)?"
    "(?:asterisks?|stars?|daggers?|bold|boldface|italics?|colou?rs?|gr[ae]y|red|blue|green|black|white|"
    "symbols?|letters|superscripts?|lines?|bars?|dots|circles|arrows|shading|boxes|"
    + _NT_SYMBOL
    + ")"
    "|\\bin\\s+(?:bold|boldface|gr[ae]y|colou?r)\\b"
    "|(?:" + _NT_SYMBOL + "\\s*)+(?:(?:and|or|,)\\s*(?:" + _NT_SYMBOL + "\\s*)+)*"
    "(?:indicat|denot|represent|mark|correspond)\\w*"
    "|\\b(?:" + _NT_VIS + ")\\s*:"
)
_NT_BOUNDARY = ";|\\.\\s+|\\.(?=" + _NT_SYMBOL + ")|:\\s+(?=[A-Z])"
_NT_ATTACHED_BEFORE = (
    "(?:threshold(?:ed|s)?|alpha|\u03b1|cut-?off|criterion|significance\\s+level)"
    "\\s*(?:of|at|=|:|\\(|was\\s+set\\s+(?:at|to)|set\\s+(?:at|to))?\\s*"
    "(?:an?\\s+|the\\s+)?(?:(?:two|one)-?\\s*tailed\\s+|(?:un)?corrected\\s+)?$"
)
_NT_ATTACHED_AFTER = (
    "^\\s*\\)?\\s*(?:\\(?(?:uncorrected|corrected|FWE|FDR|two-tailed|one-tailed)\\)?,?\\s*)*"
    "threshold"
)
_NT_COMPARATIVE = (
    "(?:higher|lower|greater|smaller|larger|increased|decreased|elevated|reduced|raised)"
)
_NT_PATTERNS: dict[str, Any] = {}


def _nt(name: str, pattern: str, ignore_case: bool = False) -> Any:
    key = f"{name}/{int(ignore_case)}"
    if key not in _NT_PATTERNS:
        _NT_PATTERNS[key] = _re(pattern, ignore_case)
    return _NT_PATTERNS[key]


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
    stat = _nt("test_stat", _TEST_STAT_PATTERN)
    stat_nz = _nt("test_stat_nonzero", _TEST_STAT_NONZERO)
    other_p = _nt("other_p", _OTHER_P)
    words = _nt("test_words", _TEST_WORDS, True)
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
    import pandas as pd

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
    paper_ids = [str(x) for x in p["paper_id"]]
    text_ids = [str(x) for x in p["text_id"]]

    # the k-th row of a p-value in a sentence is its k-th place there
    seen: dict[tuple[str, str, str, str], int] = {}
    ks: list[int] = []
    for i in range(n):
        key = (paper_ids[i], text_ids[i], sentence[i], ptext[i])
        seen[key] = seen.get(key, 0) + 1
        ks.append(seen[key])

    boundary = _nt("boundary", _NT_BOUNDARY)
    for i in range(n):
        s = sentence[i]
        # not inside a longer value: "p < .05" is not found in "p < .051"
        esc = re.sub(r"([\]\[{}()+*^$|\\.?])", r"\\\1", ptext[i])
        at = [m.start() for m in _re(esc + "(?![0-9])").finditer(s)]
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


def _p_not_test(p: Any) -> list[str | None]:
    """Port of ``.p_not_test()``: which p-values in sentences are not the result of a test.

    A table note ("* p < .05, ** p < .01"), a figure legend ("Asterisks indicate
    significant differences (p < .05)") or a stated threshold ("p < .05 was considered
    significant") gives the level that marks significance; it is not a p-value reported
    imprecisely. Each p-value is judged in its own clause (the text around it up to a ";"
    or a full stop followed by a space), and never as a note when the clause holds a test
    statistic or an estimate ("F(1, 7) = 8.03, # p < .05").

    *p* is the table of ``extract_p_values()`` with ``expanded`` (the sentence). Returns one
    entry per row: ``"marker"``, ``"threshold"``, ``"legend"``, or ``None`` for a p-value
    that may be a result.
    """
    n = len(p)
    out: list[str | None] = [None] * n
    if not n:
        return out
    cols = set(p.columns)
    sentence = [
        "" if is_missing(x) else str(x)
        for x in (p["expanded"] if "expanded" in cols else p["text"])
    ]
    ptext = [str(x).strip() if not is_missing(x) else "" for x in p["text"]]
    ctx_before, ctx_after = _p_clause_lists(p)

    marker = _nt("marker", _NT_MARKER)
    marker_br_before = _nt("marker_br_before", _NT_MARKER_BR_BEFORE)
    marker_br_after = _nt("marker_br_after", _NT_MARKER_BR_AFTER)
    ns_marker = _nt("ns_marker", _NT_NS_MARKER, True)
    ns_plain = _nt("ns_plain", _NT_NS_PLAIN, True)
    symbol_p = _nt("symbol_p", _NT_SYMBOL + "\\s*[pP]\\b")
    test_stat = _nt("test_stat", _NT_TEST_STAT)
    p_value = _nt("p_value", _NT_P_VALUE)
    p_or_symbol = _nt("p_or_symbol", _NT_P_VALUE + "|" + _NT_SYMBOL)
    stat_name = _nt("stat_name", _NT_STAT_NAME)
    ci = _nt("ci", _NT_CI)
    p_plural = _nt("p_plural", "^[pP]\\s*['\u2019]?s\\b")
    all_both = _nt("all_both", "\\b(?:all|both|each)\\s*\\(?\\s*$", True)
    attached_before = _nt("attached_before", _NT_ATTACHED_BEFORE, True)
    attached_after = _nt("attached_after", _NT_ATTACHED_AFTER, True)
    comp_threshold = _nt(
        "comp_threshold", "\\b" + _NT_COMPARATIVE + "\\s+(?:\\w+\\s+){0,2}thresholds?\\b", True
    )
    than = _nt("than", "\\bthan\\b", True)
    someone_has = _nt(
        "someone_has",
        "\\b(?:showed|shows?|had|has|have|exhibited|displayed|revealed|demonstrated|yielded)\\s+"
        "(?:an?\\s+|the\\s+)?(?:\\w+\\s+){0,1}" + _NT_COMPARATIVE,
        True,
    )
    thresh = _nt("thresh", _NT_THRESH, True)
    reach = _nt("reach", _NT_REACH, True)
    legend = _nt("legend", _NT_LEGEND, True)

    for i in range(n):
        s = sentence[i]
        before, after = ctx_before[i], ctx_after[i]
        if before is None or after is None:
            continue  # not found: may be a result
        clause = before + " " + after

        # a marker note: unless a test statistic comes right before the p-value, with no
        # other p-value between them ("t52 = 6.92, ***p < .001"), or right after it
        # ("*p < .05 [z = 2.38]")
        if (
            marker.search(before)
            or ns_marker.search(before)
            or marker_br_before.search(before)
            or marker_br_after.search(after)
            or (ns_plain.search(before) and symbol_p.search(s))
        ):
            sts = list(test_stat.finditer(before))
            owned = False
            if sts:
                between = before[sts[-1].end() :]
                owned = not p_value.search(between)
            next_p = p_or_symbol.search(after)
            soon = after[: next_p.start()] if next_p else after[:30]
            if not owned and not test_stat.search(soon[:30]):
                out[i] = "marker"
            continue

        # the words of a threshold or a legend: never with a statistic or an estimate
        # anywhere in the clause, and never for "ps" or "all p", which report results
        if stat_name.search(clause) or ci.search(clause):
            continue
        if p_plural.search(ptext[i]) or all_both.search(before):
            continue
        # the p-value is the threshold itself: "a threshold of p < .05", "the p < .05
        # uncorrected threshold", even where clusters "survived" it
        attached = bool(attached_before.search(before)) or bool(attached_after.search(after))
        # a threshold that is measured, not set: "participants showed a higher exit
        # threshold (P < 0.05)", "lower thresholds for CA than DYS"
        measured = bool(comp_threshold.search(clause)) and (
            bool(than.search(clause)) or bool(someone_has.search(clause))
        )
        if measured:
            continue
        if attached:
            out[i] = "threshold"
            continue
        if thresh.search(clause) and not reach.search(clause):
            out[i] = "threshold"
        elif legend.search(clause):
            out[i] = "legend"
    return out
