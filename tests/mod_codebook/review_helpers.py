"""Python side of the codebook_check review parity cases.

``parity/cases/mod_codebook_review.yaml`` (written by
``tests/mod_codebook/make_review_cases.py``) builds every input with an
``rv_*()`` function here and with its twin in ``tests/mod_codebook/review_helpers.R``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd


def _s(values: Sequence[Any]) -> pd.Series:
    return pd.Series(list(values), dtype="string")


def rv_osd_attrs(osds: Sequence[Any]) -> list[dict[str, Any]]:
    """The attributes R keeps on each OSD object (not part of the canonical value)."""
    keys = ("write", "code", "orphan_total", "scale", "dedup_key")
    return [{k: o.attrs.get(k) for k in keys} for o in osds]


def rv_named_frame(nms: Sequence[str], values: Sequence[float] = (1, 2, 3, 4, 5)) -> pd.DataFrame:
    """A data frame whose column names may repeat (or be empty)."""
    rows = [[float(v)] * len(nms) for v in values]
    return pd.DataFrame(rows, columns=list(nms))


def rv_propagate_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv"] * 6 + ["t.csv"] * 2),
            "column_name": _s(["1", "2", "_3", "q_1", "q_2", "__", "q_1", "q_9"]),
            "scale": _s(["S", None, "", "Q", None, None, None, "T"]),
            "scale_confidence": _s(["high", "low", "x", "medium", None, "z", None, "low"]),
            "scale_source": _s(
                ["manuscript", None, "k", "matched", None, None, None, "self_generated"]
            ),
        }
    )


def rv_dup_names() -> list[str]:
    return [
        "b", "a", "b", "a", "B", "A", "B", "A", "a", "_x", "_x", "Z", "Z",
        "é", "é", "e", "e", "solo",
    ]  # fmt: skip


def rv_text_scales(
    cols: Sequence[str] = ("scale_name", "acronym", "n_items", "administered", "confidence"),
) -> pd.DataFrame:
    ts = pd.DataFrame(
        {
            "scale_name": _s(
                [
                    "Grit Scale",
                    None,
                    "  ",
                    "Big Five Inventory",
                    "Life Orientation Test",
                    "Short Scale",
                    "grit scale",
                ]
            ),
            "acronym": _s([None, "X", "Y", "BFI", "", "LOT", " GS "]),
            "n_items": _s(["12", None, "", " 5 ", "", None, "8"]),
            "administered": _s(["unclear", "yes", None, "Unclear ", "no", "UNCLEAR", "unclear"]),
            "confidence": _s(["high", "low", "low", "medium", "high", None, "low"]),
        }
    )
    return ts.loc[:, list(cols)]


def rv_split_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x_1": _s(["1", "2", "NA", "abc", "3"]),
            "x_2": pd.Series([1.0, float("inf"), 3.0, 4.0, 2.0], dtype="float64"),
            "x_3": pd.Series([None] * 5, dtype="boolean"),
            "x_4": pd.Series([1.5, 2.0, 3.0, 4.0, 2.0], dtype="float64"),
            "x_total": pd.Series([10.0, 20.0, 30.0, 40.0, 25.0], dtype="float64"),
        }
    )


def rv_prefix_names() -> list[str]:
    return [
        "StartDate", "bfi_1", "bfi_2", "Q1_DO_x", "bfi_3", "bfi_1", "",
        "émo_1", "émo_2", "émo_3", "ab_1", "ab_2", "ab_3", "",
        "zz_1", "zz_2", "zz_3", "Q9_1", "Q9_2", "Q9_3", "Q9_TEXT",
    ]  # fmt: skip


def rv_likert_labels() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv"] * 5),
            "column_name": _s(["a1", "a2", "b1", "b2", "c1"]),
            "value_labels": _s(
                [
                    '["x","y","z"]',
                    '{"1.5":"one","2":"two"," 3":"three","1e1":"ten","-99":"Refused","x":"bad"}',
                    '{"1":"only","-9":"Missing"}',
                    '{"1":"one","2":"two"}',
                    None,
                ]
            ),
            "missing_values": _s([None, '{"-99":"Refused"}', '{"-9":"Missing"}', None, None]),
        }
    )


def rv_likert_columns() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv"] * 6),
            "column_name": _s(["c1", "c1", "c2", "c3", "d1", "d2"]),
            "min": pd.Series([1.0, 2.0, float("nan"), 1.0, 0.5, 1.0], dtype="float64"),
            "max": pd.Series([7.0, 7.0, 7.0, float("nan"), 3.0, 3.0], dtype="float64"),
        }
    )


def rv_synonyms() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["a.csv"] * 4 + ["b.csv"] * 3 + ["c.csv"]),
            "column_name": _s([f"v{i}" for i in range(1, 9)]),
            "scale": _s(
                [
                    "trust in science",
                    "science trust ratings",
                    "trust",
                    "Other thing",
                    "alpha beta",
                    "beta alpha",
                    "the scale",
                    "solo label",
                ]
            ),
            "confidence": _s(["medium"] * 8),
            "scale_source": _s(["self_generated"] * 8),
        }
    )


def rv_osd_groups() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv", "s.csv", "s.csv", "s.csv", "t.csv", "s.csv", "t.csv"]),
            "prefix": _s(["AQ", "AQT", "ZZ", "YY", "AQ", "OT", "ZZ"]),
            "scale": _s(
                [
                    "Autism Quotient",
                    "Autism Quotient",
                    None,
                    "Made Up",
                    "Autism Quotient",
                    "Orphan Total",
                    None,
                ]
            ),
            "confidence": _s(["high", "medium", None, "low", "high", "medium", None]),
            "scale_source": _s(
                ["manuscript", "manuscript", None, "self_generated", "manuscript", "matched", None]
            ),
            "n_columns": pd.Series([3, 3, 3, 2, 3, 3, 3], dtype="Int64"),
            "max_item": pd.Series([3, None, 3, 2, 3, None, 3], dtype="Int64"),
            "totals_only": pd.Series(
                [False, True, False, False, False, True, False], dtype="boolean"
            ),
            "columns": pd.Series(
                [
                    ["AQ1", "AQ2", "AQ3"],
                    ["AQT_a", "AQT_b", "AQT_c"],
                    ["ZZ1", "ZZ2", "ZZ3"],
                    ["YY1", "YY2"],
                    ["AQ3", "AQ1", "AQ2"],
                    ["OT_a", "OT_b", "OT_c"],
                    ["ZZ1", "ZZ2", "ZZ3"],
                ],
                dtype=object,
            ),
        }
    )


def rv_osd_columns() -> pd.DataFrame:
    cols = ["AQ1", "AQ2", "AQ3", "ZZ1", "ZZ2", "ZZ3", "YY1", "YY2"]
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv"] * 8 + ["t.csv"] * 3),
            "column_name": _s([*cols, "ZZ1", "ZZ2", "ZZ3"]),
            "min": pd.Series([1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1], dtype="float64"),
            "max": pd.Series([5, 5, 5, 7, 7, 7, 100, 100, 7, 7, 7], dtype="float64"),
        }
    )


def rv_osd_labels() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_file": _s(["s.csv", "s.csv", "s.csv", "s.csv", "t.csv"]),
            "column_name": _s(["AQ1", "AQ2", "ZZ1", "YY1", "AQ1"]),
            "label": _s(["I like crowds", None, "", "Slider one", "I like crowds"]),
            "question": _s([None, "Do you notice patterns?", "Zed one?", None, None]),
            "value_labels": _s(['{"1":"No","2":"Yes"}', None, None, None, None]),
        }
    )


def rv_scales_to_osd() -> dict[str, Any]:
    """The osd objects and their attributes, for ``_scales_to_osd()`` of :func:`rv_osd_groups`."""
    from pytacheck.modules._codebook import _scales_to_osd

    osds = _scales_to_osd(rv_osd_groups(), rv_osd_columns(), rv_osd_labels())
    return {"osd": osds, "attrs": rv_osd_attrs(osds)}


def rv_run_osd_attrs(mo: Any) -> list[dict[str, Any]]:
    """A module run's ``scales_osd`` attributes."""
    return rv_osd_attrs(mo["scales_osd"])


def rv_haven_labels(f: str) -> Any:
    """The module's embedded-label harvest of one labelled data file (read labels-only)."""
    import os

    from pytacheck.datacheck.columns import _extract_haven_labels
    from pytacheck.modules.codebook_check import _haven_labels_frame

    ext = f.rsplit(".", 1)[1].lower()
    return _extract_haven_labels(_haven_labels_frame(f, ext), os.path.basename(f), group="g1")


def rv_text_paper() -> Any:
    import pytacheck as pc

    p = pc.test_paper(
        [
            "We used the BFI-2 questionnaire to measure personality.",
            "Participants felt enthusiastic and determined during the task.",
            "The q.x variable was coded by two raters.",
            "An unrelated sentence about the weather.",
            "ITEM responses were recorded on a scale from 1 to 5.",
            "The Perceived Stress Scale (PSS) contains 10 items.",
            "Enthusiastic participants were more determined.",
        ]
    )
    p.paper_id = "tp"
    return p
