"""DSpace 7+ installations (port of ``R/archive-dspace7.R``).

DSpace 7 replaced the legacy ``/rest/`` API (see
:mod:`pytacheck.archives.psycharchives`) with a HAL-JSON API under
``/server/api/``. Links are recognised against the installations verified
live in :data:`DSPACE7_HOSTS`; an item is resolved by the uuid in its URL
(one request) or by its handle through ``/pid/find`` (two), and only its
``ORIGINAL`` bundle -- the deposited files -- is listed.
"""

from __future__ import annotations

import functools
import warnings
from typing import Any, cast

import pandas as pd

from pytacheck._r import is_na, regextract, sub

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
)

_UUID_RX = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_HANDLE_RX = r"(?<=/handle/)[0-9]{1,5}(?:\.[0-9]+){0,2}/[0-9A-Za-z.]+"


def _dspace7_hosts() -> list[str]:
    """Port of R/archive-dspace7.R::.dspace7_hosts(): the known DSpace 7+ installations."""
    return list(DSPACE7_HOSTS)


@functools.cache
def _dspace7_host_regex() -> str:
    """Port of R/archive-dspace7.R::.dspace7_host_regex(): any known host, as a regex."""
    return "|".join(h.replace(".", r"\.") for h in DSPACE7_HOSTS)


def _dspace7_parse(url: Any) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::.dspace7_parse(): host, uuid and handle of URLs.

    One row per URL (``host``, ``uuid``, ``handle``): the known installation
    named in the URL and the item uuid in it (both lower-cased), and the
    handle after ``/handle/`` (without trailing ``.,;``); ``None`` where
    absent.
    """
    from pytacheck.archives.psycharchives import _chr_list

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
            host[i] = hm.lower()
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


def dspace7_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::dspace7_links(): DSpace 7+ links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table to any host
    in :data:`DSPACE7_HOSTS`, plus bare mentions of such a host with a path
    in the text. Trailing slashes are stripped and duplicate rows dropped.
    """
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links, _url_rows

    host_regex = _dspace7_host_regex()
    found_href = _url_rows(paper, host_regex)
    bare = f"(?:https?://)?(?:www\\.)?(?:{host_regex})/[A-Za-z0-9/._-]+"
    other = _scan_links(paper, bare, [f"{h}/" for h in DSPACE7_HOSTS])
    return _collect_links([found_href, other])


def _dspace7_rest(path: str, host: str) -> Any:
    """Port of R/archive-dspace7.R::.dspace7_rest(): one DSpace 7 REST call.

    GETs ``https://<host>/server/api<path>`` asking for JSON; returns the
    parsed body, or ``None`` on any failure or non-200 status.
    """
    from pytacheck.archives.psycharchives import _rest_json

    return _rest_json(f"https://{host}/server/api{path}")


def _bracket(x: Any, name: str) -> Any:
    """R ``x[[name]]`` on parsed JSON (exact name; ``NULL`` when absent)."""
    if isinstance(x, dict):
        return x.get(name)
    if x is None or isinstance(x, list):
        return None
    raise IndexError("subscript out of bounds")


