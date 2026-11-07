"""A PsyArXiv preprint named by a link, a DOI or an id: validate it, fetch its PDF.

Only the id goes on. The addresses are built here from the PsyArXiv API address and the id,
so the field cannot make this server fetch from anywhere else. A file comes from PsyArXiv
itself or, when PsyArXiv only has that link, from the OSF.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from metacheck.web.config import MAX_UPLOAD_BYTES

__all__ = ["PsyArxivError", "fetch_preprint", "psyarxiv_id"]

#: sent with every request, so PsyArXiv can tell who asks
USER_AGENT = "pytacheck site (https://github.com/scienceverse/pytacheck)"
_BAD_ID = (
    "That does not look like a PsyArXiv link, DOI or id "
    "(such as https://next.psyarxiv.org/preprint/28j7s)."
)


class PsyArxivError(Exception):
    """The preprint cannot be fetched; the message says why, for the person who asked."""


def psyarxiv_id(text: str | None) -> str:
    """The preprint id in *text*: an id, a PsyArXiv, OSF or psyarxiv.com link, or a PsyArXiv
    DOI (``10.31234/osf.io/<id>``, with or without ``_v<n>``).

    Raises :class:`ValueError` with a message for the person when it is none of these.
    """
    x = (text or "").strip()
    if len(x) > 300:
        raise ValueError("That PsyArXiv link is too long.")
    x = re.sub(r"^https?://(dx\.)?doi\.org/", "", x)
    x = re.sub(r"^(doi:)?10\.31234/osf\.io/", "", x, flags=re.IGNORECASE)
    x = re.sub(r"^https?://[^/]+/(preprints?/(psyarxiv/)?)?", "", x)
    x = re.sub(r"[/?#].*$", "", x)
    x = re.sub(r"_v\d+$", "", x).lower()
    if not re.fullmatch(r"[a-z0-9]{5,12}", x):
        raise ValueError(_BAD_ID)
    return x


def _site(api: str) -> str:
    """The PsyArXiv site that an API address belongs to (``https://next.psyarxiv.org``)."""
    return re.sub(r"/api(/.*)?$", "", api)


def _file_address(record: dict[str, Any], site: str) -> str | None:
    """Where the preprint's file is: the record's own link, which is relative to the site (an
    absolute one is accepted only on that site), else the OSF link of its latest version."""
    versions = record.get("versions")
    latest = versions[0] if isinstance(versions, list) and versions else {}
    if not isinstance(latest, dict):
        latest = {}
    src = record.get("fileUrl") or latest.get("fileUrl")
    if isinstance(src, str):
        if src.startswith("/") and not src.startswith("//"):
            return site + src
        if src.startswith(site + "/"):
            return src
        src = None
    osf = latest.get("osfFileUrl")
    if src is None and isinstance(osf, str) and osf.startswith("https://osf.io/"):
        return osf
    return None


def fetch_preprint(preprint: str, folder: Path, api: str, client: Any = None) -> tuple[Path, str]:
    """Fetch the record of *preprint* (a bare id) and its PDF; the PDF is saved as
    ``input.pdf`` in *folder*. Returns the path and the preprint's title.

    *client* is an ``httpx.Client`` (one is made when it is ``None``). Raises
    :class:`PsyArxivError` when the preprint is unknown or withdrawn, has no PDF, has a PDF
    over 50 MB or that is not a PDF, or when PsyArXiv does not answer.
    """
    import httpx

    api = api.rstrip("/")
    site = _site(api)
    own = client is None
    http = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0)
    try:
        try:
            resp = http.get(f"{api}/preprints/{preprint}", follow_redirects=False)
        except httpx.HTTPError as exc:
            raise PsyArxivError("PsyArXiv did not answer; try again later.") from exc
        if resp.status_code == 404:
            raise PsyArxivError(f"PsyArXiv has no preprint with the id '{preprint}'.")
        if resp.status_code >= 400:
            raise PsyArxivError(
                f"PsyArXiv did not answer (HTTP {resp.status_code}); try again later."
            )
        try:
            record = resp.json()
        except ValueError as exc:
            raise PsyArxivError("PsyArXiv sent an answer that cannot be read.") from exc
        if not isinstance(record, dict):
            raise PsyArxivError("PsyArXiv sent an answer that cannot be read.")
        if record.get("state") == "withdrawn":
            raise PsyArxivError(
                "This preprint was withdrawn from PsyArXiv; it has no file to check."
            )
        versions = record.get("versions")
        latest = versions[0] if isinstance(versions, list) and versions else {}
        kind = str((latest.get("contentType") if isinstance(latest, dict) else None) or "")
        if kind and kind.lower() != "application/pdf":
            raise PsyArxivError(
                f"This preprint's file is a {kind} file; only PDF files are checked here."
            )
        address = _file_address(record, site)
        if address is None:
            raise PsyArxivError("This preprint has no file to check on PsyArXiv.")
        title = str(record.get("title") or "")
        dest = folder / "input.pdf"
        _download(http, address, dest)
        return dest, title
    finally:
        if own:
            http.close()


MAX_REDIRECTS = 5


def _download(http: Any, address: str, dest: Path) -> None:
    """Save *address* as *dest*, at most 50 MB, and only if it is a PDF.

    A redirect is followed (the OSF hands a file on to its storage), but only to https
    addresses of public hosts, as for a repository link (``datapackage._remote``).
    """
    import httpx

    from metacheck.datapackage import PackageError
    from metacheck.datapackage._remote import _check_hop

    url = address
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                _check_hop(url, "psyarxiv")
            except PackageError as exc:
                raise PsyArxivError(
                    "PsyArXiv sent the file from an address that is not allowed."
                ) from exc
            with http.stream("GET", url, follow_redirects=False, timeout=300.0) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise PsyArxivError(
                        f"PsyArXiv did not send the file (HTTP {resp.status_code}); "
                        "try again later."
                    )
                size = 0
                with open(dest, "wb") as out:
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise PsyArxivError("The preprint's file is larger than 50 MB.")
                        out.write(chunk)
            break
        else:
            raise PsyArxivError("PsyArXiv sent the file round in circles.")
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        raise PsyArxivError("PsyArXiv did not send the file; try again later.") from exc
    except PsyArxivError:
        dest.unlink(missing_ok=True)
        raise
    with open(dest, "rb") as fh:
        head = fh.read(4)
    if head != b"%PDF":
        dest.unlink(missing_ok=True)
        raise PsyArxivError("The file PsyArXiv sent is not the PDF it says it is.")
