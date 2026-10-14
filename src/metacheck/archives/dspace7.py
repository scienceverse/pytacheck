"""DSpace 7+ installations (port of ``R/archive-dspace7.R``).

DSpace 7 replaced the legacy ``/rest/`` API (see
:mod:`metacheck.archives.psycharchives`) with a HAL-JSON API under
``/server/api/``. Links are recognised against the installations verified
live in :data:`DSPACE7_HOSTS`; an item is listed, with its bundles and
files embedded, in one request by the uuid in its URL or in two by its handle
(``/pid/find`` first), and only its ``ORIGINAL`` bundle -- the deposited
files -- is listed.
"""

from __future__ import annotations

import functools
import re
import warnings
from typing import Any, cast

import pandas as pd

from metacheck._r import regextract, sub
from metacheck._values import field, is_missing
from metacheck.archives._memo import session_memo

__all__ = ["DSPACE7_HOSTS", "dspace7_file_download", "dspace7_links"]

#: R ``.dspace7_hosts()``: DSpace 7+ installations, in R's order.
DSPACE7_HOSTS: tuple[str, ...] = (
    "archive.uax.com",  # UAX Archive
    "aura.abdn.ac.uk",  # University of Aberdeen (UK)
    "bedl.asabe.org",  # Biosystems Engineering Digital Library
    "comum.rcaap.pt",  # IPS - Instituto Politecnico de Setubal (Portugal)
    "conservancy.umn.edu",  # Data Repository for the University of Minnesota (USA)
    "cris.usm.cl",  # CRIS, Universidad de Santa Maria (Chile)
    "daks.uni-kassel.de",  # DaKS, University of Kassel (Germany)
    "datakatalogi.helsinki.fi",  # University of Helsinki Data Catalogue (Finland)
    "deposita.ibict.br",  # Repositório Comum do Brasil (Deposita)
    "digitalcollection.zhaw.ch",  # ZHAW Digital Collection (Switzerland)
    "diglib.eg.org",  # Eurographics Digital Library
    "dl.gi.de",  # Gesellschaft für Informatik (Germany)
    "dspace.lib.cranfield.ac.uk",  # Cranfield Online Research Data (UK)
    "dspace.ut.ee",  # University of Tartu Library (Estonia)
    "ecosistema.buap.mx",  # EcoBUAP (Mexico)
    "hephaestus.nup.ac.cy",  # HEPHAESTUS, Neapolis University Paphos (Cyprus)
    "irep.mbzuai.ac.ae",  # MBZUAI iRep (UAE)
    "irf.fhnw.ch",  # Institutional Repository FHNW (Switzerland)
    "ktisis.cut.ac.cy",  # KTISIS, Cyprus University of Technology
    "mro.massey.ac.nz",  # Massey Research Online (New Zealand)
    "nalt-dspace.progress.plus",  # NALT
    "opara.zih.tu-dresden.de",  # OPARA (Germany)
    "open.fau.de",  # FAU Erlangen-Nürnberg open access repository (Germany)
    "open.ifz-muenchen.de",  # IfZ-Repositorium (Germany)
    "open.uni-marburg.de",  # open_UMR, University of Marburg (Germany)
    "openresearch.ceu.edu",  # Central European University Open Research Repository
    "openscience.ub.uni-mainz.de",  # Johannes Gutenberg University Mainz (Germany)
    "proforis.phsg.ch",  # Proforis, University of Teacher Education St.Gallen (Switzerland)
    "publish.fid-media.de",  # FID Media Publish (Germany)
    "recil.ensinolusofona.pt",  # Repositório Científico Lusófona (Portugal)
    "repositorio.ipen.br",  # Repositório Digital IPEN (Brazil)
    "repositorio.ipsantarem.pt",  # Instituto Politécnico de Santarém (Portugal)
    "repositorio.tec.mx",  # RITEC, Tecnológico de Monterrey (Mexico)
    "repositorio.uac.pt",  # Universidade dos Açores (Portugal)
    "repositorio.uautonoma.cl",  # Universidad Autónoma de Chile
    "repositorio.ucentral.cl",  # Universidad Central de Chile
    "repositorio.ucn.cl",  # Universidad Católica del Norte (Chile)
    "repositorio.ucsm.edu.pe",  # Universidad Católica de Santa María (Peru)
    "repositorio.umecit.edu.pa",  # UMECIT (Panama)
    "repository.aus.edu",  # American University of Sharjah (UAE)
    "repository.difu.de",  # Difu-Repository (Germany)
    "repository.escholarship.umassmed.edu",  # UMass Chan Medical School (USA)
    "repository.gatech.edu",  # Georgia Tech Digital Repository (USA)
    "repository.gchumanrights.org",  # Global Campus of Human Rights
    "repository.mines.edu",  # Colorado School of Mines (USA)
    "repository.mu.edu.et",  # Mekelle University (Ethiopia)
    "repository.universidadean.edu.co",  # Biblioteca Digital Minerva, Universidad EAN (Colombia)
    "repository.upol.cz",  # Palacký University Open Portal (Czechia)
    "repozitar.techlib.cz",  # National Library of Technology (Czechia)
    "researchrepository.universityofgalway.ie",  # University of Galway (Ireland)
    "ridda2.utp.ac.pa",  # Universidad Tecnológica de Panamá
    "scholarbank.nus.edu.sg",  # National University of Singapore
    "scholarshare.temple.edu",  # TUScholarShare, Temple University (USA)
    "scholarworks.umass.edu",  # University of Massachusetts Amherst (USA)
    "share.swps.edu.pl",  # SWPS University (Poland)
    "tam-datahub.online.uni-marburg.de",  # TAM DataHub, University of Marburg (Germany)
    "toubkal.imist.ma",  # Toubkal (Morocco)
    "tudatalib.ulb.tu-darmstadt.de",  # TUdatalib, TU Darmstadt (Germany)
    "ulir.ul.ie",  # University of Limerick (Ireland)
    "umontreal.scholaris.ca",  # Papyrus, Université de Montréal (Canada)
    "unsworks.unsw.edu.au",  # University of New South Wales (Australia)
    "utoronto.scholaris.ca",  # TSpace, University of Toronto (Canada)
    "uwo.scholaris.ca",  # Western Open Repository (Canada)
    "www.research-collection.ethz.ch",  # ETH Zürich Research Collection (Switzerland)
    # added upstream 2026-10-05 (metacheck #461, #464)
    "ecommons.cornell.edu",  # Cornell eCommons (USA): DSpace 8 (/server/api), not legacy DSpace
    "www.repository.cam.ac.uk",  # Apollo, University of Cambridge (UK): UI only, see _API_HOST_ALIASES
)

