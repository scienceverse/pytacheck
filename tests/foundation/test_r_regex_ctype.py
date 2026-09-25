"""R's character tables (``_r/_ctype_tables.py``) and the regex sets built from them.

The tables come from R (``gen_ctype_tables.py``); ``test_tables_match_r``
(marker ``r``) regenerates them from the reference R. The other tests check,
over every code point, that the `regex` expressions the translators emit match
exactly the characters the tables say, so an update of the `regex` module's
Unicode data (whose properties the emitted sets are written with) cannot
silently change TRE's or PCRE2's classes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import regex

from pytacheck._r import _charset as cs
from pytacheck._r import _pcre, _tre

CLASSES = (
    "alnum",
    "alpha",
    "blank",
    "cntrl",
    "digit",
    "graph",
    "lower",
    "print",
    "punct",
    "space",
    "upper",
    "xdigit",
)
SURROGATES = ((0xD800, 0xDFFF),)


def _swept(expr: str) -> cs.Ranges:
    return cs.sweep(expr)


def _expected(s: cs.Ranges) -> cs.Ranges:
    return cs.difference(s, SURROGATES)


@pytest.mark.parametrize("name", CLASSES)
def test_emitted_class_sets_are_exact(name: str) -> None:
    s = cs.glibc_class(name)
    assert s is not None
    assert _swept(cs.emit_set(s)) == _expected(s)
    neg = cs.complement(s)
    assert _swept(cs.emit_set(neg)) == _expected(neg)


@pytest.mark.parametrize("name", CLASSES)
def test_tre_bracket_classes_follow_glibc(name: str) -> None:
    """``[[:name:]]`` and ``[^[:name:]]`` in TRE (byte and wide mode)."""
    s = cs.glibc_class(name)
    assert s is not None
    t = _tre.translate(f"[[:{name}:]]", False)
    assert _swept(t.pattern) == _expected(s)
    assert _swept(t.pattern_wide) == _expected(s)
    t = _tre.translate(f"[^[:{name}:]]", False)
    assert _swept(t.pattern) == _expected(cs.complement(s))


@pytest.mark.parametrize("name", ["upper", "lower", "alpha", "punct"])
def test_tre_icase_classes(name: str) -> None:
    s = cs.glibc_class_icase(name)
    assert s is not None
    assert _swept(_tre.translate(f"[[:{name}:]]", True).pattern) == _expected(s)


@pytest.mark.parametrize(
    ("escape", "sets"),
    [
        (r"\w", lambda: cs.tre_word()),
        (r"\W", lambda: cs.complement(cs.tre_word())),
        (r"\d", lambda: cs.glibc_class("digit")),
        (r"\s", lambda: cs.glibc_class("space")),
        (r"\S", lambda: cs.complement(cs.glibc_class("space") or ())),
    ],
)
def test_tre_escapes_follow_glibc(escape: str, sets) -> None:  # type: ignore[no-untyped-def]
    t = _tre.translate(escape, False)
    assert _swept(t.pattern) == _expected(sets())


def test_fast_word_variant_differs_only_on_divergent_characters() -> None:
    """The native ``\\w`` variant is used only for subjects without the
    characters on which it and glibc disagree."""
    word = cs.tre_word()
    native = _swept(_tre.translate(r"\w", False).fast or "")
    diff = cs.union(cs.difference(native, word), cs.difference(word, native))
    divergent = cs._word_divergent()
    assert all(c in divergent for lo, hi in diff for c in range(lo, hi + 1))
    assert all(cs.word_divergent(chr(c)) for c in list(divergent)[:500])
    assert not cs.word_divergent("plain ASCII and é, ß, Σ")


def _case_pool() -> str:
    up, lo = cs.case_maps()
    pool = set(up) | set(up.values()) | set(lo) | set(lo.values())
    _, keys = cs._pcre_caseless()
    pool.update(keys)
    return "".join(chr(c) for c in sorted(pool))


def test_tre_icase_literals_use_towupper_towlower() -> None:
    """TRE ``ignore.case`` matches a literal *c* with ``towupper(c)`` and
    ``towlower(c)``, not with itself (R: ``grepl("ǅ", "ǅ", ignore.case = TRUE)``
    is ``FALSE``) and not by full case folding (``σ`` does not match ``ς``)."""
    pool = _case_pool()
    for ch in pool:
        c = ord(ch)
        rx = regex.compile(_tre.translate(regex.escape(ch), True).pattern)
        got = {ord(m) for m in rx.findall(pool)}
        assert got == {cs.towupper(c), cs.towlower(c)}, hex(c)


def test_pcre_caseless_literals_use_the_caseless_sets() -> None:
    """PCRE2's caseless matching: Kelvin sign with k/K, long s with s/S, but
    U+0130/U+0131 with nothing else."""
    pool = _case_pool()
    for ch in pool:
        c = ord(ch)
        rx = regex.compile(_pcre.translate(f"\\x{{{c:X}}}", True).pattern)
        got = {ord(m) for m in rx.findall(pool)}
        assert got == set(cs.pcre_caseless(c)), hex(c)
    assert set(cs.pcre_caseless(ord("k"))) == {ord("k"), ord("K"), 0x212A}
    assert cs.pcre_caseless(0x130) == (0x130,)
    assert cs.pcre_caseless(0x131) == (0x131,)


@pytest.mark.r
def test_tables_match_r() -> None:
    """The stored tables are what the reference R gives now."""
    if not os.environ.get("PYTACHECK_RSCRIPT"):
        pytest.skip("PYTACHECK_RSCRIPT (the reference R) is not set")
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import gen_ctype_tables as gen
    finally:
        sys.path.pop(0)
    from pytacheck._r import _ctype_tables as stored

    fresh: dict[str, object] = {}
    exec(gen.render(gen.run_r(), "VERSION"), fresh)

    def tokens(value: object) -> list[str]:
        return "".join(value).split()  # type: ignore[arg-type]

    for name in ("TOUPPER", "TOLOWER", "PCRE_CASELESS"):
        assert tokens(fresh[name]) == tokens(getattr(stored, name)), name
    classes = fresh["CLASSES"]
    assert isinstance(classes, dict)
    assert classes.keys() == stored.CLASSES.keys()
    for name, ranges in classes.items():
        assert tokens(ranges) == tokens(stored.CLASSES[name]), name
