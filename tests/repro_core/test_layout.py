"""Where the scripts and files are (port of test-reproducibility_check.R and test-code_check.R)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro.core import (
    _repro_apply_rewrites,
    _repro_neutralise_setwd,
    _repro_slash_paths,
    _repro_strip_setwd_folder,
    repro_materialize_layout,
    repro_missing_inputs,
    repro_rewrite_paths,
    repro_write_scripts,
)
from metacheck.repro.layout import (
    _code_notebook_cell,
    _code_notebook_parts,
    _in_folder,
    _in_renv_folder,
    _repro_apply_folder_vars,
    _repro_fetch_files,
    _repro_fetchable,
    _repro_fill_file_choice,
    _repro_folder_vars,
    _repro_here_roots,
    _repro_here_start,
    _repro_inputs_after_run,
    _repro_inputs_to_report,
    _repro_keep_working_refs,
    _repro_lfs_stopped,
    _repro_lfs_stopped_text,
    _repro_listed_folders,
    _repro_name_corrections,
    _repro_name_report,
    _repro_named_files,
    _repro_names_corrected,
    _repro_neutralise_rprofile,
    _repro_not_downloaded,
    _repro_not_downloaded_text,
    _repro_order_path,
    _repro_package_plan,
    _repro_path_fix_text,
    _repro_place_from_deposit,
    _repro_place_written,
    _repro_placed_text,
    _repro_ref_checkable,
    _repro_rel_from,
    _repro_run_folders,
    _repro_sandbox_names,
    _repro_script_path,
    _repro_script_places,
    _repro_shown_location,
    _repro_slash_report,
    _repro_slash_rows,
    _repro_static_light,
    _repro_unique_script_names,
    _repro_written_later,
    _repro_written_later_text,
)


def _write(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plan(files: list[str], paths: list[str | None] | None = None) -> pd.DataFrame:
    """``.repro_package_plan(data.frame(file_name, file_path))``."""
    cols: dict[str, Any] = {"file_name": files}
    if paths is not None:
        cols["file_path"] = paths
    return _repro_package_plan(pd.DataFrame(cols))


def _targets(*paths: str) -> pd.DataFrame:
    """``data.frame(target_path = c(...))``: a plan with only the target paths."""
    return pd.DataFrame({"target_path": list(paths)})


# renv's files, and a notebook's code -----------------------------------------


def test_in_renv_folder_counts_renv_files_by_path() -> None:
    files = pd.DataFrame(
        {
            "file_path": [
                "renv/activate.R",
                "a/renv/library/x.R",
                "project.zip/renv/activate.R",
                "src/main.R",
                None,
                "a\\renv\\b.R",
                "RENV/up.R",
                "renv.lock",
            ],
            "file_name": [
                "activate.R",
                "x.R",
                "activate.R",
                "main.R",
                "renv/z.R",
                "b.R",
                "up.R",
                "renv.lock",
            ],
        }
    )
    assert _in_renv_folder(files) == [True, True, True, False, True, True, True, False]
    assert _in_renv_folder(pd.DataFrame({"file_path": ["renv/activate.R", "x.R"]})) == [True, False]
    assert _in_renv_folder(pd.DataFrame({"file_path": []})) == []
    assert _in_folder(pd.DataFrame({"file_path": ["data/x.csv"]}), "data") == [True]


def _nb(cells: list[Any], language: str = "python") -> str:
    return json.dumps(
        {
            "metadata": {"kernelspec": {"language": language, "name": language}},
            "cells": [{"cell_type": "code", "source": s} for s in cells],
        }
    )


def test_code_extract_py_commands_of_a_notebook() -> None:
    """code_extract_py (test-code_check.R): what `.code_notebook_parts()` leaves of a notebook."""
    # A whole %%bash cell is not Python; %%time only wraps Python (#568).
    out = _code_notebook_parts(_nb(["%%bash\nls -l\nfor i in 1 2; do echo $i; done"]))
    assert out == {"lines": [], "removed": ["%%bash"]}
    assert _code_notebook_parts(_nb(["%%time\nx = 1"]))["lines"] == ["x = 1", ""]
    # An indented command leaves its block valid.
    out = _code_notebook_parts(_nb(["if a:\n    !pip install b"]))
    assert out["lines"] == ["if a:", "    pass", ""]
    assert out["removed"] == ["!pip install b"]
    # Commands without a %, and what must stay.
    out = _code_notebook_parts(_nb(["cd data\nnp.mean?\ncd = 3\nx = 1"]))
    assert out["lines"] == ["cd = 3", "x = 1", ""]
    assert out["removed"] == ["cd data", "np.mean?"]
    # In an R notebook these are code.
    out = _code_notebook_parts(_nb(["d\n  %>% f()\n!x"], "R"))
    assert out == {"lines": ["d", "  %>% f()", "!x", ""], "removed": []}


def test_code_notebook_parts_cells_and_bad_input() -> None:
    nb = json.dumps(
        {
            "cells": [
                {"cell_type": "markdown", "source": ["# Analysis"]},
                {"cell_type": "code", "source": ["import pandas as pd\n", "df = 1  # never run"]},
                {"cell_type": "code", "source": []},
                {"cell_type": "code", "source": ""},
                {"cell_type": "code", "source": ["%matplotlib inline\n"]},
            ]
        },
        indent=1,
    )
    # the text may come as lines, as code_read() gives it
    out = _code_notebook_parts(nb.split("\n"))
    assert out["lines"] == ["import pandas as pd", "df = 1  # never run", ""]
    assert out["removed"] == ["%matplotlib inline"]
    # not a notebook, no cells, not JSON: empty, not an error
    for text in ['{"foo": 1}', '{"cells": []}', "not json", ""]:
        assert _code_notebook_parts(text) == {"lines": [], "removed": []}
    # the kernel is read from its name too
    r_nb = json.dumps(
        {
            "metadata": {"kernelspec": {"name": "ir"}},
            "cells": [{"cell_type": "code", "source": "!x"}],
        }
    )
    assert _code_notebook_parts(r_nb)["lines"] == ["!x", ""]


def test_code_notebook_cell_help_and_first_line() -> None:
    assert _code_notebook_cell(["", "%%capture", "x = 1"]) == {
        "lines": ["", "x = 1"],
        "removed": ["%%capture"],
    }
    assert _code_notebook_cell(["  %%script bash", "ls"]) == {
        "lines": [],
        "removed": ["%%script bash"],
    }
    assert _code_notebook_cell([]) == {"lines": [], "removed": []}


# names the scripts spell otherwise than the deposit --------------------------


def test_name_corrections_of_a_script_with_no_rows_joins_the_others() -> None:
    """a script whose only reference is left out joins the others' path rows (#568)"""
    plan = pd.DataFrame(
        {
            "file_name": ["relative_cover.csv", "b.csv", "b.csv"],
            "target_path": ["d/relative_cover.csv", "x/b.csv", "y/b.csv"],
        }
    )
    none = repro_rewrite_paths(['f <- "b.csv"', "d <- read.csv(f)"], "a.R", plan)
    assert len(none) == 0
    one = repro_rewrite_paths('d <- read.csv("relative cover.csv")', "b.R", plan)
    corrections = _repro_name_corrections({"b.R": one, "a.R": none})
    assert corrections["name_in_deposit"].tolist() == ["relative_cover.csv"]
    assert corrections["name_in_code"].tolist() == ["relative cover.csv"]
    assert corrections["file_name"].tolist() == ["b.R"]
    assert _repro_names_corrected(corrections, ["a.R", "b.R"]) == [
        None,
        "relative cover.csv -> relative_cover.csv",
    ]
    # no rows, no column, nothing corrected: an empty table with the columns
    empty = _repro_name_corrections({"a.R": none, "c.R": None, "d.R": pd.DataFrame({"ref": ["x"]})})
    assert list(empty.columns) == ["file_name", "name_in_code", "name_in_deposit"]
    assert len(empty) == 0
    assert _repro_name_report(empty) is None