#: R ``.dspace7_api_host_aliases()``: a citable UI host whose ``/server/api`` is
#: served by another host. Apollo's www.repository.cam.ac.uk answers 404 there;
#: api.repository.cam.ac.uk names it as its own ``dspaceUI``.
_API_HOST_ALIASES: dict[str, str] = {"www.repository.cam.ac.uk": "api.repository.cam.ac.uk"}

_UUID_RX = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_HANDLE_RX = r"(?<=/handle/)[0-9]{1,5}(?:\.[0-9]+){0,2}/[0-9A-Za-z.]+"


def _dspace7_hosts() -> list[str]:
    """Port of R/archive-dspace7.R::.dspace7_hosts(): the known DSpace 7+ installations."""
    return list(DSPACE7_HOSTS)


def _dspace7_api_host_aliases() -> dict[str, str]:
    """Port of R/archive-dspace7.R::.dspace7_api_host_aliases(): UI host -> API host."""
    return dict(_API_HOST_ALIASES)


@functools.cache
def _dspace7_host_regex() -> str:
    """Port of R/archive-dspace7.R::.dspace7_host_regex(): any known host, as a regex."""
    return "|".join(h.replace(".", r"\.") for h in DSPACE7_HOSTS)


def _dspace7_parse(url: Any) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::.dspace7_parse(): host, uuid and handle of URLs.

    One row per URL (``host``, ``uuid``, ``handle``): the known installation
    named in the URL and the item uuid in it (both lower-cased), and the
    handle after ``/handle/`` (without trailing ``.,;``); ``None`` where
    absent. ``host`` is the host that serves the installation's API, which
    for a UI-only host (:data:`_API_HOST_ALIASES`) is another one.
    """
    from metacheck.archives.psycharchives import _chr_list

    urls = _chr_list(url)
    n = len(urls)
    host: list[str | None] = [None] * n
    uuid: list[str | None] = [None] * n
    handle: list[str | None] = [None] * n
    host_rx = _dspace7_host_regex()
    for i, u in enumerate(urls):
        if u is None or u == "":
            continue
        hm = regextract(host_rx, u, ignore_case=True, perl=True)
        if hm is not None:
            host[i] = _API_HOST_ALIASES.get(hm.lower(), hm.lower())
        um = regextract(_UUID_RX, u, ignore_case=True, perl=True)
        if um is not None:
            uuid[i] = um.lower()
        hd = regextract(_HANDLE_RX, u, perl=True)
        if hd is not None:
            handle[i] = sub("[.,;]+$", "", hd)
    return pd.DataFrame(
        {
            "host": pd.Series(host, dtype="string"),
            "uuid": pd.Series(uuid, dtype="string"),
            "handle": pd.Series(handle, dtype="string"),
        }
    )


#: a DOI anywhere in the text, with an optional ``https://(dx.)doi.org/`` (R:
#: ``doi_bare_regex`` in dspace7_links())
_DOI_BARE = r"(?:https?://)?(?:dx\.)?(?:doi\.org/)?(10\.\d{3,9}/[-._;()/:<>A-Za-z0-9]+[A-Za-z0-9])"


def dspace7_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::dspace7_links(): DSpace 7+ links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table to any host
    in :data:`DSPACE7_HOSTS`, plus bare mentions of such a host with a path
    in the text, plus the DOIs mentioned in the text that are registered to
    such a host (the row's ``href`` is then the registered URL). Trailing
    slashes are stripped and duplicate rows dropped.

    A paper's own DOI is not looked up, nor a DOI under a prefix that another
    detector handles (:func:`_dspace7_skip_doi_prefixes`); every other DOI is
    looked up once per session, in doi.org's handle API
    (:func:`_dspace7_resolve_doi_url`).
    """
    from metacheck._r import regexec
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import _collect_links, _link_matches, _url_rows

    host_regex = _dspace7_host_regex()
    found_href = _url_rows(paper, host_regex)
    bare = f"(?:https?://)?(?:www\\.)?(?:{host_regex})/[A-Za-z0-9/._-]+"
    other = _scan_links(paper, bare, [f"{h}/" for h in DSPACE7_HOSTS])

    # no DOI-prefix registry exists for DSpace 7, so a paper citing only a DOI
    # (the host never named) is found by looking up the DOIs it mentions
    mentions = _link_matches(paper, _DOI_BARE, r"10\.\d{3,9}/")
    if len(mentions) > 0:
        dois: list[str | None] = []
        for href in mentions["href"].tolist():
            groups = regexec(_DOI_BARE, href, perl=True, ignore_case=True)
            dois.append(str(groups[1]) if len(groups) >= 2 else None)
        # the paper's own DOI is matched per paper, so that a paper citing
        # another paper of the same list is still looked up
        if "paper_id" in mentions.columns:
            own = _own_dois(paper)
            pids = mentions["paper_id"].tolist()
            dois = [
                None if d is None or (pid, _dspace7_doi_key(d)) in own else d
                for d, pid in zip(dois, pids, strict=True)
            ]
        skip = set(_dspace7_skip_doi_prefixes())
        dois = [None if d is None or d.split("/", 1)[0].lower() in skip else d for d in dois]
        resolved = {d: _dspace7_resolve_doi_url(d) for d in dict.fromkeys(dois) if d is not None}
        urls = [None if d is None else resolved[d] for d in dois]
        keep = [u is not None for u in urls]
        mentions = mentions.loc[keep].copy()
        mentions["href"] = pd.Series(
            [u for u in urls if u is not None], index=mentions.index, dtype="string"
        )
    return _collect_links([found_href, other, mentions])


