"""Zenodo records (port of ``R/archive-zenodo.R``).

:func:`zenodo_links` finds Zenodo links and bare Zenodo DOIs in papers,
:func:`zenodo_info` looks records up in Zenodo's API, and
:func:`zenodo_file_download` downloads a record's files and checks them
against the sizes and MD5 checksums Zenodo publishes. Uploading lives in
:mod:`pytacheck.archives.zenodo_upload`.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import shutil
import tempfile
import warnings
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import is_na

if TYPE_CHECKING:
    import httpx

__all__ = ["zenodo_file_download", "zenodo_info", "zenodo_links"]

_ZEN_BARE_REGEX = (
    r"(?:https?://)?zenodo\.org/(?:record|records)/[0-9]+"
    r"|(?:https?://)?(?:doi\.org/)?10\.5281/zenodo\.[0-9]+"
)
_ID_PATTERNS = (
    r"10\.5281/zenodo\.([0-9]+)",
    r"zenodo\.org/(?:records?|uploads)/([0-9]+)",
    r"zenodo\.([0-9]+)",
)
_MB = 1024 * 1024


# ---------------------------------------------------------------------------
# links and IDs
# ---------------------------------------------------------------------------


def zenodo_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-zenodo.R::zenodo_links(): Zenodo links in papers.

    Hyperlinks mentioning ``zenodo.org`` or ``10.5281/zenodo`` from the
    paper's ``url`` table, plus bare record URLs and Zenodo DOIs in the text
    (which are often not hyperlinked). Trailing slashes are dropped and
    duplicates removed; ``zenodo_url``, ``zenodo_id`` and ``zenodo_link``
    (the record's DOI URL) are added.
    """
    from pytacheck._r import bind_rows, grepl, sub
    from pytacheck.papers.tables import paper_table
    from pytacheck.text.search import text_search

    urls = paper_table(paper, "url")
    keep = grepl(r"zenodo\.org|10\.5281/zenodo", urls["href"].tolist(), ignore_case=True)
    found_href = urls.loc[pd.Series([bool(k) for k in keep], index=urls.index, dtype=bool)]

    other = text_search(paper, _ZEN_BARE_REGEX, return_="match", perl=True)
    other = other[["text", *[c for c in ("text_id", "paper_id") if c in other.columns]]]
    other = other.rename(columns={"text": "href"})

    links = bind_rows([found_href, other])
    links["href"] = pd.Series(sub("/+$", "", links["href"].tolist()), dtype="string")
    links = links.drop_duplicates().reset_index(drop=True)

    ids = _zenodo_id(links["href"].tolist())
    links["zenodo_url"] = links["href"]
    links["zenodo_id"] = pd.Series(ids, dtype="string")
    links["zenodo_link"] = pd.Series(
        [None if i is None else f"https://doi.org/10.5281/zenodo.{i}" for i in ids],
        dtype="string",
    )
    return links


def _zenodo_id_one(zenodo_url: Any) -> str | None:
    from pytacheck._r import as_character, grepl, regexec, trimws

    if is_na(zenodo_url):
        return None
    text = trimws(zenodo_url if isinstance(zenodo_url, str) else as_character(zenodo_url))
    if text is None or text == "":
        return None
    if grepl("^[0-9]+$", text):
        return str(text)
    for pattern in _ID_PATTERNS:
        groups = regexec(pattern, text, perl=True, ignore_case=True)
        if len(groups) >= 2:
            return str(groups[1])
    return None


def _zenodo_id(zenodo_url: Any) -> Any:
    """Port of R/archive-zenodo.R::.zenodo_id(): Zenodo record IDs from URLs/DOIs/IDs.

    A digits-only value is an ID; otherwise the ID is taken from a
    ``10.5281/zenodo.N`` DOI, a ``zenodo.org/record(s)|uploads/N`` URL or a
    ``zenodo.N`` fragment. ``None`` where there is none. A sequence gives a
    list (``None``, R's ``NULL``, gives an empty one); a single value, a string.
    """
    from pytacheck.archives.github import _as_list, _is_vector

    if zenodo_url is None:
        return []
    if _is_vector(zenodo_url):
        return [_zenodo_id_one(u) for u in _as_list(zenodo_url)]
    return _zenodo_id_one(zenodo_url)


# ---------------------------------------------------------------------------
# record information
# ---------------------------------------------------------------------------


