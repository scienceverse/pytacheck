"""Parse the console output of R analysis code into statistical result tables.

Port of ``R/r-output.R``: :func:`read_r_output` turns what an R script
printed (as captured from ``source(script, echo = TRUE)`` or the capture
runner of :mod:`metacheck.statout.r_capture`) into the same list-of-tables
shape :func:`metacheck.statout.stat_tables.read_stat_tables` returns, so it
feeds the same STATO typing and statistical-output pipeline.

Each result table is a ``dict`` with R's list element names: ``analysis``,
``title``, ``data`` (a :class:`pandas.DataFrame` of ``string`` columns),
``line``, ``line_seq``, ``call_fn`` and ``model_ref`` (``None`` stands for
R's ``NA``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.regex import compile_r, grepl, gsub, regexec, regextract_all, strsplit, sub

__all__ = ["read_r_output"]

# ---------------------------------------------------------------------------
# Small R helpers shared with stat_tables / stat_output / r_capture
# ---------------------------------------------------------------------------

_WS = "[ \t\r\n]"


def _trimws(x: str | None) -> str | None:
    """R ``trimws()`` of one value (``[ \\t\\r\\n]`` on both ends)."""
    if x is None:
        return None
    return x.strip(" \t\r\n")


def _nzchar(x: str | None) -> bool:
    """R ``nzchar()``: ``NA`` counts as non-empty."""
    return x is None or len(x) > 0


def _make_unique(names: Sequence[str], sep: str = ".") -> list[str]:
    """R ``make.unique()`` (``src/main/unique.c``): ``a, a, a -> a, a.1, a.2``."""
    taken = set(names)
    first_seen: set[str] = set()
    counts: dict[str, int] = {}
    out: list[str] = []
    for nm in names:
        if nm not in first_seen:
            first_seen.add(nm)
            out.append(nm)
            continue
        cnt = counts.get(nm, 1)
        while True:
            cand = f"{nm}{sep}{cnt}"
            if cand not in taken:
                break
            cnt += 1
        counts[nm] = cnt + 1
        taken.add(cand)
        out.append(cand)
    return out


def _chr_frame(names: Sequence[str], columns: Sequence[Sequence[Any]]) -> pd.DataFrame:
    """A data.frame of character columns (duplicate names allowed, as in R)."""
    import numpy as np

    if not columns:
        return pd.DataFrame(index=pd.RangeIndex(0))
    nrow = len(columns[0])
    arr = np.empty((nrow, len(columns)), dtype=object)
    for j, col in enumerate(columns):
        arr[:, j] = list(col)
    return pd.DataFrame(arr, columns=list(names), dtype="string")


def _one_row_frame(pairs: Sequence[tuple[str, str]]) -> pd.DataFrame:
    """``data.frame(as.list(setNames(values, names)), check.names = FALSE)``."""
    return _chr_frame([k for k, _ in pairs], [[v] for _, v in pairs])


_R_NUMBER = (
    "^[ \t\n\x0b\x0c\r]*([+-]?(?:(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    "|0[xX](?:[0-9a-fA-F]+\\.?[0-9a-fA-F]*|\\.[0-9a-fA-F]*|(?=[pP]))(?:[pP][+-]?[0-9]+)?"
    "|(?i:infinity|inf|nan)))[ \t\n\x0b\x0c\r]*$"
)


def _r_as_numeric(x: Any) -> float | None:
    """R ``as.numeric()`` of one value (``None`` for ``NA``).

    Strings follow R's ``String -> double`` coercion: surrounding white
    space, decimal/scientific/hexadecimal notation, ``Inf``/``infinity``/
    ``NaN`` in any case; anything else (``"1e"``, ``"."``, ``"NA"``) is ``NA``.
    """
    if x is None:
        return None
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, int | float):
        return float(x)
    if not isinstance(x, str):
        return None
    m = compile_r(_R_NUMBER, perl=True).search(x)
    if m is None:
        return None
    body = m.group(1)
    low = body.lower()
    sign = -1.0 if low.startswith("-") else 1.0
    low = low.lstrip("+-")
    if not low.startswith("0x"):
        return float(body)
    # R_strtod's hexadecimal branch: the mantissa may be empty ("0x.", "0xp1").
    mant, _, exp = low[2:].partition("p")
    whole, _, frac = mant.partition(".")
    val = float(int(whole or "0", 16))
    if frac:
        val += int(frac, 16) / 16.0 ** len(frac)
    try:
        return sign * val * 2.0 ** int(exp or "0")
    except OverflowError:
        return sign * math.inf


class _RError(ValueError, TypeError):
    """An error R would raise (e.g. ``$`` on an atomic vector)."""


class _RNamedList(dict):  # type: ignore[type-arg]
    """A named R list parsed from JSON (``jsonlite::fromJSON(simplifyVector = FALSE)``).

    Unlike a Python ``dict``, an R list keeps *duplicate* names: ``pairs``
    holds every ``(name, value)`` in order, while the mapping itself holds the
    first value of each name (what ``x$name`` and ``x[["name"]]`` return).
    """

    __slots__ = ("pairs",)

    def __init__(self, pairs: Sequence[tuple[str, Any]]) -> None:
        super().__init__()
        for k, v in pairs:
            if k not in self:
                dict.__setitem__(self, k, v)
        self.pairs = list(pairs)


def _r_names(x: Any) -> list[str]:
    """``names(x)`` of a named list (``[]`` for ``NULL``/unnamed values)."""
    if isinstance(x, _RNamedList):
        return [k for k, _ in x.pairs]
    if isinstance(x, Mapping):
        return [str(k) for k in x]
    return []


def _r_values(x: Any) -> list[Any]:
    """The elements of an R list (``for (e in x)``), duplicates included."""
    if x is None:
        return []
    if isinstance(x, _RNamedList):
        return [v for _, v in x.pairs]
    if isinstance(x, Mapping):
        return list(x.values())
    if isinstance(x, list | tuple):
        return list(x)
    return [x]


def _r_dollar_found(x: Any, key: str) -> tuple[bool, Any]:
    """R ``x$key`` on a list: ``(found, value)``.

    The first *exact* name wins; otherwise a *unique* partial (prefix) match
    is used, as R's ``$`` does for lists (``list(line_seq = 2)$line`` is 2).
    ``NULL`` and unnamed lists give ``(False, None)``; an atomic value raises
    R's error.
    """
    if x is None or isinstance(x, list | tuple):
        return False, None
    if not isinstance(x, Mapping):
        raise _RError("$ operator is invalid for atomic vectors")
    if isinstance(x, _RNamedList):
        items = x.pairs
    else:
        if key in x:
            return True, x[key]
        items = list(x.items())
    partial: list[int] = []
    for i, (nm, _) in enumerate(items):
        nm = str(nm)
        if nm == key:
            return True, items[i][1]
        if nm.startswith(key):
            partial.append(i)
    if len(partial) == 1:
        return True, items[partial[0]][1]
    return False, None


def _r_dollar(x: Any, key: str) -> Any:
    """R ``x$key`` (with partial matching); ``None`` for ``NULL``."""
    return _r_dollar_found(x, key)[1]


def _as_lines(x: Any) -> list[str | None]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series):
        return [None if pd.isna(v) else str(v) for v in x.tolist()]
    return [None if v is None or (isinstance(v, float) and v != v) else str(v) for v in x]


# ---------------------------------------------------------------------------
# The shared statistic pattern (R/stat_helpers.R::.r_stat_pattern)
# ---------------------------------------------------------------------------

_OPERATORS = "=<>~\u2248\u2260\u2264\u2265\u226a\u226b"
_GREEK = "\u0370-\u03ff"
_R_STAT_PATTERN_FALLBACK = (
    f"([{_GREEK}\u00b2a-zA-Z][{_GREEK}\u00b2a-zA-Z0-9._-]*)\\s*"
    "(\\([^)]*\\))?\\s*"
    f"([{_OPERATORS}]{{1,3}})\\s*"
    "(<\\s*[.0-9]+|-?[0-9]+(?:\\.[0-9]+)?(?:e[-+]?[0-9]+)?)"
)


def _stat_pattern() -> str:
    """The pattern of ``.r_stat_pattern()`` (``R/stat_helpers.R``)."""
    try:
        from metacheck.stats.helpers import _r_stat_pattern  # type: ignore[attr-defined]
    except ImportError:
        return _R_STAT_PATTERN_FALLBACK
    pp = _r_stat_pattern()
    if isinstance(pp, Mapping):
        return str(pp["pattern"])
    if isinstance(pp, str):
        return pp
    return str(pp.pattern)


# ---------------------------------------------------------------------------
# read_r_output()
# ---------------------------------------------------------------------------

_ANSI_PROMPT = "\033\\[[0-9;]*m(> |\\+ )"
_ANSI = "\033\\[[0-9;]*m"


def _strip_ansi(x: str | None) -> str | None:
    if x is None:
        return None
    x = gsub(_ANSI_PROMPT, "\n\\1", x)
    return str(gsub(_ANSI, "", x))


def read_r_output(
    text: str | Sequence[str] | None,
    source_label: str | None = None,
    code_lines: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract statistical results from captured R console output.

    Port of ``R/r-output.R::read_r_output()``.

    Parameters
    ----------
    text
        The captured console output: a list of lines, or a single string with
        embedded newlines.
    source_label
        Optional label (e.g. the script name). Kept for API parity: R ignores
        it in practice because a result's analysis falls back to its (``NA``)
        title first.
    code_lines
        Optional script source lines, used to recover the 1-based source
        ``line`` of each result from the echoed ``> `` statements.

    Returns
    -------
    list of dict
        One element per detected result block, with ``analysis``, ``title``,
        ``data``, ``line``, ``line_seq``, ``call_fn`` and ``model_ref``.
    """
    if text is None:
        return []
    vec = _as_lines(text)
    if len(vec) == 1:
        s = _strip_ansi(vec[0])
        lines: list[str | None] = [] if s is None else list(strsplit(s, "\n", fixed=True))
    else:
        lines = [_strip_ansi(v) for v in vec]
    if not lines:
        return []

    root_map = _r_root_ref_map(code_lines) if code_lines is not None else None

    def resolve_ref(ref: str | None) -> str | None:
        if ref is None:
            return ref
        if root_map is not None and ref in root_map:
            return root_map[ref]
        return ref

    def parse_chunk(
        chunk_lines: list[str | None], line: int | None, call_text: str = ""
    ) -> list[dict[str, Any]]:
        if _r_is_preview_call(call_text):
            return []
        out = [
            *_r_output_tables(chunk_lines),
            *_r_output_oneline(chunk_lines, source_label),
            *_r_output_cohend(chunk_lines),
            *_r_output_effectsize_d(chunk_lines),
        ]
        out = [t for t in out if t is not None and len(t["data"]) > 0]
        fn = _r_call_fn(call_text)
        ref = resolve_ref(_r_call_object_ref(call_text))
        for k, t in enumerate(out, start=1):
            t["line"] = line
            t["line_seq"] = k
            t["call_fn"] = fn
            t["model_ref"] = ref
        return out

    if code_lines is None:
        return parse_chunk(lines, None)

    chunks = _r_echo_chunks(lines, code_lines)
    if not chunks:
        return parse_chunk(lines, None)
    result: list[dict[str, Any]] = []
    for ch in chunks:
        result.extend(parse_chunk(ch["output"], ch["line"], ch["call"]))
    return result


