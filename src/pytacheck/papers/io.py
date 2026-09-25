"""Reading and writing bibr JSON (``.read_bibr()``, ``paper_write()``, ...)."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from importlib import resources
from os import PathLike
from pathlib import Path
from typing import Any

import orjson
import pandas as pd

from pytacheck._r.base import as_character
from pytacheck._r.regex import gsub, is_na
from pytacheck.papers.model import Paper, PaperList
from pytacheck.papers.schema import coerce_table, records_to_frame

__all__ = [
    "demofile",
    "demopaper",
    "from_bibr",
    "paper",
    "paper_write",
    "read_bibr",
    "test_paper",
]

# Tables .read_bibr() copies from the JSON, in the order it reads them; any
# other top-level key (info_match, funding, ...) is kept in `Paper.extra`.
_BIBR_TABLES = ("author", "bib", "eq", "figure", "url", "section", "table", "text", "xref")


def _dollar(x: Any, name: str) -> Any:
    """R's ``x$name`` on a parsed JSON object: an exact key, else the one key it prefixes.

    metacheck's readers use ``$``, so a file without ``bib`` but with
    ``bib_match`` reads its ``bib_match`` rows as ``bib`` too.
    """
    if not isinstance(x, Mapping):
        return None
    if name in x:
        return x[name]
    hits = [k for k in x if isinstance(k, str) and k.startswith(name)]
    return x[hits[0]] if len(hits) == 1 else None


def paper(paper_id: str | None = None) -> Paper:
    """``metacheck::paper()``: an empty paper with every required table."""
    return Paper(paper_id)


def _info_frame(info: Any) -> pd.DataFrame:
    """Reproduce the ``info`` handling of ``.read_bibr()``.

    ``info`` is an object in current bibr output and a one-element array in
    older output; both become a one-row table whose ``keywords`` cell holds
    the original keywords value, with ``abstract`` dropped.
    """
    if isinstance(info, list):
        record = info[0] if info else {}
        # R: info$keywords of the data frame jsonlite makes is the column, so
        # a flat array is wrapped in one more list (list(c("a", "b"))); an
        # array of one array (what paper_write() writes) is a 1-row matrix,
        # which the nested list already is
        keywords: Any = record.get("keywords")
        if isinstance(keywords, list) and not any(isinstance(e, list | dict) for e in keywords):
            keywords = [keywords]
        row = dict(record)
    elif isinstance(info, Mapping):
        row = dict(info)
        keywords = row.get("keywords")
        # empty values (null, [], {}) become NA
        for key, value in row.items():
            if value is None or (isinstance(value, list | dict) and len(value) == 0):
                row[key] = None
    else:
        row, keywords = {}, None
    if "keywords" in row:
        row["keywords"] = None
    row.pop("abstract", None)
    frame = records_to_frame("info", [row], list(row))
    if "keywords" in frame.columns:
        frame["keywords"] = pd.Series([keywords], dtype=object)
    else:
        frame["keywords"] = pd.Series([keywords], dtype=object)
    return frame


def _clean_url_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in records:
        r = dict(r)
        href = r.get("href")
        if href is not None:
            href = gsub(r"\s", "", str(href))
            href = gsub(r"\.$", "", href)
        r["href"] = href
        out.append(r)
    return out


def _union_columns(records: Sequence[Mapping[str, Any]]) -> list[str]:
    order: dict[str, None] = {}
    for r in records:
        for k in r:
            order.setdefault(k, None)
    return list(order)


def _is_record_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(e, Mapping) for e in v)


def _empty_record_frames(records: list[dict[str, Any]], columns: Sequence[str]) -> None:
    """jsonlite's simplification of a column of arrays of objects, in place.

    When every row holds an array whose elements are all objects (an empty
    array included) and one of them is not empty, jsonlite makes each cell a
    data frame, so an empty array is a 0 x 0 data frame, not ``list()``. The
    non-empty cells stay lists of records (the same data to the parity
    harness and to pytacheck's code).
    """
    for col in columns:
        cells = [r.get(col, None) for r in records]
        if (
            cells
            and all(_is_record_list(v) for v in cells)
            and any(len(v) for v in cells)
            and any(not len(v) for v in cells)
        ):
            for r in records:
                if not r[col]:
                    r[col] = pd.DataFrame()


def from_bibr(data: Mapping[str, Any] | Any, include_images: bool = False) -> Paper:
    """Build a :class:`Paper` from parsed bibr JSON (a dict) or a ``bibr.Result``.

    This is the in-memory equivalent of ``.read_bibr()`` and is what the
    bibr integration uses. Output of current bibr (bibr export schema 12.x, a
    root ``schema_version``) is read natively as a 12.x paper
    (:mod:`pytacheck.io.bibr12`), any other root ``schema_version`` raises
    metacheck's error, and older payloads are read exactly as metacheck does.
    """
    if not isinstance(data, Mapping):
        data = getattr(data, "data", data)
    if not isinstance(data, Mapping):
        raise TypeError("from_bibr() needs a dict of bibr JSON or a bibr.Result")
    if _dollar(data, "schema_version") is not None:
        from pytacheck.io.bibr12 import _bibr12_from_json

        # the JSON values metacheck would read, detached from the caller's dict
        try:
            data = orjson.loads(orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY))
        except TypeError:
            data = dict(data)
        return _bibr12_from_json(data, include_images, str(data.get("paper_id")))
    return _from_bibr_legacy(data, include_images)


def _from_bibr_legacy(data: Mapping[str, Any], include_images: bool) -> Paper:
    """``.read_bibr()`` of a file without a root ``schema_version`` (bibr v10.x and older)."""
    p = Paper(_dollar(data, "paper_id"))
    # R: paper$paper_id <- data$paper_id (NULL when missing)
    p.paper_id = _dollar(data, "paper_id")
    # R: info <- data$info (NULL when absent) still yields a one-row table
    p.info = _info_frame(_dollar(data, "info"))

    for name in _BIBR_TABLES:
        records = _dollar(data, name)  # data$bib is data$bib_match without a bib key
        if not records:
            continue
        records = [dict(r) for r in records]
        if name == "url":
            records = _clean_url_records(records)
        columns = _union_columns(records)
        if name == "figure":
            for r in records:
                if not include_images:
                    r["image"] = None
                r.pop("caption", None)
            if "image" not in columns:
                columns.append("image")
            columns = [c for c in columns if c != "caption"]
        if name == "table":
            for r in records:
                r.pop("caption", None)
                contents = r.get("contents")
                if contents is not None:
                    r["contents"] = [
                        list(row) if isinstance(row, list) else row for row in contents
                    ]
            columns = [c for c in columns if c != "caption"]
        _empty_record_frames(records, columns)
        p._set_raw(name, records, columns)

    bib_match = _dollar(data, "bib_match")
    if bib_match:
        records = [dict(r) for r in bib_match]
        columns = _union_columns(records)
        _empty_record_frames(records, columns)
        p._tables["bib_match"] = None
        p._set_raw("bib_match", records, columns)

    known = {"paper_id", "info", "bib_match", *_BIBR_TABLES}
    p.extra.update({k: v for k, v in data.items() if k not in known})
    return p


def read_bibr(file_path: str | PathLike[str], include_images: bool = False) -> Paper:
    """``.read_bibr()``: read one bibr JSON file into a :class:`Paper`.

    A file with a root ``schema_version`` (bibr export schema 11 and later)
    goes to the 12.x reader (:func:`pytacheck.io.bibr12.read_bibr12`), which
    reads 12.x and raises metacheck's error for any other version; files
    without one read exactly as before.
    """
    data = orjson.loads(Path(file_path).read_bytes())
    if isinstance(data, Mapping) and _dollar(data, "schema_version") is not None:
        from pytacheck.io.bibr12 import _bibr12_from_json

        return _bibr12_from_json(data, include_images, os.path.basename(file_path))
    return _from_bibr_legacy(data, include_images)


def test_paper(text: Sequence[str] | None = None, url: Sequence[str] = ()) -> Paper:
    """``test_paper()``: a paper with the given sentences (default A-Z)."""
    if text is None:
        text = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    if isinstance(text, str):
        text = [text]
    if isinstance(url, str):
        url = [url]
    p = Paper()
    n = len(text)
    # as.character(): NA stays NA, numbers use R's formatting
    chars = [None if is_na(t) else t if isinstance(t, str) else as_character(t) for t in text]
    p.text = pd.DataFrame(
        {
            "text_id": pd.Series(range(1, n + 1), dtype="Int64"),
            "section_id": pd.Series([0.0] * n, dtype="float64"),
            "paragraph_id": pd.Series([0.0] * n, dtype="float64"),
            "text": pd.Series(chars, dtype="string"),
            "formatted": pd.Series(chars, dtype="string"),
        }
    )
    p.section = pd.DataFrame(
        {
            "section_id": pd.Series([0.0], dtype="float64"),
            "header": pd.Series(["Test"], dtype="string"),
            "parent_section_id": pd.Series([pd.NA], dtype="Int64"),
            "section_type": pd.Series(["unknown"], dtype="string"),
            "classification_score": pd.Series([0.0], dtype="float64"),
        }
    )
    # R: p$info[1, ] <- NA on the empty schema table, then fill three cells
    info = records_to_frame("info", [dict.fromkeys(p.info.columns)], list(p.info.columns))
    info["title"] = pd.Series(["Test Paper"], dtype="string")
    info["file_hash"] = pd.Series([p.paper_id], dtype="string")
    info["input_format"] = pd.Series(["test"], dtype="string")
    info["keywords"] = pd.Series([None], dtype=object)
    p.info = info
    p.url = pd.DataFrame(
        {
            "href": pd.Series(list(url), dtype="string"),
            "link_text": pd.Series([pd.NA] * len(url), dtype="string"),
            "text_id": pd.Series(range(1, len(url) + 1), dtype="Int64"),
        }
    )
    return p


def demofile(ext: str = "json") -> Path:
    """Path to a version of the demo paper (``json``, ``pdf``, ``docx``, ``doc``, ``xml``, ``qmd``)."""
    allowed = ("json", "pdf", "docx", "doc", "xml", "qmd")
    if ext not in allowed:
        raise ValueError(f"'arg' should be one of {', '.join(repr(a) for a in allowed)}")
    ref = resources.files("pytacheck.resources.demos").joinpath(f"to_err_is_human.{ext}")
    return Path(str(ref))


def demopaper() -> Paper:
    """The demo paper "To Err is Human" (``demopaper()``)."""
    return read_bibr(demofile("json"))


def _json_default(obj: Any) -> Any:
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, pd.DataFrame):
        return _frame_records(obj)
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"cannot serialise {type(obj).__name__}")


def _clean(v: Any) -> Any:
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _frame_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    cols = list(df.columns)
    return [
        {c: _clean(v) for c, v in zip(cols, row, strict=True)}
        for row in df.itertuples(index=False, name=None)
    ]


def paper_to_json(p: Paper) -> dict[str, Any]:
    """The paper as bibr-shaped JSON data (tables as arrays of row objects)."""
    out: dict[str, Any] = {"paper_id": p.paper_id}
    for name, value in p.items():
        if name == "info" and isinstance(value, pd.DataFrame):
            recs = _frame_records(value)
            out[name] = recs
        elif isinstance(value, pd.DataFrame):
            out[name] = _frame_records(value)
        else:
            out[name] = value
    out.update(p.extra)
    return out


def paper_write(
    paper: Paper | PaperList,
    file_name: str | Sequence[str] | None = None,
    save_path: str | PathLike[str] = ".",
    schema_version: str | None = "auto",
) -> Path | list[Path]:
    """``paper_write()``: save paper(s) as pretty-printed JSON; returns the path(s).

    ``schema_version`` chooses the format:

    * ``"12.0"``: a bibr export schema 12.0 file, as metacheck's
      ``paper_write(schema_version = "12.0")`` writes it, for a paper read from
      a bibr 12.0 export (or converted from Grobid TEI to 12.0). It keeps the
      paper's extraction block (a bibr export keeps bibr as its producer and
      the time bibr extracted it) and names pytacheck as the converter. A paper
      in the older format, or read from a later 12.x, is refused.
    * ``None``: metacheck's default: the paper object as it is.
    * ``"auto"`` (the default, a deliberate pytacheck difference: metacheck's
      default is ``NULL``): ``"12.0"`` for a 12.x paper and ``None`` for a
      paper in the older format, so a paper read from a bibr export is saved
      as one. (A paper read from a later 12.x, e.g. 12.1, is refused, as with
      ``"12.0"``; pass ``None`` to save it as a paper object.)

    A :class:`PaperList` is written paper by paper with the same setting; its
    file names default to the paper IDs and are recycled as ``mapply()`` does
    (one name writes every paper to the same file, as in metacheck). One
    ``.json`` or ``.zip`` suffix is dropped from a file name.
    """
    if schema_version not in ("auto", None, "12.0"):
        raise ValueError('schema_version must be "auto", None or "12.0"')
    save_dir = Path(save_path).resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(paper, PaperList):
        if file_name is None:
            names = [str(n) for n in paper.names]
        elif isinstance(file_name, str | PathLike):
            names = [str(file_name)]
        else:
            names = [str(f) for f in file_name]
        if not names and len(paper):
            raise ValueError("zero-length inputs cannot be mixed with those of non-zero length")
        # R: mapply() recycles the file names over the papers
        return [
            Path(paper_write(q, names[i % len(names)], save_dir, schema_version))  # type: ignore[arg-type]
            for i, q in enumerate(paper)
        ]
    p = paper
    name = str(file_name) if file_name is not None else str(p.paper_id)
    # R: gsub("\\.(json|zip)$", "", file_name) drops one suffix
    for suffix in (".json", ".zip"):
        if name.endswith(suffix):
            name = name.removesuffix(suffix)
            break
    path = save_dir / f"{name}.json"
    if schema_version == "auto":
        from pytacheck.io.bibr12 import is_bibr12

        schema_version = "12.0" if is_bibr12(p) else None
    if schema_version is not None:
        from pytacheck.io.bibr12 import write_bibr12

        return write_bibr12(p, path)
    payload = orjson.dumps(
        paper_to_json(p),
        default=_json_default,
        option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY,
    )
    path.write_bytes(payload)
    return path


def coerce_paper(p: Paper) -> Paper:
    """``.paper_coerce()``: coerce every schema table's columns in place."""
    for name in list(p.keys()):
        if p._raw_records(name) is not None:
            continue  # coerced when materialised
        value = p[name]
        if isinstance(value, pd.DataFrame):
            coerce_table(name, value)
    return p
