"""The demo papers and the demo repository (``demopaper()``, ``demofile()``)."""

from __future__ import annotations

import pytest

import metacheck as pc
from metacheck.papers.validate import paper_validate


def test_demopaper_stroop_effect() -> None:
    paper = pc.demopaper("stroop_effect")
    assert paper_validate(paper)
    assert paper.paper_id == "stroop_effect"
    assert paper.paper_id != pc.demopaper().paper_id


def test_demopaper_default_is_to_err_is_human() -> None:
    assert pc.demopaper().paper_id == "to_err_is_human"


def test_demopaper_bad_name() -> None:
    with pytest.raises(ValueError, match="'arg' should be one of"):
        pc.demopaper("stroop")


def test_demofile_repo_is_a_small_repository() -> None:
    repo = pc.demofile("repo")
    assert repo.is_dir()
    names = {p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file()}
    assert {
        "README.md",
        "LICENSE",
        "renv.lock",
        "data/stroop_data.csv",
        "documentation/stroop_data_codebook.csv",
        "code/analysis.qmd",
    } <= names
