"""Data Package Structure: The folder tree of a data package, its depth and whether it follows a recognised layout."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Structure",
    description="The folder tree of a data package, its depth and whether it follows a recognised layout.",
    details="""
        Looks at how the package is organised, not at what the files contain.
        The checklist has four items:

        - **Folder structure snapshot**: a text tree of the folders and files, with sizes
          (big folders are shortened, files next to an archive's wrapper folder are listed
          separately).
        - **No more than N folder levels** (`max_depth`, 3 by default): levels are counted
          from the package's top folder, so `4 Data/Raw data/x.csv` is two levels. Each
          branch that goes deeper is reported once, at its deepest folder.
        - **Organised in a recognised layout**: the top-level folders are compared with a
          list of layouts (`layouts`; by default data/code/documentation, Psych-DS style
          and one folder per figure). Numbers in front of a folder name and differences in
          capitals, spaces, `-` and `_` are ignored. A layout matches when all its required
          parts are present and at least `min_coverage` of the top-level folders belong to
          one of its parts. A small package without folders (at most `flat_max_files`
          files) needs no layout.
        - **No empty folders**.

        `layouts` is a layout spec (a dict), a list of them or the path to a JSON file;
        `severity` maps a finding's rule id (`too-deep`, `layout-not-recognised`,
        `layout-part-missing`, `layout-folder-unmatched`, `empty-folder`, ...) to
        `problem`, `suggestion` or `info`. The output has the findings (`table`), the
        `checklist`, the text `tree`, the folder table `dirs` and `parts`, the folders and
        files that belong to a part of the layout.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
        "max_depth": "the most folder levels a package should have, counted from its top folder (default 3)",
        "layouts": "the layouts to compare with: a layout dict, a list of them or a path to a JSON file (default: the built-in layouts)",
        "min_coverage": "the share of the top-level folders that must belong to a layout for it to match (default 0.6)",
        "flat_max_files": "a package with no folders and at most this many files needs no layout (default 10)",
        "severity": "a dict (or the path to a JSON file) of rule id to severity: problem, suggestion or info",
    },
)
def package_structure(
    paper: Any,
    local_path: Any = None,
    max_depth: int = 3,
    layouts: Any = None,
    min_coverage: float = 0.6,
    flat_max_files: int = 10,
    severity: Any = None,
) -> dict[str, Any]:
    """The folder tree of a data package, its depth and whether it follows a recognised layout."""
    from metacheck.datapackage import package_for
    from metacheck.datapackage.structure import check_structure, structure_report

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    res = check_structure(
        pkg,
        max_depth=max_depth,
        layouts=layouts,
        min_coverage=min_coverage,
        flat_max_files=flat_max_files,
        severity=severity,
    )
    issues = res.findings["severity"].isin(["problem", "suggestion"])
    return {
        "table": res.findings,
        "summary_table": summary_table(
            paper,
            files=res.n_files,
            folders=res.n_folders,
            folder_depth=res.deepest,
            layout=res.layout_id,
            issues=int(issues.sum()),
        ),
        "traffic_light": res.traffic_light,
        "summary_text": res.summary_text,
        "report": structure_report(res),
        "checklist": res.checklist,
        "tree": res.tree,
        "dirs": res.dirs,
        "parts": res.parts,
    }
