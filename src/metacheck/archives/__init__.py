"""Research-data archives: finding repository links in papers and querying them.

Ports of metacheck's ``R/archive-*.R``, ``R/repo-*.R`` and ``R/cache.R``.
Each archive has a ``<host>_links(paper)`` function that finds its links in a
paper's URL table (and, for some hosts, bare mentions in the text) and a
``<host>_info()`` function that queries the host's API through
:mod:`metacheck.http`.

Public names are resolved lazily so importing this package stays cheap.
Shared private helpers used by every archive port live here:
:func:`_message` (metacheck's ``message()``), :func:`_tick` (a progress
bar's ``pb$tick(0, list(what = ...))``) and :func:`_spinner` (the
``if (is.null(pb)) pb <- pb(NA, "(:spin) :what")`` idiom).
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

# public name -> defining module
_EXPORTS: dict[str, str] = {
    "aspredicted_info": "metacheck.archives.aspredicted",
    "aspredicted_links": "metacheck.archives.aspredicted",
    "local_files": "metacheck.archives.local",
    "metacheck_cache_info": "metacheck.archives.cache",
    "osf_api_check": "metacheck.archives.osf",
    "osf_cache_clear": "metacheck.archives.osf",
    "osf_check_id": "metacheck.archives.osf",
    "osf_delay": "metacheck.archives.osf",
    "osf_file_download": "metacheck.archives.osf",
    "osf_get_all_pages": "metacheck.archives.osf",
    "osf_info": "metacheck.archives.osf",
    "osf_links": "metacheck.archives.osf",
    "osf_pat": "metacheck.archives.osf_helpers",
    "osf_preprint_list": "metacheck.archives.osf",
    "osf_type": "metacheck.archives.osf",
    "osf_user_projects": "metacheck.archives.osf_helpers",
    "repo_info_cache_clear": "metacheck.archives.info_cache",
    "zip_peek_cache_clear": "metacheck.archives.zip_peek_cache",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'metacheck.archives' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def _message(*parts: Any) -> None:
    """metacheck's ``message()``: informational output, silent unless verbose."""
    report_message: Callable[..., None] | None
    try:
        from metacheck.utils import message as report_message  # added by the report port
    except ImportError:
        report_message = None
    if report_message is not None:
        report_message(*parts)
        return
    from metacheck.config import verbose

    if verbose():
        print("".join("NA" if p is None else str(p) for p in parts), file=sys.stderr)


def _paper_urls(paper: Any) -> pd.DataFrame:
    """``paper_table(paper, "url")``, typed also for an empty paper list.

    metacheck 0.4.3.7 gives an empty paper list the same typed, empty tables as an empty
    paper (U79), so the link finders see ``href``, ``link_text``, ``text_id`` and
    ``paper_id`` on it; the table of an empty list used to have no columns at all.
    """
    from metacheck.papers.tables import empty_paper_table, paper_table

    urls = paper_table(paper, "url")
    if "href" not in urls.columns and len(urls) == 0:
        return empty_paper_table("url")
    return urls


def _tick(pb: Any, what: str) -> None:
    """``pb$tick(0, list(what = what))`` on a progress bar, if one was passed."""
    if pb is not None and hasattr(pb, "tick"):
        with contextlib.suppress(Exception):  # a progress display must never break a run
            pb.tick(0, {"what": what})


@contextlib.contextmanager
def _spinner(pb: Any, what: str | None = None) -> Iterator[Any]:
    """Use *pb*, or (R: ``if (is.null(pb))``) a new ``(:spin) :what`` spinner.

    A spinner made here shows *what* first and is terminated on exit
    (R: ``on.exit(pb$terminate())``); a bar passed in is left running.
    """
    if pb is not None:
        yield pb
        return
    try:
        from metacheck.utils import pb as make_pb

        bar = make_pb(None, "(:spin) :what")
    except Exception:  # a progress display must never break a run
        yield None
        return
    try:
        if what is not None:
            _tick(bar, what)
        yield bar
    finally:
        with contextlib.suppress(Exception):
            bar.terminate()
