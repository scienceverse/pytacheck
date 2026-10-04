"""Dataverse installations (port of ``R/archive-dataverse.R``).

Dataverse is open-source repository software run by many independent
installations, so links are recognised against an allowlist of known hosts
(:data:`DATAVERSE_HOSTS`) and, for bare DOIs, of DOI prefixes verified to
belong to one of those hosts (:data:`DATAVERSE_DOI_PREFIX_HOSTS`). Every
installation exposes the same REST API, so :func:`dataverse_info` and
:func:`dataverse_file_download` work the same whatever the host.

This module also holds the helpers the Dryad, Figshare and Dataverse ports
share (R's list idioms on parsed JSON, ``utils::URLdecode()``, and the
file-by-file download and verification engine that the three R files each
carry a copy of).
"""

from __future__ import annotations

import functools
import hashlib
import math
import numbers
import os
import shutil
import tempfile
import warnings
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from metacheck._r import as_character, compile_r, grepl, gsub, is_na, plural, r_round, sub
from metacheck.archives._atomic import atomic_write

if TYPE_CHECKING:
    import httpx

__all__ = [
    "DATAVERSE_DOI_PREFIX_HOSTS",
    "DATAVERSE_HOSTS",
    "dataverse_file_download",
    "dataverse_info",
    "dataverse_links",
    "dataverse_pat",
]

# ---------------------------------------------------------------------------
# allowlists (data, copied verbatim from R/archive-dataverse.R)
# ---------------------------------------------------------------------------

#: R ``.dataverse_hosts()``: known Dataverse installations, in R's order (the
#: order matters: the host regex is an alternation tried left to right).
DATAVERSE_HOSTS: tuple[str, ...] = (
    "dataverse.harvard.edu",  # Harvard Dataverse, the flagship instance
    "demo.dataverse.org",  # Dataverse demo/test instance
    "dataverse.nl",  # DataverseNL
    "dataverse.unc.edu",  # UNC Dataverse
    "abacus.library.ubc.ca",  # Abacus (UBC)
    "borealisdata.ca",  # Borealis (Canadian national repository)
    "dataverse.icrisat.org",
    "data.aussda.at",  # AUSSDA (Austria)
    "darus.uni-stuttgart.de",  # DaRUS (U. Stuttgart)
    "dataverse.lib.virginia.edu",
    # -- Europe, verified live 2026-08-16 (see note above) --
    "dataverse.adp.fdv.uni-lj.si",  # ADP, Slovenian Social Science Data Archives
    "bonndata.uni-bonn.de",  # bonndata (U. Bonn, Germany)
    "dataverse.bsc.es",  # BSC Dataverse (Spain)
    "dataverse.cirad.fr",  # CIRAD Dataverse (France)
    "data.crossda.hr",  # CROSSDA (Croatia)
    "archivdv.soc.cas.cz",  # CSDA Dataverse (Czechia)
    "dare.uol.de",  # dare (Germany)
    "dataverse.ird.fr",  # Data Suds (France)
    "data.sciencespo.fr",  # data.sciencespo (France)
    "datadoi.ee",  # DATADOI (Estonia)
    "edatos.consorciomadrono.es",  # Dataverse e-cienciaDatos (Spain)
    "dv.dataverse.lv",  # DataverseLV (Latvia)
    "dataverse.no",  # DataverseNO (Norway)
    "dataverse.rhi.hi.is",  # DATICE (Iceland)
    "dataverse.deic.dk",  # DeiC Dataverse (Denmark)
    "edmond.mpdl.mpg.de",  # Edmond, Max Planck Digital Library (Germany)
    "health-study-hub.de",  # Health Study Hub (Germany)
    "heidata.uni-heidelberg.de",  # HeiDATA (U. Heidelberg, Germany)
    "dataverse.iza.org",  # IDSC Dataverse, IZA (Germany)
    "datasets.iisg.amsterdam",  # IISH Dataverse (Netherlands)
    "dataverse.pushdom.ru",  # Institute of Russian Literature Dataverse (Russia)
    "data.fdz.ioer.de",  # ioerDATA (Germany)
    "dataverse.ipgp.fr",  # IPGP Research Collection (France)
    "issda.ucd.ie",  # ISSDA Dataverse (Ireland)
    "dataverse.iit.it",  # Italian Institute of Technology (Italy)
    "data.fz-juelich.de",  # Jülich DATA (Germany)
    "rdr.kuleuven.be",  # KU Leuven RDR (Belgium)
    "lida.dataverse.lt",  # LiDA, Lithuanian Data Archive
    "lore.list.lu",  # LORE, LIST Open Repository (Luxembourg)
    "portal.odissei.nl",  # ODISSEI Portal (Netherlands)
    "dataverse.uclouvain.be",  # Open Data @ UCLouvain (Belgium)
    "dataverse.openforestdata.pl",  # Open Forest Data (Poland)
    "osnadata.ub.uni-osnabrueck.de",  # osnaData (Germany)
    "entrepot.recherche.data.gouv.fr",  # Recherche Data Gouv (France, national repository)
    "repod.icm.edu.pl",  # RepOD (Poland)
    "dataverse.rsu.lv",  # RSU Dataverse (Latvia)
    "archaeology.datastations.nl",  # DANS Data Station Archaeology (Netherlands)
    "lifesciences.datastations.nl",  # DANS Data Station Life Sciences (Netherlands)
    "phys-techsciences.datastations.nl",  # DANS Data Station Physical/Technical Sciences (Netherlands)
    "ssh.datastations.nl",  # DANS Data Station Social Sciences and Humanities (Netherlands)
    # -- Africa, Asia, North America, South America, verified live
    # 2026-09-11 (see note above) --
    # -- Africa --
    "dataverse.bhp.org.bw",  # Botswana Harvard Data (Botswana)
    "data.worldagroforestry.org",  # World Agroforestry - Research Data Repository (Kenya)
    # -- Asia --
    "researchdata.cuhk.edu.hk",  # CUHK Research Data Repository (Hong Kong)
    "researchdata.lib.polyu.edu.hk",  # PolyU Research Data Repository (Hong Kong)
    "data.brin.go.id",  # BRIN Dataverse (Indonesia)
    "data.cifor.org",  # CIFOR (Indonesia)
    "dataverse.theacss.org",  # ACSS Dataverse (Lebanon)
    "data.mel.cgiar.org",  # MELDATA (Lebanon)
    "researchdata.nie.edu.sg",  # NIE Data Repository (Singapore)
    "dataverse.lib.nycu.edu.tw",  # NYCU Dataverse (Taiwan)
    # -- Europe (additional, outside the 2026-08-16 pass above) --
    "www.sodha.be",  # SODHA (Belgium)
    "dataverse.uliege.be",  # ULiège Open Data Repository (Belgium)
    "data.goettingen-research-online.de",  # Göttingen Research Online (Germany)
    "infraverse.zih.tu-dresden.de",  # KEEN Data Management Platform (Germany)
    "planetary-data-portal.org",  # TRR170-DB (Germany)
    "data.tu-dortmund.de",  # TUDOdata (Germany)
    "datarepository.unive.it",  # Università Ca' Foscari Venezia Datarepository (Italy)
    "dataverse.unimi.it",  # Università degli Studi di Milano (Italy)
    "danebadawcze.uw.edu.pl",  # Dane Badawcze UW (Poland)
    "uken.rodbuk.pl",  # RODBUK UKEN (Poland)
    "rodbuk.pl",  # RODBUK (Poland)
    "agh.rodbuk.pl",  # RODBUK AGH (Poland)
    "pk.rodbuk.pl",  # RODBUK PK (Poland)
    "uek.rodbuk.pl",  # RODBUK UEK (Poland)
    "uj.rodbuk.pl",  # RODBUK UJ (Poland)
    "sano.rodbuk.pl",  # Sano (Poland)
    "ifj.rodbuk.pl",  # Henryk Niewodniczański Institute of Nuclear Physics PAS (Poland)
    "akf.rodbuk.pl",  # University of Physical Culture in Krakow (Poland)
    "uwr.rodbuk.pl",  # University of Wroclaw (Poland)
    "dataportal.ing.pan.pl",  # Institute of Geophysics, Polish Academy of Sciences (Poland)
    "dataverse.csuc.cat",  # CORA. Research Data Repository (RDR) (Spain)
    "opendata.nas.gov.ua",  # DataverseUA (Ukraine)
    # -- North America --
    "dataverse.lib.unb.ca",  # UNB Libraries Dataverse (Canada)
    "dataverse.tec.ac.cr",  # Repositorio TECdatos (Costa Rica)
    "data.cimmyt.org",  # CIMMYT Research Data (Mexico)
    "datahub.tec.mx",  # Tecnológico de Monterrey Data Hub (Mexico)
    "dataverse.asu.edu",  # ASU Library Research Data Repository (USA)
    "dataverse.dartmouth.edu",  # Dartmouth Dataverse (USA)
    "dataverse.fiu.edu",  # Florida International University Research Data Portal (USA)
    "dataverse.orc.gmu.edu",  # George Mason University Dataverse (USA)
    "dataverse.jpl.nasa.gov",  # JPL Open Repository (USA)
    "dataverse.whoi.edu",  # MBLWHOI Library Dataverse (USA)
    "data.qdr.syr.edu",  # QDR Main Collection (USA)
    "datasets.lib.berkeley.edu",  # UC Berkeley Library Dataverse (USA)
    "dataverse.ucla.edu",  # UCLA Dataverse (USA)
    "dataverse.udel.edu",  # UD Dataverse (USA)
    "dataverse.carc.usc.edu",  # USC Dataverse (USA)
    "dataverse.vtti.vt.edu",  # VTTI (USA)
    "dataverse.arcc.uwyo.edu",  # Wyoming Data Repository (USA)
    "dataverse.yale.edu",  # Yale Dataverse (USA)
    # -- South America --
    "datos.unlp.edu.ar",  # U. Nacional de La Plata data repository (Argentina)
    "arcadados.fiocruz.br",  # Arca Dados (Brazil)
    "dataverse.cidacs.org",  # CIDACS (Brazil)
    "dataverse.cbpf.br",  # Centro Brasileiro de Pesquisas Físicas (Brazil)
    "dataverse.ideal.ufpb.br",  # DataPB (Brazil)
    "domusdados.unifesp.br",  # Domus Dados (Brazil)
    "dataverse.fgv.br",  # FGV Dataverse (Brazil)
    "repositoriopesquisas.ibict.br",  # IBICT (Brazil)
    "repositorio.soildata.mapbiomas.org",  # Repositório SoilData (Brazil)
    "datospararesiliencia.cl",  # Datos para Resiliencia (Chile)
    "datos.usach.cl",  # Repositorio de Datos de Investigación USACH (Chile)
    "datos.uchile.cl",  # U. de Chile research data repository (Chile)
    "datav.udec.cl",  # U. de Concepción research data repository (Chile)
    "opendata.cesa.edu.co",  # CESA data repository (Colombia)
    "investigacionartes.mincultura.gov.co",  # Ministerio de las Culturas, las Artes y los Saberes (Colombia)
    "papyrus-datos.co",  # PAPYRUS (Colombia)
    "datosinvestigacion.udistrital.edu.co",  # U. Distrital Francisco José de Caldas (Colombia)
    "indata.cedia.edu.ec",  # Indata (Ecuador)
    "data.cipotato.org",  # International Potato Center (Peru)
    "datos.pucp.edu.pe",  # Pontificia U. Católica del Perú (Peru)
    "redata.anii.org.uy",  # Redata (Uruguay)
)