def _column_name(table: pd.DataFrame, id_col: int | str) -> str:
    """R ``table[[id_col]]``: a 1-based position or a name."""
    if isinstance(id_col, int | float) and not isinstance(id_col, bool):
        pos = int(id_col)
        ncol = len(table.columns)
        if pos < 0 and ncol == 2 and -pos <= 2:
            # `[[-1]]` on a two-column table drops one and returns the other
            return str(table.columns[1 if pos == -1 else 0])
        if pos < 0:
            raise IndexError("invalid negative subscript in get1index <real>")
        if pos == 0 or pos > ncol:
            raise IndexError("subscript out of bounds")
        return str(table.columns[pos - 1])
    if id_col not in table.columns:
        raise KeyError(f"Can't extract column that doesn't exist: {id_col}")
    return str(id_col)


def zenodo_info(
    zenodo_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-zenodo.R::zenodo_info(): look Zenodo records up.

    *zenodo_url* is a URL/DOI/ID, a sequence of them, or a table whose
    *id_col* (R-style 1-based position, or a name) holds them, e.g. from
    :func:`zenodo_links`. The result keeps the input rows and adds
    ``zenodo_id`` and the record's metadata (``title``, ``doi``,
    ``license``, ``files``...). With *cache*, records already looked up are
    read from the on-disk listing cache (see
    :func:`~pytacheck.archives.info_cache.repo_info_cache`).
    """
    from pytacheck import utils
    from pytacheck.archives import _spinner

    if not utils.online("zenodo.org"):
        raise RuntimeError("Zenodo.org seems to be offline")
    with _spinner(pb, "Zenodo Retrieve") as bar:
        return _zenodo_info_table(zenodo_url, id_col, bar, cache)


def _record_url(zenodo_id: str) -> str:
    return f"https://zenodo.org/api/records/{zenodo_id}"


def _zenodo_info_table(zenodo_url: Any, id_col: int | str, pb: Any, cache: bool) -> pd.DataFrame:
    from pytacheck import http
    from pytacheck._r import as_character, bind_rows
    from pytacheck.archives import _tick
    from pytacheck.archives.github import _as_list, _check_combine
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from pytacheck.utils import left_join

    if isinstance(zenodo_url, pd.DataFrame):
        table = zenodo_url.copy()
        table["zenodo_url"] = table[_column_name(zenodo_url, id_col)]
        # a zenodo_links() table already has a zenodo_id: the recomputed one
        # below is joined on instead of producing zenodo_id.x/.y columns
        if "zenodo_id" in table.columns:
            table = table.drop(columns="zenodo_id")
    else:
        raw = [as_character(v) for v in _as_list(zenodo_url)]
        raw_urls = [u for u in dict.fromkeys(raw) if u is not None]
        table = pd.DataFrame({"zenodo_url": pd.Series(raw_urls, dtype="string")})

    urls = table["zenodo_url"].tolist()
    ids = pd.DataFrame(
        {
            "zenodo_url": table["zenodo_url"].reset_index(drop=True),
            "zenodo_id": pd.Series(_zenodo_id(urls), dtype="string"),
        }
    )
    ids = ids.drop_duplicates().reset_index(drop=True)
    ids = ids[ids["zenodo_url"].notna().to_numpy()].reset_index(drop=True)
    valid_ids = list(dict.fromkeys(i for i in ids["zenodo_id"].tolist() if not is_na(i)))

    if not valid_ids:
        _tick(pb, "No valid Zenodo links")
        return left_join(table, ids, by="zenodo_url")

    n = len(valid_ids)
    _tick(pb, f"Starting Zenodo retrieval for {n} file{'' if n == 1 else 's'}...")
    cached_info = {
        zid: _repo_info_cache_get("zenodo", zid) if cache is True else None for zid in valid_ids
    }
    # metacheck fetches one record at a time; the uncached records are fetched
    # here in one polite batch instead (same requests, same results)
    todo = [zid for zid in valid_ids if cached_info[zid] is None]
    fetched = dict(
        zip(todo, http.batch_query([_record_url(z) for z in todo], msg=None), strict=True)
    )
    id_info: list[pd.DataFrame] = []
    for zid in valid_ids:
        cached = cached_info[zid]
        if cached is not None:
            id_info.append(cached)
            continue
        info = _zenodo_info(zid, pb=pb, resp=fetched[zid])
        if cache is True and _repo_info_ok(info):
            _repo_info_cache_put("zenodo", zid, info)
        id_info.append(info)

    # bind_rows() refuses records whose fields have incompatible types
    _check_combine(id_info)
    info = bind_rows(id_info)
    data = left_join(table, ids, by="zenodo_url")
    data = left_join(data, info, by="zenodo_id", suffix=("", ".zenodo"))
    _tick(pb, "...Zenodo retrieval complete!")
    return data


def _is_character(col: pd.Series) -> bool:
    """Would R hold this column as a character vector?"""
    if pd.api.types.is_string_dtype(col.dtype) and not pd.api.types.is_object_dtype(col.dtype):
        return True
    values = [v for v in col.tolist() if not is_na(v)]
    return bool(values) and all(isinstance(v, str) for v in values)


_INFO_COLUMNS = (
    "zenodo_id",
    "title",
    "doi",
    "description",
    "publication_date",
    "updated_date",
    "creators",
    "keywords",
    "resource_type",
    "journal",
    "owners",
    "license",
    "downloads",
    "unique_downloads",
    "views",
    "files",
)
_LIST_COLUMNS = frozenset({"creators", "keywords", "journal", "owners", "files"})
_NUM_COLUMNS = frozenset({"downloads", "unique_downloads", "views"})


class _ListCell:
    """A value R stores as a list column (``obj$x <- <a length-1 list>``)."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