def test_name_report_tells_the_authors() -> None:
    corrections = pd.DataFrame(
        {
            "file_name": ["a.R", "a.R"],
            "name_in_code": ["x y.csv", "Z.csv"],
            "name_in_deposit": ["x_y.csv", "z.csv"],
        }
    )
    assert _repro_names_corrected(corrections, ["a.R"]) == ["x y.csv -> x_y.csv; Z.csv -> z.csv"]
    report = _repro_name_report(corrections, execute=True)
    assert report is not None
    assert "**2 file names in the code are not the same as in the deposit**" in report[0]
    assert "To run the code we corrected this in a copy" in report[0]
    assert "To run the code we would correct" in _repro_name_report(corrections)[0]  # type: ignore[index]
    assert list(report[1].data.columns) == ["File", "Name in the code", "Name in the deposit"]
    one = _repro_name_report(corrections.iloc[:1])
    assert one is not None
    assert "**1 file name in the code is not the same" in one[0]


# files the repository lists but that were not downloaded ----------------------


def test_fetchable_is_a_file_address_or_a_member_of_an_archive() -> None:
    s = pd.DataFrame(
        {
            "file_location": [None, "x/a.csv", "", None, None, None],
            "file_url": ["u", "u", None, None, "", None],
            "archive_url": [None, None, "z", "z", None, "z"],
            "archive_member": [None, None, "m", None, None, ""],
        }
    )
    assert _repro_fetchable(s) == [True, False, True, False, False, True]
    assert _repro_fetchable(None) == []
    assert _repro_fetchable(s.iloc[0:0]) == []
    # a listing without the address columns has nothing to fetch
    assert _repro_fetchable(pd.DataFrame({"file_name": ["a", "b"]})) == [False, False]


def test_named_files_finds_the_files_the_code_names_by_name_or_extension() -> None:
    """.repro_named_files finds the files the code names, by name or extension (#568)"""
    s = pd.DataFrame(
        {
            "file_name": ["fig.png", "call.wav", "other.wav", "notes.pdf", "here.csv"],
            "file_url": ["u1", "u2", "u3", "u4", None],
            "file_location": [None, None, None, None, "x/here.csv"],
        }
    )
    code = ['img <- png::readPNG("figs/fig.png")', r'w <- list.files("audio", pattern = "\\.wav$")']
    assert _repro_named_files(s, code) == [True, True, True, False, False]
    assert _repro_named_files(s, "x <- 1") == [False] * 5
    assert _repro_named_files(s, 'glob.glob("*.wav")') == [False, True, True, False, False]
    assert _repro_named_files(s, r're.search(r"\.wav$", f)') == [False, True, True, False, False]
    # nothing to fetch, or no code: the answer is the rows to fetch / none
    assert _repro_named_files(s, []) == _repro_fetchable(s)
    assert _repro_named_files(None, "x") == []


def test_named_files_survives_bytes_that_are_not_utf8() -> None:
    """code and messages with bytes that are not UTF-8 do not stop the module (#568)"""
    latin1 = b"# caf\xe9".decode("utf-8", "surrogateescape")
    s = pd.DataFrame(
        {
            "file_name": ["fig.png", "notes.pdf"],
            "file_url": ["u1", "u2"],
            "file_location": [None, None],
        }
    )
    assert _repro_named_files(s, [latin1, 'img <- png::readPNG("fig.png")']) == [True, False]
    # a name with other characters than letters and digits is searched for in the text
    odd = pd.DataFrame({"file_name": ["a b.csv"], "file_url": ["u"], "file_location": [None]})
    assert _repro_named_files(odd, 'read.csv("a b.csv")') == [True]
    assert _repro_named_files(odd, 'read.csv("a_b.csv")') == [False]


