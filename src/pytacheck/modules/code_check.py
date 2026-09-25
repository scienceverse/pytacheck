"""Code Check (port of ``inst/modules/code_check.R``).

The per-file analysis loop, the summary table, the version-pinning report and
the manifest merge live in :mod:`pytacheck.modules._code_check`; the code
scanners are :mod:`pytacheck.codecheck.core`.
"""

from __future__ import annotations

import math
import os
import warnings
from typing import Any

import pandas as pd

from pytacheck.module import get_prev_outputs, module
from pytacheck.modules._code_check import (
    CHECKED_LANGS,
    LISTED_LANGS,
    analyse_files,
    col,
    collected_frame,
    is_na,
    merge_manifests,
    seed_analysis_cols,
    summary_table,
    version_pin_report,
)
from pytacheck.report import link, plural, scroll_table

_INTRO = (
    "Below, we describe some best coding practices and give the results of automatic "
    "evaluation of these practices in the code files below. This check may miss things or "
    "produce false positives if your scripts are less typical."
)
_LIBRARY = (
    "Best programming practice is to load all required libraries/imports in one block near "
    "the top of the code."
)
_ABSOLUTE = (
    "Best programming practice is to use relative file paths (e.g., './files') instead of "
    "absolute file paths (e.g., 'C://Lakens/project_dir/files') as these folder names do not "
    "exist on other computers."
)
_SETWD = (
    "Best programming practice is to avoid `setwd()` in analysis code: it hardcodes an "
    "assumption about the working directory (often an absolute path on the author's own "
    "machine), so the code breaks when run anywhere else. Keep the working directory as the "
    "caller sets it and use relative paths."
)
_INSTALL = (
    "Best programming practice is to avoid `install.packages()` in analysis code: it installs "
    "software on whoever runs the script without asking, which is at minimum disruptive (an "
    "unexpected install, network access, a package version the author never tested against) "
    "and at worst a security risk if it points at an untrusted repository. Document required "
    "packages instead (a README, a `renv.lock`/`DESCRIPTION`)."
)
_COMMENTS = (
    "Best programming practice is to add comments to code, to explain what the code does (to "
    "yourself in the future, or peers who want to re-use your code)."
)
_NO_PACKAGES = (
    "No packages/libraries were detected as loaded in the code files. (This check reads R and "
    "Python imports; other languages are not scanned for packages.) Even when no imports are "
    "detected, it is good practice to record the exact package versions the analysis depended "
    'on \u2014 see "Reproducible Environment" below.'
)

# R: col_labels of the table of code files
_COL_LABELS = {
    "file_name": "File Name",
    "percentage_comment": "% Comments",
    "loaded_files_missing": "Missing Files",
    "code_abs_path": "Absolute Paths",
    "library_max_between": "Code Between Libraries",
}


