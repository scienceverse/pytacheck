"""Dryad (port of ``R/archive-dryad.R``).

Dryad (https://datadryad.org) is a single hosted service. Its DOIs are
mostly ``10.5061/dryad.<id>``, but datasets minted under older prefixes
(:data:`DRYAD_DOI_PREFIXES`) are recognised too. Metadata and file listings
are public; downloading file bytes needs a token, either a static one
(:func:`dryad_pat`) or OAuth2 client credentials (:func:`dryad_auth`), which
are exchanged for short-lived access tokens automatically and refreshed
before they expire.
"""

from __future__ import annotations

import functools
import math
import os
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any

import pandas as pd

from pytacheck._r import compile_r, gsub, is_na, sub, trimws
from pytacheck.archives.dataverse import (
    RequestAbort,
    _as_numeric,
    _cell,
    _check_named_ids,
    _chr_values,
    _collect_links,
    _dollar,
    _dollars,
    _download_file_table,
    _download_many,
    _elements,
    _empty_or,
    _file_frame,
    _finish_file_table,
    _info_table,
    _link_matches,
    _list_cell,
    _mark_named_ids,
    _paste,
    _query,
    _resp_json,
    _string_series,
    _url_decode,
    _url_encode_reserved,
    _url_rows,
    _verify_file_table,
    _zip_members,
)

__all__ = [
    "DRYAD_DOI_PREFIXES",
    "DryadOAuthClient",
    "OAuthError",
    "dryad_auth",
    "dryad_file_download",
    "dryad_info",
    "dryad_links",
    "dryad_pat",
]

#: R ``.dryad_doi_prefixes()``: every DOI prefix Dryad has minted datasets under
#: (10.5061 is the usual ``10.5061/dryad.<id>`` form).
DRYAD_DOI_PREFIXES: tuple[str, ...] = (
    "10.5061",
    "10.15146",
    "10.25338",
    "10.25349",
    "10.5068",
    "10.6071",
    "10.6075",
    "10.6076",
    "10.6078",
    "10.6086",
    "10.7272",
    "10.7280",
    "10.7291",
    "10.7941",
)

_TOKEN_URL = "https://datadryad.org/oauth/token"  # noqa: S105 - an endpoint, not a secret


def _dryad_doi_prefixes() -> list[str]:
    """Port of R/archive-dryad.R::.dryad_doi_prefixes()."""
    return list(DRYAD_DOI_PREFIXES)


@functools.cache
def _alt_prefix_regex() -> str:
    """The prefixes other than 10.5061, as a regex alternation."""
    return "|".join(p.replace(".", r"\.") for p in DRYAD_DOI_PREFIXES if p != "10.5061")


@functools.cache
def _dryad_prefilter() -> str:
    """A cheap pattern every bare-mention match of ``dryad_links()``'s pattern contains.

    Each branch only adds optional parts around a core its matches always
    contain: ``datadryad.org/``, or ``10.<digits>/<id char>`` for 10.5061
    (``10.5061/dryad.<id>``) and the other Dryad prefixes. Sentences without
    one cannot match (see ``_link_matches()``).
    """
    digits = "|".join(p.split(".", 1)[1] for p in DRYAD_DOI_PREFIXES)
    return f"datadryad\\.org/|10\\.(?:{digits})/[A-Za-z0-9]"


