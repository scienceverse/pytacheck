"""Python side of the mod_ethics parity helpers (``tests/mod_ethics/helpers.R``).

``test_paper()`` gives every paper a time-based ID, so paper lists in the
parity cases are built with fixed IDs on both sides.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def ec_paper(
    text: Sequence[str | None], id: str = "p1", section_type: Sequence[str] | None = None
) -> Any:
    """``ec_paper()``: a test paper with a fixed ID (and optionally one section per sentence)."""
    import pandas as pd

    import pytacheck as pc

    p = pc.test_paper(list(text))
    p.paper_id = id
    if section_type is not None:
        n = len(text)
        ids = [float(i) for i in range(n)]
        text_table = p.text.copy()
        text_table["section_id"] = ids
        p.text = text_table
        p.section = pd.DataFrame(
            {
                "section_id": pd.Series(ids, dtype="float64"),
                "header": pd.Series([f"Section {i + 1}" for i in range(n)], dtype="string"),
                "parent_section_id": pd.Series([None] * n, dtype="Int64"),
                "section_type": pd.Series(list(section_type), dtype="string"),
                "classification_score": pd.Series([0.0] * n, dtype="float64"),
            }
        )
    return p


def ec_papers(ids: Sequence[str], texts: Sequence[Sequence[str | None]]) -> Any:
    """``ec_papers()``: a paper list of test papers with the given IDs."""
    import pytacheck as pc

    return pc.PaperList([ec_paper(t, i) for i, t in zip(ids, texts, strict=True)])
