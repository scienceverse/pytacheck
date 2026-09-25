"""Seeded random patterns run through R and :mod:`pytacheck._r.regex` (marker ``r``).

Each test generates patterns from a small grammar (the constructs where the
two engines' emulation is delicate: POSIX classes, word boundaries, bounded
and minimal repetitions, case-insensitivity on non-ASCII letters, inline
flags) and subjects over an alphabet with the characters on which the
emulated engines differ from Python's (``ſ``, the Kelvin sign, ``İ``, ``ı``,
``ς``, a combining accent, ...), runs every query in one R process and
compares the results. The seeds are fixed, so the queries are too.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from pytacheck._r import regex as rx

pytestmark = pytest.mark.r

R_RUN = r"""
args <- commandArgs(TRUE)
q <- jsonlite::fromJSON(args[1], simplifyVector = FALSE)
res <- lapply(q, function(e) {
  x <- unlist(e$x)
  ic <- isTRUE(e$icase); pl <- isTRUE(e$perl)
  tryCatch(suppressWarnings(switch(e$fn,
    grepl = as.list(grepl(e$p, x, ignore.case = ic, perl = pl)),
    gsub = as.list(gsub(e$p, e$r, x, ignore.case = ic, perl = pl)),
    all = lapply(regmatches(x, gregexpr(e$p, x, ignore.case = ic, perl = pl)), as.list),
    pos = lapply(gregexpr(e$p, x, ignore.case = ic, perl = pl), function(g) {
      if (g[1] == -1) list() else
        lapply(seq_along(g), function(i) list(g[i], attr(g, "match.length")[i]))
    }),
    exec = lapply(regmatches(x, regexec(e$p, x, ignore.case = ic, perl = pl)), as.list),
    split = lapply(strsplit(x, e$p, perl = pl), as.list)
  )), error = function(err) list(error = conditionMessage(err)))
})
writeLines(jsonlite::toJSON(res, auto_unbox = TRUE, null = "null", digits = NA), args[2],
           useBytes = TRUE)
"""


def _rscript() -> str:
    rscript = os.environ.get("PYTACHECK_RSCRIPT")
    if not rscript:
        pytest.skip("PYTACHECK_RSCRIPT (the reference R) is not set")
    return rscript


def _run_r(queries: list[dict[str, Any]]) -> list[Any]:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "run.R").write_text(R_RUN, encoding="utf-8")
        (d / "q.json").write_text(json.dumps(queries, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        subprocess.run(
            [_rscript(), str(d / "run.R"), str(d / "q.json"), str(d / "o.json")],
            check=True,
            env=env,
        )
        return json.loads((d / "o.json").read_text(encoding="utf-8"))


def _norm_r(q: dict[str, Any], out: Any) -> Any:
    if isinstance(out, dict):
        return "ERROR"
    if q["fn"] == "pos":
        return [[tuple(p) for p in el] for el in out]
    return out


def _run_py(q: dict[str, Any]) -> Any:
    x, p = q["x"], q["p"]
    kw = {"ignore_case": q["icase"], "perl": q["perl"]}
    try:
        fn = q["fn"]
        if fn == "grepl":
            return list(rx.grepl(p, x, **kw))
        if fn == "gsub":
            return list(rx.gsub(p, q["r"], x, **kw))
        if fn == "all":
            return [list(v) for v in rx.regextract_all(p, x, **kw)]
        if fn == "pos":
            return [[tuple(m) for m in v] for v in rx.gregexpr_all(p, x, **kw)]
        if fn == "exec":
            return [list(v) for v in rx.regexec(p, x, **kw)]
        return [list(v) for v in rx.strsplit(x, p, perl=q["perl"])]
    except rx.RegexError:
        return "ERROR"


def _compare(queries: list[dict[str, Any]]) -> None:
    outs = _run_r(queries)
    bad = []
    for q, out in zip(queries, outs, strict=True):
        want, got = _norm_r(q, out), _run_py(q)
        if want != got:
            bad.append((q["fn"], q["p"], q["icase"], want, got))
    assert not bad, "\n".join(map(repr, bad[:10])) + f"\n{len(bad)}/{len(queries)} differ"


def _grammar(rnd: random.Random, atoms: list[str], quants: list[str], groups: list[str]):  # type: ignore[no-untyped-def]
    no_repeat = {"^", "$", r"\b", r"\B", r"\<", r"\>", "(?=a)", "(?!b)", "(?<=a)", "(?i)", "(?-i)"}

    def gen(depth: int = 0) -> str:
        parts = []
        for _ in range(rnd.randint(1, 3)):
            r = rnd.random()
            if r < 0.12 and depth < 2:
                atom = groups[0] + gen(depth + 1) + ")"
            elif r < 0.2 and depth < 2:
                atom = groups[1] + gen(depth + 1) + "|" + gen(depth + 1) + ")"
            else:
                atom = rnd.choice(atoms)
            q = "" if atom in no_repeat else rnd.choice(quants)
            parts.append(atom + q)
        return "".join(parts)

    return gen


def _queries(
    seed: int,
    atoms: list[str],
    quants: list[str],
    alphabet: list[str],
    fns: tuple[str, ...],
    perl: bool,
    groups: tuple[str, str] = ("(", "("),
    n: int = 150,
) -> list[dict[str, Any]]:
    rnd = random.Random(seed)
    gen = _grammar(rnd, atoms, quants, list(groups))
    patterns = sorted({gen() for _ in range(n)})
    subjects = ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 8))) for _ in range(20)]
    out = []
    for p in patterns:
        icase = rnd.random() < 0.4
        for fn in fns:
            q = {"fn": fn, "p": p, "x": subjects, "icase": icase, "perl": perl}
            if fn == "gsub":
                q["r"] = "<\\1>" if "(" in p.replace("(?", "") else "<>"
            out.append(q)
    return out


TRE_ALPHABET = [
    *"aabbc AB.-_1\u00e9",
    "\u00df",  # sharp s
    "\u212a",  # Kelvin sign
    "\u0301",  # combining acute accent
    "\u00a0",  # no-break space
    "\u2013",
    "\u2264",
    "\u03a3",
    "\u03c2",  # final sigma
]
TRE_ATOMS = [
    *"abcA .é",
    "ß",
    "\\.",
    "[ab]",
    "[^ab]",
    "[a-c]",
    "[[:alpha:]]",
    "[[:punct:]]",
    "[[:space:]]",
    "[[:upper:]]",
    "[^[:alnum:]]",
    "\\w",
    "\\W",
    "\\s",
    "\\S",
    "\\d",
    "\\b",
    "\\B",
    "\\<",
    "\\>",
    "^",
    "$",
    "[.-]",
    "K",
    "Σ",
]
TRE_QUANTS = ["", "", "", "*", "+", "?", "{1,2}", "{2}", "{0,1}", "{2,}"]


def test_tre_agrees_with_r() -> None:
    """TRE (``perl = FALSE``): classes, boundaries, bounded repetitions,
    submatches of repeated groups, R's loops, byte and wide-character mode."""
    fns = ("grepl", "gsub", "all", "pos", "exec", "split")
    _compare(_queries(3, TRE_ATOMS, TRE_QUANTS, TRE_ALPHABET, fns, False))