def test_fetch_files_asks_for_the_rows_within_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck.archives import download

    asked: dict[str, Any] = {}

    def fake(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        asked.update(kwargs, names=files["file_name"].tolist())
        out = files.copy()
        out["file_location"] = ["/tmp/" + n for n in files["file_name"]]
        return out

    monkeypatch.setattr(download, "download_repo_files", fake)
    s = pd.DataFrame(
        {
            "file_name": ["a.png", "b.png", "c.png"],
            "repo_url": ["r1", "r1", "r2"],
            "file_location": pd.Series([None, "/x/b.png", None], dtype="string"),
        }
    )
    out = _repro_fetch_files(s, [True, False, True], 50, 200, cache=True, skip_on_api_limit=True)
    assert out["file_location"].tolist() == ["/tmp/a.png", "/x/b.png", "/tmp/c.png"]
    assert asked["names"] == ["a.png", "c.png"]
    assert (asked["max_file_size"], asked["max_download_size"]) == (50, 200)
    assert asked["repo_file_counts"] == {"r1": 2, "r2": 1}
    assert asked["cache"] is True
    assert asked["skip_on_api_limit"] is True
    # the listing is not changed in place
    assert s["file_location"].tolist()[0] is pd.NA or s["file_location"].isna().iloc[0]
    # no row to fetch: nothing is asked
    asked.clear()
    assert _repro_fetch_files(s, [False, False, False]) is s
    assert not asked


def test_fetch_files_keeps_the_listing_when_the_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives import download

    def boom(files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
        raise RuntimeError("no network")

    monkeypatch.setattr(download, "download_repo_files", boom)
    s = pd.DataFrame({"file_name": ["a.png"], "file_location": pd.Series([None], dtype="string")})
    out = _repro_fetch_files(s, [True])
    assert out["file_location"].isna().all()
    monkeypatch.setattr(download, "download_repo_files", lambda files, **kw: None)
    assert _repro_fetch_files(s, [True]) is s
    # a listing without file_location gets the column
    monkeypatch.setattr(
        download,
        "download_repo_files",
        lambda files, **kw: files.assign(file_location=["/tmp/a.png"]),
    )
    out = _repro_fetch_files(pd.DataFrame({"file_name": ["a.png"]}), [True])
    assert out["file_location"].tolist() == ["/tmp/a.png"]


def test_not_downloaded_script_that_stopped_on_a_file_not_downloaded() -> None:
    """a script that stops on a file we did not download was not tried (#568)"""
    mb = 1024**2
    s = pd.DataFrame(
        {
            "file_name": ["BF.rds", "BF.rds", "occurrences.csv", "a.csv", "a.csv"],
            "file_path": [
                "Data & code.zip/Data & code/Outputs/BF/BF.rds",
                "Detectability models/BF.rds",
                "SCAN.zip/SCAN1/occurrences.csv",
                "x/a.csv",
                "y/a.csv",
            ],
            "file_location": [None, "/tmp/BF.rds", None, None, "/tmp/a.csv"],
            "file_size": [652.8 * mb, 2e5, 293 * mb, 10, 10],
        }
    )
    rr = pd.DataFrame(
        {
            "file_name": ["fit.R", "detect.R", "occ.py", "plain.R", "ok.R"],
            "outcome": ["errored", "errored", "errored", "errored", "ran_ok"],
            "error": [
                'Error in gzfile(file, "rb") : cannot open the connection',
                "Error in gzfile(file, \"rb\") : cannot open compressed file 'Detectability models/BF.rds', "
                "probable reason 'No such file or directory'",
                "FileNotFoundError: [Errno 2] No such file or directory: '../SCAN.zip/SCAN1/occurrences.csv'",
                "Error in file(file, \"rt\") : cannot open file 'a.csv': No such file or directory",
                "",
            ],
            "stderr": [
                "cannot open compressed file 'Outputs/BF/BF.rds', probable reason 'No such file or directory'",
                "",
                "",
                "",
                "",
            ],
        }
    )
    nd = _repro_not_downloaded(rr, s)
    # fit.R and occ.py needed files that were not downloaded; detect.R's file was
    # downloaded, so it looked in the wrong place; for plain.R one a.csv was downloaded
    assert set(nd["script"]) == {"fit.R", "occ.py"}
    assert nd.loc[nd["script"] == "fit.R", "file"].tolist() == [
        "Data & code.zip/Data & code/Outputs/BF/BF.rds"
    ]
    assert list(nd.columns) == ["script", "file", "size"]
    assert len(_repro_not_downloaded(rr, s.iloc[0:0])) == 0
    assert len(_repro_not_downloaded(None, s)) == 0
    assert len(_repro_not_downloaded(rr, s.assign(file_location="/tmp/x"))) == 0
    txt = _repro_not_downloaded_text(nd, max_file_size=100)
    assert txt is not None
    assert "2 scripts could not be tried" in txt
    assert "Outputs/BF/BF.rds (652.8 MB)" in txt
    assert "files over 100 MB are not downloaded" in txt
    assert _repro_not_downloaded_text(nd.iloc[0:0]) is None
    # one script, one file under the limit: no size, no note on the limit
    small = pd.DataFrame({"script": ["a.R"], "file": ["d.csv"], "size": [10.0]})
    one = _repro_not_downloaded_text(small)
    assert one is not None
    assert (
        "**1 script could not be tried, because a file it needs was not downloaded**: a.R." in one
    )
    assert "The file: d.csv." in one
    assert "It is not counted as a script that did not reproduce." in one


# files written during a run ----------------------------------------------------


def test_file_written_during_the_run_is_found_and_copied_to_where_a_script_looked(
    tmp_path: Path,
) -> None:
    """a file written during the run is found, and copied to where a script looked (#568)"""
    root = tmp_path
    (root / "a").mkdir()
    (root / "b").mkdir()
    _write(root / "b" / "input0.txt", "old\n")  # the deposit's own copy elsewhere
    before = _repro_sandbox_names(root)
    _write(root / "a" / "input0.txt", "new\n")  # written during the run
    _write(root / "a" / "Ha_daily.Rdata")
    after = _repro_sandbox_names(root)
    assert before == ["b/input0.txt"]
    assert after == ["a/Ha_daily.Rdata", "a/input0.txt", "b/input0.txt"]
    res = pd.DataFrame(
        {
            "file_name": ["s1.py", "s2.R", "s3.R"],
            "outcome": ["errored", "errored", "errored"],
            "error": ["", "", ""],
            "stderr": [
                "FileNotFoundError: [Errno 2] No such file or directory: 'input0.txt'",
                "cannot open compressed file 'Ha_daily.Rdata', probable reason 'No such file or directory'",
                "cannot open file 'deposit.csv': No such file or directory",
            ],
        }
    )
    later = _repro_written_later(res, before, after)
    assert later == {"s1.py": ["input0.txt"], "s2.R": ["Ha_daily.Rdata"]}
    run_tbl = pd.DataFrame(
        {"file_name": ["s1.py", "s2.R"], "run_dir": [str(root / "a"), str(root / "c")]}
    )
    placed = _repro_place_written(later, res, run_tbl, root, before, after)
    # s1 looks where the file now is; s2 gets a copy in its own folder
    assert placed["script"].tolist() == ["s2.R"]
    assert placed["to"].tolist() == ["c/Ha_daily.Rdata"]
    assert placed["from"].tolist() == ["a/Ha_daily.Rdata"]
    assert list(placed.columns) == ["script", "file", "from", "to"]
    assert (root / "c" / "Ha_daily.Rdata").exists()
    text = _repro_written_later_text(later, ["s2.R"], placed)
    assert "copied it there" in text
    assert "**2 scripts stopped on a file another script writes**" in text
    assert "`s1.py` (input0.txt); `s2.R` (Ha_daily.Rdata)." in text
    assert "Then this one ran to the end: `s2.R`." in text
    # without a run or without a copy
    assert _repro_written_later(None, before, after) == {}
    assert _repro_written_later(res, before, before) == {}
    assert _repro_written_later_text({}) == ""
    assert "copied" not in _repro_written_later_text(later)
    assert len(_repro_place_written({}, res, run_tbl, root, before, after)) == 0


def test_sandbox_names_leave_out_libraries_and_hidden_files(tmp_path: Path) -> None:
    _write(tmp_path / "code" / "a.R")
    _write(tmp_path / "_rlib" / "p" / "x.R")
    _write(tmp_path / "_lib" / "p" / "x.R")
    _write(tmp_path / "_pylib2" / "p" / "x.py")
    _write(tmp_path / "_other" / "x.R")
    _write(tmp_path / ".hidden" / "x.R")
    _write(tmp_path / ".Rprofile")
    assert _repro_sandbox_names(tmp_path) == ["_other/x.R", "code/a.R"]


def test_file_the_deposit_holds_elsewhere_is_copied_to_where_a_script_looked(
    tmp_path: Path,
) -> None:
    """a file the deposit holds elsewhere is copied to where a script looked (#575, item 2)"""
    root = tmp_path
    (root / "code").mkdir()
    (root / "b").mkdir()
    _write(root / "HMSC_YXdata.csv", "a,b\n")
    _write(root / "dup.csv", "1\n")
    _write(root / "b" / "dup.csv", "1\n")
    _write(root / "two.csv", "1\n")
    _write(root / "b" / "two.csv", "2\n")
    _write(root / "_lib" / "p" / "lib.csv")
    before = _repro_sandbox_names(root)
    assert not any(b.startswith("_lib/") for b in before)  # the R library is no file of the deposit
    res = pd.DataFrame(
        {
            "file_name": ["S1.R", "S2.R", "S3.R", "S4.R", "S5.R"],
            "outcome": "errored",
            "error": [
                'Error in file(file, "rt") : cannot open the connection',
                "cannot open file './data/dup.csv': No such file or directory",
                "cannot open file 'two.csv': No such file or directory",
                "cannot open file '../../out.csv': No such file or directory",
                "cannot open file 'lib.csv': No such file or directory",
            ],
            "stderr": [
                "cannot open file './data/HMSC_YXdata.csv': No such file or directory",
                "",
                "",
                "",
                "",
            ],
        }
    )
    run_tbl = pd.DataFrame({"file_name": res["file_name"], "run_dir": str(root / "code")})
    placed = _repro_place_from_deposit(res, run_tbl, root, before)
    # one file of the name, or identical copies; not two different files, a path
    # outside the sandbox, or a file of the library
    assert placed["script"].tolist() == ["S1.R", "S2.R"]
    assert placed["to"].tolist() == ["code/data/HMSC_YXdata.csv", "code/data/dup.csv"]
    assert (root / "code" / "data" / "HMSC_YXdata.csv").exists()
    text = _repro_placed_text(placed)
    assert "we copied them there" in text
    assert "**2 scripts looked for 2 files in another folder than the deposit has them**" in text
    assert "`HMSC_YXdata.csv` to `code/data/HMSC_YXdata.csv` for `S1.R`" in text
    # placed once: the second time there is nothing left to copy
    assert len(_repro_place_from_deposit(res, run_tbl, root, before)) == 0
    assert _repro_placed_text(placed.iloc[0:0]) == ""
    assert _repro_placed_text(None) == ""
    one = _repro_placed_text(placed.iloc[:1])
    assert "**A script looked for a file in another folder than the deposit has it**" in one
    assert "before running it once more we copied it there" in one
    # no run, or no sandbox files: nothing to copy
    assert len(_repro_place_from_deposit(None, run_tbl, root, before)) == 0
    assert len(_repro_place_from_deposit(res, run_tbl, root, [])) == 0


def test_place_files_gives_no_copy_for_a_path_on_the_authors_computer(tmp_path: Path) -> None:
    root = tmp_path
    _write(root / "d" / "x.csv")
    before = _repro_sandbox_names(root)
    res = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R", "c.R"],
            "outcome": "errored",
            "error": [
                "cannot open file 'C:/Users/me/x.csv': No such file or directory",
                "cannot open file '/home/me/x.csv': No such file or directory",
                "cannot open file '/sandbox/e/x.csv': No such file or directory",
            ],
            "stderr": "",
        }
    )
    run_tbl = pd.DataFrame(
        {"file_name": res["file_name"], "run_dir": [str(root)] * 2 + ["/elsewhere"]}
    )
    placed = _repro_place_from_deposit(res, run_tbl, root, before)
    # only the /sandbox/ path, which needs no run folder
    assert placed["script"].tolist() == ["c.R"]
    assert placed["to"].tolist() == ["e/x.csv"]
    # a script from a folder outside the sandbox cannot be placed
    res2 = res.iloc[[0]].assign(error="cannot open file 'x.csv': No such file or directory")
    run2 = pd.DataFrame({"file_name": ["a.R"], "run_dir": ["/elsewhere"]})
    assert len(_repro_place_from_deposit(res2, run2, root, before)) == 0


