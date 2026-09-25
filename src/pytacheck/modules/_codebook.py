"""Helpers of the codebook_check module (port of ``inst/modules/codebook_check.R``).

Everything after ``codebook_check()`` itself in the R module file lives here:
the report tabset, the three optional LLM tiers (unstructured codebook
parsing, fuzzy column matching / label merging, scale naming), the rules-only
scale and task matchers against the ``scales`` / ``tasks`` dictionaries, the
prefix-group scale pipeline (shared stems, loop collapse, item / derived
split), the paradata ("export machinery") filter and the OpenScales OSD
export.

Representations: ``previews`` is a ``dict`` file name -> data frame (R's named
list; a data frame may carry duplicated column names, as R's does);
``scale_groups`` is a data frame with a ``columns`` list column; an OSD object
is an :class:`OsdDefinition` (a ``dict`` whose ``attrs`` hold R's attributes).
LLM calls go through :func:`_llm`, a hook tests replace.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import pandas as pd

from pytacheck._r import grepl, gsub, r_sort_key, strsplit, sub, trimws
from pytacheck._r.base import as_character, plural
from pytacheck.datacheck._checks_rvec import as_numeric_str, chr, median, tolower, toupper

__all__ = [
    "OsdDefinition",
    "codebook_file_tabset",
    "codebook_match_llm",
    "codebook_parse_llm",
]

#: ``.scale_min_items`` (R/data_check_helpers.R)
_SCALE_MIN_ITEMS = 3
#: ``.scale_group_min_chars``
_SCALE_GROUP_MIN_CHARS = 3


# -----------------------------------------------------------------------------
# small R-semantics helpers
# -----------------------------------------------------------------------------


def _na(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        return False
    if isinstance(x, float):
        return math.isnan(x)
    if x is pd.NA or x is pd.NaT:
        return True
    if isinstance(x, bool | int | list | tuple | dict):
        return False
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _ok(x: Any) -> bool:
    """``!is.na(x) && nzchar(x)`` for one value."""
    return not _na(x) and as_character(x) != ""


def _pstr(x: Any) -> str:
    """``paste()`` of one value: ``NA`` is ``"NA"``."""
    if _na(x):
        return "NA"
    return x if isinstance(x, str) else str(as_character(x))


def _key(a: Any, b: Any) -> str:
    """``paste(a, b, sep = "\\x01")``."""
    return f"{_pstr(a)}\x01{_pstr(b)}"


def _vals(df: pd.DataFrame | None, col: str) -> list[Any] | None:
    """A column's values as a list (``None`` for NA), or ``None`` when absent."""
    if df is None or col not in df.columns:
        return None
    s = df[col]
    if isinstance(s, pd.DataFrame):  # duplicated names: R's `$` takes the first
        s = s.iloc[:, 0]
    return [None if _na(v) else v for v in s.tolist()]


def _names(df: Any) -> list[str]:
    return [str(c) for c in df.columns] if isinstance(df, pd.DataFrame) else []


def _ncol(df: Any) -> int:
    return df.shape[1] if isinstance(df, pd.DataFrame) else 0


def _nrow(df: Any) -> int:
    return len(df) if isinstance(df, pd.DataFrame) else 0


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """``df[[name]]``: the first column called *name*."""
    for j, c in enumerate(df.columns):
        if str(c) == name:
            return df.iloc[:, j]
    raise KeyError(name)


def _unique(values: Iterable[Any]) -> list[Any]:
    """R ``unique()`` (NA kept once, first-appearance order)."""
    out: list[Any] = []
    seen: set[Any] = set()
    has_na = False
    for v in values:
        if _na(v):
            if not has_na:
                has_na = True
                out.append(None)
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _esc(x: str) -> str:
    """``gsub("([][{}().^$*+?|\\\\-])", "\\\\\\1", x)``: escape regex metacharacters."""
    return "".join("\\" + c if c in "][{}().^$*+?|\\-" else c for c in x)


def _num_via_chr(x: Any) -> list[float | None]:
    """``suppressWarnings(as.numeric(as.character(x)))``."""
    return [as_numeric_str(s) for s in chr(x)]


def _as_integer_str(s: Any) -> int | None:
    """``as.integer()`` of one string (truncation; NA when not a number / too big)."""
    if _na(s):
        return None
    v = as_numeric_str(str(s))
    if v is None or math.isnan(v) or math.isinf(v) or abs(v) >= 2**31:
        return None
    return int(v)


def _make_unique(names: Sequence[str]) -> list[str]:
    """R ``make.unique()``."""
    seen: dict[str, int] = {}
    taken = set(names)
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen[n] = 0
            out.append(n)
            continue
        k = seen[n]
        while True:
            k += 1
            cand = f"{n}.{k}"
            if cand not in taken:
                break
        seen[n] = k
        taken.add(cand)
        out.append(cand)
    return out


def _sub_df(df: pd.DataFrame, keep: Sequence[bool]) -> pd.DataFrame:
    """``df[, keep, drop = FALSE]``: R makes duplicated names unique."""
    idx = [j for j, k in enumerate(keep) if k]
    out = df.iloc[:, idx].copy()
    names = [str(c) for c in out.columns]
    if len(set(names)) < len(names):
        out.columns = _make_unique(names)
    return out


def _llm(**kwargs: Any) -> Any:
    """Call :func:`pytacheck.llm.core.llm` (a hook tests replace)."""
    from pytacheck.llm.core import llm

    return llm(**kwargs)


def _llm_try(**kwargs: Any) -> Any:
    """``tryCatch(llm(...), error = function(e) NULL)``."""
    try:
        return _llm(**kwargs)
    except Exception:
        return None


def _llm_use() -> bool:
    from pytacheck.llm.core import llm_use

    return bool(llm_use())


def _resolve_model(model: Any) -> Any:
    if model is not None:
        return model
    from pytacheck.llm.core import llm_model

    return llm_model()


def _strip(resp: Any, wrapper: str) -> Any:
    from pytacheck.datacheck.files import _strip_llm_wrapper

    return _strip_llm_wrapper(resp, wrapper)


def _resp_model(resp: Any) -> str | None:
    """``attr(resp, "llm")$model %||% NA``."""
    info = getattr(resp, "attrs", {}).get("llm") if resp is not None else None
    if isinstance(info, Mapping):
        m = info.get("model")
        return None if _na(m) else m
    return None


def _is_true(x: Any) -> bool:
    """R ``isTRUE()`` of one value: a non-NA logical TRUE."""
    return (isinstance(x, bool) or type(x).__name__ == "bool_") and bool(x)


def _first(x: Any) -> Any:
    """``x[[1]]`` of a response column (``NULL`` for an absent / empty column)."""
    if x is None or len(x) == 0:
        return None
    return x[0]


def _is_paper(x: Any) -> bool:
    from pytacheck.papers.model import is_paper

    return bool(is_paper(x))


def _is_paper_list(x: Any) -> bool:
    from pytacheck.papers.model import is_paper_list

    return bool(is_paper_list(x))


def _paper_text(p: Any) -> pd.DataFrame | None:
    t = getattr(p, "text", None)
    return t if isinstance(t, pd.DataFrame) else None


def _has_text(p: Any) -> bool:
    """``.is_paper(p) && !is.null(p$text) && nrow(p$text) > 0``."""
    if not _is_paper(p):
        return False
    t = _paper_text(p)
    return t is not None and len(t) > 0


def _paper_hay(p: Any) -> str:
    """``paste(as.character(p$text$text), collapse = " \\n ")`` ('' without text)."""
    if not _has_text(p):
        return ""
    t = _paper_text(p)
    assert t is not None
    vals = t["text"].tolist() if "text" in t.columns else []
    return " \n ".join(_pstr(v) for v in vals)


class _PaperForFile:
    """``paper_for_file()``: the paper owning a data file (per-file on a paper list)."""

    def __init__(self, paper: Any, labels_df: pd.DataFrame | None) -> None:
        self.paper = paper
        self.file_pid: dict[str, Any] = {}
        pids = _vals(labels_df, "paper_id")
        files = _vals(labels_df, "source_file")
        if pids is not None and files is not None:
            for f, pid in zip(files, pids, strict=True):
                k = _pstr(f)
                if k not in self.file_pid:  # a named vector: the first match
                    self.file_pid[k] = pid
        self._hay: dict[str, str] = {}

    def __call__(self, file: Any) -> Any:
        pid = self.file_pid.get(_pstr(file))
        if _na(pid) or not _is_paper_list(self.paper):
            return self.paper
        try:
            return self.paper[str(pid)]
        except (KeyError, IndexError, TypeError):
            return self.paper

    def hay(self, file: Any) -> str:
        k = _pstr(file)
        if k not in self._hay:
            self._hay[k] = _paper_hay(self(file))
        return self._hay[k]


def _text_search(paper: Any, pattern: Any, **kwargs: Any) -> pd.DataFrame | None:
    """``tryCatch(text_search(...), error = function(e) NULL)``."""
    from pytacheck.text.search import text_search

    try:
        res = text_search(paper, pattern=pattern, return_="sentence", ignore_case=True, **kwargs)
    except Exception:
        return None
    return res if isinstance(res, pd.DataFrame) else None


def _sentences(res: pd.DataFrame | None) -> list[str]:
    """``unique(trimws(as.character(sents$text)))`` without empty strings."""
    if res is None or len(res) == 0 or "text" not in res.columns:
        return []
    s = _unique(trimws(_pstr(v)) if not _na(v) else None for v in res["text"].tolist())
    return [x for x in s if x is None or x != ""]


# -----------------------------------------------------------------------------
# report tabset
# -----------------------------------------------------------------------------


def codebook_file_tabset(tbl: pd.DataFrame) -> list[Any] | None:
    """One column-documentation table per source file inside a Quarto tabset.

    Port of ``codebook_file_tabset()``. R pastes the blocks into one string;
    here they are report blocks (the tabset fences, a ``## file`` heading and
    a :func:`~pytacheck.report.scroll_table` per file), which joined with
    blank lines is R's text. ``None`` when *tbl* has no source files.
    """
    from pytacheck.report import scroll_table

    if "source_file" not in tbl.columns or len(tbl) == 0:
        return None
    rows: dict[Any, list[int]] = {}
    for i, f in enumerate(_vals(tbl, "source_file") or []):
        rows.setdefault(f, []).append(i)
    rest = [j for j, c in enumerate(tbl.columns) if c != "source_file"]
    blocks: list[Any] = ["::: {.panel-tabset}"]
    for f, idx in rows.items():
        sub_tbl = tbl.iloc[idx, rest].reset_index(drop=True)
        blocks += [f"## {_pstr(f)}", scroll_table(sub_tbl, maxrows=25)]
    blocks.append(":::")
    return blocks


# -----------------------------------------------------------------------------
# LLM tier 1: unstructured codebook text
# -----------------------------------------------------------------------------

_PARSE_PROMPT = " ".join(
    [
        "You are extracting variable definitions from a psychology research codebook or README.",
        "For each variable that has both a name and a verbatim description, return the exact",
        "variable name, the description copied verbatim (do not paraphrase), and the experiment",
        "or study heading it appears under (empty if none). Omit variables without a description.",
    ]
)


def codebook_parse_llm(
    lines: Sequence[str],
    src: str,
    model: Any = None,
    params: Mapping[str, Any] | None = None,
    max_chunks: int = 10,
) -> pd.DataFrame | None:
    """Parse unstructured codebook text (lines) into variable definitions with an LLM.

    Port of ``codebook_parse_llm()``: lines are sent in blocks of 100, at
    most *max_chunks* calls. Returns a data frame (``attrs["llm_model"]``
    names the model) or ``None``.
    """
    from pytacheck._r import bind_rows
    from pytacheck.datacheck.columns import _infer_group
    from pytacheck.llm.types import type_array, type_object, type_string

    lines = list(lines)
    chunks = [lines[i : i + 100] for i in range(0, len(lines), 100)]
    chunks = chunks[: max(0, min(len(chunks), int(max_chunks)))]
    type_spec = type_object(
        variables=type_array(
            type_object(
                variable_name=type_string("Exact variable name/code in the data file"),
                label=type_string("Verbatim description text from the codebook"),
                experiment_context=type_string(
                    "Experiment/study heading if stated, else empty", required=False
                ),
            )
        )
    )
    out: list[pd.DataFrame] = []
    model_used: str | None = None
    for ch in chunks:
        txt = "\n".join(_pstr(x) for x in ch)
        resp = _llm_try(
            text=pd.DataFrame({"text": [txt]}),
            text_col="text",
            system_prompt=_PARSE_PROMPT,
            type=type_spec,
            model=_resolve_model(model),
            params=dict(params or {}),
            phase="Parsing codebook",
        )
        resp = _strip(resp, "variables")
        if not isinstance(resp, pd.DataFrame) or len(resp) == 0:
            continue
        if model_used is None:
            model_used = _resp_model(resp)
        vn = _vals(resp, "variable_name") or []
        keep = [not _na(v) and trimws(_pstr(v)) != "" for v in vn]
        if not any(keep):
            continue
        labels = _vals(resp, "label")
        if labels is None:
            raise ValueError("arguments imply differing number of rows")
        if "experiment_context" in resp.columns:
            ec = [
                None if _na(v) else as_character(v)
                for v, k in zip(_vals(resp, "experiment_context") or [], keep, strict=True)
                if k
            ]
        else:
            ec = [None]
        n = sum(keep)
        group = _infer_group(ec)
        if len(group) == 1 and n != 1:
            group = group * n
        out.append(
            pd.DataFrame(
                {
                    "codebook_variable": pd.Series(
                        [as_character(v) for v, k in zip(vn, keep, strict=True) if k],
                        dtype="string",
                    ),
                    "label": pd.Series(
                        [
                            None if _na(v) else as_character(v)
                            for v, k in zip(labels, keep, strict=True)
                            if k
                        ],
                        dtype="string",
                    ),
                    "codebook_source": pd.Series([src] * n, dtype="string"),
                    "group": pd.Series(group, dtype="string"),
                    "parse_method": pd.Series(["llm"] * n, dtype="string"),
                }
            )
        )
    if not out:
        return None
    res = bind_rows(out)
    res.attrs["llm_model"] = model_used
    return res


