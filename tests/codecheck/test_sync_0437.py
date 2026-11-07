"""code_check helpers synchronised with metacheck 0.4.3.7 (R/code_check.R at 4a61888).

The expected values follow the R source (the regular expressions and the branches of each
helper); the parity cases in ``parity/cases/codecheck*.yaml`` hold the values R produced.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from metacheck.codecheck import core
from metacheck.utils import local_options

# ---------------------------------------------------------------------------
# kernel languages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "label"),
    [
        ("r", "R"),
        ("ir", "R"),
        ("python3", "Python"),
        ("ipython", "Python"),
        ("julia-1.9", "Julia"),
        ("octave", "MATLAB"),
        ("matlab", "MATLAB"),
        ("sas", "SAS"),
        ("stata", "Stata"),
        ("bash", "Bash"),
        ("  ", None),
        ("", None),
        (None, None),
    ],
)
def test_kernel_language(declared: str | None, label: str | None) -> None:
    assert core._kernel_language(declared) == label


def test_quarto_julia_chunk_is_julia(tmp_path: Path) -> None:
    path = tmp_path / "doc.qmd"
    path.write_text("---\ntitle: x\n---\n```{julia}\nx = 1\n```\n", encoding="utf-8")
    assert core.code_lang(str(path)) == "Julia"


def test_notebook_not_on_disk_is_python_for_now() -> None:
    assert core.code_lang("no/such/folder/nb.ipynb") == "Python"


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_newline_only_file_is_one_empty_line(tmp_path: Path) -> None:
    path = tmp_path / "a.R"
    path.write_bytes(b"\n")
    assert core.code_read(str(path)) == [""]
    path.write_bytes(b"")
    assert core.code_read(str(path)) == []


def test_missing_file_error_is_the_curl_text() -> None:
    with pytest.raises(FileNotFoundError, match="URL rejected: No host present"):
        core.code_read("/no/such/file.R")
    with pytest.raises(FileNotFoundError, match=r"Could not resolve hostname \[NA\]"):
        core.code_read("NA")


def test_binary_kinds() -> None:
    assert core._binary_kind_raw(b"x <- 1\n") is None
    assert core._binary_kind_raw(b"") is None
    assert core._binary_kind_raw(b"\x1f\x8b\x08\x00") == "compressed with gzip (as R saves data)"
    # a script that starts with "BZh" is not bzip2
    assert core._binary_kind_raw(b"BZh <- 1\n") is None
    assert core._binary_kind_raw(b"\x00\x01\x02") == "binary"
    # UTF-16 has zero bytes too, but a byte order mark
    assert core._binary_kind_raw("x <- 1\n".encode("utf-16")) is None


# ---------------------------------------------------------------------------
# renv's own files
# ---------------------------------------------------------------------------


def test_in_renv_folder() -> None:
    files = pd.DataFrame(
        {
            "file_name": ["activate.R", "a.R", "renv.lock", "b.R", "c.R"],
            "file_path": ["renv/activate.R", "code/a.R", "renv.lock", "sub/RENV/library/b.R", None],
        }
    )
    assert core._in_renv_folder(files) == [True, False, False, True, False]
    assert core._in_folder(files, "code") == [False, True, False, False, False]
    assert core._in_renv_folder(files.iloc[0:0]) == []


# ---------------------------------------------------------------------------
# notebooks
# ---------------------------------------------------------------------------


def _notebook(*cells: list[str], kernel: str = "python3") -> list[str]:
    import json

    nb = {
        "cells": [{"cell_type": "code", "source": [f"{ln}\n" for ln in c]} for c in cells],
        "metadata": {"kernelspec": {"language": kernel, "name": kernel}},
    }
    return [json.dumps(nb)]


def test_notebook_cell_commands() -> None:
    lines, removed = core._code_notebook_parts(
        _notebook(
            ["import os", "%matplotlib inline", "!pip install x", "  %time x = 1", "cd data", "df?"]
        )
    )
    assert lines == ["import os", "  pass", ""]
    assert removed == ["%matplotlib inline", "!pip install x", "%time x = 1", "cd data", "df?"]


def test_notebook_cell_magic_drops_the_cell() -> None:
    assert core._code_notebook_cell(["%%bash", "ls"]) == ([], ["%%bash"])
    # %%time and the like only time the code, which stays
    assert core._code_notebook_cell(["%%time", "x = 1"])[0] == ["x = 1"]


def test_r_notebook_keeps_its_percent_lines() -> None:
    lines, removed = core._code_notebook_parts(_notebook(["x %>% f()", "!x"], kernel="R"))
    assert lines == ["x %>% f()", "!x", ""]
    assert removed == []


# ---------------------------------------------------------------------------
# libraries and file references
# ---------------------------------------------------------------------------


def test_groundhog_library_is_not_library() -> None:
    out = core.code_library_names(['groundhog.library(c("dplyr", "tidyr"), "2023-01-15")'], "R")
    assert out["package"].tolist() == ["dplyr", "tidyr"]
    assert set(out["source"]) == {"groundhog"}


def test_groundhog_library_github_source_is_not_a_package() -> None:
    out = core.code_library_names(['groundhog.library("github::user/repo", "2023-01-15")'], "R")
    assert out["package"].tolist() == ["repo"]


def test_lapply_with_a_wrapper_function() -> None:
    out = core.code_library_names(
        [
            'pkgs <- c("tmap", "sf")',
            "lapply(pkgs, function(x) library(x, character.only = TRUE))",
        ],
        "R",
    )
    assert set(out["package"]) == {"tmap", "sf"}


def test_open_calls_over_several_lines() -> None:
    calls, taken = core._code_open_calls(
        ["x <- read.csv(", '  "./data.csv"', ")", "y <- 1"], r"read\.csv\s*\("
    )
    assert calls == ['read.csv(   "./data.csv" )']
    assert taken == [2, 3]
    # a call closed on its own line is left to the line-by-line search
    assert core._code_open_calls(['x <- read.csv("a.csv")', "y <- 1"], r"read\.csv\s*\(") == (
        [],
        [],
    )


def test_file_refs_find_a_name_on_the_next_line() -> None:
    refs = core.code_file_refs(["x <- read.csv(", '  "./data.csv"', ")"], "R")
    assert "data.csv" in " ".join(refs)


# ---------------------------------------------------------------------------
# session information and pin dates
# ---------------------------------------------------------------------------

SESSION_INFO = """R version 4.3.1 (2023-06-16)
Platform: x86_64-pc-linux-gnu

