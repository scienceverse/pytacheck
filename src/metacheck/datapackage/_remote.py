"""A data package named by a link: find the repository, download its archive, open it.

``pytacheck package https://github.com/owner/repo`` and the app's one input box take the link
of a public repository on GitHub, GitLab, OSF or Zenodo (see :func:`parse_link`) and check what
is in it, as for an archive someone uploaded. The link is turned into the address of the
repository's own archive (GitHub's zipball, GitLab's ``archive.zip``, OSF's
storage zip, Zenodo's ``files-archive``) and that one file is downloaded, so what happens next is
what happens to any archive: :func:`~metacheck.datapackage.open_package` unpacks it under its
limits.

Where a link leads is what a person types, which on a shared server is anyone, so the fetch
is careful about it (:func:`fetch_archive`):

* only these hosts are asked for a package, never an address the link names beyond them;
* every request, and every redirect it is sent on, is https on the default port, to a name (not
  an address) that resolves to public addresses only: no address of this machine, of a private
  network (a tailnet included), or of a cloud's metadata service. When an HTTPS proxy is
  configured the proxy decides where connections go, and the addresses are not looked up here;
* a response must be an archive: it is stopped past ``max_bytes`` (counted as it arrives, not
  only as announced), past ``timeout`` seconds, or if it is a web page;
* a token on this computer (``GITHUB_PAT_GITHUB_COM``, ``GITLAB_PAT``, ``OSF_PAT``, or git's
  credentials for GitHub) is sent to the host's own API only and, with ``credentials=False``
  (a shared server must), not at all, so that a link cannot be used to read a private
  repository with the server's token.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urljoin, urlsplit

from metacheck.datapackage._open import (
    ARCHIVE_SUFFIXES,
    OpenedPackage,
    PackageError,
    _size,
    open_package,
)

if TYPE_CHECKING:
    import httpx

__all__ = [
    "MAX_DOWNLOAD",
    "SUPPORTED",
    "RepositoryLink",
    "fetch_archive",
    "is_link",
    "open_link",
    "parse_link",
]

#: The most that is downloaded for one link (the archive, before it is unpacked).
MAX_DOWNLOAD = 5 * 1024**3
#: Seconds one download may take, and redirects it may be sent on.
TIMEOUT = 900.0
MAX_REDIRECTS = 5
#: The statuses that are tried again (with the server's ``Retry-After`` when it gives one), and how often.
RETRY_STATUSES = (429, 500, 502, 503, 504)
TRIES = 3

SUPPORTED = "GitHub, GitLab, OSF or Zenodo"
HELP = (
    "Give the link of a public repository on GitHub, GitLab, OSF or Zenodo, for example "
    "https://github.com/owner/repo or https://zenodo.org/records/1234567."
)

#: host -> repository kind
HOSTS = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
    "osf.io": "osf",
    "www.osf.io": "osf",
    "zenodo.org": "zenodo",
    "www.zenodo.org": "zenodo",
    "doi.org": "doi",
    "dx.doi.org": "doi",
}
NAMES = {"github": "GitHub", "gitlab": "GitLab", "osf": "OSF", "zenodo": "Zenodo"}
#: the host of each kind's API, the only one a token is sent to
API_HOSTS = {
    "github": "api.github.com",
    "gitlab": "gitlab.com",
    "osf": "files.osf.io",
    "zenodo": "zenodo.org",
}

_PART = re.compile(r"[A-Za-z0-9_.-]+")
_OSF_GUID = re.compile(r"[a-z0-9]{5}")
#: first parts of an osf.io path that are not a project
_OSF_PAGES = {
    "preprints",
    "registries",
    "meetings",
    "search",
    "dashboard",
    "myprojects",
    "quickfiles",
    "institutions",
    "user",
    "profile",
    "login",
    "settings",
    "support",
    "my-projects",
}
_ZENODO_DOI = re.compile(r"10\.5281/zenodo\.(\d+)", re.IGNORECASE)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_PAGE = {"text/html", "application/xhtml+xml"}


@dataclass(frozen=True)
class RepositoryLink:
    """A repository found in a link: where its page is and where its archive is downloaded from."""

    kind: str  # "github", "gitlab", "osf" or "zenodo"
    url: str  # the link, as https
    name: str  # a name for the package: the repository's, or the archive file's
    archive_url: str
    #: sent along with the first request when the person has a token for the host
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def host_name(self) -> str:
        return NAMES[self.kind]


# -- reading the link ---------------------------------------------------------------------


def is_link(text: object) -> bool:
    """Whether *text* is a web address (``http://`` or ``https://``), as opposed to a path."""
    return isinstance(text, str) and re.match(r"\s*https?://", text, re.IGNORECASE) is not None


def _normalise(text: str) -> str:
    """The link as typed or pasted, as an address: quotes and angle brackets off, a scheme on."""
    link = text.strip().strip("<>\"'").strip()
    if link.lower().startswith("doi:"):
        link = link[4:].strip()
    if _DOI.match(link):
        return "https://doi.org/" + link
    if link and not _SCHEME.match(link):
        link = "https://" + link
    return link


def parse_link(text: str) -> RepositoryLink:
    """The repository that the link *text* names.

    Accepts a link to a repository (``github.com/owner/repo``, with ``/tree/<branch or tag>``
    for a version other than the default), a GitLab project (``gitlab.com/group/project``,
    subgroups too), an OSF project (``osf.io/abcde``), a Zenodo record (``zenodo.org/records/<id>``,
    or its DOI, ``10.5281/zenodo.<id>``, as a link or on its own) and a direct link to an archive
    (``.zip``, ``.tar.gz`` ...) on one of these hosts. The scheme may be left out. Raises
    :class:`~metacheck.datapackage.PackageError`, with a message for the person, for anything else.
    """
    link = _normalise(text if isinstance(text, str) else "")
    if not link:
        raise PackageError(f"There is no link to check. {HELP}")
    if any(ch.isspace() for ch in link):
        raise PackageError(f"That is not a link: it has spaces in it. {HELP}")
    try:
        parts = urlsplit(link)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise PackageError(f"That is not a link. {HELP}") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise PackageError(f"That is not a web link. {HELP}")
    if parts.username is not None or parts.password is not None or port not in (None, 80, 443):
        raise PackageError(f"A link with a login or a port number is not supported. {HELP}")
    kind = HOSTS.get(host)
    if kind is None:
        raise PackageError(f"{host} is not a repository this can fetch from. {HELP}")
    segments = [seg for seg in parts.path.split("/") if seg]
    page = f"https://{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    if kind == "doi":
        found = _ZENODO_DOI.fullmatch("/".join(segments))
        if found is None:
            raise PackageError(
                "Only a Zenodo DOI (10.5281/zenodo.<number>) can be followed from here. "
                f"For another DOI, give the link of the repository itself. {HELP}"
            )
        return _zenodo(found.group(1))
    builders = {"github": _github, "gitlab": _gitlab, "osf": _osf, "zenodo": _zenodo_link}
    return builders[kind](segments, page)


def _direct(kind: str, segments: list[str], page: str) -> RepositoryLink | None:
    """A link to an archive file on the host (a release asset, ``archive.zip``): the file itself."""
    last = segments[-1].lower() if segments else ""
    suffix = next(
        (s for s in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True) if last.endswith(s)), ""
    )
    if not suffix:
        return None
    deeper = {
        "github": len(segments) >= 4 and segments[2] in ("archive", "releases", "raw"),
        "gitlab": "-" in segments and "archive" in segments,
        "zenodo": "files" in segments or "record" in segments or "records" in segments,
        "osf": False,
    }
    if not deeper[kind]:
        return None
    name = segments[-1][: -len(suffix)] or segments[-1]
    return RepositoryLink(kind, page, _clean_name(name), page)


