"""Cap reporting (port of ``R/cap-prompt.R``).

When a resource cap (download size, codebook LLM calls) refuses a unit of
work, metacheck does not prompt: it reports what was skipped -- inline, and
as a warning so ``warnings.catch_warnings()`` / manifests capture it -- and
continues. The message (built by ``cap_gate_*()``) names the parameter and
the value that would include the skipped unit on a later run.
"""

from __future__ import annotations

import math
import sys
import warnings

__all__ = ["cap_report"]


def cap_report(message: str) -> None:
    """Port of ``.cap_report()`` (R keeps it internal; here it stays public): show *message* now and raise it as a warning."""
    print(message, file=sys.stderr)
    warnings.warn(message, stacklevel=2)


def _cap_report_partial(partial: object, repo_urls: object) -> None:
    """Port of ``.cap_report_partial()``: warn, once per repository, that one whose files are about
    to be downloaded was listed only in part.

    repo_check's listing of an OSF project stopped at ``osf_max_requests`` (its ``partial_repos``),
    so files in folders not yet opened are not downloaded either. *partial* is that table,
    *repo_urls* the repositories this download uses. Reported like a size cap (:func:`cap_report`),
    so a missing file is never missing without a word.
    """
    import pandas as pd

    if not isinstance(partial, pd.DataFrame) or len(partial) == 0 or "repo_url" not in partial:
        return
    urls = set(repo_urls) if repo_urls is not None else set()  # type: ignore[call-overload]
    shown = partial[partial["repo_url"].isin(urls)].drop_duplicates("repo_url")
    for _, row in shown.iterrows():
        n = row.get("files_listed")
        listed = (
            ""
            if n is None or pd.isna(n)
            else f" ({int(n)} file{'' if int(n) == 1 else 's'} listed)"
        )
        cap_report(
            f"Repository {row['repo_url']} was listed only in part{listed}: the OSF listing "
            "stopped at the request limit, so files in folders not yet opened are not "
            "downloaded. Run again with `cache = TRUE` to list the next part, or raise "
            "`osf_max_requests`."
        )


def _cap_size_str(bytes: float | None) -> str:
    """Port of ``.cap_size_str()``: a byte count as a short string (``"5.4 GB"``)."""
    if bytes is None or (isinstance(bytes, float) and math.isnan(bytes)):
        return "unknown size"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 1 if bytes <= 0 else min(len(units), 1 + math.floor(math.log(bytes) / math.log(1024)))
    return f"{bytes / 1024 ** (i - 1):.1f} {units[i - 1]}"