def dryad_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-dryad.R::dryad_links(): Dryad links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``datadryad.org`` or a Dryad DOI, plus bare mentions in the text (dataset
    pages and DOIs under any Dryad prefix). Trailing slashes are stripped and
    duplicate rows dropped; ``dryad_url`` and ``dryad_doi`` columns are added.
    """
    alt = _alt_prefix_regex()
    found_href = _url_rows(paper, f"datadryad\\.org|10\\.5061/dryad|{alt}/")
    dryad_bare_regex = (
        "(?:https?://)?(?:www\\.)?datadryad\\.org/(?:stash/)?dataset[s]?/doi[:%]"
        "[A-Za-z0-9%._/-]*"
        "|(?:https?://)?(?:doi\\.org/)?10\\.5061/dryad\\.[A-Za-z0-9]+"
        f"|(?:https?://)?(?:doi\\.org/)?(?:{alt})/[A-Za-z0-9]+"
    )
    other_dryad = _link_matches(paper, dryad_bare_regex, _dryad_prefilter())

    links = _collect_links([found_href, other_dryad])
    links["dryad_url"] = links["href"]
    links["dryad_doi"] = _string_series(_dryad_doi(links["dryad_url"].tolist()))
    return _mark_named_ids(links, "dryad_doi")


@functools.cache
def _dryad_doi_patterns() -> tuple[Any, ...]:
    patterns = (r"(10\.5061/dryad\.[A-Za-z0-9]+)", f"((?:{_alt_prefix_regex()})/[A-Za-z0-9]+)")
    return tuple(compile_r(p, ignore_case=True, perl=True) for p in patterns)


def _dryad_doi_one(url: str | None) -> str | None:
    if url is None:
        return None
    url = trimws(url)
    if url == "":
        return None
    url = _url_decode(url)
    for rx in _dryad_doi_patterns():
        m = rx.search(url)
        if m is not None:
            return m.group(1).lower()
    return None


def _dryad_doi(dryad_url: Any) -> Any:
    """Port of R/archive-dryad.R::.dryad_doi(): the Dryad DOI in URLs or DOIs.

    The (URL-decoded) ``10.5061/dryad.<id>`` or alternate-prefix DOI, lower
    case; ``None`` when there is none. A string gives a string, a sequence a
    list.
    """
    vals, scalar = _chr_values(dryad_url)
    out = [_dryad_doi_one(v) for v in vals]
    return out[0] if scalar else out


def dryad_info(
    dryad_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-dryad.R::dryad_info(): look up Dryad datasets.

    *dryad_url* is a URL or DOI, a sequence of them, or a table whose *id_col*
    (1-based position or name) holds them (e.g. from :func:`dryad_links`).
    Each dataset is fetched from Dryad's API (with *cache*, from the on-disk
    listing cache when possible); the input rows are returned with
    ``dryad_doi`` and the dataset's ``title``, ``doi``, ``publication_date``,
    ``updated_date``, ``authors``, ``license`` and ``files`` (or ``error``)
    added.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from pytacheck.utils import left_join, online

    if not online("datadryad.org"):
        raise ConnectionError("Dryad seems to be offline")

    with _spinner(pb, "Dryad Retrieve") as bar:
        table = _info_table(dryad_url, id_col, "dryad_url", ("dryad_doi",))
        urls = table["dryad_url"].tolist()
        _check_named_ids(urls, dryad_url, id_col)
        ids = pd.DataFrame(
            {
                "dryad_url": table["dryad_url"].to_numpy(),
                "dryad_doi": _string_series(_dryad_doi(urls)),
            }
        )
        if all(isinstance(v, str) or is_na(v) for v in urls):
            ids["dryad_url"] = ids["dryad_url"].astype("string")
        ids = ids.drop_duplicates()
        ids = ids[ids["dryad_url"].notna().to_numpy()].reset_index(drop=True)
        valid_dois = list(dict.fromkeys(v for v in ids["dryad_doi"].tolist() if not is_na(v)))

        if not valid_dois:
            _tick(bar, "No valid Dryad links")
            return left_join(table, ids, by="dryad_url")

        nv = len(valid_dois)
        _tick(bar, f"Starting Dryad retrieval for {nv} dataset{'' if nv == 1 else 's'}...")
        id_info = []
        for doi in valid_dois:
            cached = _repo_info_cache_get("dryad", doi) if cache is True else None
            if cached is not None:
                id_info.append(cached)
                continue
            got = _dryad_info(doi, pb=bar)
            if cache is True and _repo_info_ok(got):
                _repo_info_cache_put("dryad", doi, got)
            id_info.append(got)

        info = bind_rows(id_info)
        data = left_join(table, ids, by="dryad_url")
        data = left_join(data, info, by="dryad_doi", suffix=("", ".dryad"))
        _tick(bar, "...Dryad retrieval complete!")
        return data


def _dryad_info(dryad_doi: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-dryad.R::.dryad_info(): one dataset.

    Returns a one-row table: ``dryad_doi`` and either ``error`` (``"unfound"``,
    with a warning, or ``"parse_error"``) or the dataset's metadata and the
    file listing of its latest version (``files``).
    """
    from pytacheck.archives import _spinner, _tick

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from Dryad DOI {_paste(dryad_doi)}...")
        obj: dict[str, pd.Series] = {"dryad_doi": _cell(dryad_doi)}
        encoded = _url_encode_reserved(f"doi:{_paste(dryad_doi)}")
        api_url = f"https://datadryad.org/api/v2/datasets/{encoded}"
        resp = _dryad_query(api_url)
        if resp is None or resp.status_code != 200:
            warnings.warn(f"{_paste(dryad_doi)} could not be found on Dryad", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)
        rec = _resp_json(resp)
        if rec is None:
            obj["error"] = _cell("parse_error")
            return pd.DataFrame(obj)

        authors_field = _dollar(rec, "authors")
        authors: list[str | None] = []
        if isinstance(authors_field, list | dict):
            for a in _elements(authors_field):
                first = _paste(_empty_or(_dollar(a, "firstName"), ""))
                last = _paste(_empty_or(_dollar(a, "lastName"), ""))
                full = trimws(f"{first} {last}")
                authors.append(full if full != "" else None)

        version_href = _dollars(rec, "_links", "stash:version", "href")
        files_list: Any = []
        if version_href is not None:
            files_resp = _dryad_query(f"https://datadryad.org{_paste(version_href)}/files")
            if files_resp is not None and files_resp.status_code == 200:
                files_rec = _resp_json(files_resp)
                listed = _dollars(files_rec, "_embedded", "stash:files")
                files_list = listed if listed is not None else []

        obj["title"] = _cell(_empty_or(_dollar(rec, "title"), None))
        obj["doi"] = _cell(_empty_or(_dollar(rec, "identifier"), None))
        obj["publication_date"] = _cell(_empty_or(_dollar(rec, "publicationDate"), None))
        obj["updated_date"] = _cell(_empty_or(_dollar(rec, "lastModificationDate"), None))
        obj["authors"] = _list_cell(authors)
        obj["license"] = _cell(_empty_or(_dollar(rec, "license"), None))
        obj["files"] = _list_cell(files_list)
        return pd.DataFrame(obj)


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryadOAuthClient:
    """An OAuth2 client for the client-credentials grant (R ``httr2::oauth_client()``).

    :meth:`token` returns a bearer token, fetching one from *token_url* on
    first use (``grant_type=client_credentials`` with the id and secret in the
    form body, as httr2 sends them) and again once the cached one is within
    30 seconds of expiring (httr2's ``expiry_margin``).
    """

    id: str
    secret: str = ""
    token_url: str = _TOKEN_URL

    def token(self) -> str:
        """A valid access token (cached per client for the session)."""
        return _client_token(self)

    def __repr__(self) -> str:  # never show the secret
        return f"DryadOAuthClient(id={self.id!r}, token_url={self.token_url!r})"


