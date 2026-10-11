"""Tests for author_check, a pytacheck module (metacheck has no counterpart)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.modules.author_check import orcid_valid

MODULE = "author_check"
FULL = Path(__file__).parents[1] / "bibr12" / "fixtures" / "bibr_12_1_full.json"


def _paper(authors: list[dict[str, object]], affiliations: list[list[int]]) -> pc.Paper:
    paper = pc.test_paper(["Text."])
    paper["author"] = pd.DataFrame(
        {
            "author_id": pd.array(range(1, len(authors) + 1), dtype="Int64"),
            "given": pd.array([a.get("given") for a in authors], dtype="string"),
            "family": pd.array([a.get("family") for a in authors], dtype="string"),
            "literal": pd.array([a.get("literal") for a in authors], dtype="string"),
            "email": pd.array([None] * len(authors), dtype="string"),
            "corresponding": pd.array(
                [bool(a.get("corresponding")) for a in authors], dtype="boolean"
            ),
            "orcid": pd.array([a.get("orcid") for a in authors], dtype="string"),
            "credit_roles": [list(a.get("roles", [])) for a in authors],  # type: ignore[call-overload]
        }
    )
    paper["affiliation"] = pd.DataFrame(
        {
            "affiliation_id": pd.array(range(1, len(affiliations) + 1), dtype="Int64"),
            "text": pd.array(["Uni"] * len(affiliations), dtype="string"),
            "author_ids": affiliations,
        }
    )
    paper["affiliation_match"] = pd.DataFrame(
        {
            "affiliation_id": pd.array([1] if affiliations else [], dtype="Int64"),
            "service": pd.array(["ror"] if affiliations else [], dtype="string"),
        }
    )
    return paper


@pytest.mark.parametrize(
    ("orcid", "valid"),
    [
        ("0000-0002-1825-0097", True),
        ("https://orcid.org/0000-0002-1694-233X", True),
        ("0000000218250097", True),
        ("0000-0002-1825-0098", False),
        ("not an orcid", False),
    ],
)
def test_orcid_check_digit(orcid: str, valid: bool) -> None:
    assert orcid_valid(orcid) is valid


def test_bibr_export() -> None:
    out = pc.module_run(pc.read(FULL), MODULE)
    assert out.traffic_light == "green"
    assert out.table["orcid_valid"].tolist() == [True]
    assert out.table["ror_matched"].tolist() == [1]
    assert out.table["credit_roles"].tolist() == [1]
    assert out.summary_table.loc[0, ["authors", "with_orcid", "with_affiliation"]].tolist() == [
        1,
        1,
        1,
    ]


def test_missing_information() -> None:
    paper = _paper(
        [
            {"given": "Ada", "family": "L", "orcid": "0000-0002-1825-0098"},
            {"given": "Bo", "family": "K"},
            {"literal": "The Consortium"},
        ],
        [[1, 3]],
    )
    out = pc.module_run(paper, MODULE)
    assert out.traffic_light == "yellow"
    assert out.table["person"].tolist() == [True, True, False]
    assert out.table["affiliations"].tolist() == [1, 0, 1]
    assert out.summary_text == (
        "The ORCID of Ada L has an invalid check digit. "
        "1 of 2 authors have no ORCID: Bo K. "
        "1 of 3 authors have no affiliation: Bo K. "
        "No corresponding author is marked."
    )


def test_unlinked_affiliations_are_not_judged() -> None:
    paper = _paper(
        [{"given": "Ada", "family": "L", "orcid": "0000-0002-1825-0097", "corresponding": True}],
        [[]],
    )
    out = pc.module_run(paper, MODULE)
    assert out.table["affiliations"].isna().all()
    assert out.traffic_light == "green"


def test_no_authors() -> None:
    out = pc.module_run(pc.paper(), MODULE)
    assert out.traffic_light == "na"
    assert len(out.table) == 0


def test_paper_list() -> None:
    good = pc.read(FULL)
    bad = _paper([{"given": "Bo", "family": "K"}], [[1]])
    out = pc.module_run(pc.PaperList([good, bad]), MODULE)
    assert out.traffic_light == "info"
    assert out.summary_text == "1 of 2 papers lack some author information."
