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
from typing import Any

from metacheck._r.base import as_character, r_round
from metacheck._r.regex import grepl, gsub, sub
from metacheck._values import as_float, is_missing

__all__ = [
    "_r_stat_pattern",
    "_stat_display_value",
    "_stat_html_escape",
    "_stato_strip_variant",
]

_NUMBER_TEXT = r"^[-+]?[0-9.]+([eE][-+]?[0-9]+)?$"


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


_NT_STAT_NAME = (
    "(?<![\\w.\u0370-\u03ff-])(?!(?:p|P|ps|Ps|q|n|N|k|K|df|alpha|value|values|pvalue|pvalues)(?![\\w\u0370-\u03ff]))"
    "(?!\u03b1(?![\\w\u0370-\u03ff]))"
    "(?:[A-Za-z\u0370-\u03ff][A-Za-z\u0370-\u03ff0-9\u00b2_]{0,5}|\u03c7\u00b2?)"
    "\\s*(?:\\([^()]{0,40}\\)|\\[[^\\]\\[]{0,40}\\])?\\s*"
    "(?:=|<|>|\u2264|\u2265|\u00bc)\\s*[-\u2212\u00c0]?\\s*\\.?\\d"
)
_NT_CI = "\\bCI\\b\\s*[\\[(=:]?\\s*[-\u2212\u00c0]?\\.?\\d|\\d+\\s*%\\s*CI\\b"
_NT_TEST_STAT = (
    "(?<![\\w\u0370-\u03ff-])(?:t|F|z|Z|r|rs|R(?![\u00b22])|W|U|H|D|V|Q|T|B|b|d|g|OR|HR|RR|"
    "\u03c7\u00b2?|\u03c72|X2|chi2|chi\u00b2|\u03c1|rho|\u03c4|\u03b2|\u03b7\u00b2?p?|\u03c9\u00b2?)"
    "[0-9]{0,4}\\s*(?:\\([^()]{0,40}\\)|\\[[^\\]\\[]{0,40}\\])?\\s*"
    "(?:=|<|>|\u2264|\u2265|\u00bc)\\s*[-\u2212\u00c0]?\\s*\\.?\\d"
)
_NT_P_VALUE = "(?<![\\w])[pP]s?\\s*(?:-?values?)?\\s*(?:=|<|>|\u2264|\u2265|\u00bc)"
_NT_SYMBOL = "[*\u2217\u2020\u2021\u00a7#\u00b6^~\ufffd]"
_NT_MARKER = "(?<![\\w)\\]/])(?:(?:" + _NT_SYMBOL + "|(?<![\\w/+])\\++)\\s*)+[:=,]?\\s*$"
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
    import re

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
    paper_ids = [str(x) for x in p["paper_id"]]
    text_ids = [str(x) for x in p["text_id"]]

    # the k-th row of a p-value in a sentence is its k-th place there
    seen: dict[tuple[str, str, str, str], int] = {}
    ks: list[int] = []
    for i in range(n):
        key = (paper_ids[i], text_ids[i], sentence[i], ptext[i])
        seen[key] = seen.get(key, 0) + 1
        ks.append(seen[key])

    marker = _nt("marker", _NT_MARKER)
    ns_marker = _nt("ns_marker", _NT_NS_MARKER, True)
    ns_plain = _nt("ns_plain", _NT_NS_PLAIN, True)
    symbol_p = _nt("symbol_p", _NT_SYMBOL + "\\s*[pP]\\b")
    test_stat = _nt("test_stat", _NT_TEST_STAT)
    p_value = _nt("p_value", _NT_P_VALUE)
    p_or_symbol = _nt("p_or_symbol", _NT_P_VALUE + "|" + _NT_SYMBOL)
    boundary = _nt("boundary", _NT_BOUNDARY)
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
        # not inside a longer value: "p < .05" is not found in "p < .051"
        esc = re.sub(r"([\]\[{}()+*^$|\\.?])", r"\\\1", ptext[i])
        at = [m.start() for m in _re(esc + "(?![0-9])").finditer(s)]
        if not at or ks[i] > len(at):
            continue  # not found: may be a result
        start = at[ks[i] - 1]
        end = start + len(ptext[i])
        before = s[max(0, start - 120) : start]
        after = s[end : end + 80]
        # the clause: back to the last boundary, on to the next
        cuts = list(boundary.finditer(before))
        if cuts:
            before = before[cuts[-1].end() :]
        cut_a = boundary.search(after)
        if cut_a:
            after = after[: cut_a.start()]
        clause = before + " " + after

        # a marker note: unless a test statistic comes right before the p-value, with no
        # other p-value between them ("t52 = 6.92, ***p < .001"), or right after it
        # ("*p < .05 [z = 2.38]")
        if (
            marker.search(before)
            or ns_marker.search(before)
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
