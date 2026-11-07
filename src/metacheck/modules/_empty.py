"""Empty input for the text and reference modules (``.paper_is_empty()``, ``.empty_input_result()``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.papers.model import is_paper, is_paper_list
from metacheck.papers.tables import paper_id, paper_table


def paper_is_empty(paper: Any) -> bool:
    """``.paper_is_empty()``: a paper or paper list with no text and no references.

    Inputs that are not papers are never empty here.
    """
    if not (is_paper(paper) or is_paper_list(paper)):
        return False
    return len(paper_table(paper, "text")) == 0 and len(paper_table(paper, "bib")) == 0


def per_paper_table(paper: Any, **columns: Any) -> pd.DataFrame:
    """``.per_paper_table()``: one row per paper, the same value in every row of each column.

    An empty paper or paper list gives a table with no rows (and the named columns).
    """
    ids = list(paper_id(paper))
    out = pd.DataFrame({"paper_id": pd.Series(ids, dtype="string")})
    for name, value in columns.items():
        out[name] = [value] * len(ids)
    return out


def empty_input_result(paper: Any) -> dict[str, Any]:
    """``.empty_input_result()``: the result of a text or reference module for an empty input."""
    return {
        "traffic_light": "na",
        "summary_text": "There was no text to check.",
        "summary_table": per_paper_table(paper),
    }