def _scalar_field(value: Any) -> Any:
    """``obj$x <- value %empty_or% NA`` on metacheck's one-row data frame.

    ``NULL`` or a zero-length value gives ``NA``. A JSON object or array of
    length one makes the column a list column holding its element (R's
    ``$<-.data.frame``); a longer one is R's "replacement has n rows" error.
    """
    if value is None or (isinstance(value, list | dict) and len(value) == 0):
        return None
    if isinstance(value, list | dict):
        if len(value) > 1:
            raise ValueError(f"replacement has {len(value)} rows, data has 1")
        return _ListCell(next(iter(value.values())) if isinstance(value, dict) else value[0])
    return value


def _info_frame(row: dict[str, Any]) -> pd.DataFrame:
    """A one-row record table with metacheck's column types.

    List columns (``creators``, ``files``...) hold the value as it is; the
    other fields are typed as R types them (character; integer, double or
    logical for JSON numbers and booleans; a list column for a
    :class:`_ListCell`). Missing statistics are double ``NA``, other missing
    fields character ``NA``.
    """
    from pytacheck.archives.github import _TYPE_DTYPE, _r_value_type

    cols: dict[str, pd.Series] = {}
    for name, value in row.items():
        if name in _LIST_COLUMNS:
            cols[name] = pd.Series([value], dtype=object)
        elif isinstance(value, _ListCell):
            cols[name] = pd.Series([value.value], dtype=object)
        elif value is None:
            cols[name] = pd.Series([None], dtype="float64" if name in _NUM_COLUMNS else "string")
        else:
            cols[name] = pd.Series([value], dtype=_TYPE_DTYPE[_r_value_type(value)])
    return pd.DataFrame(cols)


def _as_double(x: Any) -> float | None:
    if x is None or isinstance(x, list | dict):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _zenodo_unread(zenodo_id: Any, error: str) -> pd.DataFrame:
    """Port of R/archive-zenodo.R::.zenodo_unread(): the row for an unreadable record.

    The same columns as a record that was read (list columns hold ``None``),
    plus ``error``, so callers can rely on them whatever the API answered.
    """
    from pytacheck._r import as_character

    row: dict[str, Any] = dict.fromkeys(_INFO_COLUMNS)
    row["zenodo_id"] = None if is_na(zenodo_id) else as_character(zenodo_id)
    row["error"] = error
    return _info_frame(row)


_UNSET: Any = object()