def _clean_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")[:80] or "package"


def _github(segments: list[str], page: str) -> RepositoryLink:
    direct = _direct("github", segments, page)
    if direct is not None:
        return direct
    if len(segments) < 2:
        raise PackageError(
            "A GitHub link needs the owner and the repository, like https://github.com/owner/repo."
        )
    owner, repo = segments[0], re.sub(r"\.git$", "", segments[1])
    if not (_PART.fullmatch(owner) and _PART.fullmatch(repo)):
        raise PackageError(f"That is not the link of a GitHub repository. {HELP}")
    rest = segments[2:]
    if rest[:1] == ["blob"]:
        raise PackageError(
            "That is the link of one file. Give the repository's own link, "
            f"https://github.com/{owner}/{repo}."
        )
    ref = ""
    if rest[:1] == ["tree"] and len(rest) > 1:
        ref = "/".join(rest[1:])
    elif rest[:2] == ["releases", "tag"] and len(rest) > 2:
        ref = "/".join(rest[2:])
    elif rest[:1] == ["commit"] and len(rest) == 2:
        ref = rest[1]
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball"
    if ref:
        url += "/" + quote(ref, safe="/")
    return RepositoryLink("github", f"https://github.com/{owner}/{repo}", repo, url)


def _gitlab(segments: list[str], page: str) -> RepositoryLink:
    direct = _direct("gitlab", segments, page)
    if direct is not None:
        return direct
    ref = ""
    if "-" in segments:
        at = segments.index("-")
        after = segments[at + 1 :]
        segments = segments[:at]
        if after[:1] == ["tree"] and len(after) > 1:
            ref = "/".join(after[1:])
    path = [re.sub(r"\.git$", "", seg) for seg in segments]
    if len(path) < 2 or not all(_PART.fullmatch(seg) for seg in path):
        raise PackageError(
            "A GitLab link needs the group and the project, like https://gitlab.com/group/project."
        )
    project = "/".join(path)
    url = f"https://gitlab.com/api/v4/projects/{quote(project, safe='')}/repository/archive.zip"
    if ref:
        url += "?sha=" + quote(ref, safe="")
    return RepositoryLink("gitlab", f"https://gitlab.com/{project}", path[-1], url)


