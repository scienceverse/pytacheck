"""The reproducibility changes of metacheck 0.4.4.30 to 0.4.4.35 (#587, #588, #590, #592, #593, #695, #706, #707).

Ports of the R tests of ``test-reproducibility_check.R`` for: several scripts that stopped on one
folder made once and run again (#590), the one of several different files whose folders the path
names (#590), the deposit's own copy of a package and R-Forge in the Docker install (#588),
scripts that stopped at their own check of how many arguments they got (#593), an SPSS or Stata
file the code reads (#695), the packages of the local modules a Python 2 script imports (#593), the
packages an error names and the ``library()`` lines of a later run (#587, #592), and the empty
result of ``match_reported_output()`` when every test is weak (#707).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.modules import _reproducibility as h
from metacheck.repro import docker
from metacheck.repro.core import _repro_lfs_swaps
from metacheck.repro.layout import (
    _repro_folders_made_text,
    _repro_lfs_stopped,
    _repro_make_failed_write_folders,
    _repro_path_tail_match,
    _repro_place_from_deposit,
    _repro_sandbox_names,
)
from metacheck.repro.outcomes import _repro_needs_arguments, _repro_stopped_at_arg_check
from metacheck.repro.python import repro_dependencies_py
from metacheck.repro.runsupport import _repro_missing_pkg_names, _repro_more_library_lines
from metacheck.repro.software import _repro_py_with_imports


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# #590: a folder made for several scripts ----------------------------------------------------


def test_several_scripts_that_stopped_on_one_folder_each_run_again(tmp_path: Path) -> None:
    root = tmp_path
    (root / "code").mkdir()
    res = pd.DataFrame(
        {
            "file_name": ["f1.R", "f2.R", "f3.R"],
            "outcome": "errored",
            "stderr": "",
            "error": "Error in `ggsave()`:\n! Cannot find directory 'Figures/'.",
        }
    )
    run_tbl = pd.DataFrame({"file_name": res["file_name"], "run_dir": str(root / "code")})
    made = _repro_make_failed_write_folders(res, run_tbl, root)
    assert made["script"].tolist() == ["f1.R", "f2.R", "f3.R"]
    assert sorted(set(made["folder"])) == ["code/Figures"]
    assert (root / "code" / "Figures").is_dir()
    # one folder made for three scripts is one folder
    text = _repro_folders_made_text(made)
    assert "wrote into a folder that the deposit does not have" in text
    assert "so we made it and ran them once more" in text
    # a folder the deposit or another script made is not taken for one of ours
    (root / "code" / "Other").mkdir()
    res["error"] = "Error in `ggsave()`:\n! Cannot find directory 'Other/'."
    assert len(_repro_make_failed_write_folders(res, run_tbl, root)) == 0


def test_a_folder_made_in_a_pass_before_is_still_ours(tmp_path: Path) -> None:
    root = tmp_path
    (root / "code" / "Figures").mkdir(parents=True)
    res = pd.DataFrame(
        {
            "file_name": ["f4.R"],
            "outcome": "errored",
            "stderr": "",
            "error": "Error in `ggsave()`:\n! Cannot find directory 'Figures/'.",
        }
    )
    run_tbl = pd.DataFrame({"file_name": res["file_name"], "run_dir": str(root / "code")})
    seen = {"f1.R": ["code/Figures"]}
    assert _repro_make_failed_write_folders(res, run_tbl, root, seen)["script"].tolist() == ["f4.R"]


# #590: the folders a path names -------------------------------------------------------------


def test_of_several_different_files_of_one_name_the_one_in_the_named_folders_is_placed(
    tmp_path: Path,
) -> None:
    root = tmp_path
    for d in ("zip/output/ADMIXTURE/popmap", "zip/output/RAxML", "code"):
        (root / d).mkdir(parents=True)
    _write(root / "zip/output/ADMIXTURE/popmap/popmap_Cm.txt", "A\n")
    _write(root / "zip/output/RAxML/popmap_Cm.txt", "B\n")
    before = _repro_sandbox_names(root)
    res = pd.DataFrame(
        {
            "file_name": ["plot.R"],
            "outcome": "errored",
            "stderr": "",
            "error": "cannot open file 'output/ADMIXTURE/popmap/popmap_Cm.txt': "
            "No such file or directory",
        }
    )
    run_tbl = pd.DataFrame({"file_name": ["plot.R"], "run_dir": [str(root / "code")]})
    placed = _repro_place_from_deposit(res, run_tbl, root, before)
    assert placed["from"].tolist() == ["zip/output/ADMIXTURE/popmap/popmap_Cm.txt"]
    copy = root / "code" / "output" / "ADMIXTURE" / "popmap" / "popmap_Cm.txt"
    assert copy.read_text(encoding="utf-8").strip() == "A"
    # only the file name in common with both: no choice
    res["error"] = "cannot open file 'other/popmap_Cm.txt': No such file or directory"
    assert len(_repro_place_from_deposit(res, run_tbl, root, before)) == 0
    assert _repro_path_tail_match(
        "output/RAxML/popmap_HC.txt", ["x/output/RAxML/popmap_HC.txt", "y/popmap_HC.txt"]
    ) == [3, 1]
    # without regard to capitals, and a backslash separates folders
    assert _repro_path_tail_match("Out\\A\\f.txt", ["x/out/a/F.TXT"]) == [3]


# the Docker install: the deposit's own packages and R-Forge (#588) ---------------------------


def test_the_deposits_own_copy_of_a_package_is_found_and_copied_for_the_install(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    pkg = root / "rel.zip" / "owner-mypkg-1a2b"
    _write(pkg / "DESCRIPTION", "Package: mypkg\nVersion: 1.0.0\n")
    _write(pkg / "R" / "f.R", "f <- function() 1\n")
    _write(pkg / "big_data.csv")
    # three tarballs of one package: the highest version
    for v in ("0.2.0", "0.10.1", "0.9"):
        _write(root / "_archive" / f"lay_{v}.tar.gz", "")
    # an installed package in a renv library is no source
    _write(
        root / "renv" / "library" / "other" / "DESCRIPTION",
        "Package: other\nVersion: 2.0\nBuilt: R 4.3.1; ; 2023-01-01; unix\n",
    )
    found = docker._repro_deposit_packages(root, ["mypkg", "lay", "other", "notthere"])
    assert set(found) == {"mypkg", "lay"}
    assert Path(found["lay"]).name == "lay_0.10.1.tar.gz"
    assert docker._repro_deposit_packages(root, ["notthere"]) == {}
    assert docker._repro_deposit_packages(None, ["mypkg"]) == {}

    box = tmp_path / "box"
    box.mkdir()
    inside = docker._repro_copy_deposit_packages(found, box)
    assert inside["mypkg"] == "/sandbox/deposit_pkgs/mypkg"
    assert inside["lay"] == "/sandbox/deposit_pkgs/lay_0.10.1.tar.gz"
    # only the parts a package install reads, not the data beside them
    assert (box / "deposit_pkgs" / "mypkg" / "R" / "f.R").exists()
    assert not (box / "deposit_pkgs" / "mypkg" / "big_data.csv").exists()
    assert docker._repro_copy_deposit_packages({}, box) == {}
    assert docker._repro_copy_deposit_packages(None, box) == {}


def test_the_install_script_tries_the_deposit_first_and_r_forge_before_r_universe(
    rscript: str, tmp_path: Path
) -> None:
    lines = docker._ELSEWHERE_SCRIPT

    def first(needle: str) -> int:
        return next(i for i, ln in enumerate(lines) if needle in ln)

    assert first("install_local(deposit_pkgs") < first("BiocManager::install(bioc_needed")
    assert first("R-Forge.R-project.org") < first("r-universe.dev/api/search")
    assert any("found nowhere else: " in ln and "on R-Forge or on R-universe" in ln for ln in lines)
    deps = pd.DataFrame({"package": ["mypkg"], "source": ["cran"], "ref": [None]})
    full = docker._install_script(deps, {"mypkg": "/sandbox/deposit_pkgs/mypkg"})
    assert any(
        ln.startswith('.repro_docker_deposit <- c(mypkg = "/sandbox/deposit_pkgs/mypkg")')
        for ln in full
    )
    # no copy: the empty named vector, which names() of gives NULL for
    assert any(
        ln == ".repro_docker_deposit <- structure(character(0), names = character(0))"
        for ln in docker._install_script(deps)
    )
    script = tmp_path / "parse.R"
    script.write_text(
        "invisible(parse(file = '" + (tmp_path / "install.R").as_posix() + "'))\ncat('ok')\n",
        encoding="utf-8",
    )
    (tmp_path / "install.R").write_text("\n".join(full) + "\n", encoding="utf-8")
    out = subprocess.run([rscript, str(script)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"


# #593: a script that stopped at its own check of its arguments ------------------------------


def test_a_script_that_stopped_at_its_own_argument_check_needs_a_person() -> None:
    own = [
        [
            "args = commandArgs(trailingOnly = T)",
            "if(length(args)!=3) {",
            '  stop("Provide (only) signal strength for the analysis", call.=F)',
            "}",
        ],
        ["args = commandArgs(trailingOnly = T)", "x <- 1", 'stop("Something else broke")'],
    ]
    err = [
        "Error: Provide (only) signal strength for the analysis\nExecution halted",
        "Error: Something else broke\nExecution halted",
    ]
    assert _repro_needs_arguments(own, err, "R") == [True, False]
    assert _repro_stopped_at_arg_check(own[0], err[0])
    assert not _repro_stopped_at_arg_check(own[0], None)
    assert not _repro_stopped_at_arg_check(own[0], "Error: ab")  # a message too short to find
    # a file that indexes `args` it never assigned, as a driver script sources it after setting it
    un = [
        ['#args="" #init', "if(dir.exists(args[1])) x <- 1"],
        ["args <- c('a', 'b')", "f(args[3])"],
    ]
    err2 = ["Error in args[1] : object of type 'closure' is not subsettable"] * 2
    assert _repro_needs_arguments(un, err2, "R") == [True, False]
    # not for Python, where the first rule is the only one
    assert _repro_needs_arguments(
        [["import sys", "x = sys.argv[1]"]], "IndexError: list index out of range", "Python"
    ) == [False]


# #695: an SPSS or Stata file the code reads --------------------------------------------------


def test_an_spss_or_stata_file_the_code_reads_is_recognised_by_its_name_in_the_code() -> None:
    code = [
        'rawdata <- read_sav("../data/rawdata.sav")',
        "x <- read_spss('Data for SPSS.sav')",
        "# notes about otherdata.sav",
    ]
    names = ["data/RawData.sav", "other.sav", "Data for SPSS.sav", "data.sav", "otherdata.sav"]
    assert h._repro_named_in_code(names, code) == [True, False, True, False, False]
    assert h._repro_named_in_code([], code) == []


def test_data_the_code_reads_is_no_sign_that_the_analysis_was_left_out() -> None:
    code = ['d <- haven::read_sav("data/study.sav")', 'e <- haven::read_dta("data/w1.dta")']
    ss = h.spss_stata(["data/study.sav", "data/w1.dta"], ["a.R"], code)
    assert not ss["spss_red"] and not ss["stata_red"]
    assert ss["spss_report"] is not None and ss["spss_report"][0] == "#### SPSS data"
    assert "is read by the deposit's own code" in ss["spss_report"][1]
    assert ss["stata_report"] is not None and ss["stata_report"][0] == "#### Stata data"
    assert "not in a Stata do-file" in ss["stata_report"][1]
    two = h.spss_stata(["a.sav", "b.por"], ["a.R"], ['read_sav("a.sav")', 'read_por("b.por")'])
    assert "The SPSS data files (`.sav`/`.por`) are read" in two["spss_report"][1]
    # one file the code does not read is enough to be red
    some = h.spss_stata(["a.sav", "b.sav"], ["a.R"], ['read_sav("a.sav")'])
    assert some["spss_red"]
    assert some["spss_report"][0] == "#### SPSS data without syntax"
    # no code that reads them, as before
    none = h.spss_stata(["data/study.sav", "data/w1.dta"], [])
    assert none["spss_red"] and none["stata_red"]
    assert none["stata_report"][0] == "#### Stata data without syntax"
    # syntax present: the text of before
    syn = h.spss_stata(["data/study.sav", "run.sps"], [])
    assert not syn["spss_red"] and syn["spss_report"][0] == "#### SPSS data"
    assert "This module does not execute SPSS syntax itself" in syn["spss_report"][1]


# #706: no code left, but a repository was refused --------------------------------------------


def test_the_summary_of_a_paper_with_no_code_names_the_repositories_refused_for_their_files() -> (
    None
):
    base = "We found no R or Python code files to assess for reproducibility."
    assert h._repro_no_code_text() == base
    assert h._repro_no_code_text(" Note.", None) == base + " Note."
    refused = pd.DataFrame(
        {"repo_url": ["a", "b"], "n_files": [1, 2], "message": ["Repo a is big.", "Repo b too."]}
    )
    assert h._repro_no_code_text(" Note.", refused) == base + " Note. Repo a is big. Repo b too."
    assert h._repro_no_code_text("", refused.iloc[0:0]) == base


# #593: the packages of the local modules a Python 2 script imports ---------------------------


def test_the_packages_of_a_python_2_script_include_those_of_the_modules_it_imports() -> None:
    ctl = {
        "calculate_FST.py": ["import genomics", "print(genomics.f())"],
        "genomics.py": ["import numpy as np", "import helper", "def f(): return np.mean([1, 2])"],
        "helper.py": ["import scipy"],
        "other.py": ["import pandas"],
    }
    files = _repro_py_with_imports(["calculate_FST.py"], ctl, list(ctl))
    assert set(files) == {"calculate_FST.py", "genomics.py", "helper.py"}
    assert files[0] == "calculate_FST.py"
    d = repro_dependencies_py({f: ctl[f] for f in files})
    pkgs = set(d["package"])
    assert {"numpy", "scipy"} <= pkgs
    assert "pandas" not in pkgs


# #587, #592: packages an error names, and the library() lines of a later run ----------------


@pytest.mark.parametrize(
    ("txt", "pkg"),
    [
        ("Error in loadNamespace(x) : there is no package called ‘foo’", "foo"),
        ("Error: there is no package called 'a.b'", "a.b"),
        ("Package `performance` required for this function.", "performance"),
        ('Error: The package "broom.helpers" is required.', "broom.helpers"),
        ("The rnaturalearthdata package needs to be installed", "rnaturalearthdata"),
        ('Error in read.csv("x.csv") : cannot open file', None),
        (None, None),
    ],
)
def test_the_package_an_error_says_is_missing(txt: str | None, pkg: str | None) -> None:
    assert _repro_missing_pkg_names([txt]) == [pkg]
    if txt is not None:
        assert _repro_missing_pkg_names(txt) == [pkg]


def test_a_script_that_stops_on_a_second_missing_function_gets_more_library_lines() -> None:
    code = {
        "a.R": ["library(dplyr)", "x <- mutate(d, y = 1)", "z <- ggplot(d)"],
        "b.R": ["helper <- function() 1"],
        "c.R": ["y <- helper()"],
    }
    res = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R", "c.R"],
            "outcome": ["errored", "ok", "errored"],
            "error": [
                'Error: could not find function "ggplot"',
                None,
                'could not find function "helper"',
            ],
        }
    )
    import metacheck.repro.runsupport as rs

    seen: dict[str, Any] = {}

    def fake(name: str, user: str, ctl: Any, cands: Any, unavailable: Any = ()) -> list[str]:
        seen[user] = (name, list(unavailable))
        return ["dplyr", "ggplot2"]

    old = rs._repro_missing_fn_pkgs
    rs._repro_missing_fn_pkgs = fake  # type: ignore[assignment]
    try:
        out = _repro_more_library_lines(
            res, code, ["ggplot2"], unavailable=["zzz"], injected={"a.R": ["dplyr"]}
        )
    finally:
        rs._repro_missing_fn_pkgs = old  # type: ignore[assignment]
    # only what the script did not get yet; a function another script defines is the corrective
    # run's task
    assert out == {"a.R": ["ggplot2"]}
    assert seen == {"a.R": ("ggplot", ["zzz"])}
    assert _repro_more_library_lines(None, code, []) == {}
    assert _repro_more_library_lines(res.iloc[0:0], code, []) == {}


# Git LFS pointers: a pointer with no stated size has no file to swap in --------------------


def test_a_git_lfs_pointer_that_states_no_size_has_no_file_to_swap_in(tmp_path: Path) -> None:
    head = "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n"
    sized = tmp_path / "a" / "x.rds"
    unsized = tmp_path / "b" / "y.rds"
    _write(sized, head + "size 5\n")
    _write(unsized, head)
    _write(tmp_path / "c" / "x.rds", "12345")
    _write(tmp_path / "c" / "y.rds", "12345")
    swaps = _repro_lfs_swaps([str(sized), str(unsized), str(tmp_path / "c" / "x.rds")])
    assert [Path(p).name for p in swaps["pointer"]] == ["x.rds"]
    assert Path(swaps["file"].iloc[0]).parent.name == "c"
    # but both are pointers for a script that stopped on one
    res = pd.DataFrame(
        {
            "file_name": ["s.R"],
            "outcome": ["errored"],
            "error": ["Error in readRDS('y.rds') : unknown input format"],
            "stderr": [""],
        }
    )
    assert _repro_lfs_stopped(res, tmp_path / "b") == {"s.R": ["y.rds"]}