def test_tre_minimal_repetitions_agree_with_r() -> None:
    """TRE's minimal repetitions (``*?``), with submatches."""
    quants = [*TRE_QUANTS, "*?", "+?", "??", "{1,2}?"]
    fns = ("grepl", "gsub", "all", "exec", "split")
    _compare(_queries(12, TRE_ATOMS, quants, TRE_ALPHABET, fns, False))


PCRE_ALPHABET = [
    *"aAbBkKsSiI .-_1\u00e9",
    "\u00df",
    "\u212a",  # Kelvin sign
    "\u017f",  # long s
    "\u0130",  # capital I with dot
    "\u0131",  # dotless i
    "\u03c2",
    "\u03a3",
    "\u00c9",
]
PCRE_ATOMS = [
    *"abksiIKé",
    "ß",
    "Σ",
    " ",
    ".",
    "\\.",
    "[ab]",
    "[^ab]",
    "[a-k]",
    "[^a-k]",
    "[[:alpha:]]",
    "[[:^alpha:]]",
    "[[:upper:]]",
    "[[:^lower:]]",
    "[[:punct:]]",
    "\\w",
    "\\W",
    "\\s",
    "\\d",
    "\\b",
    "\\B",
    "^",
    "$",
    "[\\w.-]",
    "[^\\W]",
    "\\p{L}",
    "\\p{Lu}",
    "\\P{Ll}",
    "[\\p{Lu}k]",
    "(?=a)",
    "(?!b)",
    "(?<=a)",
    "\\x{212a}",
    "\\x{130}",
    "[\u0130\u0131]",
    "(?i:k)",
    "(?-i:s)",
    "(?i)",
    "(?-i)",
]
PCRE_QUANTS = ["", "", "", "*", "+", "?", "{1,2}", "{2}", "*?", "+?", "??", "++", "{0,1}"]


def test_pcre_agrees_with_r() -> None:
    """PCRE2 (``perl = TRUE``): caseless sets, ASCII classes, inline flags."""
    _compare(
        _queries(
            12,
            PCRE_ATOMS,
            PCRE_QUANTS,
            PCRE_ALPHABET,
            ("grepl", "gsub", "all", "exec", "split"),
            True,
            groups=("(", "(?:"),
        )
    )
