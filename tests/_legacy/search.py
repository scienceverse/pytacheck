"""``text_search()`` as it was before the indexed-document core: the oracle.

A frozen copy of ``src/metacheck/text/search.py`` at the commit before CORE-1b
(only this docstring and the ``paper_table`` import differ), the reference of
the differential tests in ``tests/core``. Do not change it to follow the
façade: a difference between the two is what the tests are for.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pandas as pd

from metacheck._r.frames import bind_rows
from metacheck._r.regex import (
    RegexError,
    _as_str,
    compile_r,
    detector,
    fold,
    gsub,
    regextract_all,
)
from metacheck._values import is_missing
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.papers.schema import records_to_frame
from tests._legacy.tables import paper_table

__all__ = ["search_text", "text_search"]

ReturnType = Literal["sentence", "paragraph", "section", "header", "match", "paper_id"]
_RETURN_TYPES = ("sentence", "paragraph", "section", "header", "match", "paper_id")
_REQUIRED = ("text", "text_id", "section_id", "paragraph_id", "paper_id", "header", "section_type")
_PARAGRAPH_MARKER = "<~p~>"
_GROUPS = {
    "paragraph": ["section_type", "header", "section_id", "paragraph_id", "paper_id"],
    "header": ["section_type", "header", "section_id", "paper_id"],
    "section": ["section_type", "section_id", "paper_id"],
    "paper_id": ["paper_id"],
}


def _text_frame(paper: Any) -> tuple[pd.DataFrame, bool]:
    """The table to search, and whether the input was a plain character vector."""
    if isinstance(paper, pd.DataFrame):
        return paper.copy(deep=False), False
    if isinstance(paper, Paper | PaperList) or (
        is_paper_list(paper) and not isinstance(paper, str)
    ):
        return _join_sections(paper), False
    if isinstance(paper, str):
        return pd.DataFrame({"text": pd.Series([paper], dtype="string")}), True
    if isinstance(paper, Sequence) and all(isinstance(s, str) for s in paper):
        return pd.DataFrame({"text": pd.Series(list(paper), dtype="string")}), True
    raise TypeError(
        "The paper argument doesn't seem to be a scivrs_paper object or a list of paper objects"
    )


def _join_sections(paper: Any) -> pd.DataFrame:
    """``paper_table(paper, "text")`` with each sentence's section ``header`` and ``section_type``."""
    fast = _join_one(paper) if isinstance(paper, Paper) else None
    if fast is not None:
        return fast
    text = paper_table(paper, "text")
    if "text" not in text.columns and len(text) == 0:
        # an empty paper list (or a paper without text): the columns of a
        # text table, typed, so that searches of it chain like any other
        # (metacheck gives logical NA columns and drops `text`; U79)
        return _empty_text_frame()
    sections = paper_table(paper, "section")
    cols = ["section_id", "paper_id", "header", "section_type"]
    if all(c in sections.columns for c in cols) and len(text.columns) > 0:
        right = sections.loc[:, cols]
        if "section_id" in text.columns:
            right = right.astype({"section_id": text["section_id"].dtype}, errors="ignore")
        text = text.merge(right, on=["section_id", "paper_id"], how="left", sort=False)
    return text


def _one_table(paper: Paper, name: str, columns: list[str] | None = None) -> pd.DataFrame | None:
    """``paper_table(paper, name, columns)`` of one paper, cheaply (``None``: not cheaply).

    From its JSON records when it still has them (``paper_table()`` types each
    column the same way from all of them), or from its table.
    """
    raw = paper._raw_records(name)
    if raw is not None:
        records, own = raw
        if not records or (columns is not None and not set(columns) <= set(own)):
            return None
        wanted = [c for c in (own if columns is None else columns) if c != "paper_id"]
        frame = records_to_frame(name, records, wanted)
    else:
        table = paper.get(name)
        if not isinstance(table, pd.DataFrame) or len(table) == 0:
            return None
        own = list(table.columns)
        if not table.columns.is_unique or (columns is not None and not set(columns) <= set(own)):
            return None
        wanted = [c for c in (own if columns is None else columns) if c != "paper_id"]
        frame = table.loc[:, wanted].reset_index(drop=True)
    at = own.index("paper_id") if "paper_id" in own and columns is None else len(wanted)
    ids = pd.Series([paper.paper_id] * len(frame), index=frame.index, dtype="string")
    frame.insert(at, "paper_id", ids)
    return frame


