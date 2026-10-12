"""Random papers and search chains, run on the frozen ``text_search()`` and on the core.

The papers are small and made to hit the hazards of a grouped chain, whose later
calls match the joined, cleaned paragraph text:

* a phrase that only exists across a sentence boundary of a paragraph;
* " , , " runs and doubled spaces and newlines, which a second cleaning changes again;
* NA text inside a paragraph (pasted as "NA"), section ids without a section row;
* paragraphs whose sentences interleave (first-appearance order of the join);
* exact duplicate rows and reference sections;
* pattern lists, whose tables are pattern-major, at every stage and level.

A paper keeps its JSON records or is materialised; an input is one paper or a
paper list with distinct ids. A chain is 1-4 calls of (patterns, return mode,
``search_header``, ``exclude``, ``include_refs``), run as
``text_search(text_search(paper, ...), ...)`` with ``tests/_legacy/search.py``,
with the façade, and as one :class:`~metacheck.core.hits.Hits` chain.
"""

from __future__ import annotations

import random
from typing import Any

import pandas as pd

from metacheck.core.doc import Docs
from metacheck.core.hits import Hits
from metacheck.core.patterns import Pat, PatternSet, patterns
from metacheck.papers.model import Paper, PaperList
from metacheck.text.search import text_search
from tests._legacy.search import text_search as legacy_text_search

WORDS = [
    "power",
    "analysis",
    "effect",
    "size",
    "sample",
    "the",
    "interest",
    "COI",
    "conflict",
    "financial",
    "disclosure",
    "a",
    "priori",
    "12",
    "0.05",
    "2019",
    ",",
    ", ,",
    " ",
    "\n",
    "  ",
    "were",
    "Power",
]
PATTERNS = [
    "power",
    r"\bpower(ed|s)?\b",
    "analysis power",  # across a sentence boundary only when joined
    "size the",
    r"\b(?!\d{4}\b)\d+(?:[.,]\d+)?\b",
    "interest",
    r"\bCOI\b",
    "conflict",
    "financial disclosure",
    ",,",  # only after a second cleaning of ", ,"
    ", ,",
    "NA",  # the pasted NA of a missing sentence
    "Methods",  # a header
    ".*",
    "zzz",
]
PERL = {r"\b(?!\d{4}\b)\d+(?:[.,]\d+)?\b"}
LEVELS = ["sentence", "paragraph", "section", "header", "paper_id"]
HEADERS = ["Intro", "Methods", "Results", None, "Methods"]
TYPES = ["intro", "method", "results", "references", None]

Chain = list[dict[str, Any]]


def _sentence(rng: random.Random) -> str | None:
    if rng.random() < 0.05:
        return None
    s = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6)))
    if rng.random() < 0.15:
        s = s + " analysis"
    if rng.random() < 0.15:
        s = "power " + s
    return s


def make_paper(rng: random.Random, pid: str, materialise: bool) -> Paper:
    """A random paper; *materialise* turns its tables into DataFrames."""
    sections = []
    texts: list[dict[str, Any]] = []
    tid = 0
    for sid in range(1, rng.randint(1, 4) + 1):
        if rng.random() > 0.1:  # sometimes no section row (header and type NA)
            sections.append(
                {
                    "section_id": sid,
                    "header": rng.choice(HEADERS),
                    "section_type": rng.choice(TYPES),
                }
            )
        rows = []
        for pg in range(1, rng.randint(1, 3) + 1):
            rows.extend((pg, _sentence(rng)) for _ in range(rng.randint(1, 4)))
        if rng.random() < 0.3:
            rng.shuffle(rows)  # interleaved paragraphs
        for pg, t in rows:
            tid += 1
            texts.append({"text": t, "text_id": tid, "section_id": sid, "paragraph_id": pg})
            if rng.random() < 0.05:  # an exact duplicate row
                texts.append(dict(texts[-1]))
    p = Paper(paper_id=pid)
    p._set_raw("text", texts, ["text", "text_id", "section_id", "paragraph_id"])
    p._set_raw("section", sections, ["section_id", "header", "section_type"])
    if materialise:
        p.get("text")
        p.get("section")
    return p


