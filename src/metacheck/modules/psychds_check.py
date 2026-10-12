"""Psych-DS Check (port of ``inst/modules/psychds_check.R``).

The module compares a repository's files (as classified by ``data_check``)
with the Psych-DS 1.5 dataset layout: which required items are present,
which files would have to move, the target file tree, and suggestions.
It only *checks*; nothing on disk is changed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r import gsub, plural, r_sort_key, regextract, slashed, sub
from metacheck._values import as_str
from metacheck.module import module
from metacheck.report import scroll_table

# R: type_to_subdir -- file type -> Psych-DS subdirectory (data and readme are
# handled separately, by data_type and doc_role). "unknown" gets its own visible
# folder rather than being folded into documentation/.
_TYPE_TO_SUBDIR = {
    "code": "analysis",
    "materials": "materials",
    "output": "outputs",
    "documentation": "documentation",
    "unknown": "unknown",
}

_NA_REPLACE = {
    "required_met": 0.0,
    "required_missing": 0.0,
    "recommended_met": 0.0,
    "recommended_missing": 0.0,
    "misplaced_n": 0.0,
}

_EMPTY_TEXT = "We found no repository files to check for Psych-DS compliance."
_COMPLIANT_TEXT = "This repository already contains the files required for a Psych-DS dataset."
_MISSING_SPAN = '<span style="color:#c0392b">'
_TREE_INTRO = (
    "The tree below shows the Psych-DS layout this repository should have. Files in "
    f"{_MISSING_SPAN}red</span> are required but missing; files annotated "
    "*(move from ...)* exist but need relocating."
)
_CALL_TO_ACTION = [
    "You can generate a Psych-DS-compliant copy of this repository — with the moves above "
    "applied and a `dataset_description.json` built from the extracted variables — using "
    "`metacheck::convert_psychds(paper, output_dir)`.",
    "To also produce a human-readable codebook of the extracted variables (a labelled data "
    "frame plus a ready-to-run R Markdown document for the `codebook` package), use "
    "`metacheck::convert_codebook(paper, output_dir)`.",
]

# (item, detail) of the required / recommended Psych-DS items, in R's order
_REQUIRED = (
    (
        "dataset_description.json",
        "Root metadata file describing the dataset (required).",
    ),
    (
        "data/ directory with data files",
        "At least one machine-readable data file under data/ (required).",
    ),
    (
        "describable variables (variableMeasured)",
        "Data files must have extractable, typed columns to populate variableMeasured (required).",
    ),
)
_RECOMMENDED = (
    ("README", "A README at the repository root (recommended)."),
    ("CHANGES", "A CHANGES file logging dataset versions (recommended)."),
    (
        "variable descriptions",
        "Codebook descriptions for variables improve reuse (recommended; see codebook_check).",
    ),
)


# -- small R helpers ---------------------------------------------------------------


def _paste(x: str | None) -> str:
    """How ``paste0()`` prints one value (``NA`` becomes ``"NA"``)."""
    return "NA" if x is None else x


def _col(df: pd.DataFrame, name: str) -> list[str | None] | None:
    """``df$name`` as a list of strings (``None`` when the column is absent)."""
    if name not in df.columns:
        return None
    return [as_str(v) for v in df[name].tolist()]


def _is_true(x: Any) -> bool:
    """R ``isTRUE()``: a single, non-missing ``TRUE``."""
    if isinstance(x, pd.Series | list | tuple | np.ndarray):
        vals = list(np.asarray(x, dtype=object).reshape(-1))
        return len(vals) == 1 and _is_true(vals[0])
    if isinstance(x, np.bool_):
        return bool(x)
    return x is True


def _backslash_to_slash(x: str | None) -> str | None:
    """R ``gsub("\\\\", "/", x)``."""
    return None if x is None else x.replace("\\", "/")


def _basename(path: str | None) -> str | None:
    """R ``basename()`` on Unix: tilde expansion, trailing slashes dropped."""
    if path is None:
        return None
    if path.startswith("~"):
        path = os.path.expanduser(path)
    return slashed(path).rstrip("/").rpartition("/")[2]


def _dirname(path: str) -> str:
    """R ``dirname()`` on Unix."""
    if path.startswith("~"):
        path = os.path.expanduser(path)
    if path == "":
        return ""
    stripped = path.rstrip("/")
    if not stripped:
        return "/"
    head, sep, _ = stripped.rpartition("/")
    if not sep:
        return "."
    head = head.rstrip("/")
    return head if head else "/"


def _file_ext(x: list[str | None]) -> list[str | None]:
    """``tools::file_ext()`` of a vector: the trailing alphanumeric extension, or ``""``."""
    return [
        None if v is None else ("" if m is None else m[1:])
        for v, m in zip(x, regextract(r"\.([[:alnum:]]+)$", x), strict=True)
    ]


def _file_path_sans_ext(x: list[str | None]) -> list[str | None]:
    """``tools::file_path_sans_ext()`` of a vector."""
    out: list[str | None] = sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x)
    return out


def _keyword_slug(x: list[str | None]) -> list[str | None]:
    """R ``keyword_slug()``: drop what a Psych-DS keyword *value* may not contain."""
    out: list[str | None] = gsub("[^a-zA-Z0-9]+", "", x)
    return out


def _tolower(x: str | None) -> str | None:
    """R ``tolower()`` (``towlower()`` per character)."""
    if x is None:
        return None
    if x.isascii():
        return x.lower()
    # towlower() maps U+0130 to "i"; other one-to-many lowerings stay unchanged
    return "".join("i" if c == "\u0130" else low if len(low := c.lower()) == 1 else c for c in x)


# -- the target tree ---------------------------------------------------------------


def _esc(x: str) -> str:
    x = x.replace("&", "&amp;")
    x = x.replace("<", "&lt;")
    return x.replace(">", "&gt;")


def psychds_tree_html(nodes: pd.DataFrame | None) -> str:
    """Render target paths as an HTML ``<pre>`` tree.

    Port of the module-local ``psychds_tree_html()`` in
    ``inst/modules/psychds_check.R``. *nodes* has columns ``path``,
    ``status`` (``"present"``, ``"move"``, ``"missing"`` or
    ``"present-scaffold"``) and ``note``. Missing leaves are shown in red and
    moved leaves carry their note; directories come first, then names in
    case-insensitive order.
    """
    if nodes is None or len(nodes) == 0:
        return ""
    paths = [as_str(v) for v in nodes["path"].tolist()]
    statuses = [as_str(v) for v in nodes["status"].tolist()]
    notes = (
        [as_str(v) for v in nodes["note"].tolist()]
        if "note" in nodes.columns
        else [""] * len(paths)
    )
    # nodes[!duplicated(nodes$path), ]
    seen: set[Any] = set()
    keep: list[int] = []
    for i, p in enumerate(paths):
        if p not in seen:
            seen.add(p)
            keep.append(i)
    norm = [_backslash_to_slash(paths[i]) for i in keep]
    # strsplit(norm, "/", fixed = TRUE), empty components dropped. An empty
    # path fails R's vapply(subset, x[[1]]) before anything is drawn; an NA
    # path is a top-level head whose `status_of[[NA]]` lookup fails later on.
    keys = [p for p in norm if p is not None]
    parts_list = [[s for s in p.split("/") if s] for p in keys]
    if len(keys) < len(norm) or not all(parts_list):
        raise IndexError("subscript out of bounds")
    # setNames(...)[[full]]: the first element with that name
    status_of: dict[str, str | None] = {}
    note_of: dict[str, str | None] = {}
    for p, i in zip(keys, keep, strict=True):
        status_of.setdefault(p, statuses[i])
        note_of.setdefault(p, notes[i])

    lines: list[str] = []

    def walk(subset: list[list[str]], parents: str, prefix: str = "") -> None:
        # the tails under each head, heads in order of first appearance
        tails_of: dict[str, list[list[str]]] = {}
        for x in subset:
            tails_of.setdefault(x[0], []).append(x[1:])
        has_child = {h: any(tails) for h, tails in tails_of.items()}
        # order(!has_child, tolower(order_heads)): directories first, stable
        order_heads = sorted(tails_of, key=lambda h: (not has_child[h], r_sort_key(_tolower(h))))
        n_heads = len(order_heads)
        for i, h in enumerate(order_heads):
            is_last = i == n_heads - 1
            child_exists = has_child[h]
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            full = f"{parents}/{h}" if parents else h
            label = h + ("/" if child_exists else "")
            st = status_of.get(full, "")
            note = note_of.get(full, "")

            if st == "missing":
                styled = f"{_MISSING_SPAN}{_esc(label)}  ← missing</span>"
            elif st == "move" and note != "":
                # nzchar(NA) is TRUE and sprintf() prints an NA note as "NA"
                styled = f'{_esc(label)} <span style="color:#b9770e">({_esc(_paste(note))})</span>'
            else:
                styled = _esc(label)

            lines.append(_esc(prefix) + branch + styled)
            if child_exists:
                walk([t for t in tails_of[h] if t], full, next_prefix)

    walk(parts_list, "")
    return '<pre style="line-height:1.4">\n' + "\n".join(lines) + "\n</pre>"


# -- the module --------------------------------------------------------------------


def _pid(paper: Any, *dfs: pd.DataFrame | None) -> str | None:
    """R ``.pid()``: the paper's (first) ID, else the first ``paper_id`` of *dfs*.

    ``paper = None`` (local files only) takes the ID from *dfs* (R's
    ``paper_id(NULL)`` stops the module).
    """
    from metacheck.papers.tables import paper_id

    ids: list[Any] = list(paper_id(paper)) if paper is not None else []
    for df in dfs:
        if len(ids) > 0:
            break
        if isinstance(df, pd.DataFrame) and "paper_id" in df.columns:
            ids = list(dict.fromkeys(as_str(v) for v in df["paper_id"].tolist()))
    return None if len(ids) == 0 else as_str(ids[0])


_COUNT_NAMES = (
    "required_met",
    "required_missing",
    "recommended_met",
    "recommended_missing",
    "misplaced_n",
)


def _summary(rows: Sequence[tuple[str | None, Sequence[int]]]) -> pd.DataFrame:
    """One summary row per ``(paper_id, counts)``."""
    data: dict[str, Any] = {"paper_id": pd.array([r[0] for r in rows], dtype="string")}
    for k, name in enumerate(_COUNT_NAMES):
        data[name] = pd.array([r[1][k] for r in rows], dtype="Int64")
    return pd.DataFrame(data)


def _subset(df: Any, pid: str) -> Any:
    """The rows of *df* that belong to paper *pid* (``df`` itself without a ``paper_id``)."""
    if not isinstance(df, pd.DataFrame) or "paper_id" not in df.columns:
        return df
    keep = [as_str(v) == pid for v in df["paper_id"].tolist()]
    return df.loc[keep].reset_index(drop=True)


# Psych-DS's own validator rule for a data file name (schema_model "Datafile"):
# keyword-value pairs, then "_data.csv" / "_data.tsv"
_PSYCHDS_DATAFILE = re.compile(r"^[a-z]+-[a-zA-Z0-9]+(_[a-z]+-[a-zA-Z0-9]+)*_data\.(csv|tsv)$")
# documentation types whose readme/licence/CHANGES files go to the root (a bare
# LICENSE or CHANGES is "unknown" in data_check); dataset_description.json and
# ro-crate-metadata.json go there by name whatever their type ("code" for .json)
_ROOT_DOC_TYPES = ("documentation", "unknown")


def _data_check_outputs(
    paper: Any,
    local_path: Any,
    local_only: bool,
    model: Any,
    params: Mapping[str, Any] | None,
    cache: Any,
    skip_on_api_limit: bool,
) -> tuple[Any, Any, Any]:
    """``structure``/``table``/``group_no_evidence`` of a chained or fresh ``data_check``."""
    from metacheck.module import get_prev_outputs, module_run

    structure_df = get_prev_outputs("data_check", "structure")
    columns_df = get_prev_outputs("data_check", "table")
    group_no_evidence = get_prev_outputs("data_check", "group_no_evidence")
    if structure_df is None:
        # data_check did not run earlier in this chain: run it now, forwarding
        # this call's own cache/skip_on_api_limit settings
        if model is None:
            from metacheck.llm.core import llm_model

            model = llm_model()
        args: dict[str, Any] = {}
        if local_path is not None:
            args["local_path"] = local_path
        args.update(
            local_only=local_only,
            model=model,
            params=dict(params or {}),
            cache=cache,
            skip_on_api_limit=skip_on_api_limit,
        )
        mo = module_run(paper, "data_check", **args)
        structure_df = mo.get("structure")
        columns_df = mo.table
        group_no_evidence = mo.get("group_no_evidence")
    return structure_df, columns_df, group_no_evidence


@module(
    title="Psych-DS Check",
    description="""
        This module checks how close a repository is to the
        [Psych-DS](https://psych-ds.github.io/) machine-readable dataset standard. It
        reports which required files are missing, which existing files are in the
        wrong place, shows the file tree the repository *should* have (with missing
        items highlighted), and gives concrete suggestions for reaching compliance.
    """,
    details="""
        Psych-DS 1.5 expects, at minimum, a root `dataset_description.json` metadata
        file, a `data/` directory, and at least one `*_data.csv` file; it recommends
        a `README`, a `CHANGES` file, and conventional subdirectories (`analysis/`,
        `materials/`, `documentation/`). This module
        compares those
        expectations against the repository's actual files, using the classification
        and columns from `data_check` and the variable documentation from
        `codebook_check`.

        Each repository file is mapped to its Psych-DS destination by type: data →
        `data/` (a file already named like `study-1_data.csv` keeps its name, others
        become `study-<name>_data.csv`), code → `analysis/`, materials → `materials/`,
        documentation (codebooks included) → `documentation/`, unknown → `unknown/` (a
        visible junk drawer for files `data_check` could not place at all — rename
        the file to include a data/code/materials/output/documentation keyword to
        get it classified). The readme (`doc_role == "readme"`), licence,
        `CHANGES` and `dataset_description.json` go to the dataset root. The module then
        renders the target tree, marking files that are **present**, **missing**
        (required but absent — shown in red), or **misplaced** (present but at the
        wrong path — shown at the target location annotated with their current
        location).

        `data_check` assigns every file except the collection-level root README/
        `ro-crate-metadata.json` to exactly one study group — deterministically
        where path/repository/code-reference evidence allows, and via an LLM only
        for the residual cases (see `data_group_llm()`). A multi-study repository is
        modelled with a `study-<group>/` directory per study (each a complete
        Psych-DS dataset); only the root readme and ro-crate metadata sit at the
        collection root beside them. When no evidence at all names a study (no
        path/repository split and no LLM), a single-rooted tree is shown together
        with a note that subgrouping could not be detected. A materials or
        documentation file genuinely reused across studies is still owned by
        exactly one study; the others get a reference to it (not a copy) written
        into their own metadata by `convert_psychds()`.

        This module only *checks* compliance; it does not modify the repository. The
        report points to a dedicated builder for generating a compliant copy.
    """,
    keywords=["results"],
    requires=["network", "llm"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={
        # line breaks as in the roxygen block, which module_help() prints
        "paper": "a paper object or paperlist object, or NULL to check local\n"
        "files only (see [test_paper()])",
        "local_path": "optional path to a local directory, passed through to\n"
        "`data_check` / `repo_check` when their output is not already available",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "model": "the LLM model name (`provider/model`, for example `groq/openai/gpt-oss-20b`) used only when\n`llm_use(TRUE)`",
        "params": "a named list passed to `llm()`, used only when `llm_use(TRUE)`",
    },
)
def psychds_check(
    paper: Any,
    local_path: str | os.PathLike[str] | None = None,
    local_only: bool = False,
    model: str | None = None,
    params: Mapping[str, Any] | None = None,
    cache: Any = False,
    skip_on_api_limit: bool = False,
) -> dict[str, Any]:
    """Port of inst/modules/psychds_check.R::psychds_check().

    Reads ``data_check``'s ``structure``/``table``/``group_no_evidence`` from
    the current chain (running ``data_check`` with *local_path*,
    *local_only*, *model*, *params*, *cache* and *skip_on_api_limit* when it
    has not run yet) and ``codebook_check``'s ``table`` when available.
    ``model=None`` means ``llm_model()``, R's default.
    """
    from metacheck.module import get_prev_outputs

    # 1. Inputs from data_check (+ codebook_check for documentation) ----------
    structure_df, columns_df, group_no_evidence = _data_check_outputs(
        paper, local_path, local_only, model, params, cache, skip_on_api_limit
    )
    no_evidence = _is_true(group_no_evidence)
    labels_df = get_prev_outputs("codebook_check", "table")

    if structure_df is None or len(structure_df) == 0:
        return {
            "table": pd.DataFrame(),
            "summary_table": _summary([(_pid(paper, structure_df, columns_df), [0] * 5)]),
            "na_replace": dict(_NA_REPLACE),
            "traffic_light": "na",
            "summary_text": _EMPTY_TEXT,
        }

    a = _assess(structure_df, columns_df, labels_df)
    n_files = a["n_files"]
    req_met, rec_met = a["req_met"], a["rec_met"]
    n_req_missing, n_rec_missing, n_misplaced = (
        a["n_req_missing"], a["n_rec_missing"], a["n_misplaced"]
    )  # fmt: skip
    has_dataset_desc, describable = a["has_dataset_desc"], a["describable"]
    n_columns, n_documented = a["n_columns"], a["n_documented"]
    study_groups, multi_study = a["study_groups"], a["multi_study"]
    target_path, current_path, misplaced = a["target_path"], a["current_path"], a["misplaced"]
    is_excluded = a["is_excluded"]

    # 6. Build the required-vs-present tree ------------------------------------
    node_path: list[str | None] = []
    node_status: list[str | None] = []
    node_note: list[str | None] = []
    for ex, tp, m, c in zip(is_excluded, target_path, misplaced, current_path, strict=True):
        if ex:  # a file with no name has no place in the tree
            continue
        node_path.append(tp)
        node_status.append("move" if m else "present")
        node_note.append(f"move from {_paste(c)}" if m else "")

    def scaffold(prefix: str = "", is_dataset: bool = True) -> list[tuple[str, bool]]:
        rows = [
            (f"{prefix}README", not a["has_readme"]),
            (f"{prefix}CHANGES", not a["has_changes"]),
        ]
        if is_dataset:
            rows.insert(0, (f"{prefix}dataset_description.json", not has_dataset_desc))
        return rows

    if multi_study:
        scaffold_rows = [r for g in study_groups for r in scaffold(f"study-{g}/")]
        scaffold_rows += scaffold("", is_dataset=False)
    else:
        scaffold_rows = scaffold("")
    # only missing scaffolding is shown (present files already appear as files)
    for path, missing in scaffold_rows:
        if missing:
            node_path.append(path)
            node_status.append("missing")
            node_note.append("")
    tree_nodes = pd.DataFrame(
        {
            "path": pd.array(node_path, dtype="string"),
            "status": pd.array(node_status, dtype="string"),
            "note": pd.array(node_note, dtype="string"),
        }
    )
    tree_html = psychds_tree_html(tree_nodes)

    # 7. Suggestions ------------------------------------------------------------
    suggestions: list[str] = [
        f"**Add** {item} — {detail}"
        for (item, detail), met in zip(_REQUIRED, req_met, strict=True)
        if not met
    ]
    if n_misplaced > 0:
        mv = [i for i, m in enumerate(misplaced) if m]
        suggestions += [
            f"**Move** `{_paste(current_path[i])}` → `{_paste(target_path[i])}`." for i in mv[:5]
        ]
        if len(mv) > 5:
            more = len(mv) - 5
            suggestions.append(f"...and {more:d} more file{plural(more)} to relocate.")
    if describable and n_documented < n_columns:
        suggestions.append(
            f"**Document** the {n_columns - n_documented:d} of {n_columns:d} data "
            f"column{plural(n_columns)} without a codebook description (see codebook_check)."
        )
    suggestions += [
        f"**Add** {item} — {detail}"
        for (item, detail), met in zip(_RECOMMENDED, rec_met, strict=True)
        if not met
    ]

    # 8. Report -------------------------------------------------------------------
    if has_dataset_desc and n_req_missing == 0:
        summary_text = _COMPLIANT_TEXT
    else:
        n_req = len(req_met)
        parts = [
            f"{sum(req_met):d} of {n_req:d} required Psych-DS item{plural(n_req)} present; "
            f"{n_req_missing:d} missing."
        ]
        if n_misplaced > 0:
            parts.append(
                f"{n_misplaced:d} file{plural(n_misplaced)} would need to move to a "
                "Psych-DS location."
            )
        if n_rec_missing > 0:
            parts.append(f"{n_rec_missing:d} recommended item{plural(n_rec_missing)} missing.")
        # R: paste("\n- ", x = parts, collapse = "") -- sep = " " adds a second space
        summary_text = "".join(f"\n-  {p}" for p in parts)

    groups_text = (
        f" across {len(study_groups):d} study group{plural(len(study_groups))}"
        if study_groups
        else ""
    )
    report: list[Any] = [
        "This module checks the repository against the Psych-DS dataset standard. "
        f"We examined {n_files:d} file{plural(n_files)}{groups_text}."
    ]

    checklist = pd.DataFrame(
        {
            "Requirement": [item for item, _ in (*_REQUIRED, *_RECOMMENDED)],
            "Level": ["required"] * len(_REQUIRED) + ["recommended"] * len(_RECOMMENDED),
            "Status": ["present" if m else "missing" for m in (*req_met, *rec_met)],
            "Detail": [detail for _, detail in (*_REQUIRED, *_RECOMMENDED)],
        }
    )
    report += ["#### Compliance Checklist", scroll_table(checklist, maxrows=10)]
    report += ["#### Target Psych-DS Structure", _TREE_INTRO, tree_html]

    if no_evidence:
        from metacheck.llm.core import llm_use

        use = llm_use()
        report.append(
            "*Study subgrouping could not be detected: no file path or "
            "repository split names a study"
            + ("" if use else ", and no LLM was used")
            + ". Every file was placed in a single default study (`ex1`) rather "
            "than from real evidence. If this repository contains multiple "
            "studies, name the study in the file or folder names "
            "(`study1/`, `experiment2_data.csv`)"
            + ("" if use else " or run with `llm_use(TRUE)`")
            + " so a multi-study Psych-DS layout can be modelled.*"
        )

    if suggestions:
        report += ["#### Suggestions", "\n".join(f"- {s}" for s in suggestions)]

    report += _CALL_TO_ACTION

    # 9. Summary table + return ----------------------------------------------------
    # one row per paper, each assessed on its own files (R reports the pooled
    # counts once, for the first paper only)
    pids = (
        list(dict.fromkeys(v for v in (as_str(x) for x in structure_df["paper_id"]) if v))
        if "paper_id" in structure_df.columns
        else []
    )
    if len(pids) > 1:
        rows = []
        for pid in pids:
            sub = _assess(
                _subset(structure_df, pid), _subset(columns_df, pid), _subset(labels_df, pid)
            )
            rows.append((pid, sub["counts"]))
        summary_table = _summary(rows)
    else:
        # the paper that owns the files: on a paper list it need not be the
        # first paper, which R's .pid() would credit with them
        owner = pids[0] if pids else _pid(paper, structure_df, columns_df)
        summary_table = _summary([(owner, a["counts"])])

    status = [
        "excluded" if ex else ("move" if m else "present")
        for ex, m in zip(is_excluded, misplaced, strict=True)
    ]
    if "referenced_by" in structure_df.columns:
        # copies, so the plan never shares a list with the input structure
        referenced_by = [
            list(v) if isinstance(v, list) else v for v in structure_df["referenced_by"].tolist()
        ]
    else:
        referenced_by = [None] * n_files
    plan_table = pd.DataFrame(
        {
            "file_name": pd.array(a["file_name"], dtype="string"),
            "data_type": pd.array(a["data_type"], dtype="string"),
            "group": pd.array(a["groups"], dtype="string"),
            "current_path": pd.array(current_path, dtype="string"),
            "target_path": pd.array(target_path, dtype="string"),
            "status": pd.array(status, dtype="string"),
            "convert": pd.array(a["needs_convert"], dtype="boolean"),
            "original_target": pd.array(a["original_target"], dtype="string"),
            "referenced_by": pd.Series(referenced_by, dtype=object),
        }
    )

    return {
        "table": plan_table,
        "summary_table": summary_table,
        "na_replace": dict(_NA_REPLACE),
        "traffic_light": a["traffic_light"],
        "report": report,
        "summary_text": summary_text,
    }


def _assess(structure_df: pd.DataFrame, columns_df: Any, labels_df: Any) -> dict[str, Any]:
    """Target paths, compliance items and counts of one set of repository files."""
    from metacheck.datacheck.files import data_format

    n_files = len(structure_df)
    raw_name = _col(structure_df, "file_name") or [None] * n_files
    raw_path = _col(structure_df, "file_path") or [None] * n_files
    data_type = _col(structure_df, "data_type") or [None] * n_files
    groups = _col(structure_df, "group") or [None] * n_files
    doc_role = _col(structure_df, "doc_role") or [None] * n_files
    study_groups = list(dict.fromkeys(g for g in groups if g is not None))
    multi_study = len(study_groups) > 1

    # 2. Each file's name and current location ---------------------------------
    # the name falls back to the path's basename; a file with neither cannot be
    # placed (R fails on a missing name or data type)
    current_path = [
        _backslash_to_slash(p) if p is not None else _backslash_to_slash(f)
        for p, f in zip(raw_path, raw_name, strict=True)
    ]
    names = [
        _basename(_backslash_to_slash(f)) if f is not None else _basename(c)
        for f, c in zip(raw_name, current_path, strict=True)
    ]
    file_name = [f if f is not None else nm for f, nm in zip(raw_name, names, strict=True)]
    dtypes = [dt if dt is not None else "unknown" for dt in data_type]
    stems = _keyword_slug(_file_path_sans_ext(names))
    name_ext = _file_ext(names)

    # 3. Map each file to its Psych-DS target path -------------------------------
    def target_of(i: int) -> str | None:
        name = names[i]
        if name is None:
            return None
        dt, role, grp = dtypes[i], doc_role[i], groups[i]
        prefix = f"study-{grp}/" if multi_study and grp is not None else ""
        low = _tolower(name) or ""
        ext = name_ext[i] or ""
        if low == "dataset_description.json":
            return f"{prefix}dataset_description.json"
        if low == "ro-crate-metadata.json":
            return name  # collection-level metadata: the root, under its own name
        if dt == "data":
            if _PSYCHDS_DATAFILE.match(name):
                return f"{prefix}data/{name}"  # already a valid Psych-DS data file name
            stem = stems[i] or f"file{i + 1}"
            return f"{prefix}data/study-{stem}_data.csv"
        if dt in _ROOT_DOC_TYPES:
            if role == "readme" or role == "license":
                base = "README" if role == "readme" else "LICENSE"
                return prefix + (f"{base}.{ext}" if ext else base)
            if re.match(r"^changes(\.|$)", low):
                return prefix + name  # a CHANGES log sits at the root, under its own name
        return f"{prefix}{_TYPE_TO_SUBDIR.get(dt, 'documentation')}/{name}"

    target_path: list[str | None] = [target_of(i) for i in range(n_files)]
    is_data = [dt == "data" for dt in dtypes]

    # tabular data not already in CSV is converted (original kept alongside);
    # raw (non-tabular) data keeps its own name and extension; a file already
    # named as a Psych-DS data file (.csv or .tsv) stays as it is
    src_ext = [_tolower(e) or "" for e in name_ext]
    src_format = data_format(src_ext)
    compliant = [n is not None and bool(_PSYCHDS_DATAFILE.match(n)) for n in names]
    needs_convert = [
        d and e != "csv" and fmt == "tabular" and not ok
        for d, e, fmt, ok in zip(is_data, src_ext, src_format, compliant, strict=True)
    ]
    is_raw_data = [
        d and e != "" and e != "csv" and not nc and not ok
        for d, e, nc, ok in zip(is_data, src_ext, needs_convert, compliant, strict=True)
    ]

    def same_dir_real_name(i: int) -> str:
        # the same directory as the _data.csv target, but the file's own basename
        return f"{_dirname(_paste(target_path[i]))}/{_paste(names[i])}"

    original_target = [same_dir_real_name(i) if needs_convert[i] else None for i in range(n_files)]
    for i in range(n_files):
        if is_raw_data[i]:
            target_path[i] = same_dir_real_name(i)
    # misplaced against the final target (R compares before a raw file's target
    # is set, so raw data already in place was "moved" onto itself)
    is_excluded = [t is None for t in target_path]
    misplaced = [
        not ex and c != t for ex, c, t in zip(is_excluded, current_path, target_path, strict=True)
    ]

    # 4. Required / recommended compliance items ---------------------------------
    file_names_lc = [_tolower(n) for n in names]
    has_dataset_desc = any(f == "dataset_description.json" for f in file_names_lc)
    has_data_files = any(is_data)
    has_readme = any(r == "readme" for r in doc_role)
    has_changes = any(f is not None and re.match(r"^changes(\.|$)", f) for f in file_names_lc)

    n_columns = len(columns_df) if isinstance(columns_df, pd.DataFrame) else 0
    describable = n_columns > 0
    if isinstance(labels_df, pd.DataFrame) and "label_status" in labels_df.columns:
        n_documented = sum(
            as_str(s) in ("labelled", "llm") for s in labels_df["label_status"].tolist()
        )
    else:
        n_documented = 0

    req_met = [has_dataset_desc, has_data_files, describable]
    rec_met = [has_readme, has_changes, n_documented > 0]
    n_req_missing = sum(not m for m in req_met)
    n_rec_missing = sum(not m for m in rec_met)
    n_misplaced = sum(1 for m in misplaced if m)

    # 5. Traffic light ----------------------------------------------------------
    if has_dataset_desc and n_req_missing == 0:
        tl = "green"  # already compliant
    elif has_data_files and describable:
        tl = "yellow"  # convertible
    else:
        tl = "red"  # not convertible yet

    return {
        "n_files": n_files,
        "file_name": file_name,
        "data_type": data_type,
        "groups": groups,
        "study_groups": study_groups,
        "multi_study": multi_study,
        "current_path": current_path,
        "target_path": target_path,
        "is_excluded": is_excluded,
        "misplaced": misplaced,
        "needs_convert": needs_convert,
        "original_target": original_target,
        "has_dataset_desc": has_dataset_desc,
        "has_readme": has_readme,
        "has_changes": has_changes,
        "describable": describable,
        "n_columns": n_columns,
        "n_documented": n_documented,
        "req_met": req_met,
        "rec_met": rec_met,
        "n_req_missing": n_req_missing,
        "n_rec_missing": n_rec_missing,
        "n_misplaced": n_misplaced,
        "counts": [sum(req_met), n_req_missing, sum(rec_met), n_rec_missing, n_misplaced],
        "traffic_light": tl,
    }
