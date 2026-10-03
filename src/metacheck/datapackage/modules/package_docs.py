"""Data Package Documentation: The README of a data package and whether the package has the parts it should have."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Documentation",
    description="The README of a data package and whether the package has the parts it should have.",
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
def package_docs(paper: Any, local_path: Any = None) -> dict[str, Any]:
    """The README of a data package and whether the package has the parts it should have."""
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
