"""Unit tests for R's regex functions (parity/cases/rcompat.yaml checks them against R)."""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pytacheck._r import regex as rx


def test_tre_is_leftmost_longest() -> None:
    assert rx.regextract("a|ab", "ab") == "ab"
    assert rx.regextract("a|ab", "ab", perl=True) == "a"


def test_tre_bracket_backslash_is_literal() -> None:
    assert rx.grepl(r"^[\d]$", "d")
    assert not rx.grepl(r"^[\d]$", "5")
    assert rx.grepl(r"^[\d]$", "5", perl=True)


def test_pcre_word_is_ascii() -> None:
    assert rx.grepl(r"^\w$", "é")
    assert not rx.grepl(r"^\w$", "é", perl=True)


def test_vectorisation_keeps_series_index() -> None:
    s = pd.Series(["a1", None, "b"], index=[10, 20, 30])
    out = rx.grepl(r"\d", s)
    assert list(out.index) == [10, 20, 30]
    assert list(out) == [True, False, False]
    assert rx.gsub("a", "b", s).tolist() == ["b1", None, "b"]


@given(st.text(max_size=30))
def test_fixed_gsub_matches_str_replace(s: str) -> None:
    assert rx.gsub(".", "!", s, fixed=True) == s.replace(".", "!")


def test_strsplit_matches_r_examples() -> None:
    # R: strsplit(c(",", ",,", "a,,", ",a"), ",")
    assert rx.strsplit([",", ",,", "a,,", ",a"], ",") == [[""], ["", ""], ["a", ""], ["", "a"]]
    assert rx.strsplit("aaa", "^a") == ["", "", ""]


def test_prefilter_never_changes_grepl() -> None:
    """grepl with the literal prefilter must equal a plain engine scan."""
    import pytacheck as pc
    from pytacheck._r.regex import _prefilter, compile_r

    texts = [
        *pc.paper_table(pc.demopaper(), "text")["text"].tolist(),
        "Marginally SIGNIFICANT",
        "ſignificant",
        "Close to significance",
        "<pre>",
        "osf.io/abc",
        "a borderline sig",
        "TREND towards significance",
        "ÉCOLE",
        "Thİs study, NıH",
        "",
    ]
    patterns = [
        r"margin\w* (?:\w+\s+){0,5}significan\w*|trend\w* (?:\w+\s+){0,1}significan\w*",
        r"significan|signif",
        r"\<pre",
        r"osf\.io|github",
        r"close to|border",
        r"écol|ecol",
        r"(?i)TREND|margin",
        r"data|code|material",
        r"this study|NIH grant|nih",
    ]
    for pat in patterns:
        for icase in (False, True):
            for perl in (False, True):
                if perl and pat == r"\<pre":
                    continue
                rx = compile_r(pat, icase, perl, False, posix=False)
                expected = [rx.search(t) is not None for t in texts]
                assert rx_grepl(pat, texts, icase, perl) == expected, (
                    pat,
                    icase,
                    perl,
                    _prefilter(pat, icase),
                )


def rx_grepl(pattern: str, texts: list[str], icase: bool, perl: bool) -> list[bool]:
    return rx.grepl(pattern, texts, ignore_case=icase, perl=perl)


def test_gregexpr_and_gsub_empty_matches_follow_r() -> None:
    # R 4.5: gregexpr("x*", c("", "ab", "abx")) and the same with perl = TRUE
    assert rx.gregexpr_all("x*", ["", "ab", "abx"]) == [
        [],
        [(1, 0), (2, 0)],
        [(1, 0), (2, 0), (3, 1)],
    ]
    assert rx.gregexpr_all("x*", ["", "ab"], perl=True) == [[(1, 0)], [(1, 0), (2, 0)]]
    assert rx.gregexpr_all("b|$", "ab") == [(2, 1)]
    # gsub("x*", "-", "abxd") is "-a-b-d-" (Python's re.sub gives "-a-b--d-")
    assert rx.gsub("x*", "-", ["abxd", ""]) == ["-a-b-d-", "-"]
    assert rx.gsub("x*", "-", "abxd", perl=True) == "-a-b-d-"
    # a match at the very end is found when the search starts before it
    assert rx.gregexpr_all("$", ["abc", ""]) == [[(4, 0)], []]
    assert rx.gregexpr_all("$", "", perl=True) == [(1, 0)]
    assert rx.regextract_all("[0-9]*$", "abc") == [""]
    assert rx.gsub("[0-9]*$", "-", "abc") == "abc-"
    # after an empty match R steps over one character, also where the pattern
    # could match non-empty there
    assert rx.regextract_all("a*?", "aaa") == ["", "", ""]
    assert rx.regextract_all("a|", "caac", perl=True) == ["", "a", "a", ""]
    assert rx.gsub("a*?", "-", "aaa") == "-a-a-a-"
    assert rx.gsub("(|a)", "-", "caac", perl=True) == "-c-a-a-c-"
    assert rx.gsub("a|", "-", "caac", perl=True) == "-c--c-"


