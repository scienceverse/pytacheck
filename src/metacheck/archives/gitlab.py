"""GitLab.com projects (port of ``R/archive-gitlab.R``).

Only gitlab.com is recognised: a self-hosted GitLab instance cannot be told
apart from any institutional website by its URL. :func:`gitlab_links` finds
links in papers, :func:`gitlab_repo` checks a project exists, and
:func:`gitlab_tree_files` lists a project's files (following the paginated
tree endpoint), its licence and default branch, with file sizes filled in by
batched GraphQL ``repository.blobs(paths: [...])`` queries.

Every query string is built into the URL by hand (not added as parameters):
the project's ``namespace%2Fproject`` path segment must reach GitLab
percent-encoded exactly as written.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

from metacheck._values import is_missing

__all__ = ["gitlab_links", "gitlab_pat", "gitlab_repo", "gitlab_tree_files"]

#: R: ``gitlab_regex`` in gitlab_links()
_GITLAB_REGEX = r"(?:https?://)?gitlab\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
#: GraphQL complexity budget: 94 paths is GitLab's limit, 90 leaves a margin
_BLOB_BATCH = 90


def gitlab_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-gitlab.R::gitlab_links(): GitLab.com links in papers.

    Hyperlinks to ``gitlab.com/namespace/project`` in the paper's ``url``
    table, plus bare ``owner/repo`` mentions within ten words of "gitlab" in
    sentences with no GitLab URL (and no ``gitlab.io``). Returns ``href``,
    ``text_id`` and ``paper_id``.
    """
    from metacheck.archives.github import _host_links

    return _host_links(paper, "gitlab", _GITLAB_REGEX)


