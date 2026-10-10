"""Codebook Check (port of ``inst/modules/codebook_check.R``).

The helpers (LLM tiers, scale / task matchers, prefix groups, paradata
filter, OSD export) live in :mod:`metacheck.modules._codebook`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any

from metacheck._values import is_true
from metacheck.module import module

_NA_REPLACE_EMPTY = {
    "column_n": 0,
    "matched_n": 0,
    "unmatched_n": 0,
    "clean_n": 0,
    "conflicted_n": 0,
    "codebook_var_n": 0,
    "unused_var_n": 0,
}

_NA_REPLACE = {
    **_NA_REPLACE_EMPTY,
    "scale_blocks_n": 0,
    "scale_named_n": 0,
    "scale_unnamed_n": 0,
    "task_files_n": 0,
    "task_named_n": 0,
    "task_paper_only_n": 0,
}

# Documentation ROLES carrying codebook-like content worth parsing.
_CODEBOOK_ROLES = ("codebook", "readme")
# Data formats with embedded variable/value labels.
_HAVEN_EXTS = ("sav", "dta", "sas7bdat")
_LABELLED_EXTS = (*_HAVEN_EXTS, "jasp", "omv")

_INTRO = (
    "This module checks whether each extracted data column is documented in a codebook or "
    "README, and flags documented variables that never appear in the data."
)


@module(
    title="Codebook Check",
    description="""
        This module checks whether the data columns in a repository are documented in
        a codebook or README. It locates codebook/readme files, extracts the variable
        definitions they contain, matches those definitions against the data columns
        extracted by `data_check`, and reports documentation coverage: how many data
        columns are documented, and which documented variables are never used.
    """,
    details="""
        The Codebook Check module consumes the columns and file classification
        produced by `data_check` (and, transitively, `repo_check`). Files classified
        as `codebook` or `readme` are parsed with `parse_codebook()`, which reads
        structured tables (CSV/TSV/Excel with a variable-name column and a label
        column), embedded labels in haven files (SPSS/Stata/SAS), and plain text from
        rich-text formats (docx/pdf/rtf/odt). Embedded haven labels from data files
        are also harvested directly.

        Each data column is then matched against the parsed variable definitions with
        `match_column_labels()`, using normalised-name matching with experiment-group
        scoping, haven-label priority, and rule-based label-equivalence merging.

        Beyond the variable label, the module harvests the DDI-style per-variable
        properties a source supplies: the **value labels / code list** (the
        1="Strongly disagree"…5="Strongly agree" mapping — from SPSS/Stata value
        labels and from a codebook "values"/"coding" column), the **missing-value
        scheme** (which codes denote missingness, from haven declared missings and
        labels that read as "refused"/"n/a"), and **question text** and
        columns when present. These are carried onto the matched
        data columns and exported into the Psych-DS `variableMeasured` (as a
        schema.org code list plus namespaced `metacheck:` fields).

        The module defaults to **rules-only** when `llm_use(FALSE)`. When
        `llm_use(TRUE)`, three optional LLM tiers run: parsing unstructured codebook
        text that the rules could not handle, fuzzy-matching still-unlabelled columns
        to still-unmatched codebook variables, and merging conflicting label
        definitions into a single canonical label.
    """,
    keywords=["results"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={
        "paper": "a paper object or paperlist object, or NULL to check local files only "
        "(see [test_paper()])",
        "local_path": "optional path to a local directory, passed through to `data_check` / "
        "`repo_check` when their output is not already available",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "codebook_max_calls": "the maximum number of LLM calls a single tier will make "
        "(default 40): the number of 100-line text blocks per unstructured codebook file, "
        "and the number of distinct survey layouts sent for scale identification. This is "
        "an upfront gate: if a tier would need more calls than this, the whole tier is "
        "skipped (not truncated) with a message naming `codebook_max_calls` and the number "
        "needed.",
        "model": "the LLM model name (`provider/model`, for example `groq/openai/gpt-oss-20b`) used only when `llm_use(TRUE)`",
        "params": "a named list passed to `llm()`, used only when `llm_use(TRUE)`",
    },
)
def codebook_check(
    paper: Any,
    local_path: str | os.PathLike[str] | None = None,
    local_only: bool = False,
    codebook_max_calls: int = 40,
    model: Any = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Port of ``inst/modules/codebook_check.R::codebook_check()``.

    Checks whether the data columns found by ``data_check`` are documented in
    a codebook, README or embedded (SPSS/Stata/SAS/JASP/jamovi) labels, and
    reports coverage, conflicting definitions, unused documented variables,
    values outside a documented range, the scale / task inventory (named from
    the manuscript, the ``scales`` / ``tasks`` dictionaries or an LLM) and
    duplicated column names. *model* defaults to :func:`~metacheck.llm_model`.
    """
    from metacheck.module import get_prev_outputs, module_run

    params = dict(params or {})
    columns_df = get_prev_outputs("data_check", "table")
    structure_df = get_prev_outputs("data_check", "structure")
    previews = get_prev_outputs("data_check", "previews")
    if columns_df is None or structure_df is None:
        kwargs: dict[str, Any] = {"local_only": local_only}
        if local_path is not None:
            kwargs = {"local_path": local_path, "local_only": local_only}
        mo = module_run(paper, "data_check", **kwargs)
        columns_df = mo.table
        structure_df = mo.get("structure")
        previews = mo.get("previews")

    if columns_df is None or len(columns_df) == 0:
        return _empty_summary(
            paper,
            columns_df,
            structure_df,
            "We found no extracted data columns to check against a codebook.",
        )
    return _codebook_check(
        paper, columns_df, structure_df, previews, codebook_max_calls, model, params
    )


