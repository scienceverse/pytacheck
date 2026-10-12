"""Helpers of the Reproducibility Check module (``inst/modules/reproducibility_check.R``).

The module's own flow lives in :mod:`metacheck.modules.reproducibility_check`;
this file holds the pieces it is built from:

* :class:`RNamedList` -- an R named list (names may repeat, ``x[[name]]`` is
  the first match), which is what the R module hands the repro helpers;
* the upstream-module inputs (chained outputs, a prior build's saved tables,
  fallback runs) and the paper-ID helper ``.pid()``;
* the report sections that do not depend on the R-code analysis: SPSS/Stata
  data without syntax, self-reproducible output (JASP/jamovi/SPSS
  Viewer/notebooks, Stata ``.smcl``, Mplus ``.out``), non-R code without
  output, and the reported-vs-reproduced match;
* the concurrent multi-paper dispatcher (``.reproducibility_check_batch()``)
  and its Docker resource limits (``.repro_docker_resource_limits()``,
  ``.repro_docker_host_memory_bytes()``).
"""

from __future__ import annotations

import math
import os
import platform
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r import as_character, grepl, plural, slashed, sub
from metacheck._values import as_str
from metacheck.report import scroll_table

# -- small R helpers ---------------------------------------------------------------


def _na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    if isinstance(x, float | np.floating):
        return math.isnan(x)
    return False


def _is_true(x: Any) -> bool:
    """R ``isTRUE()``: a single, non-missing ``TRUE``."""
    if isinstance(x, pd.Series | list | tuple | np.ndarray):
        vals = list(x)
        return len(vals) == 1 and _is_true(vals[0])
    return isinstance(x, bool | np.bool_) and bool(x)


def _as_list(x: Any) -> list[Any]:
    """One list cell (or attribute) as a list: ``NULL``/``NA`` is empty, a string is one value."""
    if x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)):
        return []
    if isinstance(x, str):
        return [x]
    return list(x)


def _has_cols(df: Any, *cols: str) -> bool:
    return isinstance(df, pd.DataFrame) and all(c in df.columns for c in cols)


def _col(df: Any, name: str) -> list[Any]:
    """``df$name`` as a list (empty when *df* is ``NULL`` or lacks the column)."""
    if not _has_cols(df, name):
        return []
    return list(df[name].tolist())


def _chr_col(df: Any, name: str) -> list[str | None]:
    return [as_str(v) for v in _col(df, name)]


def _nzchar(x: str | None) -> bool:
    """R ``nzchar()`` of one element: ``NA`` counts as non-empty."""
    return x is None or x != ""


def _basename(path: str | None) -> str | None:
    """R ``basename()`` on Unix."""
    if path is None:
        return None
    return slashed(path).rstrip("/").rpartition("/")[2]


def _file_exists(path: str | None) -> bool:
    return path is not None and path != "" and os.path.exists(path)


def _lgl_any(pattern: str, values: Sequence[str | None]) -> bool:
    """``any(grepl(pattern, values, ignore.case = TRUE))`` (``NA`` never matches)."""
    if not values:
        return False
    return any(bool(v) for v in grepl(pattern, list(values), ignore_case=True))


def _paste_bullets(lines: Sequence[str | None]) -> str:
    """``paste("\\n- ", x = lines, collapse = "")``."""
    return "".join(f"\n-  {'NA' if x is None else x}" for x in lines)


# -- an R named list -----------------------------------------------------------------


