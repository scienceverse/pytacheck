"""I1 (ARCHITECTURE.md §2.3): the Doc finds exactly what the regex finds.

For every built-in ``(pattern, icase, perl)`` triple (``tests/foundation/data/
builtin_patterns.json``) and a few case-sensitive probes, on the accuracy
matrix's papers and the demo paper, a Doc's mask equals the compiled pattern's
``search()`` row by row: on the raw text, the text cleaned once and twice, and
the headers. The Doc skips a row whose folded text lacks a required literal, so
this pins that the literal prefilter never drops a match.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

import metacheck as pc
from metacheck._r import regex as rx
from metacheck.core.doc import Doc
from metacheck.core.patterns import Pat
from parity import accuracy as A
from tests.foundation.test_required_literals import builtin_triples

PROBES = [
    ("Figure", False, False),
    ("OSF", False, False),
    (r"Table \d", False, False),
    (r"\bR\b", False, False),
    ("P", False, True),
]
INPUTS = [*dict.fromkeys(o.input for o in A.load_matrix() if o.kind == "paper"), "demopaper()"]


@pytest.fixture(autouse=True)
def _literals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck._env import env_names

    for name in env_names("LITERALS"):
        monkeypatch.delenv(name, raising=False)


def _paper(name: str) -> pc.Paper:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        paper = pc.demopaper() if name == "demopaper()" else pc.read(A.ROOT / name)
    assert isinstance(paper, pc.Paper)
    return paper


def _want(src: str, icase: bool, perl: bool, texts: list[str | None]) -> int:
    search = rx.compile_r(src, icase, perl, posix=False).search
    out = 0
    for i, s in enumerate(texts):
        if s is not None and search(s) is not None:
            out |= 1 << i
    return out


@pytest.mark.parametrize("name", INPUTS, ids=lambda n: Path(n).name)
def test_i1_doc_masks_are_the_regex_matches(name: str) -> None:
    d = Doc.of(_paper(name))
    triples = list(dict.fromkeys([*builtin_triples(), *PROBES]))
    fields = {stage: d.field(stage) for stage in (0, 1, 2)}
    mismatches = []
    for src, icase, perl in triples:
        pat = Pat.from_r(src, perl=perl, ignore_case=icase)
        for stage, texts in fields.items():
            if d.mask(pat, stage, d.all) != _want(src, icase, perl, texts):
                mismatches.append((src, icase, perl, stage))
        if d.header_mask(pat, d.all) != _want(src, icase, perl, d.header):
            mismatches.append((src, icase, perl, "header"))
    assert not mismatches, mismatches[:10]