@module(
    title="Code Check",
    description="""
        This module retrieves information from repositories checked by repo_check about code files (R, SAS, SPSS, Stata).
    """,  # noqa: E501
    details="""
        The Code Check module checks R, Rmd, Qmd, SAS, SPSS, and Stata files, using regular expressions to check the code. The regular expression search will detect the number of comments, the lines at which libraries/imports are loaded, attempts to detect absolute paths to files, and lists files that are loaded, and checks if these files are in the repository. The module will return suggestions to improve the code if there are no comments, if libraries/imports are loaded in lines further than 4 lines apart, if files that are loaded are not in the repository, and if absolute file paths are found.

        The regular expressions can miss information in code files, or falsely detect parts of the code as a fixed file path. Libraries/imports might be loaded in one block, even if there are more than 3 intermittent lines. The package was validated internally on papers published in Psychological Science. There might be valid reasons why some loaded files can\u2019t be shared, but the module can\u2019t evaluate these reasons, and always gives a warning.

        The module also checks whether the repository pins the R/package versions the analysis actually depended on: an `renv.lock` file (parsed for the R version and every locked package + its version/source), a `sessionInfo()`/`sessioninfo::session_info()` text dump (matched by filename \u2014 `sessionInfo.txt`/`session_info.txt` and similar, or embedded in a README), or a `groundhog::groundhog.library()`/`checkpoint::checkpoint()` date-pin call in the code (a bare `library(groundhog)`/`library(checkpoint)` does not count \u2014 the pinning call itself must be present). When none of these is found, the report flags it as a reproducibility gap: package versions may drift between when the analysis was run and any later reproduction attempt.

        If you want to extend the package to perform additional checks on code files, or make the checks work on other types of code files, reach out to the Metacheck development team.
    """,  # noqa: E501
    keywords=["results"],
    requires=["network"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Raphael Merz <r.t.p.merz@tue.nl>",
    ],
    params={
        "paper": "a paper object or paperlist object, or NULL to check local files only "
        "(see [test_paper()])",
        "local_path": "optional path to a local directory. When provided, all files in that "
        "directory (recursively) are added to the file list alongside any files found via "
        "`repo_check`.",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "download": "if TRUE (default), download the code files to be checked from online "
        "repositories so they are read locally. Set FALSE to stream each file from its URL "
        "instead.",
        "max_file_size": "largest single file to download, in MB (default 100). Size caps are "
        "an upfront, all-or-nothing gate per repository; set `Inf` for no cap.",
        "max_download_size": "largest total download per repository, in MB (default 500). "
        "Set `Inf` for no cap.",
        "max_files_per_repo": "largest file COUNT a single repository may have before it is "
        "refused outright (default `Inf`, no cap) -- see [download_repo_files()]'s own "
        "parameter of the same name.",
        "cache": "if TRUE, keep downloaded files in a persistent on-disk cache (see "
        "[repo_cache_dir()]) so they are reused on later runs. If FALSE (the default), "
        "download to a temporary directory discarded when the session ends. Clear the cache "
        "with [repo_cache_clear()].",
        "skip_on_api_limit": "if TRUE, a 429 that carries a confirmed rate-limit-exhausted "
        "signal (e.g. Dryad's per-day quota) skips that file instead of waiting out the "
        "host's own reset. Default FALSE (always wait for a confirmed reset) -- see "
        "[download_repo_files()]'s own parameter of the same name.",
        "manifest": "optional path to a metacheck manifest directory or `*.manifest.json` "
        "file. When given, the distinct packages loaded across the paper's code are merged "
        "into the manifest's `code$packages` section, and any code file this module tried and "
        "failed to download (after retries) is recorded in `code$files_failed` (`file_name`, "
        "`repo_url`, `file_url`, `error` per file -- the `file_url` is a direct link to fetch "
        "it manually) (see [manifest_merge()]), preserving any `files`/`provenance` written by "
        "`data_check`. A directory resolves to `<paper_id>.manifest.json` inside it; the "
        "manifest is created if it does not yet exist.",
    },
)
def code_check(
    paper: Any,
    local_path: str | os.PathLike[str] | None = None,
    local_only: bool = False,
    download: bool = True,
    max_file_size: float = 100,
    max_download_size: float = 500,
    max_files_per_repo: float = math.inf,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    manifest: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Port of ``inst/modules/code_check.R::code_check()``.

    Checks the code files (R, R Markdown, Quarto, Python and notebooks, SAS,
    SPSS, Stata, Mplus, MATLAB; JASP files are listed but not read) of the
    paper's repositories -- from ``data_check``'s ``structure`` or
    ``repo_check``'s ``table`` earlier in the chain, else a fresh
    ``repo_check`` run -- for comments, absolute paths, ``setwd()`` and
    ``install.packages()`` calls, scattered library loading, loaded files
    missing from the repository, R parse errors, loaded packages, and whether
    the R/package environment is pinned (``renv.lock``, ``sessionInfo()``,
    ``groundhog``/``checkpoint``).
    """
    from pytacheck.codecheck.core import (
        _code_expand_html,
        _code_expand_mplus,
        _code_expand_smcl,
        _code_expand_spv,
        _code_expand_zip,
        _code_predownload,
        _code_version_pin_check,
        _repo_file_counts,
        code_lang,
        code_packages,
    )

    # data_check's structure is a superset of repo_check's table (archives
    # expanded); a local_path forces a fresh repo_check
    all_files: pd.DataFrame | None = None
    if local_path is None:
        all_files = get_prev_outputs("data_check", "structure")
        if all_files is None:
            all_files = get_prev_outputs("repo_check", "table")
    if all_files is None:
        from pytacheck.module import module_run

        if local_path is not None:
            mo = module_run(
                paper, "repo_check", local_path=local_path, local_only=local_only, cache=cache
            )
        else:
            mo = module_run(paper, "repo_check", local_only=local_only, cache=cache)
        all_files = mo.table
        if all_files is None:
            all_files = pd.DataFrame(
                {"file_name": pd.Series([], dtype="string"), "repo_url": pd.Series([], dtype="string")}
            )
    all_files = all_files.reset_index(drop=True).copy()
    all_files.attrs = {}

    def set_language(df: pd.DataFrame) -> pd.DataFrame:
        langs = code_lang(col(df, "file_name")) if len(df) else []
        df["language"] = pd.Series(langs, index=df.index, dtype="string")
        return df

    set_language(all_files)

    # one download pass for everything the steps below might need
    predl_gated: list[pd.DataFrame | None] = []
    failed_parts: list[pd.DataFrame | None] = []
    if download is True:
        all_files = _code_predownload(
            all_files,
            max_file_size,
            max_download_size,
            cache,
            skip_on_api_limit,
            max_files_per_repo,
        )
        predl_gated.append(all_files.attrs.get("gated"))
        failed_parts.append(all_files.attrs.get("failed"))
        all_files = all_files.copy()
        all_files.attrs = {}

    # rendered outputs whose code can be recovered (.spv, .smcl, .out, .html)
    # and unexpanded remote .zip archives
    size_args = (max_file_size, max_download_size, cache, skip_on_api_limit, max_files_per_repo)
    for pattern, expand in (
        (r"\.spv$", _code_expand_spv),
        (r"\.smcl$", _code_expand_smcl),
        (r"\.out$", _code_expand_mplus),
        (r"\.html?$", _code_expand_html),
    ):
        if _any_name(all_files, pattern):
            all_files = set_language(expand(all_files, *size_args).reset_index(drop=True))
    if _any_name(all_files, r"\.zip$"):
        all_files = set_language(_code_expand_zip(all_files, skip_on_api_limit).reset_index(drop=True))
    all_files.attrs = {}

    ## find relevant code files ----
    language = col(all_files, "language")
    relevant = [lang in LISTED_LANGS for lang in language]
    code_files = all_files.loc[relevant].reset_index(drop=True)
    code_langs = col(code_files, "language")
    lang_parts = [f"{sum(1 for x in code_langs if x == lang)} {lang}" for lang in LISTED_LANGS]
    summary_code = (
        f"We found {', '.join(lang_parts[:-1])}, and {lang_parts[-1]} code "
        f"file{plural(len(code_files))}."
    )
    is_checked = [lang in CHECKED_LANGS for lang in code_langs]
    checked_files = code_files.loc[is_checked].reset_index(drop=True)

    # no relevant code files found ----
    if len(code_files) == 0:
        from pytacheck.papers.tables import paper_id

        pids = paper_id(paper) if paper is not None else []
        return {
            "table": code_files,
            "traffic_light": "na",
            "summary_text": summary_code,
            "summary_table": pd.DataFrame(
                {
                    "paper_id": pd.Series(pids, dtype="string"),
                    "code_n": pd.Series([0.0] * len(pids), dtype="float64"),
                }
            ),
            "na_replace": {"code_n": 0},
        }

    # download the code files the pre-pass could not classify yet
    if download is True and "file_url" in checked_files.columns:
        need_dl = _need_download(checked_files)
        if any(need_dl):
            from pytacheck.archives.download import download_repo_files

            dl = download_repo_files(
                checked_files.loc[need_dl].reset_index(drop=True),
                max_file_size=max_file_size,
                max_download_size=max_download_size,
                max_files_per_repo=max_files_per_repo,
                repo_file_counts=_repo_file_counts(checked_files),
                cache=cache,
                skip_on_api_limit=skip_on_api_limit,
            )
            checked_files = _set_file_location(checked_files, need_dl, dl)
            predl_gated.append(getattr(dl, "attrs", {}).get("gated"))
            failed_parts.append(getattr(dl, "attrs", {}).get("failed"))

    # repositories refused by the size caps: each refusal once
    gated = _bind(predl_gated)
    if gated is not None and len(gated) > 0 and "message" in gated.columns:
        for m in dict.fromkeys(col(gated, "message")):
            summary_code = f"{summary_code} {'NA' if m is None else m}"
            warnings.warn("NA" if m is None else str(m), stacklevel=2)

    # Check code ----
    rows, r_text_by_paper = analyse_files(checked_files, all_files)
    if not rows:
        # nothing analysed (every repository gated, or no file locations)
        code_files = seed_analysis_cols(code_files)
    else:
        from pytacheck._r.frames import bind_rows

        collected = collected_frame(checked_files, rows)
        unchecked = code_files.loc[[not c for c in is_checked]]
        code_files = bind_rows([collected, unchecked]).reset_index(drop=True)
    checked = code_files["checked"].astype("boolean").fillna(False)
    code_files["checked"] = checked.astype("boolean")

    # Reporting ----
    names = col(code_files, "file_name")
    has = code_files.columns.__contains__

    def nums(c: str) -> list[float | None]:
        return [None if v is None else float(v) for v in col(code_files, c)] if has(c) else []

    def which(c: str, cond: Any) -> list[int]:
        return [i for i, v in enumerate(nums(c)) if v is not None and not math.isnan(v) and cond(v)]

    ## library ----
    library_issue = [names[i] for i in which("library_max_between", lambda v: v > 3)]
    if not library_issue:
        report_library = (
            f"{_LIBRARY} In all code files, libraries/imports were loaded in one block."
        )
        summary_library = "All libraries/imports were loaded in one block."
    else:
        report_library = (
            f"{_LIBRARY} In {len(library_issue)} code files, libraries/imports were at multiple "
            "places (i.e., with more than 3 non-comment lines in between)."
        )
        summary_library = "Libraries/imports were loaded in multiple places."

    ## absolute paths ----
    abs_rows = which("code_abs_path", lambda v: v > 0)
    absolute_issues = [names[i] for i in abs_rows]
    report_table_absolute = None
    if not absolute_issues:
        report_absolute = (
            f"{_ABSOLUTE} No absolute file paths were found in any of the code files."
        )
        summary_absolute = "No absolute file paths were found."
    else:
        n = len(absolute_issues)
        report_absolute = (
            f"{_ABSOLUTE} The following absolute file paths were found in {n} code "
            f"file{plural(n)}. However, these may be false positives in code like "
            "`paste0(dir, '/file.csv')`. "
        )
        summary_absolute = "Absolute file paths were found."
        report_table_absolute = _subtable(
            code_files, abs_rows, {"file_name": "File name", "absolute_paths": "Absolute paths found"}
        )

    ## setwd() ----
    setwd_rows = which("code_setwd", lambda v: v > 0)
    setwd_issues = [names[i] for i in setwd_rows]
    report_table_setwd = None
    if not setwd_issues:
        report_setwd = f"{_SETWD} No `setwd()` calls were found in any of the code files."
        summary_setwd = "No setwd() calls were found."
    else:
        n = len(setwd_issues)
        report_setwd = f"{_SETWD} `setwd()` calls were found in {n} code file{plural(n)}."
        summary_setwd = "setwd() calls were found."
        report_table_setwd = _subtable(
            code_files, setwd_rows, {"file_name": "File name", "setwd_calls": "setwd() calls found"}
        )

    ## install.packages() ----
    install_rows = which("code_install_packages", lambda v: v > 0)
    install_packages_issues = [names[i] for i in install_rows]
    report_table_install_packages = None
    if not install_packages_issues:
        report_install_packages = (
            f"{_INSTALL} No `install.packages()` calls were found in any of the code files."
        )
        summary_install_packages = "No install.packages() calls were found."
    else:
        n = len(install_packages_issues)
        report_install_packages = (
            f"{_INSTALL} `install.packages()` calls were found in {n} code file{plural(n)}."
        )
        summary_install_packages = "install.packages() calls were found."
        report_table_install_packages = _subtable(
            code_files,
            install_rows,
            {
                "file_name": "File name",
                "install_packages_calls": "install.packages() calls found",
            },
        )

    ## Comments ----
    pct = nums("percentage_comment")
    n_analysed = sum(1 for v in pct if v is not None and not math.isnan(v))
    zero_rows = which("percentage_comment", lambda v: v == 0)
    comment_issue = [names[i] for i in zero_rows]
    if has("has_docstring"):
        docs = col(code_files, "has_docstring")
        n_docstring_only = sum(
            1 for i in zero_rows if code_langs_of(code_files, i) == "Python" and docs[i] is True
        )
    else:
        n_docstring_only = 0
    if n_analysed == 0:
        report_comments = f"{_COMMENTS} None of the files found could be checked for comments."
        summary_comments = "No code files could be checked for comments."
    elif not comment_issue:
        report_comments = f"{_COMMENTS} All your code files had comments."
        summary_comments = "All your code files had comments."
    else:
        docstring_note = ""
        if n_docstring_only > 0:
            k = n_docstring_only
            docstring_note = (
                f" Note: this percentage counts only `#` comments; {k} of these Python "
                f"file{plural(k)} {plural(k, 'is', 'are')} documented via docstrings "
                '(`"""..."""`), which are not counted as comments here since they are string '
                "literals, not comment syntax."
            )
        report_comments = f"{_COMMENTS}{docstring_note}"
        n = len(comment_issue)
        summary_comments = f"{n} code file{plural(n)} had no comments."

    ## Missing files ----
    missing_rows = which("loaded_files_missing", lambda v: v > 0)
    missingfiles_issue = [names[i] for i in missing_rows]
    report_table_files_missing = None
    if not missingfiles_issue:
        summary_missingfiles = "All files loaded in the code were present in the repository."
        report_missingfiles = summary_missingfiles
    else:
        n_missing = int(sum(v for v in nums("loaded_files_missing") if v is not None))
        summary_missingfiles = (
            f"{n_missing} file{plural(n_missing)} loaded in the code "
            f"{plural(n_missing, 'was', 'were')} missing in the repository."
        )
        k = len(missingfiles_issue)
        report_missingfiles = (
            f"The scripts load files, but {k} script{plural(k)} loaded {n_missing} "
            f"file{plural(n_missing)} that could not be automatically identified in the "
            "repository. Check if the following files are made available, so that others can "
            "reproduce your code, or that the files are missing:"
        )
        report_table_files_missing = _subtable(
            code_files,
            missing_rows,
            {"file_name": "File name", "loaded_files_missing_names": "Missing Files"},
        )

    ## table of code file links ----
    report_table = _files_table(code_files)

    ## Parsable code ----
    parse_flags = col(code_files, "parse_error")
    parse_issues = sum(1 for v in parse_flags if v is True)
    report_table_parse = None
    if parse_issues == 0:
        report_parse = (
            "All R-type code files (.R, .Rmd, .qmd) could be read in. There were no parsing "
            "issues."
        )
        summary_parse = "No parsing issues of R-type files were found."
    else:
        report_parse = (
            "We encountered parsing issues when trying to read in R-type code files. The "
            f"following errors were found in {parse_issues} code file{plural(parse_issues)}:"
        )
        summary_parse = "Parsing issues of R-type files were found."
        report_table_parse = _subtable(
            code_files,
            [i for i, v in enumerate(parse_flags) if v is True],
            {"file_name": "File name", "parse_error_msg": "Error Message"},
        )

    ## Packages / dependencies ----
    all_packages = code_packages(col(code_files, "packages")) if has("packages") else []
    if not all_packages:
        report_packages = _NO_PACKAGES
        summary_packages = "No packages/libraries were detected in the code."
    else:
        k = len(all_packages)
        report_packages = (
            f"The code files load {k} distinct package{plural(k)}/librar"
            f"{'y' if k == 1 else 'ies'}: {', '.join(all_packages)}. These are the names found "
            "in the source (no version information is available from static analysis)."
        )
        summary_packages = f"The code loaded {k} distinct package{plural(k)}."

    ## Reproducible environment (version pinning) ----
    code_text_list = [text for paper_texts in r_text_by_paper.values() for text in paper_texts.values()]
    version_pin = _code_version_pin_check(
        all_files,
        code_text_list=code_text_list,
        max_file_size=max_file_size,
        max_download_size=max_download_size,
        cache=cache,
        skip_on_api_limit=skip_on_api_limit,
        max_files_per_repo=max_files_per_repo,
    )
    all_files = _splice_locations(all_files, version_pin.get("file_location"))
    report_version_pin, summary_version_pin, report_table_version_pin = version_pin_report(
        version_pin
    )

    ## manifest: packages and failed downloads, one file per paper ----
    if manifest is not None:
        merge_manifests(manifest, code_files, paper, all_packages, _bind(failed_parts))

    report = [
        _INTRO,
        scroll_table(report_table, maxrows=5),
        "#### Code Comments",
        report_comments,
        "#### Missing Files",
        report_missingfiles,
        scroll_table(report_table_files_missing, maxrows=5),
        "#### Absolute Paths",
        report_absolute,
        scroll_table(report_table_absolute, maxrows=5),
        "#### Working Directory (setwd)",
        report_setwd,
        scroll_table(report_table_setwd, maxrows=5),
        "#### Package Installation (install.packages)",
        report_install_packages,
        scroll_table(report_table_install_packages, maxrows=5),
        "#### Libraries / Imports",
        report_library,
        "#### Packages / Dependencies",
        report_packages,
        "#### Reproducible Environment",
        report_version_pin,
        scroll_table(report_table_version_pin, maxrows=10),
        "#### Parsable code",
        report_parse,
        scroll_table(report_table_parse),
    ]

    # traffic_light: green only when something was analysed and nothing was found
    if n_analysed == 0:
        tl = "na"
    elif (
        not missingfiles_issue
        and not comment_issue
        and not absolute_issues
        and not setwd_issues
        and not install_packages_issues
        and not library_issue
        and parse_issues == 0
        and version_pin.get("pinned") is True
    ):
        tl = "green"
    else:
        tl = "yellow"

    summary = summary_table(
        code_files,
        all_files,
        r_text_by_paper,
        version_pin,
        skip_on_api_limit=skip_on_api_limit,
        max_files_per_repo=max_files_per_repo,
    )

    summary_text = "".join(
        f"\n-  {s}"
        for s in (
            summary_code,
            summary_comments,
            summary_missingfiles,
            summary_absolute,
            summary_setwd,
            summary_install_packages,
            summary_library,
            summary_packages,
            summary_version_pin,
            summary_parse,
        )
    )

    code_files.attrs = {}
    return {
        "table": code_files,
        "summary_table": summary,
        "na_replace": {"code_n": 0},
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
        "version_pin": version_pin,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def code_langs_of(df: pd.DataFrame, i: int) -> Any:
    v = df["language"].iloc[i] if "language" in df.columns else None
    return None if is_na(v) else v


def _any_name(df: pd.DataFrame, pattern: str) -> bool:
    from pytacheck._r.regex import grepl

    names = col(df, "file_name")
    return any(grepl(pattern, names, ignore_case=True)) if names else False


def _bind(parts: list[pd.DataFrame | None]) -> pd.DataFrame | None:
    """``dplyr::bind_rows()`` of the download attributes (``None`` when there are none)."""
    frames = [p for p in parts if isinstance(p, pd.DataFrame)]
    if not frames:
        return None
    from pytacheck._r.frames import bind_rows

    return bind_rows(frames)


def _need_download(files: pd.DataFrame) -> list[bool]:
    """Rows without a local copy that have a ``file_url`` or an archive member to fetch."""

    def has_value(values: list[Any]) -> list[bool]:
        return [v is not None and str(v) != "" for v in values]

    has_url = has_value(col(files, "file_url"))
    if "archive_url" in files.columns and "archive_member" in files.columns:
        member = [v is not None for v in col(files, "archive_member")]
        has_archive = [
            a and m for a, m in zip(has_value(col(files, "archive_url")), member, strict=True)
        ]
    else:
        has_archive = [False] * len(files)
    if "file_location" not in files.columns:
        return []
    empty = [v is None or str(v) == "" for v in col(files, "file_location")]
    return [
        e and (u or a) for e, u, a in zip(empty, has_url, has_archive, strict=True)
    ]


def _set_file_location(
    files: pd.DataFrame, mask: list[bool], dl: pd.DataFrame | None
) -> pd.DataFrame:
    """``files$file_location[mask] <- dl$file_location``."""
    out = files.copy()
    locs = out["file_location"].astype(object).tolist()
    new = [] if dl is None or "file_location" not in dl.columns else dl["file_location"].tolist()
    if not new:
        # R: a zero-length replacement leaves NA in every masked row
        new = [None]
    k = 0
    for i, m in enumerate(mask):
        if m:
            v = new[k % len(new)]
            locs[i] = None if is_na(v) else v
            k += 1
    dtype = out["file_location"].dtype
    out["file_location"] = pd.Series(
        locs, index=out.index, dtype=dtype if dtype != object else object
    )
    return out


def _splice_locations(all_files: pd.DataFrame, locations: Any) -> pd.DataFrame:
    """Copy the pinning files' resolved locations back into *all_files*.

    R matches each located file by name (its first row of that name) and
    skips empty paths, so later per-paper checks download nothing again.
    """
    if locations is None or len(locations) == 0:
        return all_files
    names = col(all_files, "file_name")
    first: dict[Any, int] = {}
    for i, n in enumerate(names):
        if n is not None and n not in first:
            first[n] = i
    out = all_files.copy()
    if "file_location" not in out.columns:
        out["file_location"] = pd.Series([None] * len(out), dtype=object)
    locs = out["file_location"].astype(object).tolist()
    for name, loc in zip(list(locations.index), locations.tolist(), strict=True):
        m = first.get(name)
        if m is None or (not is_na(loc) and str(loc) == ""):
            continue
        locs[m] = None if is_na(loc) else loc
    dtype = out["file_location"].dtype
    out["file_location"] = pd.Series(locs, index=out.index, dtype=dtype)
    return out


def _subtable(df: pd.DataFrame, rows: list[int], labels: dict[str, str]) -> pd.DataFrame:
    """``df[rows, names(labels)]`` with the columns renamed to *labels*."""
    out = df.iloc[rows][list(labels)].reset_index(drop=True)
    out.columns = list(labels.values())
    return out


def _files_table(code_files: pd.DataFrame) -> pd.DataFrame:
    """The report's table of code files, with links and formatted comment percentages."""
    cols = [c for c in (*_COL_LABELS, "file_url") if c in code_files.columns]
    table = code_files.loc[:, cols].drop_duplicates().reset_index(drop=True)
    if "file_url" not in table.columns:
        # R: link(NULL, file_name) is character(0)
        raise ValueError(f"replacement has 0 rows, data has {len(table)}")
    table["file_name"] = pd.Series(
        link(col(table, "file_url"), col(table, "file_name")), dtype="string"
    )
    table = table.drop(columns="file_url")
    if "percentage_comment" in table.columns:
        table["percentage_comment"] = pd.Series(
            [
                "" if v is None or math.isnan(float(v)) else f"{float(v) * 100:.0f}%"
                for v in col(table, "percentage_comment")
            ],
            dtype="string",
        )
    table.columns = [_COL_LABELS[c] for c in table.columns]
    return table