def _zenodo_info(zenodo_id: Any, pb: Any = None, resp: Any = _UNSET) -> pd.DataFrame:
    """Port of R/archive-zenodo.R::.zenodo_info(): one record's information.

    A one-row table: ``zenodo_id``, ``title``, ``doi``, ``description``,
    ``publication_date``, ``updated_date``, ``creators``, ``keywords``,
    ``resource_type``, ``journal``, ``owners``, ``license``, ``downloads``,
    ``unique_downloads``, ``views`` and ``files`` (the record's file list).
    A record that is not found (warns) or cannot be parsed gives the same
    columns empty, with ``error`` set to ``"unfound"`` / ``"parse_error"``.
    *resp* is the record's API response when it was already fetched
    (:func:`zenodo_info` fetches all of its records in one batch).

    As in R, several IDs (which :func:`zenodo_info` never passes) give one
    row per ID, every field taken from the first ID's record; when that
    record cannot be read, or for no ID at all, R's ``data.frame()`` error.
    """
    from pytacheck import http
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.github import _body_json, _empty_or
    from pytacheck.archives.github import _dollar as r_dollar

    with _spinner(pb) as bar:
        zid = _zenodo_id(zenodo_id)
        ids = zid if isinstance(zid, list) else [zid]
        zid = ids[0] if ids else None
        shown_ids = ["NA" if i is None else i for i in ids]
        shown = shown_ids[0] if shown_ids else ""  # R: paste0() drops a character(0)
        _tick(bar, f"* Retrieving info from Zenodo ID {shown}...")

        def unread(error: str) -> pd.DataFrame:
            if len(ids) != 1:  # R: data.frame(zenodo_id = <n ids>, x = I(list(NULL)))
                raise ValueError(f"arguments imply differing number of rows: {len(ids)}, 1")
            return _zenodo_unread(zid, error)

        if resp is _UNSET:
            resp = http.batch_query([_record_url(shown)], msg=None)[0]
        if resp is None:
            raise TypeError("`resp` must be an HTTP response object, not `NULL`.")
        if resp.status_code != 200:
            warnings.warn(f"{''.join(shown_ids)} could not be found", stacklevel=2)
            return unread("unfound")
        try:
            rec = _body_json(resp)
        except Exception:
            rec = None
        if rec is None:
            return unread("parse_error")
        if not ids:  # R: obj$title <- ... on data.frame(zenodo_id = character(0))
            raise ValueError("replacement has 1 row, data has 0")

        metadata = r_dollar(rec, "metadata")
        lic = r_dollar(metadata, "license")
        if lic is not None and not isinstance(lic, dict | list):
            # older records give the licence as a plain string ("cc-by"), which
            # metacheck's `metadata$license$id` cannot read (U34)
            license_value = lic
        else:
            license_value = _empty_or(
                r_dollar(lic, "id"), _empty_or(r_dollar(lic, "title"), _empty_or(lic))
            )
        stats = r_dollar(rec, "stats")
        row = {
            "zenodo_id": zid,
            "title": _scalar_field(r_dollar(metadata, "title")),
            "doi": _scalar_field(r_dollar(rec, "doi")),
            "description": _scalar_field(r_dollar(metadata, "description")),
            "publication_date": _scalar_field(r_dollar(metadata, "publication_date")),
            "updated_date": _scalar_field(r_dollar(rec, "updated")),
            "creators": r_dollar(metadata, "creators"),
            "keywords": r_dollar(metadata, "keywords"),
            "resource_type": _scalar_field(r_dollar(r_dollar(metadata, "resource_type"), "type")),
            "journal": r_dollar(metadata, "journal"),
            "owners": r_dollar(rec, "owners"),
            "license": _scalar_field(license_value),
            "downloads": _scalar_field(r_dollar(stats, "downloads")),
            "unique_downloads": _scalar_field(r_dollar(stats, "unique_downloads")),
            "views": _scalar_field(r_dollar(stats, "views")),
            "files": r_dollar(rec, "files"),
        }
        out = _info_frame(row)
        if len(ids) > 1:  # R recycles the one record's fields over the IDs
            out = out.iloc[[0] * len(ids)].reset_index(drop=True)
            out["zenodo_id"] = pd.Series(ids, dtype="string")
        return out


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------


def _r_num_str(x: float) -> str:
    """``paste0()`` of ``round(x, 1)``."""
    from pytacheck._r import as_character, r_round

    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "NA"
    return as_character(float(r_round(x, 1))) or "NA"


def _is_zip(name: list[Any]) -> list[bool]:
    """``.is_zip()`` from R/zip-peek.R: names ending in ``.zip``."""
    try:
        from pytacheck.archives.zip_peek import _is_zip as is_zip  # type: ignore[import-not-found]
    except ImportError:
        from pytacheck._r import grepl

        return [bool(v) for v in grepl("[.]zip$", name, ignore_case=True)]
    return [bool(v) for v in is_zip(name)]


def _file_rows(files_list: list[Any]) -> pd.DataFrame:
    """The flat ``id``/``key``/``size``/``checksum``/``self`` table of a record's files."""
    from pytacheck.archives.github import _dollar as r_dollar

    ids, keys, sizes, checksums, selfs = [], [], [], [], []
    for x in files_list:
        if not isinstance(x, dict | list):
            raise TypeError("$ operator is invalid for atomic vectors")
        ids.append(r_dollar(x, "id"))
        keys.append(r_dollar(x, "key"))
        sizes.append(_as_double(r_dollar(x, "size")))
        checksums.append(r_dollar(x, "checksum"))
        selfs.append(r_dollar(r_dollar(x, "links"), "self"))

    def chr_col(values: list[Any]) -> pd.Series:
        return pd.Series([None if is_na(v) else str(v) for v in values], dtype="string")

    return pd.DataFrame(
        {
            "id": chr_col(ids),
            "key": chr_col(keys),
            "size": pd.Series(sizes, dtype="float64"),
            "checksum": chr_col(checksums),
            "self": chr_col(selfs),
        }
    )


