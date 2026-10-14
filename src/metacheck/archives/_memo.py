"""Answers remembered for the rest of the session.

metacheck keeps a few lookups in package-level environments for as long as the
R session runs (a DOI's registered URL, whether an endpoint exists at all).
:func:`session_memo` gives such a store; :func:`clear_session_memos` empties
every one of them, for tests and for the parity runner, whose cases must not
see each other's answers.
"""

from __future__ import annotations

from typing import Any

_MEMOS: list[dict[str, Any]] = []


def session_memo() -> dict[str, Any]:
    """A new dict that lives as long as the process (cleared by :func:`clear_session_memos`)."""
    memo: dict[str, Any] = {}
    _MEMOS.append(memo)
    return memo


def clear_session_memos() -> None:
    """Forget every answer remembered through :func:`session_memo`."""
    for memo in _MEMOS:
        memo.clear()
