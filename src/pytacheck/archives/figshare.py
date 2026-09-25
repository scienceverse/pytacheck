"""Figshare (port of ``R/archive-figshare.R``).

Figshare is one hosted service reached through ``api.figshare.com``, but
papers cite it under many addresses: ``*.figshare.com`` institutional
subdomains, the ``figsh.com`` root, institutions' own "vanity" domains
(:data:`FIGSHARE_VANITY_HOSTS`) and institutional DOI prefixes
(:data:`FIGSHARE_DOI_PREFIX_HOSTS`) besides the generic
``10.6084/m9.figshare.<id>``. Every function takes a ``host`` so that the
Figshare-compatible 4TU.ResearchData API (``data.4tu.nl``) can reuse it.
"""

from __future__ import annotations

import functools
import os
import warnings
from typing import Any

import pandas as pd

from pytacheck._r import compile_r, grepl, is_na, trimws
from pytacheck.archives.dataverse import (
    _as_numeric,
    _cell,
    _chr_elt,
    _chr_values,
    _collect_links,
    _dollar,
    _download_file_table,
    _download_many,
    _elements,
    _empty_or,
    _field_cell,
    _file_frame,
    _finish_file_table,
    _info_table,
    _json_chr,
    _link_matches,
    _list_cell,
    _paste,
    _query,
    _resp_json,
    _string_series,
    _url_rows,
    _verify_file_table,
    _zip_members,
)

__all__ = [
    "FIGSHARE_DOI_PREFIX_HOSTS",
    "FIGSHARE_VANITY_HOSTS",
    "figshare_file_download",
    "figshare_info",
    "figshare_links",
    "figshare_pat",
]

#: R ``.figshare_vanity_hosts()``: institutions' own domains fronting figshare.com.
FIGSHARE_VANITY_HOSTS: tuple[str, ...] = (
    "aura.american.edu",  # American University Research Archive (USA)
    "books.openmonographs.org",  # Figshare-hosted monograph platform
    "indigo.uic.edu",  # University of Illinois Chicago (USA)
    "figshare.le.ac.uk",  # University of Leicester (UK)
    "drum.um.edu.mt",  # University of Malta
    "data.dtu.dk",  # Technical University of Denmark
    "dro.deakin.edu.au",  # Deakin University (Australia)
    "repository.mmu.ac.uk",  # Manchester Metropolitan University (UK)
    "figshare.unimelb.edu.au",  # University of Melbourne (Australia)
    "ore.exeter.ac.uk",  # Open Research Exeter (UK)
    "figshare.shef.ac.uk",  # University of Sheffield (UK)
    "orda.shef.ac.uk",  # University of Sheffield, ORDA (UK)
    "figshare.warwick.ac.uk",  # University of Warwick (UK)
    "rdr.ucl.ac.uk",  # University College London (UK)
    "zivahub.uct.ac.za",  # University of Cape Town (South Africa)
    "redata.arizona.edu",  # University of Arizona (USA)
    "bridges.monash.edu",  # Monash University (Australia)
    "brunel.figshare.com",  # Brunel University London (UK)
    "city.figshare.com",  # City, University of London (UK)
    "curate.curtin.edu.au",  # Curtin University (Australia)
    "tandf.figshare.com",  # Taylor & Francis (publisher instance)
    "asha.figshare.com",  # American Speech-Language-Hearing Association (publisher instance)
    "smithsonian.figshare.com",  # Smithsonian Institution (USA)
    "orcid.figshare.com",  # ORCID (publisher/org instance)
    "plus.figshare.com",  # Figshare+ (publisher-neutral instance)
    "griffith.figshare.com",  # Griffith University (Australia)
    "repository.lboro.ac.uk",  # Loughborough University (UK)
    "nhlbi.figshare.com",  # National Heart, Lung, and Blood Institute (USA)
    "arizona.figshare.com",  # University of Arizona (USA) -- separate DataCite client from redata.arizona.edu above
    "su.figshare.com",  # Stockholm University (Sweden)
    "figshare.swinburne.edu.au",  # Swinburne University of Technology (Australia)
    "ucb.figshare.com",  # University College Birmingham (UK)
    "auckland.figshare.com",  # University of Auckland (New Zealand)
    "uc.figshare.com",  # University of Cincinnati (USA)
    "figshare.manchester.ac.uk",  # University of Manchester (UK)
    "novasbe.figshare.com",  # Universidade Nova de Lisboa, NOVA SBE (Portugal)
    "figshare.uts.edu.au",  # University of Technology Sydney (Australia)
    "melbourne.figshare.com",  # University of Melbourne (Australia) -- separate DataCite client/host from figshare.unimelb.edu.au above
)