def _own_dois(paper: Any) -> set[tuple[Any, str]]:
    """``(paper_id, DOI key)`` of every paper's own DOI (R: ``paper_table(paper, "info", "doi")``)."""
    from metacheck.papers.tables import paper_table

    try:
        info = paper_table(paper, "info", ["doi"])
    except Exception:  # R: tryCatch(..., error = \(e) NULL)
        return set()
    if "doi" not in info.columns or "paper_id" not in info.columns:
        return set()
    return {
        (pid, _dspace7_doi_key(doi))
        for pid, doi in zip(info["paper_id"].tolist(), info["doi"].tolist(), strict=True)
        if not is_missing(doi)
    }


def _dspace7_doi_key(doi: str) -> str:
    """Port of R/archive-dspace7.R::.dspace7_doi_key(): a DOI in a comparable form.

    Lower case, without a ``doi.org``/``dx.doi.org`` address or ``doi:`` label.
    """
    return _DOI_KEY_RX.sub("", str(doi).strip().lower(), count=1)


_DOI_KEY_RX = re.compile(r"^(?:https?://)?(?:dx\.)?(?:doi\.org/)?(?:doi:\s*)?")


def _dspace7_skip_doi_prefixes() -> list[str]:
    """Port of R/archive-dspace7.R::.dspace7_skip_doi_prefixes(): DOI prefixes not looked up.

    Each belongs to a host that another detector already handles (OSF,
    Zenodo, Dryad, Figshare and its institutional instances,
    4TU.ResearchData, Mendeley Data, DataONE, UK Data Service ReShare,
    PsychArchives, Dataverse), so a DOI under it cannot lead to a DSpace 7
    item. Read from those detectors' own tables, so a prefix added there is
    skipped here too. 10.23673 is listed for datadoi.ee (the University of
    Tartu's Dataverse), but the same university registers DOIs under it for
    dspace.ut.ee, so it is still looked up (metacheck checked every prefix
    against DataCite, 2026-10-13).
    """
    from metacheck.archives.dataone import DATAONE_HOSTS
    from metacheck.archives.dataverse import DATAVERSE_DOI_PREFIX_HOSTS
    from metacheck.archives.dryad import DRYAD_DOI_PREFIXES
    from metacheck.archives.figshare import FIGSHARE_DOI_PREFIX_HOSTS

    skip = [
        "10.17605",  # OSF
        "10.5281",  # Zenodo
        *DRYAD_DOI_PREFIXES,
        "10.6084",  # Figshare
        *FIGSHARE_DOI_PREFIX_HOSTS,
        "10.4121",  # 4TU.ResearchData
        "10.17632",  # Mendeley Data
        *(str(h["doi_prefix"]) for h in DATAONE_HOSTS if h["doi_prefix"]),
        "10.5255",  # UK Data Service ReShare
        "10.23668",  # PsychArchives
        "10.7910",  # Harvard Dataverse
        *(p for prefixes in DATAVERSE_DOI_PREFIX_HOSTS.values() for p in prefixes),
    ]
    return [p for p in dict.fromkeys(p.lower() for p in skip) if p != "10.23673"]


