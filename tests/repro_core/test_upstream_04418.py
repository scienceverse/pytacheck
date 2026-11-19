"""The reproducibility changes of metacheck 0.4.4.7 to 0.4.4.18 (scienceverse/metacheck#580, #648).

Ports of the R tests of ``test-reproducibility_check.R`` for: the five groups a script's run is put
in (ran, did not reproduce, a package or program was not available, out of time, could not be
checked automatically), a Python module that exists only on one platform, a file of a package run
on its own, keys and sign-ins the authors' own message asks for, advice in the language of the
script, code recovered from a knitted report, paths to files the deposit does not hold, a pattern
of names that matched a file, and the calls that need a screen.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.repro.core import _repro_apply_rewrites, _repro_replace_screen_calls
from metacheck.repro.layout import _repro_path_fix_text
from metacheck.repro.outcomes import (
    _repro_check_group,
    _repro_failure_reason,
    _repro_from_report_failed,
    _repro_groups_sentence,
    _repro_platform_of,
    _repro_run_light,
)
from metacheck.repro.section import _repro_checklist_section


def _frame(files: list[str], oc: list[str], err: list[str], **extra: Any) -> tuple[Any, Any]:
    n = len(files)
    tb = pd.DataFrame(
        {
            "file_name": files,
            "run_order": list(range(1, n + 1)),
            "outcome": oc,
            "setwd_removed": [0] * n,
            "not_runnable_reason": pd.array([None] * n, dtype="string"),
            "unresolved_inputs": pd.array([None] * n, dtype="string"),
        }
    )
    rr = pd.DataFrame(
        {"file_name": files, "outcome": oc, "error": err, "stderr": [""] * n, **extra}
    )
    return tb, rr


def test_each_scripts_result_is_put_in_one_of_five_groups() -> None:
    g = _repro_check_group
    assert g(
        [
            "ran_ok",
            "not_parsed",
            "python2",
            "skipped_missing_inputs",
            "dependency_unavailable",
            "timed_out",
            "skipped_time_limit",
            "needs_a_person",
            "needs_internet",
            "out_of_memory",
            "download_too_large",
            "not_downloaded",
            "run_by_source",
            "not_analysis",
            "docker_failed",
        ]
    ) == [
        "ran",
        "code_error",
        "code_error",
        "code_error",
        "dependency",
        "time",
        "time",
        *["not_checkable"] * 5,
        None,
        None,
        None,
    ]
    msg = [
        "Error in read.csv('a.csv') : cannot open the connection\n"
        "In file(file, \"rt\") : cannot open file 'a.csv'",
        "Error in library(rgdal) : there is no package called 'rgdal'",
        "sh: 1: git: not found",
        "Error: unable to load shared object '/lib/sf.so': libgdal.so.30: cannot open shared "
        "object file",
        " *** caught segfault ***\naddress 0x0, cause 'memory not mapped'",
        'Error in dev.copy2eps(file = "x.eps") : can only print from a screen device',
        "Error: OPENCAGE_KEY is not set",
        'Error in windows() : could not find function "windows"',
        "Error in View(d) : unable to start data viewer",
        "Error: object 'x' not found",
    ]
    err = ["errored"] * len(msg)
    reason = _repro_failure_reason(err, msg)
    assert reason[7:9] == ["environment", "environment"]
    assert g(err, reason, msg) == [
        "code_error",
        "dependency",
        "dependency",
        "dependency",
        "dependency",
        "not_checkable",
        "not_checkable",
        "not_checkable",
        "not_checkable",
        "code_error",
    ]
    assert g([]) == []


def test_a_module_of_one_platform_and_a_file_of_a_package_run_alone_are_not_missing_packages() -> (
    None
):
    assert _repro_platform_of(
        [
            "ModuleNotFoundError: No module named 'google.colab'",
            "No module named 'win32com.client'",
            "No module named 'ehrql'",
            "No module named 'kaggle_secrets'",
            "No module named 'google.cloud'",
            "No module named 'torchvision'",
            None,
        ]
    ) == ["Google Colab", "Windows", "OpenSAFELY", "Kaggle", None, None, None]
    oc = ["errored", "dependency_unavailable", "errored", "errored", "dependency_unavailable"]
    msg = [
        "ModuleNotFoundError: No module named 'google.colab'",
        "ModuleNotFoundError: No module named 'win32com'",
        "ImportError: attempted relative import with no known parent package",
        "ModuleNotFoundError: No module named 'torch'",
        "ModuleNotFoundError: No module named 'torchvision'",
    ]
    reason = _repro_failure_reason(oc, msg)
    assert reason == ["platform", "platform", "how_to_run", "package", "package"]
    assert _repro_check_group(oc, reason, msg) == [
        "not_checkable",
        "not_checkable",
        "not_checkable",
        "dependency",
        "dependency",
    ]
    tb, rr = _frame([f"{c}.py" for c in "abcde"], oc, msg)
    out = _repro_checklist_section(tb, rr)
    assert isinstance(out, str)
    assert (
        "2 could not run because a package or program was not available, and 3 could not be "
        "checked automatically (1 needed Google Colab, 1 needed Windows, 1 needed to be started "
        "as part of its package)" in out
    )
    assert "**A script runs only on one platform.**" in out
    assert "<code>a.py</code> needs Google Colab." in out
    assert "| b.py | Could not be checked | It needs Windows" in out
    assert (
        "**A script is part of a package of your code, and Python cannot run it on its own.**"
        in out
    )
    # neither is advised as an older package version or a package not installed
    assert '<code>a.py</code>: "' not in out
    assert "- <code>c.py</code>\n\nThe code loads it" not in out
    light = _repro_run_light(
        "green", pd.DataFrame({"outcome": oc[:3], "reason": reason[:3], "error": msg[:3]})
    )
    assert light == "yellow"


def test_keys_or_sign_ins_are_not_errors_of_the_code() -> None:
    m = [
        "ERROR: set ZENODO_TOKEN (deposit:write). Not publishing.",
        "Error: Missing configuration. Either set OPENAI_API_KEY or pass api_key",
        "File \"/usr/lib/python3.11/os.py\", line 679, in __getitem__\nKeyError: 'WORKSPACE_CDR'",
        "google.auth.exceptions.DefaultCredentialsError: Your default credentials were not found.",
        "KeyError: 'AGE'",
    ]
    assert _repro_failure_reason(["errored"] * 5, m) == [*["settings"] * 4, "other"]


def test_advice_is_in_the_scripts_language_code_from_a_knitted_report_is_not_blamed() -> None:
    fn = ["a.R", "b.py", "c.ipynb", "rep.R"]
    err = [
        "Error: object 'dat' not found",
        "NameError: name 'df' is not defined",
        "NameError: name 'display' is not defined",
        "Error: object 'd' not found",
    ]
    tb, rr = _frame(fn, ["errored"] * 4, err)
    rr["reason"] = _repro_failure_reason(rr["outcome"].tolist(), rr["error"].tolist())
    # a name Jupyter provides is something our run lacks
    assert rr["reason"][2] == "environment"
    failed = _repro_from_report_failed(rr, ["rep.R"])
    assert failed == [False, False, False, True]
    rr.loc[failed, "reason"] = "from_report"
    assert rr["reason"][3] == "from_report"
    assert _repro_check_group(["errored"] * 4, rr["reason"].tolist(), err)[3] == "not_checkable"
    out = _repro_checklist_section(tb, rr)
    assert isinstance(out, str)
    assert (
        "**A script uses something it does not create or load itself.**\n\n- <code>a.R</code>"
        in out
    )
    assert (
        "**A Python script or notebook uses something it does not create or import itself.**"
        "\n\n- <code>b.py</code>" in out
    )
    assert "Restart the kernel" in out
    assert "taken from a knitted report" in out
    # paths to files the deposit does not hold are named, not "every path works"
    assert "1 path names a file the repository does not hold: `x.csv`" in _repro_path_fix_text(
        {}, absent=["x.csv"]
    )
    assert (
        _repro_path_fix_text({})
        == "Every data path in the code works in the repository as deposited."
    )


def test_a_pattern_of_names_gets_the_folder_of_the_file_it_matched_not_the_file() -> None:
    rw = pd.DataFrame(
        {
            "ref": ["./images/*.jpg"],
            "basename": ["*.jpg"],
            "matched": [True],
            "target": ["../docs/images/_.jpg"],
            "ambiguous": [False],
            "n_candidates": pd.array([1], dtype="Int64"),
            "is_call": [False],
        }
    )
    out = _repro_apply_rewrites(["for name in glob.glob('./images/*.jpg'):"], rw)
    assert "glob.glob('../docs/images/*.jpg')" in out[0]


def test_the_report_and_the_summary_give_the_scripts_by_group() -> None:
    oc = [
        "ran_ok",
        "errored",
        "errored",
        "dependency_unavailable",
        "timed_out",
        "needs_a_person",
        "errored",
        "docker_failed",
    ]
    err = [
        "",
        "Error: object 'x' not found",
        "Error: OPENCAGE_KEY is not set",
        "",
        "timed out after 60s",
        "",
        "Error: object 'y' not found",
        "x",
    ]
    tb, rr = _frame([f"{c}.R" for c in "abcdefgh"], oc, err)
    out = _repro_checklist_section(tb, rr, timeout=60)
    assert isinstance(out, str)
    assert (
        "**1 of 8 scripts ran to the end**: 2 did not reproduce, 1 could not run because a "
        "package or program was not available, 1 did not finish within our time limit, 2 could "
        "not be checked automatically (1 needed a key or a setting of your computer, 1 needed a "
        "person), and 1 could not be run because Docker failed." in out
    )
    rr["reason"] = _repro_failure_reason(rr["outcome"].tolist(), rr["error"].tolist())
    assert _repro_groups_sentence(rr) == (
        "Of the others, 2 did not reproduce, 1 could not run because a package or program was "
        "not available, 1 did not finish within the time limit, and 2 could not be checked "
        "automatically."
    )
    assert (
        _repro_groups_sentence(rr.iloc[[0, 1, 5]])
        == "Of the others, 1 did not reproduce and 1 could not be checked automatically."
    )
    assert _repro_groups_sentence(rr.iloc[[0]]) is None


def test_the_light_after_a_run_gives_yellow_for_a_package_that_was_not_loaded() -> None:
    # a package that was not loaded is not available, not an error of the code (#580)
    rr = pd.DataFrame({"outcome": ["ran_ok", "errored"], "reason": [None, "package"]})
    assert _repro_run_light("green", rr) == "yellow"


def test_calls_that_need_a_screen_are_replaced_by_calls_that_need_none() -> None:
    txt: list[str | None] = [
        "plot(1:3)",
        "dev.print(pdf, file = 'a.pdf')",
        "if (save) dev.copy2pdf(file = 'b.pdf',",
        "  width = 5)",
        "windows(width = 8); x11()",
        "df %>% View()",
        "grDevices::savePlot('c', type = 'png')",
        "# View(df) in a comment",
        "my.View(x); dev.printer(x); quartz.save('d.png')",
    ]
    new, calls = _repro_replace_screen_calls(txt)
    # the script keeps its lines, and names each call replaced
    assert len(new) == len(txt)
    assert sorted(calls) == sorted(
        ["dev.print()", "dev.copy2pdf()", "windows()", "x11()", "View()", "savePlot()"]
    )
    assert [new[i] for i in (0, 7, 8)] == [txt[i] for i in (0, 7, 8)]
    assert _repro_replace_screen_calls([]) == ([], [])


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not available")
def test_the_replaced_calls_parse_and_write_their_files_without_a_screen(tmp_path: Path) -> None:
    code, _ = _repro_replace_screen_calls(
        [
            "graphics::plot(1:3)",
            "dev.print(grDevices::pdf, file = 'a.pdf')",
            "dev.copy2pdf(file = 'b.pdf')",
            "savePlot('c', type = 'pdf')",
            "windows(width = 8)",
            "View(mtcars)",
            "mtcars |> View()",
        ]
    )
    script = tmp_path / "s.R"
    script.write_text(
        'grDevices::pdf(file.path(getwd(), "open.pdf"))\nopen <- grDevices::dev.cur()\n'
        + "\n".join(str(x) for x in code)
        + "\nstopifnot(identical(grDevices::dev.cur(), open))\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["Rscript", "--no-save", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert all((tmp_path / f).exists() for f in ("a.pdf", "b.pdf", "c.pdf"))
