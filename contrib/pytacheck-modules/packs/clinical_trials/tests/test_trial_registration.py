"""Tests for trial_registration (run with ``pytest`` from the pack folder)."""

from __future__ import annotations

from pathlib import Path

import pytest

import pytacheck as pc
from pytacheck.module import module_run

MODULE = str(Path(__file__).resolve().parent.parent / "trial_registration.py")


@pytest.mark.parametrize(
    ("sentence", "registry", "trial_id"),
    [
        ("Registered at ClinicalTrials.gov (NCT01234567).", "ClinicalTrials.gov", "NCT01234567"),
        ("The trial is ISRCTN 12345678.", "ISRCTN", "ISRCTN12345678"),
        ("EudraCT number 2004-001234-56.", "EU CTR (EudraCT)", "2004-001234-56"),
        ("EU CT number 2022-500014-26-00.", "CTIS (EU CT)", "2022-500014-26-00"),
        ("Registered with ANZCTR (ACTRN12615000123456).", "ANZCTR", "ACTRN12615000123456"),
        ("Registered as ChiCTR-IOR-17013520.", "ChiCTR", "ChiCTR-IOR-17013520"),
        ("Registered as ChiCTR2000029308.", "ChiCTR", "ChiCTR2000029308"),
        ("German Clinical Trials Register DRKS00012345.", "DRKS", "DRKS00012345"),
        ("Registered prospectively (CTRI/2020/05/025013).", "CTRI", "CTRI/2020/05/025013"),
    ],
)
def test_finds_each_registry(sentence: str, registry: str, trial_id: str) -> None:
    out = module_run(pc.test_paper([sentence]), MODULE)
    assert out.traffic_light == "green"
    assert out.table["registry"].tolist() == [registry]
    assert out.table["trial_id"].tolist() == [trial_id]
    assert out.summary_table["trial_registration"].tolist() == [1]


def test_a_eu_ct_number_is_not_also_a_eudract_number() -> None:
    out = module_run(pc.test_paper(["EU CT 2022-500014-26-00."]), MODULE)
    assert out.table["registry"].tolist() == ["CTIS (EU CT)"]


def test_duplicates_are_counted_once() -> None:
    paper = pc.test_paper(["NCT01234567 was registered.", "See NCT 01234567 again."])
    out = module_run(paper, MODULE)
    assert out.summary_table["trial_registration"].tolist() == [1]
    assert out.summary_table["trial_ids"].tolist() == ["NCT01234567"]


def test_trial_without_registration_is_flagged() -> None:
    out = module_run(pc.test_paper(["We ran a randomised controlled trial."]), MODULE)
    assert out.traffic_light == "yellow"
    assert out.summary_table["trial_registration"].tolist() == [0]


def test_not_a_trial() -> None:
    out = module_run(pc.test_paper(["The study was preregistered at OSF."]), MODULE)
    assert out.traffic_light == "na"
    assert len(out.table) == 0


def test_paper_list() -> None:
    papers = pc.PaperList([pc.test_paper(["NCT01234567."]), pc.test_paper(["Nothing here."])])
    out = module_run(papers, MODULE)
    assert out.summary_table["trial_registration"].tolist() == [1, 0]