class RNamedList(Mapping[str, Any]):
    """An R named list: names may repeat and ``x[[name]]`` is the first match.

    Iterating yields every name in order (repeats included) and
    :meth:`values`/:meth:`items` give every element, so the repro helpers see
    exactly the list the R module passes them.
    """

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        if len(names) != len(values):
            raise ValueError("names and values differ in length")
        self._names = [str(n) for n in names]
        self._values = list(values)
        self._first: dict[str, int] = {}
        for i, n in enumerate(self._names):
            self._first.setdefault(n, i)

    def __getitem__(self, key: str) -> Any:
        i = self._first.get(key) if isinstance(key, str) else None
        if i is None:
            raise KeyError(key)
        return self._values[i]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._first

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._names))

    def __len__(self) -> int:
        return len(self._names)

    def values(self) -> list[Any]:  # type: ignore[override]
        return list(self._values)

    def items(self) -> list[tuple[str, Any]]:  # type: ignore[override]
        return list(zip(self._names, self._values, strict=True))

    def keys(self) -> list[str]:  # type: ignore[override]
        return list(self._names)

    def at(self, i: int) -> Any:
        """``x[[i]]`` (0-based)."""
        return self._values[i]

    def __repr__(self) -> str:
        return f"RNamedList({self.items()!r})"


# -- inputs ----------------------------------------------------------------------------


def _pid(paper: Any, *dfs: Any) -> str | None:
    """R ``.pid()``: the paper's (first) ID, else the first ``paper_id`` of *dfs*.

    ``paper_id(NULL)`` is an error in metacheck, and so it is here.
    """
    from metacheck.papers.tables import paper_id

    ids: list[Any] = list(paper_id(paper))
    for df in dfs:
        if len(ids) > 0:
            break
        if _has_cols(df, "paper_id"):
            ids = list(dict.fromkeys(as_str(v) for v in df["paper_id"].tolist()))
    return None if len(ids) == 0 else as_str(ids[0])


class SavedTables:
    """``get_saved_output()``: a prior full build's saved module outputs (*tables_dir*).

    The paper's file is read once, lazily, on the first lookup:
    ``<tables_dir>/<paper_id>.json`` as :func:`metacheck.repro.capture_module_tables`
    writes it, else metacheck's ``<paper_id>.rds``. A missing file (or module,
    or item) gives ``None``.
    """

    def __init__(self, tables_dir: str | os.PathLike[str] | None, pid: Callable[[], str | None]):
        self.tables_dir = tables_dir
        self._pid = pid
        self._saved: dict[str, Any] | None = None
        self._tried = False

    def get(self, mod: str, item: str) -> Any:
        if self.tables_dir is None:
            return None
        if not self._tried:
            self._tried = True
            pid = self._pid()
            if pid is not None:
                from metacheck.repro.tables import _read_saved

                base = os.path.join(os.fspath(self.tables_dir), pid)
                for path in (base + ".json", base + ".rds"):
                    if os.path.exists(path):
                        self._saved = _read_saved(path)
                        break
        if self._saved is None:
            return None
        mo = (self._saved.get("modules") or {}).get(mod)
        if isinstance(mo, Mapping):
            return mo.get(item)
        return None


def run_missing(
    paper: Any,
    mod: str,
    *,
    local_path: Any,
    local_only: bool,
    model: Any,
    params: Mapping[str, Any] | None,
    cache: Any,
    skip_on_api_limit: bool,
    download: Any,
    skip_types: Any,
    peek_zips: bool,
    max_file_size: float,
    max_download_size: float,
) -> Any:
    """Run an upstream module whose output the chain does not carry (R ``run_missing()``).

    ``model``/``params`` go only to ``data_check`` and ``psychds_check``,
    ``cache``/``skip_on_api_limit`` to all three, the download arguments only
    to ``data_check``; LLM use is switched off for the call (this module never
    uses the LLM-refined results) and restored afterwards.
    """
    from metacheck.llm.core import llm_model, llm_use
    from metacheck.module import module_run

    args: dict[str, Any] = {"local_only": local_only}
    if local_path is not None:
        args["local_path"] = local_path
    if mod in ("data_check", "psychds_check"):
        args["model"] = llm_model() if model is None else model
        args["params"] = dict(params or {})
    if mod in ("data_check", "code_check", "psychds_check"):
        args["cache"] = cache
        args["skip_on_api_limit"] = skip_on_api_limit
    if mod == "data_check":
        args.update(
            download=download,
            skip_types=skip_types,
            peek_zips=peek_zips,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
        )
    prev_llm_use = llm_use()
    llm_use(False)
    try:
        return module_run(paper, mod, **args)
    finally:
        llm_use(prev_llm_use)


