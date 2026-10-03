"""The façades on the Doc against their frozen versions: 0 differences.

``text_search()``, ``extract_eq()``, ``extract_urls()``, ``extract_p_values()``,
``text_expand()``, ``sentence_table()`` and the one-paper ``paper_table()`` run
on papers (JSON records, DataFrames, and metacheck's TEI conversion), paper
lists, tables (chained results and odd tables) and strings, over many patterns,
pattern lists and every flag and ``return`` mode. Each result is compared with
the one of ``tests/_legacy/`` (the code before the indexed-document core):
columns, dtypes, index, values and order, or the error's type and message.

A paper read from JSON records is read twice, once per side: the frozen code
builds the paper's tables, while the façades must leave them as records.
"""

from __future__ import annotations

import random
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.modules import ethics_check as E
from metacheck.modules import funding_check_oi as F
from metacheck.modules import open_practices as O
from metacheck.papers.model import PaperList
from metacheck.papers.tables import paper_table
from metacheck.text import expand as expand_module
from metacheck.text import extract as X
from metacheck.text.expand import text_expand
from metacheck.text.search import sentence_table, text_search
from parity.cases import metacheck_defaults
from tests._legacy import extract as LX
from tests._legacy import search as LS
from tests._legacy import tables as LT

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures"
INPUTS = [
    ROOT / "upstream/metacheck/inst/demos/to_err_is_human.xml",
    FIXTURES / "formats/published.pdf.tei.xml",
    FIXTURES / "formats/apa.xml",
    FIXTURES / "problems/203020.json",
    FIXTURES / "problems/0956797617737129.xml",
    FIXTURES / "debruine/debruine-fret.xml",
    FIXTURES / "debruine/debruine-sex.xml",
    FIXTURES / "psychsci/0956797614527830.json",
    FIXTURES / "bibr12/PMC4383902.json",
    FIXTURES / "bibr12/probe_docx.json",
]
FORMS = ["raw", "materialised", "metacheck_defaults"]
PATTERNS: list[Any] = [
    ".*",
    "ethic",
    "the",
    "data",
    r"\bR\b",
    "zzzqqq",
    "significan",
    r"p\s*[<=>]",
    "approv|ethic",
    "[",
    "(a",
    "interest",
    r"\d+",
    "osf\\.io",
    "\\bsee\\b",
]
LISTS: list[Any] = [
    ["ethic", "approv"],
    ["zzz", "yyy"],
    ["data", "code", "the"],
    ["the", "the"],
    list(E._ETHICS_WORDS),
    list(X._LIVE_WORDS),
    list(O._REPO_WORDS),
    list(O._AVAILABILITY),
    list(F.PATTERN_FUND),
    list(F.PATTERN_STUDY),
    ["ethic", "[bad"],
    ["[bad", "ethic"],
    [r"\binterests?\b", r"\bCOI\b"],
    list(X._OPERATORS),
]
RETURNS = ["sentence", "paragraph", "section", "header", "match", "paper_id"]
FLAGS: list[dict[str, Any]] = [
    {"exclude": True},
    {"search_header": True},
    {"include_refs": True},
    {"ignore_case": False},
    {"fixed": True},
    {"perl": True},
    {"exclude": True, "return_": "paragraph"},
    {"search_header": True, "return_": "section"},
]


@pytest.fixture(autouse=True)
def _quiet() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _read(path: Path, form: str) -> pc.Paper:
    if form == "metacheck_defaults":
        with metacheck_defaults():
            paper = pc.read(path)
    else:
        paper = pc.read(path)
        if form == "materialised":
            _ = paper.text, paper.section
    assert isinstance(paper, pc.Paper)
    return paper


def _run(fn: Callable[..., Any], *a: Any, **k: Any) -> tuple[str, Any]:
    try:
        return ("ok", fn(*a, **k))
    except Exception as exc:
        return ("err", f"{type(exc).__name__}: {exc}")


def _same(a: tuple[str, Any], b: tuple[str, Any]) -> bool:
    if a[0] != b[0]:
        return False
    if a[0] == "err":
        return bool(a[1] == b[1])
    x, y = a[1], b[1]
    if isinstance(x, tuple) and isinstance(y, tuple):  # sentence_table(): (table, is_vector)
        return len(x) == len(y) and all(
            _same(("ok", u), ("ok", v)) for u, v in zip(x, y, strict=True)
        )
    if isinstance(x, pd.DataFrame) or isinstance(y, pd.DataFrame):
        if not (isinstance(x, pd.DataFrame) and isinstance(y, pd.DataFrame)):
            return False
        if list(x.columns) != list(y.columns) or list(x.dtypes) != list(y.dtypes):
            return False
        try:
            pd.testing.assert_frame_equal(x, y, check_exact=True)
        except AssertionError:
            return False
        return True
    return bool(x == y)