def _r_echo_chunks(
    lines: Sequence[str | None], code_lines: Sequence[str | None]
) -> list[dict[str, Any]]:
    """Split ``echo = TRUE`` stdout into one chunk per top-level statement.

    Port of ``R/r-output.R::.r_echo_chunks()``. Returns a list of
    ``{"line", "call", "output"}``; ``line`` is the 1-based position of the
    statement's first line in *code_lines* (``None`` when not found).
    """
    lines = list(lines)
    is_prompt = grepl("^(>|\\+) ?", lines)
    if not any(is_prompt):
        return []
    is_new_stmt = grepl("^> ?", lines)
    starts = [i for i, v in enumerate(is_new_stmt) if v]
    if not starts:
        return []
    ends = [s - 1 for s in starts[1:]] + [len(lines) - 1]

    code_list = _as_lines(code_lines)
    code_stripped = gsub("[[:space:]]+", "", [_trimws(c) for c in code_list])
    # which(code_stripped == x)[1], as one lookup table
    first_line: dict[str, int] = {}
    for i, c in enumerate(code_stripped):
        if c is not None and c not in first_line:
            first_line[c] = i + 1
    chunks: list[dict[str, Any]] = []
    for start, end in zip(starts, ends, strict=True):
        seg = lines[start : end + 1]
        # the statement is the "> " line and the "+ " continuation lines right
        # after it; R counts every prompt-like line of the chunk, so output
        # lines starting with "+" were taken as statement text and dropped
        # (UPSTREAM_ISSUES U140)
        prompt_n = 0
        for v in grepl("^(>|\\+) ?", seg):
            if not v:
                break
            prompt_n += 1
        stmt = [_trimws(s) for s in sub("^(>|\\+) ?", "", seg[:prompt_n])]
        output = seg[prompt_n:]
        nonempty = [s for s in stmt if _nzchar(s)]
        first_stmt_line = nonempty[0] if nonempty else None
        line: int | None = None
        if first_stmt_line is not None:
            line = first_line.get(str(gsub("[[:space:]]+", "", first_stmt_line)))
        call = " ".join("NA" if s is None else s for s in stmt)
        if output:
            chunks.append({"line": line, "call": call, "output": output})
    return chunks