# -- SPSS / Stata data without syntax --------------------------------------------------

_SPSS_REC_NONE = (
    " Consider depositing the analysis in **jamovi** (`.omv`) or "
    "**JASP** (`.jasp`) instead: these formats store the data and the "
    "analyses together in one file, so the results are reproducible from "
    "the file itself."
)
_SPSS_REC_SELF = " (A jamovi/JASP/SPSS-Viewer file is also present, which is self-reproducible.)"


def spss_stata(
    fnames_all: Sequence[str | None], code_fnames: Sequence[str | None]
) -> dict[str, Any]:
    """The SPSS/Stata data-without-syntax findings and their report sections."""
    both = [*fnames_all, *code_fnames]
    has_sav = _lgl_any(r"\.(sav|zsav|por)$", fnames_all)
    has_sps = _lgl_any(r"\.sps$", both)
    has_selfcontained = _lgl_any(r"\.(omv|jasp|spv)$", fnames_all)
    spss_red = has_sav and not has_sps
    spss_report: list[str] | None = None
    if has_sav:
        rec = _SPSS_REC_SELF if has_selfcontained else _SPSS_REC_NONE
        if not has_sps:
            spss_report = [
                "#### SPSS data without syntax",
                "An SPSS data file (`.sav`/`.por`) is present but **no SPSS syntax file "
                "(`.sps`) was found**, so the analysis that produced the results cannot be "
                "reproduced from the deposit. This is treated as a reproducibility failure "
                "(red)." + rec,
            ]
        else:
            spss_report = [
                "#### SPSS data",
                "SPSS data and syntax (`.sav` + `.sps`) are present. This module does not "
                "execute SPSS syntax itself — the syntax is at least documented, but not "
                "run here. If an SPSS-Viewer file (`.spv`) is also deposited, its results "
                "are extracted directly (no execution needed); consider adding one if not." + rec,
            ]

    has_dta = _lgl_any(r"\.dta$", fnames_all)
    has_do = _lgl_any(r"\.(do|ado)$", both)
    stata_red = has_dta and not has_do
    stata_report: list[str] | None = None
    if has_dta:
        if not has_do:
            stata_report = [
                "#### Stata data without syntax",
                "A Stata data file (`.dta`) is present but **no Stata syntax file (`.do`) "
                "or output log (`.smcl`) was found**, so the analysis that produced the "
                "results cannot be reproduced from the deposit. This is treated as a "
                "reproducibility failure (red). Consider depositing the `.do` file (or, at "
                "minimum, a `.smcl`/`.log` output file, which at least records the exact "
                "commands that were run).",
            ]
        else:
            stata_report = [
                "#### Stata data",
                "Stata data and syntax (`.dta` + `.do`, or a `.smcl` output log with the "
                "syntax recovered from it) are present. This module does not execute Stata "
                "syntax itself — the syntax is at least documented, but not run here. If a "
                "`.smcl`/`.log` output file is also deposited, its results are extracted "
                "directly (no Stata needed); consider adding one if not.",
            ]
    return {
        "has_sav": has_sav,
        "has_dta": has_dta,
        "spss_red": spss_red,
        "stata_red": stata_red,
        "spss_report": spss_report,
        "stata_report": stata_report,
    }


# -- self-reproducible output ----------------------------------------------------------


def _rows_matching(structure_df: Any, pattern: str) -> list[tuple[str | None, str | None]]:
    """``(file_name, file_location)`` of the structure rows whose name matches *pattern*."""
    if not _has_cols(structure_df, "file_name", "file_location"):
        return []
    names = _chr_col(structure_df, "file_name")
    locs = _chr_col(structure_df, "file_location")
    hits = grepl(pattern, names, ignore_case=True)
    return [(n, loc) for n, loc, h in zip(names, locs, hits, strict=True) if h]