def _pid(paper: Any, *dfs: Any) -> Any:
    """``.pid()``: the first paper id, from the paper or else the tables."""
    from metacheck.modules._codebook import _unique, _vals
    from metacheck.papers.tables import paper_id

    try:
        ids: list[Any] = list(paper_id(paper)) if paper is not None else []
    except Exception:
        ids = []
    for df in dfs:
        if ids:
            break
        v = _vals(df, "paper_id")
        if v is not None:
            ids = _unique(v)
    return ids[0] if ids else None


def _empty_summary(paper: Any, columns_df: Any, structure_df: Any, text: str) -> dict[str, Any]:
    """``empty_summary()``: the ``na`` result when there are no data columns."""
    import pandas as pd

    from metacheck.modules._codebook import _unique, _vals

    struct_pids = _vals(structure_df, "paper_id")
    pids = _unique(struct_pids) if struct_pids else []
    if not pids:
        pids = [_pid(paper, columns_df, structure_df)]
    n = len(pids)
    summary = pd.DataFrame({"paper_id": pd.Series(pids, dtype="string")})
    for col in _NA_REPLACE_EMPTY:
        summary[col] = pd.Series([0.0] * n, dtype="float64")
    return {
        "table": columns_df if columns_df is not None else pd.DataFrame(),
        "summary_table": summary,
        "na_replace": dict(_NA_REPLACE_EMPTY),
        "traffic_light": "na",
        "summary_text": text,
        "report": [],
    }


def _file_exists(p: Any) -> bool:
    return p is not None and p != "" and os.path.exists(str(p))


def _file_ext(x: Any) -> str:
    from metacheck.datacheck.files import _file_ext as fe

    return (fe(str(x)) or "") if x is not None else ""