_TOKENS: dict[DryadOAuthClient, tuple[str, float | None]] = {}
_TOKENS_LOCK = threading.Lock()
_EXPIRY_MARGIN = 30.0


class OAuthError(RequestAbort, PermissionError):
    """httr2's ``httr2_oauth`` errors: the token endpoint refused the client or
    answered with something that is not a token.

    Not an ``httr2_error``, so in R it escapes ``.batch_query()`` and aborts
    :func:`dryad_info` (a token endpoint that cannot be reached is an
    ``httr2_error``, and only makes the dataset ``"unfound"``).
    """


def _parse_error(url: str) -> OAuthError:
    return OAuthError(f"Failed to parse response from `client$token_url` OAuth url ({url}).")


def _token_from_body(body: Any, url: str) -> tuple[str, float | None]:
    """httr2 ``oauth_flow_parse()`` + ``oauth_token()``: the access token and its expiry time."""
    from pytacheck.datacheck.files import _r_as_numeric

    if not isinstance(body, dict):  # rlang::has_name() on an unnamed value
        raise _parse_error(url)
    expires_in: float | None = None
    if "expires_in" in body:  # body$expires_in <- as.numeric(body$expires_in)
        value = body["expires_in"]
        if isinstance(value, list | dict) and len(value) == 1:  # as.numeric(list(x))
            value = next(iter(value.values())) if isinstance(value, dict) else value[0]
        # null or a longer array/object: not one number (numeric(0), a vector or an error)
        num = None if value is None or isinstance(value, list | dict) else _r_as_numeric(value)
        # oauth_token(): check_number_whole(expires_in, allow_null = TRUE)
        if num is None or math.isnan(num) or not math.isfinite(num) or not num.is_integer():
            raise OAuthError("`expires_in` must be a whole number.")
        expires_in = num
    if "access_token" in body or "device_code" in body:
        token = body.get("access_token")
        if not isinstance(token, str):  # check_string(access_token)
            raise OAuthError("`access_token` must be a single string.")
        if "token_type" in body and not isinstance(body["token_type"], str):
            raise OAuthError("`token_type` must be a single string.")
        return token, (None if expires_in is None else time.time() + expires_in)
    if "error" in body:
        raise OAuthError(f"OAuth failure [{_paste(body['error'])}]")
    raise _parse_error(url)


