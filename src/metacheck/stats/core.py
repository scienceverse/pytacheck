"""``stats()``: check the statistics reported in papers (port of ``R/stats.R``)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

__all__ = ["stats"]

_ALL_STATS = ("t", "F", "cor", "chisq", "Z", "Q")


def stats(
    text: Any,
    stat: str | Iterable[str] = _ALL_STATS,
    OneTailedTests: bool = False,
    alpha: float = 0.05,
    pEqualAlphaSig: bool = True,
    pZeroError: bool = True,
    OneTailedTxt: bool = False,
    AllPValues: bool = False,
) -> pd.DataFrame:
    """Check the statistics in a paper, paper list, text table or strings (``stats()``).

    Port of ``R/stats.R::stats()``: every sentence that contains a digit is
    run through statcheck (:func:`metacheck.stats.statcheck.statcheck`, with
    the settings R passes on through ``...``). Returns statcheck's columns
    (without ``source``) followed by the columns of the matching sentence
    (``text``, ``paper_id``, ``header`` ...), or an empty DataFrame when
    nothing is found. A string or a sequence of strings gives statcheck's
    columns followed by ``text``.

    Differs from metacheck (U4, D78): a sentence keeps its other results when
    one of them cannot be checked (metacheck drops the whole sentence on the
    first R warning or error, e.g. for an unrelated ``p = .05-.10``); the
    settings are ordinary keyword (or positional) arguments, so a misspelt
    or abbreviated name raises :class:`TypeError` (R matched unique prefixes)
    and invalid values :class:`ValueError`; and strings are accepted
    (metacheck fails on a character vector).
    """
    from metacheck.stats.statcheck import _statcheck_quiet
    from metacheck.text.search import text_search

    # lines with stats must have at least one number
    table = text_search(text, "[0-9]")
    if not isinstance(table, pd.DataFrame):
        # a string or a character vector: the matching strings
        table = pd.DataFrame({"text": pd.Series(table, dtype="string")})

    texts = [None if pd.isna(t) else str(t) for t in table["text"].tolist()]
    sources, columns = _statcheck_quiet(
        texts, stat, OneTailedTests, alpha, pEqualAlphaSig, pZeroError, OneTailedTxt, AllPValues
    )
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
