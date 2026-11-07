"""``convert()``: one entry point for Grobid/bibr document conversion (``R/import-convert.R``).

Both bibr and Grobid convert PDFs; only bibr converts DOC/DOCX; Grobid TEI
XML files are converted with ``grobid_to_bibr()``. With ``method="auto"`` the
servers are tried in this order: metacheck's API on SURF Research Cloud (PDF
only), the TU/e Grobid server, a Grobid on this computer (``localhost:8070``;
a bibr on ``localhost:8000`` only while ``_BIBR_WORKING``), then the other
servers of metacheck's online list. A bibr server named in ``BIBR_URL`` (with
``BIBR_API_KEY``) comes before all of those, for ``method="auto"`` and
``method="bibr"`` (pytacheck's addition; D59).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from metacheck._r.regex import grepl, sub
from metacheck._save_path import NO_SAVE_PATH, require_save_path

__all__ = ["convert"]

PathLikeStr = str | PathLike[str]

_METHODS = ("auto", "bibr", "grobid", "xml")
SERVERS_URL = "https://www.scienceverse.org/metacheck/convert.json"
#: the TU/e Grobid server, tried right after metacheck's server on SURF
TUE_GROBID_URL = "https://grobid.hti.ieis.tue.nl"
#: whether convert() looks for a bibr server on its own (a local bibr, and the bibr servers of
#: the online list); ``False`` while bibr does not work (metacheck's ``.bibr_working``). A
#: server named in ``BIBR_URL`` is still used.
_BIBR_WORKING = False


def _message(msg: str) -> None:
    from metacheck.config import verbose

    if verbose():
        from rich.console import Console

        Console(stderr=True).print(msg, markup=False, highlight=False)


def _online(url: str) -> bool:
    from metacheck.utils import online

    return bool(online(url))


def _convert_servers() -> list[dict[str, Any]]:
    """The online list of conversion servers (``.convert_servers()``); ``[]`` when it cannot
    be read."""
    from metacheck import http

    if not _online(SERVERS_URL):
        return []
    try:
        resp = http.request("GET", SERVERS_URL)
        if resp is None or resp.status_code != 200:
            return []
        servers = resp.json()
    except Exception:
        return []
    return [s for s in servers if isinstance(s, dict)] if isinstance(servers, list) else []


def _same_url(a: Any, b: Any) -> bool:
    return str(a).rstrip("/") == str(b).rstrip("/")


def _convert_candidates() -> list[dict[str, Any]]:
    """The servers convert() tries after metacheck's server on SURF, in order
    (``.convert_candidates()``): the TU/e Grobid server (even when the online list cannot be
    read), a Grobid and a bibr on this computer, then the other servers of the online list."""
    online_list = _convert_servers()
    tue = [s for s in online_list if _same_url(s.get("url"), TUE_GROBID_URL)]
    if not tue:
        tue = [{"id": "TUE grobid", "service": "grobid", "url": TUE_GROBID_URL}]
    local = [
        {"id": "local grobid", "service": "grobid", "url": "http://localhost:8070"},
        {"id": "local bibr", "service": "bibr", "url": "http://localhost:8000"},
    ]
    rest = [s for s in online_list if not _same_url(s.get("url"), TUE_GROBID_URL)]
    return [tue[0], *local, *rest]


def _grobid_first_alive(default: str) -> str:
    """The Grobid server ``convert_grobid()`` uses when none is given (``._grobid_first_alive()``):
    the first Grobid server of :func:`_convert_candidates` that answers (TU/e, this computer,
    Hugging Face; metacheck's server on SURF returns paper JSON, not Grobid's XML), else
    *default*, so the caller's own check reports why."""
    from metacheck.io.grobid import _grobid_isalive

    for s in _convert_candidates():
        if s.get("service") != "grobid":
            continue
        _message(f"Checking {s.get('id')}")
        if _grobid_isalive(s["url"], error=False):
            _message(f"Using {s.get('id')}")
            return str(s["url"])
    return default


