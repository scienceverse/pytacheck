"""``convert()``: one entry point for Grobid/bibr document conversion (``R/import-convert.R``).

Both bibr and Grobid convert PDFs; only bibr converts DOC/DOCX; Grobid TEI
XML files are converted with ``grobid_to_bibr()``. With ``method="auto"``
a local Grobid (``localhost:8070``) or bibr (``localhost:8000``) server is
preferred, then the first live server of metacheck's online priority list.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from pytacheck._r.regex import grepl, sub

__all__ = ["convert"]

PathLikeStr = str | PathLike[str]

_METHODS = ("auto", "bibr", "grobid", "xml")
SERVERS_URL = "https://www.scienceverse.org/metacheck/convert.json"


def _message(msg: str) -> None:
    from pytacheck.config import verbose

    if verbose():
        from rich.console import Console

        Console(stderr=True).print(msg, markup=False, highlight=False)


def _server_list() -> list[dict[str, Any]]:
    from pytacheck import http

    resp = http.request("GET", SERVERS_URL)
    if resp is None or resp.status_code != 200:
        raise ConnectionError("No local grobid or bibr detected, online versions not available")
    servers: list[dict[str, Any]] = resp.json()
    return servers


def _online(url: str) -> bool:
    from pytacheck.utils import online

    return bool(online(url))


def _tempfile(fileext: str) -> str:
    """R's ``tempfile(fileext = ...)``: a fresh path in the temp directory (not created)."""
    import secrets

    return os.path.join(tempfile.gettempdir(), f"file{secrets.token_hex(6)}{fileext}")


def _unlink(paths: Any) -> None:
    """R's ``unlink()``: remove files (``NA`` / missing ones are ignored, directories kept)."""
    if paths is None:
        return
    for p in [paths] if isinstance(paths, str | PathLike) else list(paths):
        if p is not None and Path(p).is_file():
            Path(p).unlink(missing_ok=True)


def convert(
    file_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr = ".",
    method: str = "auto",
    crossref_lookup: bool = False,
    keep_xml: bool = True,
    **kwargs: Any,
) -> Any:
    """Port of ``R/import-convert.R::convert()``: convert documents to bibr JSON.

    Parameters
    ----------
    file_path:
        A document, a list of documents, or a directory of documents.
    save_path:
        Directory to save the JSON file(s) in.
    method:
        ``"auto"``, ``"bibr"``, ``"grobid"`` or ``"xml"``. XML input always
        uses ``"xml"`` (``grobid_to_bibr()``), DOC/DOCX-only input
        ``"bibr"``.
    crossref_lookup:
        Whether to add the ``bib_match`` table from CrossRef.
    keep_xml:
        With Grobid, whether to keep the intermediate XML files.
    **kwargs:
        Passed on to ``convert_bibr()`` / ``convert_grobid()`` (``api_url``,
        ``api_key``, ``start_page``, ...).

    Returns
    -------
    The path(s) to the JSON file(s).
    """
    from pytacheck.io.bibr_convert import _bibr_isalive, convert_bibr
    from pytacheck.io.grobid import _grobid_isalive, convert_grobid, grobid_to_bibr

    args: dict[str, Any] = dict(kwargs)

    # check file types (xml/pdf/doc/docx)
    if isinstance(file_path, str | PathLike):
        paths: list[str] = [os.fspath(file_path)]
    else:
        paths = [os.fspath(f) for f in file_path]
    if len(paths) == 1 and Path(paths[0]).is_dir():
        # list.files(): names (files and directories), dotfiles excluded
        files = sorted(p.name for p in Path(paths[0]).iterdir() if not p.name.startswith("."))
    else:
        files = paths
    xmls = sum(grepl(r"\.xml$", files, ignore_case=True))
    pdfs = sum(grepl(r"\.pdf$", files, ignore_case=True))
    docs = sum(grepl(r"\.docx?$", files, ignore_case=True))

    if xmls:
        method = "xml"
    elif pdfs:
        pass
    elif docs:
        method = "bibr"
    else:
        raise ValueError("No PDF, XML, DOC or DOCX files detected.")

    from pytacheck.utils import match_arg

    method = match_arg(method, _METHODS)

    # auto-detect method: local grobid > local bibr > online priority list
    if method == "auto":
        grobid_local_url = "http://localhost:8070"
        bibr_local_url = "http://localhost:8000"
        if _grobid_isalive(grobid_local_url, error=False):
            _message("Using local grobid")
            method = "grobid"
            args["api_url"] = grobid_local_url
        elif _bibr_isalive(bibr_local_url, None, error=False):
            _message("Using local bibr")
            method = "bibr"
            args["api_url"] = bibr_local_url

    if method == "auto" or args.get("api_url") is None:
        if not _online(SERVERS_URL):
            raise ConnectionError("No local grobid or bibr detected, online versions not available")
        for s in _server_list():
            if method not in (s.get("service"), "auto"):
                continue
            _message(f"Checking {s.get('id')}")
            if s.get("service") == "grobid":
                up = _grobid_isalive(s["url"], error=False)
            else:
                api_key = args.get("api_key")
                if api_key is None:  # args$api_key %||% Sys.getenv("SCIVRS_API_KEY")
                    api_key = os.environ.get("SCIVRS_API_KEY", "")
                up = _bibr_isalive(s["url"], api_key, error=False)
            if up:
                _message(f"Using {s.get('id')}")
                method = s["service"]
                args["api_url"] = s["url"]
                break

    args["file_path"] = file_path
    args["save_path"] = save_path

    if method == "xml":
        return grobid_to_bibr(file_path, save_path, crossref_lookup)
    if method == "grobid":
        # on.exit(unlink(tmp_xml)) with keep_xml = FALSE: removes whatever
        # tmp_xml names when the function exits (the converted XML file(s))
        cleanup: Any = None
        if keep_xml:
            args["save_path"] = sub(r"\.json$", r"\.xml", os.fspath(save_path))
        else:
            cleanup = _tempfile(".xml")  # R's tempfile(): a name, not a file
            args["save_path"] = cleanup
        try:
            grobid_args = ("file_path", "save_path", "api_url", "start_page", "end_page")
            tmp_xml = convert_grobid(**{k: v for k, v in args.items() if k in grobid_args})
            if cleanup is not None:
                cleanup = tmp_xml
            return grobid_to_bibr(tmp_xml, save_path, crossref_lookup)
        finally:
            _unlink(cleanup)
    # bibr. R passes the *names* of the valid arguments to convert_bibr()
    # (`do.call(convert_bibr, valid_args)`), which cannot work; the arguments
    # themselves are passed here.
    import inspect

    bibr_args = inspect.signature(convert_bibr).parameters
    return convert_bibr(**{k: v for k, v in args.items() if k in bibr_args})
