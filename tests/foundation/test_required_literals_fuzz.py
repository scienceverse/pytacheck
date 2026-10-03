"""I3 (ARCHITECTURE.md §2.3): generated patterns and strings against ``required_literals``.

Hypothesis builds TRE and PCRE patterns from groups, alternations, quantifiers,
word anchors, POSIX classes, lookarounds and inline flags, with literals that
casefold in tricky ways (the Kelvin sign, the long s, the dotted capital I, the
sharp s, the fi ligature, no-break space, ``\\x1c``, ``\\x85``), together with a
string that the pattern is built to match (and often does), and checks, for
every combination of TRE/PCRE and case on/off:

* a string that ``grepl`` matches has, for every clause of the pattern's
  required literals, one of the clause's pieces in ``fold(string)``;
* ``detect_many`` gives what ``grepl`` gives, for every pattern of the list, with
  the pieces looked up by a scan of the text and by the word index alike.

The default budget is fixed (``derandomize``), so a failure is reproducible. For
a nightly run, ``METACHECK_FUZZ_EXAMPLES=20000`` raises it (``derandomize`` is
then off, so every run draws new examples).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metacheck._env import env_names
from metacheck._r import regex as rx
from metacheck._r.regex import detect_many, fold, required_literals

DEFAULT_EXAMPLES = 1500
NIGHTLY = os.environ.get("METACHECK_FUZZ_EXAMPLES")

#: literal words: plain, and characters that fold in tricky ways
WORDS = [
    "fund",
    "grant",
    "Ethics",
    "data",
    "no",
    "a",
    "x",
    "ab",
    "OSF",
    "p-value",
    "e.g.",
    "k",
    "K",  # the Kelvin sign
    "Kelvin",
    "kelvin",
    "ſtart",  # long s
    "start",
    "ß",
    "Straße",
    "strasse",
    "ss",
    "İstanbul",
    "istanbul",
    "ıi",
    "ﬁne",  # the fi ligature
    "fine",
    "café",
    "µ",
    " ",  # no-break space
    "a b",
    "\x1c",
    "a\x1cb",
    "\x85",
    "a\x85b",
    " ",
    ",",
    "1, 2",
    "σ",
    "ς",
    "Σ",
]
#: other characters a string is padded with
FILL = [" ", ".", ",", "\n", "-", " ", "x", "Σ", "K", "ſ", "İ", "ß", "ﬁ", "0", "(", "\x1c"]
#: (what to write in a pattern, a string it matches); classes and escapes
ATOMS = [
    (r"\d", "7"),
    (r"\w+", "word"),
    (r"\s", " "),
    (r"[a-z]+", "abc"),
    (r"[^0-9]", "q"),
    (r"[[:alpha:]]", "k"),
    (r"[[:digit:]]+", "42"),
    (r"[[:space:]]", " "),
    (r"[[:upper:]]", "Q"),
    (r"[Ff]und", "fund"),
    (r"[\w.]", "."),
    (r"\.", "."),
    (r"\-", "-"),
    (r"\(", "("),
    (".", "z"),
    (r"\b", ""),
    (r"\B", ""),
    ("^", ""),
    ("$", ""),
]
TRE_ATOMS = [(r"\<", ""), (r"\>", "")]
PCRE_ATOMS = [(r"\A", ""), (r"\Z", ""), (r"\z", "")]
#: quantifier -> the repeat counts it allows (zero among them: the atom may be absent)
REPEATS = {
    "?": (0, 1),
    "*": (0, 1, 2),
    "+": (1, 2),
    "{2}": (2,),
    "{1,3}": (1, 2, 3),
    "{0,2}": (0, 1, 2),
    "+?": (1, 2),
}
TRICKY_SWAPS = {
    "k": ["K", "K"],
    "s": ["S", "ſ"],
    "i": ["I", "İ", "ı"],
    "ß": ["ẞ", "ss", "SS"],
    "f": ["F", "ﬁ"],
}

counts = {"examples": 0, "patterns_with_clauses": 0, "matches": 0, "matches_with_clauses": 0}


def esc(word: str) -> str:
    """A word as pattern text: the pattern's own metacharacters escaped."""
    return "".join("\\" + c if c in ".^$*+?()[]{}|\\" else c for c in word)


def nodes(perl: bool) -> st.SearchStrategy[tuple[str, str]]:
    """``(pattern source, a string it is built to match)``."""
    atoms = ATOMS + (PCRE_ATOMS if perl else TRE_ATOMS)
    # a word whose last character is quantified: "fundin" + "g?"
    tail = st.tuples(st.sampled_from(WORDS), st.sampled_from(sorted(REPEATS))).flatmap(
        lambda t: st.sampled_from(REPEATS[t[1]]).map(
            lambda count: (esc(t[0]) + t[1], t[0][:-1] + t[0][-1] * count)
        )
    )
    leaf = st.one_of(
        tail,
        st.sampled_from(WORDS).map(lambda w: (esc(w), w)),
        st.sampled_from(WORDS).map(lambda w: (esc(w), w)),
        st.sampled_from(WORDS).map(lambda w: (esc(w), w)),
        st.sampled_from(atoms),
    )

    def extend(children: st.SearchStrategy[tuple[str, str]]) -> st.SearchStrategy[tuple[str, str]]:
        seq = st.lists(children, min_size=2, max_size=3).map(
            lambda ns: ("".join(n[0] for n in ns), "".join(n[1] for n in ns))
        )
        alt = st.lists(children, min_size=2, max_size=3).flatmap(
            lambda ns: st.integers(0, len(ns) - 1).map(
                lambda i: ("|".join(n[0] for n in ns), ns[i][1])
            )
        )
        group = children.flatmap(
            lambda n: st.sampled_from(["({})", "(?:{})", "(?P<g>{})"] if perl else ["({})"]).map(
                lambda f: (f.format(n[0]), n[1])
            )
        )
        quant = st.tuples(children, st.sampled_from(sorted(REPEATS))).flatmap(
            lambda t: st.sampled_from(REPEATS[t[1]]).map(
                lambda count: (_quantified(*t), t[0][1] * count)
            )
        )
        out = [seq, alt, group, quant]
        if perl:
            look = st.tuples(children, st.sampled_from(["(?=", "(?!", "(?<=", "(?<!"]))
            out.append(look.map(lambda t: (t[1] + t[0][0] + ")", "")))
            inline = st.tuples(children, st.sampled_from(["(?i)", "(?i:{})", "(?-i:{})", "(?s)"]))
            out.append(inline.map(lambda t: (_inline(*t), t[0][1])))
        return st.one_of(out)

    return st.recursive(leaf, extend, max_leaves=6)


