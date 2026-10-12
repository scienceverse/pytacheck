"""ORCiD and CRediT helpers (port of ``R/svutils-orcid.R``)."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from metacheck._r.base import as_character, trimws
from metacheck._r.regex import gsub
from metacheck._values import is_missing

if TYPE_CHECKING:
    import pandas as pd
    from lxml import etree

__all__ = ["check_orcid", "credit_roles", "get_orcid", "orcid_person"]

_CREDIT_ROLES = {
    "Conceptualization": "Ideas; formulation or evolution of overarching research goals and aims.",
    "Data curation": "Management activities to annotate (produce metadata), scrub data and maintain research data (including software code, where it is necessary for interpreting the data itself) for initial use and later re-use.",
    "Formal analysis": "Application of statistical, mathematical, computational, or other formal techniques to analyse or synthesize study data.",
    "Funding acquisition": "Acquisition of the financial support for the project leading to this publication.",
    "Investigation": "Conducting a research and investigation process, specifically performing the experiments, or data/evidence collection.",
    "Methodology": "Development or design of methodology; creation of models.",
    "Project administration": "Management and coordination responsibility for the research activity planning and execution.",
    "Resources": "Provision of study materials, reagents, materials, patients, laboratory samples, animals, instrumentation, computing resources, or other analysis tools.",
    "Software": "Programming, software development; designing computer programs; implementation of the computer code and supporting algorithms; testing of existing code components.",
    "Supervision": "Oversight and leadership responsibility for the research activity planning and execution, including mentorship external to the core team.",
    "Validation": "Verification, whether as a part of the activity or separate, of the overall replication/reproducibility of results/experiments and other research outputs.",
    "Visualization": "Preparation, creation and/or presentation of the published work, specifically visualization/data presentation.",
    "Writing - original draft": "Preparation, creation and/or presentation of the published work, specifically writing the initial draft (including substantive translation).",
    "Writing - review & editing": "Preparation, creation and/or presentation of the published work by those from the original research group, specifically critical review, commentary or revision -- including pre- or post-publication stages.",
}

_CREDIT_ABBR = [
    "con",
    "dat",
    "ana",
    "fun",
    "inv",
    "met",
    "adm",
    "res",
    "sof",
    "sup",
    "val",
    "vis",
    "dra",
    "edi",
]


def credit_roles(display: str | list[str] = ("explain", "names", "abbr")) -> list[str] | None:  # type: ignore[assignment]
    """CRediT roles (port of ``credit_roles()``).

    ``display="explain"`` (the default) prints each role with its number,
    abbreviation and definition and returns ``None``; ``"abbr"`` returns the
    abbreviations and anything else the role names.
    """
    first = display if isinstance(display, str) else next(iter(display))
    if first == "explain":
        for i, (name, desc) in enumerate(_CREDIT_ROLES.items(), start=1):
            print(f"[{i}/{_CREDIT_ABBR[i - 1]}] {name}: {desc}")
        return None
    if first == "abbr":
        return list(_CREDIT_ABBR)
    return list(_CREDIT_ROLES)


def check_orcid(orcid: Any) -> str | bool:
    """Check the validity of an ORCiD (port of ``check_orcid()``).

    Accepts the bare or URL form; returns the formatted 16-character ORCiD
    (``0000-0002-7523-5539``) when its checksum is valid, else ``False``
    (with a warning when :func:`metacheck.verbose` is on).
    """
    from metacheck.config import verbose

    def invalid() -> bool:
        if verbose():
            shown = "NA" if is_missing(orcid) else orcid
            warnings.warn(f"The ORCiD {shown} is not valid.", stacklevel=3)
        return False

    # a missing ORCiD, or an X before the check digit, is not valid (metacheck
    # fails on `if (NA ...)` for both; U14)
    if is_missing(orcid):
        return invalid()
    base = str(gsub("[^0-9X]", "", as_character(orcid)))
    if len(base) != 16 or not base[:15].isdigit():
        return invalid()
    total = 0
    for ch in base[:15]:
        total = (total + int(ch)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    check = "X" if result == 10 else str(result)
    if check == base[15]:
        return f"{base[0:4]}-{base[4:8]}-{base[8:12]}-{base[12:16]}"
    return invalid()


def _initials(given: str) -> str:
    """The ``given2`` transformation of ``get_orcid()`` (before URL encoding)."""
    g = trimws(given)
    g = gsub(r"^(\w)\.?$", r"\1\*", g)  # single initial
    g = gsub(r"^(.)\.?\s", r"\1\* ", g)  # initial initial
    g = gsub(r"\s(.)\.?$", r" \1\*", g)  # ending initial
    g = gsub(r"\s(.)\.?\s", r" \1\* ", g)  # internal initial
    return str(g)


def _open_url(url: str, accept: str = "application/xml") -> bytes:
    """``url(path, "rb")``: the body, or an error when the URL cannot be opened."""
    from metacheck import http

    # base R's url() makes a single attempt (no httr2 retry policy)
    resp = http.request("GET", url, headers={"Accept": accept}, max_tries=1)
    if resp is None:
        raise ConnectionError(f"cannot open the connection to '{url}'")
    if resp.status_code >= 400:
        reason = f" {resp.reason_phrase}" if resp.reason_phrase else ""
        raise ConnectionError(
            f"cannot open URL '{url}': HTTP status was '{resp.status_code}{reason}'"
        )
    return resp.content


def _parse_xml(content: bytes) -> etree._Element:
    """``xml2::read_xml()`` of a downloaded body."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(content, parser=parser)


