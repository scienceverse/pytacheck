"""Tight integration with bibr, the ScienceVerse extraction pipeline.

bibr turns PDF/DOCX/HTML/ePub papers into bibr export schema 12.x, the
schema pytacheck targets. With ``pip install "pytacheck[bibr]"`` it runs
in-process: extraction results are read straight into native 12.x
:class:`~pytacheck.papers.Paper` objects (see :func:`pytacheck.papers.from_bibr`
and :mod:`pytacheck.io.bibr12`), exactly as the same export read from JSON,
without writing a file. ``paper_write()`` saves such a paper as a 12.0 file.

Without the extra, :func:`chew` raises an error explaining how to install
it; a remote bibr server can still be used through ``convert_bibr()``.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from pytacheck.papers.io import from_bibr
from pytacheck.papers.model import Paper, PaperList

__all__ = ["BibrNotInstalledError", "bibr_available", "bibr_version", "chew"]

INSTALL_HINT = (
    'Install the bibr extra to extract papers from documents: pip install "pytacheck[bibr]"'
)


class BibrNotInstalledError(ImportError):
    """bibr is needed but not installed."""


@functools.cache
def bibr_available() -> bool:
    """Whether bibr can be imported."""
    try:
        import bibr  # noqa: F401
    except ImportError:
        return False
    return True


def bibr_version() -> str | None:
    """The installed bibr version, or ``None``."""
    if not bibr_available():
        return None
    from importlib.metadata import version

    return version("bibr")


def _import_bibr() -> Any:
    try:
        import bibr
    except ImportError as exc:
        raise BibrNotInstalledError(INSTALL_HINT) from exc
    return bibr


def chew(
    path: str | PathLike[str] | Sequence[str | PathLike[str]],
    include_images: bool = False,
    **options: Any,
) -> Paper | PaperList:
    """Extract paper(s) with bibr and return pytacheck paper objects.

    The papers are native bibr 12.x papers (their ``info`` table says
    ``schema_version`` 12.x), as :func:`pytacheck.read` returns for bibr's
    JSON export. An export in any other schema version raises metacheck's
    error. ``options`` are passed to ``bibr.chew()`` (e.g. ``refs="llm"``,
    ``pages="1-10"``, ``ocr=...``). Figure images are requested from bibr
    only when *include_images* is true. Failed files in a batch are skipped
    and logged.
    """
    bibr = _import_bibr()
    if include_images:
        options.setdefault("figure_images", True)
    target: Any = str(path) if isinstance(path, str | PathLike) else [str(p) for p in path]
    result = bibr.chew(target, **options)
    if isinstance(result, list):
        from pytacheck.log import logger

        papers: list[Paper] = []
        for item in result:
            if getattr(item, "ok", True) and hasattr(item, "data"):
                papers.append(from_bibr(item.data, include_images=include_images))
            else:
                logger(
                    "chew",
                    {
                        "file": str(getattr(item, "path", "")),
                        "error": str(getattr(item, "error", item)),
                    },
                )
        return PaperList(papers)
    paper = from_bibr(result.data, include_images=include_images)
    if paper.paper_id is None:
        paper.paper_id = Path(str(path)).stem
    return paper
