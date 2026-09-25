"""Repository Check (port of ``inst/modules/repo_check.R``).

Lists the files in the repositories a paper links (OSF, GitHub, GitLab,
ResearchBox, DSpace, Zenodo, Dataverse, Figshare, Dryad, ReShare,
4TU.ResearchData, Mendeley Data, DataONE) and in local folders, without
downloading them; peeks inside ``.zip`` archives; classifies and groups the
files by name; and reports missing READMEs, opaque archives, proprietary
E-Prime files, restricted or closed sources, unclassifiable files, a
study/repository mismatch and file-naming problems.

The per-platform listing blocks live in :mod:`pytacheck.modules._repo_check`.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from pytacheck._r import bind_rows, grepl, plural, r_sort_key, sub
from pytacheck.module import module
from pytacheck.modules import _repo_check as rc

_NO_REPOS = (
    "We found no links to repositories on the Open Science Framework, Github, ResearchBox, "
    "DSpace, Zenodo, Dataverse, Figshare, Dryad, ReShare, 4TU.ResearchData, Mendeley Data, "
    "or DataONE."
)

_README_REPORT = (
    "#### README Files\n\nREADME files are a way to document the contents and structure of a "
    "folder, helping users locate the information they need. You can use a README to document "
    "changes to a repository, and explain how files are named. Please consider adding a README "
    "to each repository or including 'README' in the name of your overview document."
)

_ZIP_REPORT = (
    "#### Archive Files\n\nThe following files are not ZIP archives: {}. We recommend the "
    "`.zip` format for archives: only ZIP stores its file listing in a way that lets a tool "
    "inspect the contents without downloading the whole archive, so a `.zip` is more "
    "discoverable and re-usable than a `.7z`, `.rar`, or `.tar.gz`."
)

_EDAT_REPORT = (
    "#### Proprietary E-Prime Files\n\nThe repository contains {n} E-Prime file{s} ({files}). "
    "These are proprietary **binary** formats that only the E-Prime software can open, so "
    "metacheck does not download or read them and other researchers cannot reuse them directly."
)
_EDAT_MISSING = (
    " {n} of them ha{v} no matching plain-text export. Please also upload the E-Prime **.txt "
    "export** (File → Export in E-Prime) for each, so the trial-level data is readable without "
    "the proprietary software."
)
_EDAT_OK = (
    " A matching .txt export was found for each, which is the readable form metacheck uses — "
    "good. Keep including the .txt export alongside any .edat file."
)

_RESTRICTED_REPORT = (
    "#### Restricted-Access Files\n\n{n} {repos} ({urls}) {verb} files behind restricted or "
    "embargoed access. These files require a login and cannot be downloaded programmatically, "
    "so metacheck cannot examine them and other researchers cannot reuse them without "
    "requesting access. Consider making the files openly available to improve their "
    "reusability."
)

_CLOSED_REPORT = (
    "#### Registration Points to a Closed Project\n\n{n} OSF {projects} ({urls}) {is_are} "
    "referenced by a registration in this manuscript, but {it_is} closed or inaccessible, and "
    "no copy of {its} files could be found anywhere else. Some OSF registrations only describe "
    "what was shared, while the actual files live in the project they were registered from — "
    "if that project is closed and the registration holds no copy, metacheck cannot list or "
    "download its files, and other researchers cannot access them either. Consider making the "
    "source project openly available, or re-uploading its files directly into the registration."
)
_CLOSED_SUMMARY = (
    "We found {n} registration-linked OSF {projects} that {is_are} closed with no files "
    "reachable anywhere."
)
_MIRROR_REPORT = (
    "#### Registration's Source Project Is Closed\n\n{n} OSF source {projects} {is_are} closed. "
    "The files metacheck lists below were not read from {it} — they were retrieved from the OSF "
    "registration's own copy, which is separate from and unaffected by the project's access "
    "setting. Nothing is currently lost, but this is still worth fixing: a closed source "
    "project means the files only survive for as long as the registration's copy does, and "
    "anyone who follows the project link itself (rather than the registration) will find it "
    "inaccessible.\n\n{lines}\n\nConsider making the source project openly available."
)
_MIRROR_SUMMARY = (
    "We found {n} registration-linked OSF {projects} that {is_are} closed; {its} files were "
    "retrieved from the OSF registration instead."
)

_UNKNOWN_REPORT = (
    "#### Unclassified Files\n\nWe could not classify {n} file{s} by name or extension: "
    "{files}. Add a recognisable keyword (`data`, `code`, `materials`, `documentation`, "
    "`output`) to the file name, or use a common extension, so both humans and machines can "
    "tell what kind of file it is."
)

_ROSTER_REPORT = (
    "#### Study/Repository Mismatch\n\nThe studies named in the manuscript do not match how "
    "the repository's files are grouped: {parts}. Check that every study has its own "
    "clearly-named folder or file prefix, and that the folder names match how the manuscript "
    "refers to each study."
)
_ROSTER_SUMMARY = "The manuscript's studies and the repository's file groups do not match."

_NAMING_BAD = (
    "File names should be machine-parseable: no spaces or special characters, and every file "
    "should be classifiable by name or extension."
)
_NAMING_OK = "File names are broadly machine-parseable."


@module(
    title="Repository Check",
    description="This module retrieves information from repositories.",
    details="""
        The Repository Check module lists files on the OSF, GitHub, ResearchBox, DSpace (PsychArchives and other legacy- and DSpace 7-based installations), Zenodo, Dataverse, Figshare, Dryad, ReShare, 4TU.ResearchData, Mendeley Data, and DataONE (Arctic Data Center, KNB, and other member nodes) based on links in the manuscript.

        When a linked OSF page is a registration, its `registered_from` project (the
        one it was registered from, which the manuscript itself may never link
        directly) is also checked: if that project is public its files are listed
        under its own URL, and if it is closed or otherwise inaccessible the report
        flags it explicitly, since some registrations only describe their content
        rather than mirroring it.

        If you want to extend the package to be able to download files from additional data repositories reach out to the Metacheck development team.
    """,
    keywords=["results"],
    requires=["network"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={
        "paper": "a paper object or paperlist object",
        "peek_zips": "if TRUE (default), read each `.zip`'s file listing over HTTP without "
        "downloading it (one Range request per archive, see [zip_peek()]) and list its "
        "contents in place of the archive row. Set FALSE to leave archives as single opaque "
        "entries and make no per-archive request. Only `.zip` can be inspected this way; "
        "other archive formats have no tail index.",
        "osf_license": "if TRUE, make one extra lightweight API request (`?embed=license`, "
        "not the full four-call project-metadata lookup) per distinct OSF project linked, to "
        "include OSF's licence in `repo_metadata`. FALSE (the default) skips this, since it "
        "is one additional request per OSF project on top of what this module otherwise "
        "makes -- meaningful added latency across a large corpus. Every other supported "
        "repository platform (Dryad, Zenodo, Dataverse, Figshare, ReShare, GitHub, GitLab, "
        "PsychArchives) already fetches its own licence as part of a request this module "
        "makes anyway, so `repo_metadata` has their licence populated regardless of this "
        "parameter.",
        "cache": "if TRUE, reuse a previously cached Dryad/Zenodo listing (see "
        "[repo_info_cache()]) instead of re-querying the host's API for a repository already "
        "looked up. FALSE (the default) always queries fresh. This is a SEPARATE cache from "
        "the downloaded-file-bytes cache ([repo_cache_dir()]) -- listing a repository and "
        "downloading its files are different costs. Worth enabling for a corpus build likely "
        "to be interrupted and restarted: without it, a host with a strict per-IP quota "
        "(Dryad, confirmed live 2026-09-08) has its quota re-spent on every restart "
        "re-listing repositories nothing new needs fetching for, rather than on the actual "
        "(usually much smaller) download workload.",
        "model": "the LLM model name (see `llm_model_list()`), used only when `llm_use(TRUE)` "
        "for study grouping the deterministic passes cannot place",
        "params": "a named list passed to `llm()`, used only when `llm_use(TRUE)`",
    },
)
def repo_check(
    paper: Any,
    local_path: str | os.PathLike[str] | Sequence[str] | None = None,
    local_only: bool = False,
    peek_zips: bool = True,
    osf_license: bool = False,
    cache: bool = False,
    model: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Port of ``inst/modules/repo_check.R::repo_check()``.

    Lists the files of every repository the paper(s) link, plus any
    *local_path* folders (*local_only* skips the online repositories), and
    reports on their documentation, archives and naming. *model* defaults to
    :func:`~pytacheck.llm_model` and, like *params*, is only used for study
    grouping when ``llm_use(TRUE)``.
    """
    from pytacheck.archives import _tick
    from pytacheck.utils import pb as make_pb

    # R: pb(NA, "(:spin) :what"), finished by on.exit() (a no-op unless verbose())
    bar = make_pb(None, "(:spin) :what")
    _tick(bar, "Starting Repo Check")
    try:
        return _repo_check(
            paper, local_path, local_only, peek_zips, osf_license, cache, model, params, bar
        )
    finally:
        _tick(bar, "Repo Check Complete")
        with contextlib.suppress(Exception):  # a progress display must never break a run
            bar.terminate()