#: R ``.figshare_doi_prefix_hosts()``: institutional DOI prefix -> Figshare host.
FIGSHARE_DOI_PREFIX_HOSTS: dict[str, str] = {
    "10.17633": "brunel.figshare.com",
    "10.25383": "city.figshare.com",
    "10.25917": "curate.curtin.edu.au",
    "10.26187": "dro.deakin.edu.au",
    "10.60809": "drum.um.edu.mt",
    "10.23641": "asha.figshare.com",
    "10.25573": "smithsonian.figshare.com",
    "10.23640": "orcid.figshare.com",
    "10.25452": "plus.figshare.com",
    "10.57831": "griffith.figshare.com",
    "10.17028": "repository.lboro.ac.uk",
    "10.26174": "repository.lboro.ac.uk",
    "10.23634": "repository.mmu.ac.uk",
    "10.25858": "repository.mmu.ac.uk",
    "10.83056": "repository.mmu.ac.uk",
    "10.25444": "nhlbi.figshare.com",
    "10.24378": "ore.exeter.ac.uk",
    "10.15131": "orda.shef.ac.uk",
    "10.25422": "arizona.figshare.com",
    "10.17045": "su.figshare.com",
    "10.25916": "figshare.swinburne.edu.au",
    "10.83399": "ucb.figshare.com",
    "10.17608": "auckland.figshare.com",
    "10.25375": "zivahub.uct.ac.za",
    "10.60696": "uc.figshare.com",
    "10.48420": "figshare.manchester.ac.uk",
    "10.82444": "figshare.warwick.ac.uk",
    "10.60580": "novasbe.figshare.com",
    "10.71741": "figshare.uts.edu.au",
    "10.26180": "bridges.monash.edu",
    "10.26188": "melbourne.figshare.com",
}


def _figshare_vanity_hosts() -> list[str]:
    """Port of R/archive-figshare.R::.figshare_vanity_hosts()."""
    return list(FIGSHARE_VANITY_HOSTS)


def _figshare_doi_prefix_hosts() -> dict[str, str]:
    """Port of R/archive-figshare.R::.figshare_doi_prefix_hosts(): prefix -> host."""
    return dict(FIGSHARE_DOI_PREFIX_HOSTS)


@functools.cache
def _figshare_host_regex() -> str:
    """Port of R/archive-figshare.R::.figshare_host_regex(): any recognised Figshare host."""
    vanity = "|".join(h.replace(".", r"\.") for h in FIGSHARE_VANITY_HOSTS)
    return f"figshare\\.com|figsh\\.com|{vanity}"


@functools.cache
def _figshare_prefilter() -> str:
    """A cheap pattern every bare-mention match contains (see ``_link_matches()``).

    Each branch of ``figshare_links()``'s pattern only adds optional parts
    around a core its matches always contain: a host (whose last label is
    preceded by a dot) followed by ``/articles/`` (etc.), or
    ``10.<digits>/<id char>`` for 10.6084 (which covers
    ``10.6084/m9.figshare.<id>``) and the institutional prefixes. Each
    alternative starts with a literal, so scanning sentences for it is fast.
    """
    hosts = ("figshare.com", "figsh.com", *FIGSHARE_VANITY_HOSTS)
    labels = "|".join(sorted({h.rsplit(".", 1)[1] for h in hosts}))
    digits = "|".join(p.split(".", 1)[1] for p in ("10.6084", *FIGSHARE_DOI_PREFIX_HOSTS))
    return f"\\.(?:{labels})/(?:articles|ndownloader|projects|s)/|10\\.(?:{digits})/[A-Za-z0-9._-]"


def _escape_prefixes(prefixes: Any) -> str:
    return "|".join(p.replace(".", r"\.") for p in prefixes)


@functools.cache
def _doi_prefix_regex() -> str:
    """10.6084 and every institutional prefix, as a regex alternation."""
    return _escape_prefixes(("10.6084", *FIGSHARE_DOI_PREFIX_HOSTS))