class Cases:
    """Runs each case on both sides and keeps the ones that differ."""

    def __init__(self) -> None:
        self.n = 0
        self.failures: list[tuple[str, Any, Any, Any, Any]] = []

    def check(
        self,
        label: str,
        new: Callable[..., Any],
        old: Callable[..., Any],
        x_new: Any,
        x_old: Any,
        *a: Any,
        **k: Any,
    ) -> None:
        self.n += 1
        r_new = _run(new, x_new, *a, **k)
        r_old = _run(old, x_old, *a, **k)
        if not _same(r_old, r_new):
            self.failures.append(
                (
                    label,
                    a,
                    k,
                    r_old if r_old[0] == "err" else "table",
                    r_new if r_new[0] == "err" else "table",
                )
            )

    def search(self, label: str, x_new: Any, x_old: Any, *a: Any, **k: Any) -> None:
        self.check(label, text_search, LS.text_search, x_new, x_old, *a, **k)

    def assert_none(self) -> None:
        assert self.n > 0
        assert not self.failures, f"{len(self.failures)} of {self.n} differ: {self.failures[:5]}"


def _pair(path: Path, form: str) -> tuple[pc.Paper, pc.Paper]:
    """The paper for the façades and the one for the frozen code."""
    new = _read(path, form)
    return new, (_read(path, form) if form == "raw" else new)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("path", INPUTS, ids=lambda p: p.name)
def test_text_search_of_a_paper(path: Path, form: str) -> None:
    if form == "metacheck_defaults" and path.suffix != ".xml":
        pytest.skip("metacheck's TEI conversion reads XML")
    new, old = _pair(path, form)
    raw = new._raw_records("text") is not None
    rng = random.Random(path.name)
    cases = Cases()
    for pat in PATTERNS + LISTS:
        cases.search(path.name, new, old, pat)
    for pat in rng.sample(PATTERNS + LISTS, 6):
        for ret in RETURNS:
            cases.search(path.name, new, old, pat, return_=ret)
    for pat in rng.sample(PATTERNS + LISTS, 5):
        for kw in FLAGS:
            cases.search(path.name, new, old, pat, **kw)
    for fn, legacy in [
        (X.extract_eq, LX.extract_eq),
        (X.extract_urls, LX.extract_urls),
        (X.extract_p_values, LX.extract_p_values),
    ]:
        cases.check(path.name, fn, legacy, new, old)
    cases.check(path.name, sentence_table, LS._text_frame, new, old)
    for name in ("text", "section", "info", "references", "nope"):
        cases.check(path.name, paper_table, LT.paper_table, new, old, name)
    cases.assert_none()
    # the façades read the records without building the paper's tables
    assert (new._raw_records("text") is not None) == raw


def _legacy_expand(*a: Any, **k: Any) -> Any:
    mp = pytest.MonkeyPatch()
    mp.setattr(expand_module, "text_search", LS.text_search)
    try:
        return text_expand(*a, **k)
    finally:
        mp.undo()


@pytest.mark.parametrize("path", INPUTS[:5], ids=lambda p: p.name)
def test_text_expand(path: Path) -> None:
    new, old = _pair(path, "raw")
    cases = Cases()
    for pat in ["the", "data", r"\d+", "zzzqqq"]:
        found = text_search(new, pat)
        for to in ("sentence", "paragraph", "section", "paper"):
            for plus, minus in ((0, 0), (1, 1), (2, 0)):
                cases.check(
                    path.name,
                    lambda r, **k: text_expand(r, new, **k),
                    lambda r, **k: _legacy_expand(r, old, **k),
                    found,
                    found,
                    expand_to=to,
                    plus=plus,
                    minus=minus,
                )
    cases.assert_none()
    assert new._raw_records("text") is not None


def test_text_search_of_a_paper_list() -> None:
    cases = Cases()
    new = PaperList([_read(p, "raw") for p in INPUTS[:6]])
    old = PaperList([_read(p, "raw") for p in INPUTS[:6]])
    for pat in PATTERNS[:8] + LISTS[:6]:
        for ret in RETURNS:
            cases.search("paperlist", new, old, pat, return_=ret)
        cases.search("paperlist-excl", new, old, pat, exclude=True)
    for pat in ["ethic", ["a", "b"]]:
        for ret in RETURNS:
            cases.search("empty-list", PaperList([]), PaperList([]), pat, return_=ret)
    cases.search("plain-list", list(new)[:3], list(old)[:3], "ethic")
    for fn, legacy in [(X.extract_eq, LX.extract_eq), (X.extract_urls, LX.extract_urls)]:
        cases.check("paperlist", fn, legacy, new, old)
    for name in ("text", "section", "info"):
        cases.check("paperlist", paper_table, LT.paper_table, new, old, name)
    cases.search("bad-input", 42, 42, "ethic")
    cases.search("bad-input-multi", 42, 42, ["ethic", "x"])
    cases.search("bad-input-badpat", 42, 42, ["[", "x"])
    cases.assert_none()


