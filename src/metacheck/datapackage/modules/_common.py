"""Helpers shared by the datapackage modules."""

from __future__ import annotations

from typing import Any

#: Severity of one finding, worst first.
SEVERITIES = ("problem", "suggestion", "info")

NO_PACKAGE = "No data package given (pass `local_path`, the package's folder or archive)."


def paper_ids(paper: Any) -> list[str]:
    """The ids of the paper or papers a run is for (none for ``None``)."""
    if paper is None:
        return []
    from metacheck.papers.tables import paper_id

    try:
        return [str(p) for p in paper_id(paper)]
    except Exception:
        return []


def no_package() -> dict[str, Any]:
    """A module's output when no package was given."""
    return {"traffic_light": "na", "summary_text": NO_PACKAGE, "report": NO_PACKAGE}


def summary_table(paper: Any, **columns: Any) -> Any:
    """One row per paper with *columns* (each a single value, the same for every paper).

    Without a paper (``None`` or an empty stand-in) the table has no rows.
    """
    import pandas as pd

    ids = paper_ids(paper)
    data: dict[str, Any] = {"paper_id": pd.Series(ids, dtype="string")}
    for name, value in columns.items():
        data[name] = [value] * len(ids)
    return pd.DataFrame(data)
