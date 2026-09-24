"""Paper corpora from the scienceverse/papers repository (port of ``R/import-papers.R``).

metacheck publishes ready-converted corpora (``collabra``, ``jdm``, ...) as
``.rds`` files attached to GitHub releases. ``papers_available()`` lists
them, ``papers_load()`` downloads one and returns a :class:`PaperList`,
``papers_remove()`` deletes a cached copy and ``papers_metadata()`` fetches
a corpus's Dublin Core ``metadata.json``.

The corpora are R serialisations of paper lists, which no Python package in
our dependency set reads (pyreadr only reads data frames), so this module
contains a small reader for R's XDR serialisation format (``readRDS()``)
that turns ``scivrs_paper`` objects into :class:`~pytacheck.papers.Paper`
objects. The cache directory is the one metacheck uses, so R and Python
share downloaded corpora.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import math
import shutil
import struct
import tempfile
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd

from pytacheck._r.base import r_round
from pytacheck._r.regex import sub

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
    return sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", name)


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
    from pytacheck import http

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
    if assets is None:
        return pd.DataFrame(
            {
                "name": pd.Series([], dtype="string"),
                "size_mb": pd.Series([], dtype="float64"),
                "cached": pd.Series([], dtype="boolean"),
            }
        )
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
    from pytacheck import http

    cache_dir = _papers_cache_dir()
    cache_path = cache_dir / f"{name}.rds"
    if cache and cache_path.exists() and not overwrite:
        _message(f"Loading cached {name} ...")
        return _read_rds(cache_path)

    assets = _papers_release_assets(repo)
    names = [] if assets is None else [_file_path_sans_ext(n) for n in assets["name"]]
    hits = [i for i, n in enumerate(names) if n == name]
    if not hits:
        raise ValueError(f"'{name}' not found in releases of {repo}. Available: {', '.join(names)}")
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
    from pytacheck import http

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
    from pytacheck._r.base import as_character

    return str(as_character(x))


def _message(msg: str) -> None:
    from pytacheck.config import verbose

    if verbose():
        from rich.console import Console

        Console(stderr=True).print(msg, markup=False, highlight=False)


# ---------------------------------------------------------------------------
# readRDS()
# ---------------------------------------------------------------------------

_NA_INT = -(2**31)
_REF_TYPES = frozenset({1, 4, 22, 23})  # SYMSXP, ENVSXP, EXTPTRSXP, WEAKREFSXP


class RObject:
    """An R value read from an RDS file: SEXP ``type``, ``value`` and ``attrs``."""

    __slots__ = ("attrs", "type", "value")

    def __init__(self, type_: int, value: Any, attrs: dict[str, Any] | None = None) -> None:
        self.type = type_
        self.value = value
        self.attrs = attrs or {}

    @property
    def classes(self) -> list[str]:
        cls = self.attrs.get("class")
        return list(cls.value) if isinstance(cls, RObject) else []

    def __repr__(self) -> str:
        return f"RObject(type={self.type}, value={self.value!r:.60}, attrs={list(self.attrs)})"


class _Symbol(str):
    """An R symbol (``SYMSXP``)."""


class _Env:
    """An R environment (only kept so references resolve)."""

    def __init__(self) -> None:
        self.frame: Any = None


class _RdsReader:
    """R's ``unserialize()`` for the XDR format (``readRDS()``)."""

    def __init__(self, data: bytes) -> None:
        self.buf = memoryview(data)
        self.pos = 0
        self.refs: list[Any] = []
        self.encoding = "UTF-8"

    # -- primitives -----------------------------------------------------------

    def int(self) -> int:
        (v,) = struct.unpack_from(">i", self.buf, self.pos)
        self.pos += 4
        return int(v)

    def length(self) -> int:
        n = self.int()
        if n == -1:
            hi, lo = self.int(), self.int()
            n = (hi << 32) + lo
        return n

    def bytes(self, n: int) -> bytes:
        out = bytes(self.buf[self.pos : self.pos + n])
        self.pos += n
        return out

    def string(self, flags: int) -> str | None:
        n = self.int()
        if n == -1:
            return None
        raw = self.bytes(n)
        levels = flags >> 12
        if levels & 4:  # LATIN1
            return raw.decode("latin-1")
        return raw.decode("utf-8", "replace")

    # -- items ------------------------------------------------------------------

    def header(self) -> None:
        fmt = self.bytes(2)
        if fmt != b"X\n":
            raise ValueError("only the XDR serialization format is supported")
        version = self.int()
        self.int()  # writer R version
        self.int()  # minimal reader version
        if version == 3:
            n = self.int()
            self.encoding = self.bytes(n).decode("ascii")

    def attributes(self, has_attr: bool) -> dict[str, Any]:
        if not has_attr:
            return {}
        pl = self.item()
        return dict(pl.value) if isinstance(pl, RObject) and pl.type == 2 else {}

    def item(self) -> Any:
        flags = self.int()
        t = flags & 0xFF
        has_attr = bool(flags & (1 << 9))
        has_tag = bool(flags & (1 << 10))

        if t == 254:  # NILVALUE_SXP
            return None
        if t == 255:  # REFSXP
            idx = flags >> 8
            if idx == 0:
                idx = self.int()
            return self.refs[idx - 1]
        if t in (253, 252, 251, 250, 242, 241):  # environments / markers
            return _Env() if t in (253, 250, 242, 241) else None
        if t in (249, 248, 247):  # NAMESPACESXP, PACKAGESXP, PERSISTSXP
            self.int()
            n = self.int()
            names = [self.item() for _ in range(n)]
            obj = RObject(t, names)
            self.refs.append(obj)
            return obj
        if t == 1:  # SYMSXP
            sym = _Symbol(self.item() or "")
            self.refs.append(sym)
            return sym
        if t == 4:  # ENVSXP
            env = _Env()
            self.refs.append(env)
            self.int()  # locked
            self.item()  # enclos
            env.frame = self.item()
            self.item()  # hashtab
            self.item()  # attrib
            return env
        if t in (2, 3, 5, 6, 17, 239, 240):  # pairlist-like
            return self.pairlist(t, flags, has_attr, has_tag)
        if t == 238:  # ALTREP_SXP
            info = self.item()
            state = self.item()
            attrs = self.item()
            return self.altrep(info, state, attrs)
        if t == 9:  # CHARSXP
            return self.string(flags)
        if t in (10, 13):  # LGLSXP, INTSXP
            n = self.length()
            vals = struct.unpack_from(f">{n}i", self.buf, self.pos)
            self.pos += 4 * n
            if t == 10:
                value: Any = [None if v == _NA_INT else bool(v) for v in vals]
            else:
                value = [None if v == _NA_INT else v for v in vals]
            return RObject(t, value, self.attributes(has_attr))
        if t == 14:  # REALSXP
            n = self.length()
            vals = struct.unpack_from(f">{n}d", self.buf, self.pos)
            self.pos += 8 * n
            return RObject(t, list(vals), self.attributes(has_attr))
        if t == 15:  # CPLXSXP
            n = self.length()
            vals = struct.unpack_from(f">{2 * n}d", self.buf, self.pos)
            self.pos += 16 * n
            return RObject(
                t,
                [complex(vals[2 * i], vals[2 * i + 1]) for i in range(n)],
                self.attributes(has_attr),
            )
        if t == 16:  # STRSXP
            n = self.length()
            out: list[str | None] = []
            unpack, buf = struct.unpack_from, self.buf
            for _ in range(n):
                (cflags, clen) = unpack(">ii", buf, self.pos)
                self.pos += 8
                if clen == -1:
                    out.append(None)
                    continue
                raw = bytes(buf[self.pos : self.pos + clen])
                self.pos += clen
                out.append(
                    raw.decode("latin-1") if (cflags >> 12) & 4 else raw.decode("utf-8", "replace")
                )
            return RObject(t, out, self.attributes(has_attr))
        if t in (19, 20):  # VECSXP, EXPRSXP
            n = self.length()
            items = [self.item() for _ in range(n)]
            return RObject(t, items, self.attributes(has_attr))
        if t == 24:  # RAWSXP
            n = self.length()
            return RObject(t, self.bytes(n), self.attributes(has_attr))
        if t == 25:  # S4SXP
            return RObject(t, None, self.attributes(has_attr))
        if t in (7, 8):  # SPECIALSXP, BUILTINSXP
            n = self.int()
            return RObject(t, self.bytes(n).decode("ascii"))
        if t in (22, 23):  # EXTPTRSXP, WEAKREFSXP
            obj = RObject(t, None)
            self.refs.append(obj)
            if t == 22:
                self.item()  # protected value
                self.item()  # tag
            obj.attrs = self.attributes(has_attr)
            return obj
        raise ValueError(f"unsupported R object type {t} in RDS stream")

    def pairlist(self, t: int, flags: int, has_attr: bool, has_tag: bool) -> RObject:
        # iterative over the cdr chain to avoid deep recursion on long pairlists
        entries: list[tuple[Any, Any]] = []
        attrs: dict[str, Any] = {}
        first = True
        while True:
            a = self.attributes(has_attr)
            if first:
                attrs = a
                first = False
            tag = self.item() if has_tag else None
            car = self.item()
            entries.append((str(tag) if tag is not None else None, car))
            nflags = self.int()
            nt = nflags & 0xFF
            if nt != t:
                self.pos -= 4
                self.item()  # the terminating cdr (usually NULL)
                break
            has_attr = bool(nflags & (1 << 9))
            has_tag = bool(nflags & (1 << 10))
        del flags
        return RObject(t, entries, attrs)

    def altrep(self, info: Any, state: Any, attrs: Any) -> Any:
        cls = str(info.value[0][1]) if isinstance(info, RObject) and info.value else ""
        attr_dict = dict(attrs.value) if isinstance(attrs, RObject) and attrs.type == 2 else {}
        if cls in ("compact_intseq", "compact_realseq"):
            n, start, inc = [*state.value, 0, 0, 1][:3]
            n = int(n)
            if cls == "compact_intseq":
                vals: list[Any] = [int(start) + int(inc) * i for i in range(n)]
                return RObject(13, vals, attr_dict)
            return RObject(14, [float(start) + float(inc) * i for i in range(n)], attr_dict)
        if cls.startswith("wrap_"):
            inner = state.value[0][1] if isinstance(state, RObject) and state.type == 2 else state
            if isinstance(inner, RObject):
                return RObject(inner.type, inner.value, attr_dict or inner.attrs)
            return inner
        if cls == "deferred_string":
            arg = state.value[0][1] if isinstance(state, RObject) and state.type == 2 else state
            from pytacheck._r.base import as_character

            values = arg.value if isinstance(arg, RObject) else []
            return RObject(16, [as_character(v) for v in values], attr_dict)
        raise ValueError(f"unsupported ALTREP class {cls!r} in RDS stream")


