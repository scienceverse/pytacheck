"""``stats()``: check the statistics reported in papers (port of ``R/stats.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["stats"]

# statcheck::statcheck()'s formals, in order.
_STATCHECK_FORMALS = (
    "texts",
    "stat",
    "OneTailedTests",
    "alpha",
    "pEqualAlphaSig",
    "pZeroError",
    "OneTailedTxt",
    "AllPValues",
    "messages",
)


class _ArgError(Exception):
    """R's argument matching fails (``unused argument`` and friends)."""


def _match_statcheck_args(
    subtext: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Bind ``statcheck::statcheck(subtext, messages = FALSE, ...)`` like R's ``matchArgs()``.

    Named arguments match a formal exactly, then as a unique prefix (R's
    partial matching); the unnamed ones fill the remaining formals in order.
    Raises :class:`_ArgError` where R would fail (an unused argument, a formal
    matched twice, an ambiguous prefix).
    """
    supplied: list[tuple[str | None, Any]] = [(None, subtext), ("messages", False)]
    supplied += list(kwargs.items())
    supplied[2:2] = [(None, a) for a in args]  # stats(text, ...): dots keep their order
    bound: dict[str, Any] = {}
    used = [0] * len(supplied)  # 0: unused, 1: partial match, 2: exact match
    # 1. exact matches
    for f in _STATCHECK_FORMALS:
        for j, (tag, value) in enumerate(supplied):
            if tag == f:
                if f in bound:
                    raise _ArgError(f'formal argument "{f}" matched by multiple actual arguments')
                bound[f] = value
                used[j] = 2
    # 2. partial matches (unique prefixes)
    for f in _STATCHECK_FORMALS:
        if f in bound:
            continue
        for j, (tag, value) in enumerate(supplied):
            if used[j] == 2 or not tag or not f.startswith(tag):
                continue
            if used[j] == 1:
                raise _ArgError(f"argument {j + 1} matches multiple formal arguments")
            if f in bound:
                raise _ArgError(f'formal argument "{f}" matched by multiple actual arguments')
            bound[f] = value
            used[j] = 1
    if any(tag and not used[j] for j, (tag, _) in enumerate(supplied)):
        raise _ArgError("unused argument")
    # 3. positional
    free = [f for f in _STATCHECK_FORMALS if f not in bound]
    for j, (tag, value) in enumerate(supplied):
        if tag is None:
            if not free:
                raise _ArgError("unused argument")
            bound[free.pop(0)] = value
    return bound


def _stats_generic(
    texts: list[str | None], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[list[int], dict[str, pd.Series]]:
    """``stats()``'s loop for arguments that rebind ``texts`` (one full statcheck() per text)."""
    import contextlib
    import io

    from pytacheck.stats.statcheck import _ON_WARNING, RError, _Abort, statcheck

    def abort(msg: str) -> None:
        raise _Abort(msg)

    sources: list[int] = []
    frames: list[pd.DataFrame] = []
    token = _ON_WARNING.set(abort)
    try:
        for i, txt in enumerate(texts):
            try:
                bound = _match_statcheck_args(txt, args, kwargs)
                with contextlib.redirect_stdout(io.StringIO()):
                    sc = statcheck(**bound)
            except (RError, _Abort, _ArgError, TypeError, ValueError):
                continue
            if sc is None or len(sc) == 0:
                continue
            frames.append(sc.drop(columns=["source"]))
            sources.extend([i] * len(sc))
    finally:
        _ON_WARNING.reset(token)
    if not frames:
        return [], {}
    out = pd.concat(frames, ignore_index=True)
    return sources, {c: out[c] for c in out.columns}


def stats(text: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """Check the statistics in a paper, paper list or text table (``stats()``).

    Port of ``R/stats.R::stats()``: every sentence that contains a digit is
    run through statcheck (:func:`pytacheck.stats.statcheck.statcheck`, which
    receives the extra arguments as R's ``...``: ``stat``, ``OneTailedTests``,
    ``alpha``, ``pEqualAlphaSig``, ``pZeroError``, ``OneTailedTxt``,
    ``AllPValues``, matched by R's rules, including unique prefixes and
    positional arguments). Returns statcheck's columns (without ``source``)
    followed by the columns of the matching sentence (``text``, ``paper_id``,
    ``header`` ...), or an empty DataFrame when nothing is found.

    As in R, a sentence whose check raises an error or a warning (for example
    an unparseable p-value such as ``p = .05-.10``) contributes no rows, and
    arguments statcheck() does not accept (a typo, ``messages``) silently give
    an empty result.
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

    texts = [None if pd.isna(t) else str(t) for t in table["text"].tolist()]
    subtext = object()
    try:
        bound = _match_statcheck_args(subtext, args, kwargs)
    except _ArgError:
        # every statcheck() call errors, and every error becomes data.frame()
        return pd.DataFrame()
    if bound["texts"] is subtext:
        bound.pop("texts")
        bound.pop("messages")
        sources, columns = _statcheck_quiet(texts, **bound)
    else:
        sources, columns = _stats_generic(texts, args, kwargs)
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