# Which R function produced this chunk? ------------------------------------------

_R_TEST_CALLS = (
    "shapiro.test",
    "wilcox.test",
    "kruskal.test",
    "bartlett.test",
    "fisher.test",
    "mcnemar.test",
    "chisq.test",
    "prop.test",
    "t.test",
    "cor.test",
    "var.test",
    "binom.test",
    "ks.test",
    "friedman.test",
    "mood.test",
    "fligner.test",
    "ansari.test",
    "mantelhaen.test",
    "poisson.test",
    "cohen.d",
    "cohens_d",
    "repeated_measures_d",
)
_R_STATIX_ALIASES = {
    "t_test": "t.test",
    "wilcox_test": "wilcox.test",
    "kruskal_test": "kruskal.test",
    "cor_test": "cor.test",
    "chisq_test": "chisq.test",
    "prop_test": "prop.test",
    "var_test": "var.test",
    "shapiro_test": "shapiro.test",
}
_TEST_CALL_PATTERNS = tuple((f, "\\b" + f.replace(".", "\\.") + "\\s*\\(") for f in _R_TEST_CALLS)
_STATIX_PATTERNS = tuple((f, "\\b" + f + "\\s*\\(") for f in _R_STATIX_ALIASES)


def _r_call_fn(call_text: str | None) -> str:
    """The first recognised statistical call in *call_text*, or ``""``.

    Port of ``R/r-output.R::.r_call_fn()``.
    """
    if call_text is None or call_text == "":
        return ""
    ct = call_text.lower()
    for f, pat in _TEST_CALL_PATTERNS:
        if compile_r(pat, perl=True).search(ct):
            return f
    for f, pat in _STATIX_PATTERNS:
        if compile_r(pat, perl=True).search(ct):
            return _R_STATIX_ALIASES[f]
    return ""