def make_input(rng: random.Random, n: int) -> Paper | PaperList:
    """A paper (60%) or a paper list of two or three papers."""
    if rng.random() < 0.6:
        return make_paper(rng, f"p{n}", rng.random() < 0.3)
    k = rng.randint(2, 3)
    return PaperList([make_paper(rng, f"p{n}_{j}", rng.random() < 0.3) for j in range(k)])


def make_chain(rng: random.Random) -> Chain:
    """1-4 calls; ``exclude`` only after the first."""
    chain = []
    for step in range(rng.randint(1, 4)):
        pats = rng.sample(PATTERNS, rng.choice([1, 1, 1, 2, 3]))
        ret = rng.choice(LEVELS)
        exclude = step > 0 and rng.random() < 0.2
        if exclude and len(pats) > 1 and ret != "sentence":
            pats = pats[:1]  # Hits.group refuses a grouped mode after excluding a list
        if exclude and len(pats) > 2:
            pats = pats[:2]  # R's dplyr::intersect() takes two tables, a third is an error
        chain.append(
            {
                "patterns": pats,
                "perl": all(p in PERL for p in pats),
                "return_": ret,
                "exclude": exclude,
                "search_header": rng.random() < 0.3,
                "include_refs": rng.random() < 0.15,
            }
        )
    return chain


def _run_calls(search: Any, x: Any, chain: Chain) -> Any:
    for c in chain:
        x = search(
            x,
            c["patterns"] if len(c["patterns"]) > 1 else c["patterns"][0],
            return_=c["return_"],
            perl=c["perl"],
            exclude=c["exclude"],
            search_header=c["search_header"],
            include_refs=c["include_refs"],
        )
    return x


def run_legacy(x: Any, chain: Chain) -> Any:
    """The chain on the frozen ``text_search()``."""
    return _run_calls(legacy_text_search, x, chain)


def run_facade(x: Any, chain: Chain) -> Any:
    """The chain on today's ``text_search()``."""
    return _run_calls(text_search, x, chain)


def run_hits(x: Any, chain: Chain) -> pd.DataFrame:
    """The chain as one ``Hits`` chain, and its table."""
    h: Hits | None = None
    for c in chain:
        dialect = "pcre" if c["perl"] else "tre"
        ps: Pat | PatternSet = (
            Pat(c["patterns"][0], dialect)
            if len(c["patterns"]) == 1
            else patterns(*c["patterns"], dialect=dialect)
        )
        kw = {"header": c["search_header"], "include_refs": c["include_refs"]}
        if h is None:
            h = Docs.of(x).hits(ps, **kw)
        elif c["exclude"]:
            h = h.without(ps, **kw)
        else:
            h = h.where(ps, **kw)
        if c["return_"] != "sentence":
            h = h.group(c["return_"])
    assert h is not None
    return h.table()


def outcome(run: Any, x: Any, chain: Chain) -> Any:
    """The chain's table, or the name of the error it raised."""
    try:
        return run(x, chain)
    except Exception as exc:  # the legacy chain errs: the others must too
        return f"error {type(exc).__name__}"


def difference(a: Any, b: Any) -> str | None:
    """How table (or error) *b* differs from *a*: columns, dtypes, values, order."""
    if isinstance(a, str) or isinstance(b, str):
        return None if isinstance(a, str) and isinstance(b, str) else f"{a!s:.100} | {b!s:.200}"
    if list(a.columns) != list(b.columns):
        return f"columns {list(a.columns)} vs {list(b.columns)}"
    if [str(t) for t in a.dtypes] != [str(t) for t in b.dtypes]:
        return f"dtypes {[str(t) for t in a.dtypes]} vs {[str(t) for t in b.dtypes]}"
    try:
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    except AssertionError as exc:
        return str(exc)[:300]
    return None


def differences(n: int, seed: int, *, facade: bool = True) -> list[dict[str, Any]]:
    """The chains of *n* random cases (from *seed*) whose core result differs from the
    frozen one; *facade* also compares today's ``text_search()``."""
    rng = random.Random(seed)
    fails = []
    for k in range(n):
        x = make_input(rng, k)
        chain = make_chain(rng)
        want = outcome(run_legacy, x, chain)
        runs = [("hits", run_hits)] + ([("facade", run_facade)] if facade else [])
        for name, run in runs:
            diff = difference(want, outcome(run, x, chain))
            if diff is not None:
                fails.append({"case": k, "by": name, "chain": chain, "diff": diff})
    return fails
