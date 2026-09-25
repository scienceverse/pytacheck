"""Regenerate ``src/pytacheck/_r/_ctype_tables.py`` from the R reference.

R's TRE engine (``perl = FALSE``) classifies characters with the C library's
``isw*()`` functions and folds case with ``towupper()`` / ``towlower()``; the
parity goldens are generated under glibc's ``C.UTF-8`` locale. This script asks
R for every code point's classes and case mappings and writes them as compact
range tables::

    PYTACHECK_RSCRIPT=/path/to/Rscript python tests/foundation/gen_ctype_tables.py

``tests/foundation/test_r_regex_ctype.py`` checks the tables against R again
(marker ``r``) and the TRE translation against them (always).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from itertools import chain
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "pytacheck" / "_r" / "_ctype_tables.py"

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

R_SWEEP = r"""
args <- commandArgs(TRUE)
out <- args[1]
cps <- c(1:0xD7FF, 0xE000:0x10FFFF)
# R cannot convert the Unicode noncharacters to wide strings
cps <- cps[!((cps >= 0xFDD0 & cps <= 0xFDEF) | bitwAnd(cps, 0xFFFE) == 0xFFFE)]
ch <- intToUtf8(cps, multiple = TRUE)
con <- file(out, "w")
for (cl in strsplit(args[2], ",")[[1]]) {
  hit <- cps[grepl(paste0("[[:", cl, ":]]"), ch)]
  writeLines(paste(c(cl, hit), collapse = " "), con)
}
up <- utf8ToInt(paste(toupper(ch), collapse = ""))
lo <- utf8ToInt(paste(tolower(ch), collapse = ""))
d <- which(up != cps)
writeLines(paste(c("toupper", rbind(cps[d], up[d])), collapse = " "), con)
d <- which(lo != cps)
writeLines(paste(c("tolower", rbind(cps[d], lo[d])), collapse = " "), con)
# PCRE2 (perl = TRUE) caseless equivalents among the candidate characters
cand <- as.integer(strsplit(readLines(args[3], warn = FALSE), " ")[[1]])
cch <- intToUtf8(cand, multiple = TRUE)
for (k in seq_along(cand)) {
  m <- cand[grepl(sprintf("(?i)^\\x{%X}$", cand[k]), cch, perl = TRUE)]
  m <- setdiff(m, cand[k])
  if (length(m)) writeLines(paste(c("caseless", cand[k], m), collapse = " "), con)
}
close(con)
"""


def caseless_candidates() -> list[int]:
    """Characters that may have a case-insensitive equivalent (a superset)."""
    import regex

    out: set[int] = set()
    for c in chain(range(0xD800), range(0xE000, 0x110000)):
        ch = chr(c)
        for f in (str.lower, str.upper, str.title, str.casefold):
            r = f(ch)
            if r != ch:
                out.add(c)
                if len(r) == 1:
                    out.add(ord(r))
    rx = regex.compile(r"[\p{Cased}\p{Changes_When_Casefolded}\p{Changes_When_Casemapped}]")
    out.update(c for c in chain(range(0xD800), range(0xE000, 0x110000)) if rx.match(chr(c)))
    # R cannot represent the noncharacters
    return sorted(c for c in out if c and not (0xFDD0 <= c <= 0xFDEF or c & 0xFFFE == 0xFFFE))


def run_r() -> dict[str, list[int]]:
    """Ask R (``PYTACHECK_RSCRIPT``) for the class members and case maps."""
    rscript = os.environ.get("PYTACHECK_RSCRIPT", "Rscript")
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "sweep.R"
        script.write_text(R_SWEEP, encoding="utf-8")
        out = Path(tmp) / "out.txt"
        cand = Path(tmp) / "cand.txt"
        cand.write_text(" ".join(map(str, caseless_candidates())), encoding="utf-8")
        subprocess.run(
            [rscript, str(script), str(out), ",".join(CLASSES), str(cand)], check=True, env=env
        )
        data: dict[str, list[int]] = {}
        caseless: dict[int, set[int]] = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            name, *vals = line.split(" ")
            if name == "caseless":
                c, *others = map(int, vals)
                caseless[c] = set(others)
            else:
                data[name] = [int(v) for v in vals]
        data["caseless"] = caseless_groups(caseless)  # type: ignore[assignment]
    return data


def caseless_groups(pairs: dict[int, set[int]]) -> list[list[int]]:
    """The equivalence classes (R's caseless matching is symmetric)."""
    groups: list[list[int]] = []
    seen: set[int] = set()
    for c in sorted(pairs):
        if c in seen:
            continue
        group = sorted({c} | pairs[c])
        for m in group:
            assert pairs.get(m, set()) | {m} == set(group), (c, m)
        seen.update(group)
        groups.append(group)
    return groups


def to_ranges(cps: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for c in sorted(cps):
        if out and c == out[-1][1] + 1:
            out[-1] = (out[-1][0], c)
        else:
            out.append((c, c))
    return out


def b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n < 0:
        return "-" + b36(-n)
    s = ""
    while True:
        n, r = divmod(n, 36)
        s = digits[r] + s
        if not n:
            return s


def encode_ranges(ranges: list[tuple[int, int]]) -> str:
    """``gap.len`` tokens (base 36): gap from the previous range's end + 1."""
    toks, prev = [], 0
    for lo, hi in ranges:
        gap, length = lo - prev, hi - lo
        toks.append(b36(gap) if length == 0 else f"{b36(gap)}.{b36(length)}")
        prev = hi + 1
    return " ".join(toks)


def encode_map(pairs: list[int]) -> str:
    """Case map runs ``start.count.delta.step`` (base 36)."""
    items = sorted(zip(pairs[::2], pairs[1::2], strict=True))
    runs: list[list[int]] = []  # start, count, delta, step
    for c, m in items:
        d = m - c
        if runs:
            start, count, delta, step = runs[-1]
            last = start + (count - 1) * step
            if delta == d and count == 1 and c - start in (1, 2):
                runs[-1] = [start, 2, delta, c - start]
                continue
            if delta == d and count > 1 and c == last + step:
                runs[-1][1] += 1
                continue
        runs.append([c, 1, d, 1])
    return " ".join(".".join(b36(v) for v in run) for run in runs)


def wrap(s: str, width: int = 88) -> list[str]:
    lines, cur = [], ""
    for tok in s.split(" "):
        if cur and len(cur) + len(tok) + 1 > width - 8:
            lines.append(cur + " ")
            cur = tok
        else:
            cur = f"{cur} {tok}" if cur else tok
    lines.append(cur)
    return lines


def render(data: dict[str, list[int]], version: str) -> str:
    out = [
        '"""Character tables of R\'s regex engines (generated; do not edit).',
        "",
        "Generated by ``tests/foundation/gen_ctype_tables.py`` from the R reference",
        f"({version}, ``C.UTF-8``): the members of each POSIX class (``iswctype()``)",
        "and the ``towupper()`` / ``towlower()`` mappings, which TRE uses for",
        "``[[:class:]]``, ``\\\\w``, ``\\\\b`` and ``ignore.case``; and the sets of",
        "characters PCRE2 matches caselessly (``perl = TRUE, ignore.case = TRUE``).",
        "Decoded by :mod:`pytacheck._r._charset`.",
        '"""',
        "",
        "# ranges: space-separated base-36 tokens `gap[.len]` (gap after the previous range)",
        "CLASSES = {",
    ]
    for cl in CLASSES:
        out.append(f'    "{cl}": (')
        for line in wrap(encode_ranges(to_ranges(data[cl]))):
            out.append(f'        "{line}"')
        out.append("    ),")
    out.append("}")
    out.append("")
    out.append("# case maps: base-36 runs `start.count.delta.step`")
    for name in ("toupper", "tolower"):
        out.append(f"{name.upper()} = (")
        for line in wrap(encode_map(data[name])):
            out.append(f'    "{line}"')
        out.append(")")
    out.append("")
    out.append("# PCRE2 (perl = TRUE) caseless sets: base-36 code points joined by `.`")
    out.append("PCRE_CASELESS = (")
    groups = " ".join(".".join(b36(c) for c in g) for g in data["caseless"])  # type: ignore[union-attr]
    for line in wrap(groups):
        out.append(f'    "{line}"')
    out.append(")")
    return "\n".join(out) + "\n"


def main() -> int:
    data = run_r()
    rscript = os.environ.get("PYTACHECK_RSCRIPT", "Rscript")
    r_version = subprocess.run(
        [rscript, "-e", "cat(R.version$version.string)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    version = f"{r_version}, {os.confstr('CS_GNU_LIBC_VERSION')}"
    OUT.write_text(render(data, version), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
