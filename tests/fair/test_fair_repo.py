"""FAIR_repo() on a small folder written to a temporary folder: no network, no LLM.

Port of the first half of ``tests/testthat/test-fair-repo.R`` (copying, scripts, layout, errors).
Every ``test_that()`` block is one function, in R's order, with R's title in a comment.
The metadata tests are in ``test_fair_metadata.py`` and the tests of single functions in
``test_fair_functions.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.fair.repo import FAIR_repo
from tests.fair.fair_helpers import (
    BOM,
    fair_fixture,
    fair_paper,
    files_in,
    md5,
    md5_tree,
    names_in,
    rds_column,
    read_lines,
    rmd_page,
    tree_state,
    write_csv,
    write_lines,
    write_xlsx,
    zip_folder,
)
from tests.fair.rds_writer import write_list_rds, write_model_rds, write_table_rds
from tests.httpmock import no_network

FairRun = Callable[..., dict[str, Any]]

#: what FAIR_repo() raises for a bad argument (R's stop()): the class is the implementation's choice
BAD_ARGUMENT = (ValueError, TypeError, OSError)


def written(res: dict[str, Any]) -> list[str | None]:
    """The ``written`` column of the plan, with None for a file not written."""
    return [None if pd.isna(w) else w for w in res["plan"]["written"]]


# FAIR_repo copies every file to its place in the new layout
def test_fair_repo_copies_every_file_to_its_place(proj: Path, fair_run: FairRun) -> None:
    res = fair_run(proj)
    out = Path(res["output_dir"])
    assert (out / "data" / "study-scores_data.csv").exists()
    assert (out / "analysis" / "analysis.R").exists()
    assert "README.txt" in [Path(f).name for f in files_in(out)]
    assert res["n_files_copied"] == res["plan"]["written"].notna().sum()
    assert len(res["skipped"]) == 0

    # a data CSV is copied without its byte order mark
    first3 = (out / "data" / "study-bom_data.csv").read_bytes()[:3]
    assert first3 != BOM


# FAIR_repo leaves the original folder unchanged
def test_fair_repo_leaves_the_original_folder_unchanged(proj: Path, fair_run: FairRun) -> None:
    before = md5_tree(proj)
    fair_run(proj)
    assert md5_tree(proj) == before


# FAIR_repo goes online nowhere, also when the paper links a repository
def test_fair_repo_goes_online_nowhere(
    proj: Path, fair_run: FairRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = fair_paper()
    p.text["text"] = pd.Series(
        ["Data and code are at https://osf.io/abcde and https://github.com/a/b."], dtype="string"
    )
    online: list[str] = []

    def went_online(*args: Any, **kwargs: Any) -> Any:
        online.append(str(args[1] if len(args) > 1 else args[0] if args else kwargs))
        raise RuntimeError(f"went online: {online[-1]}")

    # the HTTP layer of metacheck, and (inside no_network) every socket and name lookup
    from metacheck import http

    for name in ("request", "client", "client_for", "batch_query"):
        monkeypatch.setattr(http, name, went_online)
    with no_network() as attempts:
        res = fair_run(proj, paper=p)
    assert online == []
    assert attempts == []
    assert res["n_files_copied"] > 0


# FAIR_repo changes a script's file paths and setwd() and says so
def test_fair_repo_changes_a_scripts_paths_and_setwd(
    proj: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    res = FAIR_repo(str(proj), output_dir=str(out))
    assert re.search(r"setwd\(\) was removed", capsys.readouterr().err)
    code = read_lines(out / "analysis" / "analysis.R")
    assert any('read.csv("data/study-scores_data.csv")' in line for line in code)
    # a longer name that ends in the same file name is left alone
    assert any('read.csv("old_scores.csv")' in line for line in code)
    assert re.match(r"^invisible\(NULL\)  # \[metacheck FAIR_repo removed setwd\]", code[0])
    scripts = res["scripts"]
    assert scripts["setwd_removed"].tolist() == [1]
    assert scripts["setwd_paths"].tolist() == ["C:/Users/me/project"]
    assert scripts["paths_changed"].tolist() == [1]


# FAIR_repo copies a script with nothing to change byte for byte
def test_fair_repo_copies_a_script_with_nothing_to_change_byte_for_byte(
    proj: Path, fair_run: FairRun
) -> None:
    (proj / "Code" / "helper.R").write_bytes(b"x <- 1\r\ny <- 2\r\n")
    res = fair_run(proj)
    copy = Path(res["output_dir"]) / "analysis" / "helper.R"
    assert md5(copy) == md5(proj / "Code" / "helper.R")


# FAIR_repo writes a data file that is not a CSV as a real CSV and keeps the original
def test_fair_repo_writes_a_non_csv_data_file_as_csv_and_keeps_the_original(
    proj: Path, fair_run: FairRun
) -> None:
    write_xlsx(
        proj / "data" / "survey.xlsx", {"Sheet1": {"id": [1, 2, 3], "cond": ["a", "b", "a"]}}
    )
    res = fair_run(proj)
    data_dir = Path(res["output_dir"]) / "data"
    back = pd.read_csv(data_dir / "study-survey_data.csv")
    assert len(back) == 3
    assert list(back.columns) == ["id", "cond"]
    assert (data_dir / "survey.xlsx").exists()


# FAIR_repo keeps a text table as it is, and an archive package's files in logs/
def test_fair_repo_keeps_a_text_table_and_an_archive_packages_files(
    proj: Path, fair_run: FairRun
) -> None:
    write_lines(proj / "data" / "scores_tab.txt", ["id\tscore", "1\t2", "2\t3"])
    write_lines(proj / "bagit.txt", ["BagIt-Version: 0.97"])
    write_lines(proj / "manifest-md5.txt", ["0123abc  data/scores.csv"])
    res = fair_run(proj)
    out = Path(res["output_dir"])
    assert (out / "data" / "scores_tab.txt").exists()
    assert not any("scorestab" in name for name in names_in(out / "data"))
    assert (out / "logs" / "original-package" / "bagit.txt").exists()
    assert (out / "logs" / "original-package" / "manifest-md5.txt").exists()
    assert not (out / "documentation" / "bagit.txt").exists()
    assert any("original-package" in line for line in read_lines(out / "CHANGES.md"))


# FAIR_repo keeps a data file that holds no table as it is, and two originals with one name apart
def test_fair_repo_keeps_a_data_file_with_no_table_and_two_originals_apart(
    proj: Path, fair_run: FairRun
) -> None:
    # a list is no table (and no model): kept as it is, not reported as failed
    write_list_rds(proj / "data" / "lookup.rds")
    # two different R tables with one name in one folder of the copy
    write_table_rds(proj / "data" / "a" / "tab.rds", {"x": [1, 2, 3]})
    write_table_rds(proj / "data" / "a" / "b" / "tab.rds", {"x": [4, 5, 6]})
    res = fair_run(proj)
    data_dir = Path(res["output_dir"]) / "data"
    assert len(res["copy_failed"]) == 0
    assert (data_dir / "lookup.rds").exists()
    assert not (data_dir / "study-lookup_data.csv").exists()
    # each R table is written as CSV, with its own original next to it
    assert rds_column(data_dir / "a" / "tab.rds", "x") == [1, 2, 3]
    assert rds_column(data_dir / "a" / "b" / "tab.rds", "x") == [4, 5, 6]
    assert (data_dir / "a" / "study-tab_data.csv").exists()
    assert (data_dir / "a" / "b" / "study-tab_data.csv").exists()


# FAIR_repo gives the second of two different files with one place a numbered name
def test_fair_repo_gives_the_second_of_two_files_with_one_place_a_numbered_name(
    proj: Path, fair_run: FairRun
) -> None:
    # x.csv and a one-sheet x.xlsx in one folder both become study-x_data.csv
    write_lines(proj / "data" / "x.csv", ["a,b", "1,2"])
    write_xlsx(proj / "data" / "x.xlsx", {"Sheet1": {"a": [3], "b": [4]}})
    # files with one name in different folders stay apart in their folders
    write_csv(proj / "extra" / "scores.csv", {"id": [9]})
    res = fair_run(proj)
    renamed = res["renamed"]
    assert len(renamed) == 1
    assert renamed["written"].tolist() == ["data/study-x_data_2.csv"]
    assert (Path(res["output_dir"]) / renamed["written"].iloc[0]).exists()
    assert (Path(res["output_dir"]) / "data" / "extra" / "study-scores_data.csv").exists()


# FAIR_repo leaves out a file from a zip that is also next to it, and leaves the folder unchanged
def test_fair_repo_leaves_out_a_file_from_a_zip_that_is_also_next_to_it(
    proj: Path, fair_run: FairRun
) -> None:
    # the folder holds a zip of its own data folder, next to that folder
    zip_folder(proj / "data.zip", "data", proj)
    before = md5_tree(proj)
    res = fair_run(proj)
    # the zip is unpacked elsewhere, never into the researcher's folder
    assert md5_tree(proj) == before
    out = Path(res["output_dir"])
    # each data file is in the copy once
    csvs = [f for f in files_in(out) if "scores" in Path(f).name]
    assert csvs == ["data/study-scores_data.csv"]
    ident = res["identical"]
    assert len(ident) > 0
    assert set(ident["same_as"]) <= set(res["plan"]["written"].dropna())
    assert any("identical file" in line for line in read_lines(out / "CHANGES.md"))
    # a left-out file is not listed as missing
    from tests.fair.fair_helpers import graph, read_json

    crate = read_json(out / "ro-crate-metadata.json")
    assert "metacheck:notIncluded" not in graph(crate)[1]


# FAIR_repo keeps a README in a subfolder in documentation/, and an identical one once
def test_fair_repo_keeps_a_subfolder_readme_and_an_identical_one_once(
    proj: Path, fair_run: FairRun
) -> None:
    write_lines(proj / "nest-1" / "README", ["About the files in nest-1."])
    write_lines(proj / "copy of top" / "README.txt", ["A readme."])
    res = fair_run(proj)
    out = Path(res["output_dir"])
    assert read_lines(out / "README.txt") == ["A readme."]
    assert read_lines(out / "documentation" / "nest-1_README") == ["About the files in nest-1."]
    assert not (out / "documentation" / "copy-of-top_README.txt").exists()
    ident = res["identical"]
    assert ident["same_as"][ident["path"].str.contains("copy of top")].tolist() == ["README.txt"]
    assert not any(re.match(r"^README_[0-9]", name) for name in names_in(out))


# FAIR_repo leaves an existing folder alone unless overwrite = TRUE
def test_fair_repo_leaves_an_existing_folder_alone_unless_overwrite(
    proj: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    (out / "mine.txt").write_text("keep\n")
    res = FAIR_repo(str(proj), output_dir=str(out))
    assert "already exists" in capsys.readouterr().err
    assert res["existed"] is True
    assert (out / "mine.txt").exists()

    FAIR_repo(str(proj), output_dir=str(out), overwrite=True)
    assert not (out / "mine.txt").exists()


# FAIR_repo needs a folder for the copy
def test_fair_repo_needs_a_folder_for_the_copy(proj: Path) -> None:
    # there is no default (R: CRAN does not allow writing into the working directory)
    with pytest.raises(TypeError, match="output_dir"):
        FAIR_repo(str(proj))  # type: ignore[call-arg]


# FAIR_repo stops for a path that is not a folder, or a copy inside it
def test_fair_repo_stops_for_a_bad_path_or_a_copy_inside_the_folder(tmp_path: Path) -> None:
    out = str(tmp_path / "never-written")
    with pytest.raises(BAD_ARGUMENT, match="No folder found"):
        FAIR_repo(str(tmp_path / "no_such_folder"), output_dir=out)
    with pytest.raises(BAD_ARGUMENT, match="single path"):
        FAIR_repo(["a", "b"], output_dir=out)  # type: ignore[arg-type]
    d = tmp_path / "folder"
    d.mkdir()
    with pytest.raises(BAD_ARGUMENT, match="outside the folder"):
        FAIR_repo(str(d), output_dir=str(d / "copy"))
    with pytest.raises(BAD_ARGUMENT, match="paper object"):
        FAIR_repo(str(d), output_dir=out, paper=[1])  # type: ignore[arg-type]
    assert not Path(out).exists()


# FAIR_repo treats a top folder that holds every file as the top
def test_fair_repo_treats_a_top_folder_that_holds_every_file_as_the_top(
    proj: Path, tmp_path: Path, fair_run: FairRun
) -> None:
    import shutil

    # a repository downloaded from GitHub sits in "<name>-<commit>/"
    d = tmp_path / "wrapper"
    d.mkdir()
    shutil.copytree(proj, d / "repo-1a2b3c4")
    res = fair_run(d)
    out = Path(res["output_dir"])
    assert read_lines(out / "README.txt") == ["A readme."]
    assert not (out / "README.md").exists()
    assert (out / "data" / "study-scores_data.csv").exists()


# FAIR_repo keeps the folders inside data/, so files with one name stay apart
def test_fair_repo_keeps_the_folders_inside_data(proj: Path, fair_run: FairRun) -> None:
    for s in ("scenario_lb", "scenario_ub"):
        write_csv(
            proj / "data" / s / "forcing.csv",
            {"t": [1, 2], "v": [1, 2] if s == "scenario_lb" else [3, 4]},
        )
        # identical in both scenarios, and still kept in both
        write_lines(proj / "data" / s / "soil.csv", ["a,b", "1,2"])
    res = fair_run(proj)
    data_dir = Path(res["output_dir"]) / "data"
    for s in ("scenario_lb", "scenario_ub"):
        assert set(names_in(data_dir / s)) == {"study-forcing_data.csv", "study-soil_data.csv"}
    assert len(res["renamed"]) == 0
    assert len(res["identical"]) == 0


# FAIR_repo puts results in outputs/: files only scripts write, and fitted models
def test_fair_repo_puts_results_in_outputs(proj: Path, fair_run: FairRun) -> None:
    write_lines(
        proj / "Code" / "summarise.R",
        ['d <- read.csv("../data/scores.csv")', 'write.csv(summary(d), "results_table.csv")'],
    )
    # the results the script wrote, shared next to the code
    write_lines(proj / "Code" / "results_table.csv", ["x,y", "1,2"])
    write_model_rds(proj / "data" / "fit.rds")
    # an R table with a CSV of the same name next to it is not converted again
    write_table_rds(proj / "data" / "scores.rds", {"id": [1, 2, 3, 4, 5]})
    res = fair_run(proj)
    out = Path(res["output_dir"])
    assert (out / "outputs" / "results_table.csv").exists()
    assert (out / "outputs" / "fit.rds").exists()
    assert not (out / "data" / "study-fit_data.csv").exists()
    assert (out / "data" / "scores.rds").exists()
    assert not any("scores_data_2" in name for name in names_in(out / "data"))
    # the script now writes its results into outputs/, where they are shared
    code = read_lines(out / "analysis" / "summarise.R")
    assert any('"outputs/results_table.csv"' in line for line in code)


# FAIR_repo writes no dataset_description.json for a folder of code only
def test_fair_repo_writes_no_dataset_description_for_a_folder_of_code_only(
    tmp_path: Path, fair_run: FairRun
) -> None:
    d = tmp_path / "codeonly"
    write_lines(d / "analysis.R", ["x <- 1"])
    res = fair_run(d)
    out = Path(res["output_dir"])
    assert not (out / "dataset_description.json").exists()
    assert (out / "ro-crate-metadata.json").exists()


# FAIR_repo works on a copy, so nothing is written into the folder
def test_fair_repo_works_on_a_copy_so_nothing_is_written_into_the_folder(
    proj: Path, fair_run: FairRun
) -> None:
    (proj / "docs").mkdir()
    rmd_page(proj / "docs" / "report.html")
    before = tree_state(proj)
    res = fair_run(proj)
    # code_check wrote the recovered code into a code/ folder next to the page
    assert tree_state(proj) == before
    # and no path of the temporary copy reaches the logs
    logs = sorted((Path(res["output_dir"]) / "logs").glob("*.json"))
    assert logs
    json_text = "\n".join(p.read_text(encoding="utf-8") for p in logs)
    assert "metacheck-fair-" not in json_text


# FAIR_repo keeps code recovered from a rendered page as a script
def test_fair_repo_keeps_code_recovered_from_a_rendered_page_as_a_script(
    proj: Path, fair_run: FairRun
) -> None:
    from tests.fair.fair_helpers import by_id, graph, read_json

    (proj / "docs").mkdir()
    rmd_page(proj / "docs" / "report.html")
    res = fair_run(proj)
    out = Path(res["output_dir"])
    plan = res["plan"]
    rows = plan.index[plan["file_name"] == "report.R"].tolist()
    assert len(rows) == 1
    i = rows[0]
    assert plan["written"][i].startswith("analysis/")
    code = read_lines(out / plan["written"][i])
    # its paths are changed like those of the authors' own scripts
    assert any("data/study-scores_data.csv" in line for line in code)
    assert any("recovered from output files" in line for line in read_lines(out / "CHANGES.md"))
    entities = by_id(graph(read_json(out / "ro-crate-metadata.json")))
    page = plan["written"][plan["file_name"] == "report.html"].tolist()[0]
    assert entities[plan["written"][i]]["isBasedOn"]["@id"] == page

    # not when the folder has the document the page was made from
    write_lines(proj / "docs" / "report.Rmd", ["```{r}", "x <- 1", "```"])
    res2 = fair_run(proj)
    assert "report.R" not in set(res2["plan"]["file_name"])


# FAIR_repo replaces setwd() only where it sets a written-out or the script's own folder
def test_fair_repo_replaces_setwd_only_where_it_sets_a_written_out_or_own_folder(
    proj: Path, fair_run: FairRun
) -> None:
    write_lines(
        proj / "Code" / "folders.R",
        [
            "old <- getwd()",
            "setwd(tempdir())",
            "setwd(old)",
            "setwd(dirname(rstudioapi::getActiveDocumentContext()$path))",
            'msg <- "a line that says',
            "setwd('here') in a text\"",
            '  setwd("results")',
        ],
    )
    res = fair_run(proj)
    code = read_lines(Path(res["output_dir"]) / "analysis" / "folders.R")
    replaced = ["[metacheck FAIR_repo removed setwd]" in line for line in code]
    assert [i + 1 for i, r in enumerate(replaced) if r] == [4, 7]
    assert [code[1], code[2], code[5]] == [
        "setwd(tempdir())",
        "setwd(old)",
        "setwd('here') in a text\"",
    ]
    scripts = res["scripts"]
    row = scripts[scripts["target_path"] == "analysis/folders.R"]
    assert row["setwd_removed"].tolist() == [2]
    assert row["setwd_paths"].tolist() == ["results"]


# FAIR_repo changes the path a script writes a file to, when another script reads it
def test_fair_repo_changes_the_path_a_script_writes_to_when_another_reads_it(
    proj: Path, fair_run: FairRun
) -> None:
    write_csv(proj / "data" / "clean.csv", {"id": [1, 2, 3], "y": [1, 2, 3]})
    write_lines(
        proj / "Code" / "a_clean.R",
        ['x <- read.csv("data/scores.csv")', 'write.csv(x, "data/clean.csv", row.names = FALSE)'],
    )
    write_lines(proj / "Code" / "b_model.R", ['y <- read.csv("data/clean.csv")'])
    res = fair_run(proj)
    plan = res["plan"]
    targets = plan["written"][plan["file_name"] == "clean.csv"].tolist()
    assert len(targets) == 1
    target = targets[0]
    assert target != "data/clean.csv"
    a = read_lines(Path(res["output_dir"]) / "analysis" / "a_clean.R")
    b = read_lines(Path(res["output_dir"]) / "analysis" / "b_model.R")
    assert any(f'"{target}"' in line for line in a)
    assert any(f'"{target}"' in line for line in b)


# FAIR_repo changes the links in the copied README to the files' new places
def test_fair_repo_changes_the_links_in_the_copied_readme(proj: Path, fair_run: FairRun) -> None:
    (proj / "README.txt").unlink()
    (proj / "man" / "figures").mkdir(parents=True)
    (proj / "man" / "figures" / "fig1.png").write_bytes(bytes([0x89, 0x50, 0x4E, 0x47]))
    write_lines(
        proj / "README.md",
        [
            "# My study",
            "![a figure](man/figures/fig1.png)",
            '<img src="./man/figures/fig1.png" width="100%" />',
            "[the site](https://example.org/man/figures/fig1.png)",
            "[a part](#results)",
        ],
    )
    res = fair_run(proj)
    plan = res["plan"]
    figs = plan["written"][plan["file_name"] == "fig1.png"].tolist()
    assert len(figs) == 1
    fig = figs[0]
    assert fig != "man/figures/fig1.png"
    readme = read_lines(Path(res["output_dir"]) / "README.md")
    assert readme[1] == f"![a figure]({fig})"
    assert readme[2] == f'<img src="{fig}" width="100%" />'
    assert readme[3:5] == [
        "[the site](https://example.org/man/figures/fig1.png)",
        "[a part](#results)",
    ]
    changes = read_lines(Path(res["output_dir"]) / "CHANGES.md")
    assert any("links were changed" in line for line in changes)
    # the README in the folder itself is unchanged
    assert read_lines(proj / "README.md")[1] == "![a figure](man/figures/fig1.png)"


# -- tests added by the Python port (R's tests do not pin these) --------------------------------


# (added) FAIR_repo's defaults: nothing is replaced and no code is run unless asked
def test_fair_repo_defaults_replace_nothing_and_run_no_code(proj: Path, fair_run: FairRun) -> None:
    import inspect

    params = inspect.signature(FAIR_repo).parameters
    assert list(params) == ["path", "output_dir", "paper", "overwrite", "execute"]
    assert params["output_dir"].default is inspect.Parameter.empty
    assert params["paper"].default is None
    assert params["overwrite"].default is False
    assert params["execute"].default is False
    # no code was run, so no library() call was added to a script
    res = fair_run(proj)
    assert res["scripts"]["libraries_added"].fillna("").eq("").all()


# (added) an existing output_dir that is kept returns only the counts and existed = TRUE
def test_fair_repo_existing_output_dir_returns_only_the_counts(
    proj: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    (out / "mine.txt").write_text("keep\n")
    res = FAIR_repo(str(proj), output_dir=str(out), overwrite=False)
    capsys.readouterr()
    assert set(res) == {"output_dir", "n_files_copied", "n_studies", "existed"}
    assert res["output_dir"] == str(out)
    assert res["n_files_copied"] == 0
    assert res["n_studies"] == 0
    assert files_in(out) == ["mine.txt"]
    assert (out / "mine.txt").read_text() == "keep\n"


# (added) a relative output_dir is written relative to the working directory
def test_fair_repo_relative_output_dir(
    proj: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    res = FAIR_repo(str(proj), output_dir="rel_out")
    assert res["output_dir"] == "rel_out"
    assert (tmp_path / "rel_out" / "ro-crate-metadata.json").exists()
    assert (tmp_path / "rel_out" / "data" / "study-scores_data.csv").exists()


# (added) a relative output_dir inside the folder is refused like an absolute one
def test_fair_repo_relative_output_dir_inside_the_folder_is_refused(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(proj)
    with pytest.raises(BAD_ARGUMENT, match="outside the folder"):
        FAIR_repo(str(proj), output_dir="copy")
    with pytest.raises(BAD_ARGUMENT, match="outside the folder"):
        FAIR_repo(".", output_dir=str(proj))
    assert not (proj / "copy").exists()


# (added) the returned dict has the names of R's @returns block
def test_fair_repo_returns_the_dict_of_the_roxygen_returns_block(
    proj: Path, fair_run: FairRun
) -> None:
    res = fair_run(proj)
    assert isinstance(res, dict)
    expected = {
        "output_dir", "n_files_copied", "n_studies", "plan", "scripts", "skipped", "copy_failed",
        "renamed", "identical", "empty_plan", "descriptions", "rocrate", "requirements", "logs",
        "readme_changes",
    }  # fmt: skip
    assert expected <= set(res)
    assert isinstance(res["plan"], pd.DataFrame)
    assert {"file_name", "target_path", "written"} <= set(res["plan"].columns)
    assert list(res["scripts"].columns) == [
        "target_path", "paths_changed", "setwd_removed", "setwd_paths", "libraries_added",
    ]  # fmt: skip
    assert isinstance(res["renamed"], pd.DataFrame)
    assert {"path", "same_as"} <= set(res["identical"].columns)
    assert len(res["skipped"]) == 0
    assert len(res["copy_failed"]) == 0
    assert res["empty_plan"] is False
    assert res["n_studies"] == 1
    # the paths of the metadata written are full paths of files in the copy
    out = Path(res["output_dir"])
    assert Path(res["rocrate"]) == out / "ro-crate-metadata.json"
    assert [Path(p).name for p in res["descriptions"]] == ["dataset_description.json"]
    # the folder has its own README, so only CHANGES.md is written; R code only: no requirements
    assert [Path(p).name for p in res["readme_changes"]] == ["CHANGES.md"]
    assert len(res["requirements"]) == 0
    assert {Path(p).parent.name for p in res["logs"]} == {"logs"}
    assert {"proj.files.json", "log.md"} <= {Path(p).name for p in res["logs"]}
    for p in [res["rocrate"], *res["descriptions"], *res["readme_changes"], *res["logs"]]:
        assert Path(p).is_file()


# (added) the fixture folder helper writes what the R fixture writes
def test_fixture_folder_is_the_r_fixture(tmp_path: Path) -> None:
    d = fair_fixture(tmp_path / "again")
    assert files_in(d) == [
        "Code/analysis.R",
        "README.txt",
        "codebook.csv",
        "data/bom.csv",
        "data/scores.csv",
    ]
    assert (d / "data" / "scores.csv").read_text().splitlines()[:2] == ['"id","score"', "1,2.1"]
    assert (d / "data" / "bom.csv").read_bytes().startswith(BOM)