#: R ``.dataverse_doi_prefix_hosts()``: host -> DOI prefixes individually
#: verified to belong to that installation, in R's order. A DOI under a prefix
#: shared by several hosts is resolved through doi.org (_dataverse_host_from_doi()).
DATAVERSE_DOI_PREFIX_HOSTS: dict[str, tuple[str, ...]] = {
    "agh.rodbuk.pl": ("10.58032",),
    "akf.rodbuk.pl": ("10.58145",),
    "arcadados.fiocruz.br": ("10.35078",),
    "archaeology.datastations.nl": ("10.17026",),
    # shared with the three other DANS Data Stations (see _dataverse_host_from_doi())
    "phys-techsciences.datastations.nl": ("10.17026",),
    "archivdv.soc.cas.cz": ("10.14473",),
    "borealisdata.ca": ("10.14285", "10.23685", "10.34990", "10.5203", "10.5683", "10.7939"),
    "danebadawcze.uw.edu.pl": ("10.58132",),
    "dare.uol.de": ("10.57782",),
    "data.aussda.at": ("10.11587",),
    "data.cimmyt.org": ("10.71682",),
    "data.cipotato.org": ("10.21223",),
    "data.crossda.hr": ("10.23669",),
    "data.fdz.ioer.de": ("10.71830",),
    "data.qdr.syr.edu": ("10.5064",),
    "data.sciencespo.fr": ("10.21410",),
    "data.worldagroforestry.org": ("10.34725",),
    "datadoi.ee": ("10.15155", "10.23659", "10.23673"),
    "datahub.tec.mx": ("10.57687",),
    "dataportal.ing.pan.pl": ("10.60871",),
    "datarepository.unive.it": ("10.71731",),
    "datasets.lib.berkeley.edu": ("10.60503",),
    "datav.udec.cl": ("10.48665",),
    "dataverse.arcc.uwyo.edu": ("10.15786",),
    "dataverse.asu.edu": ("10.48349",),
    "dataverse.bsc.es": ("10.82201",),
    "dataverse.carc.usc.edu": ("10.34728",),
    "dataverse.cidacs.org": ("10.57833",),
    "dataverse.cirad.fr": ("10.18167",),
    "dataverse.csuc.cat": ("10.34810",),
    "dataverse.dartmouth.edu": ("10.21989",),
    "dataverse.deic.dk": ("10.60612",),
    "dataverse.fiu.edu": ("10.34703",),
    "dataverse.harvard.edu": ("10.26300", "10.7910"),
    "dataverse.icrisat.org": ("10.21421",),
    "dataverse.ideal.ufpb.br": ("10.48472", "10.71650"),
    "dataverse.iit.it": ("10.48557",),
    "dataverse.ipgp.fr": ("10.18715",),
    "dataverse.ird.fr": ("10.23708",),
    "dataverse.jpl.nasa.gov": ("10.48577", "10.48588"),
    "dataverse.lib.nycu.edu.tw": ("10.57770",),
    "dataverse.lib.unb.ca": ("10.25545",),
    "dataverse.lib.virginia.edu": ("10.18130",),
    "dataverse.nl": ("10.34894",),
    "dataverse.no": ("10.18710", "10.23642"),
    "dataverse.openforestdata.pl": ("10.48370",),
    "dataverse.orc.gmu.edu": ("10.13021",),
    "dataverse.rhi.hi.is": ("10.34881",),
    "dataverse.rsu.lv": ("10.48510",),
    "dataverse.theacss.org": ("10.25825",),
    "dataverse.ucla.edu": ("10.25346",),
    "dataverse.udel.edu": ("10.82252",),
    "dataverse.uliege.be": ("10.58119",),
    "dataverse.unc.edu": ("10.15139",),
    "dataverse.unimi.it": ("10.13130",),
    "dataverse.vtti.vt.edu": ("10.15787",),
    "dataverse.whoi.edu": ("10.26027",),
    "dataverse.yale.edu": ("10.60600",),
    "datos.uchile.cl": ("10.34691",),
    "datos.usach.cl": ("10.60547",),
    "datospararesiliencia.cl": ("10.71578",),
    "dv.dataverse.lv": ("10.71782",),
    "edatos.consorciomadrono.es": ("10.21950",),
    "edmond.mpdl.mpg.de": ("10.17617",),
    "entrepot.recherche.data.gouv.fr": ("10.12763", "10.15454", "10.17180", "10.57745"),
    "ifj.rodbuk.pl": ("10.48733",),
    "indata.cedia.edu.ec": ("10.48661",),
    "investigacionartes.mincultura.gov.co": ("10.82294",),
    "issda.ucd.ie": ("10.7929",),
    "lifesciences.datastations.nl": ("10.17026",),
    "lore.list.lu": ("10.57828",),
    "opendata.nas.gov.ua": ("10.48788",),
    "pk.rodbuk.pl": ("10.58099",),
    "portal.odissei.nl": ("10.57934",),
    "rdr.kuleuven.be": ("10.48804",),
    "redata.anii.org.uy": ("10.60895",),
    "repod.icm.edu.pl": ("10.18150",),
    "repositorio.soildata.mapbiomas.org": ("10.60502",),
    "researchdata.cuhk.edu.hk": ("10.48668",),
    "researchdata.lib.polyu.edu.hk": ("10.60933",),
    "researchdata.nie.edu.sg": ("10.25340",),
    "rodbuk.pl": (
        "10.26106",
        "10.34616",
        "10.48733",
        "10.57903",
        "10.58032",
        "10.58099",
        "10.58116",
        "10.58145",
        "10.71580",
    ),
    "sano.rodbuk.pl": ("10.71580",),
    "ssh.datastations.nl": ("10.17026",),
    "uek.rodbuk.pl": ("10.58116",),
    "uj.rodbuk.pl": ("10.26106", "10.57903"),
    "uwr.rodbuk.pl": ("10.34616",),
    "www.sodha.be": ("10.34934",),
}


# ---------------------------------------------------------------------------
# helpers shared by the Dryad / Figshare / Dataverse ports
# ---------------------------------------------------------------------------

_MB = 1024 * 1024
_INT32_MAX = 2**31 - 1


def _dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on JSON parsed by ``httr2::resp_body_json()`` (no simplification).

    An object is a named list: an exact name, else a unique prefix (R's partial
    matching); an array is an unnamed list (``NULL``); ``NULL`` gives ``NULL``.
    A scalar has no fields either (``None``): R raises "$ operator is invalid
    for atomic vectors" there, so one API record with a plain string where an
    object is expected aborted the whole call (U34).
    """
    if isinstance(x, dict):
        if name in x:
            return x[name]
        hits = [k for k in x if isinstance(k, str) and k.startswith(name)]
        return x[hits[0]] if len(hits) == 1 else None
    return None


def _dollars(x: Any, *names: str) -> Any:
    """``x$a$b$c``."""
    for name in names:
        x = _dollar(x, name)
    return x


def _is_empty(x: Any) -> bool:
    """R ``length(x) == 0`` for a parsed JSON value (a string always has length 1)."""
    return x is None or (isinstance(x, list | tuple | dict) and len(x) == 0)


def _empty_or(x: Any, y: Any) -> Any:
    """metacheck's ``x %empty_or% y``."""
    return y if _is_empty(x) else x