def _extract(
    rows: Sequence[tuple[str | None, str | None]],
    reader: Callable[[str], list[dict[str, Any]]],
    pid: str | None,
    genuine: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Per-file ``list(file, n_tables, json, long)`` of the readable files among *rows*."""
    from metacheck.statout.stat_output import stat_output_json, stat_results_long

    out: list[dict[str, Any]] = []
    for fn, loc in rows:
        if loc is None or loc == "" or not os.path.exists(loc):
            continue
        if genuine is not None and not genuine(loc):
            continue
        try:
            tabs = reader(loc)
        except Exception:
            tabs = []
        if not tabs:
            continue
        src = _basename(fn)
        out.append(
            {
                "file": fn,
                "n_tables": len(tabs),
                "json": stat_output_json(tabs, paper_id=pid, source_file=src),
                "long": stat_results_long(tabs, paper_id=pid, source_file=src),
            }
        )
    return out


def _n_stats(stat_output: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(s["long"]) for s in stat_output)


def self_repro(
    structure_df: Any, pid: Callable[[], str | None]
) -> tuple[list[dict[str, Any]], list[str] | None]:
    """Extract JASP/jamovi/SPSS-Viewer/notebook, Stata ``.smcl`` and Mplus ``.out`` output.

    Returns the ``stat_output`` list and the ``self_repro_report`` section
    (``None`` when nothing was extracted).
    """
    stat_output: list[dict[str, Any]] = []
    report: list[str] | None = None

    rows = _rows_matching(structure_df, r"\.(jasp|omv|spv|ipynb)$")
    if rows:
        from metacheck.statout.stat_tables import read_stat_tables

        found = _extract(rows, read_stat_tables, pid())
        if found:
            stat_output = found
            n_files = len(found)
            n_tables = sum(s["n_tables"] for s in found)
            n_stats = _n_stats(found)
            report = [
                "#### Self-reproducible output (JASP / jamovi / SPSS Viewer / Jupyter notebook)",
                f"{n_files:d} JASP/jamovi/SPSS-Viewer/notebook file{plural(n_files)} "
                f"bundle{plural(n_files, 's', '')} the analyses and "
                "their saved results together, so the reported statistics are "
                f"recoverable from the file{plural(n_files)} itself, with nothing re-run. "
                f"We extracted {n_tables:d} result table{plural(n_tables)} "
                f"({n_stats:d} individual statistic{plural(n_stats)}), typed with the STATO "
                "ontology, and export them as a statistical-output JSON document (in "
                "the logs) and as queryable rows.",
            ]

    rows = _rows_matching(structure_df, r"\.smcl$")
    if rows:
        from metacheck.statout.stata import import_stata_smcl

        found = _extract(rows, import_stata_smcl, pid())
        if found:
            stat_output = [*stat_output, *found]
            n_files = len(found)
            n_tables = sum(s["n_tables"] for s in found)
            n_stats = _n_stats(found)
            report = [
                *(report or []),
                "#### Stata output logs (.smcl)",
                f"{n_files:d} Stata output log{plural(n_files)} (`.smcl`) "
                f"{plural(n_files, 'is', 'are')} present, recording every command that "
                f"was run and what it printed. We extracted {n_tables:d} result "
                f"table{plural(n_tables)} ({n_stats:d} "
                f"individual statistic{plural(n_stats)}), typed with the STATO ontology. The "
                "commands' own syntax was also recovered as a `.do` file for `code_check`, but "
                "actually re-running it would require Stata itself, which this module "
                "does not do (it runs only R).",
            ]

    rows = _rows_matching(structure_df, r"\.out$")
    if rows:
        from metacheck.statout.mplus import _mplus_is_genuine_output, import_mplus_output

        found = _extract(rows, import_mplus_output, pid(), genuine=_mplus_is_genuine_output)
        if found:
            stat_output = [*stat_output, *found]
            n_files = len(found)
            n_tables = sum(s["n_tables"] for s in found)
            n_stats = _n_stats(found)
            report = [
                *(report or []),
                "#### Mplus output (.out)",
                f"{n_files:d} Mplus output file{plural(n_files)} (`.out`) "
                f"{plural(n_files, 'is', 'are')} present. A `.out` file always "
                'carries its own verbatim analysis syntax (under "INPUT '
                'INSTRUCTIONS"), so it is self-documenting. We extracted '
                f"{n_tables:d} result table{plural(n_tables)} ({n_stats:d} individual "
                f"statistic{plural(n_stats)}), typed with the STATO ontology, and export them "
                "as a statistical-output JSON document (in the logs) and as queryable rows.",
            ]
    return stat_output, report


# -- non-R code with no reproducible output --------------------------------------------

_NO_OUTPUT = (
    "Without output, we cannot compare the results from the software against those "
    "reported in the manuscript to check whether all results can be reproduced based on "
    "the shared data, code, and results."
)
_NO_SELF_REPRO = (
    "no self-reproducible output (JASP/jamovi, SPSS-Viewer, a Stata output log, or Mplus "
    "output) was found to fall back on."
)


def nonr_code(
    code_tbl: Any,
    fnames_all: Sequence[str | None],
    has_sav: bool,
    has_dta: bool,
    has_self_repro_now: bool,
) -> tuple[list[str], list[str] | None]:
    """``nonr_lang`` (non-R code files' languages) and the ``nonr_report`` section."""
    from metacheck._r import r_sort_key

    langs = _chr_col(code_tbl, "language")
    nonr_lang = [x for x in langs if x is not None and x not in ("R", "JASP", "jamovi")]
    if not nonr_lang:
        return nonr_lang, None
    counts: dict[str, int] = {}
    for x in nonr_lang:
        counts[x] = counts.get(x, 0) + 1
    report: list[str] = []
    for lang in sorted(counts, key=r_sort_key):  # table() orders its names with sort()
        n = counts[lang]
        s, isare = plural(n), plural(n, "is", "are")
        block: list[str] | None = None
        if lang == "Stata":
            if not has_dta and not _lgl_any(r"\.smcl$", fnames_all):
                block = [
                    "#### Stata code without data or output",
                    f"{n:d} Stata syntax file{s} (`.do`) {isare} present, but no output log "
                    f"(`.smcl`) was found. {_NO_OUTPUT} Consider depositing a `.smcl` output "
                    "file.",
                ]
        elif lang == "SPSS":
            if not has_sav and not _lgl_any(r"\.spv$", fnames_all):
                block = [
                    "#### SPSS syntax without data or output",
                    f"{n:d} SPSS syntax file{s} (`.sps`) {isare} present, but no output "
                    f"(`.spv`) was found. {_NO_OUTPUT} Consider depositing an `.spv` file "
                    "instead of (or alongside) the `.sav`/`.sps` pair — it bundles the data "
                    "and analyses together and is fully self-reproducible from the file "
                    "itself.",
                ]
        elif lang == "SAS":
            if not has_self_repro_now:
                block = [
                    "#### SAS code, no reproducible output",
                    f"{n:d} SAS code file{s} {isare} present, but this module has no way to "
                    f"extract statistics from SAS output, and {_NO_SELF_REPRO} Consider "
                    "re-running the analysis in **jamovi** or **JASP** (free; the "
                    "`.omv`/`.jasp` file bundles data and results together) or depositing "
                    "SAS's own output log if one exists.",
                ]
        elif lang == "MATLAB":
            if not has_self_repro_now:
                block = [
                    "#### MATLAB code, no reproducible output",
                    f"{n:d} MATLAB code file{s} {isare} present, but this module has no way "
                    f"to extract statistics from MATLAB output, and {_NO_SELF_REPRO} Readers "
                    "without a MATLAB license cannot currently see what the code produced. "
                    "Consider depositing the results as well, so they are visible without "
                    "MATLAB installed: run `diary('output.txt')` (or `diary on`) before the "
                    "analysis and `diary off` after to save a plain-text transcript of "
                    "everything printed to the Command Window, or use "
                    "`publish('script.m', 'pdf')` (or the Live Editor's Export/Save As) to "
                    "generate a single readable document with the code, its output, and any "
                    "figures together.",
                ]
        elif not has_self_repro_now:
            block = [
                f"#### {lang} code, no reproducible output",
                f"{n:d} {lang} code file{s} {isare} present, but this module has no way to "
                f"extract statistics from {lang} output, and {_NO_SELF_REPRO} Consider "
                "depositing the analysis output (a rendered results/log file), or re-running "
                "the analysis in **jamovi** or **JASP** (free; the `.omv`/`.jasp` file "
                "bundles data and results together).",
            ]
        if block is not None:
            report.extend(block)
    return nonr_lang, (report or None)


def non_r_note(structure_df: Any, nonr_lang: Sequence[str]) -> str:
    """The note on non-R code (and MATLAB ``.mat`` data) files the repository holds."""
    names = _chr_col(structure_df, "file_name")
    code_hits = grepl(
        r"\.(py|jl|m|sas|sps|spss|do|ado|java|cpp|c|sql|inp)$", names, ignore_case=True
    )
    data_hits = grepl(r"\.(mat)$", names, ignore_case=True)
    n_non_r = sum(bool(h) for h in code_hits) if names else 0
    n_non_r_data = sum(bool(h) for h in data_hits) if names else 0
    note = ""
    if n_non_r > 0:
        note = (
            f" The repository does contain {n_non_r:d} non-R code file{plural(n_non_r)}; "
            "this phase only RUNS R code (see the "
            f"section{plural(len(nonr_lang))} above for what we found and could — or "
            "could not — check for those files)."
        )
    if n_non_r_data > 0:
        isare = plural(n_non_r_data, "is", "are")
        note += (
            f" It also contains {n_non_r_data:d} MATLAB data file{plural(n_non_r_data)} "
            f"(`.mat`), which {isare} not code and {isare} not assessed here either."
        )
    return note


# -- reported vs. reproduced -----------------------------------------------------------

_PLAUSIBLE_NOTE = (
    "Some reported values were only found alongside OTHER components after being "
    "separated from their own originally-reported neighbours (e.g. a mean split from its "
    'own confidence interval). The "Plausible" column marks whether that split is '
    "well-supported: TRUE when the pieces still trace back to the same underlying "
    "variable (via the output's own row labels), FALSE when no such link was found — a "
    "FALSE match is not necessarily wrong, but is worth checking against its own Source "
    "file/Analysis columns before treating it as confirmed."
)


def _blank_na(values: Sequence[Any]) -> list[str]:
    return ["" if _na(v) else str(v) for v in values]


def match_output(paper: Any, stat_output: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Match the paper's reported tests against the extracted output.

    The shared body of ``.empty_match()`` and the main path's "Reported vs.
    reproduced" step: ``None`` when there is no output, the match fails or
    finds no reported test; else ``report`` (the section), ``summary`` (the
    match summary) and ``table_raw`` (``match_reported_output()``'s result).
    """
    if not stat_output:
        return None
    from metacheck.statout.match_reported import match_reported_output

    try:
        matched = match_reported_output(paper, list(stat_output), include_tables=True)
    except Exception:
        return None
    if matched is None or len(matched) == 0:
        return None
    ms = matched.attrs.get("summary") or {}
    n_tests = int(ms.get("n_tests", 0))
    lines: list[Any] = [
        f"{n_tests:d} statistical test{plural(n_tests)} reported in the manuscript text "
        f"{plural(n_tests, 'was', 'were')} checked against the extracted analysis output: "
        f"{int(ms.get('n_found', 0)):d} matched ({int(ms.get('n_full', 0)):d} fully, "
        f"{int(ms.get('n_partial', 0)):d} partially) — {_pct(ms.get('pct_found'))}% found."
    ]
    if int(matched["plausible_split"].notna().sum()) > 0:
        lines.append(_PLAUSIBLE_NOTE)
    plaus = [
        "" if _na(v) else ("yes" if bool(v) else "no") for v in matched["plausible_split"].tolist()
    ]
    match_table = pd.DataFrame(
        {
            "Reported": matched["reported"].tolist(),
            "Found": matched["found"].tolist(),
            "Confidence": matched["confidence"].tolist(),
            "Plausible": plaus,
            "Matched values": _blank_na(matched["match_values"].tolist()),
            "Not matched": _blank_na(matched["not_matched"].tolist()),
            "Source file": _blank_na(matched["source_file"].tolist()),
            "Analysis": _blank_na(matched["analysis"].tolist()),
        }
    )
    return {
        "report": ["#### Reported vs. reproduced", *lines, scroll_table(match_table, maxrows=15)],
        "summary": ms,
        "table_raw": matched,
    }


def _pct(x: Any) -> str:
    """``sprintf("%s", pct_found)``."""
    return "NA" if _na(x) else str(as_character(x))


def match_summary_line(ms: Mapping[str, Any]) -> str:
    """``"<found> of <tests> reported test(s) matched (<pct>%)."``"""
    n_tests = int(ms.get("n_tests", 0))
    return (
        f"{int(ms.get('n_found', 0)):d} of {n_tests:d} reported test{plural(n_tests)} "
        f"matched ({_pct(ms.get('pct_found'))}%)."
    )


# -- concurrent multi-paper dispatch ---------------------------------------------------


def _repro_docker_host_memory_bytes() -> float | None:
    """Best-effort total host RAM in bytes (``.repro_docker_host_memory_bytes()``).

    ``None`` (R's ``NA``) when the platform-specific lookup fails.
    """
    sysname = platform.system()

    def number(text: str | None) -> float | None:
        # suppressWarnings(as.numeric(x)): NA when it is not a number
        try:
            return float(str(text).strip())
        except ValueError:
            return None

    def command(args: list[str]) -> list[str]:
        res = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
        return res.stdout.splitlines()

    try:
        if sysname == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as fh:
                first = fh.readline()
            # sub("[^0-9]*([0-9]+).*", "\\1", meminfo)
            kb = number(sub(r"[^0-9]*([0-9]+).*", r"\1", first.rstrip("\n")))
            if kb is not None:
                return kb * 1024
        elif sysname == "Darwin":
            lines = command(["sysctl", "-n", "hw.memsize"])
            b = number(lines[0]) if lines else None
            if b is not None:
                return b
        elif sysname == "Windows":
            lines = command(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
                ]
            )
            b = number(lines[-1]) if lines else None
            if b is not None:
                return b
    except OSError:
        return None
    return None


def _repro_docker_resource_limits(workers: Any) -> dict[str, float]:
    """Per-container ``--cpus``/``--memory`` for *workers* concurrent containers.

    Port of ``.repro_docker_resource_limits()``: the host's CPUs (one decimal)
    and memory (whole GB) split evenly across *workers*, never below 1.
    """
    w = max(1, int(workers))
    ncores = os.cpu_count() or 0
    if ncores < 1:
        ncores = 4  # conservative fallback
    mem_bytes = _repro_docker_host_memory_bytes()
    mem_gb = 8.0 if mem_bytes is None else mem_bytes / (1024**3)
    cpus = max(1.0, math.floor((ncores / w) * 10) / 10)
    memory_gb = max(1.0, float(math.floor(mem_gb / w)))
    return {"cpus": cpus, "memory_gb": memory_gb}


def _batch_worker(paper1: Any, args: Mapping[str, Any], limits: Mapping[str, Any]) -> Any:
    """One paper's check in a worker process (the ``callr::r_bg()`` function)."""
    from metacheck.module import module_run
    from metacheck.utils import options

    options({"metacheck.docker_resource_limits": dict(limits)})
    return module_run(paper1, "reproducibility_check", **dict(args))


def _process_executor(workers: int) -> Any:
    """A pool of *workers* fresh processes (each paper gets its own interpreter state)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def _reproducibility_check_batch(
    paper: Any,
    workers: Any,
    results_dir: str | os.PathLike[str] | None,
    executor: Callable[[int], Any] | None = None,
    **args: Any,
) -> dict[str, Any]:
    """Run a paper list's checks concurrently (``.reproducibility_check_batch()``).

    Each paper runs the unchanged single-paper check in its own worker
    process (up to *workers* at once, the next one starting as soon as one
    finishes), with the Docker resource limits split across the workers.
    With *results_dir*, each paper's result is saved
    (:func:`~metacheck.repro.capture_module_tables`) as soon as it finishes.
    The per-paper tables and summaries are combined into one result.
    *executor* makes the worker pool (default: spawned processes).
    """
    from concurrent.futures import as_completed

    from metacheck._r import bind_rows
    from metacheck.repro.core import _message
    from metacheck.utils import local_options

    pids = [str(p) for p in paper.names]
    n = len(pids)
    workers = max(1, min(int(workers), n))
    limits = _repro_docker_resource_limits(workers)
    results: dict[str, Any] = dict.fromkeys(pids)

    cpus = as_character(limits["cpus"])
    mem = as_character(limits["memory_gb"])
    _message(
        f"[repro] running {n} paper{'s' if n != 1 else ''} with up to {workers} at a time "
        f"({cpus} CPU / {mem}GB per container) ..."
    )
    make = executor or _process_executor
    with local_options({"metacheck.docker_resource_limits": limits}), make(workers) as pool:
        futures = {}
        for i, pid in enumerate(pids):
            _message(f"[repro]   -> starting '{pid}'")
            futures[pool.submit(_batch_worker, paper[i], args, limits)] = pid
        for done, fut in enumerate(as_completed(futures), 1):
            pid = futures[fut]
            try:
                res: Any = fut.result()
            except Exception as exc:
                _message(f"[repro]   x  '{pid}' failed: {exc}")
                err = str(exc)
                res = {
                    "traffic_light": "error",
                    "summary_text": "Docker reproducibility check failed: "
                    + (err if err else "unknown error"),
                    "table": pd.DataFrame(),
                    "summary_table": pd.DataFrame({"paper_id": pd.array([pid], dtype="string")}),
                }
            results[pid] = res
            if results_dir is not None:
                from metacheck.repro.tables import capture_module_tables

                try:
                    capture_module_tables([res], results_dir, paper_id=pid)
                except Exception as exc:
                    _message(f"[repro]   (could not write {pid} results to results_dir: {exc})")
            _message(f"[repro]   <- '{pid}' done ({done}/{n})")

    def elem(res: Any, key: str) -> Any:
        return res.get(key) if res is not None else None

    summaries = []
    tables = []
    for pid in pids:
        st = elem(results[pid], "summary_table")
        if not isinstance(st, pd.DataFrame) or len(st) == 0:
            st = pd.DataFrame({"paper_id": pd.array([pid], dtype="string")})
        summaries.append(st)
        tb = elem(results[pid], "table")
        if not isinstance(tb, pd.DataFrame) or len(tb) == 0:
            continue
        if "paper_id" not in tb.columns:
            tb = tb.copy()
            tb["paper_id"] = pd.array([pid] * len(tb), dtype="string")
        tables.append(tb)
    summary_table = bind_rows(summaries)
    table = bind_rows(tables) if tables else pd.DataFrame()

    tls = [elem(results[pid], "traffic_light") or "na" for pid in pids]
    if all(t == "na" for t in tls):
        overall = "na"
    elif any(t == "red" for t in tls):
        overall = "red"
    elif any(t == "error" for t in tls):
        overall = "error"
    elif any(t in ("yellow", "info") for t in tls):
        overall = "yellow"
    else:
        overall = "green"
    n_ok = sum(t in ("green", "yellow", "info") for t in tls)
    n_red = sum(t == "red" for t in tls)
    n_err = sum(t == "error" for t in tls)
    summary_text = (
        f"Ran the reproducibility check on {n:d} paper{plural(n)} (up to {workers:d} at a "
        f"time): {n_ok:d} ok, {n_red:d} red, {n_err:d} error{plural(n_err)}."
    )
    return {
        "table": table,
        "summary_table": summary_table,
        "traffic_light": overall,
        "summary_text": summary_text,
        "report": summary_text,
        "per_paper": results,
    }