def _client_token(client: DryadOAuthClient) -> str:
    """``req_oauth_client_credentials()``'s token fetch/cache/refresh cycle.

    As httr2's ``oauth_client_get_token()``: one POST (no retries, any HTTP
    status accepted) whose JSON body must hold an ``access_token``; an
    ``error`` member or any other body raises :class:`OAuthError`. A token
    endpoint that cannot be reached raises :class:`ConnectionError`.
    """
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _resp_body_json

    with _TOKENS_LOCK:
        hit = _TOKENS.get(client)
        if hit is not None:
            tok, expires_at = hit
            if expires_at is None or time.time() + _EXPIRY_MARGIN <= expires_at:
                return tok
            del _TOKENS[client]
        resp = http.request(
            "POST",
            client.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client.id,
                "client_secret": client.secret,
            },
            headers={"Accept": "application/json"},
            max_tries=1,
        )
        if resp is None:  # an httr2_failure: caught as a failed request
            raise ConnectionError(f"Failed to connect to {client.token_url}")
        try:
            body = _resp_body_json(resp)  # oauth_flow_body(): resp_body_json(check_type = NA)
        except Exception as e:
            raise _parse_error(client.token_url) from e
        tok, expires_at = _token_from_body(body, client.token_url)
        _TOKENS[client] = (tok, expires_at)
        return tok


def _dryad_headers(req: dict[str, Any]) -> dict[str, Any]:
    """Port of R/archive-dryad.R::.dryad_headers(): Dryad request headers.

    Adds ``User-Agent: metacheck`` and a bearer token to a request spec: from
    the OAuth2 client credentials when set (:func:`dryad_auth`, fetched and
    refreshed automatically), else the static token (:func:`dryad_pat`). A
    token endpoint that cannot be reached raises :class:`ConnectionError`
    (a failed request); a refusal raises :class:`OAuthError`, which aborts
    the metadata lookups as in R.
    """
    spec = dict(req)
    headers = dict(spec.get("headers") or {})
    headers["User-Agent"] = "metacheck"
    try:
        client = _dryad_oauth_client()
    except Exception:
        client = None
    if client is not None:
        headers["Authorization"] = f"Bearer {client.token()}"
    else:
        try:
            pat = dryad_pat()
        except Exception:
            pat = ""
        if pat:
            headers["Authorization"] = f"Bearer {pat}"
    spec["headers"] = headers
    return spec


def _dryad_reauth(resp: Any) -> dict[str, str] | None:
    """httr2's one re-authentication: fresh headers after an OAuth ``invalid_token`` answer.

    ``req_perform()`` clears the cached token and sends the request once more
    when a request signed with OAuth client credentials gets a 401 whose
    ``WWW-Authenticate`` header contains ``error="invalid_token"``
    (``resp_is_invalid_oauth_token()``); otherwise ``None``.
    """
    if resp is None or resp.status_code != 401:
        return None
    if 'error="invalid_token"' not in (resp.headers.get("WWW-Authenticate") or ""):
        return None
    try:
        client = _dryad_oauth_client()
    except Exception:
        client = None
    if client is None:  # a static token: no OAuth policy, no re-authentication
        return None
    with _TOKENS_LOCK:
        _TOKENS.pop(client, None)
    return dict(_dryad_headers({"headers": {}})["headers"])