def _fmt_g(x: Any) -> str:
    """``sprintf("%g", x)``."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "NA"
    v = float(x)
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:g}"


def _codebook_check(
    paper: Any,
    columns_df: Any,
    structure_df: Any,
    previews: Any,
    codebook_max_calls: int,
    model: Any,
    params: dict[str, Any],
) -> dict[str, Any]:
    import pandas as pd

    from metacheck._r import bind_rows, r_round, signif
    from metacheck._r.base import as_character, plural
    from metacheck.datacheck.checks import data_check_scale_values
    from metacheck.datacheck.columns import (
        _decode_value_labels,
        _empty_codebook_vars,
        _extract_haven_labels,
        match_column_labels,
        normalize_varname,
        parse_codebook,
    )
    from metacheck.llm.cap_prompt import cap_report
    from metacheck.modules import _codebook as cb
    from metacheck.report import scroll_table
    from metacheck.report.blocks import cap_gate_count

    _ok, _vals, _unique, _key, _pstr = cb._ok, cb._vals, cb._unique, cb._key, cb._pstr
    llm_on = cb._llm_use()
    max_llm_chunks = codebook_max_calls
    gate_msgs: list[str] = []

    # text_scales: instruments named in the manuscript (LLM only)
    text_scales = cb._identify_scales_text_llm(paper, model, params)

    # ── 2. codebook/readme files and labelled data files with a local copy ──
    struct = structure_df if isinstance(structure_df, pd.DataFrame) else pd.DataFrame()
    n_struct = len(struct)
    locs = _vals(struct, "file_location") or [None] * n_struct
    exists = [_file_exists(p) for p in locs]
    if n_struct > 0 and "doc_role" in struct.columns:
        roles = _vals(struct, "doc_role") or []
        cb_idx = [
            i
            for i in range(n_struct)
            if roles[i] in _CODEBOOK_ROLES and locs[i] is not None and locs[i] != "" and exists[i]
        ]
    else:
        cb_idx = []
    if n_struct > 0 and "data_type" in struct.columns and "file_name" in struct.columns:
        dtypes = _vals(struct, "data_type") or []
        fnames = _vals(struct, "file_name") or []
        haven_idx = [
            i
            for i in range(n_struct)
            if dtypes[i] == "data"
            and (_file_ext(fnames[i]).lower() if fnames[i] is not None else "NA") in _LABELLED_EXTS
            and locs[i] is not None
            and exists[i]
        ]
    else:
        haven_idx = []
    s_group = _vals(struct, "group")
    s_pid = _vals(struct, "paper_id")

    # ── 3. parse codebook files (rules, then optional LLM) ───────────────────
    llm_model_used: Any = None
    llm_parse_files = 0
    llm_match_cols = 0
    llm_merge_cols = 0
    parsed_list: list[pd.DataFrame] = []
    for i in cb_idx:
        p = str(locs[i])
        cb_group = s_group[i] if s_group is not None else None
        pv = parse_codebook(p, group=cb_group)
        if isinstance(pv, pd.DataFrame) and len(pv) > 0:
            pv = pv.copy()
            if s_pid is not None:
                pv["paper_id"] = pd.Series([s_pid[i]] * len(pv), index=pv.index, dtype="string")
            parsed_list.append(pv)
        elif isinstance(pv, list) and len(pv) > 0 and llm_on:
            n_chunks = math.ceil(len(pv) / 100)
            gate = cap_gate_count(
                n_chunks,
                "codebook_max_calls",
                max_llm_chunks,
                "text block",
                context=os.path.basename(p),
                action="parse",
            )
            if gate is not None:
                cap_report(gate)
                gate_msgs.append(gate)
            else:
                llm_out = cb.codebook_parse_llm(
                    pv, os.path.basename(p), model, params, max_chunks=max_llm_chunks
                )
                if llm_out is not None and len(llm_out) > 0:
                    llm_model = llm_out.attrs.get("llm_model")
                    llm_out = llm_out.copy()
                    if s_pid is not None:
                        llm_out["paper_id"] = pd.Series(
                            [s_pid[i]] * len(llm_out), index=llm_out.index, dtype="string"
                        )
                    parsed_list.append(llm_out)
                    llm_parse_files += 1
                    if llm_model_used is None:
                        llm_model_used = llm_model

    if haven_idx:
        parsed_list += _harvest_labels(haven_idx, locs, s_group, s_pid, _extract_haven_labels)

    if parsed_list:
        v = bind_rows(parsed_list)
        cv = _vals(v, "codebook_variable") or [None] * len(v)
        lab = _vals(v, "label") or [None] * len(v)
        grp = _vals(v, "group") or [None] * len(v)
        pid = _vals(v, "paper_id") or [None] * len(v)
        norm = normalize_varname(cv) if cv else []
        # exact duplicates within one paper; the same definition in two papers
        # documents both (R's key has no paper_id, so the second paper's column
        # was reported as undocumented)
        keys = [
            f"{_pstr(a)}\x01{_pstr(b)}\x01{'' if g is None else _pstr(g)}\x01{_pstr(p)}"
            for a, b, g, p in zip(norm, lab, grp, pid, strict=True)
        ]
        seen: set[str] = set()
        keep = []
        for k in keys:
            keep.append(k not in seen)
            seen.add(k)
        codebook_vars_df = v.loc[keep].reset_index(drop=True)
    else:
        codebook_vars_df = _empty_codebook_vars()

    # ── 4. match columns against codebook variables ──────────────────────────
    labels_df = match_column_labels(columns_df, codebook_vars_df)
    labels_df = cb._codebook_label_machinery(labels_df, previews)

    if llm_on and len(codebook_vars_df) > 0:
        merged = cb.codebook_match_llm(labels_df, columns_df, codebook_vars_df, model, params)
        labels_df = merged["labels_df"]
        llm_match_cols = merged["n_matched"]
        llm_merge_cols = merged["n_merged"]
        if llm_model_used is None:
            llm_model_used = merged["model"]

    # ── 4b. psychometric scales ──────────────────────────────────────────────
    labels_df = labels_df.copy()
    n_rows = len(labels_df)
    for col in ("scale", "scale_confidence", "scale_source"):
        labels_df[col] = pd.Series([None] * n_rows, index=labels_df.index, dtype="string")
    n_scale_files = 0
    n_scale_selfgen = 0
    n_tasks_found = 0
    n_task_files = 0
    scale_groups: pd.DataFrame | None = None
    have_previews = previews is not None and len(previews) > 0
    lkey = [
        _key(f, c)
        for f, c in zip(
            _vals(labels_df, "source_file") or [None] * n_rows,
            _vals(labels_df, "column_name") or [None] * n_rows,
            strict=True,
        )
    ]
    state = {"labels_df": labels_df}

    def apply_scale(sc: pd.DataFrame | None, source: str = "matched") -> None:
        if sc is None or len(sc) == 0:
            return
        ldf = state["labels_df"]
        sc_index: dict[str, int] = {}
        j: int | None
        for j, (f, c) in enumerate(
            zip(_vals(sc, "source_file") or [], _vals(sc, "column_name") or [], strict=True)
        ):
            sc_index.setdefault(_key(f, c), j)
        cur_scale = _vals(ldf, "scale") or []
        cur_conf = _vals(ldf, "scale_confidence") or []
        cur_src = _vals(ldf, "scale_source") or []
        sc_scale = _vals(sc, "scale") or []
        sc_conf = _vals(sc, "confidence") or [None] * len(sc)
        sc_src = _vals(sc, "scale_source")
        changed = False
        for i, k in enumerate(lkey):
            j = sc_index.get(k)
            if j is None or _ok(cur_scale[i]):
                continue
            cur_scale[i] = sc_scale[j]
            cur_conf[i] = sc_conf[j]
            cur_src[i] = sc_src[j] if sc_src is not None else source
            changed = True
        if changed:
            ldf = ldf.copy()
            ldf["scale"] = pd.Series(cur_scale, index=ldf.index, dtype="string")
            ldf["scale_confidence"] = pd.Series(cur_conf, index=ldf.index, dtype="string")
            ldf["scale_source"] = pd.Series(cur_src, index=ldf.index, dtype="string")
            state["labels_df"] = ldf

    # Stage 1: prefix groups, named from the manuscript by the LLM
    if have_previews:
        scale_groups = cb._identify_scales_prefix_llm(
            previews, state["labels_df"], paper, model, params, max_calls=codebook_max_calls
        )
        if scale_groups is not None and len(scale_groups) > 0:
            n_scale_files = len(_unique(_vals(scale_groups, "source_file") or []))
            per_col = [
                cb._scale_rows(r_file, cols, r_scale, r_conf, "manuscript")
                for r_file, cols, r_scale, r_conf in zip(
                    _vals(scale_groups, "source_file") or [],
                    scale_groups["columns"].tolist(),
                    _vals(scale_groups, "scale") or [],
                    _vals(scale_groups, "confidence") or [],
                    strict=True,
                )
                if _ok(r_scale)
            ]
            if per_col:
                apply_scale(bind_rows(per_col))
            if llm_model_used is None:
                llm_model_used = scale_groups.attrs.get("llm_model")

    # Stage 2: dictionary rules matcher
    if have_previews:
        scr = cb._identify_scales_rules(previews, state["labels_df"], paper)
        if scr.attrs.get("n_detected") is not None:
            n_scale_files = max(n_scale_files, scr.attrs["n_detected"])
        apply_scale(scr, source="matched")

    # Stage 2b: behavioural tasks
    if have_previews:
        tkr = cb._identify_tasks_rules(previews, state["labels_df"], paper)
        if len(tkr) > 0:
            apply_scale(tkr, source="task_matched")
            n_tasks_found = len(
                _unique(
                    f"{_pstr(f)} {_pstr(s)}"
                    for f, s in zip(
                        _vals(tkr, "source_file") or [], _vals(tkr, "scale") or [], strict=True
                    )
                )
            )
        n_task_files = tkr.attrs.get("n_detected") or 0

    # Stage 3: LLM self-generated construct labels
    if llm_on and have_previews:
        sg = cb._identify_scales_selfgen(
            previews,
            state["labels_df"],
            paper,
            model,
            params,
            columns_df=columns_df,
            text_scales=text_scales,
            max_calls=codebook_max_calls,
        )
        if sg is not None and len(sg) > 0:
            apply_scale(sg, source="self_generated")
            n_scale_selfgen = len(
                _unique(
                    f"{_pstr(f)} {_pstr(s)}"
                    for f, s in zip(
                        _vals(sg, "source_file") or [], _vals(sg, "scale") or [], strict=True
                    )
                )
            )
            if llm_model_used is None:
                llm_model_used = sg.attrs.get("llm_model")
    del n_scale_selfgen  # counted as in R, but not reported

    # Stage 4: propagate to same-prefix siblings
    labels_df = cb._propagate_scale_by_prefix(state["labels_df"])
    assert labels_df is not None

    # Backfill scale_groups from the final labels_df
    if scale_groups is not None and len(scale_groups) > 0 and len(labels_df) > 0:
        scale_groups = _backfill_scale_groups(scale_groups, labels_df, cb)

    scales_osd = (
        cb._scales_to_osd(scale_groups, columns_df, labels_df)
        if scale_groups is not None and len(scale_groups) > 0
        else []
    )

    l_scale = _vals(labels_df, "scale") or []
    l_ssrc = _vals(labels_df, "scale_source") or []
    l_file = _vals(labels_df, "source_file") or []
    named = [_ok(s) for s in l_scale]
    n_scales_found = len(_unique(s for s, nm in zip(l_scale, named, strict=True) if nm))
    files_named = _unique(f for f, nm in zip(l_file, named, strict=True) if nm)
    n_scale_unnamed = max(0, n_scale_files - len(files_named))

    tasks_in_paper = cb._scan_paper_for_tasks(paper)
    tasks_in_data = set(
        _unique(
            s
            for s, nm, src in zip(l_scale, named, l_ssrc, strict=True)
            if nm and src == "task_matched"
        )
    )
    tasks_paper_only = _unique(t for t in tasks_in_paper if t not in tasks_in_data)
    n_tasks_paper_only = len(tasks_paper_only)

    # ── 4c. data values the codebook does not allow ──────────────────────────
    sv_rows: list[dict[str, Any]] = []
    if (
        previews is not None
        and len(labels_df) > 0
        and all(c in labels_df.columns for c in ("source_file", "column_name"))
        and "value_labels" in labels_df.columns
    ):
        l_col = _vals(labels_df, "column_name") or []
        l_vl = _vals(labels_df, "value_labels") or []
        l_mv = _vals(labels_df, "missing_values")
        # name -> first position (R's df[[cn]]), per preview, built once
        pos_of: dict[Any, dict[str, int]] = {}
        j: int | None
        for i in range(len(labels_df)):
            f, cn = l_file[i], l_col[i]
            if f is None or cn is None or previews.get(f) is None:
                continue
            df_i = previews[f]
            if f not in pos_of:
                first: dict[str, int] = {}
                for j, nm in enumerate(cb._names(df_i)):
                    first.setdefault(nm, j)
                pos_of[f] = first
            j = pos_of[f].get(cn)
            if j is None:
                continue
            valid = _codes_as_numeric(_decode_value_labels(l_vl[i]))
            if len(valid) < 2:
                continue
            x = cb._num_via_chr(df_i.iloc[:, j])
            if all(v is None or math.isnan(v) for v in x):
                continue
            declared = _codes_as_numeric(
                _decode_value_labels(l_mv[i] if l_mv is not None else None)
            )
            sv = data_check_scale_values(x, declared=declared, valid_values=valid)
            if not is_true(sv.get("problem")):
                continue
            values = list(sv.get("values") or [])
            classes = list(sv.get("classes") or [])
            sv_rows.append(
                {
                    "source_file": f,
                    "column": cn,
                    "documented": f"[{_fmt_g(sv.get('lower'))}, {_fmt_g(sv.get('upper'))}]",
                    "n_values": len(values),
                    "values": ", ".join(
                        _pstr(as_character(signif(float(v), 4))) for v in values[:8]
                    ),
                    "kinds": ", ".join(_pstr(c) for c in _unique(classes)),
                }
            )
    scale_violation_df = pd.DataFrame(
        {
            "source_file": pd.Series([r["source_file"] for r in sv_rows], dtype="string"),
            "column": pd.Series([r["column"] for r in sv_rows], dtype="string"),
            "documented": pd.Series([r["documented"] for r in sv_rows], dtype="string"),
            "n_values": pd.Series([r["n_values"] for r in sv_rows], dtype="Int64"),
            "values": pd.Series([r["values"] for r in sv_rows], dtype="string"),
            "kinds": pd.Series([r["kinds"] for r in sv_rows], dtype="string"),
        }
    )

    # ── 5. coverage tallies ──────────────────────────────────────────────────
    l_status = _vals(labels_df, "label_status") or [None] * len(labels_df)
    clean = [s in ("labelled", "llm") for s in l_status]
    conflicted = [s in ("conflicting_definition", "ambiguous_experiment") for s in l_status]
    matched = [a or b for a, b in zip(clean, conflicted, strict=True)]
    n_columns = len(labels_df)
    n_matched = sum(matched)
    n_unmatched = n_columns - n_matched
    n_conflicted = sum(conflicted)

    l_pid = _vals(labels_df, "paper_id")
    pid_list = l_pid if l_pid is not None else [None] * n_columns
    coverage: dict[Any, list[int]] = {}
    for pid, m, c, is_conflicted in zip(pid_list, matched, clean, conflicted, strict=True):
        acc = coverage.setdefault(pid, [0, 0, 0, 0])
        acc[0] += 1
        acc[1] += int(m)
        acc[2] += int(c)
        acc[3] += int(is_conflicted)

    l_cbvar = _vals(labels_df, "codebook_variable") or [None] * n_columns
    matched_norm_by_paper: dict[Any, set[Any]] = {}
    for pid, m, v in zip(pid_list, matched, l_cbvar, strict=True):
        if not m or v is None or pid is None:
            continue
        parts = cb._r_strsplit_fixed(str(v), " | ")
        matched_norm_by_paper.setdefault(pid, set()).update(
            normalize_varname(parts) if parts else []
        )

    n_codebook_vars = len(codebook_vars_df)
    cv_pid = _vals(codebook_vars_df, "paper_id") or [None] * n_codebook_vars
    cv_var = _vals(codebook_vars_df, "codebook_variable") or [None] * n_codebook_vars
    if n_codebook_vars > 0:
        cv_norm = normalize_varname(cv_var)
        used_var = [
            nv in matched_norm_by_paper.get(pid, set()) if pid is not None else False
            for nv, pid in zip(cv_norm, cv_pid, strict=True)
        ]
    else:
        used_var = []
    n_unused = sum(not u for u in used_var)
    cb_counts: dict[Any, int] = {}
    unused_counts: dict[Any, int] = {}
    for pid, u in zip(cv_pid, used_var, strict=True):
        cb_counts[pid] = cb_counts.get(pid, 0) + 1
        unused_counts[pid] = unused_counts.get(pid, 0) + int(not u)

    pct_matched = r_round(100 * n_matched / n_columns) if n_columns > 0 else 0

    codebook_misaligned = (
        n_codebook_vars >= 5
        and n_columns > 0
        and pct_matched < 20
        and n_unused >= 0.8 * n_codebook_vars
    )
    misalign_msg: str | None = None
    if codebook_misaligned:
        ex_cb = [v for v, u in zip(cv_var, used_var, strict=True) if not u][:3]
        l_colname = _vals(labels_df, "column_name") or []
        ex_col = [c for c, m in zip(l_colname, matched, strict=True) if not m][:3]
        misalign_msg = (
            f"A codebook was found and parsed ({n_codebook_vars:d} variable definition"
            f"{plural(n_codebook_vars)}), but its variable names do not match the data columns, "
            "so the documentation could not be linked automatically (only "
            f"{int(pct_matched):d}% of columns matched). For example, the codebook documents "
            f"{', '.join(f'`{_pstr(x)}`' for x in ex_cb)} while the data has "
            f"{', '.join(f'`{_pstr(x)}`' for x in ex_col)}. This often means the data holds "
            "computed scores or subscales while the codebook lists the underlying items. To make "
            "the codebook usable automatically, document variables under the exact names used in "
            "the data file (one row per data column), with a label and, for rating items, the "
            "response scale."
        )

    # ── 6. traffic light ─────────────────────────────────────────────────────
    if n_codebook_vars == 0:
        tl = "red"
    elif n_unmatched == 0 and n_conflicted == 0:
        tl = "green"
    elif pct_matched >= 80:
        tl = "yellow"
    else:
        tl = "red"
    if len(scale_violation_df) > 0 and tl == "green":
        tl = "yellow"

    # ── 7. report ────────────────────────────────────────────────────────────
    summary_text: Any
    if n_codebook_vars == 0:
        summary_text = (
            f"We found no codebook or README documentation for the {n_columns:d} extracted "
            f"data column{plural(n_columns)}."
        )
    else:
        parts_txt = [
            f"We parsed {n_codebook_vars:d} variable definition{plural(n_codebook_vars)} "
            "from codebook/README files.",
            f"{n_matched:d} of {n_columns:d} data column{plural(n_columns)} ({int(pct_matched):d}%) "
            f"{'is' if n_matched == 1 else 'are'} documented in a codebook; {n_unmatched:d} "
            f"{'is' if n_unmatched == 1 else 'are'} not.",
        ]
        if n_conflicted > 0:
            parts_txt.append(
                f"{n_conflicted:d} matched column{plural(n_conflicted)} "
                f"{'has' if n_conflicted == 1 else 'have'} a conflicting or ambiguous label "
                "that needs resolution."
            )
        if n_unused > 0:
            parts_txt.append(
                f"{n_unused:d} documented variable{plural(n_unused)} never "
                f"appear{'s' if n_unused == 1 else ''} in the data."
            )
        if misalign_msg is not None:
            parts_txt.append(misalign_msg)
        summary_text = "".join(f"\n-  {p}" for p in parts_txt)

    report: list[Any] = [_INTRO]
    n_cb_files = len(cb_idx) + len(haven_idx)
    n_files = len(_unique(l_file))
    report.append(
        f"We examined {n_cb_files:d} codebook/README/label source{plural(n_cb_files)} and "
        f"{n_columns:d} data column{plural(n_columns)} across {n_files:d} file{plural(n_files)}."
    )

    if n_codebook_vars == 0:
        report.append(
            "No codebook or README documentation was found, so no data columns could be "
            "matched to variable definitions."
        )
    else:
        if misalign_msg is not None:
            report.append(f"**{misalign_msg}**")
        doc_state = [
            "yes" if c else ("conflict" if k else "no")
            for c, k in zip(clean, conflicted, strict=True)
        ]
        label_tbl = pd.DataFrame(
            {
                "source_file": labels_df["source_file"].to_numpy(),
                "Column": labels_df["column_name"].to_numpy(),
                "Documented": doc_state,
                "Label": _col_or_na(labels_df, "label"),
                "Codebook Variable": _col_or_na(labels_df, "codebook_variable"),
                "Source": _col_or_na(labels_df, "label_source"),
                "Status": _col_or_na(labels_df, "label_status"),
            }
        )
        col_keys = [_sort_na_last(c) for c in label_tbl["Column"].tolist()]
        order = sorted(
            range(len(label_tbl)), key=lambda i: (_sort_na_last(doc_state[i]), col_keys[i])
        )
        label_tbl = label_tbl.iloc[order].reset_index(drop=True)
        report.append("#### Column Documentation")
        tabset = cb.codebook_file_tabset(label_tbl)
        if tabset is not None:
            report += tabset

        if n_conflicted > 0:
            sel = [i for i, k in enumerate(conflicted) if k]
            sub_df = labels_df.iloc[sel]
            conflict_tbl = pd.DataFrame(
                {
                    "File": sub_df["source_file"].to_numpy(),
                    "Column": sub_df["column_name"].to_numpy(),
                    "Conflicting Labels": _col_or_na(sub_df, "label"),
                    "Sources": _col_or_na(sub_df, "label_source"),
                    "Issue": _col_or_na(sub_df, "label_status"),
                }
            )
            report += [
                f"#### Conflicting or Ambiguous Definitions\n\n{n_conflicted:d} matched "
                f"column{plural(n_conflicted)} {'has' if n_conflicted == 1 else 'have'} more "
                "than one codebook definition, or a definition scoped to a different "
                "experiment. These are matched to a codebook but need manual resolution before "
                "the label can be trusted.",
                scroll_table(conflict_tbl, maxrows=15),
            ]

        if n_unused > 0:
            sel = [i for i, u in enumerate(used_var) if not u]
            sub_cb = codebook_vars_df.iloc[sel]
            unused_tbl = pd.DataFrame(
                {
                    "Variable": _col_or_na(sub_cb, "codebook_variable"),
                    "Label": _col_or_na(sub_cb, "label"),
                    "Source": _col_or_na(sub_cb, "codebook_source"),
                }
            )
            report += [
                f"#### Documented but Unused Variables\n\n{n_unused:d} documented "
                f"variable{plural(n_unused)} {'was' if n_unused == 1 else 'were'} not matched "
                "to any data column.",
                scroll_table(unused_tbl, maxrows=15),
            ]

    if len(scale_violation_df) > 0:
        nv = scale_violation_df["n_values"].tolist()
        order = sorted(range(len(nv)), key=lambda i: -int(nv[i]))
        svd = scale_violation_df.iloc[order].reset_index(drop=True)
        sv_tbl = pd.DataFrame(
            {
                "File": svd["source_file"],
                "Column": svd["column"],
                "Codebook range": svd["documented"],
                "N values": svd["n_values"],
                "Values found": svd["values"],
                "Kind": svd["kinds"],
            }
        )
        n_sv_cols = len(scale_violation_df)
        n_sv_vals = int(sum(int(x) for x in nv))
        report += [
            "#### Values Outside the Documented Range",
            f"{n_sv_cols:d} column{plural(n_sv_cols)} "
            f"{'contains' if n_sv_cols == 1 else 'contain'} {n_sv_vals:d} value"
            f"{plural(n_sv_vals)} the codebook does not list. Each is either an unrecoded "
            "missing code (a stray -99 or 999), a keying error (a 55 where 5 was meant), or "
            "unexplained — the `Kind` column says which. Only columns whose codebook documents "
            "a value range are checked, so every row here is a discrepancy between what the "
            "authors declared and what the data contains.",
            scroll_table(sv_tbl, maxrows=20),
        ]

    # ── scales ───────────────────────────────────────────────────────────────
    if scale_groups is not None and len(scale_groups) > 0:
        report += _scales_report(scale_groups, cb)
    elif not llm_on and previews is not None and len(previews) > 0:
        report += [
            "#### Scales",
            "Scale naming from the manuscript needs an LLM (enable with `llm_use(TRUE)`); the "
            "dictionary rules matcher still names instruments whose abbreviation matches a "
            "known scale.",
        ]

    report += cb._scale_text_report(
        text_scales, matched=_unique(s for s, nm in zip(l_scale, named, strict=True) if nm)
    )

    # ── tasks ────────────────────────────────────────────────────────────────
    if n_task_files > 0 or n_tasks_paper_only > 0:
        task_lines: list[str] = []
        if n_task_files > 0:
            found = (
                f"{n_tasks_found:d} distinct task{plural(n_tasks_found)} "
                f"{'was' if n_tasks_found == 1 else 'were'}"
                if n_tasks_found > 0
                else "None could be"
            )
            task_lines.append(
                f"{n_task_files:d} data file{plural(n_task_files)} "
                f"contain{'s' if n_task_files == 1 else ''} columns that look like a behavioural "
                "task (reaction times, accuracy, or a block of correct/incorrect items). "
                f"{found} named to a known task."
            )
        if n_tasks_paper_only > 0:
            task_lines.append(
                f"**{n_tasks_paper_only:d} task{plural(n_tasks_paper_only)} named in the "
                f"manuscript {'has' if n_tasks_paper_only == 1 else 'have'} no matching data.** "
                "The trial-level data may not be shared, may live in a file we could not read, "
                "or may use column names we did not recognise. Sharing trial-level data (one "
                "row per trial, with condition, response time and accuracy) would let the task "
                "be verified:"
            )
            task_lines += [f"- {_pstr(t)}" for t in tasks_paper_only]
        if task_lines:
            report += ["#### Tasks", *task_lines]

    if llm_on:
        who = f"LLM model '{llm_model_used}' " if llm_model_used is not None else "LLM "
        report.append(
            f"{who}reviewed ambiguous cases (parsed {llm_parse_files:d} "
            f"file{plural(llm_parse_files)}, matched {llm_match_cols:d} "
            f"column{plural(llm_match_cols)}, merged {llm_merge_cols:d} "
            f"label{plural(llm_merge_cols)})."
        )

    dup_warnings = cb._codebook_duplicate_name_warnings(previews)
    if dup_warnings:
        report += ["#### Duplicated Column Names", *(f"- {w}" for w in dup_warnings)]
    if gate_msgs:
        report += [f"- {g}" for g in gate_msgs]

    # ── 8. summary table ─────────────────────────────────────────────────────
    pids = (
        _unique(l_pid) if l_pid is not None else [_pid(paper, labels_df, columns_df, structure_df)]
    )
    summary = pd.DataFrame({"paper_id": pd.Series(pids, dtype="string")})
    cols_int: dict[str, list[int]] = {
        "column_n": [coverage.get(p, [0, 0, 0, 0])[0] for p in pids],
        "matched_n": [coverage.get(p, [0, 0, 0, 0])[1] for p in pids],
        "unmatched_n": [
            coverage.get(p, [0, 0, 0, 0])[0] - coverage.get(p, [0, 0, 0, 0])[1] for p in pids
        ],
        "clean_n": [coverage.get(p, [0, 0, 0, 0])[2] for p in pids],
        "conflicted_n": [coverage.get(p, [0, 0, 0, 0])[3] for p in pids],
        "codebook_var_n": [cb_counts.get(p, 0) for p in pids],
        "unused_var_n": [unused_counts.get(p, 0) for p in pids],
    }
    for col, vals in cols_int.items():
        summary[col] = pd.Series(vals, dtype="Int64")
    for col, val in (
        ("scale_blocks_n", n_scale_files),
        ("scale_named_n", n_scales_found),
        ("scale_unnamed_n", n_scale_unnamed),
        ("task_files_n", n_task_files),
        ("task_named_n", n_tasks_found),
        ("task_paper_only_n", n_tasks_paper_only),
    ):
        summary[col] = pd.Series([val] * len(summary), dtype="Int64")

    return {
        "table": labels_df,
        "codebook_vars": codebook_vars_df,
        "scale_violations": scale_violation_df,
        "scales_osd": scales_osd,
        "summary_table": summary,
        "na_replace": dict(_NA_REPLACE),
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }


def _sort_na_last(x: Any) -> tuple[int, str]:
    """``dplyr::arrange()`` key: C-locale (code point) order, NA last."""
    import pandas as pd

    if x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)):
        return (1, "")
    return (0, str(x))


def _col_or_na(df: Any, col: str) -> Any:
    import pandas as pd

    if col in df.columns:
        return df[col].to_numpy()
    return pd.Series([None] * len(df), dtype="string").to_numpy()


def _codes_as_numeric(codes: Any) -> list[float]:
    """``as.numeric(names(codes) %||% codes)`` without NA."""
    import pandas as pd

    from metacheck.datacheck._checks_rvec import as_numeric_str
    from metacheck.modules._codebook import _na, _pstr

    if codes is None:
        return []
    if isinstance(codes, Mapping):
        vals: list[Any] = list(codes.keys())
    elif isinstance(codes, pd.Series):
        vals = list(codes.index)
    else:
        vals = list(codes)
    out = []
    for v in vals:
        if _na(v):
            continue
        if isinstance(v, bool):
            f: float | None = float(v)
        elif isinstance(v, int | float):
            f = float(v)
        else:
            f = as_numeric_str(_pstr(v))
        if f is not None and not math.isnan(f):
            out.append(f)
    return out


def _harvest_labels(
    haven_idx: list[int],
    locs: list[Any],
    s_group: list[Any] | None,
    s_pid: list[Any] | None,
    extract: Any,
) -> list[Any]:
    """Embedded variable / value labels of labelled data files (haven, JASP, jamovi)."""
    import pandas as pd

    from metacheck.datacheck._columns_codebook import _import_data

    try:
        import pyreadstat  # noqa: F401

        have_haven = True
    except ImportError:  # pragma: no cover - pyreadstat is a dependency
        have_haven = False
    out = []
    for i in haven_idx:
        p = str(locs[i])
        file_group = s_group[i] if s_group is not None else None
        ext = _file_ext(p).lower()
        try:
            if ext in _HAVEN_EXTS:
                df = _haven_labels_frame(p, ext) if have_haven else None
            elif ext in ("jasp", "omv"):
                df = _import_data(p, ext)
            else:
                df = None
        except Exception:
            df = None
        if df is None:
            continue
        res = extract(df, os.path.basename(p), group=file_group)
        if res is None:
            continue
        res = res.copy()
        res["parse_method"] = pd.Series(["haven"] * len(res), index=res.index, dtype="string")
        if s_pid is not None:
            res["paper_id"] = pd.Series([s_pid[i]] * len(res), index=res.index, dtype="string")
        out.append(res)
    return out


def _haven_labels_frame(path: str, ext: str) -> Any:
    """``as.data.frame(haven::read_sav/read_dta/read_sas(path, n_max = 0L))``.

    Labels only, as the R module reads them: a zero-row frame whose column
    attributes carry the variable and value labels (the value labels as
    ``(label, code)`` pairs in file order, as
    :func:`metacheck.datacheck._columns_codebook._haven_frame` keeps them).
    """
    from metacheck.datacheck._files_readers import read_stat_file

    return read_stat_file(path, ext, 0)


def _backfill_scale_groups(scale_groups: Any, labels_df: Any, cb: Any) -> Any:
    """Copy each unnamed group's column-level name (source, confidence) back from labels_df."""
    import pandas as pd

    _ok, _vals, _key = cb._ok, cb._vals, cb._key
    lk: dict[str, int] = {}
    for i, (f, c) in enumerate(
        zip(
            _vals(labels_df, "source_file") or [],
            _vals(labels_df, "column_name") or [],
            strict=True,
        )
    ):
        lk.setdefault(_key(f, c), i)
    l_scale = _vals(labels_df, "scale") or []
    l_src = _vals(labels_df, "scale_source")
    l_conf = _vals(labels_df, "scale_confidence")
    g_scale = _vals(scale_groups, "scale") or []
    g_src = _vals(scale_groups, "scale_source")
    g_conf = _vals(scale_groups, "confidence")
    g_file = _vals(scale_groups, "source_file") or []
    changed = False
    for i, cols in enumerate(scale_groups["columns"].tolist()):
        if _ok(g_scale[i]):
            continue
        idx = [lk[k] for k in (_key(g_file[i], c) for c in cols) if k in lk]
        nm = [l_scale[j] for j in idx if _ok(l_scale[j])]
        if not nm:
            continue
        g_scale[i] = nm[0]
        changed = True
        if l_src is not None and g_src is not None:
            ss = [l_src[j] for j in idx if _ok(l_src[j])]
            if ss:
                g_src[i] = ss[0]
        if l_conf is not None and g_conf is not None:
            cf = [l_conf[j] for j in idx if _ok(l_conf[j])]
            if cf:
                g_conf[i] = cf[0]
    if not changed:
        return scale_groups
    attrs = dict(scale_groups.attrs)
    out = scale_groups.copy()
    out["scale"] = pd.Series(g_scale, index=out.index, dtype="string")
    if g_src is not None:
        out["scale_source"] = pd.Series(g_src, index=out.index, dtype="string")
    if g_conf is not None:
        out["confidence"] = pd.Series(g_conf, index=out.index, dtype="string")
    out.attrs = attrs
    return out


