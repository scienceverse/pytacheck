"""The repository file tree, drawn as text (port of ``R/file-tree.R``).

Everything in each repository as a tree, with how each file was classified,
its study and any file-naming issue, and marks for junk files, hidden files
and empty folders. ``data_check`` shows it in its report (``#### Data
Tree``); the checklist report and the repository apps' brief report show the
same tree (:func:`file_tree`).

What a tree shows:

* a local folder (a ``repo_url`` that is a folder on this computer) is listed
  itself, with every file and folder, including hidden files, junk files and
  empty folders, plus any file of the file table that is not on disk (files
  inside zip archives, as ``data_check`` lists them);
* a repository link shows the file table's files plus ``repo_check``'s
  ``useless_files`` (junk), since ``repo_check`` leaves junk out of its table;
  empty folders cannot be shown, as repository listings hold only files;
* a junk folder (``.git/``, ``__pycache__/``, ``__MACOSX/``, ...) is one line
  with the number of files inside, not its contents;
* folders first (empty ones included), then files, each alphabetically
  without regard to capitals;
* the tree sits in a box with its own scroll bar, so a large repository does
  not make the report endless.

R's ``.file_tree_*`` functions are named without the dot here. Indices are
0-based (R's are 1-based).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import r_sort_key

__all__ = [
    "brief_file_tree",
    "brief_insert_after_summary",
    "file_tree",
    "file_tree_block",
    "file_tree_drop_from_report",
    "file_tree_entries",
    "file_tree_from_report",
    "file_tree_report_range",
    "file_tree_rows",
    "tree_type_icon",
    "tree_type_label",
    "useless_file_kind",
    "useless_file_severity",
]

_TREE_ICONS = {
    "data": "\U0001f4ca",  # 📊
    "code": "\U0001f4bb",  # 💻
    "documentation": "\U0001f4d6",  # 📖
    "materials": "\U0001f4c1",  # 📁
    "output": "\U0001f4c8",  # 📈
}
_README_ICON = "\U0001f4c4"  # 📄
_UNKNOWN_ICON = "❓"  # ❓

#: The marks for files and folders that are not classified.
_MARK_JUNK = {"problem": "junk: remove", "suggestion": "junk: keep only on purpose"}
_MARK_EMPTY = "empty folder"
_MARK_HIDDEN = "hidden file"


def _na(x: Any) -> bool:
    """R ``is.na()`` of one value."""
    if x is None or x is pd.NA:
        return True
    try:
        return bool(x != x)
    except (TypeError, ValueError):
        return False


def _col(df: pd.DataFrame | None, name: str) -> list[Any]:
    """``df[[name]]`` as a list with ``None`` for NA; NAs for a missing column."""
    if df is None:
        return []
    if name not in df.columns:
        return [None] * len(df)
    pos = [j for j, c in enumerate(df.columns) if c == name]
    return [None if _na(v) else v for v in df.iloc[:, pos[0]].tolist()]


# ---------------------------------------------------------------------------
# classification of files that do not belong in a repository
# ---------------------------------------------------------------------------

_OS_FILES = frozenset(
    {
        ".ds_store",
        ".localized",
        "icon\r",
        "thumbs.db",
        "ehthumbs.db",
        "ehthumbs_vista.db",
        "desktop.ini",
        ".directory",
    }
)


def _in_dir(path: str, dir_re: str) -> bool:
    return re.search(rf"(^|/){dir_re}(/|$)", path) is not None


def useless_file_kind(path: Sequence[Any] | str | None) -> list[str | None]:
    """Port of ``.useless_file_kind()``: per path, the kind of file that does not
    belong in a repository (junk), or ``None`` for any other file.

    The list follows GitHub's ignore-file templates and the junk rules of TU/e's
    data managers: operating-system files, version control, editor settings,
    RStudio and Jupyter session files, compiled Python files, lock files of
    office programs, ``.Rhistory``, editor backups and temporary files. Files
    that may matter to someone (``.RData``, knitr caches) are not on it.
    """
    paths = [path] if isinstance(path, str) else list(path or [])
    out: list[str | None] = []
    for raw in paths:
        if _na(raw):
            out.append(None)
            continue
        p = str(raw).replace("\\", "/")
        base = p.rsplit("/", 1)[-1]  # not basename(): R expands a leading "~"
        kind: str | None = None
        if (
            _in_dir(p, "__MACOSX")
            or base.startswith("._")
            or base.lower() in _OS_FILES
            or _in_dir(p, r"\.AppleDouble")
            or _in_dir(p, r"\.Spotlight-V100")
            or _in_dir(p, r"\.Trashes")
            or _in_dir(p, r"\.fseventsd")
            or _in_dir(p, r"\.TemporaryItems")
            or _in_dir(p, r"\$RECYCLE\.BIN")
            or _in_dir(p, r"\.Trash-[0-9]+")
        ):
            kind = "operating system"
        elif _in_dir(p, r"\.git") or _in_dir(p, r"\.svn") or _in_dir(p, r"\.hg"):
            kind = "version control"
        elif _in_dir(p, r"\.idea") or _in_dir(p, r"\.vscode"):
            kind = "editor settings"
        elif _in_dir(p, r"\.Rproj\.user"):
            kind = "RStudio session files"
        elif _in_dir(p, r"\.ipynb_checkpoints"):
            kind = "Jupyter checkpoints"
        elif _in_dir(p, "__pycache__") or re.search(r"\.py[co]$", base):
            kind = "compiled Python files"
        elif base.startswith("~$") or re.search(r"^\.~lock\..*#$", base):
            kind = "open-document lock files"
        elif base == ".Rhistory":
            kind = "R history"
        elif re.search(r"(~|\.bak|\.swp|\.swo)$", base, flags=re.I):
            kind = "editor backups"
        elif re.search(r"\.(tmp|temp)$", base, flags=re.I):
            kind = "temporary files"
        out.append(kind)
    return out


def useless_file_severity(kind: Sequence[str | None]) -> list[str | None]:
    """Port of ``.useless_file_severity()``: ``"problem"`` (remove it) or
    ``"suggestion"`` (version control and editor settings, which can be kept on
    purpose); ``None`` for no kind."""
    return [
        None
        if k is None
        else "suggestion"
        if k in ("version control", "editor settings")
        else "problem"
        for k in kind
    ]


# ---------------------------------------------------------------------------
# icons and labels of the "Classified as" column
# ---------------------------------------------------------------------------


def tree_type_icon(data_type: Any, doc_role: Any) -> str | None:
    """Port of ``.tree_type_icon()``: the icon of the "Classified as" column.

    A readme (a ``doc_role`` sub-kind of documentation) has its own icon.
    ``None`` (NA) for an NA ``data_type``.
    """
    if not _na(doc_role) and doc_role == "readme":
        return _README_ICON
    if _na(data_type):
        return None
    return _TREE_ICONS.get(str(data_type), _UNKNOWN_ICON)


def tree_type_label(data_type: Any, doc_role: Any) -> str | None:
    """Port of ``.tree_type_label()``: ``readme`` for a readme, else the data type."""
    if not _na(doc_role) and doc_role == "readme":
        return "readme"
    return None if _na(data_type) else str(data_type)


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------


def file_tree_rows(paths: Sequence[Any], is_dir: Sequence[bool] | None = None) -> pd.DataFrame:
    """Walk relative paths into tree rows (port of ``.file_tree_rows()``).

    Returns ``text`` (the tree line) and ``leaf_idx`` (the index into *paths* of
    the input a row is the leaf of; ``None`` for a folder row that is not itself
    an input). ``is_dir`` (same length as *paths*, default all ``False``) marks
    paths that are folders: a folder with nothing under it (an empty folder, or
    a junk folder shown as one line) is still drawn and sorted as a folder, with
    a trailing ``/``, and its row's ``leaf_idx`` points at it. Two input rows with
    the same path collapse into one tree row; only the first row's metadata is
    then shown for it.
    """
    dirs = [False] * len(paths) if is_dir is None else [bool(d) for d in is_dir]
    keep = [not _na(p) and str(p) != "" for p in paths]
    orig_idx = [i for i, k in enumerate(keep) if k]
    if not orig_idx:
        return pd.DataFrame(
            {"text": pd.Series([], dtype="string"), "leaf_idx": pd.Series([], dtype="Int64")}
        )
    parts_list = [[s for s in str(paths[i]).replace("\\", "/").split("/") if s] for i in orig_idx]
    dir_list = [dirs[i] for i in orig_idx]
    out_text: list[str] = []
    out_leaf: list[int | None] = []

    def walk(
        parts_subset: list[list[str]], idx_subset: list[int], dir_subset: list[bool], prefix: str
    ) -> None:
        groups: dict[str, list[int]] = {}
        for k, p in enumerate(parts_subset):
            groups.setdefault(p[0], []).append(k)
        # a head is a folder when something lies below it, or when it is itself
        # an input folder (an empty folder, or a junk folder shown as one line)
        is_folder = {
            h: any(len(parts_subset[k]) > 1 or dir_subset[k] for k in ks)
            for h, ks in groups.items()
        }
        head_order = sorted(groups, key=lambda h: (not is_folder[h], r_sort_key(h.lower())))
        for i, head in enumerate(head_order):
            ks = groups[head]
            tails = [parts_subset[k][1:] for k in ks]
            tail_idx = [idx_subset[k] for k in ks]
            tail_dir = [dir_subset[k] for k in ks]
            is_last = i == len(head_order) - 1
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")
            own = [j for j, t in enumerate(tails) if not t]
            out_text.append(prefix + branch + head + ("/" if is_folder[head] else ""))
            out_leaf.append(tail_idx[own[0]] if own else None)
            deeper = [j for j, t in enumerate(tails) if t]
            if deeper:
                walk(
                    [tails[j] for j in deeper],
                    [tail_idx[j] for j in deeper],
                    [tail_dir[j] for j in deeper],
                    next_prefix,
                )

    walk(parts_list, orig_idx, dir_list, "")
    return pd.DataFrame(
        {
            "text": pd.Series(out_text, dtype="string"),
            "leaf_idx": pd.Series(out_leaf, dtype="Int64"),
        }
    )


def _list_files(folder: str) -> list[str]:
    """``list.files(folder, all.files = TRUE, recursive = TRUE, include.dirs = TRUE)``."""
    found: list[str] = []
    for root, dirnames, filenames in os.walk(folder):
        rel = os.path.relpath(root, folder)
        found.extend(
            name if rel == "." else f"{rel}/{name}".replace(os.sep, "/")
            for name in (*dirnames, *filenames)
        )
    return sorted(found, key=r_sort_key)


def _unique(x: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(x))


def file_tree_entries(
    repo: Any, sub: pd.DataFrame | None = None, useless: pd.DataFrame | None = None
) -> pd.DataFrame:
    """The entries of one repository's tree (port of ``.file_tree_entries()``).

    One row per file or folder to draw: ``path`` (relative, ``/``), ``is_dir``,
    ``n_files`` (the files under a junk folder shown as one line, else NA),
    ``empty``, ``type_txt`` (icon and classification), ``study``, ``file_name``
    and ``mark`` (junk, empty folder, hidden file, or ``""``). *sub* is the file
    table's rows of this repository; *useless* is ``repo_check``'s
    ``useless_files`` rows of this repository (or ``None``).
    """
    table_paths: list[str | None] = _col(sub, "file_path")
    names = _col(sub, "file_name")
    table_paths = [
        (names[i] if p is None or p == "" else p) if names else p for i, p in enumerate(table_paths)
    ]
    table_paths = [None if p is None else str(p).replace("\\", "/") for p in table_paths]
    known = [p for p in table_paths if p is not None]

    if not _na(repo) and str(repo) != "" and os.path.isdir(str(repo)):
        # a local folder: list everything that is there, plus the files of the
        # table that are not on disk (inside zip archives)
        listed = _list_files(str(repo))
        on_disk = set(listed)
        path = [*listed, *(p for p in _unique(known) if p not in on_disk)]
        is_dir = [os.path.isdir(os.path.join(str(repo), p)) for p in listed]
        is_dir += [False] * (len(path) - len(listed))
    else:
        # a repository link: the table's files plus repo_check's junk files
        junk = [str(p).replace("\\", "/") for p in _col(useless, "file_path") if p is not None]
        path = _unique([*known, *junk])
        is_dir = [False] * len(path)
    junk_kind = useless_file_kind(path)

    # A junk folder is shown as one line: the shallowest folder of a junk path
    # that is itself junk (".git" for ".git/objects/ab/cd"), with the number of
    # files under it; nothing below it is drawn.
    junk_root: list[str | None] = []
    for i, p in enumerate(path):
        root = None
        if junk_kind[i] is not None:
            seg = p.split("/")
            for k in range(1, len(seg)):
                prefix = "/".join(seg[:k])
                if useless_file_kind(prefix)[0] is not None:
                    root = prefix
                    break
        junk_root.append(root)
    in_junk_dir = [r is not None for r in junk_root]
    # a folder that is itself junk counts as its own root
    own_root = [
        is_dir[i] and junk_kind[i] is not None and not in_junk_dir[i] for i in range(len(path))
    ]
    roots = _unique(
        [r for r in junk_root if r is not None] + [path[i] for i, o in enumerate(own_root) if o]
    )
    files_on_list = [p for i, p in enumerate(path) if not is_dir[i]]
    n_files = {r: sum(1 for p in files_on_list if p.startswith(r + "/")) for r in roots}

    keep = [i for i in range(len(path)) if not in_junk_dir[i] and not own_root[i]]
    e_path = [path[i] for i in keep] + roots
    e_dir = [is_dir[i] for i in keep] + [True] * len(roots)
    e_n: list[int | None] = [None] * len(keep) + [n_files[r] for r in roots]
    kind = useless_file_kind(e_path)
    sev = useless_file_severity(kind)

    # empty folders: folders with no file anywhere below them
    empty = [
        e_dir[i]
        and e_n[i] is None
        and not any(f.startswith(e_path[i] + "/") for f in files_on_list)
        for i in range(len(e_path))
    ]

    # what is shown after each name
    first: dict[str, int] = {}
    for j, p in enumerate(table_paths):
        if p is not None and p not in first:
            first[p] = j
    hit = [first.get(p) for p in e_path]
    types, roles = _col(sub, "data_type"), _col(sub, "doc_role")
    groups, fnames = _col(sub, "group"), _col(sub, "file_name")

    def at(values: list[Any], h: int | None) -> Any:
        return values[h] if h is not None and values else None

    type_txt: list[str] = []
    study: list[str] = []
    file_name: list[Any] = []
    mark: list[str] = []
    for i, p in enumerate(e_path):
        h = hit[i]
        if h is None or e_dir[i]:
            type_txt.append("")
        else:
            icon = tree_type_icon(at(types, h), at(roles, h))
            label = tree_type_label(at(types, h), at(roles, h))
            type_txt.append(f"{'NA' if icon is None else icon} {'NA' if label is None else label}")
        group = at(groups, h)
        study.append("" if h is None or group is None else str(group))
        file_name.append(at(fnames, h))
        jk, n = kind[i], e_n[i]
        if jk is not None and n is not None:
            mark.append(f"{_MARK_JUNK[str(sev[i])]} ({n:,} file{'' if n == 1 else 's'})")
        elif jk is not None:
            mark.append(_MARK_JUNK[str(sev[i])])
        elif empty[i]:
            mark.append(_MARK_EMPTY)
        elif re.search(r"(^|/)\.", p) and not e_dir[i] and type_txt[i] == "":
            mark.append(_MARK_HIDDEN)
        else:
            mark.append("")
    return pd.DataFrame(
        {
            "path": pd.Series(e_path, dtype="string"),
            "is_dir": e_dir,
            "n_files": pd.Series(e_n, dtype="Int64"),
            "empty": empty,
            "type_txt": pd.Series(type_txt, dtype="string"),
            "study": pd.Series(study, dtype="string"),
            "file_name": pd.Series(file_name, dtype="string"),
            "mark": pd.Series(mark, dtype="string"),
        }
    )


_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def file_tree_block(
    files: pd.DataFrame | None,
    naming_issues: pd.DataFrame | None = None,
    useless_files: pd.DataFrame | None = None,
) -> list[str] | None:
    """The file tree as report text (port of ``.file_tree_block()``).

    ``#### Data Tree``, a legend, and per repository a preformatted block in a
    scroll box, with each file's classification, study and naming issue, or a
    mark (junk, hidden file, empty folder). *files* is a file table with
    ``repo_url`` and ``file_path`` (or ``file_name``), and optionally
    ``data_type``, ``doc_role`` and ``group``, as ``data_check`` and
    ``repo_check`` give it; *naming_issues* is ``repo_check``'s ``naming_issues``
    table; *useless_files* is ``repo_check``'s ``useless_files`` table. ``None``
    when there is nothing to draw.
    """
    from metacheck.modules._data_check import _pad  # the width R's format() pads to

    has_files = files is not None and "repo_url" in files.columns
    has_useless = useless_files is not None and "repo_url" in useless_files.columns
    f_repo = _col(files, "repo_url") if has_files else []
    u_repo = _col(useless_files, "repo_url") if has_useless else []
    repo_urls = _unique([r for r in (*f_repo, *u_repo) if r is not None and str(r) != ""])
    if not repo_urls:
        return None

    # naming detail by file name, the first match only (a name flagged by several
    # rules shows its first; the full list remains in repo_check's own table)
    ni_names, ni_detail = _col(naming_issues, "file_name"), _col(naming_issues, "detail")
    first_issue: dict[Any, int] = {}
    for k, nm in enumerate(ni_names):
        if nm is not None and nm not in first_issue:
            first_issue[nm] = k

    def naming_of(file_name: Any) -> str:
        hit = first_issue.get(file_name) if file_name is not None else None
        detail = None if hit is None or not ni_detail else ni_detail[hit]
        return "" if detail is None else str(detail)

    blocks: list[dict[str, Any]] = []
    for repo in repo_urls:
        sub = files[[r == repo for r in f_repo]] if has_files and files is not None else None
        useless = (
            useless_files[[r == repo for r in u_repo]]
            if has_useless and useless_files is not None
            else None
        )
        entries = file_tree_entries(repo, sub, useless)
        if len(entries) == 0:
            continue
        rows = file_tree_rows(entries["path"].tolist(), entries["is_dir"].tolist())
        if len(rows) == 0:
            continue
        # A tree has to READ as a tree: every line in order, with the branch
        # characters lining up. So the rows are laid out as text with the
        # per-file columns padded to align.
        marks = entries["mark"].tolist()
        types = entries["type_txt"].tolist()
        studies = entries["study"].tolist()
        fnames = [None if _na(v) else v for v in entries["file_name"].tolist()]
        leaves = [None if _na(v) else int(v) for v in rows["leaf_idx"].tolist()]
        first_col, study_txt, naming_txt = [], [], []
        for leaf in leaves:
            if leaf is None:
                first_col.append("")
                study_txt.append("")
                naming_txt.append("")
                continue
            first_col.append(marks[leaf] if marks[leaf] != "" else types[leaf])
            study_txt.append(studies[leaf])
            naming_txt.append(naming_of(fnames[leaf]))
        has_study = any(s != "" for s in study_txt)
        has_naming = any(s != "" for s in naming_txt)
        texts = [str(t) for t in rows["text"].tolist()]
        p_text, p_first = _pad(texts), _pad(first_col)
        p_study = _pad(study_txt) if has_study else [""] * len(texts)
        lines = [
            re.sub(r"\s+$", "", a + b + c + (d if has_naming else ""))
            for a, b, c, d in zip(p_text, p_first, p_study, naming_txt, strict=True)
        ]
        blocks.append(
            {"repo": str(repo), "lines": lines, "has_study": has_study, "has_naming": has_naming}
        )
    if not blocks:
        return None

    sections: list[str] = []
    for b in blocks:
        # a local folder by its own name: its full path is the place on the
        # computer that ran the check, on a server its job folder
        label = b["repo"] if _URL.match(b["repo"]) else os.path.basename(b["repo"].rstrip("/"))
        sections.append(f"**Repository: {label}**")
        # a fenced block with no language, in a box of about 30 lines with its
        # own scroll bar; one element, so the report's blank-line joins do not
        # put an empty row between the lines of the tree
        sections.append(
            "\n".join(
                [
                    '::: {style="max-height: 40em; overflow: auto;"}',
                    "```",
                    *b["lines"],
                    "```",
                    ":::",
                ]
            )
        )
    legend = (
        "Each file is followed by how it was classified"
        + (", which study it belongs to" if any(b["has_study"] for b in blocks) else "")
        + (
            ", and any file-naming issue found by repo_check"
            if any(b["has_naming"] for b in blocks)
            else ""
        )
        + "; junk files, hidden files and empty folders are marked."
    )
    return ["#### Data Tree", f"Where the files sit within each repository. {legend}", *sections]


# ---------------------------------------------------------------------------
# the tree inside a report
# ---------------------------------------------------------------------------


def _report_items(report: Any) -> list[Any]:
    """A report as its list of blocks (text, or table blocks, which are kept as they are)."""
    if report is None:
        return []
    if isinstance(report, str):
        return [report]
    return list(report)


def file_tree_report_range(report: Any) -> tuple[int, int] | None:
    """Where the ``#### Data Tree`` part of a report (``data_check``'s) starts and
    ends (port of ``.file_tree_report_range()``): the index of its heading and of
    its last element. ``None`` when the report has no tree."""
    items = _report_items(report)
    starts = [i for i, r in enumerate(items) if isinstance(r, str) and r == "#### Data Tree"]
    if not starts:
        return None
    start = starts[0]
    after = [
        i
        for i, r in enumerate(items)
        if i > start and isinstance(r, str) and re.match(r"^#{1,4} ", r)
    ]
    return start, (after[0] - 1 if after else len(items) - 1)


def file_tree_from_report(report: Any) -> list[Any] | None:
    """The ``#### Data Tree`` part of a report, without its heading (port of
    ``.file_tree_from_report()``). ``None`` when the report has no tree."""
    span = file_tree_report_range(report)
    if span is None or span[1] <= span[0]:
        return None
    return _report_items(report)[span[0] + 1 : span[1] + 1]


def file_tree_drop_from_report(report: Any) -> Any:
    """A report without its ``#### Data Tree`` part, heading included (port of
    ``.file_tree_drop_from_report()``): the checklist report shows the tree in a
    section of its own."""
    span = file_tree_report_range(report)
    if span is None:
        return report
    items = _report_items(report)
    return items[: span[0]] + items[span[1] + 1 :]


def _output_of(module_output: Any, name: str) -> Any:
    """The output of module *name* in a mapping or a list of module outputs."""
    if isinstance(module_output, Mapping):
        return module_output.get(name)
    if isinstance(module_output, Sequence) and not isinstance(module_output, str):
        for mo in module_output:
            if getattr(mo, "module", None) == name:
                return mo
    return None


def _field(mo: Any, name: str) -> Any:
    if mo is None:
        return None
    if isinstance(mo, Mapping):
        return mo.get(name)
    getter = getattr(mo, "get", None)
    return getter(name) if callable(getter) else getattr(mo, name, None)


def file_tree(module_output: Any) -> list[Any] | None:
    """The file tree of a run, without its heading (port of ``.file_tree()``).

    ``data_check``'s (which also lists files inside zip archives and each file's
    study), else one drawn from ``repo_check``'s file table and junk files, since
    ``data_check`` reports nothing at all when a repository has no data file it
    can read. *module_output* is a mapping of module outputs named by module (as
    :func:`~metacheck.report.report.report_module_run` returns), or a list of
    them. ``None`` when neither module gives a tree.
    """
    tree = file_tree_from_report(_field(_output_of(module_output, "data_check"), "report"))
    if tree is not None:
        return tree
    repo = _output_of(module_output, "repo_check")
    block = file_tree_block(
        _field(repo, "table"), _field(repo, "naming_issues"), _field(repo, "useless_files")
    )
    return None if block is None else block[1:]


def brief_file_tree(module_output: Any, table: Any = None) -> str | None:
    """The file tree as an open callout, or ``None`` when there is no tree (port of
    ``.brief_file_tree()`` of the repository apps' ``brief_report.R``).

    The tree sits in a scroll box, so a large repository does not make the report
    endless. ``table`` turns a table block of the tree into text (default: a static
    table).
    """
    body = file_tree(module_output)
    if body is None:
        return None
    if table is None:
        from metacheck.report.render import static_table_chunk

        table = static_table_chunk
    from metacheck.report.blocks import ReportTable

    parts = [table(b) if isinstance(b, ReportTable) else str(b) for b in body]
    return (
        '::: {.callout-note title="Files in the repository"}\n\n' + "\n\n".join(parts) + "\n\n:::"
    )


def brief_insert_after_summary(report_text: str, section: str) -> str:
    """Put *section* right after a brief report's summary (port of
    ``.brief_insert_after_summary()``).

    A brief report follows its summary with "## Validation" (when any check was
    flagged) and otherwise with the feedback sentence, so the section goes before
    whichever comes first, and at the end if neither is found.
    """
    for marker in ("\n## Validation\n", "\nIf there are things we can improve, please email us"):
        pos = report_text.find(marker)
        if pos >= 0:
            return f"{report_text[: pos + 1]}{section}\n{report_text[pos:]}"
    return f"{report_text}\n\n{section}"