def _dryad_query(url: str) -> Any:
    """``.batch_query(url, msg = NULL, req_func = .dryad_headers)[[1]]``, with httr2's
    re-authentication after an OAuth ``invalid_token`` answer (see :func:`_dryad_reauth`)."""
    resp = _query(url, _dryad_headers)
    if resp is not None and resp.status_code == 401:
        try:
            fresh = _dryad_reauth(resp)
        except RequestAbort:
            raise
        except Exception:  # an httr2_error while re-authenticating: a NULL response
            return None
        if fresh is not None:
            resp = _query(url, _dryad_headers)
    return resp


def dryad_pat(pat: str | None = None) -> str:
    """Port of R/archive-dryad.R::dryad_pat(): get or set a Dryad API token.

    Without *pat*, returns the token set this session, else the ``DRYAD_PAT``
    environment variable, else ``""``. A token is required to download file
    bytes (even from public datasets); it expires after 10 hours, so prefer
    :func:`dryad_auth` for long runs.
    """
    return _dryad_pat(pat)


def _dryad_pat(pat: Any = None) -> str:
    """Port of R/archive-dryad.R::.dryad_pat()."""
    from pytacheck.utils import get_option, options

    if pat is None:
        value = get_option("metacheck.dryad.pat")
        return value if value is not None else os.environ.get("DRYAD_PAT", "")
    if not isinstance(pat, str):
        raise ValueError("Set dryad_pat with a single string containing your Dryad token")
    options({"metacheck.dryad.pat": pat})
    return pat


def dryad_auth(
    client_id: str | None = None,
    client_secret: str | None = None,
    token_url: str = _TOKEN_URL,
) -> DryadOAuthClient | None:
    """Port of R/archive-dryad.R::dryad_auth(): set or get Dryad OAuth2 client credentials.

    With *client_id* and *client_secret*, stores them (and *token_url*) for the
    session and returns the client. Without, returns the client built from
    the stored credentials, else the ``DRYAD_CLIENT_ID`` /
    ``DRYAD_CLIENT_SECRET`` environment variables, or ``None`` when either is
    missing. Client credentials take priority over :func:`dryad_pat`.
    """
    return _dryad_auth(client_id, client_secret, token_url)


def _dryad_auth(
    client_id: Any = None, client_secret: Any = None, token_url: str = _TOKEN_URL
) -> DryadOAuthClient | None:
    """Port of R/archive-dryad.R::.dryad_auth()."""
    from pytacheck.utils import get_option, options

    opt_id, opt_secret, opt_url = (
        "metacheck.dryad.client_id",
        "metacheck.dryad.client_secret",
        "metacheck.dryad.token_url",
    )
    if client_id is None:
        cid = get_option(opt_id)
        cid = cid if cid is not None else os.environ.get("DRYAD_CLIENT_ID", "")
        secret = get_option(opt_secret)
        secret = secret if secret is not None else os.environ.get("DRYAD_CLIENT_SECRET", "")
        url = get_option(opt_url)
        url = url if url is not None else token_url
        if not cid or not secret:
            return None
        return DryadOAuthClient(id=str(cid), secret=str(secret), token_url=str(url))
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise ValueError(
            "Set dryad_auth with a single client_id string and a single client_secret string"
        )
    options({opt_id: client_id, opt_secret: client_secret, opt_url: token_url})
    return DryadOAuthClient(id=client_id, secret=client_secret, token_url=token_url)


def _dryad_oauth_client() -> DryadOAuthClient | None:
    """Port of R/archive-dryad.R::.dryad_oauth_client(): the current client, re-read each time."""
    return _dryad_auth()


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------

