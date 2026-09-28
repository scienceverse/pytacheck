"""The parts of an OSF project that are not files (port of ``R/archive-osf-metadata.R``).

``osf_file_download(metadata = True)`` writes, into ``_osf_metadata`` inside
a project's download folder: each wiki page as Markdown, the activity log as
``logs.csv``, the descriptive metadata as ``metadata.json`` and a readable
``README.md`` summarising them.
"""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime
from typing import Any, cast

import pandas as pd

from pytacheck._r import is_na, plural

#: R/archive-osf-metadata.R::.osf_meta_dir
_OSF_META_DIR = "_osf_metadata"


def _api() -> str:
    from pytacheck.utils import get_option

    return str(get_option("metacheck.osf.api"))


def _osf_meta_get(url: str) -> Any:
    """Port of R/archive-osf-metadata.R::.osf_meta_get(): one listing, or ``None``."""
    from pytacheck.archives.osf import _osf_err, osf_get_all_pages

    try:
        out = osf_get_all_pages(url)
    except Exception:
        return None
    if out is None or _osf_err(out) is not None or len(out) == 0:
        return None
    return out


def _get_request(url: str, accept: str | None = None) -> Any:
    """GET with OSF headers, errors returned rather than raised (``None`` on failure)."""
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers

    headers = _osf_headers()["headers"]
    if accept is not None:
        headers["Accept"] = accept
    try:
        return http.request("GET", url, headers=headers, max_tries=1)
    except Exception:
        return None


def _write_lines(text: str | list[str], path: str) -> None:
    """R ``writeLines(x, path, useBytes = TRUE)``."""
    lines = [text] if isinstance(text, str) else text
    with open(path, "wb") as fh:
        for line in lines:
            fh.write(("NA" if line is None else line).encode("utf-8") + b"\n")


def _osf_download_wikis(osf_id: str, meta_dir: str) -> pd.DataFrame | None:
    """Port of R/archive-osf-metadata.R::.osf_download_wikis(): each wiki page as Markdown.

    Writes ``wiki_<name>.md`` (current version only) and returns a table of
    the pages (``wiki_id``, ``name``, ``size``, ``date_modified``, ``file``),
    or ``None`` when the node has no wiki.
    """
    from pytacheck.archives.osf_helpers import _get
    from pytacheck.utils import path_sanitize

    wikis = _osf_meta_get(f"{_api()}/nodes/{osf_id}/wikis/")
    if wikis is None or not isinstance(wikis, list) or len(wikis) == 0:
        return None

    names = [_get(w, "attributes", "name") for w in wikis]
    urls = [_get(w, "links", "download") for w in wikis]
    written = [""] * len(wikis)
    for i, (name, url) in enumerate(zip(names, urls, strict=True)):
        if url is None or url == "":
            continue
        resp = _get_request(url, accept="text/markdown, text/plain, */*")
        if resp is None or resp.status_code != 200:
            continue
        sanitized = path_sanitize(name if name is not None else None, keep_sep=False)
        fname = f"wiki_{'NA' if sanitized is None else sanitized}.md"
        _write_lines(resp.text, os.path.join(meta_dir, fname))
        written[i] = fname

    def col(*path: str) -> list[Any]:
        return [_get(w, *path) for w in wikis]

    return pd.DataFrame(
        {
            "wiki_id": pd.Series(col("id"), dtype="string"),
            "name": pd.Series(names, dtype="string"),
            "size": pd.Series(col("attributes", "size"), dtype="Int64"),
            "date_modified": pd.Series(col("attributes", "date_modified"), dtype="string"),
            "file": pd.Series(written, dtype="string"),
        }
    )


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, str | int | float | bool)


