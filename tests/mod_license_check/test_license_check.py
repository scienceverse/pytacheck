"""Tests for license_check, a pytacheck module (metacheck has no counterpart)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.modules.license_check import _from_text, _kind

MODULE = "license_check"
FULL = Path(__file__).parents[1] / "bibr12" / "fixtures" / "bibr_12_1_full.json"


def _paper(sentences: list[str], **info: str) -> pc.Paper:
    paper = pc.test_paper(sentences)
    frame = paper["info"].copy()
    for field, value in info.items():
        frame[field] = pd.array([value], dtype="string")
    paper["info"] = frame
    return paper


def test_listed_with_its_metadata() -> None:
    assert MODULE in pc.module_list()["name"].tolist()
    assert pc.module_info(MODULE).title == "License"


def test_bibr_export() -> None:
    out = pc.module_run(pc.read(FULL), MODULE)
    assert out.traffic_light == "green"
    assert out.table["found_by"].tolist() == ["bibr"]


@pytest.mark.parametrize(
    ("text", "spdx"),
    [
        ("https://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
        ("http://creativecommons.org/licenses/by-nc-nd/3.0", "CC-BY-NC-ND-3.0"),
        ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0-1.0"),
        ("This article is licensed under CC BY-SA 4.0.", "CC-BY-SA-4.0"),
        ("Released under CC0.", "CC0"),
        ("under the Creative Commons Attribution-NonCommercial License", "CC-BY-NC"),
        ("a Creative Commons Attribution 4.0 International License", "CC-BY-4.0"),
        ("© 2020 The Authors. All rights reserved.", "all-rights-reserved"),
        ("We used the CCBS scale.", None),
        ("The licence was revoked.", None),
    ],
)
def test_from_text(text: str, spdx: str | None) -> None:
    assert _from_text(text) == spdx


def test_kinds() -> None:
    assert [_kind(s) for s in ("CC-BY-4.0", "CC0-1.0", "CC-BY-NC-4.0", "CC-BY-ND", "MIT")] == [
        "open",
        "open",
        "restricted",
        "restricted",
        "other",
    ]


def test_bibr_url_without_an_spdx_id() -> None:
    paper = _paper(
        ["We ran a study."], license_url="https://creativecommons.org/licenses/by-nc/4.0/"
    )
    out = pc.module_run(paper, MODULE)
    assert out.traffic_light == "yellow"
    assert out.table["license"].tolist() == ["CC-BY-NC-4.0"]
    assert out.summary_table["open"].tolist() == [False]


def test_falls_back_to_the_text() -> None:
    paper = _paper(["We ran a study.", "This article is distributed under CC BY 4.0."])
    out = pc.module_run(paper, MODULE)
    assert out.traffic_light == "green"
    assert out.table["found_by"].tolist() == ["text"]
    assert out.table["statement"].tolist() == ["This article is distributed under CC BY 4.0."]


def test_all_rights_reserved() -> None:
    out = pc.module_run(_paper(["Copyright 2020. All rights reserved."]), MODULE)
    assert out.traffic_light == "yellow"
    assert out.table["license"].isna().all()
    assert out.summary_text == "The paper's license restricts reuse: All rights reserved."


def test_no_license() -> None:
    out = pc.module_run(pc.test_paper(["We ran a study."]), MODULE)
    assert (out.traffic_light, out.summary_text) == ("na", "No license was found.")
    assert out.summary_table["open"].tolist() == [False]


def test_paper_list() -> None:
    papers = pc.PaperList(
        [_paper(["x"], license_spdx="CC-BY-4.0"), pc.test_paper(["We ran a study."])]
    )
    out = pc.module_run(papers, MODULE)
    assert out.traffic_light == "info"
    assert out.summary_text == (
        "1 of 2 papers have an open license; 1 have no license that could be found."
    )
