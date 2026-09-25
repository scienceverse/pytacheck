"""Helpers for the ``repo_check`` module (port of ``inst/modules/repo_check.R``).

The R module is one long function; this file holds its building blocks: R
``data.frame()`` / ``dplyr`` idioms the module relies on (recycling, dropped
``NULL`` columns, ``%in%`` with ``NA``, ``duplicated()``), the link collection,
and one listing block per repository platform. Each block mirrors its R
``tryCatch()``: a failure flags every URL of that platform with the error
message in ``repo_error`` instead of aborting the module.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r import as_character, grepl, gsub, is_na, sub

# ---------------------------------------------------------------------------
# R idioms
# ---------------------------------------------------------------------------


class RError(RuntimeError):
    """An R error whose ``conditionMessage()`` the module stores in ``repo_error``."""


def _na(x: Any) -> bool:
    return x is None or is_na(x)


def _chr(values: Iterable[Any]) -> pd.Series:
    """A character column (R ``as.character()`` of each value, ``NA`` kept)."""
    return pd.Series([None if _na(v) else _as_chr(v) for v in values], dtype="string")


def _dbl(values: Iterable[Any]) -> pd.Series:
    """A double column (R ``as.numeric()``; non-numbers become ``NA``)."""
    return pd.Series([_as_num(v) for v in values], dtype="float64")


def _as_chr(x: Any) -> str | None:
    """R ``as.character()`` of one parsed JSON scalar."""
    if _na(x):
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, list | tuple):
        # as.character(list(x)) of a one-element list is the element
        return _as_chr(x[0]) if len(x) == 1 else str(x)
    return as_character(x)


def _as_num(x: Any) -> float:
    """R ``as.numeric()`` of one value (``NA`` for anything unparsable)."""
    if _na(x):
        return math.nan
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, int | float):
        return float(x)
    if isinstance(x, list | tuple) and len(x) == 1:
        return _as_num(x[0])
    try:
        return float(str(x).strip())
    except ValueError:
        return math.nan


def vals(s: pd.Series | Iterable[Any]) -> list[Any]:
    """A column as a list, missing values as ``None``."""
    values = s.tolist() if isinstance(s, pd.Series) else list(s)
    return [None if _na(v) else v for v in values]


def take(df: pd.DataFrame, mask: Iterable[Any]) -> pd.DataFrame:
    """``df[mask, , drop = FALSE]`` for a logical row mask (index reset)."""
    keep = np.asarray([bool(m) for m in mask], dtype=bool)
    return df.iloc[np.flatnonzero(keep)].reset_index(drop=True)


def _col(df: pd.DataFrame, name: str) -> list[Any] | None:
    """``df$name`` as a list (``None`` when the column is absent, R's ``NULL``)."""
    if name not in df.columns:
        return None
    return [None if _na(v) else v for v in df[name].tolist()]


def r_frame(columns: Sequence[tuple[str, Any, str]], tibble: bool = False) -> pd.DataFrame:
    """R ``data.frame()`` / ``tibble()`` of ``(name, values, dtype)`` columns.

    ``None`` values (R ``NULL``) drop the column; a scalar (not a list) is a
    length-one vector. ``tibble()`` recycles length one to any size;
    ``data.frame()`` recycles a length that divides the longest one, and a
    zero-row table cannot take a length-one column (R's error).
    """
    kept = [(n, v, t) for n, v, t in columns if v is not None]

    def length(v: Any) -> int:
        return len(v) if isinstance(v, list | tuple | pd.Series) else 1

    lens = [length(v) for _, v, _ in kept]
    n = max(lens, default=0)
    if tibble:
        vec = [ln for ln in lens if ln != 1]
        n = vec[0] if vec else n
        if any(ln not in (1, n) for ln in lens):
            raise RError("Tibble columns must have compatible sizes.")
    elif any(ln != n and (ln == 0 or n % ln) for ln in lens):
        uniq = ", ".join(str(ln) for ln in dict.fromkeys(lens))
        raise RError(f"arguments imply differing number of rows: {uniq}")
    out: dict[str, pd.Series] = {}
    for name, values, dtype in kept:
        if isinstance(values, list | tuple | pd.Series):
            items = list(values)
        else:
            items = [None if isinstance(values, _NaScalar) else values]
        if items and len(items) != n:
            items = [items[i % len(items)] for i in range(n)]
        if dtype == "string":
            out[name] = _chr(items)
        elif dtype == "float64":
            out[name] = _dbl(items)
        else:
            out[name] = pd.Series(items, dtype=dtype)
    return pd.DataFrame(out)


def placeholder() -> pd.DataFrame:
    """R ``data.frame(repo_name = character(0))``, the empty listing of a platform."""
    return pd.DataFrame({"repo_name": pd.Series([], dtype="string")})


def meta_frame(repo_url: Any = (), doi: Any = (), license: Any = ()) -> pd.DataFrame:
    """A ``repo_url`` / ``doi`` / ``license`` metadata table (R ``data.frame()``)."""
    return r_frame(
        [
            ("repo_url", repo_url, "string"),
            ("doi", doi, "string"),
            ("license", license, "string"),
        ]
    )


def r_in(x: Iterable[Any], table: Iterable[Any]) -> list[bool]:
    """R ``x %in% table`` (``NA`` matches ``NA``)."""
    values = [None if _na(v) else v for v in table]
    has_na = any(v is None for v in values)
    lookup = {v for v in values if v is not None}
    return [has_na if _na(v) else v in lookup for v in x]


def unique(x: Iterable[Any]) -> list[Any]:
    """R ``unique()`` of a vector (``NA`` kept once, as ``None``)."""
    return list(dict.fromkeys(None if _na(v) else v for v in x))


def setdiff(x: Iterable[Any], y: Iterable[Any]) -> list[Any]:
    """R ``setdiff()``: unique elements of *x* not in *y*."""
    ux = unique(x)
    return [v for v, keep in zip(ux, r_in(ux, list(y)), strict=True) if not keep]


def duplicated(x: Sequence[Any]) -> list[bool]:
    """R ``duplicated()`` (``NA`` equals ``NA``)."""
    seen: set[Any] = set()
    out = []
    for v in x:
        key = ("\0na",) if _na(v) else v
        out.append(key in seen)
        seen.add(key)
    return out


def r_basename(x: Any) -> str | None:
    """R ``basename()`` of one path (trailing slashes dropped; ``NA`` kept)."""
    if _na(x):
        return None
    s = str(x).rstrip("/")
    return s.rsplit("/", 1)[-1]


def file_ext(x: Any) -> str:
    """R ``tools::file_ext()`` of one file name."""
    if _na(x):
        return ""
    s = str(x)
    if not grepl(r"\.[[:alnum:]]+$", s):
        return ""
    return str(sub(r"^.*\.([[:alnum:]]+)$", r"\1", s))


def file_path_sans_ext(x: Any) -> str | None:
    """R ``tools::file_path_sans_ext()`` of one file name."""
    if _na(x):
        return None
    return str(sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", str(x)))


def r_tolower(x: Any) -> str | None:
    return None if _na(x) else str(x).lower()


def empty_or(x: Any, y: Any) -> Any:
    """metacheck's ``x %empty_or% y``: *y* when *x* has length zero."""
    if x is None or (isinstance(x, list | tuple | dict) and len(x) == 0):
        return y
    return x


def dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on parsed JSON: exact or unique partial match; error on atomics."""
    if x is None or isinstance(x, list | tuple):
        return None
    if isinstance(x, Mapping):
        if name in x:
            return x[name]
        hits = [k for k in x if isinstance(k, str) and k.startswith(name)]
        return x[hits[0]] if len(hits) == 1 else None
    raise RError("$ operator is invalid for atomic vectors")


def url_encode_reserved(url: str) -> str:
    """R ``utils::URLencode(url, reserved = TRUE)`` (an already-encoded URL is kept)."""
    if grepl("%[[:xdigit:]]{2}", url):
        return url
    ok = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
    return "".join(
        ch if ch in ok else "".join(f"%{b:02X}" for b in ch.encode("utf-8")) for ch in url
    )


def condition_message(e: BaseException) -> str:
    """R ``conditionMessage(e)`` for an error raised by a ported function."""
    if isinstance(e, KeyError) and e.args:
        return str(e.args[0])
    return str(e)


def format_object_size(x: float) -> str:
    """``utils:::format.object_size(x, units = "auto", standard = "SI", digits = 1)``."""
    from pytacheck._r import r_round

    units = ("B", "kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB", "RB", "QB")
    power = 0 if x <= 0 else min(int(math.log(x) / math.log(1000)), len(units) - 1)
    value = float(r_round(x / 1000.0**power, 1))
    return f"{as_character(value)} {units[power]}"


def size_label(x: Any) -> str:
    """A file size for the report (``"—"`` when unknown)."""
    if _na(x):
        return "—"
    v = float(x)
    if not math.isfinite(v) or v < 0:
        return "—"
    return format_object_size(v)


# ---------------------------------------------------------------------------
# repositories table
# ---------------------------------------------------------------------------

REPO_TYPES = (
    "osf",
    "github",
    "gitlab",
    "researchbox",
    "dspace",
    "zenodo",
    "dataverse",
    "figshare",
    "dryad",
    "reshare",
    "researchdata4tu",
    "dspace7",
    "mendeley",
    "dataone",
)

_FIGSHARE_SHARE = "private share link (figshare.com/s/...) cannot be resolved without a browser"
#: a DSpace item its host does not know (or a link that names no item)
_DSPACE_UNFOUND = "invalid or inaccessible DSpace item"


@dataclass
class Repos:
    """The module's mutable ``repos`` table (R assigns to it with ``<<-``)."""

    df: pd.DataFrame
    warnings_: list[str] = field(default_factory=list)

    def urls(self, repo_type: str) -> list[str]:
        """``repos |> filter(repo_type == type) |> _$repo_url |> unique()``."""
        mask = (self.df["repo_type"] == repo_type).fillna(False).to_numpy(dtype=bool)
        return [u for u in unique(self.df["repo_url"][mask].tolist()) if u is not None]

    def flag(self, urls: Iterable[Any], message: str) -> None:
        """``repos$repo_error[repos$repo_url %in% urls] <- message``."""
        mask = r_in(vals(self.df["repo_url"]), list(urls))
        if any(mask):
            errors = self.df["repo_error"].copy()
            errors[np.asarray(mask, dtype=bool)] = message
            self.df = self.df.assign(repo_error=errors)

    def flag_unflagged(self, urls: Sequence[Any], message: str) -> None:
        """The error handler of the OSF / DSpace blocks: flag URLs without an error yet.

        R: ``urls[is.na(repos$repo_error[match(urls, repos$repo_url)])]`` --
        ``match()`` finds a URL's first row; a URL no longer in ``repos``
        counts as unflagged (and then flags nothing).
        """
        first: dict[Any, Any] = {}
        for u, e in zip(vals(self.df["repo_url"]), vals(self.df["repo_error"]), strict=True):
            first.setdefault(u, e)
        unflagged = [u for u in urls if _na(first.get(u))]
        self.flag(unflagged, message)

    def add(self, rows: pd.DataFrame) -> None:
        from pytacheck._r import bind_rows

        self.df = bind_rows([self.df, rows]).reset_index(drop=True)

    def keep(self, mask: Sequence[bool]) -> None:
        self.df = take(self.df, mask)


def repo_rows(paper_id: Any, repo_url: Any, repo_type: str, repo_error: Any) -> pd.DataFrame:
    """``data.frame(paper_id, repo_url, repo_type, repo_error)``."""
    return r_frame(
        [
            ("paper_id", paper_id, "string"),
            ("repo_url", repo_url, "string"),
            ("repo_type", repo_type, "string"),
            ("repo_error", repo_error, "string"),
        ]
    )


def collect_links(paper: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every repository link of the paper(s): ``paper_id``, ``repo_url``, ``repo_type``.

    Also returns ``figshare_links()`` (its ``figshare_unsupported`` flags
    private share links).
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives.dataone import dataone_links
    from pytacheck.archives.dataverse import dataverse_links
    from pytacheck.archives.dryad import dryad_links
    from pytacheck.archives.dspace7 import dspace7_links
    from pytacheck.archives.figshare import figshare_links
    from pytacheck.archives.fourtu import researchdata4tu_links
    from pytacheck.archives.github import github_links
    from pytacheck.archives.gitlab import gitlab_links
    from pytacheck.archives.mendeley import mendeley_links
    from pytacheck.archives.osf import osf_links
    from pytacheck.archives.psycharchives import dspace_links
    from pytacheck.archives.researchbox import rbox_links
    from pytacheck.archives.reshare import reshare_links
    from pytacheck.archives.zenodo import zenodo_links

    osf = osf_links(paper)
    if "href" in osf.columns:
        # exclude psychsci badges
        osf = take(osf, [not v for v in grepl("tvyxz", vals(osf["href"]))])
    found = {
        "osf": osf,
        "github": github_links(paper),
        "gitlab": gitlab_links(paper),
        "researchbox": rbox_links(paper),
        "dspace": dspace_links(paper),
        "dspace7": dspace7_links(paper),
        "zenodo": zenodo_links(paper),
        "dataverse": dataverse_links(paper),
        "figshare": figshare_links(paper),
        "dryad": dryad_links(paper),
        "reshare": reshare_links(paper),
        "researchdata4tu": researchdata4tu_links(paper),
        "mendeley": mendeley_links(paper),
        "dataone": dataone_links(paper),
    }
    parts = []
    for repo_type in REPO_TYPES:
        df = found[repo_type]
        missing = [c for c in ("paper_id", "href") if c not in df.columns]
        if missing:
            raise RError("undefined columns selected")
        parts.append(
            pd.DataFrame(
                {
                    "paper_id": _chr(vals(df["paper_id"])),
                    "href": _chr(vals(df["href"])),
                    "repo_type": pd.Series([repo_type] * len(df), dtype="string"),
                }
            )
        )
    repos = bind_rows(parts).drop_duplicates().reset_index(drop=True)
    repos = repos.rename(columns={"href": "repo_url"})
    repos["repo_error"] = pd.Series([None] * len(repos), dtype="string")
    return repos, found["figshare"]


def empty_links() -> pd.DataFrame:
    """The ``repos`` table of ``local_only = TRUE`` (no online links)."""
    return pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "repo_url": pd.Series([], dtype="string"),
            "repo_type": pd.Series([], dtype="string"),
            "repo_error": pd.Series([], dtype="string"),
        }
    )


def flag_figshare_share_links(repos: Repos, fs_links: pd.DataFrame | None) -> None:
    """Private Figshare share links (``figshare.com/s/...``) cannot be resolved."""
    if fs_links is None or "figshare_unsupported" not in fs_links.columns:
        return
    # R: `figshare_unsupported %in% TRUE`
    unsupported = [(not _na(v)) and bool(v) for v in fs_links["figshare_unsupported"]]
    if any(unsupported):
        urls = [h for h, u in zip(vals(fs_links["href"]), unsupported, strict=True) if u]
        repos.flag(urls, _FIGSHARE_SHARE)


# ---------------------------------------------------------------------------
# file listings
# ---------------------------------------------------------------------------

_FILE_COLS = ("repo_url", "file_name", "file_path", "file_url", "file_location", "file_size")


def _typed_files(rows: dict[str, list[Any]]) -> pd.DataFrame:
    """The listing table of one API platform, before its file types are joined."""
    return r_frame(
        [
            ("repo_url", rows["repo_url"], "string"),
            ("file_name", rows["file_name"], "string"),
            ("file_path", rows["file_path"], "string"),
            ("file_url", rows["file_url"], "string"),
            ("file_location", rows["file_location"], "string"),
            ("file_size", rows["file_size"], "float64"),
        ]
    )


def join_file_types(files: pd.DataFrame) -> pd.DataFrame:
    """Add ``file_type`` from the file extension (``left_join(file_types, by = "ext")``).

    One row per file: an extension ``file_types`` lists twice (``json`` is
    both ``code`` and ``data``) takes its first type in table order, what the
    module's de-duplication kept, as in github_files() and the other
    listings. R's join repeats such a file once per type (UPSTREAM_ISSUES U46).
    """
    out = files.reset_index(drop=True)
    if len(out) == 0:
        out = out.copy()
        out["file_type"] = pd.Series([], dtype="string")
        return out
    types = [t[0] for t in _ext_types(vals(out["file_name"]))]
    out = out.copy()
    out["file_type"] = pd.Series(types, dtype="string")
    return out


def _ext_types(names: Sequence[Any]) -> list[list[str | None]]:
    """The ``file_types`` types of each file name's extension, in table order
    (``[None]`` for none)."""
    from pytacheck.fileinfo.types import ext_rows

    bases = [r_basename(v) for v in names]
    ext: list[str | None] = [None if b is None else r_tolower(sub(r"^.*\.", "", b)) for b in bases]
    no_ext = [(not _na(n)) and not grepl(r"\.", b) for n, b in zip(names, bases, strict=True)]
    ext = [None if ne else e for e, ne in zip(ext, no_ext, strict=True)]
    lookup = ext_rows()
    return [
        [None if _na(t) else t for _, t in lookup[e]] if e is not None and e in lookup else [None]
        for e in ext
    ]


def _file_rows(
    info: pd.DataFrame,
    url_col: str,
    row_fn: Callable[[dict[str, Any], Any], dict[str, Any]],
) -> pd.DataFrame:
    """The R per-record/per-file ``lapply()`` over an ``*_info()`` table's ``files``.

    ``row_fn(record, f)`` gives one file's fields, where *record* holds the
    record's other columns (``info$<col>[[i]]``, read once per record, not
    once per file) and is shared by the record's files. Records without
    files add nothing. The result is typed like R's ``bind_rows()`` of the
    per-file data frames (a zero-column table when no record has files).
    """
    rows: dict[str, list[Any]] = {k: [] for k in _FILE_COLS}
    any_rows = False
    others = [c for c in info.columns if c != "files"]
    columns = {c: info[c].tolist() for c in others}
    for i, files_i in enumerate(info["files"].tolist()):
        if not isinstance(files_i, list | tuple | dict):
            if not _na(files_i):
                # an atomic value: R's lapply() visits it, `f$x` then errors
                raise RError("$ operator is invalid for atomic vectors")
            continue
        if len(files_i) == 0:
            continue
        if url_col not in columns:
            raise RError("subscript out of bounds")  # R: info$<url_col>[[i]] of NULL
        record = {c: columns[c][i] for c in others}
        repo_url = _as_chr(record[url_col])
        elements = list(files_i.values()) if isinstance(files_i, dict) else list(files_i)
        for f in elements:
            if f is not None and not isinstance(f, Mapping | list | tuple):
                raise RError("$ operator is invalid for atomic vectors")
            row = row_fn(record, f)
            row.setdefault("repo_url", repo_url)
            for k in _FILE_COLS:
                rows[k].append(row.get(k))
            any_rows = True
    if not any_rows:
        return pd.DataFrame()
    return _typed_files(rows)


def _typed_listing(files: pd.DataFrame) -> pd.DataFrame:
    """``if (nrow(x) > 0) <join file types> else x$file_type <- character(0)``."""
    if len(files) > 0:
        return join_file_types(files)
    out = files.copy()
    out["file_type"] = pd.Series([], dtype="string")
    return out


@dataclass
class Listing:
    """One platform's contribution: its files and (optional) repository metadata."""

    files: pd.DataFrame = field(default_factory=placeholder)
    meta: pd.DataFrame = field(default_factory=meta_frame)


def _quiet(fn: Callable[[], Any]) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn()


# -- OSF --------------------------------------------------------------------


class _NaScalar:
    """A length-one ``NA`` for :func:`r_frame` (recycled like R's ``NA_character_``)."""

    def __repr__(self) -> str:
        return "NA"


NA_SCALAR: Any = _NaScalar()


def _osf_file_frame(files: pd.DataFrame, repo_url: list[Any]) -> pd.DataFrame:
    """The OSF ``data.frame()`` of listed files (``provider`` kept for downloads).

    ``provider`` is R's ``osf_file_list$provider %||% NA_character_``: a
    missing column is a recycled ``NA`` (and R's row-count error on 0 rows).
    """
    n = len(files)
    path = _col(files, "path")
    provider = _col(files, "provider")
    return r_frame(
        [
            ("repo_url", repo_url, "string"),
            ("file_name", _col(files, "name"), "string"),
            ("file_path", None if path is None else list(gsub("^/+", "", path)), "string"),
            ("file_url", _col(files, "download_url"), "string"),
            ("file_location", [None] * n, "string"),
            ("file_size", _col(files, "size"), "float64"),
            ("file_type", _col(files, "filetype"), "string"),
            ("provider", NA_SCALAR if provider is None else provider, "string"),
        ]
    )


def _osf_files(info: pd.DataFrame) -> pd.DataFrame:
    """The listed files that are not marked private (``public`` ``FALSE``).

    R's ``filter(kind == "file", !isFALSE(public))`` tests the whole
    ``public`` column at once, which excludes anything only when the listing
    is a single private row (UPSTREAM_ISSUES U123).
    """
    if "public" not in info.columns:
        raise RError(
            "ℹ In argument: `!isFALSE(public)`.\nCaused by error:\n! object 'public' not found"
        )
    kind = vals(info["kind"])
    public = vals(info["public"])
    keep = [
        (not _na(k)) and k == "file" and p is not False for k, p in zip(kind, public, strict=True)
    ]
    return take(info, keep)


def _osf_listing(urls: list[str], pb: Any, cache: bool) -> pd.DataFrame:
    """``lapply(urls, osf_info(x, recursive = TRUE)) |> bind_rows()`` with ``repo_name``."""
    from pytacheck._r import bind_rows
    from pytacheck.archives.osf import osf_info

    parts = []
    for x in urls:
        f = osf_info(x, recursive=True, pb=pb, cache=cache)
        f = f.copy()
        f["repo_name"] = pd.Series([x] * len(f), dtype="string")
        parts.append(f)
    return bind_rows(parts)


def list_osf(
    repos: Repos, osf_urls: list[str], osf_paper_id: Any, pb: Any, cache: bool
) -> pd.DataFrame:
    """The OSF block: list files, follow registrations to their source projects."""
    from pytacheck._r import bind_rows

    osf_files_df = placeholder()
    if not osf_urls:
        return osf_files_df
    reg_pairs: list[tuple[Any, str]] = []
    try:
        osf_info_df = _quiet(lambda: _osf_listing(osf_urls, pb, cache))

        # a link OSF does not know (an invalid id) has no osf_type: when no
        # link resolved there is no osf_type column at all
        otypes = (
            vals(osf_info_df["osf_type"])
            if "osf_type" in osf_info_df.columns
            else [None] * len(osf_info_df)
        )
        ourls = vals(osf_info_df["osf_url"])

        # registration URL -> the project it was registered from (R: a named vector)
        if "parent" in osf_info_df.columns:
            for t, parent, url in zip(otypes, vals(osf_info_df["parent"]), ourls, strict=True):
                if t == "registrations" and not _na(parent) and not _na(url):
                    reg_pairs.append((url, f"https://osf.io/{parent}"))

        # R: reg_url_to_parent[file_repo_url] -- a name matches its first entry
        reg_map: dict[Any, str] = {}
        for name, parent in reg_pairs:
            reg_map.setdefault(name, parent)

        if "kind" in osf_info_df.columns:
            # files found via a registration belong to the project it was registered from
            file_list = _osf_files(osf_info_df)
            file_repo_url = [reg_map.get(u, u) for u in vals(file_list["repo_name"])]
            osf_files_df = _osf_file_frame(file_list, file_repo_url)

        # registrations (and anything that is not a node/file) are never the
        # storage location. A link OSF does not know stays, flagged: R drops
        # it silently beside valid links, and without any valid link its
        # filter() finds metacheck's osf_type() function and stores "'match'
        # requires vector arguments" as the error (UPSTREAM_ISSUES U122)
        to_remove = [
            u
            for t, u in zip(otypes, ourls, strict=True)
            if not _na(t) and t not in ("nodes", "files", "private")
        ]
        repos.keep([not v for v in r_in(vals(repos.df["repo_url"]), to_remove)])

        private = [u for t, u in zip(otypes, ourls, strict=True) if t == "private"]
        if private:
            repos.flag(private, "private")
        unknown = [u for t, u in zip(otypes, ourls, strict=True) if _na(t) and not _na(u)]
        if unknown:
            repos.flag(unknown, "invalid or inaccessible OSF link")

        # follow registrations back to their origin project
        parent_urls_all = unique(p for _, p in reg_pairs)
        parent_urls_new = setdiff(parent_urls_all, osf_urls)
        closed_parent_urls: list[Any] = []
        if parent_urls_new:
            parent_info = _quiet(lambda: _osf_listing(parent_urls_new, pb, cache))
            if "kind" in parent_info.columns:
                parent_files = _osf_files(parent_info)
                if len(parent_files) > 0:
                    osf_files_df = bind_rows(
                        [
                            osf_files_df,
                            _osf_file_frame(parent_files, vals(parent_files["repo_name"])),
                        ]
                    )
            # a parent OSF cannot list (no osf_type) counts as closed
            p_urls = vals(parent_info["osf_url"])
            p_types = (
                vals(parent_info["osf_type"])
                if "osf_type" in parent_info.columns
                else [None] * len(p_urls)
            )
            p_in = r_in(p_urls, parent_urls_new)
            closed_parent_urls = unique(
                u
                for u, t, isin in zip(p_urls, p_types, p_in, strict=True)
                if isin and (_na(t) or t == "private")
            )
            closed_parent_urls = unique([*closed_parent_urls, *setdiff(parent_urls_new, p_urls)])

        if parent_urls_all:
            missing = setdiff(parent_urls_all, vals(repos.df["repo_url"]))
            if missing:
                file_urls = vals(osf_files_df["repo_url"]) if "repo_url" in osf_files_df else []
                has_files = r_in(missing, file_urls)
                is_closed = r_in(missing, closed_parent_urls)
                errors: list[str | None] = []
                for p, hf, closed in zip(missing, has_files, is_closed, strict=True):
                    if closed and hf:
                        regs = ", ".join(name for name, par in reg_pairs if par == p)
                        errors.append(
                            "closed registration source (files retrieved from registration: "
                            f"{regs})"
                        )
                    elif closed:
                        errors.append("closed registration source")
                    else:
                        errors.append(None)
                repos.add(repo_rows(osf_paper_id, missing, "osf", errors))

        # registrations without a parent keep their own URL for their files
        if "repo_url" in osf_files_df.columns:
            fr = vals(osf_files_df["repo_url"])
            linked = [u for u, isin in zip(fr, r_in(fr, osf_urls), strict=True) if isin]
            orphans = setdiff(unique(linked), vals(repos.df["repo_url"]))
            if orphans:
                repos.add(repo_rows(osf_paper_id, orphans, "osf", NA_SCALAR))
    except Exception as e:
        msg = condition_message(e)
        # a link removed above (a registration, to be replaced by its source
        # project) comes back with the error, holding the files already listed
        # for it: R's handler finds nothing left to flag, and the paper
        # reported "no repositories found" (UPSTREAM_ISSUES U122)
        # (only registrations: a link removed as no storage location at all,
        # such as a user page, stays removed)
        present = set(vals(repos.df["repo_url"]))
        registrations = {reg for reg, _ in reg_pairs}
        lost = [u for u in unique(osf_urls) if u not in present and u in registrations]
        if lost:
            repos.add(repo_rows(osf_paper_id, lost, "osf", msg))
            back: dict[str, Any] = {}
            for reg, parent in reg_pairs:
                back.setdefault(parent, reg)
            if back and "repo_url" in osf_files_df.columns and len(osf_files_df) > 0:
                present = set(vals(repos.df["repo_url"]))
                moved = [
                    u if u in present else back.get(u, u) for u in vals(osf_files_df["repo_url"])
                ]
                osf_files_df = osf_files_df.assign(
                    repo_url=pd.Series(moved, index=osf_files_df.index, dtype="string")
                )
        repos.flag_unflagged(osf_urls, msg)
    return osf_files_df


# -- GitHub / GitLab ----------------------------------------------------------


def list_git(
    repos: Repos, urls: list[str], host: str, cache: bool, pb: Any = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The GitHub / GitLab block: the whole tree of each repository, and its licence."""
    from pytacheck._r import bind_rows
    from pytacheck.archives import _tick
    from pytacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_list_ok,
    )

    if host == "github":
        from pytacheck.archives.github import github_tree_files as tree_files
    else:
        from pytacheck.archives.gitlab import gitlab_tree_files as tree_files

    files_df = placeholder()
    if not urls:
        return files_df, meta_frame()

    results: dict[str, Any] = {}
    for url in urls:
        cached = _repo_info_cache_get(host, url) if cache is True else None
        if cached is not None:
            results[url] = cached
            continue
        try:
            result = tree_files(url)
        except Exception as e:
            result = {
                "gated": True,
                "reason": condition_message(e),
                "files": None,
                "default_branch": None,
            }
            if host == "gitlab":
                result["license"] = None
        if cache is True and _repo_info_list_ok(result):
            _repo_info_cache_put(host, url, result)
        results[url] = result

    for url in urls:
        r = results[url]
        if r.get("gated") is True:
            reason = r.get("reason")
            repos.flag([url], reason)
            warnings.warn(f"Repository {url} was not listed: {reason}.", stacklevel=3)
            label = "GitHub" if host == "github" else "GitLab"
            _tick(pb, f"Skipping {label} repo ({reason}): {url}")

    good = [
        r.get("files")
        for r in results.values()
        if r.get("gated") is not True and r.get("files") is not None
    ]
    if good:
        file_list = bind_rows(good)
        types = _col(file_list, "type")
        if types is None:
            file_list = file_list.iloc[0:0]
        else:
            file_list = take(file_list, [t is not None and t != "dir" for t in types])
        if len(file_list) > 0:
            files_df = r_frame(
                [
                    ("repo_url", _col(file_list, "repo"), "string"),
                    ("file_name", _col(file_list, "name"), "string"),
                    ("file_path", _col(file_list, "path"), "string"),
                    ("file_url", _col(file_list, "download_url"), "string"),
                    ("file_location", NA_SCALAR, "string"),
                    ("file_size", _col(file_list, "size"), "float64"),
                    ("file_type", _col(file_list, "type"), "string"),
                ],
                tibble=True,
            )

    licenses = [empty_or(results[u].get("license"), None) for u in urls]
    return files_df, meta_frame(urls, NA_SCALAR, licenses)


# -- ResearchBox, DSpace --------------------------------------------------------


def _filter_not_dir(df: pd.DataFrame) -> pd.DataFrame:
    """``dplyr::filter(!isdir)`` (``NA`` rows dropped)."""
    if "isdir" not in df.columns:
        raise RError("ℹ In argument: `!isdir`.\nCaused by error:\n! object 'isdir' not found")
    keep = [(not _na(v)) and not bool(v) for v in vals(df["isdir"])]
    return take(df, keep)


def list_researchbox(repos: Repos, urls: list[str], pb: Any) -> pd.DataFrame:
    """The ResearchBox block (the box is downloaded and unzipped to list it)."""
    from pytacheck.archives.researchbox import rbox_file_download

    files_df = placeholder()
    if not urls:
        return files_df
    try:
        rb = rbox_file_download(urls, pb=pb)
        if rb is None:
            raise RError("no applicable method for 'filter' applied to an object of class \"NULL\"")
        rb = _filter_not_dir(rb)
        names = _col(rb, "name")
        files_df = r_frame(
            [
                ("repo_url", _col(rb, "rb_url"), "string"),
                ("file_name", None if names is None else [r_basename(n) for n in names], "string"),
                ("file_path", names, "string"),
                ("file_url", _col(rb, "rb_url"), "string"),
                ("file_location", _col(rb, "file_location"), "string"),
                ("file_size", _col(rb, "size"), "float64"),
                ("file_type", _col(rb, "type"), "string"),
            ]
        )
    except Exception as e:
        repos.flag(urls, condition_message(e))
    return files_df


def _named_lookup(named: Mapping[str, Any] | None, keys: Sequence[str]) -> list[Any] | None:
    """``unname(x[keys])`` for an R named vector (``None`` when *x* is ``NULL``)."""
    if not named:
        return None
    return [named.get(k) for k in keys]


def list_dspace(
    repos: Repos, urls: list[str], pb: Any, cache: bool
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The legacy DSpace block (PsychArchives and other DSpace 5/6 hosts)."""
    from pytacheck.archives.psycharchives import _psycharchives_file_lists

    files_df = placeholder()
    meta = meta_frame()
    if not urls:
        return files_df, meta
    try:
        # psycharchives_file_download(), and the items it could not find
        pa, unfound = _psycharchives_file_lists(urls, pb=pb, cache=cache)
        # an item that could not be found is reported like other failed
        # repositories; R leaves it unflagged (or, when every item of several
        # failed, stores its join error: UPSTREAM_ISSUES U43)
        if unfound:
            repos.flag(unfound, _DSPACE_UNFOUND)
        attrs = pa.attrs if isinstance(pa, pd.DataFrame) else {}
        rights = attrs.get("rights") or None
        if rights:
            restricted = [
                k
                for k, v in rights.items()
                if not _na(v) and grepl("restricted|embargo", str(v), ignore_case=True)
            ]
            if restricted:
                repos.flag(restricted, "restricted access")
        doi = attrs.get("doi") or None
        if rights or doi:
            meta = r_frame(
                [
                    ("repo_url", urls, "string"),
                    ("doi", _named_lookup(doi, urls), "string"),
                    ("license", _named_lookup(rights, urls), "string"),
                ]
            )
        if pa is not None and len(pa) > 0:
            pa = _filter_not_dir(pa)
            names = _col(pa, "name")
            files_df = r_frame(
                [
                    ("repo_url", _col(pa, "pa_url"), "string"),
                    ("file_name", names, "string"),
                    ("file_path", names, "string"),
                    ("file_url", _col(pa, "file_url"), "string"),
                    ("file_location", _col(pa, "file_location"), "string"),
                    ("file_size", _col(pa, "size"), "float64"),
                    ("file_type", _col(pa, "type"), "string"),
                ]
            )
    except Exception as e:
        repos.flag_unflagged(urls, condition_message(e))
    return files_df, meta


def list_dspace7(repos: Repos, urls: list[str], pb: Any) -> pd.DataFrame:
    """The DSpace 7+ block."""
    from pytacheck.archives.dspace7 import _dspace7_file_lists

    files_df = placeholder()
    if not urls:
        return files_df
    try:
        # dspace7_file_download(), and the items it could not find (an item
        # without files was found): reported like other failed repositories;
        # R leaves them unflagged, or stores its join error when every item of
        # several listed nothing, empty items included (UPSTREAM_ISSUES U43)
        ds, unfound = _dspace7_file_lists(urls, pb=pb)
        if unfound:
            repos.flag(unfound, _DSPACE_UNFOUND)
        if ds is not None and len(ds) > 0:
            ds = _filter_not_dir(ds)
            names = _col(ds, "name")
            files_df = r_frame(
                [
                    ("repo_url", _col(ds, "dspace7_url"), "string"),
                    ("file_name", names, "string"),
                    ("file_path", names, "string"),
                    ("file_url", _col(ds, "file_url"), "string"),
                    ("file_location", _col(ds, "file_location"), "string"),
                    ("file_size", _col(ds, "size"), "float64"),
                    ("file_type", _col(ds, "type"), "string"),
                ]
            )
    except Exception as e:
        repos.flag(urls, condition_message(e))
    return files_df


# -- data repositories with a JSON file list ------------------------------------


def _meta_from(info: pd.DataFrame, url_col: str, col_chr: bool = False) -> pd.DataFrame:
    """``data.frame(repo_url = <url_col>, doi, license)`` of an ``*_info()`` table."""
    if col_chr:
        from pytacheck.utils import _col_chr

        return meta_frame(_col_chr(info, url_col), _col_chr(info, "doi"), _col_chr(info, "license"))

    def chr_col(name: str) -> list[Any]:
        # a missing column (a listing without doi or license, such as a
        # Figshare private share link's) is NA; R's data.frame() refuses the
        # zero-length as.character(NULL) and the whole platform block fails
        # (UPSTREAM_ISSUES U122)
        values = _col(info, name)
        return [None] * len(info) if values is None else [_as_chr(v) for v in values]

    return r_frame(
        [
            ("repo_url", chr_col(url_col), "string"),
            ("doi", chr_col("doi"), "string"),
            ("license", chr_col("license"), "string"),
        ]
    )


def _api_listing(
    repos: Repos,
    urls: list[str],
    info_fn: Callable[[], pd.DataFrame],
    url_col: str,
    row_fn: Callable[[dict[str, Any], Any], dict[str, Any]],
    col_chr: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The shared shape of the Zenodo, Dataverse, Figshare, Dryad, ReShare,
    4TU.ResearchData, Mendeley Data and DataONE blocks."""
    files_df = placeholder()
    meta = meta_frame()
    if not urls:
        return files_df, meta
    try:
        info = info_fn()
        if len(info) > 0:
            meta = _meta_from(info, url_col, col_chr)
        if len(info) > 0 and "files" in info.columns:
            listed = _file_rows(info, url_col, row_fn)
            files_df = _typed_listing(listed)
    except Exception as e:
        # an informative error already recorded (e.g. a Figshare private share
        # link) is kept
        repos.flag_unflagged(urls, condition_message(e))
    return files_df, meta


def _f(f: Any, *path: str) -> Any:
    for name in path:
        f = dollar(f, name)
    return f


def _row(name: Any, file_url: Any, size: Any) -> dict[str, Any]:
    chr_name = _as_chr(empty_or(name, None))
    return {
        "file_name": chr_name,
        "file_path": chr_name,
        "file_url": file_url,
        "file_location": None,
        "file_size": _as_num(empty_or(size, None)),
    }


def zenodo_row(_record: dict[str, Any], f: Any) -> dict[str, Any]:
    links = dollar(f, "links")
    file_url = None
    if links is not None:
        self_ = dollar(links, "self")
        if not (self_ is None or (isinstance(self_, list | tuple) and len(self_) == 0)):
            file_url = _as_chr(self_)
    return _row(dollar(f, "key"), file_url, dollar(f, "size"))


def dataverse_row(record: dict[str, Any], f: Any) -> dict[str, Any]:
    df = dollar(f, "dataFile")
    if df is None:
        df = {}
    fid = dollar(df, "id")
    file_url = None
    if not (fid is None or (isinstance(fid, list | tuple) and len(fid) == 0)):
        if "dataverse_host" not in record:
            raise RError("subscript out of bounds")  # R: .dv_info$dataverse_host[[i]] of NULL
        host = _as_chr(record["dataverse_host"])
        file_url = f"https://{'NA' if host is None else host}/api/access/datafile/{_as_chr(fid)}"
    name = empty_or(empty_or(dollar(f, "label"), dollar(df, "filename")), None)
    return _row(name, file_url, dollar(df, "filesize"))


def figshare_row(_record: dict[str, Any], f: Any) -> dict[str, Any]:
    return _row(
        dollar(f, "name"), _as_chr(empty_or(dollar(f, "download_url"), None)), dollar(f, "size")
    )


def dryad_row(_record: dict[str, Any], f: Any) -> dict[str, Any]:
    href = empty_or(_f(f, "_links", "stash:download", "href"), None)
    file_url = None if _na(href) else "https://datadryad.org" + _as_chr(href)  # type: ignore[operator]
    return _row(dollar(f, "path"), file_url, dollar(f, "size"))


def reshare_row(_record: dict[str, Any], f: Any) -> dict[str, Any]:
    uri = empty_or(dollar(f, "uri"), None)
    file_url = None if _na(uri) else sub("^http://", "https://", _as_chr(uri))
    return _row(dollar(f, "filename"), file_url, dollar(f, "filesize"))


def mendeley_row(_record: dict[str, Any], f: Any) -> dict[str, Any]:
    cd = dollar(f, "content_details")
    if cd is None:
        cd = {}
    return _row(
        dollar(f, "filename"),
        _as_chr(empty_or(dollar(cd, "download_url"), None)),
        dollar(cd, "size"),
    )


def dataone_row(record: dict[str, Any], f: Any) -> dict[str, Any]:
    if "dataone_host" not in record:
        raise RError("subscript out of bounds")  # R: .dataone_info_tbl$dataone_host[[i]] of NULL
    host = record["dataone_host"]
    if "\0api_base" not in record:
        # R: for (h in .dataone_hosts()) if (identical(h$host, host)) api_base <- h$api_base
        # (once per record: the record is shared by its files)
        from pytacheck.archives.dataone import _dataone_hosts

        api = None
        for h in _dataone_hosts():
            if h.get("host") == host and not _na(host):
                api = h.get("api_base")
        record["\0api_base"] = api
    api_base = record["\0api_base"]
    pid = dollar(f, "pid")
    file_url = None
    has_pid = not (pid is None or (isinstance(pid, list | tuple) and len(pid) == 0))
    if api_base is not None and has_pid and not _na(pid):
        file_url = f"https://{host}{api_base}object/{url_encode_reserved(_as_chr(pid) or '')}"
    return _row(dollar(f, "key"), file_url, dollar(f, "size"))


# ---------------------------------------------------------------------------
# local files
# ---------------------------------------------------------------------------


def list_local(local_path: Any) -> pd.DataFrame:
    """``local_files(local_path, recursive = TRUE)`` with repository-relative paths."""
    import os

    from pytacheck.archives.local import local_files

    df = local_files(local_path, recursive=True)
    if len(df) == 0:
        return df
    locs = vals(df["file_location"])
    roots = vals(df["repo_url"])
    names = vals(df["file_name"])
    paths = []
    for loc, root, name in zip(locs, roots, names, strict=True):
        if _na(loc) or loc == "" or _na(root) or root == "":
            paths.append(name)
            continue
        loc_norm = os.path.realpath(os.path.expanduser(str(loc)))
        root_norm = os.path.realpath(os.path.expanduser(str(root)))
        prefix = root_norm + "/"
        if loc_norm.startswith(prefix):
            rel = loc_norm[len(prefix) :]
        elif loc_norm == root_norm:
            rel = r_basename(loc_norm) or ""
        else:
            rel = name
        paths.append(name if rel == "" else rel)
    out = df.copy()
    out["file_path"] = pd.Series(paths, dtype="string")
    # a file file_category() cannot place (most extensions: .zip, .tar.gz,
    # .txt, .pdf, images) takes its type from the extension, as the files of
    # an online repository do, when the extension has one type; R leaves it
    # NA, so a local archive was not counted as one (UPSTREAM_ISSUES U123).
    # An extension with several types (.json and .rds: code or data; .html:
    # code or web) stays unclassified rather than counted as code.
    if "file_type" in out.columns:
        by_ext = [t[0] if len(t) == 1 else None for t in _ext_types(vals(out["file_name"]))]
        out["file_type"] = pd.Series(
            [e if _na(t) else t for t, e in zip(vals(out["file_type"]), by_ext, strict=True)],
            dtype="string",
        )
    return out