#: doi.org's and hdl.handle.net's Handle System JSON APIs
DOI_HANDLE_API = "https://doi.org/api/handles/"
HDL_HANDLE_API = "https://hdl.handle.net/api/handles/"


def _handle_registered_url(handle: str, api: str = DOI_HANDLE_API) -> tuple[str | None, bool]:
    """Port of R/archive-dspace7.R::.handle_registered_url(): the URL a handle is registered to.

    A DOI is a handle too. Reads ``<api><handle>?type=URL`` without visiting
    the registered URL. Returns ``(url, ok)``: *ok* is ``True`` when the
    proxy gave a definite answer (a URL, "handle not found" -- responseCode
    100 -- or "no URL value" -- responseCode 200; the URL is then ``None``)
    and ``False`` when the request itself failed.
    """
    from metacheck import http
    from metacheck.archives.dataverse import _url_encode_reserved

    url = f"{api}{_url_encode_reserved(handle)}?type=URL"
    try:
        resp = http.request("GET", url, max_tries=1, timeout=30)
    except Exception:
        resp = None
    if resp is None:
        return None, False
    try:
        body = http.resp_json(resp, check_type=False)
    except Exception:
        body = None
    code = field(body, "responseCode")
    if code == 1 and not isinstance(code, bool):
        values = field(body, "values")
        for v in values if isinstance(values, list) else []:
            if field(v, "type") == "URL":
                value = field(v, "data", "value")
                return (value if isinstance(value, str) and value != "" else None), True
        return None, True
    return None, code in (100, 200) and not isinstance(code, bool)


