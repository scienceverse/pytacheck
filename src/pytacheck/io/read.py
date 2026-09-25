"""``read()``: load papers from bibr JSON, Grobid XML or source documents."""

from __future__ import annotations

import os
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from pytacheck.io._files import list_files
from pytacheck.log import logger
from pytacheck.papers.io import read_bibr
from pytacheck.papers.model import Paper, PaperList

__all__ = ["SOURCE_EXTENSIONS", "read"]

# Documents bibr can extract (read() hands these to bibr when it is installed).
SOURCE_EXTENSIONS = (".pdf", ".docx", ".doc", ".html", ".htm", ".xhtml", ".epub", ".nxml")


def _read_xml(path: Path, schema_version: str | None = "12.0") -> Paper:
    # R: read() calls .grobid_to_bibr(fp) (grobid_to_bibr() would save JSON)
    from pytacheck.io.grobid import _grobid_to_bibr

    return _grobid_to_bibr(path, None, schema_version)


def _read_one(
    path: Path, include_images: bool, bibr_options: dict[str, Any], schema_version: str | None
) -> Paper:
    # R: grepl("\\.json$", fp, ignore.case = TRUE), then "\\.xml$"
    name = path.name.lower()
    if name.endswith(".json"):
        return read_bibr(path, include_images)
    if name.endswith(".xml"):
        return _read_xml(path, schema_version)
    if path.suffix.lower() in SOURCE_EXTENSIONS:
        from pytacheck.io.bibr import chew

        return chew(path, include_images=include_images, **bibr_options)
    raise ValueError(f"Don't know how to read {path.name!r}")


def read(
    file_path: str | PathLike[str] | Sequence[str | PathLike[str]],
    include_images: bool = False,
    recursive: bool = False,
    *,
    schema_version: str | None = "12.0",
    **bibr_options: Any,
) -> Paper | PaperList:
    """Read paper(s) from bibr JSON or Grobid XML files, or a directory of them.

    Like ``metacheck::read()``: a directory is scanned for ``.json``/``.xml``
    files as ``list.files()`` does (lower-case extensions only, hidden files
    and directories skipped, full paths in R's sort order), a ``.xml`` is
    skipped when a ``.json`` of the same name is read and repeated paths are
    read once. Unreadable files are logged and skipped (a deliberate
    difference: metacheck then fails altogether), and a single paper is
    returned unwrapped. A JSON file with a root ``schema_version`` is a bibr export:
    schema 12.x is read natively (:mod:`pytacheck.io.bibr12`), any other
    version (bibr 11.x included) is refused; files without one (bibr v10.x and
    older) are read exactly as metacheck reads them. Source documents (PDF,
    DOCX, HTML, ...) are extracted with bibr when the ``pytacheck[bibr]`` extra
    is installed; ``bibr_options`` are passed to :func:`pytacheck.io.bibr.chew`.

    ``schema_version`` says how a Grobid TEI (``.xml``) file is converted:
    ``"12.0"`` (the default) gives a paper in bibr export schema 12.x form, as
    ``grobid_to_bibr(schema_version="12.0")`` does; ``None`` gives metacheck's
    older conversion, which is what ``metacheck::read()`` returns. Deliberate
    difference: metacheck's ``read()`` has no such argument and always converts
    TEI to its older format. JSON files are read as they are.
    """
    if schema_version is not None and (
        not isinstance(schema_version, str) or schema_version != "12.0"
    ):
        raise ValueError('schema_version must be None or "12.0"')
    from pytacheck._r.regex import grep, gsub

    if isinstance(file_path, str | PathLike):
        names = [os.fspath(file_path)]
    else:
        names = [os.fspath(p) for p in file_path]

    # R: list.files(dir, "\\.(json|xml)$", recursive = recursive, full.names = TRUE)
    if len(names) == 1 and os.path.isdir(os.path.expanduser(names[0])):
        names = list_files(names[0], r"\.(json|xml)$", recursive=recursive)
    if not names:
        print("No JSON or XML files found.")
        return PaperList()

    # R: setdiff(file_path, gsub("\\.json$", ".xml", grep("\\.json$", file_path)))
    # drops the XML twin of a JSON file (names compared exactly, case-sensitive)
    # and repeated names
    xml_dupes = set(gsub(r"\.json$", ".xml", grep(r"\.json$", names, value=True)))
    kept: dict[str, None] = {}
    for name in names:
        if name not in xml_dupes:
            kept.setdefault(name)
    paths = [Path(name) for name in kept]

    papers: list[Paper] = []
    for p in paths:
        try:
            papers.append(_read_one(p, include_images, bibr_options, schema_version))
        except Exception as exc:  # R: tryCatch(error = ...) around every file
            logger("read", {"file": str(p), "error": str(exc)})
            if len(paths) == 1:
                raise
    plist = PaperList(papers)
    return plist[0] if len(plist) == 1 else plist