def figshare_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-figshare.R::figshare_links(): Figshare links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning a
    Figshare host or DOI prefix, plus bare mentions in the text (article,
    download, project and share URLs; ``10.6084/m9.figshare.<id>`` and
    institutional DOIs). Trailing slashes are stripped and duplicate rows
    dropped; ``figshare_url``, ``figshare_id`` and ``figshare_unsupported``
    (a private share link, which cannot be resolved) are added.
    """
    host_regex = _figshare_host_regex()
    doi_prefix_regex = _doi_prefix_regex()
    found_href = _url_rows(paper, f"{host_regex}|{doi_prefix_regex}")
    fs_bare_regex = (
        f"(?:https?://)?(?:[a-z0-9.-]+\\.)?(?:{host_regex})/(?:articles|ndownloader|projects|s)"
        "/[A-Za-z0-9/_.-]*"
        "|(?:https?://)?(?:doi\\.org/)?10\\.6084/m9\\.figshare\\.[0-9]+(?:\\.v[0-9]+)?"
        f"|(?:https?://)?(?:doi\\.org/)?(?:{doi_prefix_regex})/[A-Za-z0-9._-]+(?:\\.v[0-9]+)?"
    )
    other_fs = _link_matches(paper, fs_bare_regex, _figshare_prefilter())

    links = _collect_links([found_href, other_fs])
    links["figshare_url"] = links["href"]
    links["figshare_id"] = _string_series(_figshare_id(links["figshare_url"].tolist()))
    share = grepl(r"figshare\.com/s/[A-Za-z0-9]+", links["figshare_url"].tolist(), ignore_case=True)
    links["figshare_unsupported"] = pd.Series(
        [bool(s) and is_na(i) for s, i in zip(share, links["figshare_id"].tolist(), strict=True)],
        dtype=bool,
    )
    return links


@functools.cache
def _figshare_id_patterns() -> tuple[Any, ...]:
    host_regex = _figshare_host_regex()
    inst_prefix_regex = _escape_prefixes(FIGSHARE_DOI_PREFIX_HOSTS)
    patterns = (
        r"10\.6084/m9\.figshare\.([0-9]+)",
        f"(?:{host_regex})/articles/(?:dataset|[a-z]+)/[^/]+/([0-9]+)",
        f"(?:{host_regex})/articles/[^/]+/([0-9]+)",
        f"(?:{host_regex})/articles/([0-9]+)",
        r"ndownloader\.figshare\.com/files/([0-9]+)",
        f"(?:{inst_prefix_regex})/(?:[a-z]+\\.)?([0-9]+)(?:\\.v[0-9]+)?(?:[^0-9]|$)",
    )
    return tuple(compile_r(p, ignore_case=True, perl=True) for p in patterns)


def _figshare_id_one(url: str | None) -> str | None:
    if url is None:
        return None
    url = trimws(url)
    if url == "":
        return None
    if grepl("^[0-9]+$", url):
        return url
    for rx in _figshare_id_patterns():
        m = rx.search(url)
        if m is not None:
            return m.group(1)
    return None


def _figshare_id(figshare_url: Any) -> Any:
    """Port of R/archive-figshare.R::.figshare_id(): article IDs from URLs or DOIs.

    Recognises bare numeric IDs, ``10.6084/m9.figshare.<id>[.vN]`` DOIs,
    ``/articles/[type/][slug/]<id>`` URLs on any Figshare host,
    ``ndownloader.figshare.com/files/<id>`` and institutional DOIs (the last
    all-digit segment). Project and share URLs have no article ID (``None``).
    A string gives a string, a sequence a list.
    """
    vals, scalar = _chr_values(figshare_url)
    out = [_figshare_id_one(v) for v in vals]
    return out[0] if scalar else out


@functools.cache
def _project_rx() -> Any:
    return compile_r(
        f"(?:{_figshare_host_regex()})/projects/[^/]+/([0-9]+)/?$", ignore_case=True, perl=True
    )


def _figshare_project_id(figshare_url: Any) -> Any:
    """Port of R/archive-figshare.R::.figshare_project_id(): project IDs from project URLs.

    The trailing number of a ``<figshare host>/projects/<name>/<id>`` URL, else
    ``None``. A string gives a string, a sequence a list.
    """
    vals, scalar = _chr_values(figshare_url)
    rx = _project_rx()
    out: list[str | None] = []
    for v in vals:
        s = None if v is None else trimws(v)
        m = rx.search(s) if s else None
        out.append(m.group(1) if m else None)
    return out[0] if scalar else out


def _figshare_project_articles(
    project_id: Any, host: str = "api.figshare.com", pb: Any = None
) -> list[str]:
    """Port of R/archive-figshare.R::.figshare_project_articles(): a project's article IDs.

    Pages through the public ``/v2/projects/<id>/articles`` endpoint (100 per
    page) and returns the unique article IDs; warns when the project cannot
    be found.
    """
    from pytacheck.archives import _spinner

    with _spinner(pb):
        all_ids: list[str] = []
        page = 1
        while True:
            api_url = (
                f"https://{host}/v2/projects/{_paste(project_id)}/articles"
                f"?page={page}&page_size=100"
            )
            resp = _query(api_url, lambda req: _figshare_headers(req, host=host))
            if resp is None or resp.status_code != 200:
                if not all_ids:
                    warnings.warn(
                        f"Figshare project {_paste(project_id)} could not be found on {host}",
                        stacklevel=2,
                    )
                break
            rec = _resp_json(resp)
            if rec is None or (isinstance(rec, list | dict) and len(rec) == 0):
                break
            items = _elements(rec)
            ids = [_json_chr(_empty_or(_dollar(a, "id"), None)) for a in items]
            all_ids.extend(i for i in ids if i is not None)
            if len(items) < 100:
                break
            page += 1
        return list(dict.fromkeys(all_ids))


def figshare_info(
    figshare_url: Any,
    id_col: int | str = 1,
    host: str = "api.figshare.com",
    pb: Any = None,
    cache: bool = False,
) -> pd.DataFrame:
    """Port of R/archive-figshare.R::figshare_info(): look up Figshare articles.

    *figshare_url* is a URL/DOI/ID, a sequence of them, or a table whose
    *id_col* (1-based position or name) holds them (e.g. from
    :func:`figshare_links`). A project URL is expanded into one row per
    article it contains. Each article is fetched from *host*'s API (with
    *cache*, from the on-disk listing cache when possible); the input rows
    are returned with ``figshare_id`` and the article's ``title``, ``doi``,
    ``publication_date``, ``updated_date``, ``authors``, ``license`` and
    ``files`` (or ``error``) added.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from pytacheck.utils import left_join, online

    if not online(host):
        raise ConnectionError(f"{host} seems to be offline")

    with _spinner(pb, "Figshare Retrieve") as bar:
        table = _info_table(figshare_url, id_col, "figshare_url", ("figshare_id",))
        urls = table["figshare_url"].tolist()
        ids = pd.DataFrame(
            {
                "figshare_url": table["figshare_url"].to_numpy(),
                "figshare_id": _string_series(_figshare_id(urls)),
            }
        )
        if all(isinstance(v, str) or is_na(v) for v in urls):
            ids["figshare_url"] = ids["figshare_url"].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids["figshare_url"].notna().to_numpy()].reset_index(drop=True)

        unresolved = ids["figshare_id"].isna().to_numpy()
        if unresolved.any():
            project_urls = ids["figshare_url"][unresolved].tolist()
            project_ids = _figshare_project_id(project_urls)
            project_rows = []
            for url, proj_id in zip(project_urls, project_ids, strict=True):
                if proj_id is None:
                    continue
                article_ids = _figshare_project_articles(proj_id, host=host, pb=bar)
                if article_ids:
                    project_rows.append((url, article_ids))
            if project_rows:
                expanded = list(dict.fromkeys(url for url, _ in project_rows))
                drop = (
                    ids["figshare_url"].isin(expanded).to_numpy()
                    & ids["figshare_id"].isna().to_numpy()
                )
                ids = ids[~drop]
                extra = pd.DataFrame(
                    {
                        "figshare_url": [url for url, arts in project_rows for _ in arts],
                        "figshare_id": _string_series(
                            [a for _, arts in project_rows for a in arts]
                        ),
                    }
                ).astype({"figshare_url": ids["figshare_url"].dtype})
                ids = bind_rows([ids, extra]).drop_duplicates().reset_index(drop=True)

        valid_ids = list(dict.fromkeys(v for v in ids["figshare_id"].tolist() if not is_na(v)))
        if not valid_ids:
            _tick(bar, "No valid Figshare links")
            return left_join(table, ids, by="figshare_url")

        nv = len(valid_ids)
        _tick(bar, f"Starting Figshare retrieval for {nv} article{'' if nv == 1 else 's'}...")
        id_info = []
        for fid in valid_ids:
            ckey = f"{host} {fid}"
            cached = _repo_info_cache_get("figshare", ckey) if cache is True else None
            if cached is not None:
                id_info.append(cached)
                continue
            got = _figshare_info(fid, host=host, pb=bar)
            if cache is True and _repo_info_ok(got):
                _repo_info_cache_put("figshare", ckey, got)
            id_info.append(got)

        info = bind_rows(id_info)
        data = left_join(table, ids, by="figshare_url")
        data = left_join(data, info, by="figshare_id", suffix=("", ".figshare"))
        _tick(bar, "...Figshare retrieval complete!")
        return data


