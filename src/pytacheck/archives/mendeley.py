"""Mendeley Data (port of ``R/archive-mendeley.R``).

Mendeley Data is one hosted service (institutional front ends share its
backend), so links are recognised by ``data.mendeley.com`` or the
``10.17632`` DOI prefix. Its public API only lists the files of a dataset's
current version, so dataset ids drop any version suffix.
"""

from __future__ import annotations

import warnings
from typing import Any

import pandas as pd

from pytacheck._r import is_na

__all__ = ["mendeley_info", "mendeley_links"]

_ID_PATTERNS = (
    r"10\.17632/([A-Za-z0-9]+)(?:\.[0-9]+)?",
    r"data\.mendeley\.com/datasets/([A-Za-z0-9]+)(?:/[0-9]+)?",
)


def mendeley_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-mendeley.R::mendeley_links(): Mendeley Data links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``data.mendeley.com`` or ``10.17632/``, plus bare dataset URLs and DOIs in
    the text. Trailing slashes are stripped and duplicate rows dropped;
    ``mendeley_url``, ``mendeley_id`` and ``mendeley_link`` (the dataset's
    DOI URL) columns are added.
    """
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links, _string_series, _url_rows

    found_href = _url_rows(paper, r"data\.mendeley\.com|10\.17632/")
    bare = (
        r"(?:https?://)?data\.mendeley\.com/datasets/[A-Za-z0-9]+(?:/[0-9]+)?"
        r"|(?:https?://)?(?:doi\.org/)?10\.17632/[A-Za-z0-9]+(?:\.[0-9]+)?"
    )
    other = _scan_links(paper, bare, ["data.mendeley.com/datasets/", "10.17632/"])
    links = _collect_links([found_href, other])
    ids = [_mendeley_id_one(u) for u in links["href"].tolist()]
    links["mendeley_url"] = links["href"]
    links["mendeley_id"] = _string_series(ids)
    links["mendeley_link"] = _string_series(
        [None if i is None else f"https://doi.org/10.17632/{i}" for i in ids]
    )
    return links


def _mendeley_id_one(x: Any) -> str | None:
    from pytacheck.archives.reshare import _id_one

    return _id_one(x, "^[A-Za-z0-9]+$", _ID_PATTERNS)


def _mendeley_id(mendeley_url: Any) -> Any:
    """Port of R/archive-mendeley.R::.mendeley_id(): dataset ids from URLs or DOIs.

    An alphanumeric value is an id; otherwise the id is taken from a
    ``10.17632/<id>[.<version>]`` DOI or a
    ``data.mendeley.com/datasets/<id>[/<version>]`` URL (the version is
    dropped); ``None`` where there is none. A string gives a string, a
    sequence a list.
    """
    from pytacheck.archives.dataone import _map

    return _map(mendeley_url, _mendeley_id_one)


def mendeley_info(
    mendeley_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-mendeley.R::mendeley_info(): look up Mendeley Data datasets.

    *mendeley_url* is a URL/DOI/id, a sequence of them, or a table whose
    *id_col* (1-based position or name) holds them (e.g. from
    :func:`mendeley_links`). Each dataset is fetched from the public API
    (with *cache*, from the on-disk listing cache when possible); the input
    rows are returned with ``mendeley_id`` and the dataset's ``title``,
    ``doi``, ``description``, ``publication_date``, ``updated_date``,
    ``authors``, ``license`` and ``files`` (or ``error``) added.
    """
    from pytacheck.archives.reshare import _info_by_id
    from pytacheck.utils import online

    if not online("data.mendeley.com"):
        raise ConnectionError("data.mendeley.com seems to be offline")
    return _info_by_id(
        mendeley_url,
        id_col,
        pb,
        cache,
        url_col="mendeley_url",
        id_name="mendeley_id",
        to_id=_mendeley_id_one,
        label="Mendeley Data",
        noun="dataset",
        cache_ns="mendeley",
        one=_mendeley_info,
        suffix=".mendeley",
    )


def _mendeley_info(mendeley_id: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-mendeley.R::.mendeley_info(): one Mendeley Data dataset.

    Returns a one-row table: ``mendeley_id`` and either ``error``
    (``"unfound"``, with a warning, or ``"parse_error"``) or ``title``,
    ``doi``, ``description``, ``publication_date``, ``updated_date``,
    ``authors``, ``license`` and ``files`` (the API's file records).
    """
    from pytacheck._r import trimws
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import (
        _cell,
        _dollar,
        _dollars,
        _elements,
        _empty_or,
        _field_cell,
        _paste,
        _query,
    )
    from pytacheck.archives.psycharchives import _obj_cell, _paste_json, _resp_json

    with _spinner(pb) as bar:
        mid = _paste(mendeley_id)
        _tick(bar, f"* Retrieving info from Mendeley Data {mid}...")
        obj: dict[str, pd.Series] = {"mendeley_id": _cell(None if is_na(mendeley_id) else mid)}

        resp = _query(f"https://data.mendeley.com/public-api/datasets/{mid}", lambda spec: spec)
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{mid} could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        rec = _resp_json(resp)
        if rec is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        def author(a: Any) -> str | None:
            first = _paste_json(_empty_or(_dollar(a, "first_name"), ""))
            last = _paste_json(_empty_or(_dollar(a, "last_name"), ""))
            full = trimws(f"{first} {last}")
            return str(full) if full else None

        contributors = _dollar(rec, "contributors")
        authors = (
            [author(a) for a in _elements(contributors)]
            if isinstance(contributors, list | dict)
            else []
        )

        obj["title"] = _field_cell(_empty_or(_dollar(rec, "name"), None))
        obj["doi"] = _field_cell(_empty_or(_dollars(rec, "doi", "id"), None))
        obj["description"] = _field_cell(_empty_or(_dollar(rec, "description"), None))
        obj["publication_date"] = _field_cell(_empty_or(_dollar(rec, "publish_date"), None))
        obj["updated_date"] = _field_cell(_empty_or(_dollar(rec, "modified_on"), None))
        obj["authors"] = _obj_cell(authors)
        licence = _dollar(rec, "data_licence")
        obj["license"] = _field_cell(
            _empty_or(
                _empty_or(_dollar(licence, "short_name"), _dollar(licence, "full_name")), None
            )
        )
        files = _dollar(rec, "files")
        obj["files"] = _obj_cell(files if files is not None else [])
        return pd.DataFrame(obj)