def _osf(segments: list[str], page: str) -> RepositoryLink:
    first = segments[0].lower() if segments else ""
    if first == "download" and len(segments) > 1 and _OSF_GUID.fullmatch(segments[1].lower()):
        return RepositoryLink("osf", page, f"osf-{segments[1].lower()}", page)  # a file's own link
    if not _OSF_GUID.fullmatch(first) or first in _OSF_PAGES:
        raise PackageError(
            "An OSF link is the address of a project, like https://osf.io/abcde (five letters or "
            "digits). A preprint, a user or a search page cannot be checked."
        )
    url = f"https://files.osf.io/v1/resources/{first}/providers/osfstorage/?zip="
    return RepositoryLink("osf", f"https://osf.io/{first}", f"osf-{first}", url)


def _zenodo(record: str) -> RepositoryLink:
    url = f"https://zenodo.org/api/records/{record}/files-archive"
    return RepositoryLink("zenodo", f"https://zenodo.org/records/{record}", f"zenodo-{record}", url)


def _zenodo_link(segments: list[str], page: str) -> RepositoryLink:
    direct = _direct("zenodo", segments, page)
    if direct is not None:
        return direct
    if segments[:1] in (["records"], ["record"]) and len(segments) > 1 and segments[1].isdigit():
        return _zenodo(segments[1])
    if segments[:2] == ["api", "records"] and len(segments) > 2 and segments[2].isdigit():
        return _zenodo(segments[2])
    if segments[:1] == ["doi"]:
        found = _ZENODO_DOI.fullmatch("/".join(segments[1:]))
        if found:
            return _zenodo(found.group(1))
    raise PackageError(
        "A Zenodo link is the address of a published record, like https://zenodo.org/records/1234567. "
        "A draft or a community page cannot be checked."
    )


# -- a link is only followed to where a link may lead ----------------------------------------


