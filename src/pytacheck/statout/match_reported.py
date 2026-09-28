"""Match reported statistical tests against extracted analysis output.

Port of ``R/match-reported.R``. Full statistical TESTS reported in a paper's
text (``"M = 1.93, SD = 0.76, W = 183.5, p = .791, rb = -0.16"``) are matched
against the statistics EXTRACTED from the paper's analysis output
(:func:`~pytacheck.statout.stat_output.stat_results_long` rows from JASP /
jamovi / SPSS / notebook files or executed R code).

A reported test is a multi-component statement: its components are recomposed
into one test (from :func:`~pytacheck.text.extract_tests.extract_tests`, or
legacy ``eq`` groups), then checked for CO-OCCURRENCE in a single output
analysis (a "site"). Matching whole tests, not lone numbers, removes the
coincidence problem. Matching is PRECISION-AWARE: a reported value matches an
output value when the output value, rounded to the reported number of
decimals, equals it (reported ``"d = .68"`` matches ``0.6810``).

Implementation note (performance): R evaluates ``val_in(site, component)``
site by site. Here every component is evaluated once against ALL sites at
once (a boolean vector over sites, cached per component signature), from
per-family indexes of the rounded output values -- the same predicate, so
the same results, without the tests x sites x components loop.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r import (
    as_character,
    bind_rows,
    format_num,
    grepl,
    gsub,
    r_round,
    r_sort_key,
    regextract,
    strsplit,
    sub,
    trimws,
)

__all__ = ["match_reported_output"]


# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------


def _is_na(x: Any) -> bool:
    """Scalar R ``is.na()`` (``None`` counts as NA here)."""
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, float | np.floating):
        return bool(np.isnan(x))
    return False


def _tolower(s: str) -> str:
    """R ``tolower()``: one-to-one lower-casing (``towlower``)."""
    if s.isascii():
        return s.lower()
    out = []
    for c in s:
        low = c.lower()
        out.append(low if len(low) == 1 else low[0])
    return "".join(out)


def _chr(x: Any) -> str | None:
    """``as.character()`` of a scalar; ``None`` for NA."""
    if _is_na(x):
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, bool | np.bool_):
        return "TRUE" if x else "FALSE"
    return as_character(x)


def _paste_chr(x: Any) -> str:
    """``paste()``/``sprintf("%s")`` of a scalar: NA becomes ``"NA"``."""
    s = _chr(x)
    return "NA" if s is None else s


def _scalar(x: Any) -> Any:
    """Plain Python scalar (numpy scalars unwrapped, NA -> None)."""
    if _is_na(x):
        return None
    if isinstance(x, np.generic):
        return x.item()
    return x


def _split_key(s: str) -> tuple[Any, ...]:
    """Collation key for ``split()`` levels (ICU root: control chars ignorable)."""
    stripped = "".join(ch for ch in s if ord(ch) >= 32 and ord(ch) != 127)
    return (r_sort_key(stripped), s)


def _split_order(keys: Sequence[str | None]) -> list[tuple[str, list[int]]]:
    """``split(seq_along(keys), keys)``: groups in sorted level order, NA dropped."""
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        if k is None:
            continue
        groups.setdefault(k, []).append(i)
    return [(k, groups[k]) for k in sorted(groups, key=_split_key)]


# ---------------------------------------------------------------------------
# value normalisation
# ---------------------------------------------------------------------------

_NUM_CHARS = frozenset("0123456789.eE+-")


@functools.lru_cache(maxsize=65536)
def _norm_value_str(s: str) -> tuple[float | None, int, str]:
    s = trimws(s)
    cens = ""
    if s[:1] in ("<", ">") and s != "":
        cens = s[0]
        s = trimws(sub(r"^[<>]\s*", "", s))
    s = s.replace(",", "").replace(" ", "")
    # sub("[^0-9.eE+-].*$", "", s): cut at the first character outside the set
    for i, ch in enumerate(s):
        if ch not in _NUM_CHARS:
            s = s[:i]
            break
    dm = regextract(r"\.[0-9]+", s)
    dec = len(dm) - 1 if dm is not None else 0
    # the decimals of the number, not of its mantissa: 1.5e-05 is written to
    # 6 decimals (R counts 1, so its tolerance of 0.05 matched any output
    # value that rounds to 0.0; UPSTREAM_ISSUES U143)
    em = regextract(r"[eE][+-]?[0-9]+$", s)
    if em is not None:
        dec = max(0, dec - int(em[1:]))
    if s.startswith("-."):
        s2 = "-0." + s[2:]
    elif s.startswith("."):
        s2 = "0." + s[1:]
    else:
        s2 = s
    try:
        num: float | None = float(s2)
    except ValueError:
        num = None
    return num, dec, cens


def _norm_value(x: Any) -> dict[str, Any]:
    """Normalise a value string to a number.

    Port of ``R/match-reported.R::.norm_value()``. APA leading-dot ``".06"``
    -> 0.06; thousands separators stripped; ``"< .001"``/``"> .05"`` -> the
    bound with a censored flag. Returns ``{"num", "dec", "censored"}``:
    ``num`` is ``None`` (R ``NA``) when unparseable, ``dec`` the number of
    decimals as written.
    """
    s = "" if x is None else _chr(x)
    if s is None:  # NA
        return {"num": None, "dec": 0, "censored": ""}
    num, dec, cens = _norm_value_str(s)
    return {"num": num, "dec": dec, "censored": cens}


def _norm_interval(x: Any) -> dict[str, Any] | None:
    """Split a bracketed interval (``"[.16, .29]"``) into its two bounds.

    Port of ``R/match-reported.R::.norm_interval()``. Returns
    ``{"lo": <norm_value>, "hi": <norm_value>}``, or ``None`` when *x* is not
    bracket-shaped or does not contain exactly two separated numbers. Bounds
    are separated by a comma, a semicolon, or (with neither present) an
    en/em dash or hyphen between two numbers.
    """
    s = "" if x is None else _chr(x)
    if s is None:
        return None
    s = trimws(s)
    if not grepl(r"^\[.*\]$", s):
        return None
    inner = sub(r"^\[(.*)\]$", r"\1", s)
    if grepl("[,;]", inner):
        parts = strsplit(inner, "[,;]")
    else:
        parts = strsplit(inner, r"(?<=[0-9])\s*[-–—]\s*(?=[.0-9])", perl=True)
    parts = [trimws(p) for p in parts]
    if len(parts) != 2 or any(p == "" for p in parts):
        return None
    lo = _norm_value(parts[0])
    hi = _norm_value(parts[1])
    if lo["num"] is None or hi["num"] is None:
        return None
    return {"lo": lo, "hi": hi}


def _norm_df(x: Any) -> dict[str, Any] | None:
    """Parse the anchor's own parenthetical degrees of freedom.

    Port of ``R/match-reported.R::.norm_df()``. ``"(28)"`` ->
    ``{"df1": <norm_value>}``; ``"(2, 57)"`` -> ``{"df1": ..., "df2": ...}``;
    ``None`` for NA, a non-parenthesised string, or any other shape.
    """
    if x is None or _is_na(x):
        return None
    s = trimws(_paste_chr(x))
    if s == "":
        return None
    inner = sub(r"^\((.*)\)$", r"\1", s)
    if inner == s:
        return None  # not parenthesised at all
    parts = [trimws(p) for p in strsplit(inner, ",")]
    if len(parts) == 1:
        v = _norm_value(parts[0])
        if v["num"] is None:
            return None
        return {"df1": v}
    if len(parts) == 2:
        v1 = _norm_value(parts[0])
        v2 = _norm_value(parts[1])
        if v1["num"] is None or v2["num"] is None:
            return None
        return {"df1": v1, "df2": v2}
    return None  # more than two comma-separated parts


# ---------------------------------------------------------------------------
# statistic families
# ---------------------------------------------------------------------------

# (pattern(s), family) in dplyr::case_when() order; the first two cases test
# the name BEFORE the jamovi bracket suffix is stripped.
_FAMILY_CASES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    # (regexes, exact strings, family)
    ((r"^p$|^pval$|p-value|p value|pr\(>",), (), "p"),
    ((r"^df1$",), (), "df1"),
    ((r"^df2$",), (), "df2"),
    ((r"^df$",), (), "df"),
    ((r"^t($| |-|value|stat)", r"t-?test|student'?s t"), ("student's t",), "t"),
    ((r"^f($| |-|value| change)", r"\banova\b|f-?test"), (), "F"),
    (("chi|χ|x-squared|x²|goodness of fit",), (), "chisq"),
    ((r"^z($| |value)",), (), "z"),
    ((r"^w$|wilcoxon|mann-whitney",), (), "W"),
    ((r"^u$",), (), "U"),
    ((r"^q$",), (), "Q"),
    ((r"cohen'?s d|^d$|^ds$|^es$|effect size",), (), "d"),
    (("hedge",), (), "g"),
    ((r"mean difference|^md$",), (), "meandiff"),
    ((r"^median|^med$",), (), "median"),
    (("η|ηp²|(^|[^a-z])eta",), (), "eta2"),
    (("ω|omega",), (), "omega2"),
    (("odds",), (), "or"),
    ((r"^rb$|rank.?biserial|biserial",), (), "rb"),
    (("bf|bayes",), (), "bf"),
    ((r"^r$|correlation|pearson|^rs$",), (), "r"),
    (("^rho|^ρ|spearman",), (), "rho"),
    (("^β|beta|standardi|^std\\.?coef$",), (), "beta"),
    ((r"^b$|estimate|unstandardi",), (), "b"),
    ((r"^se$|std\. error|standard error",), (), "se"),
    ((r"^m$|^mean$",), (), "mean"),
    ((r"^sd$|std\. dev",), (), "sd"),
    ((r"^n$|^ns$|sample size",), (), "n"),
    (("^α|alpha|cronbach",), (), "alpha"),
    ((r"^r2m?c?$|^rsq$|r-?squared|r sq(uared)?$",), (), "rsq"),
    (
        (r"^lcl$|^ci_?lower$|^conf\.?low$|^ci\.lb$|^cilow$|^cil$|^ciles$",),
        (),
        "ci_lower",
    ),
    (
        (r"^ucl$|^ci_?upper$|^conf\.?high$|^ci\.ub$|^cihig?h?$|^ciu$|^ciues$",),
        (),
        "ci_upper",
    ),
    (("ci|lower|upper|interval",), (), "ci"),
)


@functools.lru_cache(maxsize=16384)
def _stat_family_str(name: str) -> str | None:
    from pytacheck.statout.stato_map import _stato_strip_variant

    n = _tolower(trimws(name))
    n = gsub("[[:space:]]+", " ", n)
    n_pre_strip = n
    n = _stato_strip_variant(n)
    if grepl(r"^stat\[(stud|welc)\]$", n_pre_strip):
        return "t"
    if n_pre_strip == "stat[mann]":
        return "W"
    for patterns, exact, fam in _FAMILY_CASES:
        if n in exact or any(grepl(p, n) for p in patterns):
            return fam
    return None


def _stat_family_one(name: Any) -> str | None:
    if name is None:
        name = ""
    s = _chr(name)
    if s is None:
        return None
    return _stat_family_str(s)


def _stat_family(name: Any) -> Any:
    """Coarse statistic family for a name (both the reported and output side).

    Port of ``R/match-reported.R::.stat_family()``. ``None`` (R ``NA``) when
    unrecognised -- used to drop junk reported components whose "name" is
    prose or a variable, and to require a type-consistent output cell when
    matching. jamovi's bracket suffixes are stripped first (``p[stud]`` is a
    p), except ``stat[stud]``/``stat[welc]`` (a t) and ``stat[mann]`` (a W).

    Vectorised: a scalar gives a scalar, a list/Series gives a list.
    """
    if name is None or isinstance(name, str) or not isinstance(name, Iterable):
        return _stat_family_one(name)
    return [_stat_family_one(v) for v in name]


# ---------------------------------------------------------------------------
# reported tests
# ---------------------------------------------------------------------------


def _comp(
    family: str, name: Any, value: float, dec: int, censored: str, **extra: Any
) -> dict[str, Any]:
    return {
        "family": family,
        "name": name,
        "value": value,
        "dec": dec,
        "censored": censored,
        **extra,
    }


def _expand_component(
    name: Any, value: Any, comp: Any, df: Any, extra: dict[str, Any], legacy: bool
) -> list[dict[str, Any]] | None:
    """One reported component -> its matchable component dicts (or ``None``)."""
    fam = _stat_family_one(name)
    if fam is None:
        return None  # drop junk components
    tag = {} if legacy else {"is_anchor": False, "pos": extra["pos"]}
    ivl = _norm_interval(value)
    if ivl is not None:
        lo, hi = ivl["lo"], ivl["hi"]
        return [
            _comp("ci_lower", "ci_lower", lo["num"], lo["dec"], "", **tag),
            _comp("ci_upper", "ci_upper", hi["num"], hi["dec"], "", **tag),
        ]
    nv = _norm_value(value)
    if nv["num"] is None:
        return None
    cens = nv["censored"]
    if legacy:
        cc = comp
        if cens == "" and cc is not None and cc in ("<", ">"):
            cens = cc
    elif cens == "" and comp in ("<", ">"):
        cens = comp
    main = _comp(fam, name, nv["num"], nv["dec"], cens, **extra)
    dfv = _norm_df(df)
    if dfv is None:
        return [main]
    if "df2" in dfv:
        d1, d2 = dfv["df1"], dfv["df2"]
        return [
            main,
            _comp("df1", "df1", d1["num"], d1["dec"], "", **tag),
            _comp("df2", "df2", d2["num"], d2["dec"], "", **tag),
        ]
    d1 = dfv["df1"]
    return [main, _comp("df", "df", d1["num"], d1["dec"], "", **tag)]


def _col_values(df: pd.DataFrame, col: str) -> list[Any] | None:
    if col not in df.columns:
        return None
    return [_scalar(v) for v in df[col].tolist()]


def _recompose_eq(eq: Any) -> list[dict[str, Any]]:
    """Recompose a paper's ``eq`` table into whole tests.

    Port of ``R/match-reported.R::.recompose_eq()``. One test per
    ``(text_id, grp_id)`` -- groups in R's ``split()`` order (sorted
    ``"<text_id>|<grp_id>"`` keys) -- with its components as
    ``family``/``name``/``value``/``dec``/``censored`` dicts. Components whose
    name is not a recognised statistic, or whose value is not a clean number,
    are dropped; a group of pure junk vanishes. A bracketed CI becomes two
    bound components and an anchor's own ``df`` extra df components.
    """
    if eq is None:
        return []
    if not isinstance(eq, pd.DataFrame):
        raise TypeError("the eq table must be a data frame")
    n = len(eq)
    if n == 0:
        return []
    tid = _col_values(eq, "text_id") or list(range(1, n + 1))
    gid = _col_values(eq, "grp_id") or list(range(1, n + 1))
    lhs = _col_values(eq, "lhs") or [None] * n
    rhs = _col_values(eq, "rhs") or [None] * n
    comp_col = _col_values(eq, "comp")
    df_col = _col_values(eq, "df")
    keys = [f"{_paste_chr(a)}|{_paste_chr(b)}" for a, b in zip(tid, gid, strict=True)]
    out: list[dict[str, Any]] = []
    for _key, rows in _split_order(keys):
        comps: list[dict[str, Any]] = []
        for i in rows:
            cc = None
            if comp_col is not None:
                c = _chr(comp_col[i])
                cc = None if c is None else trimws(c)
            got = _expand_component(
                lhs[i], rhs[i], cc, None if df_col is None else df_col[i], {}, legacy=True
            )
            if got:
                comps.extend(got)
        if comps:
            out.append({"text_id": tid[rows[0]], "grp_id": gid[rows[0]], "components": comps})
    return out


def _tests_from_extract(tt: Any) -> list[dict[str, Any]]:
    """Turn an :func:`~pytacheck.text.extract_tests.extract_tests` table into tests.

    Port of ``R/match-reported.R::.tests_from_extract()``. Components are
    already grouped per test there, so this only parses each value and keeps
    the provenance (``text_id``/``sentence``); every component also carries
    ``is_anchor`` and its sentence-wide ``pos`` for
    :func:`_regroup_by_evidence`.
    """
    from pytacheck.text.extract_tests import _is_anchor

    if tt is None or len(tt) == 0:
        return []
    n = len(tt)
    # no text_id column: R's tt$text_id[i] is NULL (see _NULL_ID)
    text_id: list[Any] = _col_values(tt, "text_id") or [[] for _ in range(n)]
    test_no = _col_values(tt, "test_no") or [None] * n
    sentence = _col_values(tt, "sentence") or [None] * n
    components = tt["components"].tolist()
    out: list[dict[str, Any]] = []
    for i in range(n):
        g = components[i]
        if g is None or (not isinstance(g, list | tuple) and _is_na(g)):
            g = []
        comps: list[dict[str, Any]] = []
        for gi, c in enumerate(g, start=1):
            c = c if isinstance(c, Mapping) else {}
            # c$sentence_pos %||% gi: an absent position falls back to the
            # local index, an NA one stays NA (NaN here)
            if "sentence_pos" not in c:
                pos: Any = gi
            else:
                pos = _scalar(c["sentence_pos"])
                pos = math.nan if pos is None else pos
            name = _scalar(c.get("name"))
            is_anch = bool(_is_anchor(name))
            cc_raw = c.get("comp")
            cc_s = "" if cc_raw is None else _chr(cc_raw)
            cc = None if cc_s is None else trimws(cc_s)
            got = _expand_component(
                name,
                _scalar(c.get("value")),
                cc,
                c.get("df"),
                {"is_anchor": is_anch, "pos": pos},
                legacy=False,
            )
            if got:
                comps.extend(got)
        if comps:
            out.append(
                {
                    "text_id": text_id[i],
                    "grp_id": test_no[i],
                    "sentence": sentence[i],
                    "components": comps,
                }
            )
    return out


# ---------------------------------------------------------------------------
# output sites
# ---------------------------------------------------------------------------


class _Sites:
    """The candidate output SITES (R's ``by_site`` list), with match indexes.

    ``rows[s]`` are row positions (into the kept output rows) of site ``s``;
    ``val``/``fam``/``sf``/``an``/``rl`` are the kept rows' columns. A site
    named ``""`` is a site like any other, and a value that cannot be compared
    (``Inf``) simply does not match; in R such sites and values make
    ``val_in()`` error and the whole call fail (UPSTREAM_ISSUES U142).
    """

    def __init__(
        self,
        names: list[str],
        rows: list[list[int]],
        val: list[float],
        fam: list[str | None],
        sf: list[Any],
        an: list[Any],
        rl: list[Any],
    ) -> None:
        self.names = names
        self.rows = rows
        self.val = val
        self.fam = fam
        self.sf = sf
        self.an = an
        self.rl = rl
        self._cache: dict[tuple[Any, ...], np.ndarray] = {}
        self._flat: dict[str | None, tuple[list[float], np.ndarray]] = {}
        self._rounded: dict[tuple[str | None, int], np.ndarray] = {}
        self._has: dict[str, np.ndarray] = {}
        self._minmax: dict[str | None, tuple[np.ndarray, np.ndarray]] = {}

    def __len__(self) -> int:
        return len(self.names)

    def first(self, s: int, what: str) -> Any:
        """``by_site[[s]]$<what>[1]``."""
        rows = self.rows[s]
        return getattr(self, what)[rows[0]] if rows else None

    def _flat_of(self, fam: str | None) -> tuple[list[float], np.ndarray]:
        """Values of family *fam* (``None``: every value) with their site index."""
        got = self._flat.get(fam)
        if got is None:
            vals: list[float] = []
            site: list[int] = []
            for s, rows in enumerate(self.rows):
                for r in rows:
                    if fam is None or self.fam[r] == fam:
                        vals.append(self.val[r])
                        site.append(s)
            got = (vals, np.asarray(site, dtype=np.intp))
            self._flat[fam] = got
        return got

    def _rounded_of(self, fam: str | None, dec: int) -> tuple[np.ndarray, np.ndarray]:
        vals, site = self._flat_of(fam)
        key = (fam, dec)
        rv = self._rounded.get(key)
        if rv is None:
            rv = np.asarray([r_round(v, dec) for v in vals], dtype=float)
            self._rounded[key] = rv
        return rv, site

    def has_family(self, fam: str) -> np.ndarray:
        got = self._has.get(fam)
        if got is None:
            got = np.zeros(len(self.names), dtype=bool)
            _, site = self._flat_of(fam)
            got[site] = True
            self._has[fam] = got
        return got

    def _exact(self, fam: str | None, dec: int, value: float, tol: float) -> np.ndarray:
        rv, site = self._rounded_of(fam, dec)
        out = np.zeros(len(self.names), dtype=bool)
        if len(rv):
            with np.errstate(invalid="ignore", over="ignore"):
                hit = np.abs(rv - value) < tol
            out[site[hit]] = True
        return out

    def _bounds(self, fam: str | None) -> tuple[np.ndarray, np.ndarray]:
        got = self._minmax.get(fam)
        if got is None:
            vals, site = self._flat_of(fam)
            lo = np.full(len(self.names), np.inf)
            hi = np.full(len(self.names), -np.inf)
            if vals:
                v = np.asarray(vals, dtype=float)
                np.minimum.at(lo, site, v)
                np.maximum.at(hi, site, v)
            got = (lo, hi)
            self._minmax[fam] = got
        return got

    @staticmethod
    def _key(comp: Mapping[str, Any]) -> tuple[Any, float, int, str]:
        return (comp.get("family"), float(comp["value"]), int(comp["dec"]), comp["censored"])

    def match(self, comp: Mapping[str, Any]) -> np.ndarray:
        """``val_in(by_site[[s]], comp)`` for every site ``s`` at once."""
        key = self._key(comp)
        got = self._cache.get(key)
        if got is not None:
            return got
        fam, value, dec, censored = key
        if censored != "":
            lo, hi = self._bounds(fam)
            got = lo < value if censored == "<" else hi > value
        else:
            # 10.0**dec overflows beyond 1e308 (a reported "p = 1e-310" has
            # 310 decimals, U143); the tolerance is then a subnormal or 0
            tol = 0.5 / (10.0**dec) if dec <= 308 else 0.5 * 10.0**-dec
            got = self._exact(fam, dec, value, tol)
            fallback = _FALLBACK_FAMILY.get(fam) if fam is not None else None
            if fallback is not None:
                got = got | (~self.has_family(fam) & self._exact(fallback, dec, value, tol))
        self._cache[key] = got
        return got

    def best_site_for(
        self, comp: Mapping[str, Any], used: set[int], exclude_used: bool = True
    ) -> int | None:
        """The first site (in ``by_site`` order), not already used, that matches."""
        for s in np.flatnonzero(self.match(comp)).tolist():
            if not (exclude_used and s in used):
                return int(s)
        return None

    def count(self, comps: Sequence[Mapping[str, Any]]) -> tuple[list[np.ndarray], np.ndarray]:
        """Per component match vectors, and the per-site count of matches."""
        matches = [self.match(c) for c in comps]
        counts = np.zeros(len(self.names), dtype=np.int64)
        for m in matches:
            counts += m
        return matches, counts


_FALLBACK_FAMILY = {"beta": "b", "d": "b", "df1": "df", "df2": "df"}


def _val_in(sites: _Sites, s: int, comp: Mapping[str, Any]) -> bool:
    """``val_in(cell, comp)``: does component *comp* match a value at site *s*?

    A censored component (``"p < .001"``) matches any value of its family
    beyond the bound; an exact one matches a value of its family that, rounded
    to the reported decimals, equals it -- with a same-site fallback to the
    generic ``b`` family for ``beta``/``d`` and to ``df`` for ``df1``/``df2``
    when the site has no cell of the reported family. An untyped component
    matches any value.
    """
    return bool(sites.match(comp)[s])


def _is_null_id(x: Any) -> bool:
    """Is a test's ``text_id`` zero-length (R ``NULL`` / ``integer(0)``)?

    A test built from an ``extract_tests()`` table without a ``text_id``
    column (``tt$text_id[i]`` is ``NULL``), or from a table without a
    ``table_id`` column (``-(NULL * 1000000L + ri)`` is ``integer(0)``),
    carries ``[]``: the evidence regrouping drops it with the NA ids, and a
    result row for it errors (``data.frame(text_id = NULL, ...)``).
    """
    return isinstance(x, list) and len(x) == 0


def _sites_share_variable(rl_a: Any, rl_b: Any) -> bool | None:
    """Do two output sites plausibly concern the SAME underlying variable?

    Port of ``R/match-reported.R::.sites_share_variable()``: compares the
    lower-cased alphanumeric TOKENS (longer than 3 characters) of two
    ``row_label`` values. ``None`` (R ``NA``) when either label is missing or
    empty or has no long token.
    """
    if _is_na(rl_a) or _is_na(rl_b):
        return None
    a = _paste_chr(rl_a)
    b = _paste_chr(rl_b)
    if a == "" or b == "":
        return None

    def tok(s: str) -> list[str]:
        return [t for t in strsplit(_tolower(s), "[^a-z0-9]+", perl=True) if len(t) > 3]

    ta = tok(a)
    tb = tok(b)
    if not ta or not tb:
        return None
    sb = set(tb)
    return any(t in sb for t in ta)


# ---------------------------------------------------------------------------
# evidence-driven regrouping
# ---------------------------------------------------------------------------


def _regroup_by_evidence(
    tests: list[dict[str, Any]],
    by_site: _Sites,
    val_in: Any = None,
    text_proximity: float | None = None,
) -> list[dict[str, Any]]:
    """Re-derive test boundaries from OUTPUT evidence.

    Port of ``R/match-reported.R::.regroup_by_evidence()``. Every test from
    one sentence (``text_id``) is pooled back together; each ANCHOR
    component then claims, at its own best (not yet used) output site, every
    other pooled component that also matches there -- at most one per family,
    the one closest to the anchor. Unclaimed components fall back to
    ``extract_tests()``'s own grouping. Each new group carries
    ``plausible_split``: ``True`` unless it lost an original test-mate to a
    group whose site shares no ``row_label`` token with its own.

    Tests without anchor tags (the ``.recompose_eq()`` path, table tests)
    are left untouched and appended at the end, as in R, and so are tests
    without a ``text_id`` (no sentence to pool them with): R's ``split()``
    drops those, losing the tests (UPSTREAM_ISSUES U142).
    """
    if not tests:
        return tests
    if val_in is None:
        val_in = _val_in

    def tagged(t: Mapping[str, Any]) -> bool:
        comps = t.get("components") or []
        return len(comps) > 0 and all(c.get("is_anchor") is not None for c in comps)

    def sentence_of(t: Mapping[str, Any]) -> str | None:
        return None if _is_null_id(t.get("text_id")) else _chr(t.get("text_id"))

    has_tags = [tagged(t) and sentence_of(t) is not None for t in tests]
    if not any(has_tags):
        return tests
    taggable = [t for t, h in zip(tests, has_tags, strict=True) if h]
    untouched = [t for t, h in zip(tests, has_tags, strict=True) if not h]

    tids = [sentence_of(t) for t in taggable]
    used_sites: set[int] = set()

    def best_site_for(comp: Mapping[str, Any], exclude_used: bool = False) -> int | None:
        return by_site.best_site_for(comp, used_sites, exclude_used)

    regrouped: list[dict[str, Any]] = []
    for _tid, idx in _split_order(tids):
        grp = [taggable[i] for i in idx]
        pool: list[dict[str, Any]] = [c for t in grp for c in t["components"]]
        if not pool:
            regrouped.extend(grp)
            continue
        is_anch = [c.get("is_anchor") is True for c in pool]
        if not any(is_anch):
            regrouped.extend(grp)
            continue
        orig_test: list[int] = []
        for ti, t in enumerate(grp):
            orig_test.extend([ti] * len(t["components"]))
        # each component is itself: R's match(g, pool) compares the deparsed
        # components, so a duplicated extract_tests() row collapsed onto the
        # first (UPSTREAM_ISSUES U143)
        pool_match = list(range(len(pool)))

        claimed = [False] * len(pool)
        new_groups: list[list[int]] = []
        new_sites: list[int | None] = []
        for ai in (j for j, a in enumerate(is_anch) if a):
            if claimed[ai]:
                continue
            site = best_site_for(pool[ai], exclude_used=True)
            if site is None:
                continue
            used_sites.add(site)
            candidate = []
            for j, c in enumerate(pool):
                if claimed[j]:
                    candidate.append(False)
                    continue
                if text_proximity is not None:
                    pj = c.get("pos")
                    pa = pool[ai].get("pos")
                    pj = math.inf if pj is None else pj
                    pa = -math.inf if pa is None else pa
                    gap = abs(pj - pa)
                    # an unknown (NA) position is not near (R: if (NA > x) errors)
                    if math.isnan(gap) or gap > text_proximity:
                        candidate.append(False)
                        continue
                candidate.append(val_in(by_site, site, c))
            candidate[ai] = True
            cand_idx = [j for j, ok in enumerate(candidate) if ok]
            cand_fam = [pool[j].get("family") for j in cand_idx]
            seen: set[Any] = set()
            dup_fams: list[Any] = []
            for f in cand_fam:
                if f is None:
                    continue
                if f in seen and f not in dup_fams:
                    dup_fams.append(f)
                seen.add(f)
            pa = pool[ai].get("pos")
            pa = -math.inf if pa is None else pa
            for fam in dup_fams:
                tied = [j for j, f in zip(cand_idx, cand_fam, strict=True) if f == fam]
                dist = [
                    abs((math.inf if pool[j].get("pos") is None else pool[j]["pos"]) - pa)
                    for j in tied
                ]
                # which.min() ignores NA; with every distance NA it is
                # integer(0) and tied[-integer(0)] releases nothing
                ok = [k for k, d in enumerate(dist) if not math.isnan(d)]
                if not ok:
                    continue
                win = min(ok, key=lambda k: dist[k])
                for k, j in enumerate(tied):
                    if k != win:
                        candidate[j] = False
            claim = [j for j, ok in enumerate(candidate) if ok]
            for j in claim:
                claimed[j] = True
            new_groups.append(claim)
            new_sites.append(site)
        leftover = {j for j, c in enumerate(claimed) if not c}
        if leftover:
            offset = 0
            for t in grp:
                n = len(t["components"])
                keep = [offset + k for k in range(n) if offset + k in leftover]
                if keep:
                    new_groups.append(keep)
                    new_sites.append(None)  # no output evidence for this piece
                offset += n

        group_match = [[pool_match[j] for j in g] for g in new_groups]
        group_sets = [set(m) for m in group_match]

        def site_rl(site: int | None) -> Any:
            return None if site is None else by_site.first(site, "rl")

        plausible: list[bool] = []
        for gi, g in enumerate(new_groups):
            orig = list(dict.fromkeys(orig_test[pool_match[j]] for j in g))
            mates_all = [m for oi in orig for m, o in enumerate(orig_test) if o == oi]
            mine = group_sets[gi]
            mates = list(dict.fromkeys(m for m in mates_all if m not in mine))
            if not mates:
                plausible.append(True)
                continue
            my_rl = site_rl(new_sites[gi])
            other_gi: list[int | None] = []
            for m in mates:
                where = next((k for k, gs in enumerate(group_sets) if m in gs), None)
                other_gi.append(where)
            shares = []
            for ogi in dict.fromkeys(other_gi):
                other_rl = None if ogi is None else site_rl(new_sites[ogi])
                shares.append(_sites_share_variable(my_rl, other_rl) is True)
            plausible.append(any(shares))

        first = grp[0]
        for gi, g in enumerate(new_groups):
            regrouped.append(
                {
                    "text_id": first.get("text_id"),
                    "grp_id": first.get("grp_id"),
                    "sentence": first.get("sentence"),
                    "components": [pool[j] for j in g],
                    "plausible_split": plausible[gi],
                }
            )
    return regrouped + untouched


# ---------------------------------------------------------------------------
# match_reported_output()
# ---------------------------------------------------------------------------

_RESULT_COLUMNS = {
    "text_id": "Int64",
    "grp_id": "Int64",
    "reported": "string",
    "n_components": "Int64",
    "n_matched": "Int64",
    "found": "boolean",
    "match_values": "string",
    "not_matched": "string",
    "source_file": "string",
    "analysis": "string",
    "confidence": "string",
    "plausible_split": "boolean",
}


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {k: pd.Series([], dtype=v) for k, v in _RESULT_COLUMNS.items()},
    )


def _num_series(values: list[Any]) -> pd.Series:
    """An integer column when every value is integral-typed, else double."""
    vals = [_scalar(v) for v in values]
    if all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in vals):
        return pd.Series(vals, dtype="Int64")
    if any(isinstance(v, str) for v in vals):  # a character id column stays character
        return pd.Series([_chr(v) for v in vals], dtype="string")
    return pd.Series([np.nan if v is None else float(v) for v in vals], dtype="float64")


def _fmt_comp(c: Mapping[str, Any]) -> str:
    """``sprintf("%s=%s%s", name, censored, format(value, trim = TRUE))``."""
    value = c["value"]
    num = "NA" if value is None else format_num(float(value), 7)
    return f"{_paste_chr(c.get('name'))}={c.get('censored') or ''}{num}"


def _output_long(output: Any) -> pd.DataFrame | None:
    if isinstance(output, pd.DataFrame):
        return output
    if output is None:
        return pd.DataFrame()
    items = output.values() if isinstance(output, Mapping) else output
    parts = []
    for s in items:
        if isinstance(s, str | bytes | int | float | np.generic):
            raise TypeError("$ operator is invalid for atomic vectors")
        if isinstance(s, Mapping):
            d = s.get("long")
        else:
            d = getattr(s, "long", None)
        if isinstance(d, pd.DataFrame) and len(d) > 0:
            parts.append(d)
    return bind_rows(parts) if parts else pd.DataFrame()


def _build_sites(out_long: pd.DataFrame) -> _Sites:
    n = len(out_long)
    sf = _col_values(out_long, "source_file") or [None] * n
    an = _col_values(out_long, "analysis") or ["(all)"] * n
    values = _col_values(out_long, "value") or [None] * n
    ovals = [_norm_value(x)["num"] for x in values]
    stat = _col_values(out_long, "statistic")
    ofam = _stat_family(stat) if stat is not None else [None] * n
    rl = _col_values(out_long, "row_label") or [None] * n
    keep = [i for i, v in enumerate(ovals) if v is not None]
    k_val = [float(ovals[i]) for i in keep]  # type: ignore[arg-type]
    k_fam = [ofam[i] for i in keep]
    k_sf = [sf[i] for i in keep]
    k_an = [an[i] for i in keep]
    k_rl = [rl[i] for i in keep]

    tid = _col_values(out_long, "test_id")
    if tid is not None:
        tid_keep = [_chr(tid[i]) for i in keep]
        site_keys: list[str | None] = list(tid_keep)
    else:
        tid_keep = None
        site_keys = [f"{_paste_chr(k_sf[j])}{_paste_chr(k_an[j])}" for j in range(len(keep))]
    names: list[str] = []
    rows: list[list[int]] = []
    for name, idx in _split_order(site_keys):
        names.append(name)
        rows.append(idx)

    # ADDITIONAL, BROADER sites: every row sharing a (model_ref, source_file)
    # -- several R statements describing the SAME fitted model.
    if "model_ref" in out_long.columns:
        mref = [_chr(v) for v in _col_values(out_long, "model_ref") or []]
        mref_k = [mref[i] for i in keep]
        model_keys: list[str | None] = [
            (f"\x02model\x02{m}\x02{_paste_chr(k_sf[j])}" if m is not None and m != "" else None)
            for j, m in enumerate(mref_k)
        ]
        for name, idx in _split_order(model_keys):
            names.append(name)
            rows.append(idx)

    # jamovi ANOVA "residuals" rows (the shared denominator df) are unioned
    # into each sibling effect row's site sharing the same test_id prefix.
    if tid_keep is not None:
        is_resid = [t is not None and t.endswith("_residuals") for t in tid_keep]
        if any(is_resid):
            resid_pos = [j for j, r in enumerate(is_resid) if r]
            resid_prefix = [tid_keep[j][: -len("_residuals")] for j in resid_pos]  # type: ignore[index]
            name_pos = {nm: i for i, nm in reversed(list(enumerate(names)))}
            rows_of: dict[str, list[int]] = {}
            for j, t in enumerate(tid_keep):
                if t is not None:
                    rows_of.setdefault(t, []).append(j)
            # the distinct non-residual ids, in first-appearance order
            sib_ids = [t for t in rows_of if not t.endswith("_residuals")]
            resid_by_pfx: dict[str, list[int]] = {}
            for j, p in zip(resid_pos, resid_prefix, strict=True):
                resid_by_pfx.setdefault(p, []).append(j)  # type: ignore[arg-type]
            for pfx, resid_rows in resid_by_pfx.items():
                # a row without a test_id is no sibling (R: startsWith(NA, ...)
                # adds an NA site, whose by_site[[NA]] fails the whole call;
                # UPSTREAM_ISSUES U142)
                start = pfx + "_"
                # unique(tid_keep[sib_rows]): first-appearance order
                for sib_tid in (t for t in sib_ids if t.startswith(start)):
                    site_rows = rows_of[sib_tid] + resid_rows
                    pos = name_pos.get(sib_tid)  # type: ignore[arg-type]
                    if pos is None:
                        names.append(sib_tid)  # type: ignore[arg-type]
                        rows.append(site_rows)
                        name_pos[sib_tid] = len(names) - 1  # type: ignore[index]
                    else:
                        rows[pos] = site_rows
    return _Sites(names, rows, k_val, k_fam, k_sf, k_an, k_rl)


def match_reported_output(
    paper: Any,
    output: Any,
    include_tables: bool = False,
    min_components: int = 1,
) -> pd.DataFrame:
    """Match reported statistical tests against the extracted analysis output.

    Port of ``R/match-reported.R::match_reported_output()``. Recomposes the
    statistics a paper REPORTS into whole tests, then checks whether each
    test's component values co-occur in a single analysis ("site") of the
    paper's extracted OUTPUT. A test is matched on its whole signature, not
    lone numbers, so a match means the reported result actually appears in
    the reproducible output rather than a value coinciding by chance.

    Parameters
    ----------
    paper:
        A paper object (:func:`~pytacheck.text.extract_tests.extract_tests`
        is run on it, falling back to its ``eq`` table), an ``extract_tests()``
        table, or a raw ``eq`` data frame (recomposed by sentence).
    output:
        The extracted output: a
        :func:`~pytacheck.statout.stat_output.stat_results_long` data frame,
        or the ``stat_output`` list a ``reproducibility_check`` result
        carries (its per-file ``long`` tables are combined).
    include_tables:
        If ``True`` and *paper* is a paper object, also build tests from
        statistics reported only in a results table's cells (see
        :func:`~pytacheck.statout.match_table._table_tests`).
    min_components:
        A test must have at least this many recognised components to be
        assessed (default 1, so a lone reported statistic is still checked).

    Returns
    -------
    pandas.DataFrame
        One row per reported test: ``text_id``, ``grp_id``, ``reported``,
        ``n_components``, ``n_matched``, ``found``, ``match_values``,
        ``not_matched``, ``source_file``, ``analysis``, ``confidence``
        (``"full"``/``"partial"``/``"none"``) and ``plausible_split``. The
        roll-up (``n_tests``, ``n_found``, ``n_full``, ``n_partial``,
        ``n_missing``, ``pct_found``) is in ``df.attrs["summary"]`` (R's
        ``attr(x, "summary")``).
    """
    from pytacheck.papers import Paper, PaperList

    if isinstance(paper, PaperList):
        # R fails in .recompose_eq() ("missing value where TRUE/FALSE
        # needed"); the result rows carry no paper id (UPSTREAM_ISSUES U142)
        raise TypeError(
            "match_reported_output() takes one paper (or an extract_tests() or eq "
            "table): call it for each paper of a paper list"
        )
    tests: list[dict[str, Any]] | None = None
    if isinstance(paper, pd.DataFrame) and {"test_no", "components"} <= set(paper.columns):
        tests = _tests_from_extract(paper)
    elif isinstance(paper, Paper):
        from pytacheck.text.extract_tests import extract_tests

        try:
            tt = extract_tests(paper)
        except Exception:  # R: tryCatch(extract_tests(paper), error = NULL)
            tt = None
        if tt is not None and len(tt):
            tests = _tests_from_extract(tt)
        else:
            tests = _recompose_eq(paper.get("eq"))
    if tests is None:
        tests = _recompose_eq(paper.get("eq") if isinstance(paper, Paper) else paper)

    # isTRUE(include_tables): a single logical TRUE only
    if isinstance(include_tables, bool | np.bool_) and include_tables and isinstance(paper, Paper):
        from pytacheck.statout.match_table import _table_tests

        try:
            tt_tab = _table_tests(paper)
        except Exception:  # R: tryCatch(.table_tests(paper), error = NULL)
            tt_tab = None
        if tt_tab:
            tests = tests + list(tt_tab)

    out_long = _output_long(output)
    if not tests or out_long is None or len(out_long) == 0:
        empty = _empty_result()
        empty.attrs["summary"] = {
            "n_tests": len(tests),
            "n_found": 0,
            "n_full": 0,
            "n_partial": 0,
            "n_missing": len(tests),
            "pct_found": 0.0,
        }
        return empty

    sites = _build_sites(out_long)
    if len(sites) > 0:
        tests = _regroup_by_evidence(tests, sites, _val_in)

    rows: dict[str, list[Any]] = {k: [] for k in _RESULT_COLUMNS}
    for tst in tests:
        # a test without a text_id (an extract_tests() table without the
        # column) has an NA text_id; R's data.frame(text_id = NULL, ...) fails
        # the whole call (UPSTREAM_ISSUES U142)
        if _is_null_id(tst.get("text_id")):
            tst = {**tst, "text_id": None}
        comps = tst["components"]
        nc = len(comps)
        plausible = tst.get("plausible_split")
        res: dict[str, Any] = {
            "text_id": tst.get("text_id"),
            "grp_id": tst.get("grp_id"),
            "reported": " ".join(_fmt_comp(c) for c in comps),
            "n_components": nc,
            "n_matched": 0,
            "found": False,
            "match_values": None,
            "not_matched": None,
            "source_file": None,
            "analysis": None,
            "confidence": "none",
            "plausible_split": None if plausible is None else bool(plausible),
        }
        if nc >= min_components and len(sites) > 0:
            # Best site = the analysis where the most components co-occur
            # (the first one on ties).
            matches, counts = sites.count(comps)
            best = int(np.argmax(counts))
            best_n = int(counts[best])
            res["n_matched"] = best_n
            if best_n > 0 and best_n >= min(2, nc):
                res["found"] = True
                res["source_file"] = _chr(sites.first(best, "sf"))
                res["analysis"] = _chr(sites.first(best, "an"))
                res["confidence"] = "full" if best_n == nc else "partial"
                matched = [bool(m[best]) for m in matches]
                hit = [c for c, ok in zip(comps, matched, strict=True) if ok]
                miss = [c for c, ok in zip(comps, matched, strict=True) if not ok]
                res["match_values"] = ", ".join(_fmt_comp(c) for c in hit) if hit else ""
                res["not_matched"] = ", ".join(_fmt_comp(c) for c in miss) if miss else ""
        for k in _RESULT_COLUMNS:
            rows[k].append(res[k])

    out = pd.DataFrame(
        {
            k: (
                _num_series(v)
                if k in ("text_id", "grp_id")
                else pd.Series([_scalar(x) for x in v], dtype=_RESULT_COLUMNS[k])
            )
            for k, v in rows.items()
        }
    )
    n = len(out)
    n_found = int(out["found"].sum())
    out.attrs["summary"] = {
        "n_tests": n,
        "n_found": n_found,
        "n_full": int((out["confidence"] == "full").sum()),
        "n_partial": int((out["confidence"] == "partial").sum()),
        "n_missing": int((~out["found"]).sum()),
        "pct_found": r_round(100 * n_found / n, 1) if n else None,
    }
    return out