def _decompress(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:3] == b"BZh":
        return bz2.decompress(raw)
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw)
    return raw


def _dates(obj: RObject) -> list[Any] | None:
    """``Date`` / ``POSIXct`` vectors as ``datetime`` values (``None`` if neither)."""
    import datetime as dt

    classes = obj.classes
    if obj.type not in (13, 14) or not ({"Date", "POSIXct"} & set(classes)):
        return None
    out: list[Any] = []
    for v in obj.value:
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
    if t == 13 and "factor" in obj.classes:
        levels = obj.attrs["levels"].value
        return pd.Series([None if v is None else levels[v - 1] for v in obj.value], dtype="string")
    if t == 16:
        return pd.Series(obj.value, dtype="string")
    if t == 13:
        return pd.Series(obj.value, dtype="Int64")
    if t == 10:
        return pd.Series(obj.value, dtype="boolean")
    if t == 14:
        return pd.Series([math.nan if v is None else v for v in obj.value], dtype="float64")
    return pd.Series([_to_python(v) for v in obj.value], dtype=object)


def _names(obj: RObject) -> list[str] | None:
    names = obj.attrs.get("names")
    return list(names.value) if isinstance(names, RObject) else None


def _data_frame(obj: RObject) -> pd.DataFrame:
    names = _names(obj) or [f"V{i + 1}" for i in range(len(obj.value))]
    nrow = 0
    rn = obj.attrs.get("row.names")
    if isinstance(rn, RObject):
        if rn.type == 13 and len(rn.value) == 2 and rn.value[0] is None:
            nrow = abs(rn.value[1])
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
        return list(v.value) if v.type != 14 else [math.nan if x is None else x for x in v.value]
    return _to_python(v)