_R_PREVIEW_CALLS = (
    "head",
    "tail",
    "str",
    "glimpse",
    "print",
    "view",
    "dim",
    "names",
    "colnames",
    "rownames",
    "nrow",
    "ncol",
)
_PREVIEW_PATTERNS = tuple("^(dplyr::|utils::|base::)?" + f + "\\s*\\(" for f in _R_PREVIEW_CALLS)


def _r_is_preview_call(call_text: str | None) -> bool:
    """Is *call_text* a data-preview call (``head()``, ``str()``, ...)?

    Port of ``R/r-output.R::.r_is_preview_call()``.
    """
    if call_text is None or call_text == "":
        return False
    ct = call_text.lower()
    return any(compile_r(p, perl=True).search(ct) for p in _PREVIEW_PATTERNS)


# The object a statement is about -------------------------------------------------

_IDENT = "^[A-Za-z._][A-Za-z0-9._]*"
_CHAIN = "^(\\$[A-Za-z._][A-Za-z0-9._]*|\\[[^][]*\\]|\\[\\[[^][]*\\]\\])+$"


def _leading_ident(x: str) -> str | None:
    x = _trimws(x) or ""
    m = compile_r(_IDENT, perl=True).search(x)
    if m is None or m.group(0) == "":
        return None
    ident = m.group(0)
    rest = x[len(ident) :]
    if rest == "" or compile_r(_CHAIN, perl=True).search(rest):
        return ident
    return None