def _figshare_info(
    figshare_id: Any, host: str = "api.figshare.com", pb: Any = None
) -> pd.DataFrame:
    """Port of R/archive-figshare.R::.figshare_info(): one article.

    Returns a one-row table: ``figshare_id`` and either ``error``
    (``"unfound"``, with a warning, or ``"parse_error"``) or the article's
    metadata and ``files``.
    """
    from pytacheck.archives import _spinner, _tick

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from Figshare article {_paste(figshare_id)}...")
        fid = _json_chr(figshare_id) if not isinstance(figshare_id, str) else figshare_id
        obj: dict[str, pd.Series] = {"figshare_id": _cell(fid)}
        api_url = f"https://{host}/v2/articles/{_paste(figshare_id)}"
        resp = _query(api_url, lambda req: _figshare_headers(req, host=host))
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{_paste(figshare_id)} could not be found on {host}", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)
        rec = _resp_json(resp)
        if rec is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        authors_field = _dollar(rec, "authors")
        authors = (
            [_chr_elt(_empty_or(_dollar(a, "full_name"), None)) for a in _elements(authors_field)]
            if isinstance(authors_field, list | dict)
            else []
        )
        files = _dollar(rec, "files")
        obj["title"] = _field_cell(_empty_or(_dollar(rec, "title"), None))
        obj["doi"] = _field_cell(_empty_or(_dollar(rec, "doi"), None))
        obj["publication_date"] = _field_cell(_empty_or(_dollar(rec, "published_date"), None))
        obj["updated_date"] = _field_cell(_empty_or(_dollar(rec, "modified_date"), None))
        obj["authors"] = _list_cell(authors)
        # a plain-string licence is the licence name (metacheck's `license$name`
        # fails on it: U34)
        licence = _dollar(rec, "license")
        if not isinstance(licence, str):
            licence = _dollar(licence, "name")
        obj["license"] = _field_cell(_empty_or(licence, None))
        obj["files"] = _list_cell(files if files is not None else [])
        return pd.DataFrame(obj)