def _dspace7_info(host: Any, uuid: Any = None, handle: Any = None, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-dspace7.R::.dspace7_info(): one item from one installation.

    Resolves the item by *uuid* when given, else by *handle* (``/pid/find``).
    Returns a one-row table: ``dspace7_host`` and either ``error``
    (``"unfound"``, with a warning) or ``dspace7_uuid``, ``title``,
    ``authors``, ``doi``, ``license``, ``publication_date``,
    ``updated_date`` and ``files`` (``name``, ``size``, ``checksum``,
    ``retrieve``: the item's ``ORIGINAL`` bundle).
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import (
        _as_numeric,
        _cell,
        _dollar,
        _dollars,
        _elements,
        _empty_or,
        _field_cell,
        _paste,
        _url_encode_reserved,
    )
    from pytacheck.archives.psycharchives import _chr1, _obj_cell, _url_piece

    uuid = None if is_na(uuid) else uuid
    handle = None if is_na(handle) else handle
    with _spinner(pb) as bar:
        # the item is named by its uuid, else its handle (metacheck's
        # `uuid %||% handle` never falls back from NA and prints "(NA)": U42)
        ident = _paste(uuid if uuid is not None and str(uuid) != "" else handle)
        _tick(bar, f"* Retrieving info from {_paste(host)} ({ident})...")
        obj: dict[str, pd.Series] = {"dspace7_host": _cell(host)}

        item = None
        if uuid is not None and str(uuid) != "":
            item = _dspace7_rest(f"/core/items/{_paste(uuid)}", host=_paste(host))
        if item is None and handle is not None and str(handle) != "":
            found = _dspace7_rest(
                f"/pid/find?id={_url_encode_reserved(str(handle))}", host=_paste(host)
            )
            found_uuid = _dollar(found, "uuid")
            if found is not None and found_uuid is not None:
                piece = _url_piece(found_uuid)
                if piece is not None:  # R: several URLs, which httr2 refuses (NULL)
                    item = _dspace7_rest(f"/core/items/{piece}", host=_paste(host))
        item_uuid = _dollar(item, "uuid")
        if item is None or item_uuid is None:
            warnings.warn(f"{_paste(host)} ({ident}) could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)
        obj["dspace7_uuid"] = _field_cell(item_uuid)

        md = _dollar(item, "metadata")
        if md is None:
            md = {}

        def md_val(keys: tuple[str, ...]) -> str | None:
            for key in keys:
                entries = _bracket(md, key)
                if entries is not None and not (
                    isinstance(entries, list | dict) and len(entries) == 0
                ):
                    vals = [_chr1(_empty_or(_dollar(m, "value"), None)) for m in _elements(entries)]
                    vals = [v for v in vals if v is not None]
                    if vals:
                        return "; ".join(cast("list[str]", vals))
            return None

        name = _empty_or(_dollar(item, "name"), None)
        obj["title"] = _field_cell(name if name is not None else md_val(("dc.title",)))
        obj["authors"] = _cell(md_val(("dc.contributor.author",)))
        obj["doi"] = _cell(md_val(("dc.identifier.doi",)))
        obj["license"] = _cell(md_val(("dc.rights", "dc.rights.uri", "dc.rights.license")))
        obj["publication_date"] = _cell(md_val(("dc.date.issued", "dc.date.available")))
        obj["updated_date"] = _field_cell(_empty_or(_dollar(item, "lastModified"), None))

        names: list[Any] = []
        sizes: list[float] = []
        checksums: list[Any] = []
        retrieve: list[Any] = []
        piece = _url_piece(item_uuid)
        bundles = (
            None
            if piece is None
            else _dspace7_rest(f"/core/items/{piece}/bundles", host=_paste(host))
        )
        bundle_list = _bracket(_bracket(bundles, "_embedded"), "bundles")
        original = None
        for b in _elements(bundle_list):
            bundle_name = _dollar(b, "name")
            if isinstance(bundle_name, str) and bundle_name == "ORIGINAL":  # R: identical()
                original = b
        if original is not None:
            piece = _url_piece(_dollar(original, "uuid"))
            bs = (
                None
                if piece is None
                else _dspace7_rest(f"/core/bundles/{piece}/bitstreams", host=_paste(host))
            )
            bitstreams = _elements(_bracket(_bracket(bs, "_embedded"), "bitstreams"))
            for b in bitstreams:
                names.append(_chr1(_empty_or(_dollar(b, "name"), None)))
                sizes.append(_as_numeric(_empty_or(_dollar(b, "sizeBytes"), None)))
                checksums.append(_chr1(_empty_or(_dollars(b, "checkSum", "value"), None)))
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
    ``isdir``, ``ext`` and ``type``. ``None`` when the URL names no known
    host, the item cannot be found or it has no files. A sequence of URLs
    gives one table, aligned with the input.
    """
    return _dspace7_file_lists(dspace7_url, pb)[0]


def _dspace7_file_lists(dspace7_url: Any, pb: Any = None) -> tuple[pd.DataFrame | None, list[str]]:
    """:func:`dspace7_file_download` and the URLs it could not list.

    The second value names the URLs whose item was not found (no known DSpace
    7 host, or the host does not know the item), which repo_check() reports
    as inaccessible; an item without files was found.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _paste
    from pytacheck.archives.psycharchives import _add_ext_type, _url_values
    from pytacheck.utils import left_join

    urls = _url_values(dspace7_url)
    with _spinner(pb) as bar:
        if len(urls) > 1:
            unique_urls = [u for u in dict.fromkeys(urls) if u is not None]
            results = [_dspace7_file_lists(u, pb=bar) for u in unique_urls]
            unfound = [u for _, failed in results for u in failed]
            info = bind_rows([df for df, _ in results])
            orig = pd.DataFrame({"dspace7_url": pd.Series(urls, dtype="string")})
            if "dspace7_url" not in info.columns:
                return None, unfound  # no URL listed a file (metacheck's join errors here: U43)
            return left_join(orig, info, by="dspace7_url"), unfound

        url = urls[0] if urls else None
        unlisted = [] if url is None else [url]
        _tick(bar, f"* Listing files from {_paste(url) if urls else ''}...")
        parsed = _dspace7_parse(urls)
        if len(parsed) == 0:
            raise IndexError("subscript out of bounds")
        host = parsed["host"].iloc[0]
        if is_na(host):
            return None, unlisted

        info = _dspace7_info(
            str(host), uuid=parsed["uuid"].iloc[0], handle=parsed["handle"].iloc[0], pb=bar
        )
        if "error" in info.columns:
            return None, unlisted

        file_list = info["files"].iloc[0]
        if file_list is None or len(file_list) == 0:
            _tick(bar, f"- {_paste(url)} contained no files")
            return None, []

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
        return _add_ext_type(df), []
