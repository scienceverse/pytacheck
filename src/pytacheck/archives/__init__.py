"""Research-data archives: finding repository links in papers and querying them.

Ports of metacheck's ``R/archive-*.R``, ``R/repo-*.R`` and ``R/cache.R``.
Each archive has a ``<host>_links(paper)`` function that finds its links in a
paper's URL table (and, for some hosts, bare mentions in the text) and a
``<host>_info()`` function that queries the host's API through
:mod:`pytacheck.http`.

Public names are resolved lazily so importing this package stays cheap.
Shared private helpers used by every archive port live here:
:func:`_message` (metacheck's ``message()``) and :func:`_tick` (a progress
bar's ``pb$tick(0, list(what = ...))``).
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from typing import Any

# public name -> defining module
_EXPORTS: dict[str, str] = {
    "aspredicted_info": "pytacheck.archives.aspredicted",
    "aspredicted_links": "pytacheck.archives.aspredicted",
    "local_files": "pytacheck.archives.local",
    "metacheck_cache_info": "pytacheck.archives.cache",
    "osf_api_check": "pytacheck.archives.osf",
    "osf_cache_clear": "pytacheck.archives.osf",
    "osf_check_id": "pytacheck.archives.osf",
    "osf_delay": "pytacheck.archives.osf",
    "osf_file_download": "pytacheck.archives.osf",
    "osf_get_all_pages": "pytacheck.archives.osf",
    "osf_info": "pytacheck.archives.osf",
    "osf_links": "pytacheck.archives.osf",
    "osf_pat": "pytacheck.archives.osf_helpers",
    "osf_preprint_list": "pytacheck.archives.osf",
    "osf_type": "pytacheck.archives.osf",
    "osf_user_projects": "pytacheck.archives.osf_helpers",
    "repo_info_cache": "pytacheck.archives.info_cache",
    "repo_info_cache_clear": "pytacheck.archives.info_cache",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'pytacheck.archives' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def _message(*parts: Any) -> None:
    """metacheck's ``message()``: informational output, silent unless verbose."""
    try:
        from pytacheck.utils import message as report_message  # added by the report port
    except ImportError:
        report_message = None
    if report_message is not None:
        report_message(*parts)
        return
    from pytacheck.config import verbose

    if verbose():
        print("".join("NA" if p is None else str(p) for p in parts), file=sys.stderr)


def _tick(pb: Any, what: str) -> None:
    """``pb$tick(0, list(what = what))`` on a progress bar, if one was passed."""
    if pb is not None and hasattr(pb, "tick"):
        with contextlib.suppress(Exception):  # a progress display must never break a run
            pb.tick(0, {"what": what})
