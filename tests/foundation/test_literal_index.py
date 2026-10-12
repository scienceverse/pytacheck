"""The word index of ``detect_many`` (``_r.regex.WordIndex``) against the plain scan.

* A property test: for generated texts (with the characters that fold in tricky ways,
  white space of every kind, control characters, punctuation, digits) and generated
  pieces (substrings of the folded text, near-misses, pieces with punctuation or
  spanning a word boundary), ``piece in WordIndex(fold(text))`` is
  ``piece in fold(text)``.
* ``detect_many`` with the index (and with the scan) gives what ``detector()`` gives
  pattern by pattern, on the built-in triples and on the codebook dictionaries (the
  scale and task patterns), over real paper texts.
* The rule that picks the index (``literals="auto"``), and ``METACHECK_LITERALS=off``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metacheck._env import env_names
from metacheck._r import regex as rx
from metacheck._r.regex import INDEX_MIN_PATTERNS, WordIndex, detect_many, fold
from tests.foundation.test_required_literals import BUILTIN, builtin_triples

EXAMPLES = int(os.environ.get("METACHECK_FUZZ_EXAMPLES") or 1500)

#: pieces of text: letters that fold in tricky ways, white space of every kind, control
#: characters (``\x1c`` and ``\x85`` are white space to ``str.split``), NUL, punctuation
PARTS = [
    "a",
    "b",
    "ann",
    "plan",
    "ning",
    "planning",
    "ab.cd",
    "ab",
    "cd",
    "x",
    "K",  # the Kelvin sign
    "k",
    "ſ",  # long s
    "s",
    "İ",  # dotted capital I
    "ı",
    "i",
    "I",
    "ß",
    "ss",
    "ﬁ",  # the fi ligature
    "fi",
    "é",
    "Σ",
    "ς",
    " ",
    " ",  # no-break space
    " ",  #  (a space of the Unicode 'Zs' kind)
    " ",
    "\t",
    "\n",
    "\x1c",
    "\x1f",
    "\x85",
    "\x00",
    "\x7f",
    "​",  # zero-width space: not white space to str.split
    ".",
    ",",
    "-",
    "(",
    ")",
    "'",
    "_",
    "0",
    "12",
    "3.14",
    "p<.05",
    "e.g.",
    "http://x.org/a?b=c",
]
#: pieces that are not substrings of the text, to make near-misses from
EXTRA = ["zzz", "q", "ab.c", "a.cd", "ann ", " ann", "ning,pl", "\x00", "", " ", "\x1c", "\x85"]


def texts() -> st.SearchStrategy[str]:
    return st.lists(st.sampled_from(PARTS), min_size=0, max_size=40).map("".join)


@st.composite
def cases(draw: Any) -> tuple[str, list[str]]:
    """``(text, pieces)``: pieces cut from the folded text, mutated, or made up."""
    text = draw(texts())
    folded = fold(text)
    out: list[str] = []
    for _ in range(draw(st.integers(1, 8))):
        kind = draw(st.sampled_from(["cut", "cut", "miss", "extra", "punct"]))
        if kind == "extra" or not folded:
            out.append(draw(st.sampled_from(EXTRA)))
            continue
        i = draw(st.integers(0, len(folded) - 1))
        j = draw(st.integers(i + 1, min(len(folded), i + 8)))
        piece = folded[i:j]
        if kind == "miss":  # change one character, or add one
            pos = draw(st.integers(0, len(piece)))
            piece = piece[:pos] + draw(st.sampled_from("az.,-0 \x00\x85")) + piece[pos:]
        elif kind == "punct":  # a piece that crosses a word boundary or holds punctuation
            piece = piece + draw(st.sampled_from(PARTS)) + piece
        out.append(piece)
    return text, out


@pytest.fixture(autouse=True)
def _literals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in env_names("LITERALS"):
        monkeypatch.delenv(name, raising=False)


@settings(
    max_examples=EXAMPLES,
    derandomize=os.environ.get("METACHECK_FUZZ_EXAMPLES") is None,
    database=None,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
@given(cases())
def test_the_index_answers_like_a_scan(case: tuple[str, list[str]]) -> None:
    text, pieces = case
    folded = fold(text)
    index = WordIndex(folded)
    for piece in pieces:
        assert (piece in index) == (piece in folded), (text, piece)
        assert (piece in index) == (piece in folded), (text, piece)  # again: the memo


@pytest.mark.parametrize(
    ("text", "piece", "found"),
    [
        ("planning ahead", "ann", True),  # inside a word
        ("planning ahead", "ing a", True),  # holds a space: looked up in the text
        ("planning ahead", "ngahead", False),
        ("see ab.cd here", "ab.cd", True),  # punctuation
        ("see ab.cd here", "b.c", True),
        ("see ab. cd here", "ab.cd", False),
        ("a,b c", "a,b", True),
        ("nul\x00nul", "lnu", False),  # the vocabulary's own separator
        ("nul\x00nul", "nul\x00nul", True),  # not a printable piece: looked up in the text
        ("x\x85y x\x1cz", "x\x85y", True),  # str.split white space, looked up in the text
        ("x\x85y", "xy", False),
        ("​ab", "​ab", True),
        ("abc", "", True),
        ("", "", True),
        ("", "abc", False),
    ],
)
def test_the_index_examples(text: str, piece: str, found: bool) -> None:
    assert (piece in WordIndex(fold(text))) is found
    assert (piece in fold(text)) is found


# -- detect_many over real texts ---------------------------------------------------------

PAPERS = [
    "upstream/metacheck/tests/testthat/fixtures/formats/published.pdf.tei.xml",
    "upstream/metacheck/tests/testthat/fixtures/problems/203020.xml",
]


def _texts() -> Iterator[tuple[str, str]]:
    """The demo paper's text, two more papers' texts, and some of the demo's sentences."""
    import warnings

    import metacheck as pc
    from metacheck.modules import _codebook as C
    from parity import accuracy as A

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        demo = pc.demopaper()
        yield "demo", C._paper_hay(demo)
        sentences = [str(s) for s in demo.text["text"].tolist()]
        for k in range(0, len(sentences), max(1, len(sentences) // 40)):
            yield f"demo sentence {k}", sentences[k]
        for rel in PAPERS:
            yield rel.rsplit("/", 1)[-1], C._paper_hay(pc.read(A.ROOT / rel))


@pytest.fixture(scope="module")
def paper_texts() -> list[tuple[str, str]]:
    return list(_texts())


def _same_as_the_detector(pats: list[str], text: str, icase: bool, perl: bool, label: str) -> int:
    want = [rx.detector(p, icase, perl, False)(text) for p in pats]
    for mode in ("scan", "index", "auto"):
        got = detect_many(pats, text, icase, perl, literals=mode)
        assert got == want, (
            label,
            mode,
            [p for p, g, w in zip(pats, got, want, strict=True) if g != w][:5],
        )
    return sum(want)


def test_the_index_gives_the_detectors_answers_on_the_builtin_triples(
    paper_texts: list[tuple[str, str]],
) -> None:
    assert BUILTIN.exists()
    triples = builtin_triples()
    hits = 0
    for icase, perl in {(i, p) for _, i, p in triples}:
        pats = [p for p, i, pe in triples if (i, pe) == (icase, perl)]
        for label, text in paper_texts:
            hits += _same_as_the_detector(pats, text, icase, perl, label)
    assert hits > 100  # the comparison saw matches, not only misses


def test_the_index_gives_the_detectors_answers_on_the_codebook_dictionaries(
    paper_texts: list[tuple[str, str]],
) -> None:
    from metacheck.modules import _codebook as C

    pats = C._dict_patterns(C._scale_dictionary()) + C._dict_patterns(C._task_dictionary())
    assert len(pats) > 1000
    hits = 0
    for label, text in paper_texts:
        if len(text) > 5000 or label == "demo":  # the papers and the demo, not the sentences
            hits += _same_as_the_detector(pats, text, True, True, label)
    assert hits > 0


# -- which lookup, and the switch --------------------------------------------------------


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The texts a ``WordIndex`` was built for during the test."""
    made: list[str] = []
    init = WordIndex.__init__

    def noted(self: WordIndex, folded: str) -> None:
        made.append(folded)
        init(self, folded)

    monkeypatch.setattr(WordIndex, "__init__", noted)
    return made


def test_auto_builds_the_index_from_the_threshold_on(built: list[str]) -> None:
    pats = [f"word{i}x" for i in range(INDEX_MIN_PATTERNS)]
    assert detect_many(pats[:-1], "some words") == [False] * (len(pats) - 1)
    assert built == []
    assert detect_many(pats, "some words") == [False] * len(pats)
    assert built == ["some words"]


def test_the_lookup_can_be_chosen(built: list[str]) -> None:
    assert detect_many(["words"], "some words", literals="scan") == [True]
    assert built == []
    assert detect_many(["words"], "some words", literals="index") == [True]
    assert built == ["some words"]
    with pytest.raises(ValueError, match="literals"):
        detect_many(["words"], "some words", literals="trie")


def test_the_switch_turns_the_index_off_too(
    built: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METACHECK_LITERALS", "off")
    pats = [f"word{i}x" for i in range(INDEX_MIN_PATTERNS)]
    assert detect_many(pats, "some words", literals="index") == [False] * len(pats)
    assert built == []  # no filter, no lookup, no index