# -----------------------------------------------------------------------------
# dictionaries and paper-text scanning
# -----------------------------------------------------------------------------

#: ``.scale_stopwords``
_SCALE_STOPWORDS = frozenset(
    [
        "scale",
        "agree",
        "disagree",
        "strongly",
        "never",
        "always",
        "sometimes",
        "often",
        "rarely",
        "please",
        "following",
        "statement",
        "question",
        "response",
        "really",
        "think",
        "feel",
        "would",
        "about",
        "which",
        "there",
        "their",
        "other",
        "because",
        "being",
    ]
)


def _scale_dictionary() -> pd.DataFrame:
    """``.scale_dictionary()``: the curated ``scales`` dataset."""
    try:
        from pytacheck.datacheck.scales import scales

        d = scales()
    except Exception:
        d = None
    if d is None:
        d = pd.DataFrame(
            {k: pd.Series([], dtype="string") for k in ("name", "acronym", "code", "source")}
        )
    return d


def _task_dictionary() -> pd.DataFrame:
    """``.task_dictionary()``: the ``tasks`` dataset."""
    try:
        from pytacheck.datacheck.tasks import tasks

        d = tasks()
    except Exception:
        d = None
    if d is None:
        d = pd.DataFrame(
            {k: pd.Series([], dtype="string") for k in ("name", "acronym", "code", "atlas_id")}
        )
    return d


@functools.lru_cache(maxsize=8192)
def _scale_text_pattern(name: str, acronym: str | None) -> str:
    """Regex matching an instrument in running text: its full name or its acronym.

    Port of ``.scale_text_pattern()``: the name's alphanumeric tokens joined
    by a separator class that includes both apostrophes, OR the acronym as a
    whole word (NA acronym: name only).
    """
    toks = [t for t in strsplit([name], "[^A-Za-z0-9]+")[0] if t != ""]
    name_pat = "[\\s._/'’-]*".join(_esc(t) for t in toks)
    parts = [name_pat]
    if acronym is not None and acronym != "":
        parts.append("\\b" + _esc(acronym) + "\\b")
    return "|".join(parts)


def _dict_patterns(dict_df: pd.DataFrame, with_acronym: bool = True) -> list[str]:
    names = _vals(dict_df, "name") or []
    acr = _vals(dict_df, "acronym") or [None] * len(names)
    return [
        _scale_text_pattern(_pstr(n), (None if _na(a) else str(a)) if with_acronym else None)
        for n, a in zip(names, acr, strict=True)
    ]


def _safe_grepl(pat: str, hay: str) -> bool:
    """``isTRUE(tryCatch(grepl(pat, hay, perl = TRUE, ignore.case = TRUE), ...))``."""
    try:
        return bool(grepl(pat, hay, ignore_case=True, perl=True))
    except Exception:
        return False


def _scan_paper_with_dict(paper: Any, dict_df: pd.DataFrame | None) -> list[str]:
    """Instruments of a dictionary named (by name or acronym) in a paper's text.

    Port of ``.scan_paper_with_dict()``.
    """
    if not _has_text(paper):
        return []
    if dict_df is None or len(dict_df) == 0:
        return []
    hay = _paper_hay(paper)
    names = _vals(dict_df, "name") or []
    pats = _dict_patterns(dict_df)
    hit = [n for n, p in zip(names, pats, strict=True) if _safe_grepl(p, hay)]
    return _unique(hit)


def _scan_paper_for_scales(paper: Any) -> list[str]:
    """Port of ``.scan_paper_for_scales()``: ``scales`` instruments named in the text."""
    return _scan_paper_with_dict(paper, _scale_dictionary())


def _scan_paper_for_tasks(paper: Any) -> list[str]:
    """Port of ``.scan_paper_for_tasks()``: ``tasks`` named in the text."""
    return _scan_paper_with_dict(paper, _task_dictionary())


def _scale_name_in_text(scale: str, sentences: Sequence[str]) -> bool:
    """Does a scale name (or its parenthesised acronym) appear in *sentences*?

    Port of ``.scale_name_in_text()``: full names match as fixed substrings,
    acronyms only at word boundaries.
    """
    if scale == "" or len(sentences) == 0:
        return False
    hay = tolower(" \n ".join(_pstr(s) for s in sentences)) or ""
    needles = _unique([tolower(scale), tolower(sub("\\s*\\(.*\\)\\s*$", "", scale))])
    needles = [n for n in needles if n is not None and len(n) >= 3]
    if any(n in hay for n in needles):
        return True
    from pytacheck._r import regextract_all

    acr = regextract_all("\\(([A-Z][A-Za-z0-9-]{1,})\\)", scale)
    acr_l = _unique(tolower(gsub("[()]", "", a)) for a in acr)
    acr_l = [a for a in acr_l if a is not None and len(a) >= 2]
    if not acr_l:
        return False
    pat = "\\b(" + "|".join(_esc(a) for a in acr_l) + ")\\b"
    return bool(grepl(pat, hay, perl=True))


def _scale_paper_context(
    paper: Any, prefixes: Sequence[Any], labels: Sequence[Any], max_sent: int = 6
) -> list[str]:
    """Paper sentences mentioning a block's prefixes or its distinctive item words.

    Port of ``.scale_paper_context()``.
    """
    terms: list[str] = []
    pfx = _unique(
        p
        for p in prefixes
        if not _na(p) and p != "" and len(p) >= 3 and not grepl("^(col|var|item|value|resp)$", p)
    )
    if pfx:
        terms += [f"\\b{_esc(p)}\\b" for p in pfx]
    lab_txt = [lb for lb in labels if _ok(lb)]
    if lab_txt:
        words = list(strsplit([tolower(" ".join(lab_txt)) or ""], "[^a-z]+")[0])
        words = [w for w in words if len(w) >= 5]
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        alpha = sorted(counts, key=r_sort_key)  # table(): sorted names
        ranked = sorted(alpha, key=lambda w: counts[w])  # sort(): stable, rarest first
        ranked = [w for w in ranked if w not in _SCALE_STOPWORDS][:6]
        if ranked:
            terms += [f"\\b{w}\\b" for w in ranked]
    if not terms:
        return []
    sents = _sentences(_text_search(paper, terms))
    return [s for s in sents if s is not None][:max_sent]


# -----------------------------------------------------------------------------
# scale-name propagation
# -----------------------------------------------------------------------------


def _scale_name_prefixes(names: Sequence[Any]) -> list[Any]:
    from pytacheck.datacheck.checks import _scale_name_prefix

    if not names:
        return []
    res = _scale_name_prefix(list(names))
    return list(res) if isinstance(res, list | tuple) else [res]


