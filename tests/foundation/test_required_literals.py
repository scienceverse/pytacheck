"""Required literals (``_r.regex.required_literals``) and ``detect_many``.

* Examples pin what the parser reads and what it leaves alone.
* **I2** (ARCHITECTURE.md §2.3): over the recorded regex replay
  (``tests/foundation/data/regex_calls.json.xz``), every string a pattern matches has,
  for each clause, one of the clause's pieces in ``casefold(string)``.
* The built-in coverage ratchet: how many of the built-in ``(pattern, icase, perl)``
  triples get at least one clause. The triples are what the accuracy matrix's paper
  modules give ``detector()`` (``data/builtin_patterns.json``, written by
  ``record_builtin_patterns.py``); the patterns without clauses have no literal of
  three characters (``^[0-9]$``, ``\\bR\\b``, ``η|eta``).
* ``METACHECK_LITERALS=off`` turns the prefilter off.

The generative property test (I3) is ``test_required_literals_fuzz.py``.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from metacheck._env import env_names
from metacheck._r import regex as rx
from metacheck._r.regex import detect_many, fold, required_literals
from tests.foundation.record_regex_calls import DATA, load

BUILTIN = Path(__file__).with_name("data") / "builtin_patterns.json"

#: the built-in triples with at least one clause: a floor that only goes up
# of 271; 229 of 272 while the ethics module's own prefilter pattern (with clauses) was recorded
BUILTIN_COVERED_FLOOR = 228


@pytest.fixture(autouse=True)
def _literals_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in env_names("LITERALS"):
        monkeypatch.delenv(name, raising=False)


# -- examples -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "perl", "cnf"),
    [
        # the examples of the funding module's former literal extractor, as pieces
        ("abc(|s)def", True, (("abc",), ("def",))),
        ("x?yzw", True, (("yzw",),)),
        ("[Ff]unded", True, (("unded",),)),
        ("(?<![Ss]pecialty) [Ff]ellowship", True, (("ellowship",),)),
        ("abc|cde", True, (("abc", "cde"),)),
        ("abc|", True, ()),
        ("NOP(?i)QRS", True, (("nop",), ("qrs",))),
        ("(?:\\s+\\w+){0,3}abc{2,}", True, (("abc",),)),
        # anchors are skipped, short pieces and short branches give nothing
        ("(ab|cd)e\\bfg", True, (("efg",),)),
        ("^\\bfunding\\b$", False, (("funding",),)),
        ("\\<ethic(s|al) (approval|committee)", False, (("ethic",), ("approval", "committee"))),
        ("\\bfunding\\b|grant(s)? from", False, (("funding", "grant"),)),
        ("pre-?regist", False, (("pre",), ("regist",))),
        # white space and commas split pieces; escaped symbols are literal
        ("[[:alpha:]]+ data, are available", False, (("data",), ("are",), ("available",))),
        ("osf\\.io/[a-z0-9]{5}", True, (("osf.io/",),)),
        # escapes with arguments are skipped whole
        ("\\x{2019}abc", False, (("abc",),)),
        ("\\x41bcd", True, (("bcd",),)),
        ("(a)\\123bcd", True, (("bcd",),)),
        ("\\N{LATIN SMALL LETTER A}bcd", True, (("bcd",),)),
        ("\\Qgr.nt\\E", True, (("gr.nt",),)),  # translated to escaped literals
        # casefolded, and only ASCII pieces
        ("FundKing", False, (("fundking",),)),
        ("Straße", True, (("strasse",),)),
        ("café bar", True, (("bar",),)),
        # groups: required when repeated at least once, nothing otherwise
        ("(?:grant)+ (?:award)? (?>fund)(?P<x>ing)", True, (("grant",), ("fund",), ("ing",))),
        ("(?i:grant)", True, (("grant",),)),
        ("(?=grant)", True, ()),
        ("(grant){2,3}", False, (("grant",),)),
        ("(grant){,3}", False, ()),
    ],
)
def test_examples(pattern: str, perl: bool, cnf: tuple[tuple[str, ...], ...]) -> None:
    assert required_literals(pattern, perl=perl) == cnf
    assert required_literals(pattern, perl=perl, icase=True) == cnf


@pytest.mark.parametrize(
    ("pattern", "perl"),
    [
        ("(?x) grant", True),  # verbose: white space and # change meaning
        ("(?V1)grant", True),  # version 1: other set syntax
        ("(?#note)grant", True),
        ("grant{e<=1}ing", False),  # a fuzzy constraint
        ("gran{x}ting", False),  # a literal brace
        ("[[:]|xyz:]]abc", True),  # "[:" that is not a class name
        ("[abc", False),  # invalid
    ],
)
def test_what_is_not_modelled_requires_nothing(pattern: str, perl: bool) -> None:
    assert required_literals(pattern, perl=perl) == ()


def test_the_kill_switch_turns_the_prefilter_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert required_literals("funding") == (("funding",),)
    for value in ("off", "OFF", " Off ", "0", "false", "no"):
        monkeypatch.setenv(env_names("LITERALS")[0], value)
        assert required_literals("funding") == ()
        assert detect_many(["funding", "grant"], "Funding was ...", True) == [True, False]
    monkeypatch.setenv(env_names("LITERALS")[0], "on")
    assert required_literals("funding") == (("funding",),)
    monkeypatch.delenv(env_names("LITERALS")[0])
    monkeypatch.setenv(env_names("LITERALS")[1], "off")  # the old name still works
    assert required_literals("funding") == ()


# -- detect_many ----------------------------------------------------------------------


def test_detect_many_gives_what_the_detector_gives() -> None:
    text = "This work was FUNDED by the Straße Foundation (grant 12) and Kelvin, Inc."
    pats = [
        "funded",
        "\\bgrant\\b",
        "strasse",
        "Straße",
        "kelvin",
        "no such words",
        "(found|fund)ation",
        "",
        "[0-9]+",
        "inc\\.$",
    ]
    for icase in (False, True):
        for perl in (False, True):
            want = [rx.detector(p, icase, perl)(text) for p in pats]
            assert detect_many(pats, text, icase, perl) == want
            assert detect_many(pats, text, icase, perl) == list(grepl_each(pats, text, icase, perl))
    assert detect_many(["a.c", "abc"], "xa.cx", fixed=True) == [True, False]
    assert detect_many(pats, None) == [False] * len(pats)
    assert detect_many([], "text") == []


def grepl_each(pats: list[str], text: str, icase: bool, perl: bool) -> Iterator[bool]:
    for p in pats:
        yield rx.grepl(p, text, icase, perl)


def test_detect_many_compiles_only_candidates() -> None:
    bad = "grant[[:nosuchclass:]]"  # its literal parses; the class name is unknown
    assert required_literals(bad) == (("grant",),)
    assert detect_many([bad], "no match here") == [False]  # filtered out, never compiled
    with pytest.raises(rx.RegexError):
        detect_many([bad], "a grant here")


# -- I2: the recorded replay -----------------------------------------------------------

_SUBJECT = {"sub": 1, "subn": 1}  # compiled-pattern methods: the string's position


def _replay_subjects() -> Iterator[tuple[str, bool, bool, list[str]]]:
    """``(pattern, icase, perl, strings)`` for every recorded call of a regex pattern."""
    for name, spec, args, kwargs, _ in load(DATA):
        if spec is not None:
            pattern, icase, perl, fixed = spec[:4]
            method = name.split(".", 1)[1]
            s = kwargs.get("string", args[_SUBJECT.get(method, 0)] if args else None)
            strings = [s]
        else:
            bound = inspect.signature(getattr(rx, name)).bind(*args, **kwargs).arguments
            pattern = bound.get("pattern", bound.get("split"))
            icase, perl = bound.get("ignore_case", False), bound.get("perl", False)
            fixed = bound.get("fixed", False)
            x = bound["x"]
            strings = [x] if isinstance(x, str) or x is None else list(x)
        if fixed or not isinstance(pattern, str):
            continue
        yield pattern, bool(icase), bool(perl), [s for s in strings if isinstance(s, str)]


def _violations(pattern: str, perl: bool, s: str) -> list[tuple[str, ...]]:
    folded = fold(s)
    return [
        clause
        for clause in required_literals(pattern, perl)
        if not any(piece in folded for piece in clause)
    ]


def i2() -> dict[str, Any]:
    """The I2 counts over the replay (also printed by ``python -m tests...`` for reports)."""
    matches = with_clauses = 0
    violations: list[tuple[str, bool, tuple[str, ...], str]] = []
    for pattern, icase, perl, strings in _replay_subjects():
        search = rx.compile_r(pattern, icase, perl, posix=False).search
        cnf = required_literals(pattern, perl)
        for s in strings:
            if search(s) is None:
                continue
            matches += 1
            if not cnf:
                continue
            with_clauses += 1
            for clause in _violations(pattern, perl, s):
                violations.append((pattern, perl, clause, s[:120]))
    return {"matches": matches, "with_clauses": with_clauses, "violations": violations}


def test_i2_every_recorded_match_has_its_required_literals() -> None:
    counts = i2()
    # 92,767 matches, 11,700 of them of patterns with clauses (2026-10)
    assert counts["matches"] > 90_000
    assert counts["with_clauses"] > 11_000
    assert not counts["violations"], "\n".join(map(repr, counts["violations"][:10]))


# -- the built-in triples ---------------------------------------------------------------


def builtin_triples() -> list[tuple[str, bool, bool]]:
    """The built-in ``(pattern, icase, perl)`` triples (see the module docstring)."""
    rows = json.loads(BUILTIN.read_text(encoding="utf-8"))["triples"]
    return [(pattern, icase, perl) for pattern, icase, perl in rows]


def test_the_builtin_triples_keep_their_literals() -> None:
    triples = builtin_triples()
    assert len(triples) > 250
    covered = sum(bool(required_literals(p, perl, icase)) for p, icase, perl in triples)
    assert covered >= BUILTIN_COVERED_FLOOR, f"{covered} of {len(triples)} triples have literals"


def test_the_recorded_builtin_triples_are_current(tmp_path: Path) -> None:
    """The JSON is what the recorder gives today: a module's new pattern means a re-record.

    The recorder runs in a fresh interpreter: in this one, detectors made at import
    time and per-paper memos left by earlier tests would hide some patterns.
    """
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); "
        "from tests.foundation.record_builtin_patterns import record; "
        "print(json.dumps([list(t) for t in record()]))"
    )
    # the accuracy run makes its temporary folder in the cache folder, which must exist
    env = {**os.environ, "METACHECK_CACHE_DIR": str(tmp_path), "PYTACHECK_CACHE_DIR": str(tmp_path)}
    out = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
        env=env,
    )
    recorded = json.loads(out.stdout.strip().splitlines()[-1])
    stored = [list(t) for t in builtin_triples()]
    assert recorded == stored, (
        "the built-in patterns changed: run `python tests/foundation/record_builtin_patterns.py` "
        "and commit tests/foundation/data/builtin_patterns.json"
    )


def test_every_builtin_triple_compiles() -> None:
    for pattern, icase, perl in builtin_triples():
        rx.compile_r(pattern, icase, perl, posix=False)