@pytest.mark.parametrize("path", INPUTS, ids=lambda p: p.name)
def test_text_search_of_a_table(path: Path) -> None:
    cases = Cases()
    f = LS._text_frame(_read(path, "raw"))[0]
    sub = LS.text_search(f, "the")
    for pat in PATTERNS[:10] + LISTS[:8]:
        cases.search("chain", sub, sub, pat)
        cases.search("chain-excl", sub, sub, pat, exclude=True)
    odd = f.copy()
    odd.index = range(100, 100 + len(odd))
    cases.search("odd-index", odd, odd, ["the", "data"])
    cases.search("odd-index1", odd, odd, "data", return_="paragraph")
    dup = pd.concat([f.head(5), f.head(5)])
    for ret in RETURNS:
        cases.search("dup", dup, dup, ["the", "a"], return_=ret)
        cases.search("dup1", dup, dup, "e", return_=ret)
    for fn, legacy in [(X.extract_eq, LX.extract_eq), (X.extract_urls, LX.extract_urls)]:
        cases.check("table", fn, legacy, f, f)
        cases.check("chain", fn, legacy, sub, sub)
    if len(f):
        notext = f.drop(columns="text")
        cases.search("notext", notext, notext, ["1", "2"])
        cases.search("notext1", notext, notext, "1")
        cases.check("notext", X.extract_eq, LX.extract_eq, notext, notext)
        noheader = f.drop(columns=["header", "section_type"])
        for ret in RETURNS:
            cases.search("nohdr", noheader, noheader, ["the", "data"], return_=ret)
        obj = f.astype({"text": object})
        cases.search("objtext", obj, obj, ["the", "zzz"])
        cases.search("objtext-none", obj, obj, ["zzz", "yyy"])
        cases.search("objtext1", obj, obj, "zzz")
        cases.search("objtext-excl", obj, obj, ["the", "zzz"], exclude=True)
        cases.check("objtext", X.extract_eq, LX.extract_eq, obj, obj)
        na = f.copy()
        na.loc[na.index[:3], "text"] = pd.NA
        cases.search("na", na, na, ["the", "a"], exclude=True)
        cases.search("na2", na, na, ["the", "a"])
        cases.search("na3", na, na, "e", return_="section")
        cases.check("na", X.extract_eq, LX.extract_eq, na, na)
        cases.check("na", X.extract_urls, LX.extract_urls, na, na)
    cases.assert_none()


def test_text_search_of_strings() -> None:
    cases = Cases()
    simple = pd.DataFrame({"text": ["a", "b", "a b", "c"]})
    for pat in ["a", ["a", "b"], ["zz", "yy"], ["a", "b", "c"], ["a", "b", "c", "d"]]:
        for ex in (False, True):
            for ret in RETURNS:
                cases.search("simple", simple, simple, pat, exclude=ex, return_=ret)
    strings = ["ethics approval here", "no", "approv only", "no", "  spaced   , text  "]
    for pat in ["a", ["ethic", "approv"], ["zz", "yy"], ["e", "o", "zz"], "spaced"]:
        for ex in (False, True):
            for ret in RETURNS:
                cases.search("strings", strings, strings, pat, exclude=ex, return_=ret)
                cases.search("string", strings[0], strings[0], pat, exclude=ex, return_=ret)
    eqs = ["t(12) = 2.3, p < .05", "no stats", "r = .5 and d = 0.3", "see https://osf.io/abc"]
    for fn, legacy in [
        (X.extract_eq, LX.extract_eq),
        (X.extract_urls, LX.extract_urls),
        (X.extract_p_values, LX.extract_p_values),
    ]:
        cases.check("strings", fn, legacy, eqs, eqs)
        cases.check("string", fn, legacy, eqs[0], eqs[0])
    cases.search("badret", strings, strings, "a", return_="nope")
    cases.search("emptypat", strings, strings, [])
    cases.assert_none()


@pytest.mark.parametrize("sections", [True, False])
def test_sentence_table_of_a_text_table_that_repeats_its_text_column(sections: bool) -> None:
    """The table is returned as before (no search can use it: that raised, and still does)."""
    p = pc.Paper("p1")
    p.text = pd.DataFrame(
        [["a b p = .04", 1, 1, 1, "a b"], ["c d", 2, 1, 1, "q"]],
        columns=["text", "text_id", "section_id", "paragraph_id", "text"],
    )
    if sections:
        p.section = pd.DataFrame(
            {
                "section_id": pd.array([1], dtype="Int64"),
                "header": pd.array(["H"], dtype="string"),
                "section_type": pd.array(["intro"], dtype="string"),
            }
        )
    new, is_vector = sentence_table(p)
    old, was_vector = LS._text_frame(p)
    assert is_vector == was_vector is False
    assert list(new.columns) == list(old.columns)
    assert new.equals(old)
    with pytest.raises(AttributeError, match="tolist"):
        text_search(p, "a")
    with pytest.raises(AttributeError, match="tolist"):
        LS.text_search(p, "a")