def _r_call_object_ref(call_text: str | None) -> str | None:
    """The R object a statement is about (``"m"`` from ``summary(m)``), or ``None``.

    Port of ``R/r-output.R::.r_call_object_ref()``.
    """
    if call_text is None or call_text == "":
        return None
    ct = _trimws(call_text) or ""
    if compile_r("<-|=(?!=)", perl=True).search(ct) and compile_r(
        "^[A-Za-z._][A-Za-z0-9._]*\\s*(<-|=(?!=))", perl=True
    ).search(ct):
        return None

    pipe = compile_r("\\s*(\\|>|%>%)\\s*", perl=True).search(ct)
    lhs = sub("\\s*(\\|>|%>%).*$", "", ct, perl=True) if pipe and pipe.group(0) else ct
    ref = _leading_ident(lhs)
    if ref is not None:
        return ref

    open_paren = ct.find("(")
    if open_paren == -1:
        return None
    depth = 0
    i = open_paren
    n = len(ct)
    in_str: str | None = None
    end: int | None = None
    while i < n:
        ch = ct[i]
        if in_str is not None:
            if ch == "\\":
                i += 1
            elif ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end is None:
        return None
    args_text = ct[open_paren + 1 : end]
    for arg in _repro_split_args(args_text):
        val = sub(
            "^[.a-zA-Z][.a-zA-Z0-9_]*\\s*=\\s*(?!=)", "", _trimws(arg["text"]) or "", perl=True
        )
        r = _leading_ident(val)
        if r is not None:
            return r
    return None


def _repro_split_args(args_text: str) -> list[dict[str, Any]]:
    """Split an argument-list string at top-level commas.

    Port of ``R/r-output.R::.repro_split_args()``. Returns a list of
    ``{"text", "start", "end"}`` with 1-based inclusive character positions.
    """
    if args_text == "":
        return []
    depth = 0
    in_str: str | None = None
    n = len(args_text)
    arg_start = 1
    args: list[dict[str, Any]] = []
    i = 1
    while i <= n:
        ch = args_text[i - 1]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(
                {"text": args_text[arg_start - 1 : i - 1], "start": arg_start, "end": i - 1}
            )
            arg_start = i + 1
        i += 1
    args.append({"text": args_text[arg_start - 1 : n], "start": arg_start, "end": n})
    return args


_ASSIGN_RE = "^([A-Za-z._][A-Za-z0-9._]*)\\s*(?:<-|=(?!=))\\s*(.*)$"


def _r_root_ref_map(code_lines: Sequence[str | None] | None) -> dict[str, str]:
    """Trace assigned names back to their root object through simple chains.

    Port of ``R/r-output.R::.r_root_ref_map()``: ``{"r2": "m"}`` for
    ``r2 <- f(m)``; empty when no chain is found.
    """
    lines = [_trimws(v) for v in _as_lines(code_lines)]
    direct: dict[str, str] = {}
    for h in regexec(_ASSIGN_RE, lines, perl=True):
        if len(h) < 3:
            continue
        lhs, rhs = h[1], h[2]
        ref = _r_call_object_ref(rhs)
        if ref is not None and ref != lhs:
            direct[lhs] = ref
    if not direct:
        return direct

    def root_of(x: str, seen: tuple[str, ...] = ()) -> str:
        while not (x in seen or x not in direct):
            seen = (*seen, x)
            x = direct[x]
        return x

    return {k: root_of(k) for k in direct}


# ---------------------------------------------------------------------------
# One-line tests
# ---------------------------------------------------------------------------

_TITLE_RE = (
    "(?i)(t-test|correlation|chi-squared|proportion|wilcoxon|shapiro|anova|fisher|"
    "kruskal|bartlett|mann-whitney|test)$"
)
_STAT_NAME_RE = "(?i)^(t|z|f|r|w|u|h|d|p|p-value|df|chi|x-squared|bf|rho|tau|s|v|estimate|mean)"