def test_pcre_inline_flags_and_ascii_sets() -> None:
    # (?i) in the middle of a pattern holds to the end of its group, as in PCRE2
    assert not rx.grepl("G(?i)rant [Ss]upport", "grant support", perl=True)
    assert rx.grepl("G(?i)rant [Ss]upport", "GRANT SUPPORT", perl=True)
    # POSIX classes and \w in a PCRE set are ASCII, also ignoring case
    assert not rx.grepl(r"^[[:alpha:]\w]+$", "naïve", ignore_case=True, perl=True)
    assert rx.grepl(r"^[[:alpha:]]+$", "naïve", ignore_case=True)
    # an escaped backslash in a set is a backslash (R: "aXX")
    assert rx.gsub(r"[\\w]", "X", "a\\w", perl=True) == "aXX"


def test_lazy_tre_patterns_stay_lazy() -> None:
    assert (
        rx.sub("^JaspColumn_.*?_Encoded_", "", "JaspColumn_1_Encoded_x_Encoded_y") == "x_Encoded_y"
    )
    assert rx.regextract_all("<v>.*?</v>", "<v>a</v> x <v>b</v>") == ["<v>a</v>", "<v>b</v>"]
    # an escaped or bracketed "*?" is no lazy quantifier: leftmost-longest (R: "ab*")
    assert rx.regextract(r"(a|ab)\*?", "ab*") == "ab*"
    assert rx.regextract(r"(a|ab)x*?", "ab") == "a"


def test_empty_matches_of_empty_groups_and_anchors() -> None:
    # R 4.5: an empty group or a PCRE anchor matches empty, so R's gsub loop applies
    assert rx.gsub("b|()", "-", "abc") == "-a-c-"
    assert rx.gsub("b|()", "-", "abc", perl=True) == "-a-c-"
    assert rx.gsub(r"a\Z|\Z", "-", "ba", perl=True) == "b-"
    assert rx.gsub(r"a\z|\z", "-", "ba", perl=True) == "b-"
    assert rx.gsub(r"\Aa|\A", "-", "ab", perl=True) == "-b"


def test_hex_quoted_and_vertical_space_escapes() -> None:
    # R 4.5: \x{hhhh} in both engines, \Q...\E and \v with perl = TRUE
    assert rx.grepl(r"a\x{2212}b", ["a\u2212b", "a-b"]) == [True, False]
    assert rx.grepl(r"[\x{41}-\x{43}]", ["B", "D"], perl=True) == [True, False]
    assert rx.gsub(r"\x{2212}", "-", "\u22125", perl=True) == "-5"
    assert rx.grepl(r"\\x{41}", ["\\x{41}", "A"], perl=True) == [False, False]
    assert rx.grepl(r"x\Q(y)*\Ez", ["x(y)*z", "xyz"], perl=True) == [True, False]
    assert rx.grepl(r"\QA.B", ["A.B", "AxB"], perl=True) == [True, False]
    assert rx.grepl(r"\v", ["\v", "\n", "v", "\u2028"], perl=True) == [True, True, False, True]
    assert rx.grepl(r"^\V+$", ["ab", "a\nb"], perl=True) == [True, False]
    assert rx.grepl(r"[\v]", ["\n", "v"], perl=True) == [True, False]


def test_tre_classes_and_invalid_patterns() -> None:
    assert rx.grepl(r"a\sb", "a b") and not rx.grepl(r"a\sb", "a b")  # glibc iswspace
    assert rx.grepl(r"\d", "5") and not rx.grepl(r"\d", "٥")  # [0-9] only
    with pytest.raises(rx.RegexError):
        rx.grepl("(a", "a")
    with pytest.raises(rx.RegexError):  # R: "Unknown collating element"
        rx.grepl("[[.hyphen.]]", "-")