def _proxied(host: str) -> bool:
    """Whether a proxy sets up the connection to *host* (then the proxy decides where it goes)."""
    return "https" in urllib.request.getproxies() and not urllib.request.proxy_bypass(host)


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _check_hop(url: str, kind: str) -> None:
    """Refuse an address that a download may not be sent to (see the module's description)."""
    where = NAMES.get(kind, "the repository")
    refusal = PackageError(
        f"The link from {where} leads somewhere this is not allowed to fetch from"
    )
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise refusal from None
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise refusal
    if port not in (None, 443):
        raise refusal
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(host)  # an address in place of a name
        raise refusal
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise refusal
    if _proxied(host):
        return
    try:
        found = {str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError:
        return  # the request fails by itself, with its own message
    if not found or not all(_public(address) for address in found):
        raise refusal


# -- the download ---------------------------------------------------------------------------


def _tokens(link: RepositoryLink) -> dict[str, str]:
    """The headers that carry this computer's token for the host, if there is one."""
    try:
        if link.kind == "github":
            from metacheck.archives.github import _github_token

            token = _github_token() or ""
            return {"Authorization": f"token {token}"} if token else {}
        if link.kind == "gitlab":
            from metacheck.archives.gitlab import gitlab_pat

            pat = str(gitlab_pat() or "")
            return {"PRIVATE-TOKEN": pat} if pat else {}
        if link.kind == "osf":
            from metacheck.archives.osf_helpers import osf_pat

            osf = osf_pat()
            return {"Authorization": f"Bearer {osf}"} if osf else {}
    except Exception:
        return {}
    return {}


def _failure(
    link: RepositoryLink, status: int, limit: str | None, credentials: bool
) -> PackageError:
    where = link.host_name
    if status in (404, 410):
        return PackageError(
            f"{where} did not find {link.url}. Check the link, and that the repository is public."
        )
    if status in (401, 403, 429) and (limit == "0" or status == 429):
        tip = (
            ""
            if not credentials
            else f" Setting a token for {where} on this computer lifts the limit."
        )
        return PackageError(f"{where}'s limit on requests is used up. Try again in a while.{tip}")
    if status in (401, 403):
        return PackageError(
            f"{where} did not let this fetch {link.url}: it is private, or needs a login. "
            "Only public repositories can be checked from a link; download it and give the archive."
        )
    return PackageError(f"{where} did not answer well ({status}). Try again in a few minutes.")


def _suffix(head: bytes, ustar: bytes) -> str | None:
    """The archive name ending for a file by what it starts with, or ``None`` if it is no archive."""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return ".zip"
    if head[:2] == b"\x1f\x8b":
        return ".tar.gz"
    if head[:3] == b"BZh":
        return ".tar.bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return ".tar.xz"
    if ustar[:5] == b"ustar":
        return ".tar"
    return None


def fetch_archive(
    link: RepositoryLink,
    folder: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_DOWNLOAD,
    timeout: float = TIMEOUT,
    credentials: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> Path:
    """Download the archive of *link* into *folder*; the path of the file, named for the package.

    Raises :class:`~metacheck.datapackage.PackageError` when the repository is not found or is
    private, the host does not answer, the answer is not an archive, or it is bigger than
    *max_bytes* or takes longer than *timeout* seconds. See the module's description for the
    precautions. ``credentials=False`` sends no token (a shared server's must not be used for
    what a visitor typed).
    """
    import httpx

    from metacheck import http

    tick = progress or (lambda _fraction, _text: None)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / "download.part"
    client = http.client()
    url = link.archive_url
    headers = {"Accept": "*/*"}
    if credentials and urlsplit(url).hostname == API_HOSTS.get(link.kind):
        headers = {**headers, **_tokens(link)}  # to the host's own API, never beyond it
    deadline = time.monotonic() + timeout
    redirects = 0
    attempt = 1
    while True:
        _check_hop(url, link.kind)
        resp = None
        try:
            request = client.build_request("GET", url, headers=headers)
            resp = client.send(request, stream=True, follow_redirects=False)
            status = resp.status_code
            if resp.is_redirect and resp.headers.get("location"):
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise PackageError(f"{link.host_name} sent this fetch round in circles")
                url = urljoin(url, resp.headers["location"])
                headers = {"Accept": "*/*"}  # a token goes to the host's own API only
                continue
            if status in RETRY_STATUSES and attempt < TRIES:
                wait = _retry_after(resp)
                attempt += 1
                http.sleep(wait)
                continue
            if status >= 400:
                raise _failure(link, status, resp.headers.get("x-ratelimit-remaining"), credentials)
            kind = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if kind in _PAGE:
                raise PackageError(f"{link.url} gave a web page, not an archive. {HELP}")
            announced = resp.headers.get("content-length", "")
            if announced.isdigit() and int(announced) > max_bytes:
                raise PackageError(
                    f"The download is {_size(int(announced))}, more than the {_size(max_bytes)} "
                    "this takes from a link. Download it yourself and give the archive."
                )
            _stream(resp, part, max_bytes, deadline, announced, tick)
            break
        except (httpx.TransportError, httpx.TimeoutException):
            if attempt >= TRIES:
                part.unlink(missing_ok=True)
                raise PackageError(
                    f"Could not reach {link.host_name}. Check the internet connection and try again."
                ) from None
            attempt += 1
            http.sleep(2.0 * attempt)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            if resp is not None:
                resp.close()
    with open(part, "rb") as fh:
        head = fh.read(8)
        fh.seek(257)
        ustar = fh.read(8)
    suffix = _suffix(head, ustar)
    if suffix is None:
        part.unlink(missing_ok=True)
        raise PackageError(f"{link.url} did not give an archive (a zip or tar file). {HELP}")
    target = folder / f"{link.name}{suffix}"
    part.replace(target)
    return target


def _retry_after(resp: httpx.Response) -> float:
    """Seconds to wait before another try: the server's ``Retry-After`` (at most 30), else 2."""
    raw = resp.headers.get("retry-after", "")
    return min(float(raw), 30.0) if raw.replace(".", "", 1).isdigit() else 2.0


def _stream(
    resp: httpx.Response,
    part: Path,
    max_bytes: int,
    deadline: float,
    announced: str,
    tick: Callable[[float, str], None],
) -> None:
    """Write the body to *part*, counting what arrives (the announced size may be a lie)."""
    written = 0
    total = int(announced) if announced.isdigit() else 0
    with open(part, "wb") as out:
        for chunk in resp.iter_bytes(1 << 20):
            written += len(chunk)
            if written > max_bytes:
                raise PackageError(
                    f"The download is bigger than the {_size(max_bytes)} this takes from a link. "
                    "Download it yourself and give the archive."
                )
            if time.monotonic() > deadline:
                raise PackageError(
                    "The download took too long, so it was stopped. Try again later."
                )
            out.write(chunk)
            tick(min(0.9, written / total) if total else 0.5, f"Downloading ({_size(written)})")


@contextlib.contextmanager
def open_link(
    link: str | RepositoryLink,
    *,
    max_download: int = MAX_DOWNLOAD,
    timeout: float = TIMEOUT,
    credentials: bool = True,
    progress: Callable[[float, str], None] | None = None,
    **limits: Any,
) -> Iterator[OpenedPackage]:
    """Download the repository at *link* and open it as a data package.

    As :func:`~metacheck.datapackage.open_package`, for the archive that
    :func:`fetch_archive` downloads (at most *max_download* bytes, in *timeout* seconds); the
    downloaded file is removed when the ``with`` block ends, and ``limits`` (``max_bytes``,
    ``max_files``, ``max_ratio``, ``max_depth``) are those of the unpacking.
    The package is named for the repository and its ``source`` is the link.
    """
    found = parse_link(link) if isinstance(link, str) else link
    folder = Path(tempfile.mkdtemp(prefix="metacheck-link-"))
    try:
        archive = fetch_archive(
            found,
            folder,
            max_bytes=max_download,
            timeout=timeout,
            credentials=credentials,
            progress=progress,
        )
        with open_package(archive, **limits) as opened:
            opened.source = found.url
            opened.label = found.name
            opened.remote = True
            yield opened
    finally:
        shutil.rmtree(folder, ignore_errors=True)