def gitlab_repo(repo: Any) -> Any:
    """Port of R/archive-gitlab.R::gitlab_repo(): the ``namespace/project`` path.

    Strips a leading ``gitlab.com/`` URL and a trailing ``.git``, keeps every
    remaining path segment (GitLab groups nest), and checks the project
    exists with a ``HEAD`` request. ``None`` when it does not. Several
    projects give a list aligned with the input.
    """
    from metacheck._r import as_character, grepl, sub, trimws
    from metacheck.archives.github import _as_list, _is_vector, _perform

    if repo is None:
        return None
    if _is_vector(repo):
        items = _as_list(repo)
        if not items:
            return None
        if len(items) > 1:
            return [gitlab_repo(r) for r in items]
        repo = items[0]
    if is_missing(repo):
        return None

    path = repo if isinstance(repo, str) else as_character(repo)
    path = sub(r"^(?:https?://)?(?:www\.)?gitlab\.com/", "", path, ignore_case=True, perl=True)
    path = sub(r"\.git/?$", "", path)
    path = sub("/+$", "", path)
    path = trimws(path)
    if not path or not grepl(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$", path):
        return None

    resp = _perform("HEAD", f"https://gitlab.com/{path}")
    if resp.status_code != 200:
        return None
    return str(path)


def _gitlab_project_id(path: str) -> str:
    """Port of R/archive-gitlab.R::.gitlab_project_id(): the URL-encoded project path."""
    from metacheck.archives.github import _url_encode

    return _url_encode(path, reserved=True)


def _gitlab_config(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Port of R/archive-gitlab.R::.gitlab_config(): GitLab API request headers.

    metacheck's ``User-Agent``, plus ``PRIVATE-TOKEN`` when :func:`gitlab_pat`
    is set. R modifies an httr2 request; here the headers are returned.
    """
    out = dict(headers or {})
    out["User-Agent"] = "scienceverse/metacheck"
    try:
        pat = gitlab_pat()
    except Exception:
        pat = ""
    if pat:
        out["PRIVATE-TOKEN"] = pat
    return out


def gitlab_pat(pat: Any = None) -> Any:
    """Port of R/archive-gitlab.R::gitlab_pat(): set or get the GitLab API token.

    With no argument, returns the token (the ``metacheck.gitlab.pat`` option,
    else the ``GITLAB_PAT`` environment variable; ``""`` when unset). With a
    string, sets the option for the session and returns it. Optional for
    public projects (it only raises GitLab's rate limit).
    """
    return _gitlab_pat(pat)


def _gitlab_pat(pat: Any = None) -> Any:
    """Port of R/archive-gitlab.R::.gitlab_pat()."""
    from metacheck.utils import get_option, options

    opt = "metacheck.gitlab.pat"
    if pat is None:
        value = get_option(opt)
        return os.environ.get("GITLAB_PAT", "") if value is None else value
    if not isinstance(pat, str):
        # R accepts any character vector of length one
        from metacheck.archives.github import _as_list, _is_vector

        values = _as_list(pat) if _is_vector(pat) else []
        if len(values) == 1 and isinstance(values[0], str):
            pat = values[0]
    if not isinstance(pat, str):
        raise ValueError("Set gitlab_pat with a single string containing your GitLab token")
    options({opt: pat})
    return pat


def _unset() -> dict[str, Any]:
    return {
        "gated": True,
        "reason": "invalid or inaccessible GitLab repository",
        "files": None,
        "default_branch": None,
        "license": None,
    }


def gitlab_tree_files(repo: Any) -> dict[str, Any]:
    """Port of R/archive-gitlab.R::gitlab_tree_files(): a project's files, licence and branch.

    Returns a dict with ``gated`` (``True`` only when the project cannot be
    listed at all), ``reason``, ``files`` (``repo``, ``clean_repo``,
    ``name``, ``path``, ``download_url``, ``size``, ``type``; ``None`` when
    the tree could not be fetched), ``default_branch`` and ``license`` (the
    licence key GitLab detected, e.g. ``"mit"``).
    """
    from metacheck.archives.github import (
        _add_file_types,
        _as_list,
        _body_json,
        _empty_or,
        _empty_tree_files,
        _file_ext,
        _filter_blobs,
        _is_vector,
        _perform,
        _r_basename,
        _url_encode,
    )
    from metacheck.archives.github import _dollar as r_dollar

    clean_repo = gitlab_repo(repo)
    if clean_repo is None:
        return _unset()
    if isinstance(clean_repo, list):
        # several projects: URLencode() of a missing one stops R outright;
        # otherwise request() refuses the vector of URLs, which R reports
        # as an inaccessible project
        if any(c is None for c in clean_repo):
            raise ValueError("missing value where TRUE/FALSE needed")
        return _unset()
    proj_id = _gitlab_project_id(clean_repo)

    # 1. project metadata; license=true is needed for the licence object
    try:
        meta_resp = _perform(
            "GET",
            f"https://gitlab.com/api/v4/projects/{proj_id}?license=true",
            headers=_gitlab_config(),
        )
    except Exception:
        meta_resp = None
    if meta_resp is None or meta_resp.status_code != 200:
        return _unset()

    meta = _body_json(meta_resp)
    default_branch = r_dollar(meta, "default_branch")
    if default_branch is None:
        default_branch = "main"
    license_key = _empty_or(r_dollar(r_dollar(meta, "license"), "key"))

    # 2. the file tree, 100 entries a page
    entries: list[Any] = []
    page = 1
    while True:
        try:
            tree_resp = _perform(
                "GET",
                f"https://gitlab.com/api/v4/projects/{proj_id}/repository/tree"
                f"?recursive=true&per_page=100&page={page}",
                headers=_gitlab_config(),
            )
        except Exception:
            tree_resp = None
        if tree_resp is None or tree_resp.status_code != 200:
            if page == 1:
                return {
                    "gated": False,
                    "reason": None,
                    "files": None,
                    "default_branch": default_branch,
                    "license": license_key,
                }
            break
        page_entries = _body_json(tree_resp)
        if not page_entries:
            break
        # c(all_entries, page_entries): a JSON object adds its values
        entries.extend(
            page_entries.values()
            if isinstance(page_entries, dict)
            else page_entries
            if isinstance(page_entries, list)
            else [page_entries]
        )
        next_page = tree_resp.headers.get("x-next-page")
        if next_page is None or next_page == "":
            break
        page = int(float(next_page))

    blobs = _filter_blobs(entries)
    if not blobs:
        files_df = _empty_tree_files()
    else:
        paths = [_empty_or(r_dollar(x, "path"), "") for x in blobs]
        repo_str = _as_list(repo)[0] if _is_vector(repo) else repo
        raw_base = f"https://gitlab.com/{clean_repo}/-/raw/{_url_encode(default_branch)}/"
        names = [_r_basename(p) for p in paths]
        files_df = pd.DataFrame(
            {
                "repo": pd.Series([repo_str] * len(blobs), dtype="string"),
                "clean_repo": pd.Series([clean_repo] * len(blobs), dtype="string"),
                "name": pd.Series(names, dtype="string"),
                "path": pd.Series(paths, dtype="string"),
                "download_url": pd.Series([raw_base + p for p in paths], dtype="string"),
                "size": pd.Series([None] * len(blobs), dtype="float64"),
                "ft": pd.Series(["file"] * len(blobs), dtype="string"),
            }
        )
        # file sizes via batched GraphQL (the REST tree has none)
        sizes = _gitlab_blob_sizes(clean_repo, paths)
        if len(sizes) > 0:
            lookup: dict[Any, float] = {}
            for key, value in zip(sizes.index.tolist(), sizes.tolist(), strict=True):
                if key is not None and key not in lookup:
                    lookup[key] = value
            # sizes[files_df$path]: "" never matches a name in R
            files_df["size"] = pd.Series(
                [lookup.get(p) if p != "" else None for p in paths], dtype="float64"
            )
        files_df["ext"] = pd.Series([_file_ext(n).lower() for n in names], dtype="string")
        files_df = _add_file_types(files_df, drop_ext=True)

    return {
        "gated": False,
        "reason": None,
        "files": files_df,
        "default_branch": default_branch,
        "license": license_key,
    }


def _graphql_body(clean_repo: str, batch: list[str]) -> bytes:
    """The JSON body httr2's ``req_body_json(list(query = ...))`` sends."""
    from metacheck._r import gsub

    quoted = ",".join(f'"{p}"' for p in gsub('"', r'\\"', batch))
    query = (
        f'query {{ project(fullPath: "{clean_repo}") {{ repository {{ '
        f"blobs(paths: [{quoted}]) {{ nodes {{ path size }} }} }} }} }}"
    )
    return json.dumps({"query": query}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _gitlab_blob_sizes(clean_repo: str, paths: list[str]) -> pd.Series:
    """Port of R/archive-gitlab.R::.gitlab_blob_sizes(): file sizes by path.

    One GraphQL ``repository.blobs(paths: [...])`` query per 90 paths (the
    query-complexity budget). Returns sizes indexed by path (R: a named
    numeric vector); failed batches contribute nothing.
    """
    from metacheck.archives.github import _dollar as r_dollar
    from metacheck.archives.github import _empty_or, _num

    if not paths:
        return pd.Series([], dtype="float64")
    keys: list[Any] = []
    values: list[float | None] = []
    for start in range(0, len(paths), _BLOB_BATCH):
        nodes = _blob_batch(clean_repo, paths[start : start + _BLOB_BATCH])
        for n in nodes:
            values.append(_num(_empty_or(r_dollar(n, "size"))))
            keys.append(_empty_or(r_dollar(n, "path")))
    return pd.Series(values, index=keys, dtype="float64")


def _blob_batch(clean_repo: str, batch: list[str]) -> list[Any]:
    """One GraphQL ``blobs(paths:)`` query: its ``nodes``, or none when the request failed.

    A query over GitLab's complexity budget comes back HTTP 200 with
    ``errors`` and no data, which also gives no nodes.
    """
    from metacheck.archives.github import _body_json, _perform
    from metacheck.archives.github import _dollar as r_dollar

    try:
        resp = _perform(
            "POST",
            "https://gitlab.com/api/graphql",
            content=_graphql_body(clean_repo, batch),
            headers=_gitlab_config({"Content-Type": "application/json"}),
        )
        if resp.status_code != 200:
            return []
        res = _body_json(resp)
    except Exception:
        return []
    nodes = res
    for key in ("data", "project", "repository", "blobs", "nodes"):
        nodes = r_dollar(nodes, key)
    if isinstance(nodes, dict):
        return list(nodes.values())
    return list(nodes) if isinstance(nodes, list) else []
