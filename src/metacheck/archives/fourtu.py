"""4TU.ResearchData (port of ``R/archive-4tu.R``).

4TU.ResearchData runs Djehuty, a backward-compatible implementation of the
Figshare v2 API, so this module holds link detection and id extraction
(``10.4121`` DOIs and ``data.4tu.nl`` URLs; a uuid is resolved to the
article's numeric id through its DOI) and thin wrappers around the Figshare port called with
``host = "data.4tu.nl"``.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from metacheck._values import is_missing

__all__ = [
    "researchdata4tu_file_download",
    "researchdata4tu_info",
    "researchdata4tu_links",
    "researchdata4tu_pat",
]

_HOST = "data.4tu.nl"

# the bare uuid comes before the numeric id: metacheck tries 10.4121/([0-9]+)
# first, so 10.4121/7f866e02-... gave the id "7" (U41)
_ID_PATTERNS = (
    r"10\.4121/uuid:([0-9a-f-]{36})",
    r"10\.4121/([0-9a-f-]{36})",
    r"10\.4121/([0-9]+)",
    r"data\.4tu\.nl/datasets/([0-9a-f-]{36})",
    # figshare-style article URLs: articles/<id>, articles/_/<id>/<version> and
    # articles/<type>/<title>/<id>/<version> (metacheck knows only the dataset type)
    r"data\.4tu\.nl/articles/(?:_/|[a-z_]+/[^/?#\s]+/)?([0-9]+)(?![^/?#\s])",
)


def researchdata4tu_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-4tu.R::researchdata4tu_links(): 4TU.ResearchData links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``data.4tu.nl`` or ``10.4121/``, plus bare article/dataset URLs and DOIs
    in the text. Trailing slashes are stripped and duplicate rows dropped;
    ``researchdata4tu_url`` and ``researchdata4tu_id`` columns are added.
    """
    from metacheck.archives.dataone import _scan_links
    from metacheck.archives.dataverse import _collect_links, _string_series, _url_rows

    found_href = _url_rows(paper, r"data\.4tu\.nl|10\.4121/")
    bare = (
        r"(?:https?://)?(?:www\.)?data\.4tu\.nl/(?:articles|datasets)/"
        r"[A-Za-z0-9/_.-]*"
        r"|(?:https?://)?(?:doi\.org/)?10\.4121/(?:uuid:)?[A-Za-z0-9-]+(?:\.v[0-9]+)?"
    )
    other = _scan_links(paper, bare, ["data.4tu.nl/", "10.4121/"])
    links = _collect_links([found_href, other])
    links["researchdata4tu_url"] = links["href"]
    links["researchdata4tu_id"] = _string_series(
        [_researchdata4tu_id_one(u) for u in links["href"].tolist()]
    )
    return links


#: An id as it is: digits, or a bare uuid (metacheck takes only digits, so a
#: uuid it resolved itself is not an id the second time round: U41).
_BARE_ID = "^([0-9]+|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$"


def _researchdata4tu_id_one(x: Any) -> str | None:
    from metacheck.archives.reshare import _id_one

    return _id_one(x, _BARE_ID, _ID_PATTERNS)


def _researchdata4tu_id(researchdata4tu_url: Any) -> Any:
    """Port of R/archive-4tu.R::.researchdata4tu_id(): article ids from URLs or DOIs.

    A digits-only value or a bare uuid is an id; otherwise the numeric id or uuid is taken
    from a ``10.4121`` DOI (the version suffix is dropped) or a
    ``data.4tu.nl`` URL; ``None`` where there is none. A string gives a
    string, a sequence a list.
    """
    from metacheck.archives.dataone import _map

    return _map(researchdata4tu_url, _researchdata4tu_id_one)


_UUID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
#: the numeric id at the end of a data.4tu.nl article URL
#: (".../articles/_/12675218/1", ".../articles/dataset/<name>/12675218")
_ARTICLE_ID = r"data\.4tu\.nl/articles/.*?([0-9]+)(?:/[0-9]+)?/?$"


def _researchdata4tu_resolve_uuid(uuid: str) -> str:
    """Port of R/archive-4tu.R::.researchdata4tu_resolve_uuid(): a uuid's numeric id.

    data.4tu.nl's ``v2/articles/<id>`` does not accept every article's uuid,
    so the uuid is resolved by following its DOI
    (``https://doi.org/10.4121/uuid:<uuid>``) to the article page and reading
    the numeric id off its URL. The uuid itself when that fails.
    """
    from metacheck._r import regexec
    from metacheck.archives.dataverse import _resolved_url

    resolved = _resolved_url(f"https://doi.org/10.4121/uuid:{uuid}")
    if resolved is None:
        return uuid
    groups = regexec(_ARTICLE_ID, resolved, perl=True, ignore_case=True)
    return str(groups[1]) if len(groups) >= 2 else uuid


def _resolve_if_uuid(x: Any) -> Any:
    """An id as it is, a uuid resolved by :func:`_researchdata4tu_resolve_uuid`."""
    from metacheck._r import grepl

    if is_missing(x) or not grepl(_UUID, str(x), ignore_case=True):
        return x
    return _researchdata4tu_resolve_uuid(str(x))


