"""Data Package Files: Junk and temporary files, file formats and file and folder names in a data package."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Files",
    description="Junk and temporary files, file formats and file and folder names in a data package.",
    details="""
        Not implemented yet: lists nothing.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
    },
)
def package_files(paper: Any, local_path: Any = None) -> dict[str, Any]:
    """Junk and temporary files, file formats and file and folder names in a data package."""
    from metacheck.datapackage import package_for

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    files = pkg.files()
    return {
        "table": files,
        "summary_table": summary_table(paper, files=len(files)),
        "traffic_light": "info",
        "summary_text": f"{len(files)} files",
    }