def zenodo_file_download(
    zenodo_id: Any,
    download_to: str = ".",
    max_file_size: float | None = 10,
    max_download_size: float | None = 100,
    unzip_types: Any = None,
    pb: Any = None,
) -> pd.DataFrame | None:
    """Port of R/archive-zenodo.R::zenodo_file_download(): download a record's files.

    Files go into ``<download_to>/<zenodo_id>`` (``_1``, ``_2``... when that
    exists). Files over *max_file_size* MB are skipped, and the largest are
    dropped until the total is under *max_download_size* MB (``None`` or
    ``inf`` for no limit). When every file survives, Zenodo's whole-record
    archive is tried first. With *unzip_types* (categories as
    :func:`~pytacheck.datacheck.files.data_classify_files` names them), only
    matching members are read out of the record's ``.zip`` files instead of
    downloading them whole. Every file is then checked on disk against the
    size and MD5 Zenodo reports (``downloaded``, ``size_on_disk``,
    ``checksum_ok``); files that did not arrive intact warn.

    Returns a table of ``folder``, ``zenodo_id``, ``id``, ``key``, ``path``,
    ``size``, ``size_on_disk``, ``checksum``, ``checksum_ok``, ``self``,
    ``downloaded`` and ``extracted``, or ``None`` when nothing was downloaded.
    Several IDs are downloaded one after another and row-bound.
    """
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.github import _as_list

    ids = _zenodo_id(zenodo_id)
    ids = [i for i in dict.fromkeys(_as_list(ids)) if i is not None]
    if not ids:
        return None

    with _spinner(pb, "Zenodo File Download") as bar:
        if len(ids) > 1:
            from pytacheck._r import bind_rows

            _tick(bar, f"Starting downloads for {len(ids)} Zenodo records...\n")
            dl_list: list[pd.DataFrame] = []
            for x in ids:
                try:
                    dl = zenodo_file_download(
                        x,
                        download_to=download_to,
                        max_file_size=max_file_size,
                        max_download_size=max_download_size,
                        unzip_types=unzip_types,
                        pb=bar,
                    )
                except Exception as e:
                    warnings.warn(f"{x} resulted in an error:\n  {e}\n", stacklevel=2)
                    dl = None
                if dl is not None:
                    dl_list.append(dl)
            if not dl_list:
                return None
            out = bind_rows(dl_list)
            _tick(bar, f"...Completed downloads for {len(ids)} Zenodo records")
            return out
        return _zenodo_download_one(
            ids[0], download_to, max_file_size, max_download_size, unzip_types, bar
        )


def _limit(x: float | None) -> bool:
    """R ``!is.null(x) && is.finite(x) && x > 0``."""
    return x is not None and not is_na(x) and math.isfinite(x) and x > 0