def _elements(x: Any) -> list[Any]:
    """The elements R's ``lapply()``/``vapply()``/``for`` visit in a JSON value."""
    if x is None:
        return []
    if isinstance(x, dict):
        return list(x.values())
    if isinstance(x, list | tuple):
        return list(x)
    return [x]


def _json_chr(x: Any) -> str | None:
    """R ``as.character()`` of a JSON scalar as ``jsonlite`` parses it.

    Whole numbers within the 32-bit range are R integers; larger ones are
    doubles (so ``3000000000`` becomes ``"3e+09"``, as in R).
    """
    if x is None:
        return None
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return str(x) if -_INT32_MAX <= x <= _INT32_MAX else as_character(float(x))
    if isinstance(x, list | tuple) and len(x) == 1:
        return _json_chr(x[0])
    return as_character(x)


def _chr_elt(x: Any) -> str | None:
    """A ``vapply(..., character(1))`` result: a string or ``NA``, else R's error."""
    if x is None or isinstance(x, str):
        return x
    if isinstance(x, list | tuple) and len(x) == 1 and isinstance(x[0], str):
        return x[0]
    kind = "logical" if isinstance(x, bool) else ("double" if isinstance(x, float) else "list")
    if isinstance(x, int) and not isinstance(x, bool):
        kind = "integer"
    raise TypeError(f"values must be type 'character',\n but FUN(X[[1]]) result is type '{kind}'")


def _as_numeric(x: Any) -> float:
    """R ``as.numeric()`` of a JSON scalar (``NaN`` for ``NA``)."""
    if x is None:
        return math.nan
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, int | float):
        return float(x)
    if isinstance(x, list | tuple) and len(x) == 1:
        return _as_numeric(x[0])
    if isinstance(x, str):
        try:
            from metacheck.datacheck.files import _r_as_numeric

            v = _r_as_numeric(x)
        except ImportError:  # pragma: no cover - datacheck is part of the package
            try:
                v = float(x.strip())
            except ValueError:
                v = None
        if v is None:
            warnings.warn("NAs introduced by coercion", stacklevel=3)
            return math.nan
        return float(v)
    return math.nan


def _is_true(x: Any) -> bool:
    """R ``x %in% TRUE`` for one element of an atomic vector.

    ``match()`` coerces ``TRUE`` to the vector's type: a logical ``TRUE``, a
    number equal to 1 and the string ``"TRUE"`` match; ``NA``, ``"T"``,
    ``"true"``, 2 and anything else do not.
    """
    import numbers

    import numpy as np

    if isinstance(x, list | tuple | dict) or is_na(x):
        return False
    if isinstance(x, bool | np.bool_):
        return bool(x)
    if isinstance(x, str):
        return x == "TRUE"
    if isinstance(x, numbers.Number):
        return bool(cast("Any", x) == 1)
    return False


def _has_values(x: Any) -> bool:
    """``!is.null(x) && length(x) > 0``."""
    if x is None:
        return False
    if isinstance(x, str):
        return True
    try:
        return len(x) > 0
    except TypeError:
        return True


def _paste(x: Any) -> str:
    """One ``paste0()`` piece: ``NA`` is ``"NA"``, doubles use 15 significant digits."""
    s = as_character(x)
    return "NA" if s is None else s


def _url_decode(url: str) -> str:
    """R ``utils::URLdecode()`` for one string.

    Every ``%`` consumes the next two bytes, decoded with R's arithmetic (so a
    non-hex pair can still give a byte); an out-of-range value becomes a NUL
    byte with R's warning. Trailing NULs are dropped by ``rawToChar()``, an
    embedded one is R's error.
    """
    raw = url.encode("utf-8", errors="surrogateescape")
    out = bytearray()
    i = 0
    warned = False
    while i < len(raw):
        if raw[i] != 0x25:
            out.append(raw[i])
            i += 1
            continue
        pair = [raw[i + k] if i + k < len(raw) else 0 for k in (1, 2)]
        pair = [c - 32 if c > 96 else c for c in pair]
        pair = [c - 7 if c > 57 else c for c in pair]
        val = (pair[0] - 48) * 16 + (pair[1] - 48)
        if not 0 <= val <= 255:
            val = 0
            warned = True
        out.append(val)
        i += 3
    if warned:
        warnings.warn("out-of-range values treated as 0 in coercion to raw", stacklevel=3)
    data = bytes(out).rstrip(b"\x00")
    if b"\x00" in data:
        shown = data.decode("utf-8", errors="replace").replace("\x00", "\\0")
        raise ValueError(f"embedded nul in string: '{shown}'")
    return data.decode("utf-8", errors="surrogateescape")


def _invalid_utf8(s: str | None) -> bool:
    """Is *s* (a :func:`_url_decode` result) invalid UTF-8 in R?

    ``rawToChar()`` keeps whatever bytes the percent escapes gave; those that
    are not valid UTF-8 come back from :func:`_url_decode` as lone surrogates
    (``surrogateescape``). R's regex functions then refuse the string.
    """
    return s is not None and any("\udc80" <= ch <= "\udcff" for ch in s)


_URL_RESERVED_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")


def _url_encode_reserved(url: str) -> str:
    """R ``utils::URLencode(url, reserved = TRUE)`` for one string."""
    if grepl("%[[:xdigit:]]{2}", url):
        return url
    return "".join(
        ch if ch in _URL_RESERVED_OK else "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
        for ch in url
    )


def _as_values(x: Any) -> tuple[list[Any], bool]:
    """An R vector argument as a list, and whether it was a scalar."""
    if x is None:
        return [], False
    if isinstance(x, str) or not isinstance(x, Sequence | pd.Series | pd.Index):
        return [x], True
    return list(x), False


def _chr_values(x: Any) -> tuple[list[str | None], bool]:
    """``as.character(x)`` as a list, and whether *x* was a scalar."""
    vals, scalar = _as_values(x)
    return [None if is_na(v) else as_character(v) for v in vals], scalar


def _string_series(values: Sequence[Any]) -> pd.Series:
    """A character column (``string`` dtype) from strings / ``None``."""
    return pd.Series(list(values), dtype="string")


def _cell(value: Any) -> pd.Series:
    """A one-row column holding a JSON scalar (character when a string).

    Other archive modules import this; an array or object is kept whole in an
    object column. The Dryad/Figshare/Dataverse metadata fields use
    :func:`_field_cell`, which follows R's replacement rules instead.
    """
    if value is None or isinstance(value, str):
        return pd.Series([value], dtype="string")
    if isinstance(value, list | dict):
        return pd.Series([value], dtype=object)
    return pd.Series([value])


def _field_cell(value: Any) -> pd.Series:
    """R's ``obj$x <- value`` on a one-row table, for a parsed JSON value.

    A scalar gives an atomic column (character when a string). An array or
    object is an R list: one element makes a list column holding that element
    (an object's names are dropped from the cell), any other length is R's
    ``replacement has <n> rows, data has 1`` error.
    """
    if isinstance(value, list | tuple | dict):
        items = list(value.values()) if isinstance(value, dict) else list(value)
        if len(items) != 1:
            raise ValueError(f"replacement has {len(items)} rows, data has 1")
        element = items[0]
        # a list-column cell: a scalar element is a length-1 vector in R
        cell = element if isinstance(element, list | tuple | dict) else [element]
        return pd.Series([cell], dtype=object)
    return _cell(value)


def _list_cell(value: Any) -> pd.Series:
    """A one-row list column (R ``obj$x <- list(value)``)."""
    # built from a list (not set with .iloc, which would turn a dict into a Series)
    return pd.Series([value], dtype=object)


class RequestAbort(Exception):
    """An error while preparing a request that aborts the caller in R.

    ``req_perform_sequential(on_error = "continue")`` (inside
    ``.batch_query()``) only turns ``httr2_error`` conditions (a failed
    connection, an HTTP error) into a ``NULL`` response; any other error
    raised while a request is performed -- e.g. httr2's ``httr2_oauth``
    errors when a token endpoint rejects the client -- propagates to the
    caller. :func:`_query` re-raises this class instead of returning ``None``.
    """


def _query(url: str, req_func: Callable[[dict[str, Any]], dict[str, Any]]) -> httpx.Response | None:
    """``.batch_query(url, msg = NULL, req_func = req_func)[[1]]``.

    A request that cannot be sent gives ``None`` (R: a ``NULL`` response), as
    ``req_perform_sequential(on_error = "continue")`` does -- including a
    failure inside *req_func*, which in R only surfaces when the request is
    performed (e.g. a token endpoint that cannot be reached). A
    :class:`RequestAbort` (an error R does not catch there) propagates.
    """
    from metacheck import http

    try:
        return http.batch_query([url], msg=None, req_func=req_func)[0]
    except RequestAbort:
        raise
    except Exception:
        return None


def _resp_json(resp: httpx.Response) -> Any:
    """``tryCatch(httr2::resp_body_json(resp), error = \\(e) NULL)`` (``None`` on failure)."""
    from metacheck.archives.osf_helpers import _resp_body_json

    try:
        return _resp_body_json(resp)
    except Exception:
        return None


