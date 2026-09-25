"""``read()``: load papers from bibr JSON, Grobid XML or source documents."""

from __future__ import annotations

from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from pytacheck.log import logger
from pytacheck.papers.io import read_bibr
from pytacheck.papers.model import Paper, PaperList

__all__ = ["SOURCE_EXTENSIONS", "read"]

# Documents bibr can extract (read() hands these to bibr when it is installed).
SOURCE_EXTENSIONS = (".pdf", ".docx", ".doc", ".html", ".htm", ".xhtml", ".epub", ".nxml")


def _read_xml(path: Path) -> Paper:
    # R: read() calls .grobid_to_bibr(fp) (grobid_to_bibr() would save JSON)
    from pytacheck.io.grobid import _grobid_to_bibr

    return _grobid_to_bibr(path)


def _read_one(path: Path, include_images: bool, bibr_options: dict[str, Any]) -> Paper:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return read_bibr(path, include_images)
    if suffix == ".xml":
        return _read_xml(path)
    if suffix in SOURCE_EXTENSIONS:
        from pytacheck.io.bibr import chew

        return chew(path, include_images=include_images, **bibr_options)
    raise ValueError(f"Don't know how to read {path.name!r}")


def read(
    file_path: str | PathLike[str] | Sequence[str | PathLike[str]],
    include_images: bool = False,
    recursive: bool = False,
    **bibr_options: Any,
) -> Paper | PaperList:
    """Read paper(s) from bibr JSON or Grobid XML files, or a directory of them.

    Like ``metacheck::read()``: a directory is scanned for ``.json``/``.xml``
    files (a ``.xml`` is skipped when a ``.json`` of the same name exists),
    unreadable files are logged and skipped, and a single paper is returned
    unwrapped. A JSON file with a root ``schema_version`` is a bibr export:
    schema 12.x is read natively (:mod:`pytacheck.io.bibr12`), any other
    version (bibr 11.x included) is refused; files without one (bibr v10.x and
    older) are read exactly as metacheck reads them. Source documents (PDF, DOCX, HTML, ...) are extracted with
    bibr when the ``pytacheck[bibr]`` extra is installed; ``bibr_options``
    are passed to :func:`pytacheck.io.bibr.chew`.
    """
    if isinstance(file_path, str | PathLike):
        paths = [Path(file_path)]
    else:
        paths = [Path(p) for p in file_path]

    if len(paths) == 1 and paths[0].is_dir():
        root = paths[0]
        pattern = "**/*" if recursive else "*"
        paths = sorted(
            p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in (".json", ".xml")
        )
    if not paths:
        print("No JSON or XML files found.")
        return PaperList()

    json_stems = {p.with_suffix("") for p in paths if p.suffix.lower() == ".json"}
    paths = [
        p for p in paths if not (p.suffix.lower() == ".xml" and p.with_suffix("") in json_stems)
    ]

    papers: list[Paper] = []
    for p in paths:
        try:
            papers.append(_read_one(p, include_images, bibr_options))
        except (OSError, ValueError, NotImplementedError) as exc:
            logger("read", {"file": str(p), "error": str(exc)})
            if len(paths) == 1:
                raise
    plist = PaperList(papers)
    return plist[0] if len(plist) == 1 else plist