def _figshare_headers(req: dict[str, Any], host: str = "api.figshare.com") -> dict[str, Any]:  # noqa: ARG001
    """Port of R/archive-figshare.R::.figshare_headers(): Figshare request headers.

    Adds ``User-Agent: metacheck`` and, when a token is set
    (:func:`figshare_pat`), ``Authorization: token <pat>`` to a request spec.
    *host* is accepted for a uniform signature (R does not use it either).
    """
    spec = dict(req)
    headers = dict(spec.get("headers") or {})
    headers["User-Agent"] = "metacheck"
    try:
        pat = figshare_pat()
    except Exception:
        pat = ""
    if pat:
        headers["Authorization"] = f"token {pat}"
    spec["headers"] = headers
    return spec


def figshare_pat(pat: str | None = None) -> str:
    """Port of R/archive-figshare.R::figshare_pat(): get or set a Figshare API token.

    Without *pat*, returns the token set this session, else the
    ``FIGSHARE_PAT`` environment variable, else ``""``. A token is optional
    (it raises rate limits and unlocks private articles).
    """
    return _figshare_pat(pat)


def _figshare_pat(pat: Any = None) -> str:
    """Port of R/archive-figshare.R::.figshare_pat()."""
    from pytacheck.utils import get_option, options

    if pat is None:
        value = get_option("metacheck.figshare.pat")
        return value if value is not None else os.environ.get("FIGSHARE_PAT", "")
    if not isinstance(pat, str):
        raise ValueError("Set figshare_pat with a single string containing your Figshare token")
    options({"metacheck.figshare.pat": pat})
    return pat