def _scales_report(sg: Any, cb: Any) -> list[Any]:
    """The "Scales" report section: the scale-group inventory, orphan totals, guidance."""
    import pandas as pd

    from metacheck._r.base import plural
    from metacheck.datacheck._checks_rvec import tolower
    from metacheck.report import scroll_table

    _ok, _vals, _pstr = cb._ok, cb._vals, cb._pstr
    n = len(sg)
    scale = _vals(sg, "scale") or [None] * n
    conf = _vals(sg, "confidence") or [None] * n
    src = _vals(sg, "scale_source") or [None] * n
    max_item = _vals(sg, "max_item") or [None] * n
    inv_tbl = pd.DataFrame(
        {
            "File": sg["source_file"].to_numpy(),
            "Abbrev": sg["prefix"].to_numpy(),
            "Columns": sg["n_columns"].to_numpy(),
            "Max item": ["?" if m is None else cb._max_item_str(m) for m in max_item],
            "Scale": [s if _ok(s) else "— not matched to a named scale —" for s in scale],
            "Confidence": ["" if c is None else c for c in conf],
            "Source": ["" if s is None else s for s in src],
        }
    )
    n_named = sum(_ok(s) for s in scale)
    out: list[Any] = [
        "#### Scales",
        f"We detected {n:d} column group{plural(n)} (by shared abbreviation) that look like "
        f"scales; {n_named:d} {'was' if n_named == 1 else 'were'} matched to a named instrument "
        "from the paper text or dictionary. Groups we could not name are listed too — they are "
        "real scale-like column families whose instrument the manuscript did not identify. "
        "Identified scales are exported in the OpenScales OSD structure.",
        scroll_table(inv_tbl, maxrows=40),
    ]
    if "totals_only" in sg.columns:
        tots = _vals(sg, "totals_only") or [None] * n
        tot = [bool(t) if t is not None else False for t in tots]
        tot = [t and _ok(s) for t, s in zip(tot, scale, strict=True)]
        have_items = {tolower(s) for s, t in zip(scale, tot, strict=True) if not t and _ok(s)}
        orphan = [
            t and tolower(_pstr(s)) not in have_items for t, s in zip(tot, scale, strict=True)
        ]
        if any(orphan):
            files = _vals(sg, "source_file") or [None] * n
            cols = sg["columns"].tolist()
            lines = [
                f"- **{_pstr(scale[j])}** (columns `{'`, `'.join(_pstr(c) for c in list(cols[j])[:6])}` "
                f"in {_pstr(files[j])})"
                for j in range(n)
                if orphan[j]
            ]
            out += [
                "**Scale totals without item-level data.** For the following, we identified "
                "what looks like the total or average score, but found **no individual item "
                "columns**. The items may not be shared, or are labelled differently. Consider "
                "sharing the item-level data, or labelling items clearly, so the scale can be "
                "verified:",
                *lines,
            ]
    n_unnamed = n - n_named
    n_low = sum(c in ("medium", "low") for c in conf)
    if n_unnamed > 0 or n_low > 0:
        out.append(
            "To let this tool (and anyone reusing the data) identify these scales with **high "
            "confidence**:"
            '\n- **Name the instrument in the manuscript with its abbreviation** — e.g. "the '
            'Breakup Distress Scale (BDS)" — matching the column prefix.'
            '\n- **Add a codebook** giving the item wording, or state the number of items ("a '
            '15-item scale").'
        )
    return out