#: R ``.dspace7_doi_cache``: DOI key -> DSpace 7 URL, or ``None`` when the DOI
#: leads elsewhere. Only definite answers are kept.
_DOI_CACHE = session_memo()


def _on_known_host(url: str | None) -> bool:
    """Whether *url*'s host is in :data:`DSPACE7_HOSTS` (``www.`` aside) or a subdomain of one."""
    from metacheck.archives.dataverse import _on_host, _url_host

    url_host = None if url is None else _url_host(url)
    return url_host is not None and any(_on_host(url_host, h) for h in DSPACE7_HOSTS)


_HDL_URL_RX = re.compile(r"^https?://(?:hdl\.)?handle\.net/([^?#]+)", re.IGNORECASE)


def _dspace7_resolve_doi_url(doi: str) -> str | None:
    """Port of R/archive-dspace7.R::.dspace7_resolve_doi_url().

    The URL *doi* is registered to, when it is on a host in
    :data:`DSPACE7_HOSTS`; else (or when the DOI is not registered) ``None``.
    The URL is read from doi.org's handle API instead of following the DOI's
    redirects to the landing page (0.35 s instead of 0.4 to 6.5 s per DOI,
    measured by metacheck). Two kinds of registered URL take one more
    lookup, at hdl.handle.net: an ``hdl.handle.net`` address, as many DSpace
    installations register, and a DSpace ``/handle/<handle>`` page on a host
    that is not listed -- the installation's former address, kept in the
    DOI record after the repository moved (smartech.gatech.edu, now
    repository.gatech.edu), while the handle record follows the move.

    Answers are remembered for the session; a lookup that failed (network
    error, server error) is tried again next time. R accepts a URL that
    names a host anywhere (``grepl(host, url, fixed = TRUE)``); here the
    URL's host name must be the listed host (``www.`` aside) or a subdomain
    of it (U211).
    """
    from urllib.parse import unquote

    key = _dspace7_doi_key(doi)
    if key == "":
        return None
    if key in _DOI_CACHE:
        return cast("str | None", _DOI_CACHE[key])

    url, ok = _handle_registered_url(doi)
    hdl = None if url is None else _HDL_URL_RX.match(url)
    if ok and hdl is not None:
        url, ok = _handle_registered_url(unquote(hdl.group(1)), api=HDL_HANDLE_API)
    if ok and url is not None and not _on_known_host(url):
        handle = regextract(_HANDLE_RX, url, perl=True)
        if handle is not None:
            moved, moved_ok = _handle_registered_url(sub("[.,;]+$", "", handle), api=HDL_HANDLE_API)
            ok = moved_ok
            if _on_known_host(moved):
                url = moved

    resolved = url if ok and _on_known_host(url) else None
    if ok:
        _DOI_CACHE[key] = resolved
    return resolved


def _dspace7_rest(path: str, host: str | None = None, url: str | None = None) -> Any:
    """Port of R/archive-dspace7.R::.dspace7_rest(): one DSpace 7 REST call.

    GETs ``https://<host>/server/api<path>`` (or the full address *url*, for
    a ``_links.next`` page link) asking for JSON; returns the parsed body,
    or ``None`` on any failure or non-200 status.
    """
    from metacheck.archives.psycharchives import _rest_json

    return _rest_json(url if url is not None else f"https://{host}/server/api{path}")


def _dspace7_find_uuid(host: str, handle: str) -> str | None:
    """Port of R/archive-dspace7.R::.dspace7_find_uuid(): the uuid of the item a handle names.

    ``/pid/find`` answers 302 with the item's own address
    (``.../server/api/core/items/<uuid>``); the uuid is read from that
    ``Location`` header, without following it (which downloaded the whole
    item, only for it to be requested again with ``embed``). A 200 answer
    (an installation that does not redirect) gives the uuid in its body.
    """
    from metacheck import http
    from metacheck.archives.dataverse import _url_encode_reserved
    from metacheck.archives.psycharchives import _resp_json

    url = f"https://{host}/server/api/pid/find?id={_url_encode_reserved(handle)}"
    try:
        resp = http.request(
            "GET",
            url,
            headers={"Accept": "application/json"},
            follow_redirects=False,
            max_tries=1,
        )
    except Exception:
        resp = None
    if resp is None:
        return None
    if 300 <= resp.status_code < 400:
        found = regextract(_UUID_RX, resp.headers.get("location", ""), ignore_case=True)
        return None if found is None else found.lower()
    if resp.status_code == 200:
        uuid = field(_resp_json(resp), "uuid")
        return uuid if isinstance(uuid, str) and uuid != "" else None
    return None


