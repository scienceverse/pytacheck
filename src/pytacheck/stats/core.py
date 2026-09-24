"""``stats()``: check the statistics reported in papers (port of ``R/stats.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["stats"]

_STATCHECK_ARGS = (
    "stat",
    "OneTailedTests",
    "alpha",
    "pEqualAlphaSig",
    "pZeroError",
    "OneTailedTxt",
    "AllPValues",
)


def _valid_statcheck_args(kwargs: dict[str, Any]) -> bool:
    """Would ``statcheck(subtext, messages = FALSE, ...)`` accept these arguments?

    R raises (and ``stats()``'s ``tryCatch`` swallows) an error for unknown
    arguments and for ``messages``, which ``stats()`` already passes.
    """
    return all(k in _STATCHECK_ARGS for k in kwargs)


def stats(text: Any, **kwargs: Any) -> pd.DataFrame:
    """Check the statistics in a paper, paper list or text table (``stats()``).

    Port of ``R/stats.R::stats()``: every sentence that contains a digit is
    run through statcheck (:func:`pytacheck.stats.statcheck.statcheck`, which
    receives *kwargs*: ``stat``, ``OneTailedTests``, ``alpha``,
    ``pEqualAlphaSig``, ``pZeroError``, ``OneTailedTxt``, ``AllPValues``).
    Returns statcheck's columns (without ``source``) followed by the columns
    of the matching sentence (``text``, ``paper_id``, ``header`` ...), or an
    empty DataFrame when nothing is found.

    As in R, a sentence whose check raises an error or a warning (for example
    an unparseable p-value such as ``p = .05-.10``) contributes no rows.
    """
    from pytacheck.stats.statcheck import _statcheck_quiet
    from pytacheck.text.search import text_search

    # lines with stats must have at least one number
    table = text_search(text, "[0-9]")
    if not isinstance(table, pd.DataFrame):
        # R: nrow() of a character vector is NULL, so `if (n == 0)` fails
        raise TypeError("argument is of length zero")
    if len(table) == 0:
        return pd.DataFrame()
    if not _valid_statcheck_args(kwargs):
        # every statcheck() call errors, and every error becomes data.frame()
        return pd.DataFrame()

    texts = [None if pd.isna(t) else str(t) for t in table["text"].tolist()]
    sources, columns = _statcheck_quiet(texts, **kwargs)
    if not sources:
        return pd.DataFrame()

    # dplyr::left_join(checks, text, by = "source"), then drop `source`
    right = table.drop(columns=["source"], errors="ignore")
    right = right.iloc[sources].reset_index(drop=True)
    left = pd.DataFrame(columns)
    dupes = [c for c in right.columns if c in left.columns]
    if dupes:
        left = left.rename(columns={c: f"{c}.x" for c in dupes})
        right = right.rename(columns={c: f"{c}.y" for c in dupes})
    return pd.concat([left, right], axis=1)
