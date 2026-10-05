"""Paper corpora from the scienceverse/papers repository (port of ``R/import-papers.R``).

metacheck publishes ready-converted corpora (``collabra``, ``jdm``, ...) as
``.rds`` files attached to GitHub releases. ``papers_available()`` lists
them, ``papers_load()`` downloads one and returns a :class:`PaperList`,
``papers_remove()`` deletes a cached copy and ``papers_metadata()`` fetches
a corpus's Dublin Core ``metadata.json``.

The corpora are R serialisations of paper lists, which no Python package in
our dependency set reads (pyreadr only reads data frames), so this module
reads them (``readRDS()``) with the unserializer of the data checks
(``datacheck/_files_rdata.py``) and turns ``scivrs_paper`` objects into
:class:`~metacheck.papers.Paper` objects. The cache directory is the one
metacheck uses, so R and Python share downloaded corpora.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from os import PathLike
from pathlib import Path
from typing import Any, cast

import pandas as pd

from metacheck._r.base import r_round
from metacheck._r.regex import sub
from metacheck.datacheck._files_rdata import NA_INTEGER, RObject, read_rds

__all__ = ["papers_available", "papers_load", "papers_metadata", "papers_remove"]

DEFAULT_REPO = "scienceverse/papers"


# ---------------------------------------------------------------------------
# cache + GitHub releases
# ---------------------------------------------------------------------------


def _papers_cache_dir() -> Path:
    """Port of ``R/import-papers.R::.papers_cache_dir()``.

    ``rappdirs::user_data_dir("metacheck", "scienceverse")/papers``: shared
    with metacheck, so a corpus cached by either package is reused.
    """
    import platformdirs

    return Path(platformdirs.user_data_dir("metacheck", "scienceverse")) / "papers"


def _file_path_sans_ext(name: str) -> str:
    """``tools::file_path_sans_ext()``."""
    return cast(str, sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", name))


def _release_assets_frame(rows: list[tuple[str, str, Any, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": pd.Series([r[0] for r in rows], dtype="string"),
            "tag": pd.Series([r[1] for r in rows], dtype="string"),
            "size": pd.Series([r[2] for r in rows], dtype="Int64"),
            "download_url": pd.Series([r[3] for r in rows], dtype="string"),
        }
    )


def _papers_release_assets(repo: str = DEFAULT_REPO) -> pd.DataFrame | None:
    """Port of ``R/import-papers.R::.papers_release_assets()``.

    One row per ``.rds`` asset of the repository's releases (``name``,
    ``tag``, ``size``, ``download_url``). Release tags follow a
    ``{corpus}-{date}`` convention; when a corpus family was re-released,
    only its most recently published release is kept. ``None`` when no
    release has an ``.rds`` asset (R returns ``NULL``).
    """
    from metacheck import http

    url = f"https://api.github.com/repos/{repo}/releases"
    try:
        resp = http.request("GET", url, headers={"Accept": "application/vnd.github+json"})
    except Exception as exc:
        raise ConnectionError(f"Could not reach GitHub API: {exc}") from exc
    if resp is None:
        raise ConnectionError("Could not reach GitHub API: connection failed")
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub API error (status {resp.status_code}): {url}")

    releases = resp.json()
    if not releases:
        return _release_assets_frame([])

    # keep the most recently published release per corpus family
    tags = [r.get("tag_name") for r in releases]
    family = [sub(r"-\d{4}-\d{2}-\d{2}$", "", t) for t in tags]
    published = [str(r.get("published_at")) for r in releases]
    # order(published, decreasing = TRUE): Python's reverse sort keeps ties stable
    order = sorted(range(len(releases)), key=lambda i: published[i], reverse=True)
    kept, seen = [], set()
    for i in order:
        if family[i] in seen:
            continue
        seen.add(family[i])
        kept.append(releases[i])

    rows: list[tuple[str, str, Any, str]] = []
    for rel in kept:
        rds = [
            a for a in rel.get("assets") or [] if str(a.get("name", "")).lower().endswith(".rds")
        ]
        rows.extend((a["name"], rel["tag_name"], a["size"], a["browser_download_url"]) for a in rds)
    if not rows:
        return None
    return _release_assets_frame(rows)


def papers_available(repo: str = DEFAULT_REPO) -> pd.DataFrame:
    """Port of ``R/import-papers.R::papers_available()``: corpora that can be loaded.

    A table with the corpus ``name``, release ``tag``, ``size_mb`` and
    whether it is ``cached`` locally.
    """
    assets = _papers_release_assets(repo)
    cache_dir = _papers_cache_dir()
    # a repository without corpora gives an empty table (metacheck fails with
    # "arguments imply differing number of rows: 0, 1"; U18)
    if assets is None:
        assets = _release_assets_frame([])
    names = [_file_path_sans_ext(n) for n in assets["name"]]
    return pd.DataFrame(
        {
            "name": pd.Series(names, dtype="string"),
            "tag": assets["tag"].astype("string"),
            "size_mb": pd.Series(
                [r_round(float(s) / 1e6, 1) for s in assets["size"]], dtype="float64"
            ),
            "cached": pd.Series(
                [(cache_dir / f"{n}.rds").exists() for n in names], dtype="boolean"
            ),
        }
    )


def papers_load(
    name: str,
    repo: str = DEFAULT_REPO,
    cache: bool = False,
    overwrite: bool = False,
) -> Any:
    """Port of ``R/import-papers.R::papers_load()``: download and load a paper corpus.

    The corpus ``.rds`` is downloaded to a temporary file and read; with
    ``cache=True`` it is kept in the (metacheck-shared) user data directory
    and reused by later calls unless ``overwrite=True``.
    """
    from metacheck import http

    cache_dir = _papers_cache_dir()
    cache_path = cache_dir / f"{name}.rds"
    if cache and cache_path.exists() and not overwrite:
        _message(f"Loading cached {name} ...")
        return _read_rds(cache_path)

    assets = _papers_release_assets(repo)
    if assets is None:
        # no .rds asset: "not found" (metacheck: "argument is of length zero"; U18)
        assets = _release_assets_frame([])
    names = [_file_path_sans_ext(n) for n in assets["name"]]
    hits = [i for i, n in enumerate(names) if n == name]
    if not hits:
        available = ", ".join(names) or "none"
        raise ValueError(f"'{name}' not found in releases of {repo}. Available: {available}")
    entry = assets.iloc[hits[0]]  # type: ignore[union-attr]
    size_mb = r_round(float(entry["size"]) / 1e6, 1)
    _message(f"Downloading {name} ({_format_mb(size_mb)} MB) ...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / f"{name}.rds"
        url = str(entry["download_url"])
        try:
            with http.client().stream("GET", url, follow_redirects=True, timeout=None) as resp:
                status = resp.status_code
                if status == 200:
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
        except Exception as exc:
            raise ConnectionError(f"Download failed: {exc}") from exc
        if status != 200:
            raise RuntimeError(f"Download failed (status {status}): {url}")
        if cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp, cache_path)
            _message(f"Cached to {cache_path}")
        _message(f"Loading {name} ...")
        return _read_rds(tmp)


def papers_remove(name: str) -> bool:
    """Port of ``R/import-papers.R::papers_remove()``: delete a cached corpus.

    Returns ``True`` if it was deleted, ``False`` if it was not cached.
    """
    path = _papers_cache_dir() / f"{name}.rds"
    if not path.exists():
        _message(f"'{name}' is not cached; nothing to remove.")
        return False
    path.unlink()
    _message(f"Removed cached corpus: {name}")
    return True


def papers_metadata(name: str, repo: str = DEFAULT_REPO) -> dict[str, Any]:
    """Port of ``R/import-papers.R::papers_metadata()``: a corpus's Dublin Core metadata.

    Colons in the top-level ``dc:field`` names become underscores
    (``dc:title`` -> ``dc_title``).
    """
    from metacheck import http

    url = f"https://raw.githubusercontent.com/{repo}/main/{name}/metadata.json"
    try:
        resp = http.request("GET", url)
    except Exception as exc:
        raise ConnectionError(f"Could not reach GitHub: {exc}") from exc
    if resp is None:
        raise ConnectionError("Could not reach GitHub: connection failed")
    if resp.status_code == 404:
        avail = ", ".join(str(n) for n in papers_available(repo)["name"])
        raise ValueError(f"No metadata.json found for '{name}' in {repo}. Available: {avail}")
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub error (status {resp.status_code}): {url}")
    import orjson

    meta = orjson.loads(resp.content)
    return {k.replace(":", "_"): v for k, v in meta.items()}


def _format_mb(x: float) -> str:
    from metacheck._r.base import as_character

    return str(as_character(x))


def _message(msg: str) -> None:
    from metacheck.config import verbose

    if verbose():
        from rich.console import Console

        Console(stderr=True).print(msg, markup=False, highlight=False)


# ---------------------------------------------------------------------------
# readRDS()
# ---------------------------------------------------------------------------


def _text(s: str | None) -> str | None:
    """A string as Python text: bytes that are not UTF-8 become U+FFFD."""
    if s is None or s.isascii():
        return s
    return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _values(obj: RObject) -> list[Any]:
    """The elements of a vector as Python values.

    NA integers and logicals are ``None``, a double keeps NaN, and text is
    cleaned with :func:`_text`.
    """
    t = obj.type
    if t == 10:
        return [None if v == NA_INTEGER else bool(v) for v in obj.value.tolist()]
    if t == 13:
        return [None if v == NA_INTEGER else v for v in obj.value.tolist()]
    if t in (14, 15):
        return list(obj.value.tolist())
    if t == 16:
        return [_text(v) for v in obj.value]
    return list(obj.value)


def _dates(obj: RObject) -> list[Any] | None:
    """``Date`` / ``POSIXct`` vectors as ``datetime`` values (``None`` if neither)."""
    import datetime as dt

    classes = obj.classes
    if obj.type not in (13, 14) or not ({"Date", "POSIXct"} & set(classes)):
        return None
    out: list[Any] = []
    for v in _values(obj):
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            out.append(None)
        elif "Date" in classes:
            out.append(dt.date(1970, 1, 1) + dt.timedelta(days=math.floor(v)))
        else:
            out.append(pd.Timestamp(v, unit="s", tz="UTC"))
    return out


def _vector_series(obj: RObject) -> pd.Series:
    t = obj.type
    dates = _dates(obj)
    if dates is not None:
        if "POSIXct" in obj.classes:
            return pd.Series(pd.to_datetime(dates, utc=True))
        return pd.Series(dates, dtype=object)
    values = _values(obj)
    if t == 13 and "factor" in obj.classes:
        levels = _values(obj.attrs["levels"])
        return pd.Series([None if v is None else levels[v - 1] for v in values], dtype="string")
    if t == 16:
        return pd.Series(values, dtype="string")
    if t == 13:
        return pd.Series(values, dtype="Int64")
    if t == 10:
        return pd.Series(values, dtype="boolean")
    if t == 14:
        return pd.Series(values, dtype="float64")
    return pd.Series([_to_python(v) for v in obj.value], dtype=object)


def _names(obj: RObject) -> list[str] | None:
    names = obj.attrs.get("names")
    return _values(names) if isinstance(names, RObject) else None


def _data_frame(obj: RObject) -> pd.DataFrame:
    names = _names(obj) or [f"V{i + 1}" for i in range(len(obj.value))]
    nrow = 0
    rn = obj.attrs.get("row.names")
    if isinstance(rn, RObject):
        if rn.type == 13 and len(rn.value) == 2 and rn.value[0] == NA_INTEGER:  # c(NA, -n)
            nrow = abs(int(rn.value[1]))
        else:
            nrow = len(rn.value)
    cols: dict[str, pd.Series] = {}
    for name, col in zip(names, obj.value, strict=True):
        if isinstance(col, RObject) and col.type in (10, 13, 14, 16):
            cols[name] = _vector_series(col)
        elif isinstance(col, RObject) and col.type == 19 and "data.frame" not in col.classes:
            cols[name] = pd.Series([_cell(v) for v in col.value], dtype=object)
        else:
            cols[name] = pd.Series([_to_python(col)] * nrow, dtype=object)
    if not cols:
        return pd.DataFrame(index=range(nrow))
    return pd.DataFrame(cols)


def _cell(v: Any) -> Any:
    """A list-column cell: vectors become Python lists (``NULL`` -> ``None``)."""
    if isinstance(v, RObject) and v.type in (10, 13, 14, 16) and not v.attrs.get("class"):
        return _values(v)
    return _to_python(v)


def _paper(obj: RObject) -> Any:
    from metacheck.papers.io import coerce_paper
    from metacheck.papers.model import Paper

    names = _names(obj) or []
    elements = dict(zip(names, obj.value, strict=True))
    pid = elements.get("paper_id")
    paper_id = _values(pid)[0] if isinstance(pid, RObject) and len(pid) else None
    p = Paper(paper_id)
    for name in [n for n in names if n != "paper_id"]:
        p[name] = _to_python(elements[name])
    return coerce_paper(p)


def _to_python(obj: Any) -> Any:
    """Convert an unserialised R value into pytacheck/pandas/Python objects."""
    if not isinstance(obj, RObject):
        return obj
    classes = obj.classes
    if obj.type == 19:
        if "data.frame" in classes:
            return _data_frame(obj)
        if "scivrs_paper" in classes:
            return _paper(obj)
        items = [_to_python(v) for v in obj.value]
        if "scivrs_paperlist" in classes:
            from metacheck.papers.model import Paper, PaperList

            if all(isinstance(i, Paper) for i in items):
                return PaperList(items)
        names = _names(obj)
        if names is not None:
            return dict(zip(names, items, strict=True))
        return items
    if obj.type in (10, 13, 14, 16):
        if "factor" in classes:
            return list(_vector_series(obj))
        dates = _dates(obj)
        values = _values(obj) if dates is None else dates
        dim = obj.attrs.get("dim")
        if isinstance(dim, RObject) and len(dim.value) >= 2 and dates is None:
            import numpy as np  # a matrix / array: column-major, like R

            return np.array(values, dtype=object if obj.type == 16 else None).reshape(
                [int(d) for d in dim.value], order="F"
            )
        names = _names(obj)
        if names is not None:  # a named vector: a Series indexed by the names
            series = _vector_series(obj)
            series.index = pd.Index(names)
            return series
        return list(values)
    if obj.type == 15:  # complex
        return _values(obj)
    if obj.type == 24:  # raw
        return bytes(obj.value)
    if obj.type == 2:
        return {k: _to_python(v) for k, v in obj.value}
    return obj


def _read_rds(file: str | PathLike[str]) -> Any:
    """``readRDS()``: read an R serialisation file into Python objects.

    Paper lists and papers become :class:`~metacheck.papers.PaperList` /
    :class:`~metacheck.papers.Paper`, data frames become DataFrames, named
    lists dicts and vectors lists. The file is read by the unserializer of the
    data checks (:func:`metacheck.datacheck._files_rdata.read_rds`: XDR, binary
    and ASCII streams, gzip, bzip2 or xz compression).
    """
    return _to_python(read_rds(Path(file)))