def _r_output_oneline(
    lines: Sequence[str | None],
    source_label: str | None = None,
) -> list[dict[str, Any]]:
    """Parse ``<stat> <op> <value>`` fragments grouped under test titles.

    Port of ``R/r-output.R::.r_output_oneline()``. Results without a test
    title take *source_label* (e.g. the script name) as their analysis, as
    :func:`read_r_output` documents; metacheck's ``cur_title %||%
    source_label`` never falls back from ``cur_title``'s ``NA``
    (UPSTREAM_ISSUES U139).
    """
    pattern = _stat_pattern()
    rx = compile_r(pattern, perl=True)
    title_rx = compile_r(_TITLE_RE)
    name_rx = compile_r(_STAT_NAME_RE)
    results: list[dict[str, Any]] = []
    cur_title: str | None = None
    cur: list[tuple[str, str]] = []

    def flush() -> None:
        if not cur:
            return
        names = _make_unique([s for s, _ in cur])
        df = _one_row_frame(list(zip(names, [v for _, v in cur], strict=True)))
        analysis = cur_title if cur_title is not None else _label(source_label)
        results.append({"analysis": analysis, "title": cur_title, "data": df})

    for ln in lines:
        if ln is None:
            continue
        tl = _trimws(ln) or ""
        if title_rx.search(tl) and not rx.search(tl):
            flush()
            cur = []
            cur_title = tl
            continue
        for f in regextract_all(pattern, ln, perl=True):
            m = rx.search(f)
            if m is None:
                continue
            nm = m.group(1)
            dfp = m.group(2) or ""
            val = m.group(4)
            if name_rx.search(nm):
                cur.append((nm, val))
                if dfp:
                    cur.append(("df", gsub("[()]", "", dfp)))
    flush()
    return results


def _label(x: Any) -> str | None:
    """A label given as a string (``None`` for ``NULL``, ``NA`` or ``""``)."""
    if x is None or not isinstance(x, str) or x == "":
        return None
    return x


# ---------------------------------------------------------------------------
# effsize::cohen.d() and effectsize::cohens_d()
# ---------------------------------------------------------------------------

_COHEND_RE = "(?i)^\\s*d estimate:\\s*(-?[0-9.]+(?:e[-+]?[0-9]+)?)\\s*\\(([a-z]+)\\)\\s*$"


def _r_output_cohend(lines: Sequence[str | None]) -> list[dict[str, Any]]:
    """Parse ``effsize::cohen.d()`` printouts (``d estimate: 0.18 (negligible)``).

    Port of ``R/r-output.R::.r_output_cohend()``.
    """
    n = len(lines)
    rx = compile_r(_COHEND_RE, perl=True)
    ci_rx = compile_r("(?i)confidence interval:\\s*$")
    out: list[dict[str, Any]] = []
    for i in range(n):
        s = lines[i]
        if s is None:
            continue
        m = rx.search(s)
        if m is None:
            continue
        d_val, magnitude = m.group(1), m.group(2)
        row: list[tuple[str, str]] = [("d", d_val)]
        nxt = lines[i + 1] if i + 1 < n else None
        if i + 2 < n and nxt is not None and ci_rx.search(nxt):
            hdr = lines[i + 2] or ""
            dat = (lines[i + 3] or "") if i + 3 < n else ""
            hdr_toks = strsplit(_trimws(hdr), "\\s+")
            dat_toks = strsplit(_trimws(dat), "\\s+")
            if (
                len(hdr_toks) == 2
                and len(dat_toks) == 2
                and all(grepl("(?i)^(lower|upper)$", hdr_toks))
                and all(grepl("^-?[0-9.]+(e[-+]?[0-9]+)?$", dat_toks, ignore_case=True))
            ):
                row.extend(zip([t.lower() for t in hdr_toks], dat_toks, strict=True))
        out.append(
            {
                "analysis": "Cohen's d",
                "title": f"Cohen's d ({magnitude})",
                "data": _one_row_frame(row),
            }
        )
    return out


_EFFSIZE_HDR_RE = (
    "^\\s*(Cohen's d|d \\(rm\\)|Hedges'? g|Glass'? delta)\\s*\\|\\s*(?:[0-9]+% CI)?\\s*$"
)
_NUM = "(-?[0-9.]+(?:e[-+]?[0-9]+)?)"
_EFFSIZE_DAT_RE = f"^\\s*{_NUM}\\s*\\|\\s*\\[\\s*{_NUM}\\s*,\\s*{_NUM}\\s*\\]\\s*$"