def _zenodo_download_one(
    zid: str,
    download_to: str,
    max_file_size: float | None,
    max_download_size: float | None,
    unzip_types: Any,
    pb: Any,
) -> pd.DataFrame | None:
    from pytacheck._r import plural
    from pytacheck.archives import _message, _tick
    from pytacheck.archives.osf import _normalize, _r_basename, _r_dirname

    _tick(pb, f"Starting retrieval for {zid}")
    contents = zenodo_info(zid, pb=pb)

    files_list: Any = []
    if "files" in contents.columns and len(contents) > 0:
        files_list = contents["files"].iloc[0]
    if isinstance(files_list, dict):
        files_list = list(files_list.values())  # R: lapply() over a named list's fields
    if files_list is None or not isinstance(files_list, list | tuple) or len(files_list) == 0:
        _tick(pb, f"- {zid} contained no files")
        return None
    files_list = list(files_list)

    files = _file_rows(files_list)
    if len(files) == 0:
        _message("- ", zid, " contained no files")
        return None

    keys = files["key"].tolist()
    selfs = files["self"].tolist()
    unzippable = [False] * len(files)
    if unzip_types is not None and len(_unzip_list(unzip_types)) > 0:
        zips = _is_zip(keys)
        unzippable = [
            z and s is not None and not is_na(s) and s != ""
            for z, s in zip(zips, selfs, strict=True)
        ]

    n = len(files)
    # files omitted by the size caps stay in the table with downloaded = FALSE,
    # as metacheck documents (its code drops them: U36)
    omitted = [False] * n

    def omitting(i: int) -> None:
        key = files["key"].iloc[i]
        size = files["size"].iloc[i]
        _tick(pb, f"- omitting {'NA' if is_na(key) else key} ({_r_num_str(size / _MB)}MB)")
        omitted[i] = True

    # --- size filters (MB) ----
    if _limit(max_file_size):
        assert max_file_size is not None
        for i, (s, u) in enumerate(zip(files["size"].tolist(), unzippable, strict=True)):
            if not is_na(s) and s > max_file_size * _MB and not u:
                omitting(i)

    # remove the largest files until the total fits (only whole transfers count)
    if _limit(max_download_size):
        assert max_download_size is not None
        size_list = files["size"].tolist()
        while True:
            capped = [i for i, u in enumerate(unzippable) if not u and not omitted[i]]
            if not capped:
                break
            total = sum(size_list[i] for i in capped if not is_na(size_list[i]))
            if not total > max_download_size * _MB:
                break
            present = [i for i in capped if not is_na(size_list[i])]
            max_file = max(present, key=lambda i: (size_list[i], -i))
            omitting(max_file)

    if all(omitted):
        _tick(pb, "- All files omitted due to size constraints")
        target = None
    else:
        # --- target directory (never overwrite; an existing <dir> gives
        # <dir>_1, <dir>_2...: metacheck strips a trailing _<digits> from the
        # name itself, U37) ----
        target = _normalize(download_to)
        if os.path.isdir(target):
            target = os.path.join(target, str(zid))
        base = target
        i = 0
        while os.path.isdir(target):
            i += 1
            target = f"{base}_{i}"
        with contextlib.suppress(OSError):
            os.mkdir(target)
        _tick(pb, f"- Created directory {target}")

    downloaded = [False] * n
    extracted: list[int | None] = [None] * n
    ids = files["id"].tolist()
    keys = files["key"].tolist()
    selfs = files["self"].tolist()

    paths: list[str | None] = [None] * n
    with tempfile.TemporaryDirectory() as temppath:
        # --- the whole-record archive, when no file was filtered out ---
        used_bulk = target is None  # every file was omitted: nothing to fetch
        if n == len(files_list) and not any(unzippable) and not any(omitted):
            zip_url = f"https://zenodo.org/api/records/{zid}/files-archive"
            zip_path = os.path.join(temppath, "archive.zip")
            dl_ok = _download_archive(zip_url, zip_path)
            if dl_ok:
                used_bulk = _extract_archive(zip_path, temppath, ids, keys, downloaded)
            with contextlib.suppress(OSError):
                os.remove(zip_path)
            _tick(pb, "Downloaded as one archive")

        # --- file by file (bulk skipped or incomplete) ----
        if not used_bulk:
            n_wanted = n - sum(omitted)
            k = 0
            for i in range(n):
                if downloaded[i] or omitted[i]:
                    continue
                k += 1
                if unzippable[i]:
                    _tick(pb, f"Reading zip contents of {_na(keys[i])}")
                    try:
                        got = _zenodo_zip_members(
                            selfs[i],
                            dest=target,
                            keep_types=unzip_types,
                            max_file_size=max_file_size,
                        )
                    except Exception:
                        got = None
                    if got is not None:
                        n_ok = 0
                        if isinstance(got, pd.DataFrame) and "ok" in got.columns:
                            n_ok = sum(1 for v in got["ok"].tolist() if v is True or v == 1)
                        extracted[i] = n_ok
                        downloaded[i] = True
                        _tick(
                            pb,
                            f"- extracted {n_ok} file{plural(n_ok)} from {_na(keys[i])}",
                        )
                        continue
                    _tick(
                        pb,
                        f"- could not read {_na(keys[i])} without downloading it; "
                        "fetching the whole archive",
                    )
                ok = False
                if selfs[i] is not None and not is_na(selfs[i]) and selfs[i] != "":
                    ok = _download_file(selfs[i], os.path.join(temppath, _na(ids[i])))
                downloaded[i] = ok
                _tick(pb, f"Downloading file {k} of {n_wanted}")

        # copy into the target folder under the original file names
        for i in range(n):
            if extracted[i] is not None:
                continue  # members were written straight into the target folder
            if downloaded[i]:
                src = os.path.join(temppath, _na(ids[i]))
                key = keys[i]
                fname = key if key is not None and not is_na(key) and key != "" else _na(ids[i])
                dest = os.path.join(target, fname)
                with contextlib.suppress(OSError):
                    os.makedirs(_r_dirname(dest), exist_ok=True)
                with contextlib.suppress(OSError):
                    shutil.copyfile(src, dest)
                paths[i] = fname

    files = files.assign(
        downloaded=pd.Series(downloaded, dtype="boolean"),
        extracted=pd.Series(extracted, dtype="Int64"),
        path=pd.Series(paths, dtype="string"),
    )

    # --- check what actually reached the disk ----
    if target is not None:
        files = _zenodo_verify_downloads(files, target)
    else:
        files = files.assign(
            size_on_disk=pd.Series([math.nan] * len(files), dtype="float64"),
            checksum_ok=pd.Series([None] * len(files), dtype="boolean"),
        )

    dl = files["downloaded"].tolist()
    missing = [v is not True and not o for v, o in zip(dl, omitted, strict=True)]
    n_missing = sum(missing)
    if n_missing > 0:
        worst = [_na(k) for k, m in zip(files["key"].tolist(), missing, strict=True) if m]
        nrow = n - sum(omitted)
        warnings.warn(
            f"{n_missing} of {nrow} file{plural(nrow)} from Zenodo record {zid} did not "
            f"arrive intact (e.g. {', '.join(worst[:3])}). The returned table marks "
            f"{'it' if n_missing == 1 else 'them'} downloaded = FALSE. Run again to retry.",
            stacklevel=3,
        )

    folder = None if target is None else _r_basename(target)
    files["folder"] = pd.Series([folder] * len(files), dtype="string")
    files["zenodo_id"] = pd.Series([str(zid)] * len(files), dtype="string")
    return files[
        [
            "folder",
            "zenodo_id",
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
        ]
    ]


