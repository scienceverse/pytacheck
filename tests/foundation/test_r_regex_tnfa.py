"""TRE's own matcher (:mod:`pytacheck._r._tnfa`) and when :mod:`pytacheck._r.regex` uses it.

The expected values are R's (R 4.5, ``C.UTF-8``); ``test_r_regex_live.py``
and the ``rcompat_regex`` parity cases compare many more against R.
"""

from __future__ import annotations

import pytest
import regex

from pytacheck._r import _tnfa
from pytacheck._r import regex as rx

# ---------------------------------------------------------------------------
# results only TRE's matcher gives (R's values)
# ---------------------------------------------------------------------------


def test_minimal_repetitions_follow_tre_tags() -> None:
    """F09: a group ending in a minimal repetition ends as early as it can."""
    coi = r"(^.*The author.+no.*competing.+interest.*?\.) [A-Z].*$"
    text = (
        "The authors declare no competing interests. The authors have no competing "
        "interest. Foo bar. Baz."
    )
    assert rx.sub(coi, r"\1", text) == "The authors declare no competing interests."
    assert rx.sub("(a*?)(a*)", r"[\1|\2]", "aaa") == "[|aaa]"
    assert rx.sub("(.*)b(.*?)", r"[\1|\2]", "abab") == "[a|]ab"
    assert rx.sub(r"""^.*?["']([^"']+)["'].*$""", r"\1", "read.csv('a.csv') and 'b'") == "b"


def test_merged_states() -> None:
    """``tre_expand_ast()`` gives a copy of ``[.-]`` and the second ``\\s``
    one position, so the pattern matches ``"- "``."""
    assert rx.regextract_all(r"(-*|([.-]{1,2})+\s\s)?", "- ") == ["- "]
    assert _tnfa.merges_states(r"(-*|([.-]{1,2})+\s\s)?")


def test_alternative_matches_empty_only_through_its_first_branch() -> None:
    """``(^|\\W?)`` matches the empty string only as ``^``."""
    assert rx.grepl(r"(^|\W?)+1", ["1", "a1", "-1"]) == [True, False, True]


def test_copied_group_can_move_the_match_start() -> None:
    assert rx.regextract_all(r"[[:alpha:]]*([[:punct:]]?){2}(\w{1,2}|$)", ".eb.") == ["eb."]


def test_submatch_of_a_repeated_group() -> None:
    assert rx.regexec(r"(A*)+([ab]+|[^[:alnum:]]ß+)", "AAabb") == ["AAabb", "AA", "abb"]
    assert rx.sub(r"(A*)+([ab]+|[^[:alnum:]]ß+)", r"<\1>", "AAabb") == "<AA>"


def test_wide_mode_copies_of_negated_brackets() -> None:
    assert rx.regextract_all(r"(.[[:space:]]*)(\W?(ß{2}){0,1})+\W{1,2}", "-a≤aaa") == ["a≤aa"]


# ---------------------------------------------------------------------------
# which patterns run on TRE's matcher
# ---------------------------------------------------------------------------


def _engine(pattern: str, x: object = "abc", groups: bool = False, posix: bool = True) -> str:
    c = rx._compile_for(pattern, False, False, False, x, posix=posix, groups=groups)
    return "tnfa" if isinstance(c.rx, rx._TnfaPattern) else "regex"


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bp\s*[<>=]\s*0?\.[0-9]+",
        r"(conflicts?|competing) (of )?interests?",
        r"^\s+|\s+$",
        r"[[:alpha:]]+",
        r"(\d{1,3}(,\d{3})*)?%",
        r"https?://[^ ]+",
        r"\W{2}",  # ASCII input: byte mode, as translated
    ],
)
def test_common_patterns_stay_on_the_regex_engine(pattern: str) -> None:
    assert _engine(pattern) == "regex"


@pytest.mark.parametrize(
    ("pattern", "x", "groups", "posix"),
    [
        (r"a.*?b", "ab", False, True),  # minimal repetition
        (r"(^|\W?)+1", "1", False, False),  # also for grepl
        (r"(-*|([.-]{1,2})+\s\s)?", "- ", False, False),
        (r"[[:alpha:]]*([[:punct:]]?){2}", "ab", False, True),
        (r"(ab)+", "ab", True, True),  # submatches used
        (r"\W{2}", "é", False, True),  # wide mode
    ],
)
def test_patterns_tre_matches_its_own_way_use_its_matcher(
    pattern: str, x: str, groups: bool, posix: bool
) -> None:
    assert _engine(pattern, x, groups, posix) == "tnfa"


def test_minimal_repetitions_detection_uses_the_translation() -> None:
    """grepl only asks whether there is a match, which the translation answers."""
    assert _engine(r"a.*?b", posix=False) == "regex"
    assert _engine(r"(ab)+", groups=False) == "regex"


def test_back_references_use_backtracking() -> None:
    assert _engine(r"(a*?)\1") == "regex"
    assert not _tnfa.merges_states(r"(a)\1")


def test_compile_r_returns_a_regex_pattern() -> None:
    """Callers of compile_r get a `regex` pattern even for minimal repetitions."""
    assert isinstance(rx.compile_r(r"(a*?)(a*)"), regex.Pattern)