def _r_output_effectsize_d(lines: Sequence[str | None]) -> list[dict[str, Any]]:
    """Parse effectsize's three-line ``Cohen's d | 95% CI`` pipe tables.

    Port of ``R/r-output.R::.r_output_effectsize_d()``.
    """
    n = len(lines)
    hdr_rx = compile_r(_EFFSIZE_HDR_RE, ignore_case=True, perl=True)
    dat_rx = compile_r(_EFFSIZE_DAT_RE, perl=True)
    dash_rx = compile_r("^-+$")
    out: list[dict[str, Any]] = []
    i = 0
    while i < n:
        hdr = lines[i]
        m = hdr_rx.search(hdr) if hdr is not None else None
        sep = lines[i + 1] if i + 1 < n else None
        if m is None or i + 2 >= n or sep is None or not dash_rx.search(sep):
            i += 1
            continue
        dat = lines[i + 2]
        dm = dat_rx.search(dat) if dat is not None else None
        if dm is None:
            i += 1
            continue
        label = _trimws(m.group(1)) or ""
        df = _one_row_frame(
            [("d", dm.group(1)), ("ci_lower", dm.group(2)), ("ci_upper", dm.group(3))]
        )
        out.append({"analysis": label, "title": label, "data": df})
        i += 3
    return out


# ---------------------------------------------------------------------------
# Fixed-width text tables
# ---------------------------------------------------------------------------

_NUMLIKE_1 = "^-?[0-9][0-9.]*(e[-+]?[0-9]+)?$"
_NUMLIKE_2 = "^[<>]\\s*-?[0-9.]+(e[-+]?[0-9]+)?$"
_NUMLIKE_3 = "(?i)^(inf|-?inf|na|nan|<\\s*2e-16)$"


def _is_numlike(x: Iterable[str | None]) -> list[bool]:
    r1 = compile_r(_NUMLIKE_1, ignore_case=True)
    r2 = compile_r(_NUMLIKE_2, ignore_case=True)
    r3 = compile_r(_NUMLIKE_3)
    out = []
    for v in x:
        if v is None:
            out.append(False)
            continue
        t = _trimws(v) or ""
        out.append(bool(r1.search(t) or r2.search(t) or r3.search(t)))
    return out


_QUANTILE_HDR = {"min", "1q", "median", "3q", "max", "mean"}


def _is_quantile_hdr(h: Sequence[str | None]) -> bool:
    toks = [(_trimws(v) or "").lower() for v in h if v is not None]
    toks = [t for t in toks if t]
    return len(toks) > 0 and all(t in _QUANTILE_HDR for t in toks)


def _is_tibble_title(ln: str) -> bool:
    return bool(grepl("^#\\s*A tibble", _trimws(ln)))


_TYPE_TAG = "^<(chr|int|dbl|lgl|fct|ord|date|dttm|list|cplx|raw)>$"


def _is_tibble_type_row(ln: str | None) -> bool:
    tl = _trimws(ln) if ln is not None else None
    if not tl:
        return False
    toks = [t for t in strsplit(tl, "\\s+") if t]
    if toks and toks[0] == "*":
        toks = toks[1:]
    return len(toks) > 0 and all(grepl(_TYPE_TAG, toks))


_NOT_HEADER_RE = (
    "(?i)^(data:|alternative|signif|call|residual standard|multiple r-|--- *$|"
    "sample estimates|[0-9]+ (percent|observ))"
)


def _looks_header(ln: str | None) -> bool:
    if ln is None:
        return False
    tl = _trimws(ln) or ""
    if not tl:
        return False
    if _is_tibble_title(tl):
        return False
    if grepl(_NOT_HEADER_RE, tl):
        return False
    grps = [g for g in strsplit(tl, "\\s+") if g]
    if len(grps) < 2:
        return False
    numlike = _is_numlike(grps)
    return sum(numlike) / len(numlike) < 0.4


_NBSP = "\u00a0"
_SIGCODE_RE = "(?<=\\s)(\\*{1,3}|\\.)(?=\\s)"


def _protect_combined_df(x: str) -> str:
    """Join a combined df cell (``2,  560``) with no-break spaces of the same width.

    Keeping the width keeps the line's columns aligned with the other lines;
    R collapses the whitespace to one no-break space, which shifts every
    column to its right (UPSTREAM_ISSUES U140).
    """
    return compile_r("(?<=[0-9]),(\\s+)(?=[0-9])", perl=True).sub(
        lambda m: "," + _NBSP * len(m.group(1)), x
    )


