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
from typing import Any

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
    from pytacheck.archives.osf_helpers import _get

    resp = _get_request(f"{_api()}/nodes/{osf_id}/?embed=license")
    node = None
    if resp is not None and resp.status_code == 200:
        try:
            node = resp.json().get("data")
        except ValueError:
            node = None
    name = _get(node, "embeds", "license", "data", "attributes", "name")
    if isinstance(name, list):
        return name[0] if name else None
    return name


def _osf_node_metadata(osf_id: str) -> dict[str, Any]:
    """Port of R/archive-osf-metadata.R::.osf_node_metadata(): a project's record.

    The node's attributes, contributors (with ORCIDs), licence, citation,
    tags, and the IDs of its registrations and forks, as a dict ready to be
    written as JSON.
    """
    from pytacheck.archives.osf_helpers import _get

    api = _api()
    resp = _get_request(f"{api}/nodes/{osf_id}/?embed=license&embed=bibliographic_contributors")
    node = None
    if resp is not None and resp.status_code == 200:
        try:
            node = resp.json().get("data")
        except ValueError:
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
            if orcid is not None and orcid != "":
                who.append(f"- {c.get('name')} (ORCID {orcid})")
            else:
                who.append(f"- {c.get('name')}")
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


def _json_ready(x: Any) -> Any:
    if isinstance(x, pd.DataFrame):
        return [
            {k: _json_ready(v) for k, v in rec.items() if not _missing(v)}
            for rec in x.to_dict(orient="records")
        ]
    if isinstance(x, dict):
        return {k: _json_ready(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_json_ready(v) for v in x]
    if _missing(x):
        return None
    if hasattr(x, "item"):
        return x.item()
    return x


def _missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, list | tuple | dict):
        return False
    try:
        return bool(is_na(v))
    except (TypeError, ValueError):
        return False


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
        json.dump(_json_ready(meta), fh, indent=2, ensure_ascii=False)

    if logs is None:
        _write_csv(
            pd.DataFrame(
                {"date": pd.Series([], dtype="string"), "action": pd.Series([], dtype="string")}
            ),
            os.path.join(meta_dir, "logs.csv"),
        )
    _osf_write_readme(meta, wikis, logs, meta_dir)
    return meta_dir