def _info_table(x: Any, id_col: int | str, url_col: str, drop: Sequence[str]) -> pd.DataFrame:
    """The input table of an ``*_info()`` function.

    A data frame gets ``url_col`` from its *id_col* column (1-based position or
    name) and loses the id columns the function recomputes (*drop*); anything
    else becomes ``data.frame(<url_col> = na.omit(unique(x)))``.
    """
    if isinstance(x, pd.DataFrame):
        table = x.copy()
        if isinstance(id_col, numbers.Real) and not isinstance(id_col, bool):
            if not 1 <= int(id_col) <= table.shape[1]:
                raise IndexError("subscript out of bounds")
            source = table.iloc[:, int(id_col) - 1]
        elif id_col in table.columns:
            source = table[id_col]
        else:
            raise KeyError(f"Can't extract columns that don't exist: {id_col}")
        table[url_col] = source.to_numpy()
        return table.drop(columns=[c for c in drop if c in table.columns])
    if x is None:
        raise ValueError(
            f"Join columns in `x` must be present in the data.\n✖ Problem with `{url_col}`."
        )
    vals, _ = _as_values(x)
    uniq: list[Any] = []
    seen: set[Any] = set()
    for v in vals:
        if is_na(v) or v in seen:
            continue
        seen.add(v)
        uniq.append(v)
    if all(isinstance(v, str) for v in uniq):
        return pd.DataFrame({url_col: _string_series(uniq)})
    return pd.DataFrame({url_col: pd.Series(uniq)})


def _search_frame(paper: Any) -> pd.DataFrame | None:
    """The sentence table ``text_search()`` builds for a paper or paper list.

    ``None`` for anything else (or a table without text), which
    :func:`_link_matches` then hands to ``text_search()`` unchanged. Built once
    per ``*_links()`` call and shared by its searches.
    """
    if isinstance(paper, str | pd.DataFrame):
        return None
    try:
        from metacheck.text.search import _text_frame
    except ImportError:  # pragma: no cover - search the whole paper instead
        return None
    frame, is_vector = _text_frame(paper)
    if is_vector or "text" not in frame.columns:
        return None
    return frame