_FILE_COLUMNS = (
    "folder",
    "figshare_id",
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


def figshare_file_download(
    figshare_id: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    host: str = "api.figshare.com",
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-figshare.R::figshare_file_download(): download an article's files.

    *figshare_id* is an article ID, URL or DOI, or a sequence of them. Creates
    a ``figshare_<id>`` folder under *download_to* (``_1``, ``_2``... when it
    exists) and downloads each file into it. Files over *max_file_size* MB
    are skipped, then the largest files until the total is under
    *max_download_size* MB (``None``/``inf``: no limit). Zips are downloaded
    whole unless *unzip_types* names file categories to extract from them.
    Returns the file table (``None`` when the article lists no files);
    files left out by the size limits have ``downloaded = False`` (when all
    are, no folder is made and ``folder`` is missing), and files that did
    not arrive intact have ``downloaded = False`` and are warned about.
    """
    from pytacheck.archives import _spinner, _tick

    if host == "data.4tu.nl":
        # 4TU ids (numeric or uuid) resolve as 4TU ids: .figshare_id() knows no
        # uuid, so metacheck silently downloads nothing for one (U41)
        from pytacheck.archives.fourtu import _researchdata4tu_id

        resolved = _researchdata4tu_id(figshare_id)
    else:
        resolved = _figshare_id(figshare_id)
    ids = list(dict.fromkeys(i for i in _as_list(resolved) if i is not None))
    if not ids:
        return None

    with _spinner(pb, "Figshare File Download") as bar:
        if len(ids) > 1:
            n = len(ids)
            return _download_many(
                ids,
                lambda x: figshare_file_download(
                    x,
                    download_to=download_to,
                    max_file_size=max_file_size,
                    max_download_size=max_download_size,
                    unzip_types=unzip_types,
                    host=host,
                    pb=bar,
                ),
                lambda x: x,
                bar,
                f"Starting downloads for {n} Figshare article{'' if n == 1 else 's'}...\n",
                f"...Completed downloads for {n} Figshare article{'' if n == 1 else 's'}",
            )

        fid = ids[0]
        _tick(bar, f"Starting retrieval for Figshare article {fid}")
        contents = _figshare_info(fid, host=host, pb=bar)
        files_list: Any = []
        if "files" in contents.columns and len(contents) > 0:
            files_list = contents["files"].iloc[0]
        if files_list is None or len(files_list) == 0:
            _tick(bar, f"- {fid} contained no files")
            return None

        rows = [
            {
                "id": _json_chr(_empty_or(_dollar(x, "id"), None)),
                "key": _empty_or(_dollar(x, "name"), None),
                "size": _as_numeric(_empty_or(_dollar(x, "size"), None)),
                "checksum": _empty_or(_dollar(x, "computed_md5"), None),
                "self": _empty_or(_dollar(x, "download_url"), None),
            }
            for x in _elements(files_list)
        ]
        files = _file_frame(rows, typed=False)
        if len(files) == 0:
            from pytacheck.archives import _message

            _message("- ", fid, " contained no files")
            return None

        done = _download_file_table(
            files,
            ident=fid,
            folder_name=f"figshare_{fid}",
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            unzip_types=unzip_types,
            pb=bar,
            headers=_figshare_headers,
            zip_members=lambda *a, **k: _figshare_zip_members(*a, **k),
            verify=lambda f, to: _figshare_verify_downloads(f, to),
            what="Figshare article",
        )
        if done is None:
            return None
        files, folder = done
        return _finish_file_table(files, folder, {"figshare_id": fid}, _FILE_COLUMNS)


def _as_list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else [x]


def _figshare_zip_members(
    url: str,
    dest: str,
    keep_types: Any = ("data", "documentation"),
    max_file_size: float | None = 10,
) -> pd.DataFrame | None:
    """Port of R/archive-figshare.R::.figshare_zip_members(): extract wanted zip members.

    Reads the zip's central directory with byte-range requests and fetches
    only members of the *keep_types* categories no larger than
    *max_file_size* MB into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), or ``None`` when the archive cannot be listed.
    """
    return _zip_members(url, dest, keep_types, max_file_size)


def _figshare_verify_downloads(files: pd.DataFrame | None, download_to: str) -> Any:
    """Port of R/archive-figshare.R::.figshare_verify_downloads(): check files on disk.

    ``downloaded`` becomes whether each file is on disk under *download_to*
    with the reported size and ``computed_md5``; adds ``size_on_disk`` and
    ``checksum_ok``. Rows extracted from a zip count as downloaded.
    """
    return _verify_file_table(files, download_to, typed=False)
