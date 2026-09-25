"""``text_search()``: search the sentences of papers.

Port of ``R/text_search.R``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pandas as pd

from pytacheck._r.frames import bind_rows
from pytacheck._r.regex import RegexError, compile_r, grepl, gsub, regextract_all
from pytacheck.papers.model import Paper, PaperList, is_paper_list
from pytacheck.papers.tables import paper_table

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
        text = paper_table(paper, "text")
        if "text" not in text.columns and len(text) == 0:
            # an empty paper list (or a paper without text): the columns of a
            # text table, typed, so that searches of it chain like any other
            # (metacheck gives logical NA columns and drops `text`; U79)
            return _empty_text_frame(), False
        sections = paper_table(paper, "section")
        cols = ["section_id", "paper_id", "header", "section_type"]
        if all(c in sections.columns for c in cols) and len(text.columns) > 0:
            right = sections.loc[:, cols]
            if "section_id" in text.columns:
                right = right.astype({"section_id": text["section_id"].dtype}, errors="ignore")
            text = text.merge(right, on=["section_id", "paper_id"], how="left", sort=False)
        return text, False
    if isinstance(paper, str):
        return pd.DataFrame({"text": pd.Series([paper], dtype="string")}), True
    if isinstance(paper, Sequence) and all(isinstance(s, str) for s in paper):
        return pd.DataFrame({"text": pd.Series(list(paper), dtype="string")}), True
    raise TypeError(
        "The paper argument doesn't seem to be a scivrs_paper object or a list of paper objects"
    )


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

    if not isinstance(pattern, str):
        patterns = list(pattern)
        if len(patterns) > 1:
            parts = [
                text_search(
                    paper,
                    p,
                    return_,
                    ignore_case,
                    fixed,
                    perl,
                    exclude,
                    search_header,
                    include_refs,
                )
                for p in patterns
            ]
            if isinstance(parts[0], list):
                return _combine_vectors(parts, exclude)
            if exclude and len(parts) > 2:
                # dplyr::intersect(x, y, ...) on data frames: the dots must be empty
                extra = "\n".join(
                    f"• ..{i} = <tibble[,{part.shape[1]}]>"
                    for i, part in enumerate(parts[2:], start=1)
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
        pattern = patterns[0]

    try:
        compile_r(pattern, ignore_case, perl, fixed)
    except RegexError as exc:
        raise ValueError(f"Check the pattern argument in '{pattern}':\n{exc}") from exc

    text, is_vector = _text_frame(paper)
    missing = [c for c in _REQUIRED if c not in text.columns]
    for m in missing:
        text[m] = pd.Series([pd.NA] * len(text), index=text.index, dtype=object)
    if "text" in missing:
        text["text"] = text.iloc[:, 0]

    if include_refs:
        ft = text
    else:
        ft = text.loc[~text["section_type"].isin(["references"]).fillna(False).astype(bool)]
    ft = ft.reset_index(drop=True)

    match_rows = pd.Series(
        grepl(pattern, ft["text"].tolist(), ignore_case, perl, fixed), dtype=bool
    )
    if search_header:
        header_rows = pd.Series(
            grepl(pattern, ft["header"].tolist(), ignore_case, perl, fixed), dtype=bool
        )
        match_rows = match_rows | header_rows
    if exclude:
        match_rows = ~match_rows
    ft_match = ft.loc[match_rows.to_numpy()].reset_index(drop=True)

    all_cols = list(ft.columns)
    if return_ == "sentence":
        result = ft_match
    elif return_ == "match":
        found = regextract_all(pattern, ft_match["text"].tolist(), ignore_case, perl, fixed)
        rows = [i for i, hits in enumerate(found) for _ in hits]
        result = ft_match.iloc[rows].reset_index(drop=True)
        result["text"] = pd.Series([h for hits in found for h in hits], dtype="string")
        result = result.loc[:, all_cols]
    else:
        pgroups = _GROUPS["paragraph"]
        ft_p = _paste_groups(ft, pgroups, " ")
        groups = _GROUPS[return_]
        kept = _semi_join(ft_p, ft_match, groups)
        result = _paste_groups(kept, groups, _PARAGRAPH_MARKER)
        if return_ == "section" and len(result) > 0:
            result["header"] = _section_headers(kept, result, groups)

    if return_ != "match":
        if len(result) > 0:
            cleaned = gsub(r"\s+", " ", result["text"].tolist())
            cleaned = gsub(" , ", ", ", cleaned, fixed=True)
            cleaned = gsub(_PARAGRAPH_MARKER, "\n\n", cleaned, fixed=True)
            result = result.copy()
            result["text"] = pd.Series(cleaned, index=result.index, dtype="string")
            for col in all_cols:
                if col not in result.columns:
                    result[col] = pd.Series([pd.NA] * len(result), index=result.index, dtype=object)
            result = result.loc[:, all_cols]
            result = result.drop_duplicates().reset_index(drop=True)
        else:
            result = ft.iloc[0:0].reset_index(drop=True)

    if "text" in missing:
        result = result.drop(columns="text")
    if is_vector:
        return [None if pd.isna(t) else str(t) for t in result["text"].tolist()]
    return result


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
        from pytacheck.report.render import deparse

        extra = ", ".join("".join(deparse(part)) if part else "character(0)" for part in parts[2:])
        raise TypeError(f"unused argument{'s' if len(parts) > 3 else ''} ({extra})")
    second = set(parts[1])
    return [t for t in dict.fromkeys(parts[0]) if t in second]