def _propagate_scale_by_prefix(labels_df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Give unnamed same-file, same-prefix sibling columns their block's scale.

    Port of ``.propagate_scale_by_prefix()``: only fills empty cells, never
    overwrites; the first named column of a prefix wins.
    """
    if (
        labels_df is None
        or len(labels_df) == 0
        or not all(c in labels_df.columns for c in ("source_file", "column_name", "scale"))
    ):
        return labels_df
    scale = _vals(labels_df, "scale") or []
    named = [_ok(s) for s in scale]
    if not any(named):
        return labels_df
    has_source = "scale_source" in labels_df.columns
    conf = _vals(labels_df, "scale_confidence")
    if conf is None:
        conf = [None] * len(scale)
    src = _vals(labels_df, "scale_source") if has_source else None
    files = _vals(labels_df, "source_file") or []
    pref = _scale_name_prefixes(_vals(labels_df, "column_name") or [])
    new_scale, new_conf = list(scale), list(conf)
    new_src = list(src) if src is not None else None
    changed = False
    for f in _unique(f for f, n in zip(files, named, strict=True) if n):
        # R: NA == f is NA, which selects nothing usable
        in_f = [False] * len(files) if f is None else [ff == f for ff in files]
        map_scale: dict[Any, Any] = {}
        map_conf: dict[Any, Any] = {}
        map_src: dict[Any, Any] = {}
        for i, (a, n) in enumerate(zip(in_f, named, strict=True)):
            if a and n and pref[i] not in map_scale:
                map_scale[pref[i]] = scale[i]
                map_conf[pref[i]] = conf[i]
                if src is not None:
                    map_src[pref[i]] = src[i]
        for i, (a, n) in enumerate(zip(in_f, named, strict=True)):
            if a and not n and pref[i] in map_scale:
                # R looks the prefix up by name (map_scale[pref]): an empty or NA
                # prefix never matches a name, so those rows are set to NA.
                hit = pref[i] is not None and pref[i] != ""
                new_scale[i] = map_scale[pref[i]] if hit else None
                new_conf[i] = map_conf[pref[i]] if hit else None
                if new_src is not None:
                    new_src[i] = map_src[pref[i]] if hit else None
                changed = True
    if not changed:
        return labels_df
    out = labels_df.copy()
    out["scale"] = pd.Series(new_scale, index=out.index, dtype="string")
    if "scale_confidence" in out.columns:
        out["scale_confidence"] = pd.Series(new_conf, index=out.index, dtype="string")
    if new_src is not None:
        out["scale_source"] = pd.Series(new_src, index=out.index, dtype="string")
    return out


# -----------------------------------------------------------------------------
# rules-only scale and task matchers
# -----------------------------------------------------------------------------


def _norm_pref(x: Any) -> str | None:
    """``tolower(gsub("[^a-z0-9]", "", tolower(x)))``."""
    if _na(x):
        return None
    return tolower(gsub("[^a-z0-9]", "", tolower(str(x)) or ""))


def _empty_scale_frame(n_detected: int | None = 0) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            k: pd.Series([], dtype="string")
            for k in ("source_file", "column_name", "scale", "confidence", "scale_source")
        }
    )
    if n_detected is not None:
        out.attrs["n_detected"] = n_detected
    return out


def _scale_rows(file: Any, cols: Sequence[Any], scale: Any, conf: Any, source: Any) -> pd.DataFrame:
    n = len(cols)
    return pd.DataFrame(
        {
            "source_file": pd.Series([file] * n, dtype="string"),
            "column_name": pd.Series(list(cols), dtype="string"),
            "scale": pd.Series([scale] * n, dtype="string"),
            "confidence": pd.Series([conf] * n, dtype="string"),
            "scale_source": pd.Series([source] * n, dtype="string"),
        }
    )


def _bind_scale_rows(out: list[pd.DataFrame], n_detected: int | None) -> pd.DataFrame:
    from pytacheck._r import bind_rows

    res = bind_rows(out) if out else _empty_scale_frame(None)
    res.attrs = {}
    if n_detected is not None:
        res.attrs["n_detected"] = n_detected
    return res


class _Wording:
    """Codebook wording (label + question) of data columns, keyed by file + column."""

    def __init__(self, labels_df: pd.DataFrame | None) -> None:
        self.index: dict[str, int] = {}
        files = _vals(labels_df, "source_file")
        cols = _vals(labels_df, "column_name")
        if files is not None and cols is not None:
            for i, (f, c) in enumerate(zip(files, cols, strict=True)):
                self.index.setdefault(_key(f, c), i)
        n = _nrow(labels_df)
        self.label = _vals(labels_df, "label") or [None] * n
        self.question = _vals(labels_df, "question")

    def rows(self, file: Any, cols: Sequence[Any]) -> list[int]:
        out = []
        for c in cols:
            i = self.index.get(_key(file, c))
            if i is not None:
                out.append(i)
        return out

    def parts(self, idx: Sequence[int]) -> list[Any]:
        """``c(labels_df$label[i], if (question) labels_df$question[i])``."""
        out = [self.label[i] for i in idx]
        if self.question is not None:
            out += [self.question[i] for i in idx]
        return out


def _identify_scales_rules(
    previews: Mapping[str, Any] | None, labels_df: pd.DataFrame | None, paper: Any
) -> pd.DataFrame:
    """Rules-only scale namer against the ``scales`` dictionary (no LLM).

    Port of ``.identify_scales_rules()``: a detected Likert block's name
    prefix proposes dictionary instruments by acronym; the codebook wording
    or the paper text must name the instrument in full to confirm it
    (``high``); a lone, distinctive (>= 4 character) acronym with no
    corroboration is named ``medium``; otherwise the matcher abstains.
    Returns per-column rows with ``attrs["n_detected"]``.
    """
    from pytacheck.datacheck.checks import _detect_scale_blocks
    from pytacheck.datacheck.columns import _scale_match_items, _scale_ref_code, _scale_reference

    if not previews:
        return _empty_scale_frame()
    d = _scale_dictionary()
    if len(d) == 0:
        return _empty_scale_frame()
    names = _vals(d, "name") or []
    acronyms = _vals(d, "acronym") or [None] * len(names)
    akey = [_norm_pref(a) for a in acronyms]
    akey_index: dict[str, list[int]] = {}
    for i, k in enumerate(akey):
        if k is not None:
            akey_index.setdefault(k, []).append(i)
    wording = _Wording(labels_df)
    pff = _PaperForFile(paper, labels_df)

    def cand_for_prefix(pfx: Any) -> list[int]:
        p = _norm_pref(pfx)
        if p is None or p == "":
            return []
        return akey_index.get(p, [])

    def wording_of(file: Any, cols: Sequence[Any]) -> str:
        idx = wording.rows(file, cols)
        if not idx:
            return ""
        return " ".join(_pstr(x) for x in wording.parts(idx) if _ok(x))

    name_pats: dict[int, str] = {}

    def corroborates(i: int, hay: str) -> bool:
        if hay == "":
            return False
        if i not in name_pats:
            name_pats[i] = _scale_text_pattern(_pstr(names[i]), None)
        return _safe_grepl(name_pats[i], hay)

    def n_items_matched(i: int, file: Any, cols: Sequence[Any]) -> int:
        ref = _scale_reference(_scale_ref_code(names[i], d))
        if ref is None:
            return 0
        w: dict[str, Any] = {}
        for c in cols:
            idx = wording.rows(file, [c])
            if not idx:
                w[c] = None
                continue
            parts = [x for x in wording.parts(idx) if _ok(x)]
            w[c] = parts[0] if parts else None
        m = _scale_match_items(w, ref)
        if m is None:
            return 0
        return int(m["ref_item_id"].notna().sum())

    out: list[pd.DataFrame] = []
    n_detected = 0
    for file, df in previews.items():
        if df is None or _ncol(df) < _SCALE_MIN_ITEMS:
            continue
        blocks = _detect_scale_blocks(df)
        if not blocks:
            continue
        n_detected += 1
        all_names = _names(df)
        for cols in blocks:
            nms = [all_names[j] for j in cols]
            prefix = _scale_name_prefixes([nms[0]])[0]
            cand = cand_for_prefix(prefix)
            if not cand:
                continue
            cb_hay = wording_of(file, nms)
            pp_hay = pff.hay(file)
            corr = [corroborates(i, cb_hay) or corroborates(i, pp_hay) for i in cand]
            pick: int | None = None
            conf: str | None = None
            n_corr = sum(corr)
            if n_corr == 1:
                pick = next(i for i, c in zip(cand, corr, strict=True) if c)
                conf = "high"
            elif n_corr > 1:
                cc = [i for i, c in zip(cand, corr, strict=True) if c]
                hits = [n_items_matched(i, file, nms) for i in cc]
                top = max(hits)
                if top >= 2 and hits.count(top) == 1:
                    pick = cc[hits.index(top)]
                    conf = "high"
            elif len(cand) == 1 and len(_norm_pref(acronyms[cand[0]]) or "NA") >= 4:
                pick = cand[0]
                conf = "medium"
            if pick is not None:
                out.append(_scale_rows(file, nms, names[pick], conf, "matched"))
    return _bind_scale_rows(out, n_detected)


_TASK_MARKER_RE = (
    "[._ -]?(rt|reaction[._ -]?time|response[._ -]?time|"
    "latency|acc|accuracy|correct|error|hit|miss)([._ -].*)?$"
)
_TASK_NAME_STOP = frozenset(["the", "a", "an", "of", "task", "test"])


def _first_tok(nm: Any) -> str:
    toks = strsplit([tolower(_pstr(nm)) or ""], "[^a-z0-9]+")[0]
    toks = [t for t in toks if t != "" and t not in _TASK_NAME_STOP]
    return toks[0] if toks else ""


def _identify_tasks_rules(
    previews: Mapping[str, Any] | None, labels_df: pd.DataFrame | None, paper: Any
) -> pd.DataFrame:
    """Rules-only behavioural-task namer against the ``tasks`` dictionary.

    Port of ``.identify_tasks_rules()``: a file with rt / accuracy data
    proposes a task by its column prefix (acronym or first name token); the
    paper text must name the task in full (``high``), a lone distinctive
    prefix is ``medium``, several corroborated variants abstain.
    """
    from pytacheck.datacheck.checks import (
        _detect_accuracy_blocks,
        _detect_task_columns,
        _is_task_data,
    )

    if not previews:
        return _empty_scale_frame()
    d = _task_dictionary()
    if len(d) == 0:
        return _empty_scale_frame()
    names = _vals(d, "name") or []
    acronyms = _vals(d, "acronym") or [None] * len(names)
    akey = [_norm_pref(a) for a in acronyms]
    nkey = [_norm_pref(_first_tok(n)) for n in names]
    pff = _PaperForFile(paper, labels_df)
    name_pats: dict[int, str] = {}

    def corroborates(i: int, file: Any) -> bool:
        hay = pff.hay(file)
        if hay == "":
            return False
        if i not in name_pats:
            name_pats[i] = _scale_text_pattern(_pstr(names[i]), None)
        return _safe_grepl(name_pats[i], hay)

    out: list[pd.DataFrame] = []
    n_detected = 0
    for file, df in previews.items():
        if df is None or not isinstance(df, pd.DataFrame) or df.shape[1] == 0:
            continue
        if not _is_task_data(df):
            continue
        n_detected += 1
        task_cols = _detect_task_columns(df)
        acc_blocks = _detect_accuracy_blocks(df)
        all_names = _names(df)
        groups: list[list[str]] = [[c] for c in (_vals(task_cols, "column_name") or [])]
        groups += [[all_names[j] for j in b] for b in acc_blocks]
        for cols in groups:
            pfx = _scale_name_prefixes([cols[0]])[0]
            pfx = sub(_TASK_MARKER_RE, "", pfx, perl=True)
            pfx = sub("[._ -]+$", "", pfx) if pfx is not None else None
            # gsub(): the anchored pattern matches at most once
            p = _norm_pref(pfx)
            if p is None or p == "" or len(p) < 3:
                continue
            cand = [i for i in range(len(names)) if akey[i] == p or nkey[i] == p]
            if not cand:
                continue
            corr = [corroborates(i, file) for i in cand]
            n_corr = sum(corr)
            pick: int | None = None
            conf: str | None = None
            if n_corr == 1:
                pick = next(i for i, c in zip(cand, corr, strict=True) if c)
                conf = "high"
            elif n_corr > 1:
                continue
            elif len(cand) == 1 and len(p) >= 4:
                pick = cand[0]
                conf = "medium"
            if pick is None:
                continue
            out.append(_scale_rows(file, cols, names[pick], conf, "task_matched"))
    return _bind_scale_rows(out, n_detected)


# -----------------------------------------------------------------------------
# prefix-group scale pipeline: stems, item / derived split
# -----------------------------------------------------------------------------

_ALNUM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def _scale_shared_stem(a: str, b: str) -> str:
    """Leading stem shared by two names, trailing separators trimmed.

    Port of ``.scale_shared_stem()``.
    """
    n = min(len(a), len(b))
    if n == 0:
        return ""
    k = 0
    for i in range(n):
        if a[i] != b[i]:
            break
        k = i + 1
    if k == 0:
        return ""
    s = a[:k]
    j = len(s)
    while j > 0 and s[j - 1] not in _ALNUM:
        j -= 1
    return s[:j]


def _scale_alpha_prefix(nm: str) -> str:
    """Leading run of a name up to the first digit, trailing separator trimmed, lowercased.

    Port of ``.scale_alpha_prefix()``.
    """
    cut = len(nm)
    for i, ch in enumerate(nm):
        if "0" <= ch <= "9":
            cut = i
            break
    p = nm[:cut].rstrip("._ -")
    return tolower(p) or ""


def _scale_loop_base(stem: str) -> str:
    """Instrument base of a stem with a trailing loop/stimulus index removed.

    Port of ``.scale_loop_base()`` (``POWER.PP170`` -> ``power``).
    """
    s = tolower(stem) or ""
    base = sub("[._ -]?[a-z]*[0-9]+$", "", s, perl=True)
    base = sub("[._ -]+$", "", base)
    n_letters = sum(1 for ch in base if "a" <= ch <= "z")
    return base if n_letters >= 2 else s


_NUM_ONLY_CHARS = frozenset("0123456789")


def _num_only(r: str) -> bool:
    """``grepl("^[._ -]?[0-9]*$", r)``."""
    if r[:1] in ("._ -") and r[:1] != "":
        r = r[1:]
    return all(ch in _NUM_ONLY_CHARS for ch in r)


def _scale_same_number_run(a: str, b: str) -> bool:
    """Do two adjacent columns differ only by item number? Port of ``.scale_same_number_run()``."""
    stem = _scale_shared_stem(a, b)
    if stem == "":
        return False
    ra = a[len(stem) :]
    rb = b[len(stem) :]
    return _num_only(ra) and _num_only(rb)


#: ``.AGG_TOKEN_RE``
_AGG_TOKEN_RE = "(sum|mean|total|score|avg|average|index|composite|check|count)$"  # noqa: S105


def _col_looks_like_total(st: Mapping[str, Any] | None) -> bool | None:
    """Port of ``.col_looks_like_total()``: non-integer or spread > 10 (NA without data)."""
    if st is None:
        return None
    return (not st["int"]) or (math.isfinite(st["rng"]) and st["rng"] > 10)


def _scale_split_items(
    cols: Sequence[str], df: pd.DataFrame | None = None, min_items: int = _SCALE_MIN_ITEMS
) -> dict[str, Any]:
    """Split a prefix block into genuine items and derived (sum / mean / check) columns.

    Port of ``.scale_split_items()``: returns ``{"items", "derived",
    "totals_only"}``. Reader-invented placeholder names are dropped first;
    a block that would fall below *min_items* items is kept whole.
    """
    from pytacheck.datacheck.checks import _is_placeholder_name

    cols = list(cols)
    ph = _is_placeholder_name(cols) if cols else []
    cols = [c for c, p in zip(cols, ph, strict=True) if not p]

    def keep_all(totals_only: bool = False) -> dict[str, Any]:
        return {"items": list(cols), "derived": [], "totals_only": totals_only}

    if len(cols) < min_items:
        return keep_all()
    suffix = sub("^.*?[._ -]", "", cols)
    ends_num = grepl("[0-9]$", cols)
    is_word = grepl("[A-Za-z]$", cols)
    agg_a = grepl(_AGG_TOKEN_RE, [tolower(s) for s in suffix])
    agg_b = grepl(_AGG_TOKEN_RE, [tolower(c) for c in cols])
    agg_token = [a or b for a, b in zip(agg_a, agg_b, strict=True)]
    numbered_block = sum(ends_num) / len(cols) >= 0.5
    name_flag = [
        ag or (numbered_block and w and not e)
        for ag, w, e in zip(agg_token, is_word, ends_num, strict=True)
    ]
    n = len(cols)
    value_flag = [False] * n
    abs_total: list[bool | None] = [None] * n
    if df is not None and all(c in set(_names(df)) for c in cols):
        stats: list[dict[str, Any] | None] = []
        for c in cols:
            v = [x for x in _num_via_chr(_column(df, c)) if x is not None and math.isfinite(x)]
            if not v:
                stats.append(None)
                continue
            stats.append({"rng": max(v) - min(v), "int": all(float(x).is_integer() for x in v)})
        abs_total = [_col_looks_like_total(st) for st in stats]
        item_idx = [i for i in range(n) if not name_flag[i]]
        if len(item_idx) >= 2:
            item_rngs = [stats[i]["rng"] for i in item_idx if stats[i] is not None]  # type: ignore[index]
            item_int = [stats[i]["int"] for i in item_idx if stats[i] is not None]  # type: ignore[index]
            med_rng = median(item_rngs) if item_rngs else math.nan
            items_are_integer = bool(item_int) and sum(item_int) / len(item_int) >= 0.5
            for j in range(n):
                st = stats[j]
                if st is None:
                    continue
                wider = math.isfinite(med_rng) and med_rng > 0 and st["rng"] > 1.5 * med_rng
                non_int = items_are_integer and not st["int"]
                if wider or non_int:
                    value_flag[j] = True
    rest = [i for i in range(n) if not name_flag[i]]
    totals_only = False
    if rest:
        at = [abs_total[i] for i in rest]
        if all(a is not None for a in at) and sum(bool(a) for a in at) / len(at) >= 0.8:
            totals_only = True
    derived_mask = [a or b for a, b in zip(name_flag, value_flag, strict=True)]
    if sum(not m for m in derived_mask) < min_items:
        return keep_all(totals_only=totals_only)
    return {
        "items": [c for c, m in zip(cols, derived_mask, strict=True) if not m],
        "derived": [c for c, m in zip(cols, derived_mask, strict=True) if m],
        "totals_only": totals_only,
    }


def _scale_group_index(labels_df: pd.DataFrame | None) -> dict[Any, list[tuple[Any, Any]]] | None:
    """File -> (column, block stem) pairs of ``labels_df$scale_group`` (``None`` without it)."""
    if (
        labels_df is None
        or "scale_group" not in labels_df.columns
        or not all(c in labels_df.columns for c in ("source_file", "column_name"))
    ):
        return None
    out: dict[Any, list[tuple[Any, Any]]] = {}
    for c, g, f in zip(
        _vals(labels_df, "column_name") or [],
        _vals(labels_df, "scale_group") or [],
        _vals(labels_df, "source_file") or [],
        strict=True,
    ):
        if _ok(g) and f is not None:
            out.setdefault(f, []).append((c, g))
    return out


def _scale_group_args(
    df: pd.DataFrame,
    file: Any,
    labels_df: pd.DataFrame | None,
    index: dict[Any, list[tuple[Any, Any]]] | None = None,
) -> dict[str, Any]:
    """QSF / Qualtrics arguments of :func:`_scale_prefix_groups` for one file.

    Port of ``.scale_group_args()``: ``scale_group`` is a list of (column,
    block stem) pairs from a parsed ``.qsf`` (R's named vector), ``qualtrics``
    whether the file is a Qualtrics export (only asked without a ``.qsf`` map).
    *index* is :func:`_scale_group_index` of *labels_df*, when already built.
    """
    from pytacheck.datacheck.checks import data_check_is_qualtrics

    if index is None:
        index = _scale_group_index(labels_df)
    sg = (index or {}).get(file) or None
    return {"scale_group": sg, "qualtrics": sg is None and bool(data_check_is_qualtrics(df))}


# -----------------------------------------------------------------------------
# paradata: columns nobody measured
# -----------------------------------------------------------------------------

#: ``.PARADATA_CHANNEL_RE``
_PARADATA_CHANNEL_RE = (
    "(^|[_. -])("
    "trial_index|stimulus_type|stimulus_description|response_time|"
    "response_validation_time|validation_time|response_option_index|"
    "response_description|first[_ ]?click|last[_ ]?click|page[_ ]?submit|"
    "click[_ ]?count|timing|timer|reaction[_ ]?time"
    ")([_. -]|$)"
)

#: ``.QUALTRICS_TIMER_RE``
_QUALTRICS_TIMER_RE = (
    "(first[_. -]?click|last[_. -]?click|page[_. -]?submit|click[_. -]?count)([_. -]|$)|"
    "tim(e|ing)[_. -]*(first|last|page|click)|timing([_. -]|$)"
)

#: ``.PARADATA_PLATFORM_COLS``
_PARADATA_PLATFORM_COLS: dict[str, tuple[str, ...]] = {
    "jspsych": (
        "success",
        "timeout",
        "failed_images",
        "failed_audio",
        "failed_video",
        "trial_index",
        "time_elapsed",
        "internal_node_id",
        "width",
        "height",
        "webaudio",
        "browser",
        "browser_version",
        "mobile",
        "os",
        "fullscreen",
        "vsync_rate",
        "webcam",
        "microphone",
        "view_history",
        "plugin_version",
    ),
    "inquisit": (
        "date",
        "time",
        "build",
        "pretrialpause",
        "posttrialpause",
        "windowcenter",
        "trialduration",
        "trialtimeout",
        "blocktimeout",
        "inwindow",
    ),
    "behaverse": ("stimulus_onset", "response_validation_time", "validation_time"),
}

#: ``.PARADATA_INQUISIT_STIM_RE``
_PARADATA_INQUISIT_STIM_RE = "^stimulus(number|vpos|hpos|onset)[0-9]+$"
#: ``.PARADATA_PSYCHOPY_RE``
_PARADATA_PSYCHOPY_RE = "[.](this(n|index|repn|trialn)|ran|started|stopped)$"
#: ``.PARADATA_PSYCHOPY_META_COLS``
_PARADATA_PSYCHOPY_META_COLS = frozenset(
    ["psychopyversion", "framerate", "expname", "date", "os", "session", "expstart"]
)


def _paradata_name(nm: Sequence[Any]) -> list[bool]:
    """Is each column name a paradata channel (from the name alone)? Port of ``.paradata_name()``."""
    x = [tolower(_pstr(n)) or "" for n in nm]
    a = grepl(_PARADATA_CHANNEL_RE, x, perl=True)
    b = grepl(_QUALTRICS_TIMER_RE, x, perl=True)
    return [(aa or bb) and "response_numeric" not in xx for aa, bb, xx in zip(a, b, x, strict=True)]


def _paradata_platform_col(col_names: Sequence[Any], format: str | None) -> list[bool]:
    """A trial-level platform's own bookkeeping columns. Port of ``.paradata_platform_col()``."""
    n = len(col_names)
    if n == 0 or format is None or _na(format) or format == "":
        return [False] * n
    deny = _PARADATA_PLATFORM_COLS.get(format)
    low = [tolower(trimws(_pstr(c))) or "" for c in col_names]
    deny_l = {tolower(d) for d in deny} if deny is not None else set()
    out = [lo in deny_l for lo in low] if deny is not None else [False] * n
    if format == "inquisit":
        stim = grepl(_PARADATA_INQUISIT_STIM_RE, low, perl=True)
        out = [o or s for o, s in zip(out, stim, strict=True)]
    if format == "psychopy":
        pp = grepl(_PARADATA_PSYCHOPY_RE, low, perl=True)
        out = [
            o or p or lo in _PARADATA_PSYCHOPY_META_COLS
            for o, p, lo in zip(out, pp, low, strict=True)
        ]
    return out


def _paradata_format(df: pd.DataFrame) -> str | None:
    """The trial-level platform of a data frame, if any. Port of ``.paradata_format()``."""
    from pytacheck.datacheck.checks import (
        data_check_is_behaverse,
        data_check_is_inquisit,
        data_check_is_jspsych,
        data_check_is_psychopy,
    )

    if data_check_is_jspsych(df):
        return "jspsych"
    if data_check_is_inquisit(df):
        return "inquisit"
    if data_check_is_psychopy(df):
        return "psychopy"
    if data_check_is_behaverse(df):
        return "behaverse"
    return None


def _paradata_col(df: pd.DataFrame) -> list[bool]:
    """Is each column of *df* paradata rather than a measurement? Port of ``.paradata_col()``."""
    from pytacheck.datacheck.checks import _qualtrics_is_display_order, _qualtrics_tag_cols

    nm = _names(df)
    if not nm:
        return []
    a = _paradata_name(nm)
    tags = _qualtrics_tag_cols(nm)
    do = _qualtrics_is_display_order(nm)
    txt = grepl("_TEXT$", nm, perl=True)
    out = [aa or not _na(t) or bool(d) or tt for aa, t, d, tt in zip(a, tags, do, txt, strict=True)]
    fmt = _paradata_format(df)
    if fmt is not None:
        plat = _paradata_platform_col(nm, fmt)
        out = [o or p for o, p in zip(out, plat, strict=True)]
    return out


#: ``.scale_is_nonanalytic_col()`` is ``.paradata_col()``
_scale_is_nonanalytic_col = _paradata_col

_MACHINERY_LABEL = "Export machinery / paradata column (not a measured variable)."


def _codebook_label_machinery(
    labels_df: pd.DataFrame | None, previews: Mapping[str, Any] | None
) -> pd.DataFrame | None:
    """Self-label still-unlabelled export-machinery columns (so no LLM ever sees them).

    Port of ``.codebook_label_machinery()``.
    """
    if labels_df is None or len(labels_df) == 0 or not previews:
        return labels_df
    if not all(c in labels_df.columns for c in ("source_file", "column_name", "label_status")):
        return labels_df
    files = _vals(labels_df, "source_file") or []
    cols = _vals(labels_df, "column_name") or []
    status = _vals(labels_df, "label_status") or []
    out: pd.DataFrame | None = None
    for f in _unique(files):
        if f is None or f not in previews:
            continue
        df = previews[f]
        if df is None or _ncol(df) == 0:
            continue
        mask = _scale_is_nonanalytic_col(df)
        machine = {n for n, m in zip(_names(df), mask, strict=True) if m}
        if not machine:
            continue
        rows = [
            ff == f and c in machine and st == "unlabelled"
            for ff, c, st in zip(files, cols, status, strict=True)
        ]
        if not any(rows):
            continue
        if out is None:
            out = labels_df.copy()
        sel = pd.Series(rows, index=out.index)
        for col, value in (
            ("label", _MACHINERY_LABEL),
            ("label_status", "labelled"),
            ("label_method", "paradata_rule"),
        ):
            if col not in out.columns:
                out[col] = pd.Series([None] * len(out), index=out.index, dtype="string")
            out.loc[sel, col] = value
        for i, r in enumerate(rows):
            if r:
                status[i] = "labelled"
        if "codebook_variable" in out.columns:
            out.loc[sel, "codebook_variable"] = out.loc[sel, "column_name"]
    return labels_df if out is None else out


def _codebook_duplicate_name_warnings(
    previews: Mapping[str, Any] | None, min_repeat: int = 2
) -> list[str]:
    """One warning per data file whose column names repeat.

    Port of ``.codebook_duplicate_name_warnings()``.
    """
    if not previews:
        return []
    out: list[str] = []
    for f, df in previews.items():
        if df is None or _ncol(df) == 0:
            continue
        counts: dict[str, int] = {}
        for n in _names(df):
            counts[n] = counts.get(n, 0) + 1
        tab = sorted(counts, key=r_sort_key)  # table(): sorted names
        dup = [n for n in tab if counts[n] >= min_repeat]
        if not dup:
            continue
        dup = sorted(dup, key=lambda n: -counts[n])  # sort(decreasing = TRUE), stable
        ex = dup[:5]
        k = len(dup)
        worst = ", ".join(f"`{e}`×{counts[e]:d}" for e in ex)
        out.append(
            f"**Duplicated column names in `{_pstr(f)}`.** {k:d} column name{plural(k)} "
            f"appear{'s' if k == 1 else ''} more than once (worst: {worst}), so "
            f"{sum(counts[n] for n in dup):d} of the file's {_ncol(df):d} columns share a "
            "name with another. This usually means a survey loop/merge exported repeated "
            "blocks without numbering each iteration; the repeats cannot be told apart and "
            "can only be documented once. Consider re-exporting with per-iteration column "
            "names."
        )
    return out


# -----------------------------------------------------------------------------
# prefix groups
# -----------------------------------------------------------------------------


def _scale_prefix_groups(
    df: pd.DataFrame,
    min_cols: int = _SCALE_MIN_ITEMS,
    min_chars: int = _SCALE_GROUP_MIN_CHARS,
    scale_group: Sequence[tuple[Any, Any]] | Mapping[str, Any] | None = None,
    qualtrics: bool = False,
) -> dict[str, dict[str, Any]]:
    """Group a data file's columns into scale blocks by their shared leading stem.

    Port of ``.scale_prefix_groups()``: machinery columns are dropped; runs
    of adjacent columns sharing a >= *min_chars* stem and differing only by
    item number form blocks, zero-padded runs merge by alphabetic prefix,
    loop/stimulus repetitions collapse, and claimed (``.qsf`` / Qualtrics)
    stems form blocks of their own. Returns an ordered ``dict`` key ->
    ``{"prefix", "display", "columns", "derived", "totals_only",
    "n_columns", "max_item"}``.
    """
    from pytacheck.datacheck.checks import _qualtrics_col_stem

    nonanalytic = _scale_is_nonanalytic_col(df)
    if any(nonanalytic):
        df = _sub_df(df, [not m for m in nonanalytic])
    all_nms = _names(df)
    if len(all_nms) < min_cols:
        return {}

    # claim: positional (R's named vector); a lookup by name reads the first
    first_pos: dict[str, int] = {}
    for j, n in enumerate(all_nms):
        first_pos.setdefault(n, j)
    claim: list[Any] = [None] * len(all_nms)
    if scale_group is not None:
        pairs = list(scale_group.items()) if isinstance(scale_group, Mapping) else list(scale_group)
        sg_first: dict[str, Any] = {}
        for nm, g in pairs:
            if nm is not None:
                sg_first.setdefault(str(nm), g)
        for nm in _unique(all_nms):
            if nm in sg_first and nm != "":
                claim[first_pos[nm]] = None if _na(sg_first[nm]) else as_character(sg_first[nm])
    elif qualtrics:
        claim = [_qualtrics_col_stem(n) for n in all_nms]
    counts: dict[str, int] = {}
    for c in claim:
        if c is not None:
            counts[c] = counts.get(c, 0) + 1
    claimed_stems = [s for s in sorted(counts, key=r_sort_key) if counts[s] >= min_cols]
    claimed = set(claimed_stems)
    claim = [c if c in claimed else None for c in claim]

    def claim_of(n: str) -> Any:
        if n == "":
            return None
        return claim[first_pos[n]]

    nms = [n for n in all_nms if claim_of(n) is None]

    # pass 1: maximal runs of adjacent columns sharing a stem
    runs: list[dict[str, Any]] = []
    run_cols: list[str] = []
    run_stem = ""

    def push_run() -> None:
        if run_cols:
            runs.append({"cols": list(run_cols), "stem": run_stem})

    for nm in nms:
        if nm == "":
            push_run()
            run_cols, run_stem = [], ""
            continue
        if not run_cols:
            run_cols, run_stem = [nm], nm
            continue
        stem = _scale_shared_stem(run_stem, nm)
        same_word = _scale_same_number_run(run_cols[-1], nm)
        if len(stem) >= min_chars and same_word:
            run_cols.append(nm)
            run_stem = stem
        else:
            push_run()
            run_cols, run_stem = [nm], nm
    push_run()

    # pass 2: merge adjacent runs sharing an alphabetic prefix (zero padding)
    merged: list[dict[str, Any]] = []
    for r in runs:
        ap = _scale_alpha_prefix(r["cols"][0])
        if merged:
            last = merged[-1]
            if ap != "" and ap == last["ap"]:
                last["cols"] = last["cols"] + r["cols"]
                continue
        merged.append({"cols": r["cols"], "stem": r["stem"], "ap": ap})

    # pass 2.5: collapse loop / stimulus repetitions
    if len(merged) > 1:
        bases = [_scale_loop_base(m["stem"]) for m in merged]
        collapsed: list[dict[str, Any]] = []
        for b in _unique(bases):
            grp = [m for m, bb in zip(merged, bases, strict=True) if bb == b]
            shortened = any(len(b) < len(tolower(m["stem"]) or "") for m in grp)
            if len(grp) >= 2 and shortened:
                cols = [c for m in grp for c in m["cols"]]
                disp = toupper(grp[0]["cols"][0][: len(b)]) or ""
                collapsed.append({"cols": cols, "stem": disp, "ap": _scale_alpha_prefix(disp)})
            else:
                collapsed.extend(grp)
        merged = collapsed

    out: dict[str, dict[str, Any]] = {}

    def emit(cols: list[str], stem: str, min_stem: int = min_chars) -> None:
        if len(cols) < min_cols or len(stem) < min_stem:
            return
        if not ("A" <= stem[:1] <= "Z" or "a" <= stem[:1] <= "z"):
            return
        if sum(1 for ch in stem if "A" <= ch <= "Z" or "a" <= ch <= "z") < 2:
            return
        split = _scale_split_items(cols, df, min_items=min_cols)
        items = split["items"]
        if len(items) < min_cols:
            return
        n_lead = len(sub("^([A-Za-z]+).*$", "\\1", stem))
        totals_only = bool(split["totals_only"]) and n_lead >= 3
        numbered = [c for c, g in zip(items, grepl("[0-9]+$", items), strict=True) if g]
        nums = (
            [_as_integer_str(x) for x in sub(".*?([0-9]+)$", "\\1", numbered)] if numbered else []
        )
        if nums:
            present = [x for x in nums if x is not None]
            max_item: Any = max(present) if present else -math.inf
        else:
            max_item = None
        key = tolower(stem) or ""
        base = key
        i = 2
        while key in out:
            key = f"{base}#{i}"
            i += 1
        out[key] = {
            "prefix": base,
            "display": stem,
            "columns": items,
            "derived": split["derived"],
            "totals_only": totals_only,
            "n_columns": len(items),
            "max_item": max_item,
        }

    for m in merged:
        ap = m["ap"]
        if ap != "" and len(ap) >= 2 and all("a" <= ch <= "z" or "A" <= ch <= "Z" for ch in ap):
            stem = toupper(m["cols"][0][: len(ap)]) or ""
            emit(m["cols"], stem, min_stem=2)
        else:
            emit(m["cols"], m["stem"])

    for stem in claimed_stems:
        cols = [n for n in all_nms if claim_of(n) is not None and claim_of(n) == stem]
        emit(cols, stem, min_stem=2)
    return out


# -----------------------------------------------------------------------------
# manuscript sentences and the text-only scale tier
# -----------------------------------------------------------------------------


def _scale_prefix_sentences(paper: Any, prefixes: Sequence[Any], max_sent: int = 40) -> list[str]:
    """Paper sentences mentioning an abbreviation as a whole word.

    Port of ``.scale_prefix_sentences()``.
    """
    if not _has_text(paper):
        return []
    pfx = _unique(p for p in prefixes if _na(p) or p != "")
    if not pfx:
        return []
    pats = [f"\\b{_esc(_pstr(p))}\\b" for p in pfx]
    s = _sentences(_text_search(paper, pats, perl=True))
    return [x for x in s if x is not None][:max_sent]


#: ``.SCALE_SIGNAL_WORDS``
_SCALE_SIGNAL_WORDS = (
    "scale",
    "scales",
    "subscale",
    "subscales",
    "questionnaire",
    "questionnaires",
    "inventory",
    "inventories",
    "measure",
    "measures",
    "instrument",
    "instruments",
    "test",
    "battery",
    "index",
    "items",
    "item",
)


def _scale_description_sentences(paper: Any, max_sent: int = 60) -> list[str]:
    """Paper sentences containing a scale-signalling word. Port of ``.scale_description_sentences()``."""
    if not _has_text(paper):
        return []
    signal = "\\b(" + "|".join(_SCALE_SIGNAL_WORDS) + ")\\b"
    s = _sentences(_text_search(paper, signal, perl=True))
    return [x for x in s if x is not None][:max_sent]


_TEXT_SCALES_PROMPT = " ".join(
    [
        "You are given sentences from a research paper. List every psychometric",
        "instrument (scale, questionnaire, inventory, test) that the AUTHORS",
        "ADMINISTERED TO PARTICIPANTS in this study.",
        "GUIDANCE:",
        "(1) Instrument names are FREQUENTLY written in Capitalised Words, often",
        "defined once as 'Full Name of Scale (ABBR)', and a parenthetical ALL-CAPS",
        "acronym is a strong signal. But capitalisation is NOT required: an instrument",
        "named in lower case ('the grit scale', 'a life-orientation test') or",
        "described without a formal proper name ('a seven-item measure of perceived",
        "stress') still counts. Judge from meaning, not capitalisation. Return each",
        "distinct instrument once, using the fullest name the text gives it.",
        "(2) An author-year citation ('Gentile et al., 2013') is the REFERENCE for a",
        "scale, NOT its name. Never return an author name as the scale_name.",
        "(3) Prefer instruments the authors ADMINISTERED TO PARTICIPANTS in this",
        "study. When it is unclear whether an instrument was administered here or",
        "only cited from prior work, INCLUDE it and mark confidence 'low'. Recall",
        "matters more than precision: it is better to list a scale that turns out not",
        "to have been administered than to miss one that was.",
        "(4) Use 'administered' to record whether the text shows the authors gave this",
        "instrument to their own participants: 'yes', 'unclear', or 'no'.",
    ]
)


def _identify_scales_text_llm(
    paper: Any, model: Any = None, params: Mapping[str, Any] | None = None
) -> pd.DataFrame | None:
    """Instruments the manuscript describes, named by an LLM from its sentences.

    Port of ``.identify_scales_text_llm()``: ``None`` without an LLM or a
    single paper; otherwise ``scale_name``, ``acronym``, ``n_items``,
    ``administered`` and ``confidence`` (one row per distinct name).
    """
    from pytacheck.llm.types import type_array, type_object, type_string

    if not _llm_use() or not _is_paper(paper):
        return None
    sents = _scale_description_sentences(paper, max_sent=30)
    if not sents:
        return None
    type_spec = type_object(
        scales=type_array(
            type_object(
                scale_name=type_string(
                    "Full name of the instrument, exactly as capitalised in the text."
                ),
                acronym=type_string("Its acronym as given in the text, or empty."),
                n_items=type_string("Number of items if stated, else empty."),
                administered=type_string(
                    "Did the authors administer this to their own participants? yes, unclear, or no."
                ),
                confidence=type_string("high, medium, or low."),
            )
        )
    )
    resp = _llm_try(
        text=pd.DataFrame({"text": ["\n".join(f"- {s}" for s in sents)]}),
        text_col="text",
        system_prompt=_TEXT_SCALES_PROMPT,
        type=type_spec,
        model=_resolve_model(model),
        params=dict(params or {}),
        phase="Identifying scales in the manuscript",
    )
    resp = _strip(resp, "scales")
    if not isinstance(resp, pd.DataFrame) or len(resp) == 0 or "scale_name" not in resp.columns:
        return None
    keep = [
        c
        for c in ("scale_name", "acronym", "n_items", "administered", "confidence")
        if c in resp.columns
    ]
    resp = resp.loc[:, keep].reset_index(drop=True)
    names = [None if _na(v) else trimws(as_character(v)) for v in resp["scale_name"].tolist()]
    resp["scale_name"] = pd.Series(names, dtype="string")
    rows = [i for i, n in enumerate(names) if n is None or n != ""]
    resp = resp.iloc[rows].reset_index(drop=True)
    seen: set[Any] = set()
    first: list[int] = []
    for i, n in enumerate(resp["scale_name"].tolist()):
        k = None if _na(n) else tolower(n)
        if k not in seen:
            seen.add(k)
            first.append(i)
    resp = resp.iloc[first].reset_index(drop=True)
    if len(resp) == 0:
        return None
    return resp


def _cell(ts: pd.DataFrame, col: str, i: int) -> Any:
    """``ts$col[i] %||% ""``: ``""`` for an absent column, ``None`` for NA."""
    if col not in ts.columns:
        return ""
    v = ts[col].iloc[i]
    return None if _na(v) else v


def _scale_text_report(text_scales: pd.DataFrame | None, matched: Sequence[Any] = ()) -> list[str]:
    """The "Scales in the manuscript" report section. Port of ``.scale_text_report()``."""
    if text_scales is None or len(text_scales) == 0:
        return []
    # a missing (NA) name, acronym or item count is simply absent (R's
    # nzchar(NA) is TRUE, so it listed "**NA**", "(NA; NA items)" and matched
    # the acronym "NA"); acronyms are matched literally as whole words (R pastes
    # them into a regex, so "C++" or "(X" stops the module)
    names = text_scales["scale_name"].tolist()
    keep = [i for i, n in enumerate(names) if not _na(n) and trimws(_pstr(n)) != ""]
    ts = text_scales.iloc[keep].reset_index(drop=True)
    if len(ts) == 0:
        return []

    def present(v: Any) -> str | None:
        return None if v is None or trimws(_pstr(v)) == "" else trimws(_pstr(v))

    if len(matched):
        m = [tolower(_pstr(x)) for x in matched if not _na(x)]
        m_set = set(m)
        hit_rows = []
        for i in range(len(ts)):
            name_hit = tolower(_pstr(_cell(ts, "scale_name", i))) in m_set
            a = present(_cell(ts, "acronym", i))
            acr_hit = a is not None and any(
                re.search(r"(?<!\w)" + re.escape(tolower(a)) + r"(?!\w)", x) for x in m
            )
            hit_rows.append(name_hit or acr_hit)
        ts = ts.iloc[[i for i, h in enumerate(hit_rows) if not h]].reset_index(drop=True)
    if len(ts) == 0:
        return []
    lines = []
    for i in range(len(ts)):
        bits = []
        acr = present(_cell(ts, "acronym", i))
        if acr is not None:
            bits.append(acr)
        nit = present(_cell(ts, "n_items", i))
        if nit is not None:
            bits.append(f"{nit} items")
        adm = _cell(ts, "administered", i)
        if adm is not None and tolower(trimws(_pstr(adm))) == "unclear":
            bits.append("possibly not administered here")
        extra = f" ({'; '.join(bits)})" if bits else ""
        lines.append(f"- **{_pstr(_cell(ts, 'scale_name', i))}**{extra}")
    n = len(ts)
    return [
        "#### Scales in the manuscript",
        f"The manuscript describes {n:d} instrument{plural(n)} whose item-level data we could "
        "not find. De-identified item-level responses are far more valuable to "
        "share than total scores: they let others check the scoring, the "
        "reverse-coding, and the reliability, and let the items be reused. "
        "Consider sharing the raw item responses for:",
        *lines,
        "(Detected from the manuscript text; an instrument mentioned only in "
        "passing may be listed here in error.)",
    ]


# -----------------------------------------------------------------------------
# stage 1: prefix groups named from the manuscript (one LLM call per file)
# -----------------------------------------------------------------------------

_PREFIX_PROMPT = " ".join(
    [
        "A dataset's columns are grouped by a leading abbreviation; each group is",
        "likely one questionnaire/scale. You are given, for each group, its",
        "abbreviation, how many columns it has, the highest item number, and (if any)",
        "the item wording; plus sentences from the paper (some mention the group's",
        "abbreviation, others describe an instrument), and an optional list of known",
        "instruments. For EACH group, give the instrument the abbreviation stands",
        "for, taken from the paper sentences.",
        "GUIDANCE:",
        "(1) A scale/questionnaire/test is almost always referred to by a proper name",
        "in Capitalised Words, e.g. 'Anthropomorphism Questionnaire', 'Teleological",
        "Beliefs Scale', 'Centrality of Religiosity Scale' — often defined once as",
        "'Full Name of Scale (ABBR)'. Return that Capitalised name, not a lowercase",
        "paraphrase.",
        "(2) An author-year citation like 'Neave et al., 2015' or 'Huber & Huber,",
        "2012' is the REFERENCE for a scale, NOT the scale's name. Never return an",
        "author name as the scale_name.",
        "(3) Use the counts to match: a group's column count (and highest item",
        "number) should line up with a count stated in the text (e.g. 'the AQ",
        "contains 20 items', or 'the scale has 98 statements across six categories of",
        "14, 14, 25, 10, 25, and 10'). Prefer the instrument whose stated size matches",
        "the group's size, even if the abbreviation differs from the column prefix.",
        "(4) If the text does not identify the group, return an empty scale_name for",
        "it — do NOT guess and do NOT force a known instrument. Return one entry per",
        "group.",
    ]
)

_NOT_A_NAME = frozenset(["unknown", "unclear", "na", "none"])


def _max_item_str(x: Any) -> str:
    """``as.character(max_item)`` (integer or ``-Inf``)."""
    if isinstance(x, float) and math.isinf(x):
        return "Inf" if x > 0 else "-Inf"
    return str(int(x))


def _identify_scales_prefix_llm(
    previews: Mapping[str, Any] | None,
    labels_df: pd.DataFrame | None,
    paper: Any,
    model: Any = None,
    params: Mapping[str, Any] | None = None,
    max_calls: int = 40,
) -> pd.DataFrame | None:
    """Every prefix group of every data file, named from the manuscript when an LLM is on.

    Port of ``.identify_scales_prefix_llm()``: one row per group
    (``source_file``, ``prefix``, ``scale``, ``confidence``,
    ``scale_source``, ``n_columns``, ``max_item``, ``totals_only``,
    ``columns``); unnamed groups are kept. ``attrs["llm_model"]`` names the
    model. ``None`` when no file has a group.
    """
    from pytacheck.llm.types import type_array, type_object, type_string

    if not previews:
        return None
    wording = _Wording(labels_df)
    have_keys = labels_df is not None and all(
        c in labels_df.columns for c in ("source_file", "column_name")
    )
    # sentences come from the paper owning each file (on a paper list R built
    # this lookup but then required `paper` itself to be one paper, so it sent
    # no manuscript sentences at all)
    pff = _PaperForFile(paper, labels_df)

    def wording_of(file: Any, cols: Sequence[Any]) -> list[Any]:
        if not have_keys:
            return []
        idx = wording.rows(file, cols)
        if not idx:
            return []
        return _unique(w for w in wording.parts(idx) if _ok(w))

    type_spec = type_object(
        scales=type_array(
            type_object(
                prefix=type_string("The column-name abbreviation, exactly as given."),
                scale_name=type_string(
                    "The instrument this abbreviation stands for, named or clearly described in the provided sentences (e.g. 'Breakup Distress Scale'). Empty if the text does not identify it."
                ),
                confidence=type_string("high, medium, or low."),
            )
        )
    )

    rows: list[dict[str, Any]] = []
    model_used: str | None = None
    n_calls = 0
    sg_index = _scale_group_index(labels_df)
    for file, df in previews.items():
        if df is None or _ncol(df) == 0:
            continue
        sga = _scale_group_args(df, file, labels_df, sg_index)
        groups = _scale_prefix_groups(
            df, scale_group=sga["scale_group"], qualtrics=sga["qualtrics"]
        )
        if not groups:
            continue

        resp = None
        if _llm_use() and n_calls < max_calls:
            n_calls += 1
            resp = _strip(
                _llm_try(
                    text=pd.DataFrame(
                        {"text": [_prefix_llm_text(file, groups, pff(file), wording_of)]}
                    ),
                    text_col="text",
                    system_prompt=_PREFIX_PROMPT,
                    type=type_spec,
                    model=_resolve_model(model),
                    params=dict(params or {}),
                    phase="Matching scales to text",
                ),
                "scales",
            )
            if isinstance(resp, pd.DataFrame) and model_used is None:
                model_used = _resp_model(resp)

        named: dict[str, Any] = dict.fromkeys(groups)
        conf: dict[str, Any] = dict.fromkeys(groups)
        if isinstance(resp, pd.DataFrame) and len(resp) and "prefix" in resp.columns:
            rk = [
                None if _na(v) else tolower(trimws(as_character(v)))
                for v in resp["prefix"].tolist()
            ]
            sn_col = _vals(resp, "scale_name")
            cf_col = _vals(resp, "confidence")
            for k in groups:
                j = [i for i, r in enumerate(rk) if r is not None and r == k]
                if not j:
                    continue
                sn = "" if sn_col is None else sn_col[j[0]]
                sn = None if _na(sn) else trimws(as_character(sn))
                if sn is not None and sn != "" and tolower(sn) not in _NOT_A_NAME:
                    named[k] = sn
                    cf = "medium" if cf_col is None else cf_col[j[0]]
                    cf = None if _na(cf) else tolower(trimws(as_character(cf)))
                    conf[k] = cf if cf in ("high", "medium", "low") else "medium"

        for k, gg in groups.items():
            rows.append(
                {
                    "source_file": file,
                    "prefix": gg["display"],
                    "scale": named[k],
                    "confidence": conf[k],
                    "scale_source": "manuscript" if named[k] is not None else None,
                    "n_columns": gg["n_columns"],
                    "max_item": gg["max_item"],
                    "totals_only": bool(gg["totals_only"]),
                    "columns": list(gg["columns"]),
                }
            )
        # (no early stop at the call cap: every file's groups are listed, named
        # or not; R breaks here even with the LLM off, so codebook_max_calls = 0
        # ended the inventory after the first file with groups)

    if not rows:
        return None
    res = pd.DataFrame(
        {
            "source_file": pd.Series([r["source_file"] for r in rows], dtype="string"),
            "prefix": pd.Series([r["prefix"] for r in rows], dtype="string"),
            "scale": pd.Series([r["scale"] for r in rows], dtype="string"),
            "confidence": pd.Series([r["confidence"] for r in rows], dtype="string"),
            "scale_source": pd.Series([r["scale_source"] for r in rows], dtype="string"),
            "n_columns": pd.Series([r["n_columns"] for r in rows], dtype="Int64"),
            "max_item": pd.Series([r["max_item"] for r in rows], dtype=object),
            "totals_only": pd.Series([r["totals_only"] for r in rows], dtype="boolean"),
            "columns": pd.Series([r["columns"] for r in rows], dtype=object),
        }
    )
    res.attrs["llm_model"] = model_used
    return res


def _prefix_llm_text(
    file: Any,
    groups: Mapping[str, Mapping[str, Any]],
    paper: Any,
    wording_of: Callable[[Any, Sequence[Any]], list[Any]],
) -> str:
    """The text sent to the stage-1 LLM for one data file."""
    prefixes = [g["display"] for g in groups.values()]
    sents = (
        _unique([*_scale_prefix_sentences(paper, prefixes), *_scale_description_sentences(paper)])
        if _is_paper(paper)
        else []
    )
    d = _scale_dictionary()
    acr = _vals(d, "acronym") or []
    dnames = _vals(d, "name") or []
    up_pfx = {toupper(p) for p in prefixes}
    hints_rows = [
        (a, n)
        for a, n in zip(acr, dnames, strict=True)
        if _ok(a) and toupper(gsub("[^A-Za-z0-9]", "", a)) in up_pfx
    ]
    hints = "; ".join(f"{_pstr(a)} = {_pstr(n)}" for a, n in hints_rows)
    grp_txt = []
    for gg in groups.values():
        wd = wording_of(file, gg["columns"])
        mi = gg["max_item"]
        grp_txt.append(
            f"- {gg['display']}: {gg['n_columns']:d} columns, highest item number "
            f"{'unknown' if mi is None else _max_item_str(mi)}"
            + (f"; item wording: {' | '.join(_pstr(w) for w in wd[:6])}" if wd else "")
        )
    sents = sents[:60]
    return (
        "Column groups in this data file:\n"
        + "\n".join(grp_txt)
        + (
            "\n\nSentences from the paper (abbreviation mentions and instrument descriptions):\n"
            + "\n".join(f"- {s}" for s in sents)
            if sents
            else "\n\n(No relevant paper sentences found.)"
        )
        + (f"\n\nKnown instruments (hints only, confirm from the text): {hints}" if hints else "")
    )


# -----------------------------------------------------------------------------
# OpenScales OSD export
# -----------------------------------------------------------------------------


class OsdDefinition(dict):  # type: ignore[type-arg]
    """An OpenScales OSD object (R list) with R's attributes in :attr:`attrs`.

    ``attrs`` holds ``write`` (whether a file writer should write it),
    ``code``, ``orphan_total``, ``scale`` and ``dedup_key``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.attrs: dict[str, Any] = {}


def _decoded_names(vl: Any) -> list[Any] | None:
    """``names()`` of a decoded value-label set (``None`` for an unnamed array)."""
    if isinstance(vl, Mapping):
        return list(vl.keys())
    if isinstance(vl, pd.Series):
        return list(vl.index)
    return None


def _decoded_values(vl: Any) -> list[Any]:
    if isinstance(vl, Mapping):
        return list(vl.values())
    if isinstance(vl, pd.Series):
        return vl.tolist()
    return list(vl)


class _LikertLookup:
    """The indexes :func:`_osd_likert_options` searches, built once per run."""

    def __init__(self, columns_df: pd.DataFrame | None, labels_df: pd.DataFrame | None) -> None:
        self.lk: dict[str, int] | None = None
        if (
            labels_df is not None
            and len(labels_df) > 0
            and all(c in labels_df.columns for c in ("source_file", "column_name"))
            and "value_labels" in labels_df.columns
        ):
            self.lk = {}
            files = _vals(labels_df, "source_file") or []
            cols = _vals(labels_df, "column_name") or []
            for i, (f, c) in enumerate(zip(files, cols, strict=True)):
                self.lk.setdefault(_key(f, c), i)
            self.vls = _vals(labels_df, "value_labels") or []
            self.mvs = _vals(labels_df, "missing_values")
        self.ck: dict[str, list[int]] | None = None
        if (
            columns_df is not None
            and len(columns_df) > 0
            and all(c in columns_df.columns for c in ("source_file", "column_name", "min", "max"))
        ):
            self.ck = {}
            files = _vals(columns_df, "source_file") or []
            cols = _vals(columns_df, "column_name") or []
            for i, (f, c) in enumerate(zip(files, cols, strict=True)):
                self.ck.setdefault(_key(f, c), []).append(i)
            self.mins = _vals(columns_df, "min") or []
            self.maxs = _vals(columns_df, "max") or []

    def __call__(self, cols: Sequence[Any], source_file: Any) -> dict[str, Any] | None:
        from pytacheck.datacheck.columns import _decode_value_labels

        want = [_key(source_file, c) for c in cols]
        if self.lk is not None:
            for w in want:
                i = self.lk.get(w)
                if i is None:
                    continue
                vl = _decode_value_labels(self.vls[i])
                if vl is None or len(vl) == 0:
                    continue
                names = _decoded_names(vl)
                if names is None:
                    continue  # as.integer(names(<unnamed list>)) is integer(0): no codes
                values = _decoded_values(vl)
                codes = [_as_integer_str(nm) for nm in names]
                miss = _decode_value_labels(self.mvs[i] if self.mvs is not None else None)
                if miss is not None:
                    miss_names = set(_decoded_names(miss) or [])
                    drop = [nm in miss_names for nm in names]
                    codes = [c for c, d in zip(codes, drop, strict=True) if not d]
                    values = [v for v, d in zip(values, drop, strict=True) if not d]
                keep = [c is not None for c in codes]
                codes = [c for c, k in zip(codes, keep, strict=True) if k]
                values = [v for v, k in zip(values, keep, strict=True) if k]
                if len(codes) >= 2:
                    return {
                        "points": len(codes),
                        "min": min(codes),
                        "max": max(codes),
                        "labels": [None if _na(v) else as_character(v) for v in values],
                        "order": "ascending",
                        "source": "codebook",
                    }
        if self.ck is not None:
            idx = sorted({i for w in set(want) for i in self.ck.get(w, ())})
            if idx:
                mn_v = [float(self.mins[i]) for i in idx if not _na(self.mins[i])]
                mx_v = [float(self.maxs[i]) for i in idx if not _na(self.maxs[i])]
                mn = median(mn_v) if mn_v else math.nan
                mx = median(mx_v) if mx_v else math.nan
                if (
                    math.isfinite(mn)
                    and math.isfinite(mx)
                    and mx > mn
                    and float(mn).is_integer()
                    and float(mx).is_integer()
                    and (mx - mn) <= 12
                ):
                    return {
                        "points": int(mx - mn + 1),
                        "min": mn,
                        "max": mx,
                        "order": "ascending",
                        "source": "observed",
                    }
        return None


def _osd_likert_options(
    cols: Sequence[Any],
    source_file: Any,
    columns_df: pd.DataFrame | None,
    labels_df: pd.DataFrame | None,
) -> dict[str, Any] | None:
    """Response scale of a block, codebook first, else observed min / max.

    Port of ``.osd_likert_options()``: the codebook's value labels of the
    first documented column (declared missing codes dropped), else the
    median observed min / max from data_check's column statistics when that
    is a small integer span; ``None`` otherwise.
    """
    return _LikertLookup(columns_df, labels_df)(cols, source_file)


_TOTAL_NOTE = (
    "Detected as the total/average score for this scale, but no item-level columns "
    "were found. The individual items may not be shared, or are labelled differently. "
    "Consider sharing item-level data or labelling items clearly so the scale can be verified."
)


def _scales_to_osd(
    scale_groups: pd.DataFrame | None,
    columns_df: pd.DataFrame | None = None,
    labels_df: pd.DataFrame | None = None,
    dict_df: pd.DataFrame | None = None,
) -> list[OsdDefinition]:
    """The scale-group inventory in the OpenScales OSD structure.

    Port of ``.scales_to_osd()``: one object per (file, group), with the
    response scale (codebook first), item wording as ``translations``, a
    reference-instrument block and signed-weight scoring when the items match
    a known instrument, and metacheck provenance under ``definition$metacheck``.
    Same-scale objects from several files are merged (``.osd_dedup_by_source()``).
    """
    from pytacheck.datacheck.checks import _scale_block_is_ratinglike
    from pytacheck.datacheck.columns import (
        _osd_code_and_provenance,
        _osd_safe_code,
        _scale_match_items,
        _scale_reference,
    )

    if scale_groups is None or len(scale_groups) == 0:
        return []
    if dict_df is None:
        dict_df = _scale_dictionary()
    have_lbl = (
        labels_df is not None
        and len(labels_df) > 0
        and all(c in labels_df.columns for c in ("source_file", "column_name"))
    )
    lbl_index: dict[str, int] = {}
    if have_lbl:
        for i, (f, c) in enumerate(
            zip(
                _vals(labels_df, "source_file") or [],
                _vals(labels_df, "column_name") or [],
                strict=True,
            )
        ):
            lbl_index.setdefault(_key(f, c), i)
    lbl_label = _vals(labels_df, "label") if have_lbl else None
    lbl_question = _vals(labels_df, "question") if have_lbl else None

    def label_of(file: Any, col: Any) -> Any:
        if not lbl_index:
            return None
        i = lbl_index.get(_key(file, col))
        if i is None:
            return None
        w = lbl_label[i] if lbl_label is not None else None
        if _ok(w):
            return w
        if lbl_question is not None and _ok(lbl_question[i]):
            return lbl_question[i]
        return None

    likert = _LikertLookup(columns_df, labels_df)
    n = len(scale_groups)
    g_scale = _vals(scale_groups, "scale") or [None] * n
    g_tot = _vals(scale_groups, "totals_only")
    tot_col = (
        [bool(t) if t is not None else False for t in g_tot] if g_tot is not None else [False] * n
    )
    has_items_for = set(
        _unique(tolower(s) for s, t in zip(g_scale, tot_col, strict=True) if _ok(s) and not t)
    )
    g_file = _vals(scale_groups, "source_file") or [None] * n
    g_prefix = _vals(scale_groups, "prefix") or [None] * n
    g_source = _vals(scale_groups, "scale_source") or [None] * n
    g_conf = _vals(scale_groups, "confidence") or [None] * n
    g_ncol = _vals(scale_groups, "n_columns") or [None] * n
    g_max = _vals(scale_groups, "max_item") or [None] * n
    g_cols = scale_groups["columns"].tolist()

    out: list[OsdDefinition] = []
    for i in range(n):
        cols = list(g_cols[i] or [])
        file = g_file[i]
        scale = g_scale[i]
        named = _ok(scale)
        totals_only = g_tot is not None and bool(g_tot[i])
        redundant_total = totals_only and named and tolower(scale) in has_items_for
        rating_like = (not named) and bool(_scale_block_is_ratinglike(cols, file, columns_df))
        eff_source = g_source[i] if named else ("unnamed_block" if rating_like else g_source[i])
        cp = _osd_code_and_provenance(scale, g_prefix[i], eff_source, dict_df)
        wording = {c: label_of(file, c) for c in cols}
        translations_en = {c: w for c, w in wording.items() if _ok(w)}
        lopts = likert(cols, file)
        scale_info = {
            "name": scale if named else "",
            "code": cp["code"],
            "abbreviation": g_prefix[i],
        }
        reference = _scale_reference(cp["ref_code"])
        ref_match = _scale_match_items(wording, reference)
        definition: dict[str, Any] = {"scale_info": scale_info, "likert_options": lopts}

        ref_ids: dict[Any, Any] = {}
        if ref_match is not None:
            for cn, rid in zip(
                ref_match["column_name"].tolist(), ref_match["ref_item_id"].tolist(), strict=True
            ):
                ref_ids.setdefault(cn, rid)
        items = []
        for c in cols:
            it: dict[str, Any] = {"id": c, "text_key": c}
            if lopts is not None:
                it["type"] = "likert"
            if ref_match is not None and c in ref_ids and not _na(ref_ids[c]):
                it["metacheck:reference_item"] = ref_ids[c]
            items.append(it)
        definition["items"] = items

        coding: dict[str, int] | None = None
        if ref_match is not None:
            coding = {}
            for cn, rev in zip(
                ref_match["column_name"].tolist(), ref_match["ref_reverse"].tolist(), strict=True
            ):
                if not _na(rev):
                    coding[cn] = -1 if bool(rev) else 1
        if coding:
            dim_id = _osd_safe_code(scale if named else g_prefix[i])
            dim_id = tolower(_pstr(dim_id).replace("-", "_"))
            n_rev = sum(1 for v in coding.values() if v == -1)
            definition["dimensions"] = [
                {
                    "id": dim_id,
                    "name": scale if named else g_prefix[i],
                    "description": (
                        f"Total score over the {len(coding):d} item{plural(len(coding))} matched "
                        f"to the reference instrument. {n_rev:d} reverse-keyed."
                    ),
                }
            ]
            definition["scoring"] = {
                dim_id: {
                    "method": "sum_coded",
                    "items": coding,
                    "description": "Reverse-coding taken from the reference instrument's "
                    "published scoring, not inferred from the data.",
                }
            }
        else:
            definition["dimensions"] = []
            definition["scoring"] = {}

        orphan_total = totals_only and not redundant_total
        ref_block = _osd_reference_block(reference, ref_match)
        mc: dict[str, Any] = {
            "scale_source": cp["source"],
            "provenance": cp["provenance"],
        }
        if not _na(g_conf[i]):
            mc["confidence"] = g_conf[i]
        if orphan_total:
            mc["kind"] = "scale_total_only"
            mc["note"] = _TOTAL_NOTE
        if ref_block is not None:
            mc["reference"] = ref_block
        mc["source_files"] = [file]
        mc["n_columns"] = g_ncol[i]
        if g_max[i] is not None and not _na(g_max[i]):
            mc["declared_length"] = g_max[i]
        definition["metacheck"] = mc

        osd = OsdDefinition(osd_version="1.0", definition=definition)
        if translations_en:
            osd["translations"] = {"en": translations_en}
        n_items_block = len([c for c in _unique(cols) if c in translations_en])
        if n_items_block == 0:
            n_items_block = len(cols)
        inferred = eff_source in ("self_generated", "unnamed_block")
        too_small = inferred and n_items_block < _SCALE_MIN_ITEMS
        osd.attrs["write"] = (named or rating_like) and not redundant_total and not too_small
        osd.attrs["code"] = cp["code"]
        osd.attrs["orphan_total"] = orphan_total
        osd.attrs["scale"] = scale if named else None
        osd.attrs["dedup_key"] = (
            _pstr(None if _na(cp["code"]) else tolower(cp["code"]))
            + "\x02"
            + "\x01".join(sorted((_pstr(c) for c in cols), key=r_sort_key))
        )
        out.append(osd)
    return _osd_dedup_by_source(out)


def _osd_reference_block(reference: Any, ref_match: pd.DataFrame | None) -> dict[str, Any] | None:
    """The ``definition$metacheck$reference`` block of a known instrument."""
    if reference is None:
        return None
    n_matched = 0 if ref_match is None else int(ref_match["ref_item_id"].notna().sum())
    meta = reference["meta"]
    m = meta.iloc[0] if isinstance(meta, pd.DataFrame) else meta

    def g(k: str) -> Any:
        v = m.get(k)
        return None if _na(v) else v

    alphas: list[float] = []
    scoring = reference.get("scoring")
    if isinstance(scoring, pd.DataFrame) and "alpha" in scoring.columns:
        alphas = [float(a) for a in scoring["alpha"].tolist() if not _na(a)]
    n_items = g("n_items")
    block: dict[str, Any] = {"registry": "OpenScales", "code": g("code"), "name": g("name")}
    for k in ("license", "citation", "url"):
        v = g(k)
        if v is None or v != "":  # nzchar(NA) is TRUE: an NA field is kept (as NA)
            block[k] = v
    block["n_items"] = n_items
    block["n_reverse"] = g("n_reverse")
    block["items_matched"] = n_matched
    if alphas:
        block["alpha_range"] = {"min": min(alphas), "max": max(alphas)}
    if n_matched > 0 and n_items is not None and n_matched < n_items:
        block["note"] = (
            f"Matched {n_matched:d} of the reference instrument's {int(n_items):d} items by "
            "wording. The remaining items may not be shared, or may be worded differently "
            "in this codebook."
        )
    elif n_matched == 0:
        block["note"] = (
            "The scale NAME matches a known instrument, but no item wording could be matched "
            "to it — the codebook may not record item text, or this may be a different "
            "version of the instrument."
        )
    return block


def _osd_dedup_by_source(osds: list[OsdDefinition]) -> list[OsdDefinition]:
    """Merge OSD objects of the same scale seen in several files.

    Port of ``.osd_dedup_by_source()``: the first object is kept and records
    every file under ``definition$metacheck$source_files``.
    """
    if not osds:
        return osds
    kept: list[OsdDefinition] = []
    seen: dict[str, int] = {}
    for o in osds:
        k = o.attrs.get("dedup_key") or ""
        if k != "" and k in seen:
            j = seen[k]
            mc = kept[j]["definition"]["metacheck"]
            mc["source_files"] = _unique(
                [*mc.get("source_files", []), *o["definition"]["metacheck"].get("source_files", [])]
            )
            continue
        kept.append(o)
        if k != "":
            seen[k] = len(kept) - 1
    return kept


# -----------------------------------------------------------------------------
# stage 3: self-generated construct labels
# -----------------------------------------------------------------------------

_SELFGEN_PROMPT = " ".join(
    [
        "You are given ONE block of survey/rating items from a dataset: their column",
        "names (which often contain meaningful words), any documented item wording,",
        "and (optionally) sentences from the paper that mention these variables. The",
        "block does NOT match a known published instrument. Give a short, natural",
        "CONSTRUCT LABEL for what the block measures (e.g. 'Positive trait ratings',",
        "'Environmental Concern'). Base it on the provided evidence — you MAY use",
        "meaningful words in the column names (including non-English words, e.g.",
        "Spanish 'buena'=good, 'mala'=bad) together with the wording and sentences.",
        "Only return an empty construct if the column names are opaque codes with no",
        "interpretable words AND there is no wording or paper text. Never invent a",
        "published scale name; describe the construct in plain words.",
    ]
)


def _identify_scales_selfgen(
    previews: Mapping[str, Any] | None,
    labels_df: pd.DataFrame,
    paper: Any,
    model: Any = None,
    params: Mapping[str, Any] | None = None,
    columns_df: pd.DataFrame | None = None,
    text_scales: pd.DataFrame | None = None,  # noqa: ARG001 - unused in R too
    max_calls: int = 40,
) -> pd.DataFrame:
    """LLM construct labels for scale-like blocks no instrument matched.

    Port of ``.identify_scales_selfgen()``: per candidate block (Likert blocks
    plus rating-like prefix groups) with some evidence (wording, paper
    context or interpretable name tokens), one call; ``scale_source`` is
    ``"self_generated"``. Synonymous labels within a file are merged.
    """
    from pytacheck._r import bind_rows
    from pytacheck.datacheck.checks import _detect_scale_blocks, _scale_block_is_ratinglike
    from pytacheck.llm.types import type_object, type_string

    if not previews:
        return _empty_scale_frame(None)
    have = labels_df is not None and all(
        c in labels_df.columns for c in ("source_file", "column_name", "scale")
    )
    named_key: set[str] = set()
    if have:
        for f, c, sc in zip(
            _vals(labels_df, "source_file") or [],
            _vals(labels_df, "column_name") or [],
            _vals(labels_df, "scale") or [],
            strict=True,
        ):
            if _ok(sc):
                named_key.add(_key(f, c))
    wording = _Wording(labels_df)

    def wording_of(file: Any, cols: Sequence[Any]) -> list[Any]:
        idx = wording.rows(file, cols)
        if not idx:
            return []
        return [w for w in wording.parts(idx) if _ok(w)]

    pff = _PaperForFile(paper, labels_df)
    sg_index = _scale_group_index(labels_df)
    type_spec = type_object(
        construct=type_string(
            "Short construct label for what these items measure (e.g. 'Institutional Trust', 'Environmental Concern'), or empty if the text does not say."
        ),
        confidence=type_string("high, medium, or low."),
    )

    def candidate_blocks(df: pd.DataFrame, file: Any) -> list[list[str]]:
        names = _names(df)
        blocks = [[names[j] for j in ci] for ci in _detect_scale_blocks(df)]
        sga = _scale_group_args(df, file, labels_df, sg_index)
        groups = _scale_prefix_groups(
            df, scale_group=sga["scale_group"], qualtrics=sga["qualtrics"]
        ).values()
        blocks.extend(
            list(g["columns"])
            for g in groups
            if _scale_block_is_ratinglike(g["columns"], file, columns_df)
        )
        out: list[list[str]] = []
        for b in blocks:
            if b not in out:
                out.append(b)
        return out

    out: list[pd.DataFrame] = []
    model_used: str | None = None
    n_calls = 0
    for file, df in previews.items():
        if df is None or _ncol(df) < _SCALE_MIN_ITEMS:
            continue
        for nms in candidate_blocks(df, file):
            if any(_key(file, c) in named_key for c in nms):
                continue
            wd = wording_of(file, nms)
            ctx = (
                _scale_paper_context(pff(file), _scale_name_prefixes([nms[0]]), wd)
                if _has_text(pff(file))
                else []
            )
            tokens = _unique(t for n in nms for t in strsplit([tolower(n) or ""], "[^a-z]+")[0])
            tokens = [t for t in tokens if len(t) >= 3]
            if not wd and not ctx and not tokens:
                continue
            tokens_only = not wd and not ctx
            if n_calls >= max_calls:
                break
            n_calls += 1
            text_in = (
                "Column names:\n"
                + "\n".join(f"- {n}" for n in nms)
                + "\n\nItem wording:\n"
                + ("\n".join(f"- {_pstr(w)}" for w in wd) if wd else "(none documented)")
                + (
                    "\n\nRelevant paper sentences:\n" + "\n".join(f"- {s}" for s in ctx)
                    if ctx
                    else ""
                )
            )
            resp = _llm_try(
                text=pd.DataFrame({"text": [text_in]}),
                text_col="text",
                system_prompt=_SELFGEN_PROMPT,
                type=type_spec,
                model=_resolve_model(model),
                params=dict(params or {}),
                phase="Labelling scales",
            )
            if (
                not isinstance(resp, pd.DataFrame)
                or len(resp) == 0
                or "construct" not in resp.columns
            ):
                continue
            if model_used is None:
                model_used = _resp_model(resp)
            construct = _first(resp["construct"].tolist())
            construct = "" if construct is None else construct
            construct = None if _na(construct) else trimws(as_character(construct))
            if construct is None or construct == "" or tolower(construct) in _NOT_A_NAME:
                continue
            conf = _first(resp["confidence"].tolist()) if "confidence" in resp.columns else "medium"
            conf = None if _na(conf) else tolower(trimws(as_character(conf)))
            if conf not in ("high", "medium", "low"):
                conf = "medium"
            if tokens_only:
                conf = "low"
            out.append(_scale_rows(file, nms, construct, conf, "self_generated"))
        if n_calls >= max_calls:
            break
    res = bind_rows(out) if out else _empty_scale_frame(None)
    res.attrs = {}
    res = _selfgen_merge_synonyms(res)
    res.attrs["llm_model"] = model_used
    return res


_SYNONYM_STOP = frozenset(
    [
        "scale",
        "score",
        "scores",
        "ratings",
        "rating",
        "assessment",
        "task",
        "level",
        "levels",
        "during",
        "the",
        "of",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "in",
        "on",
        "data",
        "metrics",
        "measure",
    ]
)


def _synonym_tokens(s: Any) -> list[str]:
    """``toks()`` of ``.selfgen_merge_synonyms()``: lower-cased word tokens.

    (R strips ``[^a-z ]`` before lower-casing, so capital letters become
    spaces -- "Emotion Recognition" -> "motion ecognition" -- and capitalised
    labels never merge with lower-case ones.)
    """
    x = gsub("[^a-z ]+", " ", tolower(_pstr(s)) or "") or ""
    t = _unique(strsplit([x], "\\s+")[0])
    return [w for w in t if w is not None and len(w) > 0 and w not in _SYNONYM_STOP]


def _selfgen_merge_synonyms(res: pd.DataFrame | None) -> pd.DataFrame | None:
    """Collapse synonymous self-generated labels within a file (subset word sets).

    Port of ``.selfgen_merge_synonyms()``.
    """
    if res is None or len(res) == 0 or "scale" not in res.columns:
        return res
    files = _vals(res, "source_file") or []
    scales = _vals(res, "scale") or []
    new = list(scales)
    changed = False
    for f in _unique(files):
        idx = [i for i, ff in enumerate(files) if ff is not None and ff == f]
        if len(idx) < 2:
            continue
        names_f = _unique(scales[i] for i in idx)
        if len(names_f) < 2:
            continue
        tk = [_synonym_tokens(nm) for nm in names_f]
        order = sorted(range(len(tk)), key=lambda i: len(tk[i]))
        canon = {j: names_f[j] for j in range(len(names_f))}
        pos = {}
        for j, nm in enumerate(names_f):
            pos.setdefault(nm, j)
        for a in order:
            for b in order:
                if a == b:
                    continue
                if tk[a] and all(t in tk[b] for t in tk[a]):
                    canon[pos[names_f[b]]] = canon[pos[names_f[a]]]
        for i in idx:
            v = canon[pos[scales[i]]]
            if v != new[i]:
                new[i] = v
                changed = True
    if not changed:
        return res
    out = res.copy()
    out["scale"] = pd.Series(new, index=out.index, dtype="string")
    return out


# -----------------------------------------------------------------------------
# LLM tiers 2 + 3: label merging and fuzzy column matching
# -----------------------------------------------------------------------------

_MERGE_PROMPT = " ".join(
    [
        "You are reviewing whether multiple label definitions for the same variable in a",
        "psychology dataset describe the same construct. If they do, return equivalent=true and",
        "the most human-readable single label as canonical; otherwise equivalent=false.",
    ]
)

_MATCH_PROMPT = " ".join(
    [
        "You are matching data column names to codebook variable names for a psychology dataset.",
        "Return only confident pairings referring to the same construct (abbreviations, naming",
        "conventions, underscores vs spaces). Do not guess; both names must appear verbatim in",
        "the lists provided.",
    ]
)


def codebook_match_llm(
    labels_df: pd.DataFrame,
    columns_df: pd.DataFrame | None,  # noqa: ARG001 - unused in R too
    codebook_vars_df: pd.DataFrame,
    model: Any = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge conflicting label definitions and fuzzy-match unlabelled columns with an LLM.

    Port of ``codebook_match_llm()``: conflicting definitions of a (paper,
    column) are merged into one canonical label when the model says they are
    equivalent; then, per paper, still-unlabelled columns are paired with
    still-unmatched codebook variables (batches of 50 x 50). Returns
    ``{"labels_df", "n_matched", "n_merged", "model"}``.
    """
    from pytacheck.datacheck.columns import normalize_varname
    from pytacheck.llm.types import type_array, type_boolean, type_object, type_string

    n_matched = 0
    n_merged = 0
    model_used: str | None = None
    df = labels_df.copy()
    n = len(df)
    col_names = _vals(df, "column_name") or [None] * n
    norm_col = list(normalize_varname(col_names)) if n else []
    pids = _vals(df, "paper_id") or [None] * n
    label = _vals(df, "label") or [None] * n
    status = _vals(df, "label_status") or [None] * n
    method = _vals(df, "label_method") or [None] * n
    cbvar = _vals(df, "codebook_variable") or [None] * n
    lsource = _vals(df, "label_source") or [None] * n

    conflict_idx = [i for i, s in enumerate(status) if s == "conflicting_definition"]
    if conflict_idx:
        keys = [_key(pids[i], col_names[i]) for i in conflict_idx]
        type_spec = type_object(
            equivalent=type_boolean("Whether all labels describe the same construct"),
            canonical=type_string("Best single label if equivalent, else empty", required=False),
        )
        for key in _unique(keys):
            match_pos = [i for i, k in zip(conflict_idx, keys, strict=True) if k == key]
            idx1 = match_pos[0]
            labs = ["NA"] if label[idx1] is None else _r_strsplit_fixed(_pstr(label[idx1]), " | ")
            txt = f"Column: {_pstr(col_names[idx1])}\nCandidate labels:\n" + "\n".join(
                f"- {x}" for x in labs
            )
            resp = _llm_try(
                text=pd.DataFrame({"text": [txt]}),
                text_col="text",
                system_prompt=_MERGE_PROMPT,
                type=type_spec,
                model=_resolve_model(model),
                params=dict(params or {}),
                phase="Matching codebook columns",
            )
            if not isinstance(resp, pd.DataFrame) or len(resp) == 0:
                continue
            if model_used is None:
                model_used = _resp_model(resp)
            eq = _first(resp["equivalent"].tolist()) if "equivalent" in resp.columns else None
            if not _is_true(eq):
                continue
            canonical = _first(resp["canonical"].tolist()) if "canonical" in resp.columns else ""
            if canonical is None and "canonical" not in resp.columns:
                canonical = ""
            if _na(canonical) or as_character(canonical) == "":
                continue
            for i in match_pos:
                label[i] = as_character(canonical)
                status[i] = "labelled"
                method[i] = "merged_llm"
            n_merged += len(match_pos)

    cb_pids = _vals(codebook_vars_df, "paper_id") or [None] * len(codebook_vars_df)
    cb_vars = _vals(codebook_vars_df, "codebook_variable") or [None] * len(codebook_vars_df)
    cb_labels = _vals(codebook_vars_df, "label") or [None] * len(codebook_vars_df)
    cb_src = _vals(codebook_vars_df, "codebook_source") or [None] * len(codebook_vars_df)
    cb_norm = list(normalize_varname(cb_vars)) if cb_vars else []
    match_type = type_object(
        matches=type_array(
            type_object(
                column_name=type_string("Exact column name from the data list"),
                codebook_variable=type_string("Exact variable name from the codebook list"),
            )
        )
    )
    for pid in _unique(pids):
        if pid is None:
            continue  # labels_df$paper_id == NA selects nothing
        labels_paper_idx = [i for i, p in enumerate(pids) if p == pid]
        cbk_paper_idx = [i for i, p in enumerate(cb_pids) if p is not None and p == pid]
        documented = [s in ("labelled", "llm") for s in status]
        unlabelled_idx = [i for i in labels_paper_idx if status[i] == "unlabelled"]
        matched_norm = set(
            _unique(
                _nv(cbvar[i])
                for i in range(n)
                if documented[i] and pids[i] == pid and cbvar[i] is not None
            )
        )
        unmatched = [i for i in cbk_paper_idx if cb_norm[i] not in matched_norm]
        if not unlabelled_idx or not unmatched:
            continue
        unlab_cols = _unique(col_names[i] for i in unlabelled_idx)
        norm_unmatched = [cb_norm[i] for i in unmatched]
        un_vars = [cb_vars[i] for i in unmatched]
        col_batches = [unlab_cols[i : i + 50] for i in range(0, len(unlab_cols), 50)]
        var_batches = [un_vars[i : i + 50] for i in range(0, len(un_vars), 50)]
        norm_unlab = [norm_col[i] for i in unlabelled_idx]
        for cols_b in col_batches:
            for vars_b in var_batches:
                txt = (
                    "Data columns (unlabelled):\n"
                    + "\n".join(f"{k}. {_pstr(c)}" for k, c in enumerate(cols_b, 1))
                    + "\n\nCodebook variables (unmatched):\n"
                    + "\n".join(f"{k}. {_pstr(v)}" for k, v in enumerate(vars_b, 1))
                )
                resp = _strip(
                    _llm_try(
                        text=pd.DataFrame({"text": [txt]}),
                        text_col="text",
                        system_prompt=_MATCH_PROMPT,
                        type=match_type,
                        model=_resolve_model(model),
                        params=dict(params or {}),
                        phase="Matching codebook columns",
                    ),
                    "matches",
                )
                if (
                    not isinstance(resp, pd.DataFrame)
                    or len(resp) == 0
                    or not all(c in resp.columns for c in ("column_name", "codebook_variable"))
                ):
                    continue
                if model_used is None:
                    model_used = _resp_model(resp)
                for rc, rv in zip(
                    resp["column_name"].tolist(), resp["codebook_variable"].tolist(), strict=True
                ):
                    pnc = _nv(None if _na(rc) else rc)
                    pnv = _nv(None if _na(rv) else rv)
                    if pnc not in norm_unlab or pnv not in norm_unmatched:
                        continue
                    var_row = norm_unmatched.index(pnv)
                    rows = [i for i in unlabelled_idx if norm_col[i] == pnc]
                    if not rows:
                        continue
                    j = unmatched[var_row]
                    for i in rows:
                        label[i] = cb_labels[j]
                        cbvar[i] = cb_vars[j]
                        lsource[i] = cb_src[j]
                        status[i] = "llm"
                        method[i] = "llm"
                    n_matched += len(rows)

    for col, values in (
        ("label", label),
        ("label_status", status),
        ("label_method", method),
        ("codebook_variable", cbvar),
        ("label_source", lsource),
    ):
        if n and (col in df.columns or any(v is not None for v in values)):
            df[col] = pd.Series(values, index=df.index, dtype="string")
    return {"labels_df": df, "n_matched": n_matched, "n_merged": n_merged, "model": model_used}


def _nv(x: Any) -> Any:
    """``normalize_varname()`` of one value."""
    from pytacheck.datacheck.columns import normalize_varname

    return normalize_varname([x])[0]


def _r_strsplit_fixed(x: str, split: str) -> list[str]:
    """``strsplit(x, split, fixed = TRUE)[[1]]`` (no trailing empty string)."""
    return strsplit([x], split, fixed=True)[0]
