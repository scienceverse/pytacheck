"""``extract_tests()``: the statistical tests a paper reports, as matchable units.

Port of ``R/extract-tests.R``. :func:`~pytacheck.text.extract.extract_eq`
scrapes every ``name <op> value`` fragment and groups them by *sentence*;
this module turns those fragments into one row per reported TEST: components
whose name is a recognised statistic are kept (a figure's ``height = 4`` is
not evidence), a sentence is split into separate tests at repeated or new
anchors (``t``, ``F``, ``r``, ...), and the reporting sentence is kept so a
matched result can be traced back to its claim.

Each row's ``components`` cell is a list of dicts with the keys ``name``,
``comp``, ``value``, ``df`` and ``sentence_pos`` (R: a list of named lists;
``None`` is ``NA``, or ``NULL`` when the ``eq`` table lacks that column).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pandas as pd

from pytacheck.papers.model import Paper

__all__ = ["extract_tests"]

# Statistic names that ANCHOR a test (in .norm_stat_name() form).
_TEST_ANCHORS = (
    "t",
    "f",
    "z",
    "chi2",
    "chisq",
    "x2",
    "r",
    "rho",
    "tau",
    "u",
    "w",
    "h",
    "q",
    "beta",
    "b",
    "rr",
    "hr",
    "d",
    "cohens d",
    "cohens ds",
    "cohens dz",
    "hedges g",
    "hedges gs",
    "eta2",
    "etap2",
    "etap 2",
    "omega2",
    "delta",
    "deltam",
    "bf10",
    "bf01",
    "bf",
    "alpha",
    "cronbachs alpha",
    "m",
    "mean",
)

# Statistic names that ACCOMPANY an anchor within one reported test.
_TEST_SATELLITES = (
    "p",
    "df",
    "se",
    "sd",
    "m",
    "mean",
    "md",
    "ci",
    "lower",
    "upper",
    "n",
    "eta2",
    "etap2",
    "etap 2",
    "omega2",
    "cohens d",
    "cohens ds",
    "cohens dz",
    "hedges g",
    "hedges gs",
    "g",
    "delta",
    "deltam",
    "bf10",
    "bf01",
    "bf",
    "d",
    "r",
    "ci95",
    "95 ci",
    "alpha",
    "cronbachs alpha",
)

# Statistics in general, but never evidence for a reported test on their own.
_TEST_NONEVIDENCE = (
    "range",
    "mode",
    "or",
    "and",
    "min",
    "max",
    "sum",
    "count",
    "total",
    "height",
    "width",
    "age",
)

_ANCHORS = frozenset(_TEST_ANCHORS)
_SATELLITES = frozenset(_TEST_SATELLITES)
_NONEVIDENCE = frozenset(_TEST_NONEVIDENCE)

_FOLDS = (
    ("α", "alpha"),  # α (Cronbach's alpha)
    ("η", "eta"),  # η
    ("χ", "chi"),  # χ
    ("β", "beta"),  # β
    ("ρ", "rho"),  # ρ
    ("τ", "tau"),  # τ
    ("Δ", "delta"),  # Δ
    ("δ", "delta"),  # δ
    ("²", "2"),  # superscript 2
)
_KEEP = frozenset("abcdefghijklmnopqrstuvwxyz0123456789 ")
# component key -> eq column
_COMPONENT_COLS = (("name", "lhs"), ("comp", "comp"), ("value", "rhs"), ("df", "df"))
_R_WS = " \t\r\n"


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and x != x


def _tolower(s: str) -> str:
    """R ``tolower()``: one-to-one lower-casing (``towlower``)."""
    if s.isascii():
        return s.lower()
    out = []
    for c in s:
        low = c.lower()
        out.append(low if len(low) == 1 else low[0])
    return "".join(out)


def _norm_stat_name(x: Any) -> str | None:
    """Normalise a statistic name to a comparison key (port of ``.norm_stat_name()``).

    Lower-cased, Greek and superscripts folded to ASCII, punctuation dropped:
    ``"ηp²"`` -> ``"etap2"``, ``"Cohen's d"`` -> ``"cohens d"``, ``"χ²"`` ->
    ``"chi2"``. ``None`` (R ``NA``) stays ``None``.
    """
    if x is None:
        x = ""
    if _is_na(x):
        return None
    s = _tolower(str(x).strip(_R_WS))
    for greek, ascii_ in _FOLDS:
        if greek in s:
            s = s.replace(greek, ascii_)
    s = s.replace("’", "").replace("‘", "").replace("'", "")
    return " ".join("".join(c for c in s if c in _KEEP).split())


def _term_source(res: Any) -> str:
    if isinstance(res, Mapping):
        value = res.get("termSource", "")
    else:
        value = getattr(res, "termSource", "")
    return "" if value is None else str(value)


def _is_stat_name(nm: Any) -> bool:
    """Is *nm* a statistic we recognise? (port of ``.is_stat_name()``).

    Uses the anchor/satellite lists and, for other names, the same
    STATO/metacheck vocabulary as the analysis-output side
    (:func:`pytacheck.statout.stato_map.stato_type_column`).
    """
    key = _norm_stat_name(nm)
    if key == "":
        return False
    if key in _NONEVIDENCE:
        return False
    if key in _ANCHORS or key in _SATELLITES:
        return True
    from pytacheck.statout.stato_map import stato_type_column

    return _term_source(stato_type_column(key)) != ""


def _is_anchor(nm: Any) -> bool:
    """Port of ``.is_anchor()``: an anchor-eligible statistic name."""
    key = _norm_stat_name(nm)
    return key not in _NONEVIDENCE and key in _ANCHORS


def _is_primary_anchor(nm: Any) -> bool:
    """Port of ``.is_primary_anchor()``: an anchor that is never a satellite."""
    key = _norm_stat_name(nm)
    return _is_anchor(nm) and key not in _SATELLITES


def _split_into_tests(comps: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Split one sentence's components into separate tests (``.split_into_tests()``).

    A new test starts at a repeated name, at a primary anchor (``t``, ``F``,
    ``z``, ...) once the current test holds anything, at a dual-role anchor
    (``r``, ``d``, ``M``, ...) while no primary anchor is open, at a repeated
    CI, or at an ``estimate`` after the current test's CI. Groups without an
    anchor are dropped.
    """
    n = len(comps)
    if not n:
        return []
    is_anch = [_is_anchor(c.get("name")) for c in comps]
    if not any(is_anch):
        return []
    is_primary = [_is_primary_anchor(c.get("name")) for c in comps]
    nms = [_norm_stat_name(c.get("name")) for c in comps]
    is_ci = [nm in ("ci", "ci95", "95 ci") for nm in nms]
    is_est = [nm == "estimate" for nm in nms]

    starts: list[int] = []
    seen: list[str | None] = []
    ci_seen = anchor_seen = primary_seen = False
    group_start = 0
    for i in range(n):
        if is_est[i] and ci_seen:
            starts.append(i)
            seen = []
            ci_seen = anchor_seen = primary_seen = False
            group_start = i
            continue
        splits_here = is_anch[i] and (
            nms[i] in seen
            or (is_primary[i] and i > group_start)
            or (not is_primary[i] and anchor_seen and not primary_seen)
        )
        if splits_here:
            starts.append(i)
            seen = [nms[i]]
            ci_seen = is_ci[i]
            anchor_seen = True
            primary_seen = is_primary[i]
            group_start = i
            continue
        if is_anch[i]:
            anchor_seen = True
            if is_primary[i]:
                primary_seen = True
        if not is_anch[i] and not is_ci[i]:
            continue
        if nms[i] in seen:
            starts.append(i)
            seen = [nms[i]]
            ci_seen = is_ci[i]
            group_start = i
        else:
            seen.append(nms[i])
            if is_ci[i]:
                ci_seen = True
    bounds = sorted({0, *starts})
    ends = [b - 1 for b in bounds[1:]] + [n - 1]
    groups = [list(comps[s : e + 1]) for s, e in zip(bounds, ends, strict=True)]
    return [g for g in groups if any(_is_anchor(c.get("name")) for c in g)]