def _osf_download_logs(osf_id: str, meta_dir: str) -> pd.DataFrame | None:
    """Port of R/archive-osf-metadata.R::.osf_download_logs(): the activity log as CSV.

    Every entry's ``date`` and ``action``, plus each ``params`` field that is
    a simple value in every entry (nested fields are left out). Written to
    ``logs.csv``; returns the table, or ``None`` when it could not be read.
    """
    from pytacheck.archives.osf_helpers import _get

    logs = _osf_meta_get(f"{_api()}/nodes/{osf_id}/logs/")
    if logs is None or not isinstance(logs, list) or len(logs) == 0:
        return None

    n = len(logs)
    dates = [_get(e, "attributes", "date") for e in logs]
    actions = [_get(e, "attributes", "action") for e in logs]
    out: dict[str, list[Any]] = {"date": dates, "action": actions}

    params = [_get(e, "attributes", "params") for e in logs]
    if all(p is None or isinstance(p, dict) for p in params) and any(
        isinstance(p, dict) for p in params
    ):
        keys: list[str] = []
        for p in params:
            for k in p or {}:
                if k not in keys:
                    keys.append(k)
        for k in keys:
            values = [(p or {}).get(k) for p in params]
            if all(_is_scalar(v) for v in values):
                out[k] = values  # R: out[[nm]] <- v (a "date" param would replace date)
    frame = pd.DataFrame({k: _typed(v) for k, v in out.items()}, index=pd.RangeIndex(n))
    _write_csv(frame, os.path.join(meta_dir, "logs.csv"))
    return frame


def _typed(values: list[Any]) -> pd.Series:
    """A column typed the way ``jsonlite`` simplifies a JSON array of scalars."""
    kinds = {type(v) for v in values if v is not None}
    if not kinds or kinds == {bool}:
        return pd.Series(values, dtype="boolean")
    if kinds <= {int}:
        return pd.Series(values, dtype="Int64")
    if kinds <= {int, float}:
        return pd.Series([None if v is None else float(v) for v in values], dtype="float64")
    if kinds == {str}:
        return pd.Series(values, dtype="string")
    return pd.Series([None if v is None else _csv_value(v) for v in values], dtype="string")


def _csv_value(v: Any) -> str:
    """One cell as ``readr::write_csv()`` writes it."""
    if v is None or (not isinstance(v, str) and is_na(v)):
        return "NA"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        if math.isinf(v):
            return "Inf" if v > 0 else "-Inf"
        return repr(v).replace("e+", "e")
    return str(v)


