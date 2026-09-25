"""Psych-DS Check (port of ``inst/modules/psychds_check.R``).

The module compares a repository's files (as classified by ``data_check``)
with the Psych-DS 1.5 dataset layout: which required items are present,
which files would have to move, the target file tree, and suggestions.
It only *checks*; nothing on disk is changed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r import grepl, gsub, is_na, plural, r_sort_key, regextract, sub
from pytacheck.module import module
from pytacheck.report import scroll_table

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


def _na(x: Any) -> bool:
    return x is None or is_na(x)


def _chr(x: Any) -> str | None:
    """One value of a character vector (``None`` for ``NA``)."""
    if _na(x):
        return None
    if isinstance(x, str):
        return x
    from pytacheck._r import as_character

    return str(as_character(x))


def _paste(x: str | None) -> str:
    """How ``paste0()`` prints one value (``NA`` becomes ``"NA"``)."""
    return "NA" if x is None else x


def _col(df: pd.DataFrame, name: str) -> list[str | None] | None:
    """``df$name`` as a list of strings (``None`` when the column is absent)."""
    if name not in df.columns:
        return None
    return [_chr(v) for v in df[name].tolist()]


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
    return path.rstrip("/").rpartition("/")[2]


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


def _r_and(*values: bool | None) -> bool | None:
    """R's three-valued ``&`` of scalars (``None`` is ``NA``)."""
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def _neq(a: str | None, b: str | None) -> bool | None:
    """R ``a != b`` for two strings (``NA`` when either is missing)."""
    if a is None or b is None:
        return None
    return a != b