def _fmt(x: Any) -> str:
    """``sprintf("%s", x)`` of one value."""
    if _is_na(x):
        return "NA"
    from pytacheck._r.base import as_character

    return x if isinstance(x, str) else (as_character(x) or "NA")


def _render_test(g: Sequence[Mapping[str, Any]]) -> str:
    """Render one test as reported: ``"t(23) = 3.77, p = .001"`` (``.render_test()``)."""
    parts = []
    for k, c in enumerate(g, start=1):
        if "name" not in c or "value" not in c:  # sprintf() with a NULL gives character(0)
            raise ValueError(f"values must be length 1,\n but FUN(X[[{k}]]) result is length 0")
        df = c.get("df")
        dfp = "" if _is_na(df) or _fmt(df) == "" else _fmt(df)
        comp = c.get("comp", "=")  # an absent key is R's NULL
        parts.append(f"{_fmt(c.get('name'))}{dfp} {_fmt(comp)} {_fmt(c.get('value'))}")
    return ", ".join(parts)


def _empty_tests() -> pd.DataFrame:
    """The zero-row result (port of ``.empty_tests()``)."""
    return pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "test_no": pd.Series([], dtype="Int64"),
            "text_id": pd.Series([], dtype="Int64"),
            "paragraph_id": pd.Series([], dtype="Int64"),
            "section_id": pd.Series([], dtype="Int64"),
            "sentence": pd.Series([], dtype="string"),
            "anchor": pd.Series([], dtype="string"),
            "n_components": pd.Series([], dtype="Int64"),
            "reported": pd.Series([], dtype="string"),
            "components": pd.Series([], dtype=object),
        }
    )