def _write_csv(df: pd.DataFrame, path: str) -> None:
    """``readr::write_csv()``: ``NA`` for missing, quotes only where needed."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(list(df.columns))
        for row in df.itertuples(index=False, name=None):
            writer.writerow(
                [_csv_value(None if (not isinstance(v, str) and is_na(v)) else v) for v in row]
            )


def _osf_license(osf_id: str) -> str | None:
    """Port of R/archive-osf-metadata.R::.osf_license(): a project's licence name.

    One request (``?embed=license``); ``None`` when no licence is set or the
    project could not be read.
    """
    from pytacheck.archives.osf_helpers import _data_of, _get, _resp_body_json

    resp = _get_request(f"{_api()}/nodes/{osf_id}/?embed=license")
    node = None
    if resp is not None and resp.status_code == 200:
        try:
            node = _data_of(_resp_body_json(resp))
        except Exception:
            node = None
    name = _get(node, "embeds", "license", "data", "attributes", "name")
    if isinstance(name, list):
        return cast("str | None", name[0] if name else None)
    return cast("str | None", name)


def _osf_node_metadata(osf_id: str) -> dict[str, Any]:
    """Port of R/archive-osf-metadata.R::.osf_node_metadata(): a project's record.

    The node's attributes, contributors (with ORCIDs), licence, citation,
    tags, and the IDs of its registrations and forks, as a dict ready to be
    written as JSON.
    """
    from pytacheck.archives.osf_helpers import _data_of, _get, _resp_body_json

    api = _api()
    resp = _get_request(f"{api}/nodes/{osf_id}/?embed=license&embed=bibliographic_contributors")
    node = None
    if resp is not None and resp.status_code == 200:
        try:
            node = _data_of(_resp_body_json(resp))
        except Exception:
            node = None
    att = _get(node, "attributes") or {}

    contributors: list[dict[str, Any]] = []
    contrib = _get(node, "embeds", "bibliographic_contributors", "data")
    users = (
        [_get(c, "embeds", "users", "data", "attributes") for c in contrib]
        if isinstance(contrib, list)
        else []
    )
    if any(_get(u, "full_name") is not None for u in users):
        contributors = [
            {
                "name": _get(u, "full_name"),
                "given_name": _get(u, "given_name"),
                "family_name": _get(u, "family_name"),
                "orcid": _get(u, "social", "orcid"),
            }
            for u in users
        ]

    citation = _osf_meta_get(f"{api}/nodes/{osf_id}/citation/")
    regs = _osf_meta_get(f"{api}/nodes/{osf_id}/registrations/")
    forks = _osf_meta_get(f"{api}/nodes/{osf_id}/forks/")

    def cite_attrs(x: Any) -> Any:
        if x is None:
            return None
        if isinstance(x, dict):
            return x.get("attributes")
        return [r.get("attributes") for r in x if isinstance(r, dict)]

    tags = att.get("tags")
    return {
        "osf_id": osf_id,
        "osf_url": f"https://osf.io/{osf_id}",
        "title": att.get("title"),
        "description": att.get("description"),
        "category": att.get("category"),
        "public": att.get("public"),
        "date_created": att.get("date_created"),
        "date_modified": att.get("date_modified"),
        "tags": tags if tags is not None else [],
        "license": _get(node, "embeds", "license", "data", "attributes", "name"),
        "node_license": att.get("node_license"),
        "contributors": contributors,
        "citation": cite_attrs(citation),
        "registrations": [r.get("id") for r in regs] if isinstance(regs, list) else None,
        "forks": [r.get("id") for r in forks] if isinstance(forks, list) else None,
        "retrieved": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _said(x: Any, fallback: str) -> str:
    if isinstance(x, list | tuple):
        x = x[0] if x else None
    if x is None or (not isinstance(x, str) and is_na(x)) or x == "":
        return fallback
    from pytacheck._r import as_character

    return as_character(x) or fallback


def _osf_write_readme(
    meta: dict[str, Any],
    wikis: pd.DataFrame | None,
    logs: pd.DataFrame | None,
    meta_dir: str,
) -> str:
    """Port of R/archive-osf-metadata.R::.osf_write_readme(): a readable ``README.md``."""
    ln = [
        f"# {_said(meta.get('title'), str(meta.get('osf_id')))}",
        "",
        f"Archived from <{meta.get('osf_url')}> on {meta.get('retrieved')}.",
        "",
    ]
    desc = _said(meta.get("description"), "")
    if desc != "":
        ln += ["## Description", "", desc, ""]

    details = [
        f"- OSF ID: {meta.get('osf_id')}",
        f"- Category: {_said(meta.get('category'), 'unknown')}",
        f"- Visibility: {'public' if meta.get('public') is True else 'private'}",
        f"- Created: {_said(meta.get('date_created'), 'unknown')}",
        f"- Last modified: {_said(meta.get('date_modified'), 'unknown')}",
        f"- License: {_said(meta.get('license'), 'none recorded on the OSF')}",
    ]
    tags = meta.get("tags") or []
    if len(tags) > 0:
        details.append(f"- Tags: {', '.join(str(t) for t in tags)}")
    ln += ["## Project", "", *details, ""]

    contributors = meta.get("contributors") or []
    if contributors:
        who = []
        for c in contributors:
            orcid = c.get("orcid")
            name = c.get("name")
            name = "NA" if name is None or (not isinstance(name, str) and is_na(name)) else name
            if (
                orcid is not None
                and not (not isinstance(orcid, str) and is_na(orcid))
                and orcid != ""
            ):
                who.append(f"- {name} (ORCID {orcid})")
            else:
                who.append(f"- {name}")
        ln += ["## Contributors", "", *who, ""]

    files = [] if wikis is None else [f for f in wikis["file"].tolist() if f]
    if wikis is not None and len(wikis) > 0 and files:
        contents = [f"- {len(files)} wiki page{plural(len(files))}: {', '.join(files)}"]
    else:
        contents = ["- Wiki pages: none (this project has no wiki)"]
    if logs is not None and len(logs) > 0:
        dates = [d for d in logs["date"].tolist() if not is_na(d)]
        first = str(min(dates))[:10] if dates else "Inf"
        last = str(max(dates))[:10] if dates else "-Inf"
        n = len(logs)
        contents.append(
            f"- logs.csv: {n} activity log entr{'y' if n == 1 else 'ies'}, {first} to {last}"
        )
    else:
        contents.append("- logs.csv: empty (no activity log could be retrieved)")
    contents.append(
        "- metadata.json: the project's title, description, tags, licence, contributors, "
        "dates and citation, as the OSF API returned them"
    )
    ln += ["## This folder", "", *contents, ""]

    path = os.path.join(meta_dir, "README.md")
    _write_lines(ln, path)
    return path


# ---------------------------------------------------------------------------
# jsonlite::write_json(auto_unbox = TRUE, pretty = TRUE, null = "null")
# ---------------------------------------------------------------------------
# metadata.json is written from R values, some built by metacheck and some
# parsed from the API with jsonlite's simplification. The writer below works
# on a small tagged tree so both kinds come out as jsonlite writes them:
#   ("vec", type, values)  atomic vector (inline; a length-1 vector is unboxed)
#   ("list", items)        unnamed list (one item per line)
#   ("obj", {key: node})   named list
#   ("df", rows)           data frame, one object per row (see _jl_df_rows)
#   ("matrix", rows)       matrix, one inline array per row
#   ("null",)              NULL

_JNode = tuple[Any, ...]
_NULL: _JNode = ("null",)


def _is_scalar_value(v: Any) -> bool:
    return v is None or isinstance(v, str | int | float | bool)


def _jl_vec(values: list[Any]) -> _JNode:
    """``jsonlite:::list_to_vec()``: ``unlist()`` of JSON scalars (``null`` is ``NA``).

    The common type is the highest of logical < integer < double < character.
    """
    kinds = {type(v) for v in values if v is not None}
    if not kinds or kinds == {bool}:
        return ("vec", "lgl", values)
    if str in kinds:
        return ("vec", "chr", [None if v is None else _jl_chr(v) for v in values])
    if float in kinds:
        return ("vec", "dbl", [None if v is None else float(v) for v in values])
    return ("vec", "int", [None if v is None else int(v) for v in values])


def _jl_chr(v: Any) -> str:
    """``as.character()`` of a JSON scalar (a double as R prints it, 15 digits)."""
    from pytacheck._r import as_character

    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return as_character(v) or "NA"
    return str(v)


def _jl_simplify(x: Any) -> _JNode:
    """A parsed JSON value as ``jsonlite::fromJSON(simplifyVector = TRUE)`` holds it.

    Port of ``jsonlite:::simplify()``: an array of objects (and ``null``)
    becomes a data frame, an array of scalars an atomic vector, an array of
    equal-length scalar arrays a matrix; anything else stays a list.
    """
    if x is None:
        return _NULL
    if isinstance(x, dict):
        return ("obj", {k: _jl_simplify(v) for k, v in x.items()})
    if isinstance(x, list):
        if not x:
            return ("list", [])
        if _jl_is_recordlist(x):
            return ("df", _jl_df_rows(*_jl_records(x)))
        if all(_is_scalar_value(v) for v in x):
            return _jl_vec(x)
        if all(isinstance(v, list) and v and all(_is_scalar_value(e) for e in v) for v in x) and (
            len({len(v) for v in x}) == 1
        ):
            return ("matrix", [_jl_vec(v) for v in x])
        return ("list", [_jl_simplify(v) for v in x])
    return _jl_vec([x])


def _jl_is_recordlist(x: list[Any]) -> bool:
    """``jsonlite:::is.recordlist()``: objects and ``null`` only, at least one object."""
    return all(v is None or isinstance(v, dict) for v in x) and any(isinstance(v, dict) for v in x)


def _jl_records(records: list[Any]) -> tuple[int, dict[str, _JNode]]:
    """``jsonlite:::simplifyDataFrame()``: the columns of an array of objects.

    Columns are named in order of first appearance; each is simplified as a
    whole (``simplifyMatrix = FALSE``), so scalars share one type across rows,
    objects make a nested data frame and anything else a list column.
    """
    names: dict[str, None] = {}
    for rec in records:
        for k in rec or {}:
            names.setdefault(k, None)
    cols: dict[str, _JNode] = {}
    for k in names:
        values = [None if rec is None else rec.get(k) for rec in records]
        if _jl_is_recordlist(values):
            cols[k] = ("dfcols", *_jl_records(values))
        elif all(_is_scalar_value(v) for v in values):
            cols[k] = _jl_vec(values)
        else:
            cols[k] = ("list", [_jl_simplify(v) for v in values])
    return len(records), cols


def _jl_df_rows(n: int, cols: dict[str, _JNode]) -> list[_JNode]:
    """A data frame written by rows (``dataframe = "rows"``).

    ``NA`` cells of atomic columns are left out, a nested data frame gives
    the nested row (``{}`` when all of it is ``NA``) and a list column its
    element (``NULL`` as ``null``).
    """
    nested = {k: _jl_df_rows(c[1], c[2]) for k, c in cols.items() if c[0] == "dfcols"}
    rows: list[_JNode] = []
    for i in range(n):
        fields: dict[str, _JNode] = {}
        for k, col in cols.items():
            if col[0] == "vec":
                v = col[2][i]
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                fields[k] = ("vec", col[1], [v])
            elif col[0] == "dfcols":
                fields[k] = nested[k][i]
            else:
                fields[k] = col[1][i]
        rows.append(("obj", fields))
    return rows


def _jl_scalar(v: Any, kind: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return '"NA"' if kind in ("dbl", "int") else "null"
    if kind == "lgl":
        return "true" if v else "false"
    if kind == "chr":
        return json.dumps(v, ensure_ascii=False)
    if kind == "int":
        return str(int(v))
    x = float(v)
    if math.isinf(x):
        return '"Inf"' if x > 0 else '"-Inf"'
    return _num_to_char(x)


def _num_to_char(x: float, digits: int = 4) -> str:
    """jsonlite's ``num_to_char()`` (C) for a finite double, ``digits`` decimals.

    Between 1e-5 and 2^31 - 1 (exclusive) it is ``modp_dtoa2()``: fixed
    decimals, trailing zeros dropped; otherwise ``sprintf("%.*g")`` with the
    decimals turned into significant digits (at most 17).
    """
    ax = abs(x)
    if 1e-5 < ax < 2147483647:
        return _modp_dtoa2(x, digits)
    mag = math.log10(ax) if ax > 0 else -math.inf
    decimals = math.ceil(min(17, max(1, mag) + digits))
    return f"{x:.{decimals}g}"


def _modp_dtoa2(value: float, prec: int) -> str:
    """``modp_dtoa2()`` (stringencoders) for ``|value| < 2^31 - 1``, ``0 <= prec <= 9``."""
    neg = value < 0
    if neg:
        value = -value
    p10 = 10**prec
    whole = int(value)
    tmp = (value - whole) * p10
    frac = int(tmp)
    diff = tmp - frac
    if diff > 0.5 or (diff == 0.5 and ((prec > 0 and frac & 1) or (prec == 0 and whole & 1))):
        frac += 1
        if frac >= p10:  # rollover, e.g. 0.99 with prec 1 is 1.0
            frac = 0
            whole += 1
    if prec == 0:
        diff = value - whole
        if diff > 0.5 or (diff == 0.5 and whole & 1):
            whole += 1
        out = str(whole)
    elif frac:
        count = prec
        while frac % 10 == 0:
            count -= 1
            frac //= 10
        out = f"{whole}.{str(frac).rjust(count, '0')}"
    else:
        out = str(whole)
    return "-" + out if neg else out


def _jl_write(node: _JNode, level: int = 0) -> str:
    pad = "  " * (level + 1)
    end = "  " * level
    tag = node[0]
    if tag == "null":
        return "null"
    if tag == "vec":
        _, kind, values = node
        if len(values) == 1:
            return _jl_scalar(values[0], kind)
        return "[" + ", ".join(_jl_scalar(v, kind) for v in values) + "]"
    if tag in ("list", "df", "matrix"):
        items = node[1]
        if not items:
            return "[]"
        if tag == "matrix":
            body = ["[" + ", ".join(_jl_scalar(v, row[1]) for v in row[2]) + "]" for row in items]
        else:
            body = [_jl_write(item, level + 1) for item in items]
        return "[\n" + ",\n".join(pad + b for b in body) + "\n" + end + "]"
    if tag == "obj":
        fields = node[1]
        if not fields:
            return "{}"
        body = [
            f"{json.dumps(k, ensure_ascii=False)}: {_jl_write(v, level + 1)}"
            for k, v in fields.items()
        ]
        return "{\n" + ",\n".join(pad + b for b in body) + "\n" + end + "}"
    raise ValueError(f"unknown JSON node {tag!r}")


def _jl_value(x: Any, kind: str | None = None) -> _JNode:
    """A value metacheck itself built (a scalar or character vector)."""
    if x is None:
        return _NULL
    if isinstance(x, list | tuple):
        return _jl_vec(list(x)) if kind is None else ("vec", kind, list(x))
    if kind is not None:
        return ("vec", kind, [x])
    return _jl_vec([x])


def _jl_frame(df: pd.DataFrame | None) -> _JNode:
    if df is None:
        return ("list", [])
    rows = []
    for rec in df.to_dict(orient="records"):
        fields: dict[str, _JNode] = {}
        for k, v in rec.items():
            if v is None or (not isinstance(v, str) and is_na(v)):
                continue
            if hasattr(v, "item"):
                v = v.item()
            fields[cast("str", k)] = _jl_vec([v])
        rows.append(("obj", fields))
    return ("df", rows)


def _metadata_json(meta: dict[str, Any]) -> str:
    """``jsonlite::write_json(meta, auto_unbox = TRUE, pretty = TRUE, null = "null")``."""
    fields: dict[str, _JNode] = {}
    for key, value in meta.items():
        if key in ("tags", "citation", "node_license"):
            fields[key] = _jl_simplify(value) if value != [] else ("list", [])
        elif key in ("registrations", "forks"):
            fields[key] = _NULL if value is None else ("vec", "chr", list(value))
        elif key == "public":
            fields[key] = ("vec", "lgl", [value])
        elif key == "contributors":
            fields[key] = (
                "list",
                [("obj", {k: ("vec", "chr", [v]) for k, v in c.items()}) for c in value],
            )
        elif key == "wikis":
            fields[key] = _jl_frame(value if isinstance(value, pd.DataFrame) else None)
        elif key == "files_written":
            logs = value.get("logs")
            fields[key] = (
                "obj",
                {
                    "wiki_pages": ("vec", "chr", list(value.get("wiki_pages") or [])),
                    "logs": _NULL
                    if logs is None
                    else (
                        "obj",
                        {
                            "file": ("vec", "chr", [logs["file"]]),
                            "entries": ("vec", "int", [logs["entries"]]),
                            "first": _jl_value(logs["first"], "chr"),
                            "last": _jl_value(logs["last"], "chr"),
                        },
                    ),
                },
            )
        else:
            fields[key] = _jl_value(
                value, "chr" if isinstance(value, str) or value is None else None
            )
    return _jl_write(("obj", fields)) + "\n"


def _osf_metadata_download(osf_id: str, download_to: str, pb: Any = None) -> str | None:
    """Port of R/archive-osf-metadata.R::.osf_metadata_download(): write ``_osf_metadata``.

    Wiki pages (``wiki_<name>.md``), the activity log (``logs.csv``, written
    even when empty), ``metadata.json`` and ``README.md``, inside
    *download_to*. Returns the metadata folder, or ``None`` for an invalid ID.
    """
    from pytacheck.archives import _tick
    from pytacheck.archives.osf import osf_check_id

    checked = osf_check_id([osf_id])
    ident = checked[0] if checked else None
    if ident is None:
        return None

    meta_dir = os.path.join(download_to, _OSF_META_DIR)
    os.makedirs(meta_dir, exist_ok=True)
    _tick(pb, f"Retrieving metadata for {ident}")

    wikis = _osf_download_wikis(ident, meta_dir)
    logs = _osf_download_logs(ident, meta_dir)
    meta = _osf_node_metadata(ident)

    meta["wikis"] = [] if wikis is None else wikis
    log_dates = [] if logs is None else [d for d in logs["date"].tolist() if not is_na(d)]
    meta["files_written"] = {
        "wiki_pages": [] if wikis is None else [f for f in wikis["file"].tolist() if f],
        "logs": None
        if logs is None
        else {
            "file": "logs.csv",
            "entries": len(logs),
            "first": min(log_dates) if log_dates else None,
            "last": max(log_dates) if log_dates else None,
        },
    }
    with open(os.path.join(meta_dir, "metadata.json"), "w", encoding="utf-8") as fh:
        fh.write(_metadata_json(meta))

    if logs is None:
        _write_csv(
            pd.DataFrame(
                {"date": pd.Series([], dtype="string"), "action": pd.Series([], dtype="string")}
            ),
            os.path.join(meta_dir, "logs.csv"),
        )
    _osf_write_readme(meta, wikis, logs, meta_dir)
    return meta_dir