def _join_one(paper: Paper) -> pd.DataFrame | None:
    """:func:`_join_sections` of one paper, with a lookup instead of the merge.

    Every row has the paper's ID, so the merge key is ``section_id`` alone
    (``NA`` finds ``NA``, as in the merge). ``None`` when the general path is
    needed: no rows, a section ID that is not unique (the merge repeats rows),
    columns the merge would rename, or key types it would convert.
    """
    if not isinstance(paper.paper_id, str):
        return None
    text = _one_table(paper, "text")
    sections = _one_table(paper, "section", ["section_id", "header", "section_type"])
    if (
        text is None
        or sections is None
        or "section_id" not in text.columns
        or "header" in text.columns
        or "section_type" in text.columns
        or not isinstance(text["section_id"].dtype, pd.Int64Dtype)
        or not isinstance(sections["section_id"].dtype, pd.Int64Dtype)
        or not all(
            isinstance(sections[c].dtype, pd.StringDtype) for c in ("header", "section_type")
        )
    ):
        return None
    keys = [None if k is pd.NA else k for k in sections["section_id"].tolist()]
    row_of = {k: j for j, k in enumerate(keys)}
    if len(row_of) < len(keys):
        return None
    rows = [row_of.get(None if k is pd.NA else k, -1) for k in text["section_id"].tolist()]
    for c in ("header", "section_type"):
        text[c] = sections[c].array.take(rows, allow_fill=True)
    return text


def _empty_text_frame() -> pd.DataFrame:
    """A zero-row text table with the columns :func:`text_search` returns for papers."""
    return pd.DataFrame(
        {
            "text": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
            "section_id": pd.Series([], dtype="Int64"),
            "paragraph_id": pd.Series([], dtype="Int64"),
            "paper_id": pd.Series([], dtype="string"),
            "header": pd.Series([], dtype="string"),
            "section_type": pd.Series([], dtype="string"),
        }
    )


def _paste_groups(df: pd.DataFrame, by: list[str], sep: str) -> pd.DataFrame:
    """``summarise(text = paste(text, collapse = sep), .by = by)``."""
    if len(df) == 0:
        out = df.loc[:, by].iloc[0:0].copy()
        out["text"] = pd.Series([], dtype="string")
        return out
    keys = df.loc[:, by]
    texts = ["NA" if pd.isna(t) else str(t) for t in df["text"].tolist()]
    groups: dict[tuple[Any, ...], list[str]] = {}
    first_row: dict[tuple[Any, ...], int] = {}
    for i, key in enumerate(keys.itertuples(index=False, name=None)):
        norm = tuple(None if pd.isna(k) else k for k in key)
        if norm not in groups:
            groups[norm] = []
            first_row[norm] = i
        groups[norm].append(texts[i])
    rows = [first_row[k] for k in groups]
    out = keys.iloc[rows].reset_index(drop=True)
    out["text"] = pd.Series([sep.join(v) for v in groups.values()], dtype="string")
    return out