def _dspace7_item(host: str, uuid: str) -> Any:
    """Port of R/archive-dspace7.R::.dspace7_item(): an item with its bundles and bitstreams.

    One request: ``embed=bundles/bitstreams`` returns the item, its bundles
    and each bundle's bitstreams together (three requests before), and
    ``embed.size=bundles/bitstreams=200`` raises the embedded bitstream
    page from DSpace's default 20 to 200.
    """
    return _dspace7_rest(
        f"/core/items/{uuid}?embed=bundles/bitstreams&embed.size=bundles/bitstreams=200",
        host=host,
    )


def _dspace7_bitstreams(bundle: Any, host: str) -> list[Any]:
    """Port of R/archive-dspace7.R::.dspace7_bitstreams(): every bitstream of one bundle.

    A bitstream listing is a page (20 by default, 200 when embedded by
    :func:`_dspace7_item`); further pages are followed through
    ``_links.next``. When the installation did not embed them, they are
    requested directly. Warns when fewer bitstreams arrive than the listing
    reports.
    """
    from metacheck.archives.dataverse import _elements
    from metacheck.archives.psycharchives import _url_piece

    page = _bracket(_bracket(bundle, "_embedded"), "bitstreams")
    if page is None:
        piece = _url_piece(field(bundle, "uuid"))
        if piece is not None:
            page = _dspace7_rest(f"/core/bundles/{piece}/bitstreams?size=200", host=host)
    out = list(_elements(_bracket(_bracket(page, "_embedded"), "bitstreams")))
    total = field(page, "page", "totalElements")
    next_href = field(page, "_links", "next", "href")
    seen: set[str] = set()
    while isinstance(next_href, str) and next_href not in seen:
        seen.add(next_href)
        nxt = _dspace7_rest("", url=next_href)
        if nxt is None:
            break
        out.extend(_elements(_bracket(_bracket(nxt, "_embedded"), "bitstreams")))
        next_href = field(nxt, "_links", "next", "href")
    if isinstance(total, int | float) and not isinstance(total, bool) and len(out) < total:
        bundle_uuid = field(bundle, "uuid")
        warnings.warn(
            f"{host} listed only {len(out)} of the {int(total)} files of bundle "
            f"{bundle_uuid if isinstance(bundle_uuid, str) else ''}. Files not listed are "
            "not retrieved, so this listing is incomplete. Run again later.",
            stacklevel=3,
        )
    return out


def _bracket(x: Any, name: str) -> Any:
    """R ``x[[name]]`` on parsed JSON (exact name; ``NULL`` when absent)."""
    if isinstance(x, dict):
        return x.get(name)
    if x is None or isinstance(x, list):
        return None
    raise IndexError("subscript out of bounds")


