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

import pandas as pd

from metacheck._r.base import as_character, r_round
from metacheck._r.regex import compile_r, grepl, gsub, sub
from metacheck._values import as_float, is_missing

__all__ = [
    "_p_not_test",
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
        f"(?!α(?![\\w{g}]))"
        f"(?:[A-Za-z{g}][A-Za-z{g}0-9²_]{{0,5}}|χ²?)"
        "\\s*(?:\\([^()]{0,40}\\)|\\[[^][]{0,40}\\])?\\s*"
        "(?:=|<|>|≤|≥|¼)\\s*[-−À]?\\s*\\.?\\d"
    )
    ci = "\\bCI\\b\\s*[\\[(=:]?\\s*[-−À]?\\.?\\d|\\d+\\s*%\\s*CI\\b"
    # a test statistic, which a p-value right after it belongs to: not a fit index, R
    # squared, a size or another number, which a table note can follow
    test_stat = (
        f"(?<![\\w{g}-])(?:t|F|z|Z|r|rs|R(?![²2])|W|U|H|D|V|Q|T|B|b|d|g|OR|HR|RR|"
        "χ²?|χ2|X2|chi2|chi²|ρ|rho|τ|β|η²?p?|ω²?)"
        "[0-9]{0,4}\\s*(?:\\([^()]{0,40}\\)|\\[[^][]{0,40}\\])?\\s*"
        "(?:=|<|>|≤|≥|¼)\\s*[-−À]?\\s*\\.?\\d"
    )
    p_value = "(?<![\\w])[pP]s?\\s*(?:-?values?)?\\s*(?:=|<|>|≤|≥|¼)"
    symbol = "[*∗†‡§#¶^~�]"
    marker = f"(?<![\\w)\\]/])(?:(?:{symbol}|(?<![\\w/+])\\++)\\s*)+[:=,]?\\s*$"
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
    paper_ids = p["paper_id"].tolist() if "paper_id" in p else [None] * n
    text_ids = p["text_id"].tolist() if "text_id" in p else [None] * n

    # the k-th row of a p-value in a sentence is its k-th place there
    seen: dict[tuple[str, str, str, str], int] = {}
    ks: list[int] = []
    for pid, tid, s, t in zip(paper_ids, text_ids, sentences, ptexts, strict=True):
        key = (str(pid), str(tid), s, t)
        seen[key] = seen.get(key, 0) + 1
        ks.append(seen[key])

    r = _pnt()
    for i in range(n):
        s = sentences[i]
        ptext = ptexts[i]
        # not inside a longer value: "p < .05" is not found in "p < .051"
        found = list(
            compile_r(
                re.sub(r"([][{}()+*^$|\\.?])", r"\\\1", ptext) + "(?![0-9])", perl=True
            ).finditer(s)
        )
        if not found or ks[i] > len(found):
            continue  # not found: may be a result
        start = found[ks[i] - 1].start()
        end = start + len(ptext)
        before = s[max(0, start - 120) : start]
        after = s[end : end + 80]
        # the clause: back to the last boundary, on to the next
        cuts = list(r.boundary.finditer(before))
        if cuts:
            before = before[cuts[-1].end() :]
        cut_a = r.boundary.search(after)
        if cut_a:
            after = after[: cut_a.start()]
        clause = f"{before} {after}"

        # a marker note: unless a test statistic comes right before the p-value, with no
        # other p-value between them ("t52 = 6.92, ***p < .001"), or right after it
        # ("*p < .05 [z = 2.38]")
        if (
            r.marker.search(before)
            or r.ns_marker.search(before)
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
