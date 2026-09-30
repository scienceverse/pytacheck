"""Data Check (port of ``inst/modules/data_check.R``).

The module's local helpers (file tree, report tabsets, careless responding,
Qualtrics metadata, the issue cells and the spreadsheet inspectors) live in
:mod:`metacheck.modules._data_check`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import grepl, plural, r_round
from metacheck.module import module
from metacheck.modules import _data_check as h

_INTRO = (
    "This module classifies repository files and, for tabular data files available "
    "locally, extracts each column's type and summary statistics."
)
_DV_INTRO = (
    "This module runs automated data-quality checks (outliers, miscoded missing values, "
    "empty and constant columns, SPSS filter variables, category casing, problematic "
    "column names, mixed text encodings, spreadsheet formatting) on the extracted data "
    "files."
)

_FILE_PROMPT = " ".join(
    [
        "Classify each file into one type:",
        "documentation, materials, output, unknown.",
        "Return one result per numbered input line, echoing its index and the",
        "best single type as `value`. Use 'unknown' when uncertain.",
    ]
)
_FILE_LEVELS = ("documentation", "materials", "output", "unknown")

_CONCEPT_LEVELS = (
    "reaction_time",
    "accuracy",
    "age",
    "gender",
    "race",
    "likert",
    "condition",
    "id",
    "date",
    "timestamp",
    "measure",
    "other",
)
_CONCEPT_PROMPT = " ".join(
    [
        "Each line describes one column of a psychology dataset. Say what the",
        "column MEASURES (its concept), independent of how it is stored. Use",
        "exactly one of:",
        ", ".join(_CONCEPT_LEVELS),
        ".",
        "'likert' = a rating-scale item; 'measure' = a substantive numeric",
        "measurement with no more specific concept; 'other' when unsure.",
        "Return one {index, value} per numbered line, echoing its index.",
    ]
)

_CHECK_PHRASE = {
    "Out-of-range values": "with values outside the column's apparent range (likely data-entry errors)",
    "Miscoded missing": "with miscoded missing values",
    "Constant": "with a single constant value",
    "Empty column": "with no observed values (entirely empty)",
    "SPSS filter variable": 'holding an SPSS "Select Cases" filter (analyses may have used only a subset of rows)',
    "Case issues": "with inconsistent category casing",
    "Whitespace": "with leading/trailing whitespace",
    "Numeric as text": "with numeric values stored as text",
    "Problematic column name": "whose name contains file-illegal characters or is excessively long",
    "Colliding column names": "whose names become identical when special characters are removed",
    "Mixed encoding": "with values in a legacy (non-UTF-8) encoding",
    "Personal info (values)": "whose values look like personal information",
    "Personal info (column name)": "whose name suggests personal information",
    "Geographic coordinates": "that look like geographic coordinates",
    "Free-text (may hold PII)": "of free text that may contain personal detail",
}

_PII_CHECKS = ("Personal info (values)", "Personal info (column name)", "Free-text (may hold PII)")
_PII_NOTE = (
    "\n\n**About the personal-information flags.** These are pattern "
    "matches, not confirmed disclosures, and they are tuned to over-report "
    "rather than miss something. Expect false positives:\n\n"
    "- **Column names** are matched against identifying words in English "
    "and other European languages (`email`, `telefoonnummer`, "
    "`geboortedatum`, ...). A column called `phone_number` flags whether "
    "or not it holds one, and an empty or already-anonymised column flags "
    "on its name alone.\n"
    "- **Values** are matched against the shape of an identifier. An IP "
    "address pattern also matches a version string or a dotted numeric "
    "code; a card-number pattern requires both a real issuer prefix and a "
    "checksum, but a long numeric ID can still coincide.\n"
    "- **Free text** is flagged when a column holds long, mostly-distinct "
    "prose — the shape open-ended answers take. Most such columns contain "
    "no names at all.\n\n"
    "A flag means *look at this column*. Where the data is already "
    "de-identified (an `ip` column reading `Anonymized`, a `name` column "
    "the authors emptied), no action is needed."
)

_ID_COL_RE = r"(?i)(^id$|\bparticipant|\bsubject\b|\brespond|_id$|\bprolific|\bmturk|\bworker)"

_NA_REPLACE = {
    "data_file_n": 0,
    "column_n": 0,
    "empty_col_n": 0,
    "flagged_n": 0,
    "spreadsheet_file_n": 0,
    "spreadsheet_flagged_file_n": 0,
}


# -----------------------------------------------------------------------------
# small helpers
# -----------------------------------------------------------------------------


def _str_series(values: Sequence[Any], index: Any = None) -> pd.Series:
    return pd.Series(list(values), index=index, dtype="string")


def _lgl(values: Sequence[Any]) -> list[bool]:
    return [bool(v) for v in values]


def _paper_ids(paper: Any) -> list[str]:
    if paper is None:
        return []
    from metacheck.papers.tables import paper_id

    try:
        return list(paper_id(paper))
    except Exception:
        return []


def _pids_all(paper: Any, files: pd.DataFrame | None = None) -> list[Any]:
    """Port of ``.pids_all()``: every paper's id, with the files' ids as fallback."""
    ids: list[Any] = _paper_ids(paper)
    if not ids and files is not None and "paper_id" in files.columns:
        ids = h._unique(h._col(files, "paper_id") or [])
    if not ids:
        ids = [getattr(paper, "paper_id", None)]
    return ids


def _pid(paper: Any, files: pd.DataFrame | None = None) -> Any:
    """Port of ``.pid()``: the first paper id (see :func:`_pids_all`)."""
    return _pids_all(paper, files)[0]


def _set(df: pd.DataFrame, name: str, values: Sequence[Any], dtype: Any = "string") -> None:
    """``df$name <- values`` (in place; keeps the column's position when it exists)."""
    if dtype is object:
        s = pd.Series(list(values), index=df.index, dtype=object)
    else:
        s = pd.Series(list(values), index=df.index, dtype=dtype)
    df[name] = s


def _summarise_by(
    keys: Sequence[Any], values: Sequence[Any], fn: Any
) -> tuple[list[Any], list[Any]]:
    """``summarise(<fn>(values), .by = key)``: groups in first-appearance order."""
    groups: dict[Any, list[Any]] = {}
    for k, v in zip(keys, values, strict=True):
        groups.setdefault(k, []).append(v)
    return list(groups), [fn(v) for v in groups.values()]


def _frame_by(name: str, keys: Sequence[Any], values: Sequence[Any]) -> pd.DataFrame:
    return pd.DataFrame(
        {"paper_id": _str_series(keys), name: pd.Series(list(values), dtype="Int64")}
    )


def _left_join(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    """``dplyr::left_join(x, y, by = "paper_id")`` (``y`` has unique keys here)."""
    y = y.copy()
    y["paper_id"] = y["paper_id"].astype("string")
    out = x.merge(y, on="paper_id", how="left", sort=False)
    return out.reset_index(drop=True)


def _merge_all(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    """Base ``merge(x, y, by = "paper_id", all = TRUE, sort = FALSE)``.

    Matched rows in ``x``'s order, then ``x``-only rows, then ``y``-only rows.
    """
    xk = [None if h._na(v) else v for v in x["paper_id"].tolist()]
    yk = [None if h._na(v) else v for v in y["paper_id"].tolist()]
    ypos: dict[Any, list[int]] = {}
    for j, k in enumerate(yk):
        ypos.setdefault(k, []).append(j)
    pairs: list[tuple[int | None, int | None]] = []
    x_only: list[tuple[int | None, int | None]] = []
    used_y: set[int] = set()
    for i, k in enumerate(xk):
        if k in ypos:
            for j in ypos[k]:
                pairs.append((i, j))
                used_y.add(j)
        else:
            x_only.append((i, None))
    y_only: list[tuple[int | None, int | None]] = [
        (None, j) for j in range(len(yk)) if j not in used_y
    ]
    order = pairs + x_only + y_only
    xrest = [c for c in x.columns if c != "paper_id"]
    yrest = [c for c in y.columns if c != "paper_id"]
    data: dict[str, pd.Series] = {
        "paper_id": _str_series(
            [xk[i] if i is not None else yk[j] for i, j in order]  # type: ignore[index]
        )
    }

    def take(df: pd.DataFrame, col: str, idx: list[int | None]) -> pd.Series:
        s = df[col]
        vals = [s.iloc[i] if i is not None else None for i in idx]
        dtype: Any = s.dtype
        if pd.api.types.is_integer_dtype(dtype) or pd.api.types.is_bool_dtype(dtype):
            dtype = "Int64" if pd.api.types.is_integer_dtype(dtype) else "boolean"
        try:
            return pd.Series(vals, dtype=dtype)
        except (TypeError, ValueError):
            return pd.Series(vals, dtype=object)

    for c in xrest:
        data[c] = take(x, c, [i for i, _ in order])
    for c in yrest:
        data[c] = take(y, c, [j for _, j in order])
    return pd.DataFrame(data)


def _eq_true(x: Any) -> bool:
    """``x == TRUE`` in an ``if ()`` (``TRUE``, ``1``, ``"TRUE"``)."""
    if isinstance(x, str):
        return x == "TRUE"
    try:
        return bool(x == True)  # noqa: E712 - R's `== TRUE` compares values
    except (TypeError, ValueError):
        return False


def _is_numeric_vec(x: Any) -> bool:
    """R ``is.numeric()`` of a data-frame column."""
    from metacheck.datacheck._checks_rvec import rvec

    return rvec(x).is_numeric


def _sample_values(df: pd.DataFrame, j: int) -> str:
    """``paste(head(as.character(col[!is.na(col)]), 5), collapse = " | ")``."""
    v = [s for s in h.col_chr(df, j) if s is not None]
    return "" if not v else " | ".join(v[:5])


# -----------------------------------------------------------------------------
# the module
# -----------------------------------------------------------------------------


@module(
    title="Data Check",
    description="""
        This module classifies the files in a repository into semantic types
        (data, codebook, code, software, output, supplemental, readme, asset,
        other) and, for tabular data files, extracts each column's type and summary
        statistics.
    """,
    details="""
        The Data Check module consumes the file list produced by `repo_check` (or
        any files added via `local_path`). Each file is classified with rule-based
        heuristics layered over metacheck's `file_category()` and `file_types`: file
        names matching README / codebook patterns win first, then an extension
        crosswalk, then format-locked extension overrides. For every file classified
        as tabular `data` that is available locally, the module reads the file head,
        describes each column with `data_col_facets()` and computes summary statistics
        for numeric columns. Following DDI, a column is described by orthogonal facets
        rather than a single type: `representation` (numeric/text/datetime/code — how
        it is stored), `measurement_level` (Stevens: nominal/ordinal/interval/ratio),
        `concept` (what it measures: reaction_time/age/gender/likert/id/…), `role`
        (identifier/measure/condition/timestamp), `unit`, and a `quality` state
        (ok/empty/constant). Concepts are detected by name+value rules (the demographic
        concepts via `data_check_demographic()`); when `llm_use(TRUE)` the model fills
        concepts and measurement levels the rules left blank. Qualtrics
        survey exports are recognised (`data_check_is_qualtrics()`): their extra
        header rows are stripped so columns type correctly, and the reserved
        response-metadata columns (StartDate, Duration, Finished, ...) are tagged.

        The module defaults to **rules-only** when `llm_use(FALSE)`: columns the
        rules cannot resolve (ambiguous 3–20-unique integers, ambiguous character
        columns) fall back to `continuous` / `text` respectively. When
        `llm_use(TRUE)`, ambiguous columns and unresolved file types (`other`) are
        sent to `llm()` for optional refinement, and every file is assigned a study
        group (`ex1`, `pilot2`, `shared`, ...) so that multi-study repositories can
        be recognised (used by `psychds_check`). Without an LLM the study `group` is
        left `NA` (unknown).

        Column extraction requires files to be readable from disk. Files fetched by
        `repo_check` from OSF/GitHub/etc. without a local copy (`file_location` is
        `NA`) are classified but not column-extracted; pass `local_path` to point at
        a downloaded copy of the repository.

        <validation>This module has not been validated. All checks in the data_check module have unknown error rates. Carefully evaluate the output of this module. You can help improve this module by reporting an issue on GitHub.</validation>
    """,
    keywords=["results"],
    requires=["network", "llm"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={
        "paper": "a paper object or paperlist object, or NULL to check local\n"
        "files only (see [test_paper()])",
        "local_path": "optional path to a local directory. When provided, all\n"
        "files in it (recursively) are added to the file list alongside any files\n"
        "found via `repo_check`, and give the module local copies to column-extract.",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "download": "what to download from online repositories (OSF/GitHub/Zenodo)\n"
        'into the shared cache: `"data"` (the default), `"all"`, or `FALSE`/`"none"`.',
        "skip_types": "an optional character vector of `data_type`s never to\n"
        'download even under `download = "all"`.',
        "peek_zips": "if TRUE, look inside each `.zip` (via an HTTP range request)\n"
        "and only fetch zips that contain actual data or a codebook.",
        "max_file_size": "largest single file to download, in MB (default 100).",
        "max_download_size": "largest total download per repository, in MB (default 500).",
        "max_files_per_repo": "largest file COUNT a single repository may have\n"
        "before it is refused outright (default `Inf`, no cap).",
        "cache": "if `TRUE`, keep downloaded files in a persistent on-disk cache.",
        "skip_on_api_limit": "if `TRUE`, a 429 that carries a confirmed\n"
        "rate-limit-exhausted signal skips that file instead of waiting.",
        "manifest": "optional path to write a per-paper file manifest as JSON.",
        "plot_distributions": "if TRUE, draw a distribution plot for each numeric\n"
        "column and embed it in the report.",
        "max_facets": "the most distribution panels to draw in one plot when\n"
        "`plot_distributions = TRUE`.",
        "model": "the LLM model name (see `llm_model_list()`) used only when\n`llm_use(TRUE)`",
        "params": "a named list passed to `llm()` (e.g., `list(seed = 123)`),\n"
        "used only when `llm_use(TRUE)`",
    },
)
def data_check(
    paper: Any,
    local_path: str | os.PathLike[str] | None = None,
    local_only: bool = False,
    download: Any = "data",
    skip_types: Sequence[str] | str | None = None,
    peek_zips: bool = False,
    max_file_size: float = 100,
    max_download_size: float = 500,
    max_files_per_repo: float = math.inf,
    cache: Any = False,
    skip_on_api_limit: bool = False,
    manifest: str | os.PathLike[str] | None = None,
    plot_distributions: bool = False,
    max_facets: int = h.DV_MAX_FACETS,
    model: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Port of inst/modules/data_check.R::data_check().

    Classifies the files listed by ``repo_check`` (read from the chain, or run
    with *local_path* / *local_only* / *cache*), downloads the ones the checks
    read, describes every column of each local tabular data file and runs the
    data-validation checks (column checks, demographics, Qualtrics metadata,
    careless responding, spreadsheet formatting). ``model=None`` means
    ``llm_model()``, R's default.

    Careless responding needs metacheck's suggested ``careless`` package in R;
    pytacheck ports its ``longstring()``/``irv()``, so the screen always runs
    unless the option ``pytacheck.careless`` is ``False`` (then, as metacheck
    without ``careless``, the report says the check was skipped). The
    distribution figure (``plot_distributions = TRUE``; ggplot2 in R) is
    drawn with matplotlib when it is installed.
    """
    from metacheck.llm.core import llm_use
    from metacheck.module import get_prev_outputs
    from metacheck.utils import match_arg

    if download is True:
        download = "data"
    elif download is False:
        download = "none"
    else:
        from metacheck._r import as_character

        arg = (
            download
            if isinstance(download, str)
            else (None if download is None else as_character(download))
        )
        if arg is None:
            raise ValueError("'arg' should be one of “data”, “all”, “none”")
        download = match_arg(arg, ["data", "all", "none"])
    params = dict(params or {})
    if model is None:
        from metacheck.llm.core import llm_model

        model = llm_model()

    # -- 1. the file list from repo_check ---------------------------------------
    all_files = get_prev_outputs("repo_check", "table")
    listing_gated = get_prev_outputs("repo_check", "gated_repos")
    tree_naming_issues = get_prev_outputs("repo_check", "naming_issues")
    if all_files is None:
        from metacheck.module import module_run

        rc_args: dict[str, Any] = {}
        if local_path is not None:
            rc_args["local_path"] = local_path
        mo = module_run(paper, "repo_check", **rc_args, local_only=local_only, cache=cache)
        all_files = mo.table
        if all_files is None:
            all_files = pd.DataFrame(
                {
                    "file_name": _str_series([]),
                    "repo_url": _str_series([]),
                    "file_location": _str_series([]),
                }
            )
        listing_gated = mo.get("gated_repos")
        tree_naming_issues = mo.get("naming_issues")
    all_files = all_files.copy().reset_index(drop=True)

    # -- 2. classify every file --------------------------------------------------
    if len(all_files) == 0:
        manifest_path = None
        if manifest is not None:
            from metacheck.datacheck.files import _data_check_write_manifest

            empty_files = pd.DataFrame(
                {
                    "file_name": _str_series([]),
                    "file_path": _str_series([]),
                    "repo_url": _str_series([]),
                    "file_url": _str_series([]),
                    "file_size": pd.Series([], dtype="float64"),
                    "data_type": _str_series([]),
                    "data_format": _str_series([]),
                    "file_location": _str_series([]),
                }
            )
            manifest_path = _data_check_write_manifest(
                manifest,
                empty_files,
                [],
                None,
                paper_id=_pids_all(paper, all_files),
                download=download,
                max_file_size=max_file_size,
                max_download_size=max_download_size,
                skip_types=skip_types,
            )
        return {
            "table": all_files,
            "structure": all_files,
            "traffic_light": "na",
            "summary_text": "We found no files to analyse.",
            "gated_repos": listing_gated,
            "manifest_path": manifest_path,
            "summary_table": pd.DataFrame(
                {
                    "paper_id": _str_series([_pid(paper, all_files)]),
                    "data_file_n": pd.Series([0.0]),
                    "column_n": pd.Series([0.0]),
                }
            ),
        }

    state = _classify(all_files, paper, model, params, llm_use())
    all_files = state["all_files"]

    # -- 2c. download what the checks read ----------------------------------------
    dl = _download(
        all_files,
        download=download,
        skip_types=skip_types,
        peek_zips=peek_zips,
        max_file_size=max_file_size,
        max_download_size=max_download_size,
        max_files_per_repo=max_files_per_repo,
        cache=cache,
        skip_on_api_limit=skip_on_api_limit,
    )
    all_files = dl["all_files"]
    want = dl["want"]

    # -- 3./4. select local tabular files, extract columns --------------------------
    ex = _extract(all_files, paper, model, params, state)
    all_files = ex["all_files"]
    columns_df = ex["columns_df"]
    file_previews = ex["previews"]
    n_columns = len(columns_df) if columns_df is not None else 0

    # -- 5. reporting -----------------------------------------------------------
    data_type = h._col(all_files, "data_type") or []
    counts = dict.fromkeys(_data_check_types(), 0)
    for t in data_type:
        if t in counts:
            counts[t] += 1
    n_files = len(all_files)
    summary_files = (
        f"We classified {n_files:d} file{plural(n_files)}: "
        + ", ".join(f"{n:d} {t}" for t, n in counts.items() if n > 0)
        + "."
    )
    n_tabular_all = ex["n_tabular_all"]
    n_no_local = ex["n_no_local"]
    # the files columns were actually extracted from (R subtracts manifests and
    # non-rectangular files but not unreadable or workspace-only ones)
    n_extracted = ex["n_extracted"]
    summary_data = (
        f"We found {n_tabular_all:d} tabular data file{plural(n_tabular_all)} and extracted "
        f"{n_columns:d} column{plural(n_columns)} from {n_extracted:d} of them."
    )

    study_grp = h._col(all_files, "group") or [None] * n_files
    doc_role = h._col(all_files, "doc_role") or [None] * n_files
    studies = h._unique([g for g in study_grp if g is not None])
    n_root = sum(1 for g, r in zip(study_grp, doc_role, strict=True) if g is None and r == "readme")
    ref_by = all_files["referenced_by"].tolist() if "referenced_by" in all_files else []
    n_reused = sum(1 for r in ref_by if isinstance(r, list | tuple) and len(r) > 0)
    summary_studies: list[str] = []
    if studies:
        from metacheck._r import r_sorted

        root_txt = (
            f", plus {n_root:d} collection-level file{plural(n_root)} (README/ro-crate metadata)"
            if n_root > 0
            else ""
        )
        reused_txt = (
            f", and {n_reused:d} file{plural(n_reused)} reused across studies "
            "(referenced, not duplicated)"
            if n_reused > 0
            else ""
        )
        summary_studies = [
            f"We grouped the files into {len(studies):d} study group{plural(len(studies))} "
            f"({', '.join(r_sorted(studies))}){root_txt}{reused_txt}."
        ]
    unresolved = state["group_unresolved"]
    summary_ungrouped = (
        [
            f"The study-group step did not get an answer for {len(unresolved):d} "
            f"file{plural(len(unresolved))} (the model request failed); "
            f"{'it was' if len(unresolved) == 1 else 'they were'} grouped by the fallback "
            "rules instead."
        ]
        if unresolved
        else []
    )
    gated_repos = dl["gated_repos"]
    gate_msgs = (
        list(h._col(gated_repos, "message") or []) if isinstance(gated_repos, pd.DataFrame) else []
    )
    summary_omitted = ["\n\n".join(h._dquote_free(m) for m in gate_msgs)] if gate_msgs else []
    summary_nolocal = []
    if n_no_local > 0:
        tail = (
            "This can happen for private repositories or failed downloads; you can also pass "
            "`local_path` to point at a local copy."
            if download != "none"
            else 'Set `download = "data"`, or pass `local_path` to point at a local copy, '
            "to analyse them."
        )
        summary_nolocal = [
            f"{n_no_local:d} tabular data file{plural(n_no_local)} could not be read because "
            f"{'it was' if n_no_local == 1 else 'they were'} not downloaded. {tail}"
        ]
    workspace = h._unique(ex["workspace_files"])
    summary_workspace = []
    if workspace:
        n_ws = len(workspace)
        summary_workspace = [
            f"{n_ws:d} R workspace file{plural(n_ws)} (`.RData`/`.rda`) contain"
            f"{'s' if n_ws == 1 else ''} no reusable tabular data — only fitted models or "
            "saved session objects. Such files need R (and the exact packages used) to open "
            "and are not machine-readable. Share the underlying data as CSV (or a documented "
            "`.sav`/`.dta`) with a codebook so it can be reused without R: "
            f"{', '.join(h._dquote_free(w) for w in workspace)}."
        ]
    non_tab: dict[str, Any] = ex["non_tabular"]
    summary_nontabular = []
    if non_tab:
        n_nt = len(non_tab)
        summary_nontabular = [
            f"{n_nt:d} file{plural(n_nt)} read as a table but {'is' if n_nt == 1 else 'are'} "
            "not a usable rectangular dataset ("
            + "; ".join(f"{k}: {h._dquote_free(v)}" for k, v in non_tab.items())
            + "). No columns were extracted and they were not sent to the LLM. Share the "
            "underlying data as a plain rectangular table (one header row, one column per "
            "variable) with a codebook: " + ", ".join(non_tab) + "."
        ]
    trial = h._unique(ex["trial_level_files"])
    summary_trial = []
    if trial:
        n_tl = len(trial)
        summary_trial = [
            f"{n_tl:d} trial-level data file{plural(n_tl)} (E-Prime / Inquisit / jsPsych / "
            f"Behaverse) {'was' if n_tl == 1 else 'were'} recognised. These are "
            "per-participant records of a behavioural task, so they are not listed as "
            "separate datasets — convert_psychds() merges them per instrument into Behaverse "
            "`paradata/<instrument>.json` (one file per instrument, all participants). "
            "Nothing is deleted."
        ]

    report: list[Any] = [_INTRO]
    tree_block = h.repo_tree_block(all_files, tree_naming_issues)
    if tree_block is not None:
        report.extend(tree_block)
    if file_previews:
        report.append("#### Data Files")
        report.extend(h.data_file_tabset(file_previews) or [])
    if n_columns > 0:
        assert columns_df is not None
        report.extend(_descriptives_report(columns_df))
    if llm_use():
        n_groups = len({g for g in study_grp if g is not None})
        model_used = state["llm_model_used"]
        who = f"LLM model '{model_used}' " if model_used is not None else "LLM "
        report.append(
            f"{who}reviewed ambiguous cases ({state['llm_file_updates']:d} "
            f"file{plural(state['llm_file_updates'])}, {ex['llm_col_updates']:d} "
            f"column{plural(ex['llm_col_updates'])}) and assigned study groups "
            f"({n_groups:d} study group{plural(n_groups)} detected)."
        )

    # -- 6. traffic light + summary table ------------------------------------------
    tl = "na" if n_tabular_all == 0 else "yellow" if n_no_local > 0 else "green"
    summary_table = _summary_table(all_files, columns_df, n_columns, ex, paper)
    parts = [
        summary_files,
        summary_data,
        *summary_studies,
        *summary_ungrouped,
        *summary_nolocal,
        *summary_omitted,
        *summary_workspace,
        *summary_nontabular,
        *summary_trial,
    ]
    summary_text = "".join(f"\n-  {p}" for p in parts)

    # -- 6b. manifest --------------------------------------------------------------
    manifest_path = None
    if manifest is not None:
        from metacheck.datacheck.files import _data_check_write_manifest

        manifest_path = _data_check_write_manifest(
            manifest,
            all_files,
            want,
            gated_repos,
            paper_id=_pid(paper, all_files),
            download=download,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            skip_types=skip_types,
            oversize=dl["oversize_files"],
            failed=dl["failed_files"],
            zip_peek=dl["zip_peek_reason"],
            model=model,
        )

    # == DATA VALIDATION ===========================================================
    dv = _validate(
        previews=file_previews,
        structure_df=all_files,
        columns_df=columns_df,
        plot_distributions=plot_distributions,
        max_facets=max_facets,
    )
    if not file_previews and tl == "green":
        tl = "na"  # tabular files exist but none could be read: nothing was checked
    rank = {"na": 0, "green": 1, "yellow": 2, "red": 3}
    both = [t for t in (tl, dv["traffic_light"]) if t in rank]
    final_tl = max(both, key=lambda t: rank[t]) if both else "na"

    dv_summary = dv["summary_table"]
    dv_summary = dv_summary.loc[:, [c for c in dv_summary.columns if c != "column_n"]]
    return {
        "table": columns_df if columns_df is not None else pd.DataFrame(),
        "findings": dv["findings"],
        "careless": dv["careless"],
        "demographics": dv["demographics"],
        "qualtrics": dv["qualtrics"],
        "structure": all_files,
        "previews": file_previews,
        "gated_repos": listing_gated,
        "manifest_path": manifest_path,
        "group_no_evidence": state["group_no_evidence"],
        "summary_table": _merge_all(summary_table, dv_summary),
        "na_replace": dict(_NA_REPLACE),
        "traffic_light": final_tl,
        "report": report + dv["report"],
        "summary_text": f"{summary_text} {dv['summary_text']}",
    }


def _data_check_types() -> tuple[str, ...]:
    from metacheck.datacheck.files import _DATA_CHECK_TYPES

    return tuple(_DATA_CHECK_TYPES)


# -----------------------------------------------------------------------------
# 2. classification and study groups
# -----------------------------------------------------------------------------


def _classify(
    all_files: pd.DataFrame, paper: Any, model: Any, params: dict[str, Any], use_llm: bool
) -> dict[str, Any]:
    from metacheck.datacheck.files import (
        _data_doc_role,
        _data_group_from_path,
        _llm_classify_batched,
        data_classify_files,
        data_format,
        data_group_llm,
    )

    n = len(all_files)
    names = h._col(all_files, "file_name") or [None] * n
    paths = h._col(all_files, "file_path")
    data_type = list(data_classify_files(names, paths))
    _set(all_files, "data_type", data_type)
    doc_role = list(_data_doc_role(names))
    _set(all_files, "doc_role", doc_role)
    ext = [h._file_ext(nm) for nm in names]
    fmt = data_format(ext)
    _set(
        all_files,
        "data_format",
        [f if t == "data" else None for t, f in zip(data_type, fmt, strict=True)],
    )
    _set(all_files, "group", [None] * n)
    _set(all_files, "referenced_by", [None] * n, dtype=object)

    llm_file_updates = 0
    llm_model_used: str | None = None
    if use_llm:
        amb = [i for i, t in enumerate(data_type) if t == "unknown"]
        if amb:
            texts = []
            for i in amb:
                fname = names[i]
                e = h._file_ext(fname)
                texts.append(
                    f"file_name: {h._dquote_free(fname)}\nextension: "
                    f"{'NA' if e is None else (e if e != '' else 'none')}"
                )
            pred = _llm_classify_batched(
                texts,
                _FILE_PROMPT,
                value_desc="Best single semantic file type",
                valid=_FILE_LEVELS,
                model=model,
                params=params,
                phase="Classifying file types",
            )
            llm_model_used = pred.attrs.get("llm_model")
            vals = [None if h._na(v) else v for v in pred.tolist()]
            ok = [v is not None for v in vals]
            if any(ok):
                for i, v in zip(amb, vals, strict=True):
                    if v is not None:
                        data_type[i] = v
                _set(all_files, "data_type", data_type)
                llm_file_updates = sum(ok)

    # 2b. study groups: the collection-level readme / licence gets none
    if paths is None:
        # R: `all_files$file_path %||% ""` makes every per-row vector zero-length
        is_root: list[bool] | None = None
    else:
        pfg = [nm if (p is None or p == "") else p for p, nm in zip(paths, names, strict=True)]
        from_path = _data_group_from_path(pfg)
        study_rx = grepl(
            "study[-_]?[0-9]|/ex[0-9]|/pilot[0-9]",
            [None if p is None else p.lower() for p in pfg],
        )
        is_root = [
            r is not None and r in ("readme", "license") and fp is None and not bool(s)
            for r, fp, s in zip(doc_role, from_path, study_rx, strict=True)
        ]
    sel = [] if is_root is None else [i for i, r in enumerate(is_root) if not r]
    grp = data_group_llm(
        all_files.iloc[sel].reset_index(drop=True), model=model, params=params, paper=paper
    )
    group_no_evidence = False
    group_unresolved: list[Any] = []
    roster_check = None
    if grp is not None:
        group = [None] * n
        ref_by: list[Any] = [None] * n
        g_vals = [None if h._na(v) else v for v in grp["group"].tolist()]
        r_vals = grp["referenced_by"].tolist()
        for k, i in enumerate(sel):
            group[i] = g_vals[k]
            ref_by[i] = r_vals[k]
        _set(all_files, "group", group)
        _set(all_files, "referenced_by", ref_by, dtype=object)
        # the study-group model is reported like the file-type one (R reads
        # `grp$model`, a column that does not exist, so it never was)
        if llm_model_used is None:
            llm_model_used = grp.attrs.get("model")
        roster_check = grp.attrs.get("roster_check")
        group_unresolved = list(grp.attrs.get("unresolved") or [])
        group_no_evidence = h._is_true(grp.attrs.get("no_evidence"))
    return {
        "all_files": all_files,
        "llm_file_updates": llm_file_updates,
        "llm_model_used": llm_model_used,
        "group_unresolved": group_unresolved,
        "group_no_evidence": group_no_evidence,
        "roster_check": roster_check,
    }


# -----------------------------------------------------------------------------
# 2c. downloads, archive expansion, content reclassification
# -----------------------------------------------------------------------------


def _download(
    all_files: pd.DataFrame,
    download: str,
    skip_types: Any,
    peek_zips: bool,
    max_file_size: float,
    max_download_size: float,
    max_files_per_repo: float,
    cache: Any,
    skip_on_api_limit: bool,
) -> dict[str, Any]:
    from metacheck.archives.zip_peek import (
        _expand_compressed,
        _expand_tar,
        _expand_zip,
        _is_readable_archive,
        _is_single_compress,
        _is_tar_archive,
        _is_zip,
    )

    n = len(all_files)
    names = h._col(all_files, "file_name") or [None] * n
    data_type = h._col(all_files, "data_type") or [None] * n
    doc_role = h._col(all_files, "doc_role") or [None] * n
    data_format = h._col(all_files, "data_format") or [None] * n
    txt_iq = grepl("[.](txt|iqdat)$", names, ignore_case=True)
    if download == "all":
        want = [True] * n
    elif download == "data":
        want = [
            (t == "data" and f == "tabular")
            or (t == "documentation" and r in ("codebook", "readme"))
            or bool(x)
            for t, f, r, x in zip(data_type, data_format, doc_role, txt_iq, strict=True)
        ]
    else:
        want = [False] * n
    if skip_types is None:
        never_fetch: list[str] = ["materials", "unknown"]
    else:
        never_fetch = [skip_types] if isinstance(skip_types, str) else list(skip_types)
    if never_fetch:
        keep_archive = _is_readable_archive(names)
        want = [
            w and ((t not in never_fetch) or bool(k))
            for w, t, k in zip(want, data_type, keep_archive, strict=True)
        ]

    file_url = h._col(all_files, "file_url")
    zip_peek_reason: list[Any] = [None] * n
    if h._is_true(peek_zips) and download != "none" and file_url is not None:
        from metacheck.archives.zip_peek import zip_decision

        is_zip = [
            w and bool(z) and h._has_value(u)
            for w, z, u in zip(
                want, grepl("[.]zip$", names, ignore_case=True), file_url, strict=True
            )
        ]
        for i, z in enumerate(is_zip):
            if not z:
                continue
            d = zip_decision(
                file_url[i], skip_types=skip_types if skip_types is not None else "materials"
            )
            if h._is_false(d.get("worth")):
                want[i] = False
                zip_peek_reason[i] = f"zip skipped: {h._dquote_free(d.get('reason'))}"

    gated_repos = None
    oversize_files = None
    failed_files = None
    if download != "none":
        locs = h._col(all_files, "file_location")
        if file_url is not None and locs is not None:
            archive_url = h._col(all_files, "archive_url")
            archive_member = h._col(all_files, "archive_member")
            has_target = [
                h._has_value(file_url[i])
                or (
                    archive_url is not None
                    and h._has_value(archive_url[i])
                    and archive_member is not None
                    and archive_member[i] is not None
                )
                for i in range(n)
            ]
            need_dl = [
                w and not h._has_value(loc) and t
                for w, loc, t in zip(want, locs, has_target, strict=True)
            ]
            if any(need_dl):
                from metacheck.archives.download import download_repo_files

                repo_counts: dict[str, float] = {}
                for r in h._col(all_files, "repo_url") or []:
                    if r is not None:
                        repo_counts[r] = repo_counts.get(r, 0) + 1
                rows = [i for i, d in enumerate(need_dl) if d]
                res = download_repo_files(
                    all_files.iloc[rows].reset_index(drop=True),
                    max_file_size=max_file_size,
                    max_download_size=max_download_size,
                    max_files_per_repo=max_files_per_repo,
                    repo_file_counts=repo_counts,
                    cache=cache,
                    skip_on_api_limit=skip_on_api_limit,
                )
                new_locs = list(locs)
                got = h._col(res, "file_location") if res is not None else None
                for k, i in enumerate(rows):
                    new_locs[i] = got[k] if got is not None else None
                _set(all_files, "file_location", new_locs)
                if res is not None:
                    gated_repos = res.attrs.get("gated")
                    oversize_files = res.attrs.get("oversize_skipped")
                    failed_files = res.attrs.get("failed")

        # .out that is not a genuine Mplus output -> unknown
        locs = h._col(all_files, "file_location")
        if locs is not None:
            from metacheck.statout.mplus import _mplus_is_genuine_output

            data_type = h._col(all_files, "data_type") or [None] * n
            is_out = grepl(r"\.out$", names, ignore_case=True)
            changed = False
            for i in range(n):
                if (
                    data_type[i] == "output"
                    and is_out[i]
                    and h._has_value(locs[i])
                    and h._file_exists(locs[i])
                    and not _mplus_is_genuine_output(locs[i])
                ):
                    data_type[i] = "unknown"
                    changed = True
            if changed:
                _set(all_files, "data_type", data_type)

            # expand downloaded archives (zip only with peek_zips)
            on_disk = [h._has_value(loc) and h._file_exists(loc) for loc in locs]
            zips = _is_zip(names)
            tars = _is_tar_archive(names)
            gzs = _is_single_compress(names)
            da_zip = [i for i in range(n) if h._is_true(peek_zips) and on_disk[i] and zips[i]]
            da_tar = [i for i in range(n) if on_disk[i] and tars[i]]
            da_gz = [i for i in range(n) if on_disk[i] and gzs[i]]
            da = da_zip + da_tar + da_gz
            if da:
                st = skip_types if skip_types is not None else "materials"
                extracted = []
                for i in da:
                    row1 = all_files.iloc[[i]].reset_index(drop=True)
                    if i in da_zip:
                        rows_x = _expand_zip(locs[i], row1, skip_types=st)
                    elif i in da_tar:
                        rows_x = _expand_tar(locs[i], row1, skip_types=st)
                    else:
                        rows_x = _expand_compressed(locs[i], row1, skip_types=st)
                    if rows_x is not None and len(rows_x) > 0:
                        extracted.append(rows_x)
                drop = set(da)
                keep_rows = [i for i in range(n) if i not in drop]
                all_files = all_files.iloc[keep_rows].reset_index(drop=True)
                want = [want[i] for i in keep_rows]
                # keep the zip-peek reasons aligned with the rows (R does not,
                # so the manifest can give a file another file's reason)
                zip_peek_reason = [zip_peek_reason[i] for i in keep_rows]
                if extracted:
                    from metacheck._r import bind_rows

                    added = bind_rows(extracted)
                    all_files = bind_rows([all_files, added]).reset_index(drop=True)
                    want = want + [True] * len(added)
                    zip_peek_reason = zip_peek_reason + [None] * len(added)
                n = len(all_files)
                names = h._col(all_files, "file_name") or [None] * n

            # reclassify downloaded .txt files from their content
            from metacheck.datacheck.files import txt_classify_content

            locs = h._col(all_files, "file_location") or [None] * n
            data_type = h._col(all_files, "data_type") or [None] * n
            doc_role = h._col(all_files, "doc_role") or [None] * n
            data_format = h._col(all_files, "data_format") or [None] * n
            is_txt = grepl("[.]txt$", names, ignore_case=True)
            changed = False
            for i in range(n):
                if not (
                    is_txt[i]
                    and (
                        data_type[i] == "unknown"
                        or (data_type[i] == "documentation" and doc_role[i] == "supplemental")
                    )
                    and h._has_value(locs[i])
                    and h._file_exists(locs[i])
                ):
                    continue
                try:
                    ct = txt_classify_content(locs[i])
                except Exception:
                    ct = None
                if ct == "data":
                    from metacheck.datacheck.files import data_format as fmt_of

                    data_type[i] = "data"
                    data_format[i] = fmt_of(["txt"])[0]
                    changed = True
            if changed:
                _set(all_files, "data_type", data_type)
                _set(all_files, "data_format", data_format)
    return {
        "all_files": all_files,
        "want": want,
        "gated_repos": gated_repos,
        "oversize_files": oversize_files,
        "failed_files": failed_files,
        "zip_peek_reason": zip_peek_reason,
    }


# -----------------------------------------------------------------------------
# 3./4. column extraction
# -----------------------------------------------------------------------------


def _excel_sheets(path: str) -> list[str]:
    """``readxl::excel_sheets(path)`` (``character(0)`` on error)."""
    from metacheck.datacheck import _files_readers as readers

    try:
        book = readers._XlsBook(path) if path.lower().endswith(".xls") else readers._XlsxBook(path)
        return [str(s) for s in book.sheet_names]
    except Exception:
        return []


def _facets(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
    from metacheck.datacheck._colattrs import col_attrs_at
    from metacheck.datacheck.checks import _detect_scale_blocks, data_col_facets

    names = [str(c) for c in df.columns]
    blocks = _detect_scale_blocks(df)
    scale_cols = {names[j] for b in blocks for j in b}
    cls = [
        # by position, as R's data_col_facets(names(df)[j], df[[j]]): a repeated
        # column name must not pick up the other column's class
        data_col_facets(
            names[j],
            df.iloc[:, j],
            in_scale_block=names[j] in scale_cols,
            col_class=col_attrs_at(df, j).get("class", []),
        )
        for j in range(df.shape[1])
    ]
    return cls, sorted(scale_cols)


def _file_columns(
    f: dict[str, Any], df: pd.DataFrame, cls: list[dict[str, Any]], pid_fallback: Any
) -> pd.DataFrame:
    """The per-column rows of one data file (the ``data.frame(...)`` of step 4)."""
    from metacheck.datacheck.checks import (
        _qualtrics_is_display_order,
        _qualtrics_tag_cols,
        data_check_is_qualtrics,
    )
    from metacheck.datacheck.columns import _col_stats, _stats_frame

    names = [str(c) for c in df.columns]
    p = len(names)

    def getf(field: str) -> list[Any]:
        return [None if h._na(c.get(field)) else c.get(field) for c in cls]

    is_numeric = [h._is_true(c.get("is_numeric")) for c in cls]
    concept = getf("concept")
    file_is_qualtrics = bool(data_check_is_qualtrics(df))
    if file_is_qualtrics:
        qtags = _qualtrics_tag_cols(names)
        concept = [q if q is not None else c for c, q in zip(concept, qtags, strict=True)]
        qdo = [bool(v) for v in _qualtrics_is_display_order(names)]
    else:
        qdo = [False] * p

    stats = []
    for j in range(p):
        c = cls[j]
        x_for_stats = c.get("numeric_values")
        if (
            x_for_stats is None
            and h._is_true(c.get("ambiguous"))
            and h._is_true(c.get("is_numeric"))
        ):
            x_for_stats = df.iloc[:, j]
        stats.append(_col_stats(x_for_stats, df.iloc[:, j]))
    stats_mat = _stats_frame(stats)

    rep_counts = df.attrs.get("utf8_repaired") or {}
    utf8_fixed = [int(rep_counts[nm]) if nm in rep_counts else 0 for nm in names]

    data: dict[str, pd.Series] = {}
    pid = f.get("paper_id", "__absent__")
    data["paper_id"] = _str_series([pid_fallback if pid == "__absent__" else pid] * p)
    # a listing without repo_url (local files) gives NA (R fails building the frame)
    data["repo_url"] = _str_series([f.get("repo_url")] * p)
    data["source_file"] = _str_series([f.get("file_name")] * p)
    data["group"] = _str_series([f.get("group")] * p)
    data["column_name"] = _str_series(names)
    for field in ("representation", "measurement_level"):
        data[field] = _str_series(getf(field))
    data["concept"] = _str_series(concept)
    for field in ("role", "unit", "quality", "parse_note"):
        data[field] = _str_series(getf(field))
    data["ambiguous"] = pd.Series([h._is_true(c.get("ambiguous")) for c in cls], dtype="boolean")
    data["is_numeric"] = pd.Series(is_numeric, dtype="boolean")
    data["is_qualtrics"] = pd.Series([file_is_qualtrics] * p, dtype="boolean")
    data["qualtrics_display_order"] = pd.Series(qdo, dtype="boolean")
    data["sample_values"] = _str_series([_sample_values(df, j) for j in range(p)])
    data["utf8_repaired"] = pd.Series(utf8_fixed, dtype="Int64")
    return pd.concat([pd.DataFrame(data), stats_mat], axis=1)


def _extract(
    all_files: pd.DataFrame, paper: Any, model: Any, params: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    from metacheck.datacheck.checks import (
        _bh_is_trial_level_file,
        _tabular_usable,
        data_check_is_qualtrics,
        data_strip_qualtrics_header,
    )
    from metacheck.datacheck.files import data_is_manifest, data_read_head
    from metacheck.llm.core import llm_use

    n = len(all_files)
    names = h._col(all_files, "file_name") or [None] * n
    data_type = h._col(all_files, "data_type") or [None] * n
    data_format = h._col(all_files, "data_format") or [None] * n
    locs = h._col(all_files, "file_location")
    is_tab = [t == "data" and f == "tabular" for t, f in zip(data_type, data_format, strict=True)]
    if locs is None:
        # R: every file_location-based vector is zero-length without the column
        has_local: list[bool] = []
        is_trial: list[bool] = []
        n_tabular_all = 0
        n_no_local = 0
        data_rows: list[int] = []
        trial_level_files: list[Any] = []
    else:
        has_local = [h._has_value(loc) and h._file_exists(loc) for loc in locs]

        def trial(p: Any) -> bool:
            try:
                return h._is_true(_bh_is_trial_level_file(p))
            except Exception:
                return False

        is_trial = [hl and trial(loc) for hl, loc in zip(has_local, locs, strict=True)]
        trial_level_files = [nm for nm, t in zip(names, is_trial, strict=True) if t]
        if any(is_trial):
            data_format = [
                "trial_level" if t else f for f, t in zip(data_format, is_trial, strict=True)
            ]
            _set(all_files, "data_format", data_format)
        data_rows = [i for i in range(n) if is_tab[i] and has_local[i] and not is_trial[i]]
        n_tabular_all = sum(1 for t, tr in zip(is_tab, is_trial, strict=True) if t and not tr)
        n_no_local = sum(1 for t, hl in zip(is_tab, has_local, strict=True) if t and not hl)

    manifest_files: list[Any] = []
    workspace_files: list[Any] = []
    non_tabular: dict[str, Any] = {}
    previews: dict[str, pd.DataFrame] = {}
    columns_df: pd.DataFrame | None = None
    llm_col_updates = 0
    n_extracted = 0

    if data_rows:
        from metacheck._r import bind_rows

        records: list[dict[str, Any]] = all_files.to_dict("records")  # type: ignore[assignment]
        pid_fallback = _pid(paper, all_files)
        per_file = []
        for i in data_rows:
            f = {
                k: (None if not isinstance(v, list) and h._na(v) else v)
                for k, v in records[i].items()
            }
            fname: Any = f.get("file_name")
            loc = str(f["file_location"])
            df = data_read_head(loc, n_rows=math.inf)
            if df is None or df.shape[1] == 0:
                if h._file_ext(fname) in ("rdata", "rda"):
                    workspace_files.append(fname)
                continue
            other_files = [nm for nm in names if nm is None or nm != fname]
            if data_is_manifest(df, other_files):
                manifest_files.append(fname)
                continue
            if data_check_is_qualtrics(df):
                df = data_strip_qualtrics_header(df)
            cls, _ = _facets(df)
            usable = _tabular_usable(cls, df)
            if not h._is_true(usable.get("usable")) and h._file_ext(fname) in ("xlsx", "xls"):
                sheets = _excel_sheets(loc)
                sheets = h._setdiff(sheets, sheets[:1])  # sheet 1 already tried
                for sh in sheets:
                    try:
                        df2 = data_read_head(loc, n_rows=math.inf, sheet=sh)
                    except Exception:
                        df2 = None
                    if df2 is None or df2.shape[1] == 0:
                        continue
                    cls2, _ = _facets(df2)
                    usable2 = _tabular_usable(cls2, df2)
                    if h._is_true(usable2.get("usable")):
                        df, cls, usable = df2, cls2, usable2
                        break
            if not h._is_true(usable.get("usable")):
                non_tabular[fname] = usable.get("reason")
                continue
            previews[fname] = df
            per_file.append(_file_columns(f, df, cls, pid_fallback))
            n_extracted += 1
        columns_df = bind_rows(per_file).reset_index(drop=True) if per_file else pd.DataFrame()

        header_sig: list[str] | None = None
        if len(columns_df) > 0:
            src = [str(s) for s in columns_df["source_file"].tolist()]
            cols = [str(c) for c in columns_df["column_name"].tolist()]
            by_file: dict[str, set[str]] = {}
            for s, c in zip(src, cols, strict=True):
                by_file.setdefault(s, set()).add(c)
            sig = {s: "\r".join(sorted(v)) for s, v in by_file.items()}
            header_sig = [sig[s] for s in src]

        if manifest_files:
            mset = set(manifest_files)
            doc_role = h._col(all_files, "doc_role") or [None] * n
            for k, nm in enumerate(names):
                if nm in mset:
                    data_type[k] = "documentation"
                    doc_role[k] = "supplemental"
                    data_format[k] = None
            _set(all_files, "data_type", data_type)
            _set(all_files, "doc_role", doc_role)
            _set(all_files, "data_format", data_format)
            is_tab = [
                t == "data" and f == "tabular" for t, f in zip(data_type, data_format, strict=True)
            ]
            n_tabular_all = sum(is_tab)
            n_no_local = sum(1 for t, hl in zip(is_tab, has_local, strict=True) if t and not hl)

        if len(columns_df) > 0 and llm_use():
            llm_col_updates, model_used = _llm_concepts(columns_df, header_sig, model, params)
            if state["llm_model_used"] is None:
                state["llm_model_used"] = model_used

        if len(columns_df) > 0:
            rep = [None if h._na(v) else v for v in columns_df["representation"].tolist()]
            isnum = [h._is_true(v) for v in columns_df["is_numeric"].tolist()]
            rep = [
                ("numeric" if nm else "text") if r is None else r
                for r, nm in zip(rep, isnum, strict=True)
            ]
            lvl = [None if h._na(v) else v for v in columns_df["measurement_level"].tolist()]
            lvl = [
                "ratio" if lv is None and r == "numeric" else lv
                for lv, r in zip(lvl, rep, strict=True)
            ]
            columns_df["representation"] = _str_series(rep)
            columns_df["measurement_level"] = _str_series(lvl)
            columns_df = columns_df.drop(columns=["ambiguous", "is_numeric"])

    # tabular_usable / non_tabular_reason
    _set(all_files, "tabular_usable", [True] * n, dtype="boolean")
    reasons = [non_tabular.get(nm) if nm in non_tabular else None for nm in names]
    if non_tabular:
        _set(all_files, "tabular_usable", [nm not in non_tabular for nm in names], dtype="boolean")
    _set(all_files, "non_tabular_reason", reasons)
    return {
        "all_files": all_files,
        "columns_df": columns_df,
        "previews": previews,
        "n_tabular_all": n_tabular_all,
        "n_no_local": n_no_local,
        "n_data_files": len(data_rows),
        "n_extracted": n_extracted,
        "manifest_files": manifest_files,
        "workspace_files": workspace_files,
        "non_tabular": non_tabular,
        "trial_level_files": trial_level_files,
        "is_tab": is_tab,
        "is_trial": is_trial,
        "llm_col_updates": llm_col_updates,
    }


def _llm_concepts(
    columns_df: pd.DataFrame, header_sig: list[str] | None, model: Any, params: dict[str, Any]
) -> tuple[int, Any]:
    """The LLM concept tier (in place): fill concepts the rules left blank."""
    from metacheck.datacheck.files import _llm_classify_batched

    src = [str(s) for s in columns_df["source_file"].tolist()]
    if header_sig is not None:
        first_file: dict[str, str] = {}
        for s, sg in zip(src, header_sig, strict=True):
            first_file.setdefault(sg, s)
        rep_file = [first_file[sg] == s for s, sg in zip(src, header_sig, strict=True)]
    else:
        rep_file = [True] * len(src)
    concept = [None if h._na(v) else v for v in columns_df["concept"].tolist()]
    ambiguous = [h._is_true(v) for v in columns_df["ambiguous"].tolist()]
    gap_idx = [i for i in range(len(src)) if (concept[i] is None or ambiguous[i]) and rep_file[i]]
    if not gap_idx:
        return 0, None
    cname = columns_df["column_name"].tolist()
    samples = columns_df["sample_values"].tolist()
    isnum = columns_df["is_numeric"].tolist()
    texts = [
        f"column_name: {h._dquote_free(None if h._na(cname[i]) else cname[i])}\n"
        f"sample_values: {h._dquote_free(None if h._na(samples[i]) else samples[i])}\n"
        f"is_numeric: {'TRUE' if h._is_true(isnum[i]) else 'FALSE'}"
        for i in gap_idx
    ]
    pred = _llm_classify_batched(
        texts,
        _CONCEPT_PROMPT,
        value_desc="Best single concept",
        valid=_CONCEPT_LEVELS,
        model=model,
        params=params,
        phase="Classifying column concepts",
    )
    model_used = pred.attrs.get("llm_model")
    vals = [None if h._na(v) else v for v in pred.tolist()]
    updates = 0
    for k, i in enumerate(gap_idx):
        v = vals[k]
        if v is not None and v not in ("measure", "other") and concept[i] is None:
            concept[i] = v
            updates += 1
    columns_df["concept"] = _str_series(concept)
    if header_sig is not None and not all(rep_file):
        cols = [str(c) for c in cname]
        key = [f"{sg}\r{c}" for sg, c in zip(header_sig, cols, strict=True)]
        rep_pos: dict[str, int] = {}
        for pos, (kk, r) in enumerate(zip(key, rep_file, strict=True)):
            if r and kk not in rep_pos:
                rep_pos[kk] = pos
        for facet in ("concept", "measurement_level"):
            vals_f = [None if h._na(v) else v for v in columns_df[facet].tolist()]
            new = list(vals_f)
            for i in range(len(src)):
                if rep_file[i] or vals_f[i] is not None:
                    continue
                j = rep_pos.get(key[i])
                if j is not None and vals_f[j] is not None:
                    new[i] = vals_f[j]
            columns_df[facet] = _str_series(new)
    return updates, model_used


# -----------------------------------------------------------------------------
# 5. descriptives and summary table
# -----------------------------------------------------------------------------


def _descriptives_report(columns_df: pd.DataFrame) -> list[Any]:
    def num(col: str) -> list[float | None]:
        return [None if h._na(v) else float(v) for v in columns_df[col].tolist()]

    def txt(col: str, coalesce: bool = False) -> list[Any]:
        vals = [None if h._na(v) else v for v in columns_df[col].tolist()]
        return ["" if (coalesce and v is None) else v for v in vals]

    n = num("n")
    n_missing = num("n_missing")
    rows = [None if a is None or b is None else a + b for a, b in zip(n, n_missing, strict=True)]
    pct = [
        None if r is None or m is None else (r_round(100 * m / r, 1) if r > 0 else None)
        for r, m in zip(rows, n_missing, strict=True)
    ]

    def rnd(col: str) -> list[float | None]:
        return [None if v is None or math.isnan(v) else r_round(v, 3) for v in num(col)]

    desc = pd.DataFrame(
        {
            "source_file": _str_series(txt("source_file")),
            "Column": _str_series(txt("column_name")),
            "Representation": _str_series(txt("representation")),
            "Level": _str_series(txt("measurement_level", True)),
            "Concept": _str_series(txt("concept", True)),
            "Role": _str_series(txt("role", True)),
            "Unit": _str_series(txt("unit", True)),
            "Rows": pd.Series(rows, dtype="float64").astype("Int64"),
            "% Missing": pd.Series(pct, dtype="float64"),
            "N Unique": pd.Series(num("n_unique"), dtype="float64").astype("Int64"),
            "Mean": pd.Series(rnd("mean"), dtype="float64"),
            "SD": pd.Series(rnd("sd"), dtype="float64"),
            "Min": pd.Series(rnd("min"), dtype="float64"),
            "Max": pd.Series(rnd("max"), dtype="float64"),
        }
    )
    cols = desc["Column"].tolist()
    order = sorted(
        range(len(desc)),
        key=lambda i: (
            pct[i] is None,
            -(pct[i] or 0.0),
            h._c_key(None if h._na(cols[i]) else cols[i]),
        ),
    )
    desc = desc.iloc[order].reset_index(drop=True)

    n_missing_cols = sum(1 for v in n_missing if v is not None and v > 0)
    quality = [None if h._na(v) else str(v).lower() for v in columns_df["quality"].tolist()]
    empty_cols = sum(1 for q in quality if q == "empty")
    empty_note = (
        f" {empty_cols:d} column{plural(empty_cols)} {'is' if empty_cols == 1 else 'are'} "
        "empty (no values in any row); store data without empty columns."
        if empty_cols > 0
        else ""
    )
    return [
        "#### Descriptives Overview\n\nWe found "
        f"{n_missing_cols:d} column{plural(n_missing_cols)} with at least one missing value."
        f"{empty_note}",
        *(h.desc_file_tabset(desc) or []),
    ]


def _summary_table(
    all_files: pd.DataFrame,
    columns_df: pd.DataFrame | None,
    n_columns: int,
    ex: dict[str, Any],
    paper: Any,
) -> pd.DataFrame:
    n = len(all_files)
    af_pid = h._col(all_files, "paper_id")
    if n_columns > 0 and columns_df is not None:
        cpid = [None if h._na(v) else v for v in columns_df["paper_id"].tolist()]
        rep = [None if h._na(v) else v for v in columns_df["representation"].tolist()]
        counts: dict[tuple[Any, Any], int] = {}
        for k in zip(cpid, rep, strict=True):
            counts[k] = counts.get(k, 0) + 1
        keys = sorted(counts, key=lambda k: (h._c_key(k[0]), h._c_key(k[1])))
        pids = h._unique([k[0] for k in keys])
        reps = h._unique([k[1] for k in keys])
        wide: dict[str, pd.Series] = {"paper_id": _str_series(pids)}
        for r in reps:
            wide[f"col_{'NA' if r is None else r}"] = pd.Series(
                [counts.get((p, r), 0) for p in pids], dtype="Int64"
            )
        coltype_wide = pd.DataFrame(wide)
    else:
        coltype_wide = pd.DataFrame({"paper_id": _str_series([_pid(paper, all_files)])})

    if af_pid is None:
        raise ValueError("object 'paper_id' not found")
    is_tab = ex["is_tab"]
    is_trial = ex["is_trial"]
    if len(is_trial) != n:
        raise ValueError(f"`.__is_tab` must be size {n} or 1, not {len(is_trial)}.")
    k1, v1 = _summarise_by(
        af_pid, [t and not tr for t, tr in zip(is_tab, is_trial, strict=True)], sum
    )
    data_file_n = _frame_by("data_file_n", k1, v1)
    if n_columns > 0 and columns_df is not None:
        cpid = [None if h._na(v) else v for v in columns_df["paper_id"].tolist()]
        k2, v2 = _summarise_by(cpid, [1] * len(cpid), len)
        column_n = _frame_by("column_n", k2, v2)
        quality = [
            None if h._na(v) else str(v).lower() == "empty" for v in columns_df["quality"].tolist()
        ]
        k3, v3 = _summarise_by(cpid, quality, lambda v: sum(1 for x in v if x))
        empty_n = _frame_by("empty_col_n", k3, v3)
    else:
        column_n = _frame_by("column_n", [], [])
        empty_n = _frame_by("empty_col_n", [], [])
    ids = h._unique(af_pid) if n > 0 else [_pid(paper, all_files)]
    st = pd.DataFrame({"paper_id": _str_series(ids)})
    for y in (data_file_n, column_n, empty_n, coltype_wide):
        st = _left_join(st, y)
    for c in ("data_file_n", "column_n", "empty_col_n"):
        st[c] = st[c].astype("Int64").fillna(0)
    return st


# -----------------------------------------------------------------------------
# DATA VALIDATION
# -----------------------------------------------------------------------------


def _validate(
    previews: dict[str, pd.DataFrame],
    structure_df: pd.DataFrame,
    columns_df: pd.DataFrame | None,
    plot_distributions: bool,
    max_facets: int,
) -> dict[str, Any]:
    from metacheck._r import bind_rows
    from metacheck.module import get_prev_outputs

    labels_df = get_prev_outputs("codebook_check", "table")
    sp = h.dv_spreadsheet_findings(structure_df)
    sp_df: pd.DataFrame = sp["findings"]
    n_sp_files = sp["n_files"]

    if not previews:
        # no readable table: the spreadsheet findings are all there is to report
        text = "We found no readable tabular data files to validate."
        has = len(sp_df) > 0
        sp_files_by_paper, sp_flagged_by_paper = _spreadsheet_counts(structure_df, sp_df)
        ids = h._unique([None if h._na(v) else v for v in sp_files_by_paper["paper_id"].tolist()])
        dv_summary = pd.DataFrame({"paper_id": _str_series(ids)})
        for y in (sp_files_by_paper, sp_flagged_by_paper):
            dv_summary = _left_join(dv_summary, y)
        for c in ("spreadsheet_file_n", "spreadsheet_flagged_file_n"):
            dv_summary[c] = dv_summary[c].astype("Int64").fillna(0)
        dv_summary.insert(1, "flagged_n", pd.Series([0] * len(ids), dtype="Int64"))
        return {
            "findings": bind_rows([_findings_df([]), sp_df]).reset_index(drop=True),
            "careless": _careless({}, columns_df, labels_df)["careless"],
            "demographics": _demographics({}, columns_df),
            "qualtrics": pd.DataFrame(),
            "summary_table": dv_summary,
            "traffic_light": "yellow" if has else "na",
            "summary_text": f"{text} {h.dv_spreadsheet_summary_text(sp_df, n_sp_files)}"
            if has
            else text,
            "report": h.dv_spreadsheet_report(sp_df, n_sp_files) if has else [],
        }

    labels = _label_lookup(labels_df)
    utf8 = _utf8_lookup(columns_df)

    # -- 2. per-column checks ------------------------------------------------------
    findings, plot_specs = _column_findings(previews, labels, utf8)

    # -- 2a. demographics ------------------------------------------------------------
    demo_df = _demographics(previews, columns_df)

    # -- 2a2. Qualtrics metadata -------------------------------------------------------
    from metacheck.datacheck.checks import data_check_is_qualtrics

    qualtrics_specs = []
    for file, df in previews.items():
        if df is None or df.shape[1] == 0 or not data_check_is_qualtrics(df):
            continue
        s = h.dv_qualtrics_summary(df)
        s["source_file"] = file
        qualtrics_specs.append(s)

    # -- 2b. careless responding -------------------------------------------------------
    car = _careless(previews, columns_df, labels_df)
    careless_df: pd.DataFrame = car["careless"]

    findings_df = _findings_df(findings)
    findings_df = bind_rows([findings_df, sp_df]).reset_index(drop=True)
    n_columns = (
        len(columns_df) if columns_df is not None else sum(d.shape[1] for d in previews.values())
    )
    col_mask = [not h._na(v) for v in findings_df["column"].tolist()]
    column_findings = findings_df.loc[col_mask].reset_index(drop=True)
    cf_src = [None if h._na(v) else v for v in column_findings["source_file"].tolist()]
    cf_col = [None if h._na(v) else v for v in column_findings["column"].tolist()]
    cf_chk = [None if h._na(v) else v for v in column_findings["check"].tolist()]
    cf_keys = [
        f"{h._dquote_free(s)} {h._dquote_free(c)}" for s, c in zip(cf_src, cf_col, strict=True)
    ]
    n_flagged = len(set(cf_keys))

    # per-paper counts
    file_to_paper: dict[Any, Any] = {}
    if columns_df is not None and len(columns_df) > 0:
        for src_f, p in zip(
            columns_df["source_file"].tolist(), columns_df["paper_id"].tolist(), strict=True
        ):
            key_f = None if h._na(src_f) else src_f
            if key_f not in file_to_paper:
                file_to_paper[key_f] = None if h._na(p) else p
    if n_columns > 0 and columns_df is not None:
        cpid = [None if h._na(v) else v for v in columns_df["paper_id"].tolist()]
        k, v = _summarise_by(cpid, [1] * len(cpid), len)
        column_n_dv = _frame_by("column_n", k, v)
    else:
        column_n_dv = _frame_by("column_n", [], [])
    if len(column_findings) > 0:
        k, v = _summarise_by([file_to_paper.get(s) for s in cf_src], cf_keys, lambda x: len(set(x)))
        flagged_by_paper = _frame_by("flagged_n", k, v)
    else:
        flagged_by_paper = _frame_by("flagged_n", [], [])
    sp_files_by_paper, sp_flagged_by_paper = _spreadsheet_counts(structure_df, sp_df)

    # per-check tally (distinct columns per check, most first; ties by name, C locale)
    distinct = h._unique(list(zip(cf_src, cf_col, cf_chk, strict=True)))
    tally: dict[Any, int] = {}
    for _, _, c in distinct:
        tally[c] = tally.get(c, 0) + 1
    check_counts = sorted(
        sorted(tally.items(), key=lambda kv: h._c_key(kv[0])), key=lambda kv: -kv[1]
    )

    # -- 3. traffic light ------------------------------------------------------------
    frac = n_flagged / n_columns if n_columns > 0 else 0
    dv_tl = "green" if n_flagged == 0 else "yellow" if frac < 0.25 else "red"
    if len(careless_df) > 0 and dv_tl == "green":
        dv_tl = "yellow"
    if len(sp_df) > 0 and dv_tl == "green":
        dv_tl = "yellow"

    # -- 4. report ------------------------------------------------------------------
    if n_flagged == 0:
        dv_text = (
            f"We ran automated data-quality checks on {n_columns:d} column{plural(n_columns)} "
            "and found no issues."
        )
    else:
        parts = []
        for chk, n in check_counts:
            phrase = _CHECK_PHRASE.get(chk, f"flagged by {h._dquote_free(chk).lower()}")
            parts.append(f"{n:d} column{plural(n)} {phrase}")
        dv_text = (
            f"We ran automated data-quality checks on {n_columns:d} column{plural(n_columns)}; "
            f"{n_flagged:d} column{plural(n_flagged)} {'has' if n_flagged == 1 else 'have'} "
            f"at least one potential issue ({'; '.join(parts)})."
        )
    if len(careless_df) > 0:
        n_car = len(careless_df)
        n_short = sum(1 for v in careless_df["short_scale_only"].tolist() if h._is_true(v))
        dv_text += (
            f" {n_car:d} survey respondent{plural(n_car)} {'was' if n_car == 1 else 'were'} "
            "flagged for possible careless responding"
            + (
                f" ({n_short:d} of them only via short-scale straightlining, which can be "
                "normal answering)"
                if n_short > 0
                else ""
            )
            + "."
        )
    if len(demo_df) > 0:
        from metacheck._r import r_sorted

        kinds = r_sorted(h._unique(h._col(demo_df, "demographic") or []))
        dv_text += (
            f" We detected demographic column{plural(len(kinds))} for "
            f"{', '.join(k[:1].upper() + k[1:] for k in kinds)}."
        )
    if qualtrics_specs:
        n_q = len(qualtrics_specs)
        n_drop = sum(int(s.get("n_drop") or 0) for s in qualtrics_specs)
        dv_text += (
            f" {n_q:d} file{plural(n_q)} {'is' if n_q == 1 else 'are'} a Qualtrics survey export"
            + (
                f" ({n_drop:d} row{plural(n_drop)} look{'s' if n_drop == 1 else ''} like "
                "previews/unfinished responses to review)"
                if n_drop > 0
                else ""
            )
            + "."
        )
    if len(sp_df) > 0:
        dv_text = f"{dv_text} {h.dv_spreadsheet_summary_text(sp_df, n_sp_files)}"

    report: list[Any] = [
        _DV_INTRO,
        f"We examined {n_columns:d} column{plural(n_columns)} across {len(previews):d} data "
        f"file{plural(len(previews))}.",
    ]
    all_issue = bind_rows([column_findings, sp_df]).reset_index(drop=True)
    if len(all_issue) > 0:
        report.extend(_issues_section(all_issue))
    if plot_specs and _eq_true(plot_distributions):
        if h.plotting_available():
            report.extend(
                [
                    "#### Distributions",
                    h.data_validate_dist_facets(plot_specs, max_facets=max_facets),
                ]
            )
        else:
            report.append("*Install the `matplotlib` package to see the distribution histograms.*")
    if qualtrics_specs:
        report.extend(h.dv_qualtrics_report(qualtrics_specs, len(previews)))
    if len(sp_df) > 0:
        report.extend(h.dv_spreadsheet_report(sp_df, n_sp_files))
    report.extend(car["report"])

    # -- 5. summary table --------------------------------------------------------------
    ids = h._unique(
        [None if h._na(v) else v for v in column_n_dv["paper_id"].tolist()]
        + [None if h._na(v) else v for v in sp_files_by_paper["paper_id"].tolist()]
    )
    dv_summary = pd.DataFrame({"paper_id": _str_series(ids)})
    for y in (column_n_dv, flagged_by_paper, sp_files_by_paper, sp_flagged_by_paper):
        dv_summary = _left_join(dv_summary, y)
    for c in ("column_n", "flagged_n", "spreadsheet_file_n", "spreadsheet_flagged_file_n"):
        dv_summary[c] = dv_summary[c].astype("Int64").fillna(0)

    if qualtrics_specs:
        qualtrics_df = pd.DataFrame(
            {
                "source_file": _str_series([s["source_file"] for s in qualtrics_specs]),
                "n_rows": pd.Series([s["n_rows"] for s in qualtrics_specs], dtype="Int64"),
                "n_drop": pd.Series([s["n_drop"] for s in qualtrics_specs], dtype="Int64"),
                "median_seconds": pd.Series(
                    [s["median_seconds"] for s in qualtrics_specs], dtype="float64"
                ),
                "n_fast": pd.Series([s["n_fast"] for s in qualtrics_specs], dtype="Int64"),
                "date_min": _str_series([s["date_min"] for s in qualtrics_specs]),
                "date_max": _str_series([s["date_max"] for s in qualtrics_specs]),
                "pii_fields": _str_series([", ".join(s["pii_fields"]) for s in qualtrics_specs]),
            }
        )
    else:
        qualtrics_df = pd.DataFrame()

    return {
        "findings": findings_df,
        "careless": careless_df,
        "demographics": demo_df,
        "qualtrics": qualtrics_df,
        "summary_table": dv_summary,
        "traffic_light": dv_tl,
        "summary_text": dv_text,
        "report": report,
    }


def _spreadsheet_counts(
    structure_df: pd.DataFrame | None, sp_df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per paper: spreadsheets examined, and spreadsheets with a finding."""
    xl_rows = h._spreadsheet_rows(structure_df)
    if xl_rows:
        assert structure_df is not None
        spid = h._col(structure_df, "paper_id") or [None] * len(structure_df)
        k, v = _summarise_by([spid[i] for i in xl_rows], [1] * len(xl_rows), len)
        sp_files_by_paper = _frame_by("spreadsheet_file_n", k, v)
    else:
        sp_files_by_paper = _frame_by("spreadsheet_file_n", [], [])
    sp_file_to_paper: dict[Any, Any] = {}
    if structure_df is not None and len(structure_df) > 0:
        for nm_f, p in zip(
            h._col(structure_df, "file_name") or [],
            h._col(structure_df, "paper_id") or [],
            strict=False,
        ):
            if nm_f not in sp_file_to_paper:
                sp_file_to_paper[nm_f] = p
    if len(sp_df) > 0:
        sp_src = h._col(sp_df, "source_file") or []
        k, v = _summarise_by(
            [sp_file_to_paper.get(s) for s in sp_src], sp_src, lambda x: len(set(x))
        )
        sp_flagged_by_paper = _frame_by("spreadsheet_flagged_file_n", k, v)
    else:
        sp_flagged_by_paper = _frame_by("spreadsheet_flagged_file_n", [], [])
    return sp_files_by_paper, sp_flagged_by_paper


def _label_lookup(labels_df: Any) -> dict[tuple[Any, Any], Any]:
    """``label_of()``: (source_file, column_name) -> documented label (first hit)."""
    if not isinstance(labels_df, pd.DataFrame) or not all(
        c in labels_df.columns for c in ("source_file", "column_name", "label")
    ):
        return {}
    status = h._col(labels_df, "label_status")
    if status is None:
        return {}
    out: dict[tuple[Any, Any], Any] = {}
    for s, c, lbl, st in zip(
        h._col(labels_df, "source_file") or [],
        h._col(labels_df, "column_name") or [],
        h._col(labels_df, "label") or [],
        status,
        strict=True,
    ):
        if st in ("labelled", "llm") and (s, c) not in out:
            out[(s, c)] = lbl
    return out


def _utf8_lookup(columns_df: pd.DataFrame | None) -> dict[tuple[Any, Any], Any]:
    """``utf8_repaired_of()``: (source_file, column_name) -> repaired-value count."""
    if columns_df is None or not all(
        c in columns_df.columns for c in ("source_file", "column_name", "utf8_repaired")
    ):
        return {}
    out: dict[tuple[Any, Any], Any] = {}
    for s, c, n in zip(
        h._col(columns_df, "source_file") or [],
        h._col(columns_df, "column_name") or [],
        h._col(columns_df, "utf8_repaired") or [],
        strict=True,
    ):
        if (s, c) not in out:
            out[(s, c)] = 0 if n is None else n
    return out


def _column_findings(
    previews: dict[str, pd.DataFrame],
    labels: dict[tuple[Any, Any], Any],
    utf8: dict[tuple[Any, Any], Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from metacheck.datacheck.checks import (
        data_check_case_issues,
        data_check_colname,
        data_check_colname_collisions,
        data_check_empty,
        data_check_numeric_in_text,
        data_check_pii_freetext,
        data_check_pii_geo,
        data_check_pii_name,
        data_check_pii_values,
        data_check_spss_filter,
        data_check_whitespace,
    )

    findings: list[dict[str, Any]] = []
    plot_specs: list[dict[str, Any]] = []
    for file, df in previews.items():
        if df is None or df.shape[1] == 0:
            continue
        names = [str(c) for c in df.columns]
        first = _first_positions(names)
        coll = data_check_colname_collisions(names)
        for col in names:
            x = df.iloc[:, first[col]]  # df[[col]]: the first column of that name
            lbl = labels.get((file, col))
            finds: dict[str, str] = {}
            numeric = _is_numeric_vec(x)
            if numeric:
                import numpy as np

                vals = pd.to_numeric(x, errors="coerce").astype("float64").to_numpy()
                v = vals[~np.isnan(vals)]
                if v.size >= 4 and np.unique(v).size > 1:
                    plot_specs.append(
                        {
                            "file": file,
                            "col": col,
                            "values": v[:5000].tolist(),
                            "lower": None,
                            "upper": None,
                        }
                    )
            else:
                nt = data_check_numeric_in_text(x)
                if nt["problem"]:
                    finds["Numeric as text"] = nt["message"]
                else:
                    cc = data_check_case_issues(x)
                    if cc["problem"]:
                        finds["Case issues"] = cc["message"]
                ws = data_check_whitespace(x)
                if ws["problem"]:
                    finds["Whitespace"] = ws["message"]
            emp = data_check_empty(x)
            if emp["problem"]:
                finds["Empty column"] = emp["message"]
            fl = data_check_spss_filter(col, x)
            if fl["problem"]:
                finds["SPSS filter variable"] = fl["message"]
            cn = data_check_colname(col)
            if cn["problem"]:
                finds["Problematic column name"] = cn["message"]
            if coll.get(col) is not None:
                finds["Colliding column names"] = coll[col]
            n_enc = utf8.get((file, col), 0)
            if n_enc is not None and n_enc > 0:
                n_enc = int(n_enc)
                finds["Mixed encoding"] = (
                    f"{n_enc:d} value{plural(n_enc)} {'is' if n_enc == 1 else 'are'} not valid "
                    "UTF-8 (bytes from a legacy encoding such as Latin-1/Windows-1252, e.g. a "
                    "mis-encoded accent or apostrophe); metacheck re-interpreted "
                    f"{'it' if n_enc == 1 else 'them'} as Latin-1 to continue. Such characters "
                    "corrupt silently when the file is opened on another system. Re-save the "
                    "file with UTF-8 encoding: in Excel use 'Save As > CSV UTF-8 (Comma "
                    'delimited)\'; in R, write.csv(..., fileEncoding = "UTF-8"); in SPSS, '
                    "'Save as type: CSV' with Encoding 'UTF-8'."
                )
            pv = data_check_pii_values(x)
            if pv["problem"]:
                finds["Personal info (values)"] = pv["message"]
            pn = data_check_pii_name(col)
            if pn["problem"]:
                finds["Personal info (column name)"] = pn["message"]
            pg = data_check_pii_geo(col, x, sibling_names=names)
            if pg["problem"]:
                finds["Geographic coordinates"] = pg["message"]
            if not numeric:
                pf = data_check_pii_freetext(x)
                if pf["problem"]:
                    finds["Free-text (may hold PII)"] = pf["message"]
            for chk, detail in finds.items():
                findings.append(
                    {
                        "source_file": file,
                        "column": col,
                        "label": lbl,
                        "check": chk,
                        "detail": detail,
                    }
                )
    return findings, plot_specs


def _first_positions(names: list[str]) -> dict[str, int]:
    """Each name's first column position (R's ``df[[name]]`` with duplicated names)."""
    out: dict[str, int] = {}
    for j, nm in enumerate(names):
        out.setdefault(nm, j)
    return out


def _findings_df(findings: list[dict[str, Any]]) -> pd.DataFrame:
    cols = ("source_file", "column", "label", "check", "detail")
    return pd.DataFrame({c: pd.Series([f.get(c) for f in findings], dtype="string") for c in cols})


def _demographics(
    previews: dict[str, pd.DataFrame], columns_df: pd.DataFrame | None
) -> pd.DataFrame:
    from metacheck.datacheck.checks import data_check_demographic

    tagged = columns_df is not None and all(
        c in columns_df.columns for c in ("source_file", "column_name", "concept")
    )
    concept_of: dict[tuple[Any, Any], Any] = {}
    if tagged:
        assert columns_df is not None
        for s, c, v in zip(
            h._col(columns_df, "source_file") or [],
            h._col(columns_df, "column_name") or [],
            h._col(columns_df, "concept") or [],
            strict=True,
        ):
            if (s, c) not in concept_of:
                concept_of[(s, c)] = v
    rows = []
    for file, df in previews.items():
        if df is None or df.shape[1] == 0:
            continue
        names = [str(c) for c in df.columns]
        first = _first_positions(names)
        for col in names:
            if tagged:
                v = concept_of.get((file, col))
                kind = None if v is None or v == "" else v
            else:
                kind = data_check_demographic(col, df.iloc[:, first[col]])
            if kind is not None and kind not in ("age", "gender", "race"):
                kind = None
            if kind is not None:
                rows.append((file, col, kind))
    return pd.DataFrame(
        {
            "source_file": _str_series([r[0] for r in rows]),
            "column": _str_series([r[1] for r in rows]),
            "demographic": _str_series([r[2] for r in rows]),
        }
    )


def _careless(
    previews: dict[str, pd.DataFrame], columns_df: pd.DataFrame | None, labels_df: Any
) -> dict[str, Any]:
    from metacheck._r import bind_rows, grep
    from metacheck.datacheck.checks import (
        _detect_scale_blocks,
        _scale_block_range,
        _scale_name_prefix,
    )

    avail = h._careless_available()
    specs: list[pd.DataFrame] = []
    n_files = 0
    scored = 0
    skipped = 0
    roles_ok = columns_df is not None and all(
        c in columns_df.columns for c in ("source_file", "column_name", "role")
    )
    lab_ok = isinstance(labels_df, pd.DataFrame) and all(
        c in labels_df.columns for c in ("source_file", "column_name", "scale")
    )
    for file, df in previews.items():
        if df is None or df.shape[1] < h.DV_CARELESS_MIN_ITEMS or len(df) < h.DV_CARELESS_MIN_ROWS:
            skipped += 1
            continue
        blocks = _detect_scale_blocks(df)
        if not blocks:
            skipped += 1
            continue
        names = [str(c) for c in df.columns]
        id_col = None
        if roles_ok:
            assert columns_df is not None
            idc = [
                c
                for s, c, r in zip(
                    h._col(columns_df, "source_file") or [],
                    h._col(columns_df, "column_name") or [],
                    h._col(columns_df, "role") or [],
                    strict=True,
                )
                if s == file and r == "identifier"
            ]
            idc = [c for c in idc if c in names]
            if idc:
                id_col = idc[0]
        if id_col is None:
            hit = grep(_ID_COL_RE, names, perl=True)
            if hit:
                id_col = names[hit[0]]
        if id_col is None:
            skipped += 1
            continue
        n_files += 1
        if not avail:
            skipped += 1
            continue
        scored += 1
        ids = h.col_chr(df, names.index(id_col))  # df[[id_col]]: the first of that name
        for cols in blocks:
            block = df.iloc[:, list(cols)]
            scale = _scale_block_range(block)
            prefix = _scale_name_prefix(names[cols[0]])
            if lab_ok:
                block_names = {names[j] for j in cols}
                for s, c, sc in zip(
                    h._col(labels_df, "source_file") or [],
                    h._col(labels_df, "column_name") or [],
                    h._col(labels_df, "scale") or [],
                    strict=True,
                ):
                    if s == file and c in block_names and sc is not None and sc != "":
                        prefix = sc
                        break
            try:
                res = h.dv_careless_block(block, ids, scale, prefix)
            except Exception:
                res = None
            if res is not None and len(res) > 0:
                res["source_file"] = _str_series([file] * len(res))
                specs.append(res)
    note = None
    if n_files > 0 and not avail:
        note = (
            # R: "%d file%s contain" (UPSTREAM_ISSUES U82)
            f"{n_files:d} file{plural(n_files)} contain{'s' if n_files == 1 else ''} survey data "
            "with an identifier, but careless-response checks were skipped because the "
            "`careless` package is not "
            'installed. Install it with `install.packages("careless")` to screen for '
            "straightlining and other careless responding."
        )
    if specs:
        block_df = bind_rows(specs).reset_index(drop=True)
    else:
        block_df = pd.DataFrame(
            {
                "source_file": _str_series([]),
                "scale": _str_series([]),
                "respondent": _str_series([]),
                "longstring": pd.Series([], dtype="Int64"),
                "irv": pd.Series([], dtype="float64"),
                "reason": _str_series([]),
                "n_items": pd.Series([], dtype="Int64"),
                "straight_cut": pd.Series([], dtype="Int64"),
                "scale_range": _str_series([]),
            }
        )
    careless_df = h.dv_careless_by_respondent(block_df, previews, columns_df)

    report: list[Any] = []
    if len(careless_df) > 0:
        from metacheck.report import scroll_table

        n_car = len(careless_df)
        short = [h._is_true(v) for v in careless_df["short_scale_only"].tolist()]
        n_short = sum(short)
        car_tbl = pd.DataFrame(
            {
                "Respondent": careless_df["respondent"],
                "Blocks flagged": careless_df["n_blocks_flagged"],
                "Scales": careless_df["scales"],
                "Longest run": careless_df["threshold"],
                "Short-scale only": _str_series(["yes" if s else "no" for s in short]),
            }
        )
        report = [
            "#### Careless Responding",
            # R: "%d distinct respondent%s were" and "**%d were flagged" (UPSTREAM_ISSUES U82)
            f"{n_car:d} distinct respondent{plural(n_car)} {'was' if n_car == 1 else 'were'} "
            "flagged for **straightlining**: giving the same answer for at least 80% of the "
            "items in a multi-item scale of "
            f"{h.DV_CARELESS_MIN_ITEMS:d} items or more. One respondent can be flagged in several "
            "scales; the table below is **one row per person**, and the *Longest run* column "
            "gives the run that triggered the strongest flag, so you can check it against the "
            "raw data.",
        ]
        if n_short > 0:
            report.append(
                f"Of these, **{n_short:d} {'was' if n_short == 1 else 'were'} flagged *only* by "
                "short-scale straightlining** (a "
                f"run of identical answers on a scale of {h.DV_SHORT_SCALE_MAX:d} items or "
                "fewer). On a short, one-directional scale, answering consistently is often "
                "normal, coherent responding rather than carelessness — treat these as weak "
                "signals and inspect the actual responses before excluding anyone."
            )
        report += [
            "These are prompts to inspect those rows, not definitive judgements.",
            scroll_table(car_tbl, maxrows=20),
            h.dv_careless_coverage_text(scored, skipped),
        ]
    elif note is not None:
        report = ["#### Careless Responding", note]
    elif scored > 0 or skipped > 0:
        report = [
            "#### Careless Responding",
            f"No respondent was flagged for straightlining in the {scored:d} file"
            f"{plural(scored)} that could be screened."
            if scored > 0
            else "No file could be screened for careless responding.",
            h.dv_careless_coverage_text(scored, skipped),
        ]
    return {"careless": careless_df, "report": report}


def _issues_section(all_issue: pd.DataFrame) -> list[Any]:
    from metacheck.report import scroll_table

    src = [None if h._na(v) else v for v in all_issue["source_file"].tolist()]
    col = [None if h._na(v) else v for v in all_issue["column"].tolist()]
    lbl = [None if h._na(v) else v for v in all_issue["label"].tolist()]
    chk = [None if h._na(v) else v for v in all_issue["check"].tolist()]
    det = [None if h._na(v) else v for v in all_issue["detail"].tolist()]
    display = [l_ if c is None else c for c, l_ in zip(col, lbl, strict=True)]
    order = sorted(range(len(src)), key=lambda i: (h._c_key(src[i]), h._c_key(display[i])))
    groups: dict[tuple[Any, Any], list[int]] = {}
    for i in order:
        groups.setdefault((src[i], display[i]), []).append(i)
    files, cols, issues = [], [], []
    for (s, d), idx in groups.items():
        files.append(s)
        cols.append(d)
        issues.append(h.dv_issue_cell([chk[i] for i in idx], [det[i] for i in idx]))
    tbl = pd.DataFrame(
        {"File": _str_series(files), "Column": _str_series(cols), "Issues": _str_series(issues)}
    )
    n = len(tbl)
    note = _PII_NOTE if any(c in _PII_CHECKS for c in chk) else ""
    return [
        "#### Issues Identified",
        f"{n:d} file/column combination{plural(n)} {'has' if n == 1 else 'have'} at least one "
        f"issue.{note}",
        scroll_table(tbl, maxrows=20, escape=False),
    ]