other attached packages:
[1] dplyr_1.1.2   ggplot2_3.4.2

loaded via a namespace (and not attached):
[1] compiler_4.3.1 rlang_1.1.1
"""


def test_session_packages() -> None:
    out = core._code_session_packages(SESSION_INFO.splitlines())
    assert out["package"].tolist() == ["dplyr", "ggplot2", "rlang"]  # compiler is part of R
    assert out["version"].tolist() == ["1.1.2", "3.4.2", "1.1.1"]
    assert out["source"].isna().all()


def test_session_packages_none_without_heading() -> None:
    assert len(core._code_session_packages(["R version 4.3.1", "dplyr_1.1.2"])) == 0


def test_pin_dates_in_call_and_variable() -> None:
    out = core._code_pin_dates(
        [
            'day <- "2023-02-01"',
            "groundhog.library(c('a', 'b'), day)",
            "# groundhog.library('z', '2020-01-01')",
            'checkpoint("2022-05-05")',
        ]
    )
    assert sorted(zip(out["mechanism"], out["date"], strict=True)) == [
        ("checkpoint", "2022-05-05"),
        ("groundhog", "2023-02-01"),
    ]


def test_env_pinned_per_language() -> None:
    r_only = {"pinned": True, "python_pinned": False}
    assert core._code_env_pinned(r_only, ["R"]) is True
    assert core._code_env_pinned(r_only, ["R", "Python"]) is False
    assert core._code_env_pinned(r_only, ["Python"]) is False
    assert core._code_env_pinned(r_only, ["Stata"]) is True  # judged by the R mechanisms
    assert core._code_env_pinned({"pinned": False, "python_pinned": True}, ["Python"]) is True


# ---------------------------------------------------------------------------
# the first download also fetches what a run needs
# ---------------------------------------------------------------------------


def _predownload_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "file_name": ["a.R", "model.rds", "photo.png", "activate.R"],
            "file_path": ["a.R", "model.rds", "photo.png", "renv/activate.R"],
            "file_url": ["u1", "u2", "u3", "u4"],
            "file_location": [None, None, None, None],
            "repo_url": ["https://osf.io/abc"] * 4,
            "language": ["R", None, None, "R"],
        }
    )


def test_predownload_fetches_run_files_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_download(rows: pd.DataFrame, all_files: pd.DataFrame, **kw: object) -> pd.DataFrame:
        calls.append(rows["file_name"].tolist())
        out = rows.copy()
        out["file_location"] = [f"/cache/{n}" for n in rows["file_name"]]
        out.attrs = {"gated": None, "oversize_skipped": None, "failed": None}
        return out

    monkeypatch.setattr(core, "_download", fake_download)

    core._code_predownload(_predownload_frame(), 10, 100, False)
    assert calls[-1] == ["a.R"]  # code only; renv's own file is left out

    with local_options({"metacheck.download_for_run": True}):
        core._code_predownload(_predownload_frame(), 10, 100, False)
    # the data and model files a run needs come too; images do not, nor does renv/activate.R
    assert calls[-1] == ["a.R", "model.rds"]
