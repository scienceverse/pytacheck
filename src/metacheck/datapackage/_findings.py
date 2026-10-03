"""The two tables every package check returns: findings and a checklist.

**Findings** are the details, one row per thing to fix or look at:
:data:`FINDING_COLUMNS`. ``severity`` is ``"problem"`` (should be fixed before
the package is archived), ``"suggestion"`` (would make it better) or
``"info"`` (worth knowing).

**The checklist** is the overview a data steward works down, one row per
requirement: :data:`CHECKLIST_COLUMNS`. ``status`` is ``"fail"``, ``"warn"``,
``"pass"``, ``"manual"`` (a person has to decide, for example whether the
research involved people) or ``"na"`` (does not apply). Item ids are stable,
so a pack can collect the rows of several checks into one list in its own
order.

A module's traffic light follows from its checklist (:func:`traffic_light`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "CHECKLIST_COLUMNS",
    "FINDING_COLUMNS",
    "SEVERITIES",
    "STATUSES",
    "checklist_frame",
    "findings_frame",
    "status_from_findings",
    "traffic_light",
]

FINDING_COLUMNS = (
    "path",  # the file or folder, relative to the package's base ("" for the package)
    "kind",  # "file", "folder" or "package"
    "check",  # the checklist item it belongs to, e.g. "junk_files"
    "rule",  # what exactly, e.g. "macos-metadata"
    "severity",  # "problem", "suggestion" or "info"
    "detail",  # a plain-language sentence for the report
)

SEVERITIES = ("problem", "suggestion", "info")

CHECKLIST_COLUMNS = (
    "item",  # stable id, e.g. "junk_files"
    "title",  # short label, e.g. "No junk or temporary files"
    "status",  # "fail", "warn", "pass", "manual" or "na"
    "detail",  # one sentence: what was found
)

STATUSES = ("fail", "warn", "manual", "pass", "na")


def findings_frame(rows: Iterable[Mapping[str, Any]] = ()) -> Any:
    """Findings as a table with :data:`FINDING_COLUMNS` (all text)."""
    import pandas as pd

    rows = list(rows)
    for row in rows:
        if row.get("severity") not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, not {row.get('severity')!r}")
    return pd.DataFrame(
        {c: pd.Series([row.get(c, "") for row in rows], dtype="string") for c in FINDING_COLUMNS}
    )


def checklist_frame(rows: Iterable[Mapping[str, Any]] = ()) -> Any:
    """Checklist rows as a table with :data:`CHECKLIST_COLUMNS` (all text).

    Other keys of the rows (a pack's ``part``, say) become columns after them.
    """
    import pandas as pd

    rows = list(rows)
    for row in rows:
        if row.get("status") not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, not {row.get('status')!r}")
    extra = dict.fromkeys(k for row in rows for k in row if k not in CHECKLIST_COLUMNS)
    return pd.DataFrame(
        {
            c: pd.Series([row.get(c, "") for row in rows], dtype="string")
            for c in (*CHECKLIST_COLUMNS, *extra)
        }
    )


def status_from_findings(
    findings: Any, check: str, *, problem: str = "fail", suggestion: str = "warn"
) -> str:
    """A checklist status from the findings of one *check*.

    ``problem`` and ``suggestion`` say what a finding of that severity makes
    the item; ``"info"`` findings leave it ``"pass"``.
    """
    severities = set(findings.loc[findings["check"] == check, "severity"])
    if "problem" in severities:
        return problem
    if "suggestion" in severities:
        return suggestion
    return "pass"


def traffic_light(checklist: Any) -> str:
    """``red`` for any fail, ``yellow`` for any warn or manual, ``green``, or ``na``."""
    statuses = set(checklist["status"]) if len(checklist) else set()
    if "fail" in statuses:
        return "red"
    if statuses & {"warn", "manual"}:
        return "yellow"
    if "pass" in statuses:
        return "green"
    return "na"