def researchdata4tu_info(
    researchdata4tu_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-4tu.R::researchdata4tu_info(): look up 4TU.ResearchData articles.

    *researchdata4tu_url* is a URL/DOI/id, a sequence of them, or a table
    whose *id_col* (1-based position or name) holds them (e.g. from
    :func:`researchdata4tu_links`). Each
    article is fetched from ``data.4tu.nl``'s Figshare-compatible API (with
    *cache*, from the on-disk listing cache shared with
    ``figshare_info(host = "data.4tu.nl")``); the rows are returned with
    ``researchdata4tu_id`` and the article's ``title``, ``doi``,
    ``publication_date``, ``updated_date``, ``authors``, ``license`` and
    ``files`` (or ``error``) added.
    """
    from metacheck._r import bind_rows
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _info_table, _string_series
    from metacheck.archives.figshare import _figshare_info
    from metacheck.archives.psycharchives import _cached
    from metacheck.utils import left_join, online

    if researchdata4tu_url is None:
        # R: data.frame(researchdata4tu_url = NULL) has no columns, so the final
        # left_join() by "researchdata4tu_url" fails (after the online() check)
        if not online(_HOST):
            raise ConnectionError("data.4tu.nl seems to be offline")
        raise ValueError(
            "Join columns in `x` must be present in the data.\n"
            "✖ Problem with `researchdata4tu_url`."
        )
    # a table keeps its columns and a vector is de-duplicated without NA, as in
    # the other *_info() functions (metacheck keeps only the id column of a
    # table, and a vector with an NA fails: U33)
    table = _info_table(researchdata4tu_url, id_col, "researchdata4tu_url", ("researchdata4tu_id",))
    raw = [None if is_missing(v) else v for v in table["researchdata4tu_url"].tolist()]

    if not online(_HOST):
        raise ConnectionError("data.4tu.nl seems to be offline")

    with _spinner(pb, "4TU.ResearchData Retrieve") as bar:
        ids = pd.DataFrame(
            {
                "researchdata4tu_url": table["researchdata4tu_url"].to_numpy(),
                "researchdata4tu_id": _string_series([_researchdata4tu_id_one(u) for u in raw]),
            }
        ).astype({"researchdata4tu_url": table["researchdata4tu_url"].dtype})
        ids = ids.drop_duplicates()
        ids = ids[ids["researchdata4tu_url"].notna().to_numpy()].reset_index(drop=True)
        # a uuid is not an id v2/articles/<id> accepts in general: resolved to
        # the article's numeric id first, so the joins and cache keys use it
        ids["researchdata4tu_id"] = _string_series(
            [_resolve_if_uuid(v) for v in ids["researchdata4tu_id"].tolist()]
        )
        valid = list(
            dict.fromkeys(v for v in ids["researchdata4tu_id"].tolist() if not is_missing(v))
        )

        if not valid:
            _tick(bar, "No valid 4TU.ResearchData links")
            return left_join(table, ids, by="researchdata4tu_url")

        nv = len(valid)
        _tick(
            bar,
            f"Starting 4TU.ResearchData retrieval for {nv} article{'' if nv == 1 else 's'}...",
        )
        id_info = [
            _cached(
                "figshare",
                f"{_HOST} {v}",
                cache,
                lambda v=v: _figshare_info(v, host=_HOST, pb=bar),
            )
            for v in valid
        ]
        info = bind_rows(id_info).rename(columns={"figshare_id": "researchdata4tu_id"})
        data = left_join(table, ids, by="researchdata4tu_url")
        data = left_join(data, info, by="researchdata4tu_id", suffix=("", ".researchdata4tu"))
        _tick(bar, "...4TU.ResearchData retrieval complete!")
        return data


def researchdata4tu_pat(pat: str | None = None) -> str:
    """Port of R/archive-4tu.R::researchdata4tu_pat(): get or set a 4TU.ResearchData token.

    Without *pat*, returns the token set this session, else the
    ``RESEARCHDATA4TU_PAT`` environment variable, else ``""``. A token is
    optional (it raises rate limits and unlocks private articles); it is
    separate from :func:`figshare_pat`.
    """
    return _researchdata4tu_pat(pat)


def _researchdata4tu_pat(pat: Any = None) -> str:
    """Port of R/archive-4tu.R::.researchdata4tu_pat()."""
    from metacheck.utils import get_option, options

    opt = "metacheck.researchdata4tu.pat"
    if pat is None:
        value = get_option(opt)
        return value if value is not None else os.environ.get("RESEARCHDATA4TU_PAT", "")
    if not isinstance(pat, str):
        raise ValueError(
            "Set researchdata4tu_pat with a single string containing your 4TU.ResearchData token"
        )
    options({opt: pat})
    return pat


def researchdata4tu_file_download(
    researchdata4tu_id: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-4tu.R::researchdata4tu_file_download(): download an article's files.

    :func:`figshare_file_download` with ``host = "data.4tu.nl"``, authenticated
    with :func:`researchdata4tu_pat` rather than :func:`figshare_pat`; the
    ``figshare_id`` column of the result is named ``researchdata4tu_id``.
    Articles known only by their uuid are downloaded too, by the numeric id
    the uuid resolves to (metacheck re-resolves the ids as Figshare ids, which
    drops them: U41).
    """
    from metacheck.archives.figshare import figshare_file_download
    from metacheck.archives.reshare import _unique_ids
    from metacheck.utils import local_options

    ids = _unique_ids(_researchdata4tu_id(researchdata4tu_id))
    # a uuid is looked up by its numeric id, as in researchdata4tu_info()
    ids = list(dict.fromkeys(_resolve_if_uuid(i) for i in ids))
    if not ids:
        return None
    with local_options({"metacheck.figshare.pat": _researchdata4tu_pat()}):
        dl = figshare_file_download(
            ids,
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            unzip_types=unzip_types,
            pb=pb,
            host=_HOST,
        )
    if dl is not None and "figshare_id" in dl.columns:
        dl = dl.rename(columns={"figshare_id": "researchdata4tu_id"})
    return dl