def _repo_check(
    paper: Any,
    local_path: str | os.PathLike[str] | Sequence[str] | None,
    local_only: bool,
    peek_zips: bool,
    osf_license: bool,
    cache: bool,
    model: str | None,
    params: Mapping[str, Any] | None,
    bar: Any,
) -> dict[str, Any]:
    """The body of :func:`repo_check` (*bar* is the module's progress spinner)."""
    from pytacheck.papers.tables import paper_id as paper_ids

    params = dict(params or {})
    if isinstance(local_path, os.PathLike):
        local_path = os.fspath(local_path)

    # get repository links ----
    if local_only is True:
        repos = rc.Repos(rc.empty_links())
    else:
        links, fs_links = rc.collect_links(paper)
        repos = rc.Repos(links)
        rc.flag_figshare_share_links(repos, fs_links)

    # get files ----
    osf_urls = repos.urls("osf")
    osf_ids = rc.vals(repos.df["paper_id"][(repos.df["repo_type"] == "osf").fillna(False)])
    osf_paper_id = osf_ids[0] if osf_ids else paper_ids(paper)[0]

    osf_meta = rc.meta_frame()
    if osf_license is True and osf_urls:
        osf_meta = _osf_license_meta(osf_urls)
    osf_files = rc.list_osf(repos, osf_urls, osf_paper_id, bar, cache)

    github_files, github_meta = rc.list_git(repos, repos.urls("github"), "github", cache, bar)
    gitlab_files, gitlab_meta = rc.list_git(repos, repos.urls("gitlab"), "gitlab", cache, bar)
    rb_files = rc.list_researchbox(repos, repos.urls("researchbox"), bar)
    pa_files, pa_meta = rc.list_dspace(repos, repos.urls("dspace"), bar, cache)
    dspace7_files = rc.list_dspace7(repos, repos.urls("dspace7"), bar)
    listings = _api_listings(repos, cache, bar)

    local_files_df = rc.placeholder()
    if local_path is not None:
        local_files_df = rc.list_local(local_path)
        paths = [local_path] if isinstance(local_path, str) else list(local_path)
        repos.add(rc.repo_rows(paper_ids(paper)[0], paths, "local", rc.NA_SCALAR))

    # no repos found ----
    if len(repos.df) == 0:
        ids = paper_ids(paper)
        n = len(ids)
        return {
            "traffic_light": "na",
            "summary_text": _NO_REPOS,
            "summary_table": pd.DataFrame(
                {
                    "paper_id": pd.Series(ids, dtype="string"),
                    "repo_n": pd.Series([0.0] * n, dtype="float64"),
                    **{
                        c: pd.Series([pd.NA] * n, dtype="boolean")
                        for c in (
                            "files_n",
                            "files_data",
                            "files_code",
                            "files_readme",
                            "files_zip",
                        )
                    },
                }
            ),
        }

    # file numbers and types ----
    all_files = bind_rows(
        [
            osf_files,
            github_files,
            gitlab_files,
            rb_files,
            pa_files,
            dspace7_files,
            listings["zenodo"][0],
            listings["dataverse"][0],
            listings["figshare"][0],
            listings["dryad"][0],
            listings["reshare"][0],
            listings["researchdata4tu"][0],
            listings["mendeley"][0],
            listings["dataone"][0],
            local_files_df,
        ]
    ).reset_index(drop=True)
    repo_metadata = bind_rows(
        [
            osf_meta,
            github_meta,
            gitlab_meta,
            pa_meta,
            listings["zenodo"][1],
            listings["dataverse"][1],
            listings["figshare"][1],
            listings["dryad"][1],
            listings["reshare"][1],
            listings["researchdata4tu"][1],
            listings["mendeley"][1],
            listings["dataone"][1],
        ]
    ).reset_index(drop=True)

    all_files, is_readme = _prepare_files(all_files, repos)
    if peek_zips is True and len(all_files) > 0:
        all_files = _peek_zips(all_files)

    # preliminary classification + study grouping ----
    all_files, roster_check, group_no_evidence = _classify(all_files, model, params, paper)

    # attach paper_id (a repository shared by several papers repeats its files)
    from pytacheck.utils import left_join

    repo_keys = repos.df.loc[:, ["paper_id", "repo_url"]]
    all_files = left_join(all_files, repo_keys, by="repo_url")
    if len(repo_metadata) > 0:
        repo_metadata = left_join(repo_metadata, repo_keys, by="repo_url")
    else:
        repo_metadata = repo_metadata.copy()
        repo_metadata["paper_id"] = pd.Series([], dtype="string")

    repo_sum = _summarise_repos(repos.df, all_files)

    report, summary, flags = _report(all_files, repo_sum, is_readme, roster_check)

    # traffic_light ----
    green = (
        flags["zip_n"] == 0
        and flags["repo_no_readme"] == 0
        and flags["n_unknown"] == 0
        and not flags["roster"]
        and flags["n_naming_bad"] == 0
        and flags["n_closed_unreachable"] == 0
    )
    tl = "green" if green else "yellow"

    summary_table = _summary_table(repo_sum, all_files, flags, roster_check)
    summary_text = "".join(f"\n-  {s}" for s in summary)

    error_rows = repo_sum["repo_error"].notna().to_numpy(dtype=bool)
    gated_repos = repo_sum.loc[error_rows, ["repo_url", "repo_type", "repo_error"]].reset_index(
        drop=True
    )

    return {
        "table": all_files,
        "summary_table": summary_table,
        "gated_repos": gated_repos,
        "naming_issues": flags["naming_issues_by_paper"],
        "repo_metadata": repo_metadata,
        "roster_check": roster_check,
        "group_no_evidence": group_no_evidence,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


def _osf_license_meta(osf_urls: list[str]) -> pd.DataFrame:
    """The ``osf_license = TRUE`` lookup: one ``?embed=license`` request per project."""
    from pytacheck.archives.osf import osf_check_id
    from pytacheck.archives.osf_metadata import _osf_license

    ids = osf_check_id(osf_urls)
    licenses: list[str | None] = []
    for osf_id in ids:
        if osf_id is None:
            licenses.append(None)
            continue
        try:
            licenses.append(_osf_license(osf_id))
        except Exception:
            licenses.append(None)
    return rc.meta_frame(osf_urls, rc.NA_SCALAR, licenses)


def _api_listings(
    repos: rc.Repos, cache: bool, bar: Any = None
) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """The Zenodo, Dataverse, Figshare, Dryad, ReShare, 4TU, Mendeley and DataONE blocks."""
    from pytacheck.archives import (
        dataone,
        dataverse,
        dryad,
        figshare,
        fourtu,
        mendeley,
        reshare,
        zenodo,
    )

    out: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    urls = repos.urls("zenodo")
    out["zenodo"] = rc._api_listing(
        repos,
        urls,
        lambda: zenodo.zenodo_info(urls, pb=bar, cache=cache),
        "zenodo_url",
        rc.zenodo_row,
        col_chr=True,
    )
    dv = repos.urls("dataverse")
    out["dataverse"] = rc._api_listing(
        repos,
        dv,
        lambda: dataverse.dataverse_info(dv, cache=cache),
        "dataverse_url",
        rc.dataverse_row,
    )
    fs = repos.urls("figshare")
    out["figshare"] = rc._api_listing(
        repos, fs, lambda: figshare.figshare_info(fs, cache=cache), "figshare_url", rc.figshare_row
    )
    dr = repos.urls("dryad")
    out["dryad"] = rc._api_listing(
        repos, dr, lambda: dryad.dryad_info(dr, cache=cache), "dryad_url", rc.dryad_row
    )
    rs = repos.urls("reshare")
    out["reshare"] = rc._api_listing(
        repos, rs, lambda: reshare.reshare_info(rs, cache=cache), "reshare_url", rc.reshare_row
    )
    ft = repos.urls("researchdata4tu")
    out["researchdata4tu"] = rc._api_listing(
        repos,
        ft,
        lambda: fourtu.researchdata4tu_info(ft, cache=cache),
        "researchdata4tu_url",
        rc.figshare_row,
    )
    md = repos.urls("mendeley")
    out["mendeley"] = rc._api_listing(
        repos, md, lambda: mendeley.mendeley_info(md, cache=cache), "mendeley_url", rc.mendeley_row
    )
    do = repos.urls("dataone")
    out["dataone"] = rc._api_listing(
        repos, do, lambda: dataone.dataone_info(do, cache=cache), "dataone_url", rc.dataone_row
    )
    return out


def _prepare_files(all_files: pd.DataFrame, repos: rc.Repos) -> tuple[pd.DataFrame, list[bool]]:
    """De-duplicate, drop R package trees, mark READMEs and add ``repo_name``."""
    is_readme: list[bool] = []
    if len(all_files) > 0:
        # R: duplicated(file_url) & duplicated(file_path), each on its own
        key = "file_path" if "file_path" in all_files.columns else "file_name"
        urls = rc.vals(all_files["file_url"]) if "file_url" in all_files.columns else []
        if len(urls) != len(all_files):
            dupes = [True] * len(all_files)  # R: logical(0) drops every row
            all_files = all_files.iloc[0:0]
        else:
            dupes = [
                a and b
                for a, b in zip(
                    rc.duplicated(urls), rc.duplicated(all_files[key].tolist()), strict=True
                )
            ]
            all_files = rc.take(all_files, [not d for d in dupes])
        # keep repos with explicit errors (e.g. gated/private) in summary/reporting
        in_files = [
            a or not rc._na(e)
            for a, e in zip(
                rc.r_in(rc.vals(repos.df["repo_url"]), rc.vals(all_files["repo_url"])),
                rc.vals(repos.df["repo_error"]),
                strict=True,
            )
        ]
        repos.keep(in_files)

    if len(all_files) == 0:
        all_files = all_files.copy()
        for col, dtype in (
            ("repo_url", "string"),
            ("file_name", "string"),
            ("file_path", "string"),
            ("file_url", "string"),
            ("file_location", "string"),
            ("file_size", "float64"),
            ("file_type", "string"),
        ):
            all_files[col] = pd.Series([], dtype=dtype)
    else:
        if "file_path" not in all_files.columns:
            all_files["file_path"] = all_files["file_name"]
        fp = rc.vals(all_files["file_path"])
        fn = rc.vals(all_files["file_name"])
        all_files["file_path"] = pd.Series(
            [n if (rc._na(p) or p == "") else p for p, n in zip(fp, fn, strict=True)],
            dtype="string",
        )
        all_files = _drop_r_packages(all_files)
        is_readme = [
            bool(v)
            for v in grepl("readme|read[_ ]me", rc.vals(all_files["file_name"]), ignore_case=True)
        ]
        if "file_type" not in all_files.columns:
            all_files["file_type"] = pd.Series([None] * len(all_files), dtype="string")
        ftype = rc.vals(all_files["file_type"])
        all_files["file_type"] = pd.Series(
            ["readme" if r else t for r, t in zip(is_readme, ftype, strict=True)], dtype="string"
        )
    all_files["repo_name"] = pd.Series(
        [rc.r_basename(u) for u in rc.vals(all_files["repo_url"])], dtype="string"
    )
    return all_files.reset_index(drop=True), is_readme


def _drop_r_packages(all_files: pd.DataFrame) -> pd.DataFrame:
    """Drop an R package's own source tree (``ave(file_path, repo_url, FUN = .is_r_package_file)``)."""
    from pytacheck.datacheck.files import _is_r_package_file

    paths = rc.vals(all_files["file_path"])
    repo = rc.vals(all_files["repo_url"])
    flags: list[bool | None] = [None] * len(paths)
    groups: dict[Any, list[int]] = {}
    for i, r in enumerate(repo):
        if rc._na(r):
            # ave() leaves rows outside every group as they are: as.logical(<path>)
            flags[i] = _as_logical(paths[i])
            continue
        groups.setdefault(r, []).append(i)
    for idx in groups.values():
        res = _is_r_package_file([paths[i] for i in idx])
        for i, v in zip(idx, res, strict=True):
            flags[i] = bool(v)
    if not any(v is True for v in flags):
        if any(v is None for v in flags):
            raise rc.RError("missing value where TRUE/FALSE needed")
        return all_files
    # df[!flags, ]: NA keeps a row of NAs, as R does
    rows = []
    for i, v in enumerate(flags):
        if v is None:
            rows.append(-1)
        elif not v:
            rows.append(i)
    if all(r >= 0 for r in rows):
        return all_files.iloc[rows].reset_index(drop=True)
    parts = [
        all_files.iloc[[r]] if r >= 0 else all_files.iloc[[0]].map(lambda _: None) for r in rows
    ]
    return bind_rows(parts).reset_index(drop=True)


def _as_logical(x: Any) -> bool | None:
    """R ``as.logical()`` of a string."""
    if rc._na(x):
        return None
    s = str(x)
    if s in ("TRUE", "true", "T", "True"):
        return True
    if s in ("FALSE", "false", "F", "False"):
        return False
    return None


def _peek_zips(all_files: pd.DataFrame) -> pd.DataFrame:
    """Replace each listable ``.zip`` row by the archive's entries (one Range request each)."""
    from pytacheck.archives.zip_peek import _is_zip, zip_peek
    from pytacheck.fileinfo.types import file_types

    names = rc.vals(all_files["file_name"])
    urls = rc.vals(all_files["file_url"])
    is_zip = [
        bool(z) and not rc._na(u) and u != "" for z, u in zip(_is_zip(names), urls, strict=True)
    ]
    if not any(is_zip):
        return all_files
    ft = file_types()
    first_type: dict[str, Any] = {}
    for e, t in zip(rc.vals(ft["ext"]), rc.vals(ft["type"]), strict=True):
        first_type.setdefault(e, t)

    from pytacheck.utils import pb as make_pb

    expanded: list[pd.DataFrame] = []
    consumed: list[int] = []
    zpb = make_pb(sum(is_zip), "Reading zip contents [:bar] :current/:total")
    try:
        for i in [k for k, z in enumerate(is_zip) if z]:
            try:
                peek = zip_peek(urls[i])
            except Exception:
                peek = None
            with contextlib.suppress(Exception):  # a progress display must never break a run
                zpb.tick()
            if peek is not None and len(peek) > 0:
                expanded.append(_zip_rows(all_files, i, names[i], urls[i], peek, first_type))
                consumed.append(i)
    finally:
        with contextlib.suppress(Exception):
            zpb.terminate()
    if not consumed:
        return all_files
    kept = all_files.drop(index=consumed)
    return bind_rows([kept, bind_rows(expanded)]).reset_index(drop=True)


def _zip_rows(
    all_files: pd.DataFrame,
    i: int,
    archive_name: Any,
    archive_url: Any,
    peek: pd.DataFrame,
    first_type: Mapping[Any, Any],
) -> pd.DataFrame:
    """Row *i* of *all_files* (a ``.zip``) repeated for each entry *peek* lists.

    R: the entry's name and size replace the archive's, the inner path is
    prefixed with the archive's name, ``file_url`` is ``NA`` and
    ``archive_url`` / ``archive_member`` locate the entry inside the archive;
    ``file_type`` is re-derived from the entry's extension.
    """
    members = [str(n) for n in rc.vals(peek["name"])]
    n = len(members)
    rows = all_files.iloc[[i] * n].reset_index(drop=True)
    rows["file_name"] = pd.Series([rc.r_basename(m) for m in members], dtype="string")
    prefix = "NA" if rc._na(archive_name) else str(archive_name)
    rows["file_path"] = pd.Series([f"{prefix}/{m}" for m in members], dtype="string")
    rows["file_size"] = pd.Series([rc._as_num(v) for v in rc.vals(peek["size"])], dtype="float64")
    rows["file_url"] = pd.Series([None] * n, dtype="string")
    rows["archive_url"] = pd.Series([archive_url] * n, dtype="string")
    rows["archive_member"] = pd.Series(members, dtype="string")
    if "file_type" in rows.columns:
        types = []
        for name in rc.vals(rows["file_name"]):
            t = first_type.get(rc.file_ext(name).lower())
            types.append("file" if rc._na(t) else t)
        rows["file_type"] = pd.Series(types, dtype="string")
    return rows


def _classify(
    all_files: pd.DataFrame, model: str | None, params: dict[str, Any], paper: Any
) -> tuple[pd.DataFrame, Any, bool]:
    """Name/path-only classification and study grouping (``data_group_llm()``)."""
    from pytacheck.datacheck.files import (
        _data_doc_role,
        _data_group_from_path,
        data_classify_files,
        data_group_llm,
    )

    all_files = all_files.copy()
    names = rc.vals(all_files["file_name"])
    paths = rc.vals(all_files["file_path"])
    names_l = [None if rc._na(n) else n for n in names]
    paths_l = [None if rc._na(p) else p for p in paths]
    all_files["data_type"] = pd.Series(data_classify_files(names_l, paths_l), dtype="string")
    all_files["doc_role"] = pd.Series(_data_doc_role(names_l), dtype="string")

    path_for_group = [
        n if (p is None or p == "") else p for p, n in zip(paths_l, names_l, strict=True)
    ]
    from_path = _data_group_from_path(path_for_group) if path_for_group else []
    study = grepl("study[-_]?[0-9]|/ex[0-9]|/pilot[0-9]", [rc.r_tolower(p) for p in path_for_group])
    roles = rc.vals(all_files["doc_role"])
    is_root_readme = [
        (not rc._na(r)) and r in ("readme", "license") and g is None and not s
        for r, g, s in zip(roles, from_path, study, strict=True)
    ]

    group: list[Any] = [None] * len(all_files)
    roster_check = None
    group_no_evidence = False
    if len(all_files) > 0 and not all(is_root_readme):
        sel = [not r for r in is_root_readme]
        grp = data_group_llm(rc.take(all_files, sel), model=model, params=params, paper=paper)
        if grp is not None:
            it = iter(rc.vals(grp["group"]))
            group = [next(it) if s else None for s in sel]
            roster_check = grp.attrs.get("roster_check")
            group_no_evidence = grp.attrs.get("no_evidence") is True
    all_files["group"] = pd.Series([None if rc._na(g) else g for g in group], dtype="string")
    return all_files, roster_check, group_no_evidence


def _summarise_repos(repos: pd.DataFrame, all_files: pd.DataFrame) -> pd.DataFrame:
    """``full_join(repos, all_files, by = c("paper_id", "repo_url")) |> summarise(.by = ...)``."""

    def key(v: Any) -> Any:
        return None if rc._na(v) else v

    file_pid = [key(v) for v in rc.vals(all_files["paper_id"])]
    file_url = [key(v) for v in rc.vals(all_files["repo_url"])]
    file_name = rc.vals(all_files["file_name"])
    file_type = rc.vals(all_files["file_type"])
    by_key: dict[tuple[Any, Any], list[int]] = {}
    for i, k in enumerate(zip(file_pid, file_url, strict=True)):
        by_key.setdefault(k, []).append(i)

    groups: dict[tuple[Any, ...], list[int]] = {}
    matched: set[tuple[Any, Any]] = set()
    for pid, url, rtype, err in zip(
        rc.vals(repos["paper_id"]),
        rc.vals(repos["repo_url"]),
        rc.vals(repos["repo_type"]),
        rc.vals(repos["repo_error"]),
        strict=True,
    ):
        k = (key(pid), key(url))
        g = (k[0], k[1], key(rtype), key(err))
        rows = groups.setdefault(g, [])
        if k in by_key:
            matched.add(k)
            rows.extend(by_key[k])
        else:
            rows.append(-1)
    for k, idx in by_key.items():
        if k not in matched:
            groups.setdefault((k[0], k[1], None, None), []).extend(idx)

    def count(idx: list[int], test: Any) -> int:
        return sum(1 for i in idx if i >= 0 and test(i))

    out = {
        "paper_id": [],
        "repo_url": [],
        "repo_type": [],
        "repo_error": [],
        "files_n": [],
        "files_data": [],
        "files_code": [],
        "files_readme": [],
        "files_zip": [],
    }
    for (pid, url, rtype, err), idx in groups.items():
        out["paper_id"].append(pid)
        out["repo_url"].append(url)
        out["repo_type"].append(rtype)
        out["repo_error"].append(err)
        out["files_n"].append(count(idx, lambda i: not rc._na(file_name[i])))
        for col, t in (
            ("files_data", "data"),
            ("files_code", "code"),
            ("files_readme", "readme"),
            ("files_zip", "archive"),
        ):
            out[col].append(count(idx, lambda i, t=t: file_type[i] == t))
    frame = {k: pd.Series(v, dtype="string") for k, v in out.items() if k[:5] != "files"}
    frame.update({k: pd.Series(v, dtype="Int64") for k, v in out.items() if k[:5] == "files"})
    return pd.DataFrame(frame)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _is_are(n: int) -> str:
    return "is" if n == 1 else "are"


def _split_levels(values: Sequence[Any]) -> list[str]:
    """``levels(factor(x))``: the sorted distinct non-missing values (R collation)."""
    return sorted({v for v in values if not rc._na(v)}, key=r_sort_key)


def _report(
    all_files: pd.DataFrame,
    repos: pd.DataFrame,
    is_readme: list[bool],
    roster_check: Any,
) -> tuple[list[Any], list[str], dict[str, Any]]:
    """The report blocks, the summary lines and the traffic-light inputs."""
    from pytacheck.fileinfo.naming import check_file_naming
    from pytacheck.report.blocks import collapse_section, link, scroll_table

    files_n = int(repos["files_n"].sum())
    n_repos = len(repos)
    summary_files = (
        f"We found {files_n} file{plural(files_n)} in {n_repos} "
        f"{plural(n_repos, 'repository', 'repositories')}."
    )

    # empty repos
    repo_no_files = int((repos["files_n"] == 0).sum())
    summary_repo = report_repo = None
    if repo_no_files > 0:
        summary_repo = (
            f"We found {repo_no_files} empty {plural(repo_no_files, 'repository', 'repositories')}."
        )
        report_repo = "Double-check permissions on repositories with no detectable files."

    # missing READMEs
    readme_n = sum(is_readme)
    repo_no_readme = int((repos["files_readme"] == 0).sum())
    summary_readme = (
        f"We found {readme_n} README file{plural(readme_n)} and {repo_no_readme} "
        f"{plural(repo_no_readme, 'repository', 'repositories')} without READMEs."
    )
    report_readme = (
        _README_REPORT if repo_no_readme > 0 else "README files were found in all repositories."
    )

    names = rc.vals(all_files["file_name"])
    ftypes = rc.vals(all_files["file_type"])

    # zip files
    zip_n = int(repos["files_zip"].sum())
    summary_zip = report_zip = None
    if zip_n > 0:
        zip_files = [
            n for n, t in zip(names, ftypes, strict=True) if not rc._na(t) and t == "archive"
        ]
        nonzip = [
            n
            for n, z in zip(zip_files, grepl("[.]zip$", zip_files, ignore_case=True), strict=True)
            if not z
        ]
        if nonzip:
            report_zip = _ZIP_REPORT.format(", ".join(_na_str(n) for n in nonzip))
        summary_zip = f"We found {zip_n} archive file{plural(zip_n)}."

    # proprietary E-Prime binary files
    edat = [
        n
        for n, hit in zip(names, grepl("[.](edat2?|emrg2?)$", names, ignore_case=True), strict=True)
        if not rc._na(n) and hit
    ]
    summary_edat = report_edat = None
    if edat:
        txt = [
            n
            for n, hit in zip(names, grepl("[.]txt$", names, ignore_case=True), strict=True)
            if hit
        ]
        txt_stems = {rc.r_tolower(rc.file_path_sans_ext(n)) for n in txt}
        missing_txt = [e for e in edat if rc.r_tolower(rc.file_path_sans_ext(e)) not in txt_stems]
        report_edat = _EDAT_REPORT.format(
            n=len(edat), s=plural(len(edat)), files=", ".join(edat[:8])
        )
        if missing_txt:
            report_edat += _EDAT_MISSING.format(
                n=len(missing_txt), v="s" if len(missing_txt) == 1 else "ve"
            )
        else:
            report_edat += _EDAT_OK
        summary_edat = f"We found {len(edat)} proprietary E-Prime file{plural(len(edat))}."

    # restricted-access repositories
    errors = rc.vals(repos["repo_error"])
    repo_urls = rc.vals(repos["repo_url"])
    restricted = [u for u, e in zip(repo_urls, errors, strict=True) if e == "restricted access"]
    summary_restricted = report_restricted = None
    if restricted:
        n = len(restricted)
        report_restricted = _RESTRICTED_REPORT.format(
            n=n,
            repos=plural(n, "repository", "repositories"),
            urls=", ".join(_na_str(u) for u in restricted),
            verb="contains" if n == 1 else "contain",
        )
        summary_restricted = (
            f"We found {n} {plural(n, 'repository', 'repositories')} with restricted-access files."
        )

    # registrations pointing to a closed source project
    mirror = [
        not rc._na(e)
        and bool(grepl(r"^closed registration source \(files retrieved from registration: ", e))
        for e in errors
    ]
    mirrored = [u for u, m in zip(repo_urls, mirror, strict=True) if m]
    mirrored_src = [
        sub(
            r"^closed registration source \(files retrieved from registration: (.+)\)$",
            r"\1",
            e,
        )
        for e, m in zip(errors, mirror, strict=True)
        if m
    ]
    unreachable = [
        u for u, e in zip(repo_urls, errors, strict=True) if e == "closed registration source"
    ]
    report_closed: list[str] = []
    summary_closed: list[str] = []
    if unreachable:
        n = len(unreachable)
        report_closed.append(
            _CLOSED_REPORT.format(
                n=n,
                projects=plural(n, "project", "projects"),
                urls=", ".join(_na_str(u) for u in unreachable),
                is_are=_is_are(n),
                it_is="it is" if n == 1 else "they are",
                its="its" if n == 1 else "their",
            )
        )
        summary_closed.append(
            _CLOSED_SUMMARY.format(
                n=n, projects=plural(n, "project", "projects"), is_are=_is_are(n)
            )
        )
    if mirrored:
        n = len(mirrored)
        lines = "\n".join(
            f"- **{_na_str(u)}** is closed; its files were retrieved from the OSF registration {s}."
            for u, s in zip(mirrored, mirrored_src, strict=True)
        )
        report_closed.append(
            _MIRROR_REPORT.format(
                n=n,
                projects=plural(n, "project", "projects"),
                is_are=_is_are(n),
                it="it" if n == 1 else "them",
                lines=lines,
            )
        )
        summary_closed.append(
            _MIRROR_SUMMARY.format(
                n=n,
                projects=plural(n, "project", "projects"),
                is_are=_is_are(n),
                its="its" if n == 1 else "their",
            )
        )

    # unclassifiable files
    dtypes = rc.vals(all_files["data_type"])
    unknown = [n for n, d in zip(names, dtypes, strict=True) if not rc._na(d) and d == "unknown"]
    n_unknown = len(unknown)
    summary_unknown = report_unknown = None
    if n_unknown > 0:
        report_unknown = _UNKNOWN_REPORT.format(
            n=n_unknown, s=plural(n_unknown), files=", ".join(_na_str(u) for u in unknown[:10])
        )
        summary_unknown = f"We could not classify {n_unknown} file{plural(n_unknown)}."

    # study roster mismatch
    report_roster = summary_roster = None
    if _roster_mismatch(roster_check):
        parts = []
        missing = list(roster_check.get("missing") or [])
        extra = list(roster_check.get("extra") or [])
        if missing:
            parts.append(
                f"the manuscript names {plural(len(missing), 'a study', 'studies')} "
                f"({', '.join(missing)}) with no matching files in the repository"
            )
        if extra:
            parts.append(
                f"the repository separates out {plural(len(extra), 'a study', 'studies')} "
                f"({', '.join(extra)}) not named in the manuscript"
            )
        report_roster = _ROSTER_REPORT.format(parts="; ".join(parts))
        summary_roster = _ROSTER_SUMMARY

    # file-naming conventions
    paths = rc.vals(all_files["file_path"])
    naming_issues = check_file_naming(names, file_path=paths, data_type=dtypes)
    sev = rc.vals(naming_issues["severity"])
    n_naming_bad = sum(1 for s in sev if s == "bad")
    n_naming_suggest = len(
        rc.unique(
            n
            for n, s in zip(rc.vals(naming_issues["file_name"]), sev, strict=True)
            if s == "suggestion"
        )
    )
    naming_by_paper = _naming_by_paper(all_files, naming_issues)

    report_naming: list[Any] = []
    summary_naming = None
    if len(naming_issues) > 0:
        naming_tbl = naming_issues.copy()
        naming_tbl.columns = ["File", "Rule", "Severity", "Detail"]
        lead = _NAMING_BAD if n_naming_bad > 0 else _NAMING_OK
        report_naming = [
            "#### File Naming",
            f"{lead} We found {n_naming_bad} naming problem{plural(n_naming_bad)} that should be "
            f"fixed, and {n_naming_suggest} file{plural(n_naming_suggest)} with a naming "
            "suggestion (not required, but a good habit).",
            scroll_table(naming_tbl, maxrows=10),
        ]
        if n_naming_bad > 0:
            summary_naming = (
                f"We found {n_naming_bad} file naming problem{plural(n_naming_bad)} to fix."
            )
        elif n_naming_suggest > 0:
            summary_naming = (
                f"We found {n_naming_suggest} file{plural(n_naming_suggest)} with a naming "
                "suggestion."
            )

    # classification dropdown
    report_classification: list[Any] = []
    class_mask = [not rc._na(d) for d in dtypes]
    if any(class_mask):
        groups = rc.vals(all_files["group"])
        sections: list[Any] = []
        for dt in _split_levels(dtypes):
            idx = [i for i, d in enumerate(dtypes) if d == dt]
            tbl = pd.DataFrame(
                {
                    "File": pd.Series([names[i] for i in idx], dtype="string"),
                    "Group": pd.Series(
                        ["—" if rc._na(groups[i]) else groups[i] for i in idx], dtype="string"
                    ),
                    "Path": pd.Series([paths[i] for i in idx], dtype="string"),
                }
            )
            sections.append(f"**{dt}** ({len(idx)} file{plural(len(idx))})")
            sections.append(scroll_table(tbl, maxrows=10))
        callout = collapse_section(
            sections, title="See how every file was classified and grouped", callout="note"
        )
        report_classification = ["#### File Classification", *_as_blocks(callout)]

    # tables
    furls = rc.vals(all_files["file_url"])
    linked = link(furls, names) if len(furls) else []
    report_tbl = pd.DataFrame(
        {
            "Repository": pd.Series(rc.vals(all_files["repo_url"]), dtype="string"),
            "File": pd.Series(
                [n if rc._na(lk) else lk for lk, n in zip(linked, names, strict=True)],
                dtype="string",
            ),
            "Size": pd.Series(
                [rc.size_label(s) for s in rc.vals(all_files["file_size"])], dtype="string"
            ),
            "Type": pd.Series(rc.vals(all_files["file_type"]), dtype="string"),
        }
    )
    repo_tbl = repos.drop(columns="paper_id").copy()
    repo_tbl["repo_url"] = pd.Series(
        link(rc.vals(repo_tbl["repo_url"])) if len(repo_tbl) else [], dtype="string"
    )
    repo_tbl.columns = [
        "Repository",
        "Platform",
        "Error",
        "All Files",
        "Data Files",
        "Code Files",
        "READMEs",
        "Archives",
    ]
    if repo_tbl["Error"].isna().all():
        repo_tbl = repo_tbl.drop(columns="Error")

    blocks: tuple[Any, ...] = (
        report_repo,
        "#### Repositories" if len(repo_tbl) else None,
        scroll_table(repo_tbl, maxrows=10),
        "#### Files" if len(report_tbl) else None,
        scroll_table(report_tbl, maxrows=10),
        report_readme,
        report_zip,
        report_edat,
        report_restricted,
        *report_closed,
        report_unknown,
        report_roster,
        *report_naming,
        *report_classification,
    )
    report: list[Any] = [b for b in blocks if b is not None]

    summary = [
        s
        for s in (
            summary_repo,
            summary_files,
            summary_readme,
            summary_zip,
            summary_edat,
            summary_restricted,
            *summary_closed,
            summary_unknown,
            summary_roster,
            summary_naming,
        )
        if s is not None
    ]
    flags = {
        "zip_n": zip_n,
        "repo_no_readme": repo_no_readme,
        "n_unknown": n_unknown,
        "roster": report_roster is not None,
        "n_naming_bad": n_naming_bad,
        "n_closed_unreachable": len(unreachable),
        "naming_issues_by_paper": naming_by_paper,
    }
    return report, summary, flags


def _as_blocks(x: Any) -> list[Any]:
    return list(x) if isinstance(x, list) else [x]


def _na_str(x: Any) -> str:
    return "NA" if rc._na(x) else str(x)


def _roster_mismatch(roster_check: Any) -> bool:
    if not isinstance(roster_check, Mapping):
        return False
    roster = roster_check.get("roster") or []
    return len(roster) > 0 and (
        len(roster_check.get("missing") or []) > 0 or len(roster_check.get("extra") or []) > 0
    )


def _naming_by_paper(all_files: pd.DataFrame, naming_issues: pd.DataFrame) -> pd.DataFrame:
    """``check_file_naming()`` per paper, tagged with ``paper_id`` (R: ``split()`` by paper)."""
    from pytacheck.fileinfo.naming import check_file_naming

    if len(all_files) == 0:
        return naming_issues.iloc[0:0].copy()
    pids = rc.vals(all_files["paper_id"])
    levels = _split_levels(pids)
    if not levels:
        return naming_issues.iloc[0:0].copy()
    names = rc.vals(all_files["file_name"])
    paths = rc.vals(all_files["file_path"])
    dtypes = rc.vals(all_files["data_type"])
    rows_of: dict[Any, list[int]] = {}
    for i, p in enumerate(pids):
        if p is not None:
            rows_of.setdefault(p, []).append(i)
    parts = []
    for pid in levels:
        idx = rows_of[pid]
        pf = check_file_naming(
            [names[i] for i in idx],
            file_path=[paths[i] for i in idx],
            data_type=[dtypes[i] for i in idx],
        )
        if len(pf) > 0:
            pf = pf.copy()
            pf["paper_id"] = pd.Series([pid] * len(pf), dtype="string")
        parts.append(pf)
    return bind_rows(parts).reset_index(drop=True)


def _summary_table(
    repos: pd.DataFrame, all_files: pd.DataFrame, flags: dict[str, Any], roster_check: Any
) -> pd.DataFrame:
    """One row per paper: repository and file counts, unknown files, naming problems."""
    counts = ["files_n", "files_data", "files_code", "files_readme", "files_zip"]
    rows: dict[Any, dict[str, int]] = {}
    for rec in repos.loc[:, ["paper_id", *counts]].itertuples(index=False):
        pid = None if rc._na(rec[0]) else rec[0]
        row = rows.setdefault(pid, {"repo_n": 0, **dict.fromkeys(counts, 0)})
        row["repo_n"] += 1
        for c, v in zip(counts, rec[1:], strict=True):
            row[c] += int(v)

    unknown: dict[Any, int] = {}
    for pid, d in zip(rc.vals(all_files["paper_id"]), rc.vals(all_files["data_type"]), strict=True):
        k = None if rc._na(pid) else pid
        unknown[k] = unknown.get(k, 0) + (1 if (not rc._na(d) and d == "unknown") else 0)
    naming = flags["naming_issues_by_paper"]
    bad: dict[Any, int] = {}
    if len(naming) > 0 and "paper_id" in naming.columns:
        for pid, s in zip(rc.vals(naming["paper_id"]), rc.vals(naming["severity"]), strict=True):
            k = None if rc._na(pid) else pid
            bad[k] = bad.get(k, 0) + (1 if s == "bad" else 0)
    elif len(naming) > 0:
        raise rc.RError("object 'paper_id' not found")

    pids = list(rows)
    mismatch = _roster_mismatch(roster_check)
    return pd.DataFrame(
        {
            "paper_id": pd.Series(pids, dtype="string"),
            "repo_n": pd.Series([rows[p]["repo_n"] for p in pids], dtype="Int64"),
            **{c: pd.Series([rows[p][c] for p in pids], dtype="Int64") for c in counts},
            "files_unknown": pd.Series([unknown.get(p, 0) for p in pids], dtype="Int64"),
            "naming_issues": pd.Series([bad.get(p, 0) for p in pids], dtype="Int64"),
            "roster_mismatch": pd.Series([mismatch] * len(pids), dtype="boolean"),
        }
    )