def _dspace7_info(host: Any, uuid: Any = None, handle: Any = None, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::.dspace7_info(): one item from one installation.

    Resolves the item by *uuid* when given, else by *handle* (``/pid/find``),
    in one request (two by handle) with its bundles and bitstreams embedded
    (:func:`_dspace7_item`); an installation that ignores ``embed`` is asked
    for them separately. Every page of the ``ORIGINAL`` bundle's bitstreams
    is read (:func:`_dspace7_bitstreams`). Returns a one-row table: ``dspace7_host`` and either ``error``
    (``"unfound"``, with a warning) or ``dspace7_uuid``, ``title``,
    ``authors``, ``doi``, ``license``, ``publication_date``,
    ``updated_date`` and ``files`` (``name``, ``size``, ``checksum``,
    ``retrieve``: the item's ``ORIGINAL`` bundle).
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import (
        _as_numeric,
        _cell,
        _elements,
        _empty_or,
        _field_cell,
        _paste,
    )
    from metacheck.archives.psycharchives import _chr1, _obj_cell, _url_piece

    uuid = None if is_missing(uuid) else uuid
    handle = None if is_missing(handle) else handle
    with _spinner(pb) as bar:
        # the item is named by its uuid, else its handle (metacheck's
        # `uuid %||% handle` never falls back from NA and prints "(NA)": U42)
        ident = _paste(uuid if uuid is not None and str(uuid) != "" else handle)
        _tick(bar, f"* Retrieving info from {_paste(host)} ({ident})...")
        obj: dict[str, pd.Series] = {"dspace7_host": _cell(host)}

        item = None
        if uuid is not None and str(uuid) != "":
            item = _dspace7_item(_paste(host), _paste(uuid))
        if item is None and handle is not None and str(handle) != "":
            found_uuid = _dspace7_find_uuid(_paste(host), str(handle))
            if found_uuid is not None:
                item = _dspace7_item(_paste(host), found_uuid)
        item_uuid = field(item, "uuid")
        if item is None or item_uuid is None:
            warnings.warn(f"{_paste(host)} ({ident}) could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)
        obj["dspace7_uuid"] = _field_cell(item_uuid)

        md = field(item, "metadata")
        if md is None:
            md = {}

        def md_val(keys: tuple[str, ...]) -> str | None:
            for key in keys:
                entries = _bracket(md, key)
                if entries is not None and not (
                    isinstance(entries, list | dict) and len(entries) == 0
                ):
                    vals = [_chr1(_empty_or(field(m, "value"), None)) for m in _elements(entries)]
                    vals = [v for v in vals if v is not None]
                    if vals:
                        return "; ".join(cast("list[str]", vals))
            return None

        name = _empty_or(field(item, "name"), None)
        obj["title"] = _field_cell(name if name is not None else md_val(("dc.title",)))
        obj["authors"] = _cell(md_val(("dc.contributor.author",)))
        obj["doi"] = _cell(md_val(("dc.identifier.doi",)))
        obj["license"] = _cell(md_val(("dc.rights", "dc.rights.uri", "dc.rights.license")))
        obj["publication_date"] = _cell(md_val(("dc.date.issued", "dc.date.available")))
        obj["updated_date"] = _field_cell(_empty_or(field(item, "lastModified"), None))

        names: list[Any] = []
        sizes: list[float] = []
        checksums: list[Any] = []
        retrieve: list[Any] = []
        # the bundles normally arrive embedded in the item (_dspace7_item())
        bundles = _bracket(_bracket(item, "_embedded"), "bundles")
        if bundles is None:
            piece = _url_piece(item_uuid)
            if piece is not None:  # R: several URLs, which httr2 refuses (NULL)
                bundles = _dspace7_rest(f"/core/items/{piece}/bundles", host=_paste(host))
        bundle_list = _bracket(_bracket(bundles, "_embedded"), "bundles")
        original = None
        for b in _elements(bundle_list):
            bundle_name = field(b, "name")
            if isinstance(bundle_name, str) and bundle_name == "ORIGINAL":  # R: identical()
                original = b
        if original is not None:
            for b in _dspace7_bitstreams(original, host=_paste(host)):
                names.append(_chr1(_empty_or(field(b, "name"), None)))
                sizes.append(_as_numeric(_empty_or(field(b, "sizeBytes"), None)))
                checksums.append(_chr1(_empty_or(field(b, "checkSum", "value"), None)))
                retrieve.append(
                    _chr1(
                        _empty_or(
                            _bracket(_bracket(_bracket(b, "_links"), "content"), "href"), None
                        )
                    )
                )
        obj["files"] = _obj_cell(
            pd.DataFrame(
                {
                    "name": pd.Series(names, dtype="string"),
                    "size": pd.Series(sizes, dtype="float64"),
                    "checksum": pd.Series(checksums, dtype="string"),
                    "retrieve": pd.Series(retrieve, dtype="string"),
                }
            )
        )
        return pd.DataFrame(obj)


def dspace7_file_download(dspace7_url: Any, pb: Any = None) -> pd.DataFrame | None:
    """Port of R/archive-dspace7.R::dspace7_file_download(): list an item's public files.

    Lists the files of one or more DSpace 7 items (the ``ORIGINAL`` bundle)
    without downloading them: one row per file with ``dspace7_url``,
    ``name``, ``file_url`` (the bitstream's content URL, fetched later by
    ``download_repo_files()``), ``file_location`` (``NA``), ``size``,
    ``isdir``, ``ext`` and ``type``. Each item's licence and DOI are carried
    in the table's ``attrs["license"]`` / ``attrs["doi"]`` (``{url: value}``;
    R: named vectors in attributes), also for an item without files (a
    zero-row table). ``None`` when the URL names no known host or the item
    cannot be found. A sequence of URLs gives one table, aligned with the
    input.
    """
    return _dspace7_file_lists(dspace7_url, pb)[0]


_EMPTY_FILES = {
    "dspace7_url": "string",
    "name": "string",
    "file_url": "string",
    "file_location": "string",
    "size": "float64",
    "isdir": "boolean",
    "ext": "string",
    "type": "string",
}


def _dspace7_file_lists(dspace7_url: Any, pb: Any = None) -> tuple[pd.DataFrame | None, list[str]]:
    """:func:`dspace7_file_download` and the URLs it could not list.

    The second value names the URLs whose item was not found (no known DSpace
    7 host, or the host does not know the item), which repo_check() reports
    as inaccessible; an item without files was found.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _paste
    from metacheck.archives.psycharchives import _add_ext_type, _url_values
    from metacheck.utils import left_join

    urls = _url_values(dspace7_url)
    with _spinner(pb) as bar:
        if len(urls) > 1:
            unique_urls = [u for u in dict.fromkeys(urls) if u is not None]
            results = [_dspace7_file_lists(u, pb=bar) for u in unique_urls]
            file_lists = [df for df, _ in results]
            unfound = [u for _, failed in results for u in failed]
            info = bind_rows(file_lists)
            orig = pd.DataFrame({"dspace7_url": pd.Series(urls, dtype="string")})
            if "dspace7_url" not in info.columns:
                return None, unfound  # no URL found an item (metacheck's join errors here: U43)
            df = left_join(orig, info, by="dspace7_url")
            # R: unlist(lapply(file_lists, attr, "license")) -- NULL (no attribute) if none
            for name in ("license", "doi"):
                merged: dict[str, Any] = {}
                for fl in file_lists:
                    if fl is not None:
                        merged.update(fl.attrs.get(name) or {})
                if merged:
                    df.attrs[name] = merged
            return df, unfound

        url = urls[0] if urls else None
        unlisted = [] if url is None else [url]
        _tick(bar, f"* Listing files from {_paste(url) if urls else ''}...")
        parsed = _dspace7_parse(urls)
        if len(parsed) == 0:
            raise IndexError("subscript out of bounds")
        host = parsed["host"].iloc[0]
        if is_missing(host):
            return None, unlisted

        info = _dspace7_info(
            str(host), uuid=parsed["uuid"].iloc[0], handle=parsed["handle"].iloc[0], pb=bar
        )
        if "error" in info.columns:
            return None, unlisted

        def attr(col: str) -> dict[str, Any]:
            v = info[col].iloc[0] if col in info.columns and len(info) else None
            return {_paste(url): None if is_missing(v) else v}

        license_, doi = attr("license"), attr("doi")
        file_list = info["files"].iloc[0]
        if file_list is None or len(file_list) == 0:
            _tick(bar, f"- {_paste(url)} contained no files")
            empty = pd.DataFrame({k: pd.Series([], dtype=t) for k, t in _EMPTY_FILES.items()})
            empty.attrs["license"] = license_
            empty.attrs["doi"] = doi
            return empty, []

        n = len(file_list)
        df = pd.DataFrame(
            {
                "dspace7_url": pd.Series([url] * n, dtype="string"),
                "name": file_list["name"].astype("string").reset_index(drop=True),
                "file_url": file_list["retrieve"].astype("string").reset_index(drop=True),
                "file_location": pd.Series([None] * n, dtype="string"),
                "size": file_list["size"].astype("float64").reset_index(drop=True),
                "isdir": pd.Series([False] * n, dtype="boolean"),
            }
        )
        df = _add_ext_type(df)
        df.attrs["license"] = license_
        df.attrs["doi"] = doi
        return df, []