def _blank_sigcode(x: str) -> str:
    return compile_r(_SIGCODE_RE, perl=True).sub(lambda m: " " * len(m.group(0)), x)


def _split_block(block: Sequence[str]) -> list[list[str]] | None:
    """Split block lines at character columns blank in every line."""
    block = [_blank_sigcode(_protect_combined_df(b.replace("\t", " " * 8))) for b in block]
    w = max(len(b) for b in block)
    padded = [b.ljust(w) for b in block]
    nb = [any(line[j] != " " for line in padded) for j in range(w)]
    if not any(nb):
        return None
    runs: list[tuple[int, int]] = []
    j = 0
    while j < w:
        if nb[j]:
            k = j
            while k < w and nb[k]:
                k += 1
            runs.append((j, k))
            j = k
        else:
            j += 1
    nbsp_run = compile_r(_NBSP + "+")
    return [[nbsp_run.sub(" ", _trimws(line[a:b]) or "") for line in padded] for a, b in runs]


_SECTION_RE = "^[A-Za-z][A-Za-z0-9 .()|>-]*:$"
_STOP_RE = "(?i)^(---|signif|residual standard|multiple r-|f-statistic|call:|data:|alternative)"


def _r_output_tables(lines: Sequence[str | None]) -> list[dict[str, Any]]:
    """Parse fixed-width text tables (``summary(lm)``, ``aov``, ``anova``, ...).

    Port of ``R/r-output.R::.r_output_tables()``: columns are split at the
    character positions that are blank in every line of the block.
    """
    lines = list(lines)
    n = len(lines)
    section_rx = compile_r(_SECTION_RE)
    dash_rx = compile_r("^-{3,}\\s*$")
    stop_rx = compile_r(_STOP_RE)
    digit_rx = compile_r("[0-9]")
    tables: list[dict[str, Any]] = []
    section: str | None = None
    i = 0
    while i < n:
        cur = lines[i]
        tl = _trimws(cur) or ""
        if cur is not None and section_rx.search(tl):
            section = sub(":$", "", tl)
        if _looks_header(cur) and not _is_quantile_hdr(strsplit(tl, "\\s+")):
            j = i + 1
            if j < n and _is_tibble_type_row(lines[j]):
                j += 1
            if j < n and dash_rx.search(_trimws(lines[j]) or ""):
                j += 1
            data_lines: list[str] = []
            while j < n:
                dl = lines[j]
                if dl is None:
                    break
                dtl = _trimws(dl) or ""
                if not dtl:
                    break
                if stop_rx.search(dtl):
                    break
                if not digit_rx.search(dtl):
                    break
                data_lines.append(sub("\\s+[*.]+\\s*$", "", dl))
                j += 1
            if data_lines:
                cols = _split_block([cur or "", *data_lines])
                if cols is not None and len(cols) >= 2:
                    header = [c[0] for c in cols]
                    body = [c[1:] for c in cols]
                    header, body = _repair_columns(header, body)
                    nm = [_trimws(h) or "" for h in header]
                    nm = [v if v else f"V{k + 1}" for k, v in enumerate(nm)]
                    df = _chr_frame(_make_unique(nm), body)
                    if any(any(_is_numlike(col)) for col in body):
                        tables.append({"analysis": section, "title": section, "data": df})
                        i = j
                        continue
        i += 1
    return tables


def _repair_columns(header: list[str], body: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    def empty(col: list[str]) -> bool:
        return all(not (_trimws(v) or "") for v in col)

    k = 0
    while k < len(body) - 1:
        if (
            not (_trimws(header[k]) or "")
            and not empty(body[k])
            and empty(body[k + 1])
            and (_trimws(header[k + 1]) or "")
        ):
            header[k] = header[k + 1]
            del header[k + 1]
            del body[k + 1]
        else:
            k += 1
    k = 0
    while k < len(body) - 1:
        if empty(body[k]) and (_trimws(header[k]) or "") and (_trimws(header[k + 1]) or ""):
            header[k + 1] = f"{_trimws(header[k])} {_trimws(header[k + 1])}"
            del header[k]
            del body[k]
        else:
            k += 1
    return header, body