def _link_matches(
    paper: Any,
    pattern: str,
    prefilter: str | None = None,
    frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """``text_search(paper, pattern, return = "match", perl = TRUE) |>
    dplyr::select(href = text, dplyr::any_of(c("text_id", "paper_id")))``.

    *prefilter*, when given, must be a pattern that every match of *pattern*
    contains a match of (under the same flags: caseless, PCRE). Sentences
    without one cannot match, so only the others are searched: the same
    result, without running an expensive pattern over every sentence.
    *frame* is *paper*'s :func:`_search_frame`, when already built.
    """
    from metacheck.text.search import text_search

    target = paper
    if prefilter is not None:
        if frame is None:
            frame = _search_frame(paper)
        if frame is not None:
            keep = grepl(prefilter, frame["text"].tolist(), ignore_case=True, perl=True)
            target = frame.loc[[bool(k) for k in keep]]
    found: pd.DataFrame = text_search(target, pattern, return_="match", perl=True)
    ids = [c for c in ("text_id", "paper_id") if c in found.columns]
    if "text" not in found.columns:
        # an empty paper list: R's `href = text` then names the (NULL) local
        # variable `text`, which selects nothing
        return found.loc[:, ids]
    return found.loc[:, ["text", *ids]].rename(columns={"text": "href"})


def _collect_links(parts: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """``bind_rows(...) |> mutate(href = sub("/+$", "", href)) |> unique()``."""
    from metacheck._r import bind_rows

    links = bind_rows(parts)
    if "href" not in links.columns:
        # R: `href` is then the (NULL) local variable, so mutate() adds a
        # character(0) column -- which only fits a table without rows
        if len(links) > 0:
            raise ValueError(f"`href` must be size {len(links)} or 1, not 0.")
        links["href"] = pd.Series([], dtype="string", index=links.index)
        return links.reset_index(drop=True)
    links["href"] = pd.Series(sub("/+$", "", links["href"]), index=links.index, dtype="string")
    return links.drop_duplicates().reset_index(drop=True)


def _url_rows(paper: Any, pattern: str, ignore_case: bool = True) -> pd.DataFrame:
    """``paper_table(paper, "url") |> dplyr::filter(grepl(pattern, href, ignore.case))``."""
    from metacheck.papers.tables import paper_table

    urls = paper_table(paper, "url")
    if "href" not in urls.columns:
        # an empty paper list gives a table without columns: R's `href` is then
        # the (NULL) local variable and grepl() a logical(0) filter
        if len(urls) > 0:
            raise ValueError(f"`..1` must be of size {len(urls)} or 1, not size 0.")
        return urls
    keep = [bool(v) for v in grepl(pattern, urls["href"], ignore_case=ignore_case)]
    return urls[pd.Series(keep, index=urls.index, dtype=bool)]


def _cap_on(cap: float | None) -> bool:
    """``!is.null(cap) && is.finite(cap) && cap > 0`` (``is.finite()`` of a string is FALSE)."""
    if not isinstance(cap, numbers.Real) or is_na(cap):
        return False
    return math.isfinite(cap) and cap > 0


def _is_zip(name: Sequence[str | None]) -> list[bool]:
    """``.is_zip()`` (R/zip-peek.R): does each name end in ``.zip``?"""
    try:
        from metacheck.archives.zip_peek import _is_zip as is_zip
    except ImportError:  # the zip-peek port may not be available yet
        return [bool(v) for v in grepl("[.]zip$", list(name), ignore_case=True)]
    return [bool(v) for v in is_zip(list(name))]


def _zip_members(
    url: str, dest: str, keep_types: Any, max_file_size: float | None
) -> pd.DataFrame | None:
    """The body shared by ``.dryad_zip_members()``, ``.figshare_zip_members()`` and
    ``.dataverse_zip_members()``: extract the wanted members of a remote zip."""
    from metacheck.datacheck.files import data_classify_files

    try:
        from metacheck.archives.zip_peek import zip_peek

        listing = zip_peek(url)
    except Exception:  # R: tryCatch(zip_peek(url), error = \(e) NULL)
        listing = None
    if listing is None or len(listing) == 0:
        return None
    names = [None if is_na(v) else str(v) for v in listing["name"].tolist()]
    types = data_classify_files(
        [None if v is None else v.rstrip("/").rpartition("/")[2] for v in names]
    )
    wanted = {keep_types} if isinstance(keep_types, str) else set(keep_types or [])
    keep = [t in wanted for t in types]
    if _cap_on(max_file_size):
        assert max_file_size is not None
        cap = max_file_size * _MB
        keep = [
            k and not is_na(s) and s <= cap
            for k, s in zip(keep, listing["size"].tolist(), strict=True)
        ]
    if not any(keep):
        return listing.iloc[0:0].loc[:, ["name", "size"]]
    from metacheck.archives.zip_peek import _zip_fetch_members

    return _zip_fetch_members(
        url, names=[n for n, k in zip(names, keep, strict=True) if k], dest=dest
    )


def _md5sum(path: str) -> str | None:
    """``tools::md5sum()`` of one file (``None`` when it cannot be read)."""
    try:
        h = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _verify_file_table(files: pd.DataFrame | None, download_to: str, typed: bool) -> Any:
    """The body of the ``.<archive>_verify_downloads()`` functions.

    *typed*: only rows whose ``checksum_type`` is ``"md5"`` are hashed (Dryad,
    Dataverse); otherwise every 32-hex-digit ``checksum`` is (Figshare).
    """
    if files is None or len(files) == 0:
        return files
    files = files.copy()
    n = len(files)
    unzipped = (
        [not is_na(v) for v in files["extracted"].tolist()] if "extracted" in files else [False] * n
    )
    files["size_on_disk"] = pd.Series([math.nan] * n, dtype="float64", index=files.index)
    files["checksum_ok"] = pd.Series([None] * n, dtype="boolean", index=files.index)
    if "path" not in files:
        files["downloaded"] = pd.Series([False] * n, dtype="boolean", index=files.index)
        return files

    paths = [None if is_na(p) else str(p) for p in files["path"].tolist()]
    full = [None if p is None else f"{download_to}/{p}" for p in paths]
    on_disk = [f is not None and os.path.exists(f) and not os.path.isdir(f) for f in full]
    size_on_disk = [
        float(os.path.getsize(f)) if d and f is not None else math.nan
        for f, d in zip(full, on_disk, strict=True)
    ]
    ok = [d and not math.isnan(s) for d, s in zip(on_disk, size_on_disk, strict=True)]
    with warnings.catch_warnings():  # R: suppressWarnings(as.numeric(files$size))
        warnings.simplefilter("ignore")
        expected = (
            [_as_numeric(None if is_na(v) else v) for v in files["size"].tolist()]
            if "size" in files
            else [math.nan] * n
        )
    ok = [
        o and (math.isnan(e) or (not math.isnan(s) and s == e))
        for o, e, s in zip(ok, expected, size_on_disk, strict=True)
    ]

    checksums = (
        [None if is_na(v) else str(v) for v in files["checksum"].tolist()]
        if "checksum" in files
        else [None] * n
    )
    if typed:
        types = (
            [None if is_na(v) else str(v) for v in files["checksum_type"].tolist()]
            if "checksum_type" in files
            else None
        )
        is_md5 = [t == "md5" for t in types] if types is not None else [False] * n
    else:
        is_md5 = [True] * n
    hexlike = grepl("^[0-9a-f]{32}$", checksums, ignore_case=True)
    checksum_ok: list[bool | None] = [None] * n
    for i in range(n):
        if ok[i] and is_md5[i] and checksums[i] is not None and hexlike[i]:
            got = _md5sum(full[i])  # type: ignore[arg-type]
            checksum_ok[i] = got is not None and got.lower() == checksums[i].lower()  # type: ignore[union-attr]
    ok = [o and c is not False for o, c in zip(ok, checksum_ok, strict=True)]
    if "downloaded" not in files:
        # R: `files$downloaded <- ok & files$downloaded %in% TRUE` is a length-0 value
        raise ValueError(f"replacement has 0 rows, data has {n}")
    prev = files["downloaded"].tolist()
    downloaded = [o and _is_true(p) for o, p in zip(ok, prev, strict=True)]
    downloaded = [True if u else d for d, u in zip(downloaded, unzipped, strict=True)]
    files["size_on_disk"] = pd.Series(size_on_disk, dtype="float64", index=files.index)
    files["checksum_ok"] = pd.Series(checksum_ok, dtype="boolean", index=files.index)
    files["downloaded"] = pd.Series(downloaded, dtype="boolean", index=files.index)
    return files


def _omit_msg(key: Any, size: float) -> str:
    """``paste0("- omitting ", key, " (", round(size / 1024 / 1024, 1), "MB)")``."""
    return f"- omitting {_paste(key)} ({_paste(r_round(size / 1024 / 1024, 1))}MB)"


def _fetch_file(
    url: str,
    headers: dict[str, str],
    target: str,
    reauth: Callable[[httpx.Response], dict[str, str] | None] | None = None,
) -> bool:
    """``httr2::request(url) |> <headers> |> req_timeout(600) |>
    req_error(is_error = \\(resp) FALSE) |> req_perform()``, body written to *target*.

    True when the server answered 200 (the body is streamed to disk rather
    than held in memory); request failures give False, as R's ``tryCatch``.
    *reauth*, given a non-200 response, may return fresh headers to send the
    request once more with (httr2 re-authenticates once after an OAuth
    ``invalid_token`` answer). A 200 answer without a body is an empty file
    (verification then compares its size with the listed one); in metacheck
    ``resp_body_raw()`` refuses it outside the ``tryCatch()``, so one
    zero-byte file aborted the whole download (U36).
    """
    import httpx

    from metacheck import http

    for attempt in range(2):
        try:
            with http.client().stream("GET", url, headers=headers, timeout=600) as resp:
                if resp.status_code != 200:
                    try:
                        fresh = reauth(resp) if reauth is not None and attempt == 0 else None
                    except Exception:  # R: tryCatch(req_perform(...), error = \(e) NULL)
                        return False
                    if fresh is None:
                        return False
                    headers = fresh
                    continue
                with atomic_write(target) as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
                return True
        except (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL):
            return False
    return False  # pragma: no cover - the loop always returns


def _inside(root: str, rel: str) -> bool:
    """Does ``file.path(root, rel)`` stay inside *root*? (guards hostile file names)."""
    base = os.path.normpath(root)
    target = os.path.normpath(f"{root}/{rel}")
    return target == base or target.startswith(base.rstrip(os.sep) + os.sep)


def _download_file_table(
    files: pd.DataFrame,
    *,
    ident: str,
    folder_name: str,
    download_to: str,
    max_file_size: float | None,
    max_download_size: float | None,
    unzip_types: Any,
    pb: Any,
    headers: Callable[[dict[str, Any]], dict[str, Any]],
    zip_members: Callable[..., pd.DataFrame | None],
    verify: Callable[[pd.DataFrame, str], pd.DataFrame],
    what: str,
    reauth: Callable[[httpx.Response], dict[str, str] | None] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Everything the three ``*_file_download()`` functions do after listing files.

    *files* has ``id``, ``key``, ``size``, ``checksum``, (``checksum_type``,)
    ``self``. Applies the size caps (a zip named in *unzip_types* is exempt),
    creates the target folder, extracts wanted zip members or downloads each
    file, copies files under their names, verifies them and warns about any
    that did not arrive. Returns the verified table and the folder; the table's
    ``attrs["failed"]`` lists the zip members that could not be extracted
    (``key``, ``member``, ``error``; metacheck #429's ``attr(, "failed")``).

    Files omitted by the size caps stay in the table with ``downloaded =
    False``, as metacheck documents (its code drops them: U36); when every
    file is omitted no folder is created and the folder is ``None``. The
    folder for an existing ``<name>`` is ``<name>_1``, ``<name>_2``...
    (metacheck strips a trailing ``_<digits>`` from the name itself, so an
    existing ``figshare_123`` gives ``figshare_1``: U37).
    """
    from metacheck.archives import _tick
    from metacheck.archives.osf import _normalize

    files = files.reset_index(drop=True)
    n = len(files)
    unzippable = [False] * n
    if _has_values(unzip_types):
        selfs = [None if is_na(v) else str(v) for v in files["self"].tolist()]
        unzippable = [
            z and s is not None and s != ""
            for z, s in zip(_is_zip(files["key"].tolist()), selfs, strict=True)
        ]

    sizes = [math.nan if is_na(v) else float(v) for v in files["size"].tolist()]
    omitted = [False] * n
    if _cap_on(max_file_size):
        assert max_file_size is not None
        cap = max_file_size * _MB
        for i in range(n):
            if not math.isnan(sizes[i]) and sizes[i] > cap and not unzippable[i]:
                _tick(pb, _omit_msg(files["key"].iloc[i], sizes[i]))
                omitted[i] = True

    if _cap_on(max_download_size):
        assert max_download_size is not None
        cap = max_download_size * _MB
        while True:
            capped = [i for i in range(n) if not unzippable[i] and not omitted[i]]
            if not capped or sum(sizes[i] for i in capped if not math.isnan(sizes[i])) <= cap:
                break
            max_file = max(
                (i for i in capped if not math.isnan(sizes[i])), key=lambda i: (sizes[i], -i)
            )
            _tick(pb, _omit_msg(files["key"].iloc[max_file], sizes[max_file]))
            omitted[max_file] = True

    if all(omitted):
        _tick(pb, "- All files omitted due to size constraints")
        files = files.copy()
        files["downloaded"] = pd.Series([False] * n, dtype="boolean")
        files["extracted"] = pd.Series([None] * n, dtype="Int64")
        files["path"] = _string_series([None] * n)
        files["size_on_disk"] = pd.Series([math.nan] * n, dtype="float64")
        files["checksum_ok"] = pd.Series([None] * n, dtype="boolean")
        return files, None

    # --- target directory (avoid overwrite) ----
    download_to = _normalize(download_to)
    if os.path.isdir(download_to):
        download_to = f"{download_to}/{folder_name}"
    base = download_to
    i = 0
    while os.path.isdir(download_to):
        i += 1
        download_to = f"{base}_{i}"
    try:
        os.mkdir(download_to)
    except OSError:
        pass  # R: dir.create(showWarnings = FALSE); a failure shows up as missing files
    _tick(pb, f"- Created directory {download_to}")

    temppath = tempfile.mkdtemp()
    try:
        downloaded = [False] * n
        extracted: list[int | None] = [None] * n
        keys = [None if is_na(v) else str(v) for v in files["key"].tolist()]
        ids = [None if is_na(v) else str(v) for v in files["id"].tolist()]
        selfs = [None if is_na(v) else str(v) for v in files["self"].tolist()]
        n_wanted = n - sum(omitted)
        failed_rows: list[tuple[str | None, str | None, str | None]] = []
        k = 0
        for i in range(n):
            if omitted[i]:
                continue
            k += 1
            if unzippable[i]:
                _tick(pb, f"Reading zip contents of {_paste(keys[i])}")
                try:
                    got = zip_members(
                        selfs[i],
                        dest=download_to,
                        keep_types=unzip_types,
                        max_file_size=max_file_size,
                    )
                except Exception:
                    got = None
                if got is not None:
                    oks = got["ok"].tolist() if "ok" in got else []
                    extracted[i] = sum(1 for v in oks if _is_true(v))
                    downloaded[i] = True
                    _tick(
                        pb,
                        f"- extracted {extracted[i]} file{plural(extracted[i])} from {_paste(keys[i])}",
                    )
                    # a member that failed left a row with ok = FALSE: say why,
                    # so a passing failure can be told from a lasting one
                    bad = _failed_members(got)
                    failed_rows.extend((keys[i], name, why) for name, why in bad)
                    for name, why in bad:
                        _tick(pb, f"  - failed to extract {_paste(name)}: {_paste(why)}")
                    continue
                _tick(
                    pb,
                    f"- could not read {_paste(keys[i])} without downloading it; "
                    "fetching the whole archive",
                )
            ok = False
            if selfs[i] is not None and selfs[i] != "":
                target = f"{temppath}/{_paste(ids[i])}"
                try:
                    spec = headers({"method": "GET", "url": selfs[i], "headers": {}})
                except Exception:
                    spec = None
                if spec is not None:
                    ok = _fetch_file(
                        selfs[i],  # type: ignore[arg-type]
                        dict(spec.get("headers") or {}),
                        target,
                        reauth,
                    )
            downloaded[i] = ok
            _tick(pb, f"Downloading file {k} of {n_wanted}")

        # copy to the flat target directory using the original file name if available
        paths: list[str | None] = [None] * n
        for i in range(n):
            if extracted[i] is not None or not downloaded[i]:
                continue
            src = f"{temppath}/{_paste(ids[i])}"
            # a file with neither a name nor an id is still copied (file.path()
            # turns NA into "NA"), but its path is recorded as NA, so the
            # verification step marks it as not downloaded.
            fname = keys[i] if keys[i] is not None and keys[i] != "" else ids[i]
            if not _inside(download_to, _paste(fname)):
                continue  # divergence: R would write outside the folder (e.g. "../x")
            to = f"{download_to}/{_paste(fname)}"
            try:
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.copyfile(src, to)
            except OSError:
                pass  # R: file.copy() returns FALSE; verification catches it
            paths[i] = fname
    finally:
        shutil.rmtree(temppath, ignore_errors=True)

    files = files.copy()
    files["downloaded"] = pd.Series(downloaded, dtype="boolean")
    files["extracted"] = pd.Series(extracted, dtype="Int64")
    files["path"] = _string_series(paths)

    # --- verify what actually reached the disk ----
    files = verify(files, download_to)
    files.attrs["failed"] = _failed_frame(failed_rows)

    missing = [
        not bool(v) and not o for v, o in zip(files["downloaded"].tolist(), omitted, strict=True)
    ]
    n_missing = sum(missing)
    if n_missing > 0:
        worst = [_paste(k) for k, m in zip(files["key"].tolist(), missing, strict=True) if m][:3]
        nf = n_wanted
        warnings.warn(
            f"{n_missing} of {nf} file{plural(nf)} from {what} {ident} did not arrive intact "
            f"(e.g. {', '.join(worst)}). The returned table marks "
            f"{'it' if n_missing == 1 else 'them'} downloaded = FALSE. Run again to retry.",
            stacklevel=3,
        )
    return files, download_to


def _failed_members(got: pd.DataFrame) -> list[tuple[str | None, str | None]]:
    """``got[!(got$ok %in% TRUE), ]``: (name, error) of the members that failed."""
    n = len(got)
    oks = got["ok"].tolist() if "ok" in got else [None] * n
    names = got["name"].tolist() if "name" in got else [None] * n
    errors = got["error"].tolist() if "error" in got else ["unknown failure"] * n
    return [
        (None if is_na(nm) else str(nm), None if is_na(e) else str(e))
        for nm, ok, e in zip(names, oks, errors, strict=True)
        if not _is_true(ok)
    ]


def _failed_frame(rows: Sequence[tuple[str | None, str | None, str | None]]) -> pd.DataFrame:
    """The ``failed`` table of the ``*_file_download()`` functions (key, member, error)."""
    return pd.DataFrame(
        {
            "key": _string_series([r[0] for r in rows]),
            "member": _string_series([r[1] for r in rows]),
            "error": _string_series([r[2] for r in rows]),
        }
    )


def _bind_downloads(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of several ``*_file_download()`` tables.

    dplyr keeps the first table's attributes (so its ``failed`` table); pandas
    would instead compare every frame's ``attrs``, which fails for a DataFrame
    value.
    """
    from metacheck._r import bind_rows

    first = dict(frames[0].attrs)
    stripped = []
    for f in frames:
        f = f.copy()
        f.attrs = {}
        stripped.append(f)
    out = bind_rows(stripped)
    out.attrs = first
    return out


def _finish_file_table(
    files: pd.DataFrame, download_to: str | None, ids: dict[str, Any], columns: Sequence[str]
) -> pd.DataFrame:
    """``files$folder <- basename(download_to)``, the id columns, then R's column order.

    Keeps ``attrs["failed"]`` (R sets ``attr(files, "failed")`` after this).
    """
    failed = files.attrs.get("failed")
    files = files.copy()
    n = len(files)
    folder = None if download_to is None else download_to.rstrip("/").rpartition("/")[2]
    files["folder"] = _string_series([folder] * n)
    for name, value in ids.items():
        files[name] = (
            _string_series([value] * n)
            if value is None or isinstance(value, str)
            else pd.Series([value] * n)
        )
    out = files.loc[:, list(columns)].reset_index(drop=True)
    if failed is not None:
        out.attrs["failed"] = failed
    return out


def _download_many(
    items: Sequence[Any],
    one: Callable[[Any], pd.DataFrame | None],
    label: Callable[[Any], Any],
    pb: Any,
    start: str,
    done: str,
) -> pd.DataFrame | None:
    """The ``# --- iterate over multiple ...`` branch of the ``*_file_download()`` functions."""
    from metacheck.archives import _tick

    _tick(pb, start)
    results = []
    for item in items:
        try:
            results.append(one(item))
        except Exception as e:  # R: tryCatch(..., error = warning + NULL)
            warnings.warn(f"{_paste(label(item))} resulted in an error:\n  {e}\n", stacklevel=3)
            results.append(None)
    frames = [r for r in results if r is not None]
    if not frames:
        return None
    dl = _bind_downloads(frames)
    _tick(pb, done)
    return dl


# ---------------------------------------------------------------------------
# Dataverse
# ---------------------------------------------------------------------------


def _dataverse_hosts() -> list[str]:
    """Port of R/archive-dataverse.R::.dataverse_hosts(): the known installations."""
    return list(DATAVERSE_HOSTS)


@functools.cache
def _dataverse_host_regex() -> str:
    """Port of R/archive-dataverse.R::.dataverse_host_regex(): any known host, as a regex."""
    return "|".join(h.replace(".", r"\.") for h in DATAVERSE_HOSTS)


def _dataverse_doi_prefix_hosts() -> dict[str, list[str]]:
    """Port of R/archive-dataverse.R::.dataverse_doi_prefix_hosts(): host -> DOI prefixes."""
    return {host: list(prefixes) for host, prefixes in DATAVERSE_DOI_PREFIX_HOSTS.items()}


@functools.cache
def _dataverse_doi_prefix_regex() -> str:
    """Port of R/archive-dataverse.R::.dataverse_doi_prefix_regex(): any verified prefix."""
    prefixes = dict.fromkeys(p for ps in DATAVERSE_DOI_PREFIX_HOSTS.values() for p in ps)
    return "|".join(p.replace(".", r"\.") for p in prefixes)


@functools.cache
def _prefix_hosts() -> dict[str, tuple[str, ...]]:
    """DOI prefix -> every host listing it, in list order."""
    out: dict[str, list[str]] = {}
    for host, prefixes in DATAVERSE_DOI_PREFIX_HOSTS.items():
        for p in prefixes:
            out.setdefault(p, []).append(host)
    return {p: tuple(hosts) for p, hosts in out.items()}


def _dataverse_host_from_doi(doi: Any) -> Any:
    """Port of R/archive-dataverse.R::.dataverse_host_from_doi().

    The installation a DOI belongs to, from its verified prefix (``None`` when
    the prefix is not on the allowlist). A prefix that several installations
    share (the four DANS Data Stations share 10.17026) is resolved by
    following the DOI's redirect (:func:`_dataverse_resolve_doi_host`). A
    string gives a string, a sequence a list.
    """
    vals, scalar = _chr_values(doi)
    px = sub(r"^(10\.\d+).*", r"\1", vals)
    table = _prefix_hosts()
    out: list[str | None] = []
    for d, p in zip(vals, px, strict=True):
        candidates = table.get(p) if d is not None and d != "" and p is not None else None
        if not candidates or d is None:
            out.append(None)
        elif len(candidates) == 1:
            out.append(candidates[0])
        else:
            out.append(_dataverse_resolve_doi_host(d, candidates))
    return out[0] if scalar else out


def _resolved_url(url: str) -> str | None:
    """The URL a GET of *url* ends on, after redirects; ``None`` when it cannot be sent.

    R: ``tryCatch(resp_url(req_perform(req_error(request(url), is_error = \\(r) FALSE))),
    error = \\(e) NA)`` -- one try, any status.
    """
    from metacheck import http

    try:
        resp = http.request("GET", url, max_tries=1)
    except Exception:
        return None
    return None if resp is None else str(resp.url)


def _bare_host(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _url_host(url: str) -> str | None:
    """The host name of *url*, lower case and without a leading ``www.``."""
    from urllib.parse import urlsplit

    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return None if not host else _bare_host(host)


def _on_host(url_host: str, host: str) -> bool:
    """Whether *url_host* (see :func:`_url_host`) is *host* or one of its subdomains."""
    h = _bare_host(host)
    return url_host == h or url_host.endswith("." + h)


def _dataverse_resolve_doi_host(doi: str, candidates: Sequence[str]) -> str:
    """Port of R/archive-dataverse.R::.dataverse_resolve_doi_host().

    The candidate whose host the URL ``https://doi.org/<doi>`` redirects to,
    else the first candidate (also when the DOI cannot be resolved).

    R takes the first candidate whose name occurs anywhere in the resolved
    URL (``grepl(host, resolved, fixed = TRUE)``), so with the candidates
    ``rodbuk.pl`` and ``uj.rodbuk.pl``, in that order, a dataset on
    ``uj.rodbuk.pl`` is given ``rodbuk.pl``. Here the resolved URL's host
    name is compared: the candidate it equals, else the longest candidate it
    is a subdomain of (U211).
    """
    resolved = _resolved_url(f"https://doi.org/{doi}")
    url_host = None if resolved is None else _url_host(resolved)
    if url_host is not None:
        for host in candidates:
            if _bare_host(host) == url_host:
                return host
        parents = [h for h in candidates if _on_host(url_host, h)]
        if parents:
            return max(parents, key=len)
    return candidates[0]


@functools.cache
def _host_rx() -> Any:
    # a known host, not the start of a longer host name (dataverse.no is not
    # dataverse.northwestern.edu)
    return compile_r(f"(?:{_dataverse_host_regex()})(?![A-Za-z0-9-])", ignore_case=True, perl=True)


_DOI_PARAM = r"""persistentId=doi:([^&\s"']+)"""
_BARE_DOI = r"(10\.\d+/[A-Za-z0-9/._-]+)"


def _dataverse_parse(url: Any) -> pd.DataFrame:
    """Port of R/archive-dataverse.R::.dataverse_parse(): host and DOI of Dataverse URLs.

    One row per URL (``host``, ``doi``). The host is the known installation
    named in the URL, else the one a bare DOI's prefix belongs to; the DOI is
    the ``persistentId=doi:`` parameter (URL-decoded), else a bare
    ``10.xxxx/...`` in the URL, without a trailing ``"."``.

    Each URL is matched on its own. metacheck assigns the hosts found by one
    vectorised ``regmatches()`` by position, so a URL without a host name
    takes the next URL's host (U31). A ``persistentId`` whose escapes do not
    decode to valid UTF-8 (or hold a NUL) is not a DOI: that URL gets no DOI,
    where metacheck's string functions abort the call for every URL (U39).
    """
    urls, _ = _chr_values(url)
    n = len(urls)
    host: list[str | None] = [None] * n
    has_url = [u is not None and u != "" for u in urls]
    if not any(has_url):
        return pd.DataFrame({"host": _string_series(host), "doi": _string_series([None] * n)})

    rx = _host_rx()
    for i, u in enumerate(urls):
        if has_url[i]:
            m = rx.search(u)  # type: ignore[arg-type]
            host[i] = m.group(0).lower() if m else None

    doi_rx = compile_r(_DOI_PARAM, ignore_case=True, perl=True)
    doi: list[str | None] = []
    bad = [False] * n
    for i, u in enumerate(urls):
        m = doi_rx.search(u) if u is not None else None
        d = None
        if m:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    d = _url_decode(m.group(1))
            except ValueError:  # an embedded NUL
                d = None
                bad[i] = True
            if _invalid_utf8(d):
                d = None
                bad[i] = True
        doi.append(d)

    if any(d is None and h and not b for d, h, b in zip(doi, has_url, bad, strict=True)):
        bare_rx = compile_r(_BARE_DOI, perl=True)
        for i, u in enumerate(urls):
            if doi[i] is None and has_url[i] and not bad[i]:
                m = bare_rx.search(u)  # type: ignore[arg-type]
                doi[i] = m.group(1) if m else None

    doi = sub(r"\.$", "", doi)

    needed = [i for i in range(n) if has_url[i] and not host[i] and doi[i] is not None]
    if needed:
        resolved = _dataverse_host_from_doi([doi[i] for i in needed])
        for i, h in zip(needed, resolved, strict=True):
            host[i] = h
    return pd.DataFrame({"host": _string_series(host), "doi": _string_series(doi)})


@functools.cache
def _dataverse_prefilter() -> str:
    """A cheap pattern every bare host mention contains (see ``_link_matches()``).

    A match holds a known host followed by ``/``, so the host's last label,
    preceded by a dot: ``.<tld>/``. Starting with a literal, it is much
    cheaper to scan for than the host alternation itself (which, run on its
    own, costs more per sentence than the full pattern).
    """
    labels = "|".join(sorted({h.rsplit(".", 1)[1] for h in DATAVERSE_HOSTS}))
    return f"\\.(?:{labels})/"


@functools.cache
def _dataverse_doi_prefilter() -> str:
    """The core (``10.<digits>/<id char>`` of a verified prefix) every bare DOI mention contains."""
    prefixes = dict.fromkeys(p for ps in DATAVERSE_DOI_PREFIX_HOSTS.values() for p in ps)
    digits = "|".join(p.split(".", 1)[1] for p in prefixes)
    return f"10\\.(?:{digits})/[A-Za-z0-9/._-]"


def dataverse_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-dataverse.R::dataverse_links(): Dataverse links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table on a known
    Dataverse host, plus bare mentions in the text of a known host or of a DOI
    under a verified Dataverse prefix. Trailing slashes are stripped and
    duplicate rows dropped; ``dataverse_url``, ``dataverse_host`` and
    ``dataverse_doi`` columns are added.
    """
    host_regex = _dataverse_host_regex()
    found_href = _url_rows(paper, host_regex)
    frame = _search_frame(paper)  # built once for both searches
    dv_bare_regex = f"(?:https?://)?(?:www\\.)?(?:{host_regex})/[A-Za-z0-9/danddoi:._?=&%-]*"
    other_dv = _link_matches(paper, dv_bare_regex, _dataverse_prefilter(), frame)
    dv_doi_regex = (
        f"(?:https?://)?(?:doi\\.org/)?(?:{_dataverse_doi_prefix_regex()})/[A-Za-z0-9/._-]+"
    )
    other_dv_doi = _link_matches(paper, dv_doi_regex, _dataverse_doi_prefilter(), frame)

    links = _collect_links([found_href, other_dv, other_dv_doi])
    links["dataverse_url"] = links["href"]
    parsed = _dataverse_parse(links["dataverse_url"])
    links["dataverse_host"] = parsed["host"].to_numpy()
    links["dataverse_doi"] = parsed["doi"].to_numpy()
    links["dataverse_host"] = links["dataverse_host"].astype("string")
    links["dataverse_doi"] = links["dataverse_doi"].astype("string")
    return links


def dataverse_info(
    dataverse_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-dataverse.R::dataverse_info(): look up Dataverse datasets.

    *dataverse_url* is a URL, a sequence of URLs, or a table whose *id_col*
    (1-based position or name) holds them (e.g. from :func:`dataverse_links`).
    Each dataset with a recognised host and DOI is fetched from its
    installation's API (with *cache*, from the on-disk listing cache when
    possible); the input rows are returned with ``dataverse_host``,
    ``dataverse_doi`` and the dataset's ``title``, ``doi``,
    ``publication_date``, ``updated_date``, ``authors``, ``license`` and
    ``files`` (or ``error``) added.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.utils import left_join, online

    with _spinner(pb, "Dataverse Retrieve") as bar:
        table = _info_table(
            dataverse_url, id_col, "dataverse_url", ("dataverse_host", "dataverse_doi")
        )
        parsed = _dataverse_parse(table["dataverse_url"])
        ids = pd.DataFrame(
            {
                "dataverse_url": table["dataverse_url"].to_numpy(),
                "dataverse_host": parsed["host"].to_numpy(),
                "dataverse_doi": parsed["doi"].to_numpy(),
            }
        ).astype({"dataverse_host": "string", "dataverse_doi": "string"})
        if ids["dataverse_url"].dtype == object and all(
            isinstance(v, str) or is_na(v) for v in ids["dataverse_url"]
        ):
            ids["dataverse_url"] = ids["dataverse_url"].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids["dataverse_url"].notna().to_numpy()].reset_index(drop=True)
        valid = ids[(ids["dataverse_host"].notna() & ids["dataverse_doi"].notna()).to_numpy()]
        # one request per dataset: two URLs for the same dataset (e.g. with and
        # without a trailing ".") share it, and each joins to one info row
        # (metacheck fetches it once per URL, and the join then repeats rows: U35)
        valid = valid.drop_duplicates(["dataverse_host", "dataverse_doi"])

        if len(valid) == 0:
            _tick(bar, "No valid Dataverse links")
            return left_join(table, ids, by="dataverse_url")

        first_host = str(valid["dataverse_host"].iloc[0])
        if not online(first_host):
            raise ConnectionError(f"{first_host} seems to be offline")

        nv = len(valid)
        _tick(bar, f"Starting Dataverse retrieval for {nv} dataset{'' if nv == 1 else 's'}...")
        id_info = []
        for h, d in zip(
            valid["dataverse_host"].tolist(), valid["dataverse_doi"].tolist(), strict=True
        ):
            ckey = f"{h} {d}"
            cached = _repo_info_cache_get("dataverse", ckey) if cache is True else None
            if cached is not None:
                id_info.append(cached)
                continue
            got = _dataverse_info(h, d, pb=bar)
            if cache is True and _repo_info_ok(got):
                _repo_info_cache_put("dataverse", ckey, got)
            id_info.append(got)

        info = bind_rows(id_info)
        data = left_join(table, ids, by="dataverse_url")
        data = left_join(
            data, info, by=["dataverse_host", "dataverse_doi"], suffix=("", ".dataverse")
        )
        _tick(bar, "...Dataverse retrieval complete!")
        return data


def _dataverse_info(host: Any, doi: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-dataverse.R::.dataverse_info(): one dataset from one installation.

    Returns a one-row table: ``dataverse_host``, ``dataverse_doi`` and either
    ``error`` (``"unfound"``, with a warning, or ``"parse_error"``) or the
    dataset's metadata and file listing.
    """
    from metacheck.archives import _spinner, _tick

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from {_paste(host)} ({_paste(doi)})...")
        obj: dict[str, pd.Series] = {
            "dataverse_host": _cell(host),
            "dataverse_doi": _cell(doi),
        }
        api_url = (
            f"https://{_paste(host)}/api/datasets/:persistentId/?persistentId=doi:{_paste(doi)}"
        )
        resp = _query(api_url, _dataverse_headers)
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{_paste(doi)} could not be found on {_paste(host)}", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        rec = _resp_json(resp)
        if rec is None or _dollar(rec, "data") is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        data = _dollar(rec, "data")
        if not isinstance(data, dict):
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)
        version = _dollar(data, "latestVersion")
        if not isinstance(version, dict):
            version = {}
        fields = _dollars(version, "metadataBlocks", "citation", "fields")
        if fields is None:
            fields = []

        def field_val(type_name: str) -> Any:
            for f in _elements(fields):
                tn = _dollar(f, "typeName")
                if isinstance(tn, str) and tn == type_name:
                    return _dollar(f, "value")
            return None

        title = field_val("title")
        authors_field = field_val("author")
        authors = (
            [
                _chr_elt(_empty_or(_dollars(a, "authorName", "value"), None))
                for a in _elements(authors_field)
            ]
            if isinstance(authors_field, list | dict)
            else []
        )
        pub = _empty_or(
            _empty_or(_dollar(version, "releaseTime"), _dollar(data, "publicationDate")), None
        )
        files = _dollar(version, "files")
        obj["title"] = _field_cell(_empty_or(title, None))
        obj["doi"] = _field_cell(_empty_or(_dollar(data, "persistentUrl"), None))
        obj["publication_date"] = _field_cell(pub)
        obj["updated_date"] = _field_cell(_empty_or(_dollar(version, "lastUpdateTime"), None))
        obj["authors"] = _list_cell(authors)
        # older installations give the licence as a plain string ("CC0"), which
        # metacheck's `license$name` cannot read (U34)
        licence = _dollar(version, "license")
        if not isinstance(licence, str):
            licence = _dollar(licence, "name") if isinstance(licence, dict) else None
        obj["license"] = _field_cell(_empty_or(licence, None))
        obj["files"] = _list_cell(files if files is not None else [])
        return pd.DataFrame(obj)


def _dataverse_headers(req: dict[str, Any]) -> dict[str, Any]:
    """Port of R/archive-dataverse.R::.dataverse_headers(): Dataverse request headers.

    *req* is a request spec (``{"method", "url", "headers", ...}``, as used by
    :func:`metacheck.http.batch_query`); a new spec is returned with
    ``User-Agent: metacheck`` and, when a token is set for the URL's host
    (:func:`dataverse_pat`), ``X-Dataverse-key``.
    """
    from urllib.parse import urlsplit

    try:
        host = urlsplit(str(req.get("url"))).hostname
    except ValueError:
        host = None
    spec = dict(req)
    headers = dict(spec.get("headers") or {})
    headers["User-Agent"] = "metacheck"
    try:
        pat = _dataverse_pat(host)
    except Exception:
        pat = ""
    if pat:
        headers["X-Dataverse-key"] = pat
    spec["headers"] = headers
    return spec


def dataverse_pat(host: str | None, pat: str | None = None) -> str:
    """Port of R/archive-dataverse.R::dataverse_pat(): get or set a Dataverse API token.

    Tokens are per installation. Without *pat*, returns the token set for
    *host* this session, else the ``DATAVERSE_PAT_<HOST>`` environment
    variable (host upper-cased, runs of other characters replaced by ``_``,
    e.g. ``DATAVERSE_PAT_DATAVERSE_HARVARD_EDU``), else ``""``. With *pat*,
    sets it for the session.
    """
    return _dataverse_pat(host, pat)


def _dataverse_pat(host: str | None, pat: Any = None) -> str:
    """Port of R/archive-dataverse.R::.dataverse_pat()."""
    from metacheck.utils import get_option, options

    raw = "" if host is None else ("NA" if is_na(host) else str(host))
    key = gsub("[^A-Za-z0-9]+", "_", raw).upper()
    opt = f"metacheck.dataverse.pat.{key}"
    env = f"DATAVERSE_PAT_{key}"
    if pat is None:
        value = get_option(opt)
        return value if value is not None else os.environ.get(env, "")
    if not isinstance(pat, str):
        raise ValueError("Set dataverse_pat with a single string containing your Dataverse token")
    options({opt: pat})
    return pat


_FILE_COLUMNS = (
    "folder",
    "dataverse_host",
    "dataverse_doi",
    "id",
    "key",
    "path",
    "size",
    "size_on_disk",
    "checksum",
    "checksum_ok",
    "self",
    "downloaded",
    "extracted",
)


def dataverse_file_download(
    host: Any,
    doi: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-dataverse.R::dataverse_file_download(): download a dataset's files.

    Creates a folder for the dataset under *download_to* (named from the DOI;
    ``_1``, ``_2``... when it exists) and downloads each file into it. Files
    over *max_file_size* MB are skipped, then the largest files until the
    total is under *max_download_size* MB (``None``/``inf``: no limit). Zips
    are downloaded whole unless *unzip_types* names file categories (as
    ``data_classify_files()`` does) to extract from them instead. *host* and
    *doi* may be vectors (recycled). Returns the file table (``None`` when
    the dataset lists no files); files left out by the size limits have
    ``downloaded = False`` (when all are, no folder is made and ``folder`` is
    missing), and files that did not arrive intact have ``downloaded =
    False`` and are warned about.
    """
    from metacheck.archives import _spinner, _tick

    hosts, _ = _as_values(host)
    dois, _ = _as_values(doi)
    if not hosts or not dois:
        return None

    with _spinner(pb, "Dataverse File Download") as bar:
        if len(hosts) > 1 or len(dois) > 1:
            m = max(len(hosts), len(dois))
            pairs = list(
                dict.fromkeys((hosts[i % len(hosts)], dois[i % len(dois)]) for i in range(m))
            )
            npairs = len(pairs)
            return _download_many(
                pairs,
                lambda p: dataverse_file_download(
                    p[0],
                    p[1],
                    download_to=download_to,
                    max_file_size=max_file_size,
                    max_download_size=max_download_size,
                    unzip_types=unzip_types,
                    pb=bar,
                ),
                lambda p: p[1],
                bar,
                f"Starting downloads for {npairs} Dataverse dataset{'' if npairs == 1 else 's'}...\n",
                f"...Completed downloads for {npairs} Dataverse dataset{'' if npairs == 1 else 's'}",
            )

        h, d = hosts[0], dois[0]
        if is_na(h) or str(h) == "" or is_na(d) or str(d) == "":
            return None
        h, d = str(h), str(d)

        _tick(bar, f"Starting retrieval for {d} on {h}")
        contents = _dataverse_info(h, d, pb=bar)
        files_list: Any = []
        if "files" in contents.columns and len(contents) > 0:
            files_list = contents["files"].iloc[0]
        if files_list is None or len(files_list) == 0:
            _tick(bar, f"- {d} contained no files")
            return None

        rows = []
        for x in _elements(files_list):
            df = _dollar(x, "dataFile")
            if df is None:
                df = {}
            file_id = _dollar(df, "id")
            rows.append(
                {
                    "id": _json_chr(_empty_or(file_id, None)),
                    "key": _empty_or(_empty_or(_dollar(x, "label"), _dollar(df, "filename")), None),
                    "size": _as_numeric(_empty_or(_dollar(df, "filesize"), None)),
                    "checksum": _empty_or(_dollars(df, "checksum", "value"), None),
                    "checksum_type": _lower(_empty_or(_dollars(df, "checksum", "type"), None)),
                    "self": (
                        f"https://{h}/api/access/datafile/{_json_chr(file_id)}"
                        if not _is_empty(file_id)
                        else None
                    ),
                }
            )
        files = _file_frame(rows, typed=True)
        if len(files) == 0:
            from metacheck.archives import _message

            _message("- ", d, " contained no files")
            return None

        done = _download_file_table(
            files,
            ident=d,
            folder_name=gsub("[^A-Za-z0-9._-]+", "_", d),
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            unzip_types=unzip_types,
            pb=bar,
            headers=_dataverse_headers,
            zip_members=lambda *a, **k: _dataverse_zip_members(*a, **k),
            verify=lambda f, to: _dataverse_verify_downloads(f, to),
            what="Dataverse dataset",
        )
        if done is None:
            return None
        files, folder = done
        return _finish_file_table(
            files, folder, {"dataverse_host": h, "dataverse_doi": d}, _FILE_COLUMNS
        )


def _lower(x: Any) -> Any:
    """R ``tolower()`` of a JSON scalar (``NA`` stays ``NA``)."""
    if x is None:
        return None
    s = _json_chr(x) if not isinstance(x, str) else x
    return None if s is None else s.lower()


def _file_frame(rows: list[dict[str, Any]], typed: bool) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of the per-file tibbles built by the ``*_file_download()`` functions."""
    cols = ["id", "key", "size", "checksum"] + (["checksum_type"] if typed else []) + ["self"]
    data: dict[str, pd.Series] = {}
    for c in cols:
        vals = [r[c] for r in rows]
        if c == "size":
            data[c] = pd.Series(vals, dtype="float64")
        elif all(v is None or isinstance(v, str) for v in vals):
            data[c] = _string_series(vals)
        else:
            data[c] = pd.Series(vals, dtype=object)
    return pd.DataFrame(data)


def _dataverse_zip_members(
    url: str,
    dest: str,
    keep_types: Any = ("data", "documentation"),
    max_file_size: float | None = 10,
) -> pd.DataFrame | None:
    """Port of R/archive-dataverse.R::.dataverse_zip_members(): extract wanted zip members.

    Reads the zip's central directory with byte-range requests and fetches
    only members of the *keep_types* categories no larger than
    *max_file_size* MB into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), or ``None`` when the archive cannot be listed
    (the caller then downloads it whole).
    """
    return _zip_members(url, dest, keep_types, max_file_size)


def _dataverse_verify_downloads(files: pd.DataFrame | None, download_to: str) -> Any:
    """Port of R/archive-dataverse.R::.dataverse_verify_downloads(): check files on disk.

    ``downloaded`` becomes whether each file is on disk under *download_to*
    with the reported size and (for an MD5 ``checksum_type``) checksum; adds
    ``size_on_disk`` and ``checksum_ok``. Rows extracted from a zip count as
    downloaded.
    """
    return _verify_file_table(files, download_to, typed=True)