def _scalar(v: Any) -> Any:
    """A table cell as a plain Python value (``None`` for missing)."""
    if _is_na(v):
        return None
    if hasattr(v, "item") and not isinstance(v, str):
        try:
            return v.item()
        except (ValueError, AttributeError):  # pragma: no cover
            return v
    return v


class _FirstRows:
    """``x[txt$text_id == tid][1]`` lookups on the text table."""

    def __init__(self, txt: pd.DataFrame | None) -> None:
        self.txt = txt
        self.first: dict[Any, int] = {}
        self.first_na: int | None = None
        if txt is None or "text_id" not in txt.columns:
            return
        for i, tid in enumerate(txt["text_id"].tolist()):
            if _is_na(tid):
                if self.first_na is None:
                    self.first_na = i
            elif tid not in self.first:
                self.first[tid] = i

    def row(self, tid: Any) -> int | None:
        k = self.first.get(tid)
        if self.first_na is not None and (k is None or self.first_na < k):
            return -1  # an NA comparison comes first: R selects NA
        return k

    def value(self, col: str, tid: Any) -> Any:
        if self.txt is None or col not in self.txt.columns or "text_id" not in self.txt.columns:
            return None
        k = self.row(tid)
        if k is None or k == -1:
            return None
        return _scalar(self.txt[col].iloc[k])

    def dtype(self, col: str) -> Any:
        if self.txt is None or col not in self.txt.columns or "text_id" not in self.txt.columns:
            return None
        return self.txt[col].dtype


