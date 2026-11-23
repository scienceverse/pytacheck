"""Repositories refused for their number of files (metacheck 0.4.4.30 to 0.4.4.35, #702, #706).

Ports of R/repo-download.R's ``.repo_count_message()``, ``.repos_over_count()``,
``.count_refused_add()`` and ``.count_refused_note()``: data_check, code_check and
reproducibility_check share them. Kept in a module of its own so that no check has to import
another's private helpers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from metacheck._values import is_missing


def _int_str(x: float) -> str:
    if is_missing(x):
        return "NA"
    return str(int(x)) if float(x) == int(x) else str(x)


def _repo_count_message(repo: Any, n: float, max_files_per_repo: float) -> str:
    """The message for a repository refused for holding more files than max_files_per_repo,
    the same in download_repo_files(), data_check and code_check."""
    return (
        f"Repository {repo} holds {_int_str(n)} files, exceeding the "
        f"{_int_str(max_files_per_repo)}-file cap: skipped entirely (not size-capped, "
        "file-count-capped). Raise `max_files_per_repo` to include it."
    )


_COUNT_COLS = ["repo_url", "n_files", "message"]


def _repos_over_count(listing: pd.DataFrame | None, max_files_per_repo: float) -> pd.DataFrame:
    """The repositories of *listing* (a file table with ``repo_url``, a repository's full
    listing) that hold more files than *max_files_per_repo*: ``repo_url``, ``n_files``,
    ``message`` (0 rows when none). data_check and code_check refuse them before their first
    step on each file (#702)."""
    none = pd.DataFrame(
        {
            "repo_url": pd.Series([], dtype="object"),
            "n_files": pd.Series([], dtype="int64"),
            "message": pd.Series([], dtype="object"),
        }
    )
    if (
        not math.isfinite(max_files_per_repo)
        or not isinstance(listing, pd.DataFrame)
        or len(listing) == 0
        or "repo_url" not in listing.columns
    ):
        return none
    counts = listing["repo_url"].dropna().astype(str).value_counts(sort=False)
    # R's table() lists the repositories in sorted order
    counts = counts.sort_index()
    over: pd.Series[int] = counts[counts.to_numpy() > max_files_per_repo]
    if len(over) == 0:
        return none
    return pd.DataFrame(
        {
            "repo_url": list(over.index),
            "n_files": [int(v) for v in over.to_numpy()],
            "message": [
                _repo_count_message(r, int(v), max_files_per_repo)
                for r, v in zip(over.index, over.to_numpy(), strict=True)
            ],
        }
    )


def _count_refused_add(own: pd.DataFrame | None, earlier: Any) -> pd.DataFrame | None:
    """The repositories an earlier check refused for their number of files (data_check's or
    code_check's ``count_refused``), added to a check's own (#706)."""
    if not isinstance(earlier, pd.DataFrame) or len(earlier) == 0 or "repo_url" not in earlier:
        return own
    if own is None or len(own) == 0:
        return earlier.reset_index(drop=True)
    new = earlier[~earlier["repo_url"].isin(own["repo_url"])]
    return pd.concat([own, new], ignore_index=True)


def _count_refused_note(text: str, refused: Any) -> Mapping[str, Any] | None:
    """A check that found nothing to check names the repositories an earlier check refused for
    their number of files, and is then yellow, not "na" (#706). Returns
    ``{"summary_text", "traffic_light"}``, or ``None`` when none was refused."""
    if not isinstance(refused, pd.DataFrame) or len(refused) == 0 or "message" not in refused:
        return None
    return {
        "summary_text": "".join(f"\n-  {t}" for t in [text, *refused["message"].tolist()]),
        "traffic_light": "yellow",
    }
