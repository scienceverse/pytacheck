"""Downloading a whole OSF project, one archive per node (port of ``R/archive-osf-all.R``).

``osf_file_download(mode = "all")`` walks the component tree and takes one
Waterbutler archive per node, never listing individual files; files on a
linked add-on (GitHub, Dropbox, ...) are in no OSF archive, so those are
listed and fetched individually.
"""

from __future__ import annotations

import contextlib
import os
import zipfile
from typing import Any

import pandas as pd

from pytacheck._r import bind_rows, is_na, plural, sub


def _api() -> str:
    from pytacheck.utils import get_option

    return str(get_option("metacheck.osf.api"))


def _pages_or_none(url: str) -> Any:
    """``tryCatch(osf_get_all_pages(url), error = \\(e) NULL)``."""
    from pytacheck.archives.osf import osf_get_all_pages

    try:
        return osf_get_all_pages(url)
    except Exception:
        return None


def _files_under(folder: str) -> list[str]:
    """R ``list.files(folder, recursive = TRUE)`` minus directories (hidden files skipped)."""
    out: list[str] = []
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        out.extend(os.path.join(dirpath, f) for f in files if not f.startswith("."))
    return out


def _osf_walk_nodes(osf_id: str, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-osf-all.R::.osf_walk_nodes(): every node under a project.

    Breadth-first, the project itself first; returns ``osf_id`` and ``title``.
    """
    from pytacheck import http
    from pytacheck.archives import _tick
    from pytacheck.archives.osf_helpers import _get, _osf_headers

    api = _api()
    ids: list[str] = []
    titles: list[str | None] = []
    todo = [osf_id]
    level = 0
    while todo:
        level += 1
        _tick(pb, f"Finding components: level {level}, {len(todo)} to check ({len(ids)} found)")
        nxt: list[str] = []
        for node in todo:
            kids = _pages_or_none(f"{api}/nodes/{node}/children/")
            if not isinstance(kids, list) or len(kids) == 0:
                continue
            ids.extend(k.get("id") for k in kids)
            titles.extend(_get(k, "attributes", "title") for k in kids)
            nxt.extend(k.get("id") for k in kids)
        todo = nxt

    root_title = None
    try:
        resp = http.request(
            "GET", f"{api}/nodes/{osf_id}/", headers=_osf_headers()["headers"], max_tries=1
        )
        if resp is not None and resp.status_code == 200:
            root_title = _get(resp.json(), "data", "attributes", "title")
    except Exception:
        root_title = None

    return pd.DataFrame(
        {
            "osf_id": pd.Series([osf_id, *ids], dtype="string"),
            "title": pd.Series([root_title, *titles], dtype="string"),
        }
    )


def _osf_download_addons(node: str, node_dir: str, pb: Any = None) -> dict[str, float]:
    """Port of R/archive-osf-all.R::.osf_download_addons(): a node's add-on files.

    Lists each non-``osfstorage`` provider (walking its folders) and fetches
    the files into ``<node_dir>/<provider>/``. Returns ``{"files", "bytes"}``.
    """
    from pytacheck.archives import _tick
    from pytacheck.archives.osf import osf_get_all_pages
    from pytacheck.archives.osf_helpers import _get, _osf_parse_response
    from pytacheck.utils import path_sanitize

    api = _api()
    none = {"files": 0, "bytes": 0.0}
    try:
        provs = osf_get_all_pages(f"{api}/nodes/{node}/files/")
    except Exception:
        return none
    if not isinstance(provs, list) or len(provs) == 0:
        return none
    names = [_get(p, "attributes", "provider") for p in provs]
    extra = list(dict.fromkeys(str(n).lower() for n in names if n is not None))
    extra = [p for p in extra if p != "osfstorage"]
    if not extra:
        return none

    got = 0
    nbytes = 0.0
    for p in extra:
        _tick(pb, f"{node}: listing {p} files (not in the OSF archive)")
        listing = _pages_or_none(f"{api}/nodes/{node}/files/{p}/")
        if not isinstance(listing, list) or len(listing) == 0:
            continue
        info = _osf_parse_response(listing)
        if info is None or len(info) == 0:
            continue

        while "files" in info.columns and info["files"].notna().any():
            more = [_pages_or_none(u) for u in info["files"].tolist() if not is_na(u)]
            more = [m for m in more if m is not None]
            if not more:
                break
            parsed = bind_rows(
                [_osf_parse_response(m) if isinstance(m, list) else None for m in more]
            )
            if len(parsed) == 0:
                break
            info = info.copy()
            info["files"] = pd.Series([None] * len(info), dtype="string")
            info = bind_rows([info, parsed])

        kind = info["kind"].tolist() if "kind" in info else [None] * len(info)
        urls = info["download_url"].tolist() if "download_url" in info else [None] * len(info)
        rows = [
            i
            for i, (k, u) in enumerate(zip(kind, urls, strict=True))
            if not is_na(k) and k == "file" and not is_na(u)
        ]
        if not rows:
            continue
        os.makedirs(node_dir, exist_ok=True)
        source = info["path"] if "path" in info else info["name"]
        rel = [sub("^/+", "", "NA" if is_na(v) else str(v)) for v in source.iloc[rows].tolist()]
        dests = [os.path.join(node_dir, p, s) for s in path_sanitize(rel)]
        for d in dict.fromkeys(os.path.dirname(x) for x in dests):
            os.makedirs(d, exist_ok=True)

        from pytacheck.archives.download import _download_many_parallel

        errs = list(
            _download_many_parallel([urls[i] for i in rows], dests, [float("nan")] * len(rows))
        )
        ok = [j for j, e in enumerate(errs) if is_na(e)]
        got += len(ok)
        nbytes += sum(os.path.getsize(dests[j]) for j in ok if os.path.exists(dests[j]))
    return {"files": got, "bytes": nbytes}


def _osf_download_all(
    osf_id: str, download_to: str, metadata: bool = True, pb: Any = None
) -> pd.DataFrame:
    """Port of R/archive-osf-all.R::.osf_download_all(): ``osf_file_download(mode = "all")``.

    One row per node: ``folder``, ``osf_project``, ``osf_url``, ``title``,
    ``files`` (how many arrived), ``bytes``, ``download_path`` and
    ``downloaded``. Folders are named ``<sanitised title>_<id>``.
    """
    import warnings

    from pytacheck.archives import _message, _tick
    from pytacheck.archives.osf import _cap_helpers, _normalize, _stream_to_file
    from pytacheck.archives.osf_metadata import _osf_metadata_download
    from pytacheck.utils import path_sanitize

    _cap_report, cap_size_str, _cap_num = _cap_helpers()
    nodes = _osf_walk_nodes(osf_id, pb=pb)
    _message(f"{osf_id}: {len(nodes)} node{plural(len(nodes))} to download")

    download_to = _normalize(download_to)
    if os.path.isdir(download_to):
        download_to = os.path.join(download_to, osf_id).replace("\\", "/")
    os.makedirs(download_to, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for i, (node, title) in enumerate(zip(nodes["osf_id"], nodes["title"], strict=True), start=1):
        title = None if is_na(title) else str(title)
        folder = (
            node
            if title is None or title == ""
            else f"{path_sanitize(title, keep_sep=False)}_{node}"
        )
        node_dir = os.path.join(download_to, folder)
        _tick(pb, f"Downloading {folder} ({i} of {len(nodes)})")

        zip_url = f"https://files.osf.io/v1/resources/{node}/providers/osfstorage/?zip="
        zip_path = os.path.join(download_to, f"{node}.zip")
        try:
            ok = _stream_to_file(zip_url, zip_path, timeout_s=1800) == 200
        except Exception:
            ok = False

        if ok and os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    entries = zf.namelist()
                    if entries:
                        os.makedirs(node_dir, exist_ok=True)
                        zf.extractall(node_dir)
            except (zipfile.BadZipFile, OSError):
                pass
        if os.path.exists(zip_path):
            os.remove(zip_path)

        _osf_download_addons(node, node_dir, pb=pb)

        n_files = 0
        nbytes = 0.0
        if os.path.isdir(node_dir):
            present = _files_under(node_dir)
            n_files = len(present)
            nbytes = float(sum(os.path.getsize(f) for f in present))
        rows.append(
            {
                "folder": folder,
                "osf_project": node,
                "osf_url": f"https://osf.io/{node}",
                "title": title,
                "files": n_files,
                "bytes": nbytes,
                "download_path": node_dir.replace("\\", "/") if n_files > 0 else None,
                "downloaded": ok,
            }
        )

    out = pd.DataFrame(
        {
            "folder": pd.Series([r["folder"] for r in rows], dtype="string"),
            "osf_project": pd.Series([r["osf_project"] for r in rows], dtype="string"),
            "osf_url": pd.Series([r["osf_url"] for r in rows], dtype="string"),
            "title": pd.Series([r["title"] for r in rows], dtype="string"),
            "files": pd.Series([r["files"] for r in rows], dtype="Int64"),
            "bytes": pd.Series([r["bytes"] for r in rows], dtype="float64"),
            "download_path": pd.Series([r["download_path"] for r in rows], dtype="string"),
            "downloaded": pd.Series([r["downloaded"] for r in rows], dtype="boolean"),
        }
    )

    failed = [r["osf_project"] for r in rows if not r["downloaded"]]
    if failed:
        warnings.warn(
            f"{len(failed)} of {len(out)} node{plural(len(out))} could not be downloaded "
            f"(e.g. {', '.join(failed[:3])}). Rerun to try again.",
            stacklevel=2,
        )

    if metadata is True:
        for r in rows:
            if r["download_path"] is None:
                continue
            with contextlib.suppress(Exception):
                _osf_metadata_download(r["osf_project"], r["download_path"], pb=pb)

    total_files = sum(r["files"] for r in rows)
    with_files = sum(1 for r in rows if r["files"] > 0)
    _message(
        f"{osf_id}: {total_files} file{plural(total_files)} in {with_files} "
        f"node{plural(with_files)}, {cap_size_str(sum(r['bytes'] for r in rows))}"
    )
    return out