def _read_xml(url: str, accept: str = "application/xml") -> etree._Element:
    return _parse_xml(_open_url(url, accept))


def _namespaces(root: etree._Element) -> dict[str, str]:
    """Prefix -> URI for every namespace declared in the document (like xml2)."""
    ns: dict[str, str] = {}
    for el in root.iter():
        for prefix, uri in (el.nsmap or {}).items():
            if prefix and prefix not in ns:
                ns[prefix] = uri
    return ns


def _find_text(root: etree._Element, xpath: str, join: str | None = None) -> Any:
    """``.xml_find_text(xml, xpath, join)``: trimmed texts, runs of spaces collapsed."""
    ns = _namespaces(root)
    try:
        nodes: Any = root.xpath(xpath, namespaces=ns)
    except Exception:  # an undeclared prefix: xml2 finds nothing
        nodes = []
    if not isinstance(nodes, list):
        nodes = [nodes]
    raw = [
        "".join(str(t) for t in n.itertext()) if hasattr(n, "itertext") else str(n) for n in nodes
    ]
    texts: list[str] = gsub(" +", " ", [t.strip() for t in raw])
    if join is not None:
        return join.join(texts)
    return texts if texts else ""


def get_orcid(family: str, given: str | None = "*") -> Any:
    """Get ORCiDs from a name (port of ``get_orcid()``).

    Searches the ORCID public API by family name and (optionally) given
    names; initials are expanded to wildcards (``"L M"`` -> ``"L* M*"``).
    Returns the matching ORCiD(s), or ``""`` when the search failed.
    """
    from metacheck.db._utils import url_encode

    if family is None or trimws(family) == "":
        raise ValueError("You must include a family name")
    if given is None or trimws(given) == "":
        given = "*"
    query = "https://pub.orcid.org/v3.0/search/?q=family-name:{}+AND+given-names:{}"
    url = query.format(url_encode(trimws(family)), url_encode(_initials(given)))
    # url(..., "rb") is opened outside tryCatch(): a failed connection or an
    # HTTP error status is an error; only unreadable XML gives the warning
    content = _open_url(url)
    try:
        root = _parse_xml(content)
    except Exception:  # tryCatch(error = ) catches every error
        warnings.warn("ORCID search failed", stacklevel=2)
        return ""
    orcid = _find_text(root, "//common:path")  # "" when there are none
    if isinstance(orcid, list) and len(orcid) == 1:
        return orcid[0]
    return orcid


def orcid_person(orcid: Any) -> pd.DataFrame:
    """Get person details for ORCiDs (port of ``orcid_person()``).

    One row per ORCiD with ``orcid``, ``given``, ``family``, ``email``
    (list), ``country``, ``keywords`` (list) and ``urls`` (list); an ORCiD
    that cannot be fetched gets an ``error`` message instead.
    """
    from metacheck._r.frames import bind_rows
    from metacheck.db._utils import as_vector, records_frame

    rows = []
    for x in as_vector(orcid):
        # file.path() pastes a missing ORCiD as "NA"
        path = f"https://pub.orcid.org/v3.0/{'NA' if is_missing(x) else as_character(x)}/person"
        try:
            root = _read_xml(path)
        except Exception as exc:
            rows.append(records_frame([{"orcid": x, "error": str(exc)}]))
            continue

        def as_list(v: Any) -> list[str]:
            return v if isinstance(v, list) else [v]

        rows.append(
            records_frame(
                [
                    {
                        "orcid": x,
                        "given": _find_text(root, "//personal-details:given-names", " "),
                        "family": _find_text(root, "//personal-details:family-name", " "),
                        "email": as_list(_find_text(root, "//email:email //email:email")),
                        "country": _find_text(root, "//address:country", ";"),
                        "keywords": as_list(_find_text(root, "//keyword:content")),
                        "urls": as_list(_find_text(root, "//researcher-url:url")),
                    }
                ]
            )
        )
    return bind_rows(rows)
