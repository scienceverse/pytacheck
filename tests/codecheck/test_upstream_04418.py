"""code_check synchronised with metacheck 0.4.4.18 (tests/testthat/test-code_check.R, #648)."""

from __future__ import annotations

import pandas as pd

from metacheck.codecheck.core import (
    _code_missing_files,
    _in_others_code_folder,
    _is_others_code_path,
    code_abs_path,
)


def _ap(x: str) -> list[str]:
    return code_abs_path(x)["abs_path"].tolist()


def test_a_web_route_is_not_an_absolute_path() -> None:
    # a web route given to a call that serves or requests it, or assigned to a name that says it
    # is a web address; a folder named "prefix" stays
    assert _ap('@admin_bp.route("/api/v1/auth/login", methods=["POST"])') == []
    assert _ap('r = client.get("/api/health")') == []
    assert _ap('d <- httr::GET("/api/x")') == []
    assert _ap('API_PREFIX = "/api/v1"') == []
    assert _ap('data_prefix = "/home/me/data"') == ["/home/me/data"]
    assert _ap('p <- fs::path("/Users/me/data")') == ["/Users/me/data"]


def test_code_by_others_is_recognised_by_its_folder() -> None:
    path = [
        "node_modules/x/index.js",
        "extra_ados/a.ado",
        "src/jspsych.min.js",
        "analysis/a.R",
        "toolboxes/t.m",
        "Vendor\\lib.R",
        None,
    ]
    assert _is_others_code_path(path) == [True, True, True, False, True, True, False]


def test_a_folder_named_after_a_release_is_by_others_only_beside_the_authors_code() -> None:
    path = ["jspsych-6.3.1/jspsych.js", "code/a.js"]
    assert _is_others_code_path(path, group=["r", "r"]) == [True, False]
    # the repository holds no code outside the release folder: the authors' own code
    only = ["tool-v0.4.0/a.R", "tool-v0.4.0/b.R"]
    assert _is_others_code_path(only, group=["r", "r"]) == [False, False]
    # only code counts as the authors' beside it, and each repository is judged on its own
    assert _is_others_code_path(
        ["jspsych-6.3.1/j.js", "readme.md", "x-1.0/a.R", "b.R"],
        is_code=[True, False, True, True],
        group=["r1", "r1", "r2", "r2"],
    ) == [False, False, True, False]


def test_a_listing_is_judged_by_file_path_and_language() -> None:
    files = pd.DataFrame(
        {
            "file_name": ["pkg.ado", "a.do"],
            "file_path": ["extra_ados/pkg.ado", "a.do"],
            "language": ["Stata", "Stata"],
            "repo_url": ["r", "r"],
        }
    )
    assert _in_others_code_folder(files) == [True, False]
    assert _in_others_code_folder(files.iloc[0:0]) == []


def test_files_not_found_are_listed_apart_when_the_paper_downloads_without_naming() -> None:
    cf = pd.DataFrame(
        {
            "paper_id": ["p", "p", "q"],
            "repo_url": ["r", "r", "r2"],
            "file_name": ["0.R", "1.R", "2.R"],
            "loaded_refs": ["", "VIA.Rda\u001fa.csv", "VIA.Rda"],
            "files_written": ["", "", ""],
            "fetches_unnamed": [True, False, False],
        }
    )
    out = _code_missing_files(
        cf, pd.DataFrame({"repo_url": ["r", "r2"], "file_name": ["a.csv", "x.csv"]})
    )
    assert out["loaded_files_missing"].tolist() == [0, 0, 1]
    assert out["loaded_files_fetched_names"].tolist() == [pd.NA, "VIA.Rda", pd.NA]
    assert out["loaded_files_missing_names"].tolist() == ["", "", "VIA.Rda"]
    assert "fetches_unnamed" not in out.columns
