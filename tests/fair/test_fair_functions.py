"""The private ``_FAIR_repo_*`` functions that ``test-fair-repo.R`` calls directly.

Port of the blocks of ``tests/testthat/test-fair-repo.R`` that test a single function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from metacheck.fair import metadata as md
from metacheck.fair import repo
from metacheck.papers.io import test_paper as make_test_paper
from tests.fair.fair_helpers import cell, rds_column, read_lines, write_lines
from tests.fair.rds_writer import write_table_rds


# .FAIR_repo_copy keeps two different originals with one place apart
def test_FAIR_repo_copy_keeps_two_different_originals_with_one_place_apart(
    tmp_path: Path,
) -> None:
    # with folders kept this is rare, but a converted original placed where another original
    # already is got overwritten
    d = tmp_path / "src"
    write_table_rds(d / "one.rds", {"x": [1, 2, 3]})
    write_table_rds(d / "two.rds", {"x": [4, 5, 6]})
    plan = pd.DataFrame(
        {
            "file_name": ["tab.rds", "tab.rds"],
            "data_type": ["data", "data"],
            "group": pd.Series([None, None], dtype=object),
            "target_path": ["data/study-tab_data.csv", "data/study-tab_data.csv"],
            "convert": [True, True],
            "original_target": ["data/tab.rds", "data/tab.rds"],
        }
    )
    structure_df = pd.DataFrame({"file_location": [str(d / "one.rds"), str(d / "two.rds")]})
    out = tmp_path / "out"
    out.mkdir()
    repo._FAIR_repo_copy(plan, structure_df, str(out))
    originals = sorted((out / "data").glob("*.rds"))
    assert len(originals) == 2
    assert {tuple(rds_column(f, "x")) for f in originals} == {(1, 2, 3), (4, 5, 6)}


# the study design is filled from a single preregistration only
def test_the_study_design_is_filled_from_a_single_preregistration_only() -> None:
    def pr(n: int) -> dict[str, Any]:
        return {
            "prereg_check": {
                "table": pd.DataFrame(
                    {
                        "paper_id": ["p"] * n,
                        "research_questions": [f"Q {i}" for i in range(1, n + 1)],
                        "study_design_overview": ["between"] * n,
                        "data_exclusion_criteria": pd.Series([None] * n, dtype=object),
                        "sample_size": ["100"] * n,
                    }
                )
            }
        }

    one = md._FAIR_repo_study_design_entity(pr(1), "p")
    assert one["purpose"] == ["Q 1"]
    assert one["targetSampleSize"] == ["100"]
    assert one["dataCollectionMethodology"] == ["between"]
    # a field not found is left out, except how the data were collected
    assert one.get("populationOfConcern") is None
    two = md._FAIR_repo_study_design_entity(pr(2), "p")
    assert two.get("purpose") is None
    assert two["dataCollectionMethodology"] == ["unknown"]
    none = md._FAIR_repo_study_design_entity({}, "p")
    assert none.get("purpose") is None
    assert none["dataCollectionMethodology"] == ["unknown"]


# without a paper, the title comes from the folder's own README (the plain first line)
def test_a_plain_first_line_is_no_title(tmp_path: Path) -> None:
    # a plain first line is no title
    o = tmp_path / "readme-folder"
    write_lines(o / "README.txt", ["This readme.text file was generated on 2024-01-01 by me"])
    assert md._FAIR_repo_readme_title(["README.txt"], str(o)) is None


# (added) the title comes from a "# " heading, a "===" underline or the title: line
def test_readme_title_forms(tmp_path: Path) -> None:
    def title(name: str, lines: list[str]) -> Any:
        o = tmp_path / name.replace(".", "-")
        write_lines(o / name, lines)
        return md._FAIR_repo_readme_title([name], str(o))

    assert title("README.md", ["# A heading", "text"]) == "A heading"
    assert title("README.txt", ["Underlined", "=========="]) == "Underlined"
    assert title("README.Rmd", ["---", 'title: "Quoted title"', "---"]) == "Quoted title"
    # only a README at the top counts
    o = tmp_path / "nested"
    write_lines(o / "sub" / "README.md", ["# Not the top"])
    assert md._FAIR_repo_readme_title(["sub/README.md"], str(o)) is None
    assert md._FAIR_repo_readme_title([], str(o)) is None


# .FAIR_repo_funding reads funding_check's sentences and bibr's funders
def test_FAIR_repo_funding_reads_funding_checks_sentences_and_bibrs_funders() -> None:
    p = make_test_paper(["x"])
    assert md._FAIR_repo_funding(p, {}) is None
    ops = {
        "funding_check": {"table": pd.DataFrame({"paper_id": ["x"], "text": ["Funded by NWO."]})}
    }
    assert md._FAIR_repo_funding(p, ops)["statement"] == "Funded by NWO."
    p.info["funding_statement"] = pd.Series(["Supported by ERC."], dtype="string")
    funding = pd.DataFrame({"funding_id": [1], "funder": ["ERC"]})
    funding["award_ids"] = pd.Series([["A1"]], dtype=object)
    p.funding = funding
    f = md._FAIR_repo_funding(p, ops)
    assert f["statement"] == "Supported by ERC."
    assert list(f["funders"]) == ["ERC"]
    assert list(f["awards"]) == ["A1"]


# .FAIR_repo_credit_slug recognises CRediT roles as papers write them
def test_FAIR_repo_credit_slug_recognises_credit_roles_as_papers_write_them() -> None:
    slugs = md._FAIR_repo_credit_slug(["Writing – review & editing", "Formal Analysis", "Cooking"])
    assert [None if pd.isna(s) else s for s in slugs] == [
        "writing-review-editing",
        "formal-analysis",
        None,
    ]


# FAIR_repo adds the library() calls reproducibility_check added when it ran a script
def test_FAIR_repo_adds_the_library_calls_reproducibility_check_added(tmp_path: Path) -> None:
    ops = {
        "reproducibility_check": {
            "modifications": pd.DataFrame(
                {
                    "file_name": ["a.R", "a.R", "b.R"],
                    "change_type": ["library_injected", "library_injected", "path_rewrite"],
                    "detail": ["library(dplyr)", "library(tidyr)", '"x" -> "y"'],
                }
            )
        }
    }
    libs = repo._FAIR_repo_added_libraries(ops)
    assert {k: list(v) for k, v in libs.items()} == {"a.R": ["dplyr", "tidyr"]}
    plan = pd.DataFrame({"file_name": ["a.R", "b.R", "sub/b.R"], "data_type": "code"})
    # the row positions of the Python port are 0-based (R: 1)
    assert list(repo._FAIR_repo_libraries_for(libs, plan, 0)) == ["dplyr", "tidyr"]
    # two scripts named b.R: which one was run is unknown
    assert list(repo._FAIR_repo_libraries_for({"b.R": ["x"]}, plan, 1)) == []

    d = tmp_path / "script"
    write_lines(d / "a.R", ["x <- mutate(df, y = 1)"])
    sc = repo._FAIR_repo_write_script(
        str(d / "a.R"),
        "a.R",
        str(d / "out.R"),
        "analysis/a.R",
        pd.DataFrame({"file_name": ["a.R"]}),
        None,
        libraries=["dplyr"],
    )
    code = read_lines(d / "out.R")
    assert code[0].startswith("library(dplyr)  # [metacheck FAIR_repo added")
    assert code[1] == "x <- mutate(df, y = 1)"
    assert cell(sc, "libraries_added") == "dplyr"
