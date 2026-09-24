"""Reading and writing bibr JSON (``.read_bibr()``, ``paper_write()``, ...)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from importlib import resources
from os import PathLike
from pathlib import Path
from typing import Any

import orjson
import pandas as pd

from pytacheck._r.regex import gsub
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
        keywords: Any = record.get("keywords")
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


def from_bibr(data: Mapping[str, Any] | Any, include_images: bool = False) -> Paper:
    """Build a :class:`Paper` from parsed bibr JSON (a dict) or a ``bibr.Result``.

    This is the in-memory equivalent of ``.read_bibr()`` and is what the
    bibr integration uses, so no JSON round trip is needed.
    """
    if not isinstance(data, Mapping):
        data = getattr(data, "data", data)
    if not isinstance(data, Mapping):
        raise TypeError("from_bibr() needs a dict of bibr JSON or a bibr.Result")

    p = Paper(data.get("paper_id"))
    # R: paper$paper_id <- data$paper_id (NULL when missing)
    p.paper_id = data.get("paper_id")
    if "info" in data:
        p.info = _info_frame(data["info"])

    for name in _BIBR_TABLES:
        records = data.get(name)
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
        p._set_raw(name, records, columns)

    bib_match = data.get("bib_match")
    if bib_match:
        records = [dict(r) for r in bib_match]
        p._tables["bib_match"] = None
        p._set_raw("bib_match", records, _union_columns(records))

    known = {"paper_id", "info", "bib_match", *_BIBR_TABLES}
    p.extra.update({k: v for k, v in data.items() if k not in known})
    return p


def read_bibr(file_path: str | PathLike[str], include_images: bool = False) -> Paper:
    """``.read_bibr()``: read one bibr JSON file into a :class:`Paper`."""
    raw = Path(file_path).read_bytes()
    return from_bibr(orjson.loads(raw), include_images=include_images)


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
    p.text = pd.DataFrame(
        {
            "text_id": pd.Series(range(1, n + 1), dtype="Int64"),
            "section_id": pd.Series([0.0] * n, dtype="float64"),
            "paragraph_id": pd.Series([0.0] * n, dtype="float64"),
            "text": pd.Series([str(t) for t in text], dtype="string"),
            "formatted": pd.Series([str(t) for t in text], dtype="string"),
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
    p: Paper | PaperList,
    file_name: str | Sequence[str] | None = None,
    save_path: str | PathLike[str] = ".",
) -> Path | list[Path]:
    """``paper_write()``: save paper(s) as pretty-printed JSON; returns the path(s)."""
    save_dir = Path(save_path).resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(p, PaperList):
        names = list(file_name) if file_name is not None else [str(n) for n in p.names]
        return [Path(paper_write(q, f, save_dir)) for q, f in zip(p, names, strict=True)]  # type: ignore[arg-type]
    name = str(file_name) if file_name is not None else str(p.paper_id)
    for suffix in (".json", ".zip"):
        name = name.removesuffix(suffix)
    path = save_dir / f"{name}.json"
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