def _section_headers(kept: pd.DataFrame, result: pd.DataFrame, groups: list[str]) -> pd.Series:
    """The header of each section in *result*: its paragraphs' header when they share one.

    metacheck groups ``return = "section"`` by ``section_type``, ``section_id``
    and ``paper_id`` only, so the header column came back ``NA`` although each
    section of a paper has one header (U80).
    """

    def key(row: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(None if pd.isna(k) else k for k in row)

    seen: dict[tuple[Any, ...], set[Any]] = {}
    for row, header in zip(
        kept.loc[:, groups].itertuples(index=False, name=None), kept["header"].tolist(), strict=True
    ):
        seen.setdefault(key(row), set()).add(None if pd.isna(header) else header)
    headers = []
    for row in result.loc[:, groups].itertuples(index=False, name=None):
        found = seen.get(key(row), set())
        headers.append(next(iter(found)) if len(found) == 1 else None)
    return pd.Series(headers, index=result.index, dtype=kept["header"].dtype)


def _semi_join(x: pd.DataFrame, y: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """``dplyr::semi_join()`` (NA keys match NA keys), keeping x's row order."""

    def key_set(df: pd.DataFrame) -> set[tuple[Any, ...]]:
        return {
            tuple(None if pd.isna(k) else k for k in row)
            for row in df.loc[:, by].itertuples(index=False, name=None)
        }

    wanted = key_set(y)
    mask = [
        tuple(None if pd.isna(k) else k for k in row) in wanted
        for row in x.loc[:, by].itertuples(index=False, name=None)
    ]
    return x.loc[mask].reset_index(drop=True)


class _Table:
    """What one :func:`text_search` call searches: its table, and the columns it scans as lists.

    Built once per call, however many patterns are searched: every pattern is
    matched against the same lists, and a DataFrame is built once, for the
    result rows only (metacheck, and the old port, ran a full search per
    pattern and row-bound the results).
    """

    __slots__ = (
        "_cleaned",
        "_folded",
        "_ft",
        "_paragraphs",
        "_strings",
        "frame",
        "header",
        "is_vector",
        "missing",
        "rows",
        "text",
    )

    @classmethod
    def of(cls, paper: Any, include_refs: bool) -> _Table:
        """The table for *paper*."""
        return cls(*_text_frame(paper), include_refs)

    def __init__(self, frame: pd.DataFrame, is_vector: bool, include_refs: bool) -> None:
        self.is_vector = is_vector
        self.missing = [c for c in _REQUIRED if c not in frame.columns]
        if self.missing:
            frame = frame.copy(deep=False)
            for m in self.missing:
                frame[m] = pd.Series([pd.NA] * len(frame), index=frame.index, dtype=object)
            if "text" in self.missing:
                frame["text"] = frame.iloc[:, 0]
        self.frame = frame
        if include_refs:
            self.rows: list[int] | None = None
        else:
            types = frame["section_type"].tolist()
            refs = [i for i, t in enumerate(types) if isinstance(t, str) and t == "references"]
            self.rows = None if not refs else sorted(set(range(len(frame))) - set(refs))
        text = frame["text"].tolist()
        header = frame["header"].tolist()
        if self.rows is not None:
            text = [text[i] for i in self.rows]
            header = [header[i] for i in self.rows]
        self.text = text
        self.header = header
        self._strings: dict[str, list[str | None]] = {}
        self._cleaned: dict[int, Any] = {}
        self._folded: dict[str, list[str | None]] = {}
        self._ft: pd.DataFrame | None = None
        self._paragraphs: pd.DataFrame | None = None

    @property
    def ft(self) -> pd.DataFrame:
        """The searched rows (references dropped) as a frame, index reset."""
        if self._ft is None:
            self._ft = self.take(None)
        return self._ft

    def take(self, positions: list[int] | None) -> pd.DataFrame:
        """Rows *positions* of the searched rows (all of them for ``None``), index reset."""
        if positions is None:
            positions = self.rows
        elif self.rows is not None:
            positions = [self.rows[i] for i in positions]
        if positions is None:
            return self.frame.reset_index(drop=True)
        return self.frame.take(positions).reset_index(drop=True)

    def matches(
        self, pattern: str, ignore_case: bool, perl: bool, fixed: bool, exclude: bool, header: bool
    ) -> list[int]:
        """Positions of the searched rows whose text (or header) matches *pattern*."""
        hits = self._scan("text", pattern, ignore_case, perl, fixed)
        if header:
            in_header = self._scan("header", pattern, ignore_case, perl, fixed)
            hits = [a or b for a, b in zip(hits, in_header, strict=True)]
        return [i for i, h in enumerate(hits) if h != exclude]

    def _scan(
        self, column: str, pattern: str, ignore_case: bool, perl: bool, fixed: bool
    ) -> list[bool]:
        """``grepl(pattern, column)``, with the column's strings and their folding made once."""
        strings = self._strings.get(column)
        if strings is None:
            values = self.text if column == "text" else self.header
            strings = self._strings[column] = [_as_str(v) for v in values]
        match = detector(pattern, ignore_case, perl, fixed)
        if not match.folds:
            return [match(s) for s in strings]
        folded = self._folded.get(column)
        if folded is None:
            folded = self._folded[column] = [None if s is None else fold(s) for s in strings]
        return [match(s, f) for s, f in zip(strings, folded, strict=True)]

    def sentences(self, positions: list[int]) -> pd.DataFrame:
        """The result of a sentence search that found *positions* (in result order)."""
        if not positions:
            return self.take([])
        result = self.take(positions)
        cleaned = self._cleaned
        todo = [i for i in dict.fromkeys(positions) if i not in cleaned]
        cleaned.update(zip(todo, _clean([self.text[i] for i in todo]), strict=True))
        result["text"] = pd.Series([cleaned[i] for i in positions], dtype="string")
        if self._distinct(positions):
            return result  # distinct() has nothing to drop
        return result.drop_duplicates().reset_index(drop=True)

    def _distinct(self, positions: list[int]) -> bool:
        """Whether the rows at *positions* have distinct, known ``(paper_id, text_id)``."""
        rows = positions if self.rows is None else [self.rows[i] for i in positions]
        pids = self.frame["paper_id"].tolist()
        tids = self.frame["text_id"].tolist()
        keys = set()
        for i in rows:
            pid, tid = pids[i], tids[i]
            if is_missing(pid) or is_missing(tid):
                return False
            try:
                keys.add((pid, tid))
            except TypeError:  # an unhashable cell: let drop_duplicates() decide
                return False
        return len(keys) == len(rows)

    def search(
        self,
        pattern: str,
        return_: str,
        ignore_case: bool,
        fixed: bool,
        perl: bool,
        exclude: bool,
        search_header: bool,
    ) -> pd.DataFrame:
        """One pattern's result table (before a dropped ``text`` column is removed)."""
        found = self.matches(pattern, ignore_case, perl, fixed, exclude, search_header)
        if return_ == "sentence":
            return self.sentences(found)
        if return_ == "match":
            hits = regextract_all(pattern, [self.text[i] for i in found], ignore_case, perl, fixed)
            result = self.take([i for i, h in zip(found, hits, strict=True) for _ in h])
            result["text"] = pd.Series([h for each in hits for h in each], dtype="string")
            return result
        ft = self.ft
        all_cols = list(ft.columns)
        groups = _GROUPS[return_]
        kept = _semi_join(self.paragraphs(), ft.iloc[found], groups)
        result = _paste_groups(kept, groups, _PARAGRAPH_MARKER)
        if len(result) == 0:
            return ft.iloc[0:0].reset_index(drop=True)
        if return_ == "section":
            result["header"] = _section_headers(kept, result, groups)
        result["text"] = pd.Series(_clean(result["text"].tolist()), dtype="string")
        for col in all_cols:
            if col not in result.columns:
                result[col] = pd.Series([pd.NA] * len(result), index=result.index, dtype=object)
        return result.loc[:, all_cols].drop_duplicates().reset_index(drop=True)

    def paragraphs(self) -> pd.DataFrame:
        """The searched rows' text pasted by paragraph (the same for every pattern)."""
        if self._paragraphs is None:
            self._paragraphs = _paste_groups(self.ft, _GROUPS["paragraph"], " ")
        return self._paragraphs

    def finish(self, result: pd.DataFrame) -> Any:
        """*result* as text_search() returns it: without an added ``text``, or as strings."""
        if "text" in self.missing:
            result = result.drop(columns="text")
        if self.is_vector:
            return [None if pd.isna(t) else str(t) for t in result["text"].tolist()]
        return result


def _clean(texts: list[Any]) -> list[Any]:
    """Whitespace runs as one space, " , " as ", " and paragraph markers as blank lines."""
    cleaned: list[Any] = gsub(r"\s+", " ", texts)
    cleaned = gsub(" , ", ", ", cleaned, fixed=True)
    cleaned = gsub(_PARAGRAPH_MARKER, "\n\n", cleaned, fixed=True)
    return cleaned


def _check_pattern(pattern: str, ignore_case: bool, perl: bool, fixed: bool) -> None:
    try:
        compile_r(pattern, ignore_case, perl, fixed)
    except RegexError as exc:
        raise ValueError(f"Check the pattern argument in '{pattern}':\n{exc}") from exc


def text_search(
    paper: Any,
    pattern: str | Sequence[str] = ".*",
    return_: ReturnType = "sentence",
    ignore_case: bool = True,
    fixed: bool = False,
    perl: bool = False,
    exclude: bool = False,
    search_header: bool = False,
    include_refs: bool = False,
) -> Any:
    """Search the text of a paper, a paper list, a previous result table, or strings.

    Parameters mirror metacheck's ``text_search()``; ``return_`` is R's
    ``return`` argument (``"sentence"``, ``"paragraph"``, ``"section"``,
    ``"header"``, ``"match"`` or ``"paper_id"``). Patterns are R regular
    expressions (TRE unless ``perl=True``). Several patterns are searched
    separately and their results combined (or intersected when
    ``exclude=True``). References are skipped unless ``include_refs=True``.

    Returns a DataFrame, or a list of strings when *paper* is a string or a
    sequence of strings.
    """
    if return_ not in _RETURN_TYPES:
        raise ValueError(f"'arg' should be one of {', '.join(repr(r) for r in _RETURN_TYPES)}")
    if fixed:
        ignore_case = False
    patterns = [pattern] if isinstance(pattern, str) else list(pattern)
    # R checks each pattern before it builds the table to search for it
    _check_pattern(patterns[0], ignore_case, perl, fixed)
    table = _Table.of(paper, include_refs)
    for p in patterns[1:]:
        _check_pattern(p, ignore_case, perl, fixed)

    if len(patterns) == 1:
        found = table.search(patterns[0], return_, ignore_case, fixed, perl, exclude, search_header)
        return table.finish(found)

    if return_ == "sentence" and not exclude and not table.is_vector:
        # bind_rows() of each pattern's rows, then distinct(): one pass over the rows
        order: dict[int, None] = {}
        for p in patterns:
            order.update(
                dict.fromkeys(table.matches(p, ignore_case, perl, fixed, False, search_header))
            )
        return table.finish(table.sentences(list(order)))

    parts = [
        table.finish(table.search(p, return_, ignore_case, fixed, perl, exclude, search_header))
        for p in patterns
    ]
    if table.is_vector:
        return _combine_vectors(parts, exclude)
    if exclude and len(parts) > 2:
        # dplyr::intersect(x, y, ...) on data frames: the dots must be empty
        extra = "\n".join(
            f"• ..{i} = <tibble[,{part.shape[1]}]>" for i, part in enumerate(parts[2:], start=1)
        )
        raise ValueError(
            f"`...` must be empty.\n✖ Problematic argument{'s' if len(parts) > 3 else ''}:"
            f"\n{extra}\nℹ Did you forget to name an argument?"
        )
    if exclude:
        result = parts[0]
        for part in parts[1:]:
            result = result.merge(part, how="inner")
        return result.drop_duplicates().reset_index(drop=True)
    return bind_rows(parts).drop_duplicates().reset_index(drop=True)


search_text = text_search


def _combine_vectors(parts: list[list[str | None]], exclude: bool) -> list[str | None]:
    """R's combination of the per-pattern results of a search of character strings.

    ``dplyr::bind_rows()`` refuses character vectors, so several patterns
    always fail unless ``exclude = TRUE``; then ``dplyr::intersect()`` is
    ``base::intersect(x, y)`` (unique strings of the first result that are in
    the second), which has no room for a third result.
    """
    if not exclude:
        raise ValueError("Argument 1 must be a data frame or a named atomic vector.")
    if len(parts) > 2:
        from metacheck._r.base import r_literal

        extra = ", ".join(r_literal(part) if part else "character(0)" for part in parts[2:])
        raise TypeError(f"unused argument{'s' if len(parts) > 3 else ''} ({extra})")
    second = set(parts[1])
    return [t for t in dict.fromkeys(parts[0]) if t in second]