def convert(
    file_path: PathLikeStr | Sequence[PathLikeStr],
    save_path: PathLikeStr = NO_SAVE_PATH,
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
        Directory to save the JSON file(s) in. There is no default folder.
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
        ``api_key``, ``start_page``, ...). A server, key or backend given here is
        used as given. With ``BIBR_URL`` set (and no ``api_url`` or other ``backend``
        passed), ``method="auto"`` and ``method="bibr"`` use that server through
        ``convert_bibr(backend="bibr")``; ``BIBR_API_URL`` does not steer ``convert()``.

    Returns
    -------
    The path(s) to the JSON file(s).
    """
    from metacheck.io.bibr_convert import (
        _bibr_isalive,
        _bibr_steering_url,
        _surf_isalive,
        convert_bibr,
    )
    from metacheck.io.grobid import _grobid_isalive, convert_grobid, grobid_to_bibr

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
    # before any file is sent to a server: there is no default folder
    require_save_path(save_path)

    from metacheck.utils import match_arg

    method = match_arg(method, _METHODS)

    # a bibr server that was asked for (BIBR_URL) beats guessing: no probe, so a wrong address
    # or token is reported as it is instead of falling back to another service
    if method in ("auto", "bibr") and args.get("api_url") is None:
        configured = _bibr_steering_url()
        if configured and args.get("backend") in (None, "auto", "bibr"):
            _message("Using the bibr server in BIBR_URL")
            method = "bibr"
            args["api_url"] = configured
            args["backend"] = "bibr"

    # choose a server: metacheck's API on SURF Research Cloud, the TU/e Grobid server, a Grobid
    # (then a bibr) on this computer, then the other servers of the online list. A server, key
    # or backend given in `...` is used as given.
    given = (
        args.get("api_url") is not None
        or args.get("api_key") is not None
        or args.get("backend") is not None
    )

    # SURF converts PDFs, not DOC or DOCX files; it returns the paper JSON whichever method was
    # asked for
    surf = method != "xml" and not docs and not given and _surf_isalive(error=False)
    if surf:
        _message("Using the SURF API")
        method = "bibr"
        args["backend"] = "surf"

    # a server given with method "auto": Grobid when it answers as one, else bibr
    if given and method == "auto":
        api_url = args.get("api_url")
        method = (
            "grobid" if api_url is not None and _grobid_isalive(api_url, error=False) else "bibr"
        )

    # XML is converted locally, so no server is looked up for it (offline, the lookup stopped an
    # XML conversion; U18)
    if not surf and not given and method != "xml":
        for s in _convert_candidates():
            # skip if the server doesn't match a set method
            if method not in (s.get("service"), "auto"):
                continue
            # a bibr server is not asked while bibr does not work (_BIBR_WORKING)
            if s.get("service") == "bibr" and not _BIBR_WORKING:
                continue
            _message(f"Checking {s.get('id')}")
            if s.get("service") == "grobid":
                up = _grobid_isalive(s["url"], error=False)
            else:
                up = _bibr_isalive(s["url"], os.environ.get("SCIVRS_API_KEY", ""), error=False)
            if up:
                _message(f"Using {s.get('id')}")
                method = str(s["service"])
                args["api_url"] = s["url"]
                if s.get("id") == "local bibr":
                    # a bibr serve, whatever SCIVRS_API_KEY says: the platform's job queue is
                    # not there
                    args["backend"] = "selfhosted"
                break
        if method == "auto" or (method == "grobid" and args.get("api_url") is None):
            raise ConnectionError(
                "No conversion server answers: not metacheck's server on SURF, "
                "the TU/e GROBID server, a GROBID on this computer "
                "(http://localhost:8070), or the servers of the online list."
            )

    args["file_path"] = file_path
    args["save_path"] = save_path

    if method == "xml":
        return grobid_to_bibr(file_path, save_path, crossref_lookup)
    if method == "grobid":
        # keep_xml = FALSE: a temporary folder, in which convert_grobid() names each XML file
        # after its PDF (the paper ID and JSON file name come from the XML file name); removed
        # when the function exits (on.exit(unlink(tmp_dir, recursive = TRUE)))
        tmp_dir: str | None = None
        if keep_xml:
            args["save_path"] = sub(r"\.json$", r"\.xml", os.fspath(save_path))
        else:
            tmp_dir = tempfile.mkdtemp(prefix="grobid")
            args["save_path"] = tmp_dir
        try:
            grobid_args = ("file_path", "save_path", "api_url", "start_page", "end_page")
            tmp_xml = convert_grobid(**{k: v for k, v in args.items() if k in grobid_args})
            return grobid_to_bibr(tmp_xml, save_path, crossref_lookup)
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    # bibr. R passes the *names* of the valid arguments to convert_bibr()
    # (`do.call(convert_bibr, valid_args)`), which cannot work; the arguments
    # themselves are passed here.
    import inspect

    bibr_args = inspect.signature(convert_bibr).parameters
    return convert_bibr(**{k: v for k, v in args.items() if k in bibr_args})