_FILE_COLUMNS = (
    "folder",
    "dryad_doi",
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


def dryad_file_download(
    dryad_doi: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-dryad.R::dryad_file_download(): download a dataset's files.

    *dryad_doi* is a DOI or URL containing one, or a sequence of them. Creates
    a folder named from the DOI under *download_to* (``_1``, ``_2``... when it
    exists) and downloads each file of the latest version into it (a token,
    :func:`dryad_pat` or :func:`dryad_auth`, is required). Files over
    *max_file_size* MB are skipped, then the largest files until the total is
    under *max_download_size* MB (``None``/``inf``: no limit). Zips are
    downloaded whole unless *unzip_types* names file categories to extract
    from them. Returns the file table (``None`` when there is nothing to
    download); files that did not arrive intact have ``downloaded = False``
    and are warned about.
    """
    from pytacheck.archives import _spinner, _tick

    found = _dryad_doi(dryad_doi)
    dois = list(
        dict.fromkeys(d for d in (found if isinstance(found, list) else [found]) if d is not None)
    )
    if not dois:
        return None

    with _spinner(pb, "Dryad File Download") as bar:
        if len(dois) > 1:
            n = len(dois)
            return _download_many(
                dois,
                lambda x: dryad_file_download(
                    x,
                    download_to=download_to,
                    max_file_size=max_file_size,
                    max_download_size=max_download_size,
                    unzip_types=unzip_types,
                    pb=bar,
                ),
                lambda x: x,
                bar,
                f"Starting downloads for {n} Dryad dataset{'' if n == 1 else 's'}...\n",
                f"...Completed downloads for {n} Dryad dataset{'' if n == 1 else 's'}",
            )

        doi = dois[0]
        _tick(bar, f"Starting retrieval for {doi}")
        contents = _dryad_info(doi, pb=bar)
        files_list: Any = []
        if "files" in contents.columns and len(contents) > 0:
            files_list = contents["files"].iloc[0]
        if files_list is None or len(files_list) == 0:
            _tick(bar, f"- {doi} contained no files")
            return None

        rows = []
        for x in _elements(files_list):
            self_href = _empty_or(_dollars(x, "_links", "self", "href"), None)
            dl_href = _empty_or(_dollars(x, "_links", "stash:download", "href"), None)
            digest_type = _empty_or(_dollar(x, "digestType"), None)
            rows.append(
                {
                    "id": (
                        sub(r"^.*/([0-9]+)$", r"\1", _paste(self_href))
                        if self_href is not None
                        else None
                    ),
                    "key": _empty_or(_dollar(x, "path"), None),
                    "size": _as_numeric(_empty_or(_dollar(x, "size"), None)),
                    "checksum": _empty_or(_dollar(x, "digest"), None),
                    "checksum_type": None if digest_type is None else _paste(digest_type).lower(),
                    "self": (
                        f"https://datadryad.org{_paste(dl_href)}" if dl_href is not None else None
                    ),
                }
            )
        files = _file_frame(rows, typed=True)
        if len(files) == 0:
            from pytacheck.archives import _message

            _message("- ", doi, " contained no files")
            return None

        done = _download_file_table(
            files,
            ident=doi,
            folder_name=gsub("[^A-Za-z0-9._-]+", "_", doi),
            download_to=download_to,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            unzip_types=unzip_types,
            pb=bar,
            headers=_dryad_headers,
            reauth=_dryad_reauth,
            zip_members=lambda *a, **k: _dryad_zip_members(*a, **k),
            verify=lambda f, to: _dryad_verify_downloads(f, to),
            what="Dryad dataset",
        )
        if done is None:
            return None
        files, folder = done
        return _finish_file_table(files, folder, {"dryad_doi": doi}, _FILE_COLUMNS)


def _dryad_zip_members(
    url: str,
    dest: str,
    keep_types: Any = ("data", "documentation"),
    max_file_size: float | None = 10,
) -> pd.DataFrame | None:
    """Port of R/archive-dryad.R::.dryad_zip_members(): extract wanted zip members.

    Reads the zip's central directory with byte-range requests and fetches
    only members of the *keep_types* categories no larger than
    *max_file_size* MB into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), or ``None`` when the archive cannot be listed.
    """
    return _zip_members(url, dest, keep_types, max_file_size)


def _dryad_verify_downloads(files: pd.DataFrame | None, download_to: str) -> Any:
    """Port of R/archive-dryad.R::.dryad_verify_downloads(): check files on disk.

    ``downloaded`` becomes whether each file is on disk under *download_to*
    with the reported size and (for an MD5 ``checksum_type``) digest; adds
    ``size_on_disk`` and ``checksum_ok``. Rows extracted from a zip count as
    downloaded.
    """
    return _verify_file_table(files, download_to, typed=True)