def _na(x: Any) -> str:
    """``paste0()`` of a possibly missing string."""
    return "NA" if x is None or is_na(x) else str(x)


def _unzip_list(x: Any) -> list[Any]:
    from pytacheck.archives.github import _as_list

    if x is False:
        return [False]
    return _as_list(x)


def _stream_to(resp_ctx: Any, path: str) -> httpx.Response:
    with resp_ctx as resp:
        if resp.status_code == 200:
            with open(path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        return resp  # type: ignore[no-any-return]


def _download_archive(url: str, path: str) -> bool:
    """``req_perform(path = ...)`` of Zenodo's files-archive (3 tries, 600 s timeout)."""
    from pytacheck import http

    for attempt in range(1, 4):
        try:
            resp = _stream_to(http.client().stream("GET", url, timeout=600), path)
        except Exception:
            if attempt == 3:
                return False
            http.sleep(min(2**attempt, 30))
            continue
        if resp.status_code in (429, 503) and attempt < 3:
            http.sleep(min(2**attempt, 30))
            continue
        return resp.status_code == 200 and os.path.exists(path) and os.path.getsize(path) > 0
    return False


def _extract_archive(
    zip_path: str, temppath: str, ids: list[Any], keys: list[Any], downloaded: list[bool]
) -> bool:
    """Unpack the whole-record archive and stage each wanted file under its id."""
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as zf:
            entries = zf.infolist()
            if not entries:
                return False
            extract_dir = os.path.join(temppath, "extracted")
            os.mkdir(extract_dir)
            with contextlib.suppress(Exception):
                zf.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError):
        return False
    for i, key in enumerate(keys):
        src = os.path.join(extract_dir, _na(key))
        if os.path.isfile(src) and os.path.getsize(src) > 0:
            shutil.copyfile(src, os.path.join(temppath, _na(ids[i])))
            downloaded[i] = True
    return all(downloaded)


def _download_file(url: str, path: str) -> bool:
    """One file (600 s timeout, one try); ``True`` when it came back 200 and was written."""
    from pytacheck import http

    try:
        resp = _stream_to(http.client().stream("GET", url, timeout=600), path)
    except Exception:
        return False
    return resp.status_code == 200


def _zenodo_zip_members(
    url: str,
    dest: str,
    keep_types: Any = ("data", "documentation"),
    max_file_size: float | None = 10,
) -> pd.DataFrame | None:
    """Port of R/archive-zenodo.R::.zenodo_zip_members(): pull chosen members out of a zip.

    Reads the zip's central directory with byte-range requests, keeps the
    members whose category (as
    :func:`~pytacheck.datacheck.files.data_classify_files` names it) is in
    *keep_types* and whose size is known and at most *max_file_size* MB, and
    fetches only those into *dest*. Returns one row per member (``name``,
    ``path``, ``size``, ``ok``), a zero-row table when nothing is wanted, or
    ``None`` when the archive cannot be listed (the caller then downloads it
    whole).
    """
    from pytacheck.archives.github import _as_list, _r_basename
    from pytacheck.archives.zip_peek import (  # type: ignore[import-not-found]
        _zip_fetch_members,
        zip_peek,
    )
    from pytacheck.datacheck.files import data_classify_files

    try:
        listing = zip_peek(url)
    except Exception:
        listing = None
    if listing is None or len(listing) == 0:
        return None

    names = listing["name"].tolist()
    types = data_classify_files([_r_basename(str(nm)) for nm in names])
    wanted = set(_as_list(keep_types))
    keep = [t in wanted for t in types]
    if _limit(max_file_size):
        assert max_file_size is not None
        sizes = listing["size"].tolist()
        keep = [
            k and not is_na(s) and s <= max_file_size * _MB
            for k, s in zip(keep, sizes, strict=True)
        ]
    if not any(keep):
        return listing.iloc[0:0][["name", "size"]]
    return _zip_fetch_members(  # type: ignore[no-any-return]
        url, names=[nm for nm, k in zip(names, keep, strict=True) if k], dest=dest
    )