def _paper(obj: RObject) -> Any:
    from pytacheck.papers.io import coerce_paper
    from pytacheck.papers.model import Paper

    names = _names(obj) or []
    elements = dict(zip(names, obj.value, strict=True))
    pid = elements.get("paper_id")
    paper_id = pid.value[0] if isinstance(pid, RObject) and pid.value else None
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
            from pytacheck.papers.model import Paper, PaperList

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
        values = obj.value if dates is None else dates
        if obj.type == 14 and dates is None:
            values = [math.nan if v is None else v for v in values]
        names = _names(obj)
        if names is not None:  # a named vector: a Series indexed by the names
            series = _vector_series(obj)
            series.index = pd.Index(names)
            return series
        return list(values)
    if obj.type == 2:
        return {k: _to_python(v) for k, v in obj.value}
    return obj


def _read_rds(file: str | PathLike[str]) -> Any:
    """``readRDS()``: read an R serialisation file into Python objects.

    Paper lists and papers become :class:`~pytacheck.papers.PaperList` /
    :class:`~pytacheck.papers.Paper`, data frames become DataFrames, named
    lists dicts and vectors lists. Supports the XDR format (versions 2 and
    3) with gzip, bzip2 or xz compression.
    """
    raw = _decompress(Path(file).read_bytes())
    reader = _RdsReader(raw)
    reader.header()
    return _to_python(reader.item())