def test_script_that_stopped_on_a_git_lfs_pointer_stopped_on_a_missing_file(tmp_path: Path) -> None:
    """a script that stopped on a Git LFS pointer stopped on a missing file (#568)"""
    root = tmp_path
    _write(
        root / "data" / "results.rds",
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:625d8398ce0000000000000000000000000000000000000000000000000000\n"
        "size 123456\n",
    )
    _write(root / "data" / "ok.csv", "a,b\n")
    _write(root / "_rlib" / "p" / "lfs.rds", "version https://git-lfs.github.com/spec/v1\n")
    res = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R", "c.R"],
            "outcome": ["errored", "errored", "ran_ok"],
            "error": [
                'Error in readRDS(here::here("data", "results.rds")) : unknown input format',
                "Error in f() : something else",
                "",
            ],
            "stderr": "",
        }
    )
    st = _repro_lfs_stopped(res, root)
    assert st == {"a.R": ["results.rds"]}
    text = _repro_lfs_stopped_text(st)
    assert "only as a Git LFS pointer" in text
    assert "**1 script stopped while reading" in text
    assert "`results.rds`" in text
    assert "`a.R`" in text
    assert _repro_lfs_stopped(res.iloc[1:], root) == {}
    assert _repro_lfs_stopped(None, root) == {}
    assert _repro_lfs_stopped_text({}) == ""
    # a big file starting like a pointer is no pointer
    _write(root / "big.txt", "version https://git-lfs.github.com/spec/v1\n" + "x" * 2000)
    assert _repro_lfs_stopped(res, root) == {"a.R": ["results.rds"]}


# what is checked before the code runs ------------------------------------------