def _ifelse(test: bool | None, yes: Any, no: Any) -> Any:
    """R ``ifelse()`` of one element (a missing test gives ``NA``)."""
    if test is None:
        return None
    return yes if test else no


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
    paths = [_chr(v) for v in nodes["path"].tolist()]
    statuses = [_chr(v) for v in nodes["status"].tolist()]
    notes = (
        [_chr(v) for v in nodes["note"].tolist()] if "note" in nodes.columns else [""] * len(paths)
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
    """R ``.pid()``: the paper's (first) ID, else the first ``paper_id`` of *dfs*."""
    from pytacheck.papers.tables import paper_id

    ids: list[Any] = list(paper_id(paper))
    for df in dfs:
        if len(ids) > 0:
            break
        if isinstance(df, pd.DataFrame) and "paper_id" in df.columns:
            ids = list(dict.fromkeys(_chr(v) for v in df["paper_id"].tolist()))
    return None if len(ids) == 0 else _chr(ids[0])


def _summary(pid: str | None, counts: Sequence[int]) -> pd.DataFrame:
    names = (
        "required_met",
        "required_missing",
        "recommended_met",
        "recommended_missing",
        "misplaced_n",
    )
    data: dict[str, Any] = {"paper_id": pd.array([pid], dtype="string")}
    for name, n in zip(names, counts, strict=True):
        data[name] = pd.array([n], dtype="Int64")
    return pd.DataFrame(data)


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
    from pytacheck.module import get_prev_outputs, module_run

    structure_df = get_prev_outputs("data_check", "structure")
    columns_df = get_prev_outputs("data_check", "table")
    group_no_evidence = get_prev_outputs("data_check", "group_no_evidence")
    if structure_df is None:
        # data_check did not run earlier in this chain: run it now, forwarding
        # this call's own cache/skip_on_api_limit settings
        if model is None:
            from pytacheck.llm.core import llm_model

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
        `materials/`, `documentation/`, `documentation/codebooks/`). This module
        compares those
        expectations against the repository's actual files, using the classification
        and columns from `data_check` and the variable documentation from
        `codebook_check`.

        Each repository file is mapped to its Psych-DS destination by type: data →
        `data/`, code → `analysis/`, materials → `materials/`, documentation →
        `documentation/` (or `documentation/codebooks/` for `documentation` rows
        whose fine-grained `doc_role` is `"codebook"`), unknown → `unknown/` (a
        visible junk drawer for files `data_check` could not place at all — rename
        the file to include a data/code/materials/output/documentation keyword to
        get it classified), the root readme (`doc_role == "readme"`) → root
        `README`. The module then
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
        "paper": "a paper object or paperlist object, or NULL to check local files only "
        "(see [test_paper()])",
        "local_path": "optional path to a local directory, passed through to `data_check` / "
        "`repo_check` when their output is not already available",
        "local_only": "if TRUE, skip online repository lookups (see `repo_check`)",
        "model": "the LLM model name (see `llm_model_list()`) used only when `llm_use(TRUE)`",
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
    from pytacheck.module import get_prev_outputs

    # 1. Inputs from data_check (+ codebook_check for documentation) ----------
    structure_df, columns_df, group_no_evidence = _data_check_outputs(
        paper, local_path, local_only, model, params, cache, skip_on_api_limit
    )
    no_evidence = _is_true(group_no_evidence)
    labels_df = get_prev_outputs("codebook_check", "table")

    if structure_df is None or len(structure_df) == 0:
        return {
            "table": pd.DataFrame(),
            "summary_table": _summary(_pid(paper, structure_df, columns_df), [0, 0, 0, 0, 0]),
            "na_replace": dict(_NA_REPLACE),
            "traffic_light": "na",
            "summary_text": _EMPTY_TEXT,
        }

    n_files = len(structure_df)
    # `structure_df$file_name` / `$data_type` are NULL when the column is absent
    file_name = _col(structure_df, "file_name")
    data_type = _col(structure_df, "data_type")

    # 2. Do study groups exist? ------------------------------------------------
    groups = _col(structure_df, "group") or [None] * n_files
    doc_role = _col(structure_df, "doc_role") or [None] * n_files
    study_groups = list(dict.fromkeys(g for g in groups if g is not None))
    have_groups = len(study_groups) > 0
    multi_study = len(study_groups) > 1

    # 3. Map each file to its Psych-DS target path -----------------------------
    names = None if file_name is None else [_basename(_backslash_to_slash(f)) for f in file_name]
    # whole-column regex work, looked up per row by target_of()
    stems = [] if names is None else _keyword_slug(_file_path_sans_ext(names))
    name_ext = [] if names is None else _file_ext(names)

    def target_of(i: int) -> str:
        # `structure_df$data_type[i] %||% "unknown"`: only an absent column is "unknown"
        dt = "unknown" if data_type is None else data_type[i]
        role = doc_role[i]
        grp = groups[i]
        prefix = f"study-{grp}/" if multi_study and grp is not None else ""
        if dt is None:
            raise ValueError("missing value where TRUE/FALSE needed")
        is_named = dt == "data" or (dt == "documentation" and role in ("readme", "license"))
        if names is None:
            # no file_name column: `name` is character(0), so `if (!nzchar(stem))`
            # and `if (nzchar(ext))` fail; paste0() drops the empty name
            if is_named:
                raise ValueError("argument is of length zero")
            return f"{prefix}{_TYPE_TO_SUBDIR.get(dt, 'documentation')}/"
        if dt == "data":
            stem = stems[i]
            if stem == "":
                stem = f"file{i + 1}"
            return f"{prefix}data/study-{_paste(stem)}_data.csv"
        if is_named:
            ext = name_ext[i]
            base = "README" if role == "readme" else "LICENSE"
            # nzchar(NA) is TRUE: a missing extension gives "README.NA"
            return prefix + (f"{base}.{_paste(ext)}" if ext is None or ext != "" else base)
        sub_dir = _TYPE_TO_SUBDIR.get(dt, "documentation")
        return f"{prefix}{sub_dir}/{_paste(names[i])}"

    target_path: list[str | None] = [target_of(i) for i in range(n_files)]
    file_path = _col(structure_df, "file_path")
    if file_name is None:
        # R carries on with zero-length vectors until a later step fails
        if file_path is None:
            # current_path is logical(0): basename(logical(0))
            raise TypeError("a character vector argument expected")
        if any(p is None for p in file_path):
            # ifelse(is.na(current_path), NULL, current_path)
            raise ValueError("replacement has length zero")
        # target_path becomes logical(0): data.frame(path = logical(0), status = <n>)
        raise ValueError(f"arguments imply differing number of rows: 0, {n_files}")
    if data_type is None:
        # is_data is logical(0), so ifelse(is_raw_data, ...) empties target_path
        raise ValueError(f"arguments imply differing number of rows: 0, {n_files}")
    is_data = [dt is not None and dt == "data" for dt in data_type]
    raw_current = file_path if file_path is not None else file_name
    current_path = [
        f if c is None else c
        for c, f in zip((_backslash_to_slash(p) for p in raw_current), file_name, strict=True)
    ]
    # target_of() always returns a path; kept defensively as in R
    is_excluded = [t is None for t in target_path]
    misplaced = [
        _r_and(not ex, _neq(c, t))
        for ex, c, t in zip(is_excluded, current_path, target_path, strict=True)
    ]

    # tabular data not already in CSV is converted (original kept alongside);
    # raw (non-tabular) data keeps its own name and extension
    from pytacheck.datacheck.files import data_format

    src_ext = [_tolower(e) for e in _file_ext(file_name)]
    src_format = data_format(src_ext)
    needs_convert = [
        _r_and(d, _neq(e, "csv"), fmt == "tabular")
        for d, e, fmt in zip(is_data, src_ext, src_format, strict=True)
    ]
    is_raw_data = [
        _r_and(d, e is None or e != "", _neq(e, "csv"), None if nc is None else not nc)
        for d, e, nc in zip(is_data, src_ext, needs_convert, strict=True)
    ]

    real_names = names or []

    def same_dir_real_name(tp: str | None, i: int) -> str:
        # the same directory as the _data.csv target, but the file's own basename
        return f"{_dirname(_paste(tp))}/{_paste(real_names[i])}"

    original_target = [
        same_dir_real_name(target_path[i], i) if needs_convert[i] is True else None
        for i in range(n_files)
    ]
    raw_target = [
        same_dir_real_name(target_path[i], i) if is_raw_data[i] is True else None
        for i in range(n_files)
    ]
    target_path = [
        _ifelse(raw, rt, tp)
        for raw, rt, tp in zip(is_raw_data, raw_target, target_path, strict=True)
    ]

    # 4. Required / recommended compliance items -------------------------------
    file_names_lc = [_tolower(_basename(c)) for c in current_path]
    has_dataset_desc = any(f == "dataset_description.json" for f in file_names_lc)
    has_data_files = any(is_data)
    has_readme = any(r is not None and r == "readme" for r in doc_role)
    has_changes = any(grepl(r"^changes(\.|$)", file_names_lc))

    n_columns = len(columns_df) if columns_df is not None else 0
    describable = n_columns > 0
    if isinstance(labels_df, pd.DataFrame) and "label_status" in labels_df.columns:
        n_documented = sum(
            _chr(s) in ("labelled", "llm") for s in labels_df["label_status"].tolist()
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

    # 6. Build the required-vs-present tree ------------------------------------
    node_path: list[str | None] = list(target_path)
    node_status: list[str | None] = [_ifelse(m, "move", "present") for m in misplaced]
    node_note: list[str | None] = [
        _ifelse(m, f"move from {_paste(c)}", "")
        for m, c in zip(misplaced, current_path, strict=True)
    ]

    def scaffold(prefix: str = "", is_dataset: bool = True) -> list[tuple[str, bool]]:
        rows = [(f"{prefix}README", not has_readme), (f"{prefix}CHANGES", not has_changes)]
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
    if any(m is None for m in misplaced):
        # an NA current path (no file name and no file path) makes
        # sum(misplaced) NA, and `if (n_misplaced > 0)` fails
        raise ValueError("missing value where TRUE/FALSE needed")
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
        if have_groups
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
        from pytacheck.llm.core import llm_use

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
    summary_table = _summary(
        _pid(paper, structure_df, columns_df),
        [sum(req_met), n_req_missing, sum(rec_met), n_rec_missing, n_misplaced],
    )

    status = [
        "excluded" if ex else _ifelse(m, "move", "present")
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
            "file_name": pd.array(file_name, dtype="string"),
            "data_type": pd.array(data_type, dtype="string"),
            "group": pd.array(groups, dtype="string"),
            "current_path": pd.array(current_path, dtype="string"),
            "target_path": pd.array(target_path, dtype="string"),
            "status": pd.array(status, dtype="string"),
            "convert": pd.array(needs_convert, dtype="boolean"),
            "original_target": pd.array(original_target, dtype="string"),
            "referenced_by": pd.Series(referenced_by, dtype=object),
        }
    )

    return {
        "table": plan_table,
        "summary_table": summary_table,
        "na_replace": dict(_NA_REPLACE),
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
