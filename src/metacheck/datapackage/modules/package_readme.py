"""Data Package README Draft: A README for a data package, filled in by rules, as a file to save."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package README Draft",
    description="A README for a data package, filled in by rules from what the checks know, handed back as a file.",
    details="""
        Drafts a README for a data package and hands it back as a file (`README.md`), without an LLM and without changing the package.
        Run it by name (`-m datapackage::package_readme`); it is not part of the `datapackage::default` preset, because it makes a file rather than a verdict.

        * **Your own text stays.** If the package has a README, the draft is that text, unchanged. A section it lacks is added at the end, in the order of the README template, in the heading style the README already uses; a heading with nothing under it gets its text inserted. Nothing you wrote is removed or rewritten.
        * **What rules can say is filled in:** the list of files (a folder tree and one line for each top-level folder and file), the file formats with their counts, the file naming convention when one is evident, the variables of the data files (name, type and number of empty cells), and the licence (a LICENSE file, or one the README names).
        * **What only a person knows is a placeholder**, in the syntax the README check (`package_docs`) already flags (`<Describe ...>`), so running that check on the completed draft lists every gap that is left.
        * **No cell value is in the draft.** The variable tables have names, types and counts only, and a column name that looks like a value (a name, a number, a date) is shown as "column N", as in `package_pii`.

        The sections and their headings come from the README template, the same `readme` as in `package_docs`: a dict or the path to a JSON file (default: a generic template). A section's `prompt` says what its placeholder asks for and `fill` (`files`, `variables` or `licence`) names the part of the package that fills it.

        The draft is in the result's `files` (`{"README.md": text}`): `check_package(...).files` in Python, and `metacheck package --files-dir DIR` writes it to a folder. It is never written into the package. The module's table has one row for each section of the template (was it in the README, and what the draft did with it), and `variables` has one row for each variable that the draft lists.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
        "readme": "the README template: a dict, or the path to a JSON file (default: a generic template)",
        "max_rows": "the most rows read from each data file or sheet, to count its empty cells (default 100000)",
        "max_file_size": "data files bigger than this, in MB, are not read for their variables (default 50)",
        "max_files": "the most data files read for their variables (default 30)",
    },
)
def package_readme(
    paper: Any,
    local_path: Any = None,
    readme: Any = None,
    max_rows: int = 100_000,
    max_file_size: float = 50,
    max_files: int = 30,
) -> dict[str, Any]:
    """A README for a data package, filled in by rules, as a file to save."""
    from metacheck.datapackage import package_for
    from metacheck.datapackage.readme import draft_readme, readme_report, summary_line

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    draft = draft_readme(
        pkg,
        readme=readme,
        max_rows=int(max_rows),
        max_file_size=float(max_file_size),
        max_files=int(max_files),
    )
    counts = draft.counts
    return {
        "table": draft.sections,
        "summary_table": summary_table(
            paper,
            readme=draft.readme_path or "",
            sections_kept=counts["kept"],
            sections_from_package=counts["from_package"],
            sections_placeholder=counts["placeholders"],
            placeholders=draft.placeholders,
        ),
        "traffic_light": "info",
        "summary_text": summary_line(draft),
        "report": readme_report(draft),
        "files": draft.files,
        "readme_path": draft.readme_path,
        "readme_text": draft.text,
        "variables": draft.variables,
        "placeholders": draft.placeholders,
        "notes": draft.notes,
    }