def extract_tests(paper: Any) -> pd.DataFrame:
    """Extract the statistical tests a paper reports (port of ``extract_tests()``).

    Uses the paper's ``eq`` table (or :func:`extract_eq` when it is empty) and
    returns one row per reported test with ``paper_id``, ``test_no``
    (sequential within the paper), ``text_id``, ``paragraph_id``,
    ``section_id``, ``sentence`` (the reporting sentence), ``anchor`` (the
    normalised test statistic, e.g. ``"t"``), ``n_components``, ``reported``
    (the test rendered as text, e.g. ``"t(23) = 3.77, p = .001, d = 0.77"``)
    and ``components`` (a list of ``name``/``comp``/``value``/``df``/
    ``sentence_pos`` dicts).

    A paper list gives each paper's tests in turn (``test_no`` restarts per
    paper). metacheck grouped a list's sentences by ``text_id`` across papers,
    left the sentence metadata ``NA`` and repeated every test once per paper
    id (U10).
    """
    from pytacheck.papers.model import is_paper_list
    from pytacheck.papers.tables import paper_id as get_paper_id
    from pytacheck.text.extract import extract_eq

    if is_paper_list(paper) and not isinstance(paper, str | pd.DataFrame):
        from pytacheck._r.frames import bind_rows

        papers = paper.values() if isinstance(paper, Mapping) else paper
        parts = [extract_tests(p) for p in papers]
        parts = [t for t in parts if len(t) > 0]
        return bind_rows(parts).reset_index(drop=True) if parts else _empty_tests()

    is_paper = isinstance(paper, Paper)
    eq = paper.get("eq") if is_paper else None
    if not isinstance(eq, pd.DataFrame) or len(eq) == 0:
        try:
            eq = extract_eq(paper)
        except Exception:  # R: tryCatch(..., error = function(e) NULL)
            eq = None
    if eq is None or len(eq) == 0 or "text_id" not in eq.columns:
        return _empty_tests()

    txt = paper.get("text") if is_paper else None
    if not isinstance(txt, pd.DataFrame):
        txt = None
    try:
        pid: list[Any] = list(get_paper_id(paper))
    except Exception:  # R: tryCatch(..., error = NA)
        pid = [None]
    lookup = _FirstRows(txt)

    text_ids = [_scalar(v) for v in eq["text_id"].tolist()]
    groups: dict[Any, list[int]] = {}
    for i, tid in enumerate(text_ids):
        if tid is None:
            continue  # R compares with NA: no anchors can survive
        groups.setdefault(tid, []).append(i)

    # R: list(name = sub$lhs[i], comp = sub$comp[i], value = sub$rhs[i], df = sub$df[i]);
    # a missing eq column gives a NULL element: an absent key while splitting and
    # rendering (NULL and NA render differently), None in the returned components.
    fields = [(key, col) for key, col in _COMPONENT_COLS if col in eq.columns]
    values = {col: [_scalar(v) for v in eq[col].tolist()] for _, col in fields}
    lhs = values.get("lhs", [None] * len(eq))

    rows: list[dict[str, Any]] = []
    test_no = 0
    for tid, idx in groups.items():
        names = [lhs[i] for i in idx]
        if not any(_is_anchor(nm) for nm in names):
            continue  # no anchor survives the filter: .split_into_tests() gives nothing
        comps: list[dict[str, Any]] = []
        for pos, i in enumerate(idx, start=1):
            c = {key: values[col][i] for key, col in fields}
            c["sentence_pos"] = pos
            comps.append(c)
        comps = [c for c in comps if _is_stat_name(c["name"])]
        for g in _split_into_tests(comps):
            test_no += 1
            anchor = next(c for c in g if _is_anchor(c["name"]))
            sent = lookup.value("text", tid) if txt is not None else None
            base = {
                "test_no": test_no,
                "text_id": tid,
                "paragraph_id": lookup.value("paragraph_id", tid),
                "section_id": lookup.value("section_id", tid),
                "sentence": None if sent is None else str(sent),
                "anchor": _norm_stat_name(anchor.get("name") or ""),
                "n_components": len(g),
                "reported": _render_test(g),
            }
            if not pid:
                raise ValueError("arguments imply differing number of rows: 0, 1")
            # R keeps every element of list(name = , comp = , value = , df = ,
            # sentence_pos = ); an eq column that is missing gives NULL (None here)
            comps_out = [
                {
                    **{key: c.get(key) for key, _ in _COMPONENT_COLS},
                    "sentence_pos": c["sentence_pos"],
                }
                for c in g
            ]
            rows.extend({"paper_id": p, **base, "components": comps_out} for p in pid)

    if not rows:
        return _empty_tests()

    def series(name: str, dtype: Any) -> pd.Series:
        return cast(pd.Series, pd.Series([r[name] for r in rows], dtype=dtype))

    text_dtype = eq["text_id"].dtype
    out = pd.DataFrame(
        {
            "paper_id": series("paper_id", "string"),
            "test_no": series("test_no", "Int64"),
            "text_id": series("text_id", "Int64" if text_dtype == "object" else text_dtype),
            "paragraph_id": series("paragraph_id", lookup.dtype("paragraph_id") or object),
            "section_id": series("section_id", lookup.dtype("section_id") or object),
            "sentence": series("sentence", "string"),
            "anchor": series("anchor", "string"),
            "n_components": series("n_components", "Int64"),
            "reported": series("reported", "string"),
        }
    )
    out["components"] = pd.Series([r["components"] for r in rows], dtype=object)
    return out