def _md5sum(path: str) -> str | None:
    """``tools::md5sum()`` of one file."""
    h = hashlib.md5(usedforsecurity=False)
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _zenodo_verify_downloads(files: pd.DataFrame | None, download_to: str) -> pd.DataFrame | None:
    """Port of R/archive-zenodo.R::.zenodo_verify_downloads(): check files on disk.

    A file counts as downloaded only if it is present at ``download_to/path``,
    has the size Zenodo reported, and (when Zenodo published an
    ``md5:<32 hex>`` checksum) matches it. Adds ``size_on_disk`` and
    ``checksum_ok``. Rows whose zip members were extracted (``extracted``
    set) keep ``downloaded = TRUE``: the members were CRC-checked instead.
    """
    from pytacheck._r import grepl, sub

    if files is None or len(files) == 0:
        return files
    files = files.copy()
    n = len(files)
    unzipped = (
        [not is_na(v) for v in files["extracted"].tolist()]
        if "extracted" in files.columns
        else [False] * n
    )
    size_on_disk: list[float | None] = [None] * n
    checksum_ok: list[bool | None] = [None] * n
    files["size_on_disk"] = pd.Series(size_on_disk, dtype="float64", index=files.index)
    files["checksum_ok"] = pd.Series(checksum_ok, dtype="boolean", index=files.index)
    if "path" not in files.columns:
        files["downloaded"] = pd.Series([False] * n, dtype="boolean", index=files.index)
        return files

    paths = files["path"].tolist()
    full = [None if is_na(p) else os.path.join(download_to, str(p)) for p in paths]
    on_disk = [f is not None and os.path.exists(f) and not os.path.isdir(f) for f in full]
    for i, f in enumerate(full):
        if on_disk[i] and f is not None:
            size_on_disk[i] = float(os.path.getsize(f))
    ok = [d and s is not None for d, s in zip(on_disk, size_on_disk, strict=True)]

    # size: a file present at the wrong length looks complete downstream
    expected = (
        [_as_double(v) if not is_na(v) else None for v in files["size"].tolist()]
        if "size" in files.columns
        else [None] * n
    )
    ok = [
        o and (e is None or (s is not None and s == e))
        for o, e, s in zip(ok, expected, size_on_disk, strict=True)
    ]

    # checksum, only for files that passed so far
    checksums = files["checksum"].tolist() if "checksum" in files.columns else [None] * n
    md5 = [None if is_na(c) else sub("^md5:", "", str(c)) for c in checksums]
    is_hex = grepl("^[0-9a-f]{32}$", md5, ignore_case=True)
    for i in range(n):
        if ok[i] and md5[i] is not None and is_hex[i]:
            got = _md5sum(full[i]) if full[i] is not None else None
            checksum_ok[i] = got is not None and got.lower() == str(md5[i]).lower()
    ok = [o and c is not False for o, c in zip(ok, checksum_ok, strict=True)]

    from pytacheck.archives.dataverse import _is_true

    if "downloaded" not in files.columns:
        # R: `files$downloaded <- ok & files$downloaded %in% TRUE` is a length-0 value
        raise ValueError(f"replacement has 0 rows, data has {n}")
    was = files["downloaded"].tolist()
    downloaded = [o and _is_true(w) for o, w in zip(ok, was, strict=True)]
    downloaded = [True if u else d for d, u in zip(downloaded, unzipped, strict=True)]

    files["size_on_disk"] = pd.Series(size_on_disk, dtype="float64", index=files.index)
    files["checksum_ok"] = pd.Series(checksum_ok, dtype="boolean", index=files.index)
    files["downloaded"] = pd.Series(downloaded, dtype="boolean", index=files.index)
    return files