def test_ref_checkable_keeps_only_complete_literal_names() -> None:
    """.repro_ref_checkable keeps only complete literal names (#568)"""
    d = pd.DataFrame(
        {
            "ref": [
                "data/x.csv",
                "{name}.csv",
                "out/%s.pdf",
                "_field.csv",
                ".png",
                "input$a",
                "paste0(x, 'y.csv')",
                "{path}/weights/model.pth",
                "log(4runs)10k_3p.p",
                "fst=0.2_x.tab",
                "data\\\\win.csv",
            ],
            "basename": [
                "x.csv",
                "{name}.csv",
                "%s.pdf",
                "_field.csv",
                ".png",
                "input$a",
                "y.csv",
                "model.pth",
                "log(4runs)10k_3p.p",
                "fst=0.2_x.tab",
                "win.csv",
            ],
            "is_call": [False] * 6 + [True] + [False] * 4,
        }
    )
    assert _repro_ref_checkable(d) == [
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    # Python rows can lack is_call
    assert _repro_ref_checkable(pd.DataFrame({"ref": ["x.csv"], "basename": ["x.csv"]})) == [True]
    # a name the script builds, in any case
    assert _repro_ref_checkable(
        pd.DataFrame({"ref": ["/X.csv"], "basename": ["x.csv"]}), ["x.csv"]
    ) == [False]
    assert _repro_ref_checkable(d.iloc[0:0]) == []


def test_inputs_to_report_keeps_what_a_run_cannot_show() -> None:
    """.repro_inputs_to_report keeps what a run cannot show (#568)"""
    m = pd.DataFrame(
        {
            "basename": ["a.csv", "b.csv", "c.csv", "d.csv"],
            "status": ["absent", "withheld_size", "in_archive", "absent"],
            "detail": "",
            "similar_to": [None, None, None, "D_.csv"],
        }
    )
    assert _repro_inputs_to_report(m, False) is m
    assert _repro_inputs_to_report(m, True)["basename"].tolist() == ["b.csv", "c.csv", "d.csv"]
    assert len(_repro_inputs_to_report(m.iloc[0:0], True)) == 0


def test_inputs_after_run_keeps_only_files_of_scripts_that_did_not_run_through() -> None:
    """.repro_inputs_after_run keeps only files of scripts that did not run through (#568)"""
    m = pd.DataFrame(
        {
            "basename": ["a.csv", "b.csv", "c.csv"],
            "status": "absent",
            "detail": "",
            "similar_to": [None, None, None],
        }
    )
    unresolved = [["a.csv"], ["b.csv"], ["c.csv"], []]
    files = ["ok.R", "stops.R", "nopkg.R", "other.R"]
    rr = pd.DataFrame(
        {"file_name": files, "outcome": ["ran_ok", "errored", "dependency_unavailable", "ran_ok"]}
    )
    assert _repro_inputs_after_run(m, unresolved, files, rr)["basename"].tolist() == [
        "b.csv",
        "c.csv",
    ]
    rr["outcome"] = ["ran_ok", "run_by_source", "ran_ok", "ran_ok"]
    assert len(_repro_inputs_after_run(m, unresolved, files, rr)) == 0
    rr["outcome"] = ["ran_ok", "run_by_import", "ran_ok", "ran_ok"]
    assert len(_repro_inputs_after_run(m, unresolved, files, rr)) == 0
    # no run, nothing changes; a script with no result counts as not run
    assert _repro_inputs_after_run(m, unresolved, files, None) is m
    rest = rr[rr["file_name"] != "ok.R"]
    assert _repro_inputs_after_run(m, unresolved, files, rest)["basename"].tolist() == ["a.csv"]


def test_static_light_follows_the_missing_inputs_and_the_other_checks() -> None:
    """.repro_static_light follows the missing inputs and the other checks (#568)"""
    none = pd.DataFrame(
        {"basename": pd.Series([], dtype="string"), "status": pd.Series([], dtype="string")}
    )
    withheld = pd.DataFrame({"basename": ["a.csv"], "status": ["withheld_size"]})
    mixed = pd.DataFrame({"basename": ["a.csv", "b.csv"], "status": ["withheld_size", "absent"]})
    assert _repro_static_light(none, True) == "green"
    assert _repro_static_light(none, False) == "yellow"
    assert _repro_static_light(withheld, True) == "red"
    assert _repro_static_light(mixed, True) == "yellow"


# the package layout ---------------------------------------------------------------


def test_package_plan_gives_every_file_its_own_path() -> None:
    """.repro_package_plan gives every file its own path"""
    structure_df = pd.DataFrame(
        {
            "file_name": ["log.csv", "log.csv", "helpers.py", "a.csv"],
            "file_path": [
                "raw_data/scan1/log.csv",
                "raw_data/scan2/log.csv",
                "code/utils/helpers.py",
                None,
            ],
            "file_location": ["/x/1/log.csv", "/x/2/log.csv", "/x/helpers.py", "/x/a.csv"],
        }
    )
    plan = _repro_package_plan(structure_df)
    assert list(plan.columns) == ["file_name", "current_path", "target_path", "source"]
    assert plan["target_path"].tolist() == [
        "raw_data/scan1/log.csv",
        "raw_data/scan2/log.csv",
        "code/utils/helpers.py",
        "a.csv",
    ]
    assert plan["current_path"].tolist() == plan["target_path"].tolist()
    # each row keeps its own file, also when names repeat
    assert plan["source"].tolist()[:2] == ["/x/1/log.csv", "/x/2/log.csv"]
    assert len(_repro_package_plan(None)) == 0
    assert list(_repro_package_plan(None).columns) == [
        "file_name",
        "current_path",
        "target_path",
        "source",
    ]


def test_package_plan_cleans_the_paths() -> None:
    s = pd.DataFrame(
        {
            "file_name": ["a.csv", "b.csv", "c.csv", "d.csv", "e.csv", "f.csv"],
            "file_path": [
                "./data/a.csv",
                "/data/b.csv",
                "..\\up\\c.csv",
                "x\\..\\d.csv",
                "dup/e.csv",
                "dup/e.csv",
            ],
        }
    )
    plan = _repro_package_plan(s)
    # a path that goes up is left out, a repeated path is one row
    assert plan["target_path"].tolist() == ["data/a.csv", "data/b.csv", "dup/e.csv"]
    # without a path column, the file name is the path
    no_path = _repro_package_plan(pd.DataFrame({"file_name": ["a.csv", "b/c.csv"]}))
    assert no_path["target_path"].tolist() == ["a.csv", "b/c.csv"]
    assert len(_repro_package_plan(pd.DataFrame({"x": [1]}))) == 0
    # no file_location: no source
    no_loc = _repro_package_plan(pd.DataFrame({"file_name": ["a.csv"]}))
    assert no_loc["source"].isna().all()


def test_package_plan_is_a_plan_of_the_layout_function(tmp_path: Path) -> None:
    """repro_materialize_layout copies each file of the package layout from its own source"""
    d = tmp_path / "d"
    _write(d / "1" / "log.csv", "one\n")
    _write(d / "2" / "log.csv", "two\n")
    structure_df = pd.DataFrame(
        {
            "file_name": ["log.csv", "log.csv"],
            "file_path": ["scan1/log.csv", "scan2/log.csv"],
            "file_location": [str(d / "1" / "log.csv"), str(d / "2" / "log.csv")],
        }
    )
    plan = _repro_package_plan(structure_df)
    root = tmp_path / "root"
    repro_materialize_layout(plan, structure_df, root)
    assert (root / "scan1" / "log.csv").read_text() == "one\n"
    assert (root / "scan2" / "log.csv").read_text() == "two\n"


def test_package_plan_runs_through_rewrite_paths_and_write_scripts(tmp_path: Path) -> None:
    structure_df = pd.DataFrame(
        {
            "file_name": ["x.csv", "main.R", "notes.csv"],
            "file_path": ["processed_data/fig7/x.csv", "code/main.R", "data/notes.csv"],
            "file_location": [None, None, None],
        }
    )
    plan = _repro_package_plan(structure_df)
    code = [
        "a <- read.csv('data/notes.csv')",
        "b <- read.csv('C:/Users/author/project/processed_data/fig7/x.csv')",
    ]
    rw = repro_rewrite_paths(code, "main.R", plan, "R", script_path="code/main.R")
    rw = _repro_keep_working_refs(rw, plan, "code/main.R", ".")
    assert rw.set_index("ref")["as_written"].to_dict() == {
        "data/notes.csv": True,
        "C:/Users/author/project/processed_data/fig7/x.csv": False,
    }
    root = tmp_path / "sandbox"
    out = repro_write_scripts(
        {"main.R": code},
        {"main.R": rw},
        plan,
        root,
        script_paths=["code/main.R"],
        run_dirs=["."],
    )
    written = Path(out["script_path"].iloc[0])
    assert written == root / "code" / "main.R"
    text = written.read_text()
    assert "read.csv('data/notes.csv')" in text
    assert "read.csv('processed_data/fig7/x.csv')" in text
    assert "C:/Users" not in text


def test_a_file_found_only_inside_an_archive_is_a_missing_input_in_the_package_layout() -> None:
    """a file found only inside an archive is a missing input in the package layout (#520, item 2)"""
    structure_df = pd.DataFrame(
        {
            "file_name": ["Humor study 4a.csv", "main.R", "notes.csv"],
            "file_path": [
                "data.zip/data/download 1a/Humor study 4a.csv",
                "main.R",
                "data/notes.csv",
            ],
            "file_location": [None, None, None],
        }
    )
    plan = _repro_package_plan(structure_df)
    code = [
        "d <- data.table::fread('data/download 1a/Humor study 4a.csv')",
        "n <- read.csv('data/notes.csv')",
    ]
    rw = _repro_keep_working_refs(repro_rewrite_paths(code, "main.R", plan, "R"), plan, "main.R")
    inside = rw[rw["basename"] == "humor study 4a.csv"]
    assert len(inside) == 1
    assert not inside["as_written"].any()
    # a file at its own path still works as written
    assert rw.loc[rw["basename"] == "notes.csv", "as_written"].tolist() == [True]


def test_keep_working_refs_leaves_a_path_that_works_in_the_package_as_written() -> None:
    """.repro_keep_working_refs leaves a path that works in the package as written"""
    plan = pd.DataFrame(
        {
            "file_name": ["log.csv", "log.csv", "notes.txt"],
            "target_path": [
                "raw_data/scan1/log.csv",
                "raw_data/scan2/log.csv",
                "raw_data/notes.txt",
            ],
        }
    )
    code = [
        "a = open('raw_data/scan1/log.csv')",
        "b = open('C:/Users/author/project/raw_data/notes.txt')",
    ]
    rw = repro_rewrite_paths(code, "code/main.py", plan, "Python")
    rw = _repro_keep_working_refs(rw, plan)
    works = rw[rw["ref"] == "raw_data/scan1/log.csv"].iloc[0]
    assert works["as_written"]
    assert not works["ambiguous"]  # 2 files are named log.csv
    assert works["target"] == "raw_data/scan1/log.csv"
    abs_ = rw[rw["basename"] == "notes.txt"].iloc[0]
    assert not abs_["as_written"]  # rewritten to where the file is
    assert abs_["target"] == "raw_data/notes.txt"


def test_keep_working_refs_rewrites_only_what_cannot_work_as_written() -> None:
    """.repro_keep_working_refs rewrites only what cannot work as written"""
    plan = pd.DataFrame(
        {
            "file_name": ["x.csv", "helpers.R", "scores.csv"],
            "target_path": ["processed_data/fig7/x.csv", "code/helpers.R", "raw_data/scores.csv"],
        }
    )
    code = [
        "a <- read.csv(file.path(base, 'fig7/x.csv'))",
        "source('helpers.R')",
        "d <- read.csv('../raw_data/scores.csv')",
    ]
    rw = repro_rewrite_paths(code, "analysis.R", plan, "R")
    rw = _repro_keep_working_refs(rw, plan, "code/analysis.R")
    # a partial path the code joins with a folder: left as written (rewriting it gave
    # processed_data/fig7/processed_data/fig7/x.csv)
    part = rw[rw["ref"] == "fig7/x.csv"].iloc[0]
    assert part["as_written"]
    assert part["target"] == "fig7/x.csv"
    # relative to the script's own folder: the path from the top
    assert rw.loc[rw["ref"] == "helpers.R", "target"].tolist() == ["code/helpers.R"]
    assert rw.loc[rw["ref"] == "../raw_data/scores.csv", "target"].tolist() == [
        "raw_data/scores.csv"
    ]
    assert not rw.loc[rw["ref"].isin(["helpers.R", "../raw_data/scores.csv"]), "as_written"].any()


def test_keep_working_refs_points_a_path_that_works_nowhere_to_the_file_of_its_name() -> None:
    """.repro_keep_working_refs points a path that works nowhere to the file of its name (#568)"""
    # the authors moved or left out a folder when they deposited
    plan = pd.DataFrame(
        {
            "file_name": ["VEG.csv", "forest_age.R"],
            "target_path": ["exploratory/VEG.csv", "exploratory/forest_age.R"],
        }
    )
    code = ['d <- read.csv("./data/VEG.csv")', 'e <- read.csv("data/absent.csv")']
    rw = repro_rewrite_paths(
        code, "forest_age.R", plan, "R", script_path="exploratory/forest_age.R"
    )
    rw = _repro_keep_working_refs(rw, plan, "exploratory/forest_age.R", "exploratory")
    veg = rw[rw["ref"] == "./data/VEG.csv"].iloc[0]
    assert not veg["as_written"]
    # counted from the folder the script runs from
    assert veg["target"] == "VEG.csv"
    # no file of its name anywhere: as written, and a missing input
    absent = rw[rw["ref"] == "data/absent.csv"].iloc[0]
    assert absent["as_written"]
    assert not absent["matched"]
    # the report tells the authors
    txt = _repro_path_fix_text({"forest_age.R": rw}, execute=True)
    assert "**1 data path in the code does not work in the repository as deposited.**" in txt
    assert "`./data/VEG.csv` to `VEG.csv` (in forest_age.R)" in txt
    assert "Every data path" in _repro_path_fix_text({"a.R": rw.iloc[0:0]})


def test_archive_case_missing_inputs_status() -> None:
    """a file found only inside an archive is a missing input in the package layout (#520, item 2)"""
    structure_df = pd.DataFrame(
        {
            "file_name": ["Humor study 4a.csv", "main.R", "notes.csv"],
            "file_path": [
                "data.zip/data/download 1a/Humor study 4a.csv",
                "main.R",
                "data/notes.csv",
            ],
            "file_location": [None, None, None],
        }
    )
    plan = _repro_package_plan(structure_df)
    code = [
        "d <- data.table::fread('data/download 1a/Humor study 4a.csv')",
        "n <- read.csv('data/notes.csv')",
        "o <- read.csv(file.path(base, 'download 1a/Humor study 4a.csv'))",
    ]
    rw = _repro_keep_working_refs(repro_rewrite_paths(code, "main.R", plan, "R"), plan, "main.R")
    inside = rw[rw["basename"] == "humor study 4a.csv"]
    # both the full path and the part a folder of the code's own is added to
    assert len(inside) == 2
    assert not inside["matched"].any()
    assert not inside["as_written"].any()
    mi = repro_missing_inputs("Humor study 4a.csv", plan, structure_df)
    assert mi["status"].tolist() == ["in_archive"]
    assert "only inside data.zip; unpack it so the code finds it" in mi["detail"].iloc[0]
    # a name that also exists outside the archive is not "in_archive"
    extra = pd.DataFrame(
        {
            "file_name": ["Humor study 4a.csv"],
            "file_path": ["Humor study 4a.csv"],
            "file_location": [None],
        }
    )
    both = pd.concat([structure_df, extra], ignore_index=True)
    assert repro_missing_inputs("Humor study 4a.csv", plan, both)["status"].tolist() == [
        "in_repo_not_downloaded"
    ]


def test_keep_working_refs_counts_from_where_the_script_runs() -> None:
    """.repro_keep_working_refs counts from where the script runs (#568)"""
    plan = _plan(["d.csv", "h.R"], ["top/data/d.csv", "top/R/h.R"])

    def keep(code: str, run_dir: str) -> pd.Series:
        rw = repro_rewrite_paths(code, "m.R", plan)
        return _repro_keep_working_refs(rw, plan, "top/R/m.R", run_dir).iloc[0]

    # works as written from the folder it runs in
    assert keep('read.csv("data/d.csv")', "top")["as_written"]
    # a file named from elsewhere is rewritten, counted from that folder
    abs_ = keep('read.csv("/home/me/data/d.csv")', "top/R")
    assert abs_["target"] == "../data/d.csv"
    assert not abs_["as_written"]
    # beside the script, while it runs from above it
    assert keep('source("h.R")', "top")["target"] == "R/h.R"


def test_a_file_at_the_top_named_as_it_is_there_counts_from_where_the_script_runs() -> None:
    """a file at the top named as it is there counts from where the script runs (#568)"""
    plan = _plan(["q.csv", "a.Rmd"], ["q.csv", "g/x.zip/x-1/a.Rmd"])
    rw = _repro_keep_working_refs(
        repro_rewrite_paths('d <- read.csv("./q.csv")', "a.Rmd", plan),
        plan,
        "g/x.zip/x-1/a.Rmd",
        "g/x.zip/x-1",
    ).iloc[0]
    assert not rw["as_written"]
    assert rw["target"] == "../../../q.csv"
    # from the top it works as written
    top = _repro_keep_working_refs(
        repro_rewrite_paths('d <- read.csv("./q.csv")', "a.Rmd", plan),
        plan,
        "g/x.zip/x-1/a.Rmd",
        ".",
    ).iloc[0]
    assert top["as_written"]


def test_a_bare_name_read_directly_is_pointed_to_its_file_in_a_folder_below() -> None:
    """a bare name read directly is pointed to its file in a folder below (#568)"""
    plan = _plan(["x.csv", "m.R"], ["data/x.csv", "scripts/m.R"])
    rw = _repro_keep_working_refs(
        repro_rewrite_paths(
            ["setwd('./data')", 'd <- read.csv("x.csv")'], "m.R", plan, script_path="scripts/m.R"
        ),
        plan,
        "scripts/m.R",
        ".",
    ).iloc[0]
    assert not rw["as_written"]
    assert rw["target"] == "data/x.csv"
    # in a joining call it stays as written: the code joins it to a folder of its own
    rw2 = _repro_keep_working_refs(
        repro_rewrite_paths(
            'd <- read.csv(file.path(dir, "x.csv"))', "m.R", plan, script_path="scripts/m.R"
        ),
        plan,
        "scripts/m.R",
        ".",
    )
    assert rw2.loc[rw2["ref"] == "x.csv", "as_written"].all()


def test_a_name_still_ambiguous_is_counted_not_kept_as_written() -> None:
    """a name still ambiguous is counted, not kept as written (#575, item 3)"""
    plan = _plan(["n.csv", "n.csv", "m.R"], ["s1/n.csv", "s2/n.csv", "m.R"])
    rw = repro_rewrite_paths('d <- read.csv("n.csv")', "m.R", plan, script_path="m.R")
    assert rw["ambiguous"].iloc[0]
    kept = _repro_keep_working_refs(rw, plan, "m.R", ".").iloc[0]
    assert kept["ambiguous"]
    assert not kept["as_written"]


def test_a_path_given_to_here_counts_from_where_here_starts() -> None:
    """a path given to here() counts from where here() starts (#575, item 6)"""
    plan = _plan(["dateModPars.csv", "sim.R"], ["p/data/dateModPars.csv", "p/scripts/sim.R"])
    rw = repro_rewrite_paths(
        'd <- read.csv(here::here("JohnstoneStrait/dateModPars.csv"))', "sim.R", plan
    )
    assert rw["in_here"].iloc[0]
    kept = _repro_keep_working_refs(rw, plan, "p/scripts/sim.R", "p/scripts", here_dir=".")
    assert kept["target"].iloc[0] == "p/data/dateModPars.csv"
    # an .Rproj above the run folder is where here() starts
    plan2 = _plan(["d.csv", "p.Rproj"], ["p/data/d.csv", "p/p.Rproj"])
    assert _repro_here_start("p/scripts", None, plan2) == "p"
    assert _repro_here_start("p/scripts", "q", plan2) == "q"
    assert _repro_here_start("p/scripts", None, plan) == "."
    # here("../data/x.csv") from scripts/ names data/x.csv
    code = {"a.R": 'd <- read.csv(here("../data/dateModPars.csv"))'}
    assert list(_repro_here_roots(code, ["p/scripts"], plan).values()) == ["p/scripts"]


# the folder each script runs from, and where here() starts --------------------


def test_rel_from_counts_a_path_from_a_folder_of_the_package() -> None:
    """.repro_rel_from counts a path from a folder of the package (#568)"""
    assert _repro_rel_from("a/b.csv") == "a/b.csv"
    assert _repro_rel_from("a/b.csv", "a") == "b.csv"
    assert _repro_rel_from("a/b.csv", "a/c") == "../b.csv"
    assert _repro_rel_from("x/y.csv", "a/c") == "../../x/y.csv"


def test_run_folders_runs_a_script_from_where_its_paths_work() -> None:
    """.repro_run_folders runs a script from where its paths work (#568)"""
    plan = _targets(
        "top/R/a.R",
        "top/R/b.R",
        "top/R/h.R",
        "top/data/d.csv",
        "top/p.Rproj",
        "other/s.R",
        "other/near.csv",
        "tie/c.R",
        "tie/x/e.R",
        "tie/p.Rproj",
    )
    code = {
        "a.R": 'read.csv("data/d.csv")',  # from the top of the project
        "b.R": 'source("h.R")',  # from its own folder
        "h.R": "x <- 1",  # sourced: runs where b.R runs
        "s.R": 'read.csv("near.csv")',
        "c.R": "x <- 1",  # names nothing: its own folder
        "e.R": "x <- 1",  # a tie: the .Rproj folder
    }
    places = ["top/R/a.R", "top/R/b.R", "top/R/h.R", "other/s.R", "tie/c.R", "tie/x/e.R"]
    order_tbl = pd.DataFrame({"file_name": list(code)})
    order_tbl.attrs["source_edges"] = pd.DataFrame({"from": ["h.R"], "to": ["b.R"]})
    out = _repro_run_folders(places, code, plan, order_tbl)
    assert out == ["top", "top/R", "top/R", "other", "tie", "tie"]
    # no plan: from the top
    assert _repro_run_folders(places, code, None, order_tbl) == ["."] * 6
    # a script at the top stays there, and a folder keeps its spelling
    p2 = _targets("Top/a.R", "Top/d.csv", "m.R")
    out2 = _repro_run_folders(
        ["Top/a.R", "m.R"],
        {"a.R": 'read.csv("d.csv")', "m.R": "x"},
        p2,
        pd.DataFrame({"file_name": ["a.R", "m.R"]}),
    )
    assert out2 == ["Top", "."]


def test_here_gets_the_folder_its_paths_point_to_as_the_projects_top() -> None:
    """here() gets the folder its paths point to as the project's top (#568)"""
    code = {
        "a.Rmd": [
            'source(here("scripts", "functions.R"))',
            'd <- readRDS(here("data", "soil.rds"))',
        ],
        "b.R": "x <- 1",
    }
    plan = _plan(
        ["functions.R", "soil.rds", "a.Rmd", "b.R"],
        ["scripts/functions.R", "data/soil.rds", "scripts/a.Rmd", "scripts/b.R"],
    )
    assert list(_repro_here_roots(code, ["scripts", "scripts"], plan).values()) == [".", None]
    # the authors' .Rproj at the top: here() finds it, nothing to add
    plan2 = _plan(
        ["functions.R", "soil.rds", "p.Rproj"],
        ["scripts/functions.R", "data/soil.rds", "p.Rproj"],
    )
    assert list(_repro_here_roots({"a.Rmd": code["a.Rmd"]}, ["scripts"], plan2).values()) == [None]
    # paths that exist from the run folder itself
    plan3 = _plan(
        ["functions.R", "soil.rds"],
        ["proj/scripts/functions.R", "proj/data/soil.rds"],
    )
    assert list(_repro_here_roots({"a.Rmd": code["a.Rmd"]}, ["proj/scripts"], plan3).values()) == [
        "proj"
    ]


# folders on the authors' computer held in a variable ----------------------------


def test_a_folder_held_in_a_variable_gets_the_deposits_folder() -> None:
    """a folder on the authors' computer held in a variable gets the deposit's folder (#568)"""
    # root <- "~/Data_to_publish/", and the files are in Data_Dryad.zip/Data_Dryad/
    plan = _plan(
        ["0_FUNCTIONS.R", "a.rds", "m.R"],
        [
            "Data_Dryad.zip/Data_Dryad/1_scripts/0_FUNCTIONS.R",
            "Data_Dryad.zip/Data_Dryad/2_data/a.rds",
            "Data_Dryad.zip/Data_Dryad/1_scripts/m.R",
        ],
    )
    code = [
        'root <- "~/Data_to_publish/"',
        'p <- paste0(root,"2_data/")',
        'source(paste0(root,"/1_scripts/0_FUNCTIONS.R"))',
    ]
    fv = _repro_folder_vars(code, plan, "Data_Dryad.zip/Data_Dryad/1_scripts")
    assert list(fv.columns) == ["value", "target", "keep_pieces", "pieces"]
    assert fv["value"].tolist() == ["~/Data_to_publish/"]
    assert fv["target"].tolist() == ["../"]
    assert set(fv["pieces"].iloc[0]) == {"2_data/", "/1_scripts/0_FUNCTIONS.R"}
    # the pasted piece is no longer rewritten on its own; the folder is
    rw = pd.DataFrame(
        {
            "ref": ["/1_scripts/0_FUNCTIONS.R"],
            "basename": ["0_functions.r"],
            "matched": [True],
            "target": ["0_FUNCTIONS.R"],
            "ambiguous": [False],
            "n_candidates": [1],
            "is_call": [False],
            "as_written": [False],
        }
    )
    rw2 = _repro_apply_folder_vars(rw, fv)
    assert rw2["ref"].tolist() == ["~/Data_to_publish/"]
    assert _repro_apply_rewrites(code, rw2)[0] == 'root <- "../"'
    # a piece that goes up first: paste0(root, "../2_data/a.rds"), root being the scripts' folder
    fv_up = _repro_folder_vars(
        [
            'root <- "C:/Users/me/Data_to_publish/1_scripts/"',
            'd <- readRDS(paste0(root, "../2_data/a.rds"))',
        ],
        plan,
        "Data_Dryad.zip/Data_Dryad/1_scripts",
    )
    assert fv_up["target"].tolist() == ["./"]
    # file.path() and a folder without the final slash
    plan2 = _plan(
        ["k.csv", "s.R"],
        ["Examples/P2/Data/k.csv", "Examples/P2/s.R"],
    )
    fv2 = _repro_folder_vars(
        ['dataDir <- "~/MGDrivE/Main/P2"', 'k <- read.csv(file.path(dataDir, "Data/k.csv"))'],
        plan2,
        "Examples/P2",
    )
    assert fv2["target"].tolist() == ["."]
    # nothing of the deposit under any folder: left alone
    none = _repro_folder_vars(
        ['d <- "C:/me/"', 'x <- read.csv(paste0(d, "none/x.csv"))'], plan2, "."
    )
    assert len(none) == 0
    # a relative folder is left alone
    rel = _repro_folder_vars(
        ['d <- "Data/"', 'x <- read.csv(paste0(d, "k.csv"))'], plan2, "Examples/P2"
    )
    assert len(rel) == 0


def test_a_folder_in_a_variable_made_with_paste0_becomes_the_run_folder_when_its_file_is_found() -> (
    None
):
    """a folder in a variable made with paste0() becomes the run folder when its file is found (#578, item 4)"""
    code = [
        'main_address = paste0("~/x/")',
        'd <- read_xlsx(file.path(main_address, "sub/a.xlsx"))',
        'dir.create(file.path(main_address, "Outputs"))',
        'main_address = "~/x"',
    ]
    plan = _plan(["a.xlsx", "m.R"], ["D.zip/D/a.xlsx", "m.R"])
    rw = _repro_keep_working_refs(
        repro_rewrite_paths(code, "m.R", plan, script_path="m.R"), plan, "m.R", "."
    )
    fv = _repro_folder_vars(code, plan, ".", rw=rw)
    assert fv["keep_pieces"].all()
    out = _repro_apply_rewrites(code, _repro_apply_folder_vars(rw, fv))
    assert out == [
        'main_address = paste0("./")',
        'd <- read_xlsx(file.path(main_address, "D.zip/D/a.xlsx"))',
        'dir.create(file.path(main_address, "Outputs"))',
        'main_address = "./"',
    ]


def test_apply_folder_vars_without_folders_leaves_the_rewrites() -> None:
    rw = pd.DataFrame({"ref": ["a"], "target": ["b"]})
    assert _repro_apply_folder_vars(rw, None) is rw
    assert _repro_apply_folder_vars(rw, _repro_folder_vars([], None)) is rw


def test_a_folder_a_script_lists_is_rewritten_to_where_the_deposit_holds_it() -> None:
    """a folder a script lists is rewritten to where the deposit holds it (#578, item 2)"""
    plan = _plan(
        ["a.csv", "b.csv", "m.R"],
        [
            "x.zip/top/ir_data/control_forage_data/a.csv",
            "x.zip/top/ir_data/control_forage_data/b.csv",
            "scripts/m.R",
        ],
    )
    lf = _repro_listed_folders(
        'd <- list.files("../data/ir_data/control_forage_data/", full.names = TRUE)',
        plan,
        "scripts",
    )
    assert lf["target"].tolist() == ["../x.zip/top/ir_data/control_forage_data/"]
    assert _repro_apply_rewrites(
        ['d <- list.files("../data/ir_data/control_forage_data/")'], lf
    ) == ['d <- list.files("../x.zip/top/ir_data/control_forage_data/")']
    # a folder that works as written stays
    works = _repro_listed_folders(
        'd <- list.files("../x.zip/top/ir_data/control_forage_data")', plan, "scripts"
    )
    assert len(works) == 0
    # two folders with the same last parts: no guess
    plan2 = _plan(
        ["a.csv", "b.csv"],
        ["s1/ir_data/control_forage_data/a.csv", "s2/ir_data/control_forage_data/b.csv"],
    )
    assert (
        len(
            _repro_listed_folders(
                'd <- list.files("../data/ir_data/control_forage_data/")', plan2, "scripts"
            )
        )
        == 0
    )
    # a glob pattern, and Python
    glob = _repro_listed_folders('f <- Sys.glob("data/control_forage_data/*.csv")', plan, ".")
    assert glob["target"].tolist() == ["x.zip/top/ir_data/control_forage_data/*.csv"]
    py = _repro_listed_folders(
        [
            'f = glob.glob("data/control_forage_data/*.csv")',
            'g = Path("data/control_forage_data").glob("*.csv")',
            'h = Path("data/x.csv")',
        ],
        plan,
        ".",
        "Python",
    )
    assert py["ref"].tolist() == ["data/control_forage_data/*.csv", "data/control_forage_data"]
    # nothing to compare with
    assert len(_repro_listed_folders("x", None)) == 0


# a file chosen by a person, the .Rprofile, Windows paths ---------------------


def test_fill_file_choice_fills_in_the_file_the_code_names() -> None:
    """.repro_fill_file_choice fills in the file the code names (#568)"""
    files = ["nest_outcomes.csv", "transects.csv"]

    def f(x: Any, d: list[str] = files) -> dict[str, Any]:
        return _repro_fill_file_choice(x, d)

    same_line = f('d <- read.csv(file.choose())   # Select "nest_outcomes" here')
    assert same_line["txt"] == [
        'd <- read.csv("nest_outcomes.csv")   # Select "nest_outcomes" here'
    ]
    assert same_line["filled"] == ["nest_outcomes.csv"]
    above = f(["# the transects file", "d <- read.csv(file.choose())"])
    assert above["txt"][1] == 'd <- read.csv("transects.csv")'
    # the only data file of the deposit
    assert f("d <- read.csv(file.choose())", ["a.csv"])["txt"] == ['d <- read.csv("a.csv")']
    # nothing says which, or two fit: it stays
    assert f("d <- read.csv(file.choose())")["txt"] == ["d <- read.csv(file.choose())"]
    assert len(f("d <- read.csv(file.choose())")["filled"]) == 0
    assert f("d <- file.choose() # nest_outcomes or transects")["filled"] == []
    # a comment is left alone, other forms too
    assert f("# file.choose()", ["a.csv"])["txt"] == ["# file.choose()"]
    assert f('p <- choose.files(multi = F, caption = c("a"))', ["a.csv"])["txt"] == ['p <- "a.csv"']


def test_neutralise_rprofile_keeps_the_rest_of_a_rprofile(tmp_path: Path) -> None:
    """.repro_neutralise_rprofile keeps the rest of a .Rprofile"""
    assert _repro_neutralise_rprofile(tmp_path) == 0
    path = tmp_path / ".Rprofile"
    path.write_text('options(digits = 4)\nsource("renv/activate.R")\n')
    assert _repro_neutralise_rprofile(tmp_path) == 1
    lines = path.read_text().splitlines()
    assert lines[0] == "options(digits = 4)"
    assert "removed renv's activate.R" in lines[1]


def test_windows_paths_in_quoted_file_names_get_a_slash() -> None:
    """Windows paths in quoted file names get / (#568)"""
    txt, paths = _repro_slash_paths(
        [
            'write.table(m, "Data\\\\MatrixNoct.txt", sep = "\\t")',
            'f <- list.files(pattern = "\\\\.csv$")',
            'd <- read.csv("C:\\\\Users\\\\a\\\\data.csv")',
            'g <- gsub("\\\\d+", "", x)',
            'p <- "results/plain.csv"',
        ]
    )
    assert txt == [
        'write.table(m, "Data/MatrixNoct.txt", sep = "\\t")',
        'f <- list.files(pattern = "\\\\.csv$")',
        'd <- read.csv("C:/Users/a/data.csv")',
        'g <- gsub("\\\\d+", "", x)',
        'p <- "results/plain.csv"',
    ]
    assert len(paths) == 2
    rows = _repro_slash_rows(
        pd.DataFrame({"file_name": ["a.R"], "slashes_fixed": ["; ".join(paths)]})
    )
    assert rows is not None
    assert rows["change_type"].tolist() == ["windows_path", "windows_path"]
    assert rows["detail"].iloc[0] == '"Data\\\\MatrixNoct.txt" -> "Data/MatrixNoct.txt"'
    report = _repro_slash_report(pd.DataFrame({"file_name": ["a.R"], "slashes_fixed": [paths[0]]}))
    assert report is not None
    assert "1 script uses Windows paths" in report[0]
    assert _repro_slash_rows(pd.DataFrame({"file_name": ["a.R"], "slashes_fixed": [""]})) is None


def test_a_path_under_the_folder_of_a_removed_setwd_counts_from_the_working_folder() -> None:
    """a path under the folder of a removed setwd() counts from the working folder (#578, item 3)"""
    code: list[str | None] = [
        'setwd("C:\\\\proj")',
        'x <- read.csv("C:\\\\proj\\\\data\\\\a.csv")',
        'y <- "C:/PROJ/b.txt"',
        'z <- "C:\\\\other\\\\c.csv"',
    ]
    folders = _repro_neutralise_setwd(code)[2]
    out, paths = _repro_strip_setwd_folder(code, folders)
    assert out[1:4] == [
        'x <- read.csv("data/a.csv")',
        'y <- "b.txt"',
        'z <- "C:\\\\other\\\\c.csv"',
    ]
    assert len(paths) == 2
    tbl = pd.DataFrame(
        {
            "file_name": ["a.R"],
            "slashes_fixed": [""],
            "setwd_folder_removed": ["; ".join(paths)],
        }
    )
    rows = _repro_slash_rows(tbl)
    assert rows is not None
    assert set(rows["change_type"]) == {"path_rewrite"}
    # those rows are not Windows paths in the report
    assert _repro_slash_report(tbl) is None
    # a relative setwd() folder is left alone
    assert len(_repro_strip_setwd_folder(['x <- "data/a.csv"'], ["data"])[1]) == 0


# a script's name and place --------------------------------------------------------


def test_unique_script_names_changes_only_names_that_repeat() -> None:
    """.repro_unique_script_names changes only names that repeat (#568)"""
    files = pd.DataFrame(
        {"file_name": ["a.R", "a.R", "b.R"], "file_path": ["x/a.R", ".\\y\\a.R", "z/b.R"]}
    )
    assert _repro_unique_script_names(files) == ["x/a.R", "y/a.R", "b.R"]
    # no repeats, no change
    assert _repro_unique_script_names(files.iloc[1:]) == ["a.R", "b.R"]
    # the same path in two repositories
    files = pd.DataFrame(
        {
            "file_name": ["a.R", "a.R"],
            "file_path": ["x/a.R", "x/a.R"],
            "repo_name": ["github", "zenodo.1"],
        }
    )
    assert _repro_unique_script_names(files) == ["github/x/a.R", "zenodo.1/x/a.R"]
    # nothing to tell them apart by: a number, and the name still ends in the file name
    files = pd.DataFrame({"file_name": ["a.R", "a.R"]})
    assert _repro_unique_script_names(files) == ["1/a.R", "2/a.R"]
    # a name the other language already has
    files = pd.DataFrame({"file_name": ["n.ipynb"], "file_path": ["py/n.ipynb"]})
    assert _repro_unique_script_names(files, taken=["n.ipynb"]) == ["py/n.ipynb"]


def test_script_places_gives_same_named_scripts_places_of_their_own() -> None:
    """.repro_script_places gives same-named scripts places of their own (#568)"""
    files = pd.DataFrame(
        {"file_name": ["x/a.R", "y/a.R", "b.R"], "file_path": ["x/a.R", "y/a.R", "z/b.R"]}
    )
    # each its own path
    assert _repro_script_places(files) == ["x/a.R", "y/a.R", "z/b.R"]
    # the same path in two repositories
    files = pd.DataFrame({"file_name": ["r1/x/a.R", "r2/x/a.R"], "file_path": ["x/a.R", "x/a.R"]})
    assert _repro_script_places(files) == ["r1/x/a.R", "r2/x/a.R"]
    # never above the sandbox
    files = pd.DataFrame(
        {"file_name": ["../a.R", "x/../../a.R"], "file_path": ["../a.R", "x/../../a.R"]}
    )
    assert _repro_script_places(files) == ["_/a.R", "x/_/_/a.R"]


def test_script_path_and_order_path() -> None:
    files = pd.DataFrame(
        {
            "file_name": ["a.R", "r/b.R", "c.R", "d.R"],
            "file_path": ["./x\\a.R", None, "", "/abs/d.R"],
        }
    )
    assert _repro_script_path(files) == ["x/a.R", "r/b.R", "c.R", "abs/d.R"]
    assert _repro_script_path(pd.DataFrame({"file_name": ["a.R"]})) == ["a.R"]
    # the unique name when it holds a folder, else the path
    assert _repro_order_path(files) == ["x/a.R", "r/b.R", "c.R", "abs/d.R"]
    both = pd.DataFrame({"file_name": ["k/a.R", "a.R"], "file_path": ["z/a.R", "y/a.R"]})
    assert _repro_order_path(both) == ["k/a.R", "y/a.R"]


def test_shown_location_is_the_link_else_the_path_never_the_local_place() -> None:
    files = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R", "c.R", "d.R"],
            "file_url": ["https://x.org/a.R", "ftp://x/b.R", None, None],
            "file_path": ["r/a.R", "r/b.R", None, ""],
            "file_location": ["/job/1/a.R", "/job/1/b.R", "/job/1/c.R", "/job/1/d.R"],
        }
    )
    assert _repro_shown_location(files, [0, 1, 2, 3]) == ["https://x.org/a.R", "r/b.R", None, None]
    assert _repro_shown_location(files, 1) == ["r/b.R"]
    assert _repro_shown_location(files, []) == []