def _quantified(node: tuple[str, str], q: str) -> str:
    src = node[0]
    atomic = len(src) == 1 or (src.startswith("(") and src.endswith(")"))
    return (src if atomic else f"(?:{src})") + q


def _inline(node: tuple[str, str], flags: str) -> str:
    return flags.format(node[0]) if "{}" in flags else flags + node[0]


@st.composite
def examples(draw: Any) -> tuple[bool, bool, list[str], str]:
    """``(perl, icase, patterns, string)``."""
    perl = draw(st.booleans())
    icase = draw(st.booleans())
    built = draw(st.lists(nodes(perl), min_size=1, max_size=4))
    mode = draw(st.sampled_from(["same", "upper", "swap", "swap"]))

    def shown(example: str) -> str:
        if mode == "upper":
            return example.upper()
        if mode == "same":
            return example
        return "".join(
            draw(st.sampled_from([c, *TRICKY_SWAPS.get(c.lower(), [c.swapcase()])]))
            for c in example
        )

    pad = st.sampled_from(FILL)
    text = draw(pad) + draw(pad).join(shown(n[1]) for n in built) + draw(pad)
    return perl, icase, [n[0] for n in built], text


def check(perl: bool, icase: bool, patterns: list[str], text: str) -> None:
    """The properties of one example (raises ``AssertionError`` on a violation)."""
    counts["examples"] += 1
    folded = fold(text)
    truth: list[bool | str] = []
    for p in patterns:
        try:
            hit = bool(rx.grepl(p, text, icase, perl))
        except rx.RegexError:
            truth.append("invalid")
            continue
        truth.append(hit)
        cnf = required_literals(p, perl, icase)
        counts["patterns_with_clauses"] += bool(cnf)
        if hit:
            counts["matches"] += 1
            counts["matches_with_clauses"] += bool(cnf)
            for clause in cnf:
                assert any(piece in folded for piece in clause), (
                    f"pattern {p!r} (perl={perl}, icase={icase}) matches {text!r}, "
                    f"but no piece of {clause!r} is in {folded!r}"
                )
    valid = [p for p, t in zip(patterns, truth, strict=True) if t != "invalid"]
    want = [t for t in truth if t != "invalid"]
    for mode in ("auto", "scan", "index"):  # the scan and the word index answer alike
        assert detect_many(valid, text, icase, perl, literals=mode) == want, (
            patterns,
            text,
            perl,
            icase,
            mode,
        )
        # a clause-less pattern in the list, and the text folded once for all
        assert detect_many(["", *valid], text, icase, perl, literals=mode) == [True, *want]


def run(max_examples: int) -> dict[str, int]:
    """Run the property over *max_examples* examples; the counts of what it checked."""
    for key in counts:
        counts[key] = 0

    @settings(
        max_examples=max_examples,
        derandomize=NIGHTLY is None,
        database=None,
        deadline=None,
        suppress_health_check=list(HealthCheck),
    )
    @given(examples())
    def prop(example: tuple[bool, bool, list[str], str]) -> None:
        check(*example)

    prop()
    return dict(counts)


@pytest.fixture(autouse=True)
def _literals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in env_names("LITERALS"):
        monkeypatch.delenv(name, raising=False)


def test_i3_generated_patterns_keep_their_literals() -> None:
    budget = int(NIGHTLY) if NIGHTLY else DEFAULT_EXAMPLES
    got = run(budget)
    # each example has 1-4 patterns; the floors say the property was exercised
    assert got["examples"] >= budget // 2
    assert got["matches"] >= budget // 6, got
    assert got["matches_with_clauses"] >= budget // 12, got


def test_the_tricky_strings_alone() -> None:
    """The listed characters against literals that fold to ASCII, in every mode."""
    texts = [
        "KELVIN",  # the Kelvin sign
        "ſtart ſTART",
        "STRASSE Straße STRAẞE",
        "İSTANBUL ıi İi",
        "ﬁne ﬁ",
        "a b a\x1cb a\x85b",
        "SİS ſis KİTE",
    ]
    patterns = [
        "kelvin",
        "start",
        "strasse",
        "straße",
        "istanbul",
        "fine",
        "a b",
        r"a\sb",
        "sis",
        "kite",
    ]
    for perl in (False, True):
        for icase in (False, True):
            for text in texts:
                check(perl, icase, patterns, text)
