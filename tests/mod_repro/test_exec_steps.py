"""The execution step of reproducibility_check for R code, with the runners faked.

:mod:`metacheck.modules._reproducibility_exec` ports section 7b of metacheck 0.4.3.7's R
block: the first run, the second run in a corrected order or with ``library()`` calls,
the runs with R's old defaults, with a fetched file, after another script wrote a file or
with a file of the deposit put where the script looked, the relabelling of outcomes that
say nothing about the code, and the report's "Execution" part. Here R's runners
(``repro_run_scripts``, ``repro_run_scripts_docker``) and the installs are fakes that
answer from the script they are given, so every pass runs without R or Docker. The
parity goldens of the executed cases are replayed the same way: the fake gives each
script the outcome R recorded, and the module must then give R's report.
"""

from __future__ import annotations

import datetime as dt
import os
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import ModuleOutput, module_run
from metacheck.modules import _reproducibility_exec as rx
from metacheck.report.blocks import ReportTable
from metacheck.repro import core, docker, recorded, runsupport
from metacheck.repro.core import _run_frame, _run_row
from tests.mod_repro.test_reproducibility_check import chain, code_tbl_row, report_text

DOCKER_OK = {"ok": True, "msg": ""}
NOT_FOUND = "cannot open file '{}': No such file or directory"


# -- the fakes -----------------------------------------------------------------------------


@dataclass
class Call:
    """One call of a runner: one pass over some of the scripts."""

    files: list[str]
    order: list[str]
    kwargs: dict[str, Any]
    texts: dict[str, str]
    run_dirs: dict[str, str]


Respond = Callable[[int, str, Call], Mapping[str, Any] | None]


class Runner:
    """A fake of both script runners. *respond(pass, file, call)* gives the fields of the
    script's row (``outcome`` ``ran_ok`` when it gives none); a file the module says does
    not parse is ``not_parsed``, as the real runners have it."""

    def __init__(self, respond: Respond | None = None) -> None:
        self.respond = respond or (lambda *_: None)
        self.calls: list[Call] = []
        self.parses = True  # False: *respond* answers for those files too

    def __call__(self, run_tbl: pd.DataFrame, order: Any, *args: Any, **kw: Any) -> pd.DataFrame:
        files = [str(f) for f in run_tbl["file_name"]] if len(run_tbl) else []
        paths = dict(zip(files, run_tbl["script_path"], strict=True)) if files else {}
        dirs = dict(zip(files, run_tbl["run_dir"], strict=True)) if files else {}
        texts = {f: Path(p).read_text(encoding="utf-8") for f, p in paths.items()}
        call = Call(files, list(order), dict(kw), texts, dirs)
        n = len(self.calls)
        self.calls.append(call)
        parses = kw.get("parses") if self.parses else None
        rows = []
        for f in order:
            if f not in paths:
                continue
            if parses is not None and f in parses.index and not bool(parses[f]):
                rows.append(_run_row(f, "not_parsed", "the file does not parse"))
                continue
            fields = dict(self.respond(n, f, call) or {})
            rows.append(_run_row(f, **{"outcome": "ran_ok", **fields}))
        return _run_frame(rows)

    def files(self, n: int) -> list[str]:
        return sorted(self.calls[n].files)


def errored(error: str, **kw: Any) -> dict[str, Any]:
    return {"outcome": "errored", "error": error, "stderr": f"Error: {error}", **kw}


def undefined(var: str) -> dict[str, Any]:
    return errored(f"object '{var}' not found", error_type="undefined_variable", undefined_var=var)


def no_function(fn: str) -> dict[str, Any]:
    return errored(
        f'could not find function "{fn}"', error_type="undefined_variable", undefined_var=fn
    )


@pytest.fixture
def no_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """No R is started: R's answers (base packages, exports, load checks) are "unknown"."""
    for mod in (core, runsupport, recorded):
        monkeypatch.setattr(mod, "_run_r", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")  # sandbox = "process" needs it
    monkeypatch.setattr(runsupport, "_rscript", lambda: None)
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, no_r: None) -> Runner:
    """The fake of both runners; a test sets its ``respond``."""
    fake = Runner()
    monkeypatch.setattr(core, "repro_run_scripts", fake)
    monkeypatch.setattr(docker, "repro_run_scripts_docker", fake)
    return fake


def deposit(tmp_path: Path, files: Mapping[str, str]) -> Path:
    d = tmp_path / "deposit"
    for name, text in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def run(d: Path, **kw: Any) -> ModuleOutput:
    """``module_run(local_path = d, execute = TRUE)``, by default with ``sandbox = "process"``."""
    kw.setdefault("sandbox", "process")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return module_run(
            pc.test_paper(),
            "reproducibility_check",
            local_path=str(d),
            local_only=True,
            execute=True,
            **kw,
        )


def outcomes(mo: ModuleOutput) -> dict[str, str]:
    rr = mo.run_results
    return dict(zip(rr["file_name"], rr["outcome"], strict=True))


def mods(mo: ModuleOutput, kind: str) -> list[tuple[str, str]]:
    m = mo.modifications
    m = m[m["change_type"] == kind]
    return list(zip(m["file_name"], m["detail"], strict=True))


def table(mo: ModuleOutput, column: str) -> pd.DataFrame:
    """The report's table with *column*."""

    def flat(x: Any) -> list[Any]:
        return [y for v in x for y in flat(v)] if isinstance(x, list) else [x]

    (found,) = [
        t.data for t in flat(mo.report) if isinstance(t, ReportTable) and column in t.data.columns
    ]
    return found


def exists_from(call: Call, f: str, rel: str) -> bool:
    return os.path.exists(os.path.join(call.run_dirs[f], rel))


# -- the first run ---------------------------------------------------------------------------


def test_the_first_run_gets_the_deadline_of_all_scripts_together(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(tmp_path, {"a.R": "x <- 1\n", "b.R": "y <- 2\n"})
    start = time.time()
    mo = run(d)
    (call,) = runner.calls
    assert call.order == ["a.R", "b.R"]
    assert abs(call.kwargs["deadline"] - (start + 3600)) < 30  # total_timeout = 3600
    assert call.kwargs["timeout"] == 600
    assert call.kwargs["failed_deps"] == []
    assert call.kwargs["lib_dir"] is None  # nothing was installed
    assert outcomes(mo) == {"a.R": "ran_ok", "b.R": "ran_ok"}
    text = report_text(mo)
    assert "**The code was run.** Each script ran in an isolated subprocess" in text
    assert "Of 2 scripts: 2 ran without error, 0 errored" in text
    assert "Running against current package versions" in text


def test_no_total_timeout_gives_the_runner_no_deadline(tmp_path: Path, runner: Runner) -> None:
    run(deposit(tmp_path, {"a.R": "x <- 1\n"}), total_timeout=float("inf"))
    assert "deadline" not in runner.calls[0].kwargs


def test_docker_gets_the_image_the_net_limits_only_with_internet_and_its_relabels(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(tmp_path, {"a.R": "x <- 1\n"})
    run(d, sandbox="docker")
    kw = runner.calls[0].kwargs
    assert kw["image"] == "ghcr.io/scienceverse/metacheck_r_large:latest"
    assert kw["net"] is None  # no network
    runner.calls.clear()
    mo = run(d, sandbox="docker", internet=True)
    assert runner.calls[0].kwargs["net"] is not None
    assert "**The code was run.** Each script ran in an isolated Docker container" in report_text(
        mo
    )


@pytest.mark.parametrize(
    ("error", "sandbox", "outcome", "said"),
    [
        (
            "docker: Error response from daemon: no such image",
            "docker",
            "docker_failed",
            "**Docker failed for 1 script, so it was not run.**",
        ),
        # only Docker's own failure counts as one
        ("Error response from daemon", "process", "errored", None),
        (
            "menu() cannot be used non-interactively",
            "process",
            "needs_a_person",
            "**1 script needs a person** to choose a file",
        ),
        (
            "cannot open URL 'https://example.org/x.csv'",
            "docker",
            "needs_internet",
            "**1 script needs the internet**",
        ),
        # the network is not off outside Docker
        ("cannot open URL 'https://example.org/x.csv'", "process", "errored", None),
        ("cannot allocate vector of size 3.2 Gb", "process", "out_of_memory", None),
    ],
)
def test_outcomes_that_say_nothing_about_the_code(
    tmp_path: Path, runner: Runner, error: str, sandbox: str, outcome: str, said: str | None
) -> None:
    runner.respond = lambda n, f, call: errored(error)
    mo = run(deposit(tmp_path, {"a.R": "x <- 1\n"}), sandbox=sandbox)
    rr = mo.run_results
    assert rr["outcome"].tolist() == [outcome]
    if outcome != "errored":
        assert rr["error_type"].isna().all()
        assert mo.traffic_light != "red"
    if said is not None:
        assert said in report_text(mo)


def test_a_script_that_stops_without_a_message_gets_one_and_every_failure_a_reason(
    tmp_path: Path, runner: Runner
) -> None:
    runner.respond = lambda n, f, call: (
        {"outcome": "errored", "error": "", "stderr": ""}
        if f == "quiet.R"
        else errored(NOT_FOUND.format("x.csv"))
        if f == "file.R"
        else None
    )
    d = deposit(tmp_path, {"quiet.R": "q()\n", "file.R": "x <- 1\n", "ok.R": "y <- 1\n"})
    rr = run(d).run_results.set_index("file_name")
    assert rr.loc["quiet.R", "error"] == "the script stopped without a message"
    assert rr.loc["file.R", "reason"] == "file"
    assert pd.isna(rr.loc["ok.R", "reason"])


def test_a_model_file_gets_its_own_outcome_and_is_not_run(tmp_path: Path, runner: Runner) -> None:
    jags = (
        "model {\n  for (i in 1:N) {\n    y[i] ~ dnorm(mu, tau)\n  }\n  mu ~ dnorm(0, 0.001)\n}\n"
    )
    mo = run(deposit(tmp_path, {"model.R": jags, "fit.R": "x <- 1\n"}))
    assert outcomes(mo)["model.R"] == "jags_model"
    assert "Of 1 script:" in report_text(mo)  # a model file is not counted as a script


def test_a_sourced_script_runs_only_inside_the_script_that_sources_it(
    tmp_path: Path, runner: Runner
) -> None:
    # main.R loads dplyr and then sources a.R, which uses %>% without loading anything
    d = deposit(
        tmp_path,
        {
            "main.R": 'library(dplyr)\nsource("a.R")\n',
            "a.R": "x <- data.frame(v = 1:3) %>% mutate(w = v * 2)\nprint(x)\n",
        },
    )
    mo = run(d)
    assert runner.calls[0].files == ["main.R"]
    assert outcomes(mo) == {"main.R": "ran_ok", "a.R": "run_by_source"}
    assert mo.traffic_light == "green"
    text = report_text(mo)
    assert "**Run by another script**" in text
    assert "1 script is run by another script with `source()`, so it was not run on its own" in text
    assert ", and 1 was run by another script with `source()`." in text


def test_a_here_mark_is_put_at_the_top_unless_the_project_has_one(
    tmp_path: Path, runner: Runner
) -> None:
    seen: list[bool] = []

    def respond(n: int, f: str, call: Call) -> None:
        seen.append(exists_from(call, f, ".here"))

    runner.respond = respond
    run(deposit(tmp_path / "a", {"a.R": "x <- 1\n"}))
    run(deposit(tmp_path / "b", {"a.R": "x <- 1\n", "proj.Rproj": "Version: 1.0\n"}))
    assert seen == [True, False]


def test_the_output_is_searched_only_in_part_and_the_report_says_so(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runsupport, "_max_output_lines", lambda: 2)
    runner.respond = lambda n, f, call: {"stdout": "a\nb\nc\nd\ne\n"}
    mo = run(deposit(tmp_path, {"a.R": "x <- 1\n"}))
    assert "*Statistical results were searched for in the first 2 lines of the output of `a.R`" in (
        report_text(mo)
    )


# -- the second run: another script's objects and library() calls ------------------------------


def test_a_script_that_uses_what_another_script_defines_runs_after_it(
    tmp_path: Path, runner: Runner
) -> None:
    # port of metacheck's "a script that uses what another script defines runs after it"
    d = deposit(
        tmp_path,
        {
            "one/d.csv": "a,b\n1,2\n",
            "one/make.R": 'dat <- read.csv("d.csv")\nx <- nrow(dat)\n',
            "two/use.R": "stopifnot(x > 0)\n",
            # defined nowhere: stays a failure
            "two/broken.R": "stopifnot(nothing > 0)\n",
            # two scripts in other folders define it: not guessed
            "one/w1.R": "w <- 1\n",
            "w2.R": "w <- 2\n",
            "two/unclear.R": "stopifnot(w > 0)\n",
            # two scripts that need each other do not load each other for ever
            "ca.R": "b <- 1\nstopifnot(a == 1)\n",
            "cb.R": "a <- 1\nstopifnot(b == 1)\n",
        },
    )
    needs = {"use.R": ("x", "make.R"), "broken.R": ("nothing", None), "unclear.R": ("w", None)}
    needs |= {"ca.R": ("a", "cb.R"), "cb.R": ("b", "ca.R")}

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if f not in needs:
            return None
        var, definer = needs[f]
        if n > 0 and definer is not None and definer in call.texts[f]:
            return None  # the definer was loaded first
        return undefined(var)

    runner.respond = respond
    mo = run(d)
    oc = outcomes(mo)
    assert oc["use.R"] == "ran_ok"
    assert oc["make.R"] == "ran_ok"
    assert oc["broken.R"] == "errored"
    assert oc["unclear.R"] == "errored"
    assert sorted(mods(mo, "loaded_first")) == [("ca.R", "cb.R"), ("use.R", "make.R")]
    # only what the second run changes runs again
    assert runner.files(1) == ["ca.R", "cb.R", "use.R"]
    assert mo.run_results.attrs == {"reran_for_order": True, "reran_for_reorder": True}
    text = report_text(mo)
    assert "`use.R` after `make.R`" in text
    assert "**2 scripts use an object or function that only another script defines**" in text
    assert "We inferred which script supplies it and re-ordered so that script runs first" in text
    assert "**Undefined-variable errors**" in text
    undef = table(mo, "Missing variable").set_index("File")
    assert undef.loc["broken.R", "Defined in"] == "(not defined anywhere in the paper's code)"
    assert undef.loc["unclear.R", "Defined in"] == "w1.R, w2.R"


def test_of_several_definers_the_one_in_the_scripts_own_folder_is_loaded(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(
        tmp_path,
        {"one/v1.R": "v <- 1\n", "two/v2.R": "v <- 2\n", "two/user.R": "stopifnot(v > 0)\n"},
    )
    runner.respond = lambda n, f, call: (
        undefined("v") if f == "user.R" and "v2.R" not in call.texts[f] else None
    )
    mo = run(d)
    assert outcomes(mo)["user.R"] == "ran_ok"
    assert mods(mo, "loaded_first") == [("user.R", "v2.R")]


@pytest.mark.parametrize("reader_first", ["errored", "timed_out"])
def test_the_second_run_also_runs_the_readers_of_a_changed_script_unless_they_timed_out(
    tmp_path: Path, runner: Runner, reader_first: str
) -> None:
    d = deposit(
        tmp_path,
        {
            "1_use.R": 'print(x)\nwrite.csv(data.frame(a = x), "use_out.csv")\n',
            "2_def.R": "x <- 1\n",
            "3_read.R": 'y <- read.csv("use_out.csv")\n',
        },
    )

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if n == 0 and f == "1_use.R":
            return undefined("x")
        if n == 0 and f == "3_read.R":
            if reader_first == "timed_out":
                return {"outcome": "timed_out", "error": "timed out", "error_type": "timeout"}
            return errored(NOT_FOUND.format("use_out.csv"))
        return None

    runner.respond = respond
    mo = run(d)
    if reader_first == "errored":
        assert runner.files(1) == ["1_use.R", "3_read.R"]
        assert outcomes(mo)["3_read.R"] == "ran_ok"
    else:
        assert runner.files(1) == ["1_use.R"]  # it would only time out again (#581)
        assert outcomes(mo)["3_read.R"] == "timed_out"
    assert "2_def.R" not in runner.calls[1].files  # unchanged: its first result stays


def test_a_script_the_second_run_changed_but_has_no_time_for_is_skipped(
    tmp_path: Path, runner: Runner
) -> None:
    # port of metacheck's "a script the second run changed but has no time for is skipped,
    # not its first result (#581)": 2_def.R uses up the time
    d = deposit(tmp_path, {"1_use.R": "print(x)\n", "2_def.R": "x <- 1\nSys.sleep(60)\n"})

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if n == 0 and f == "1_use.R":
            return undefined("x")
        if f == "2_def.R":
            return {"outcome": "timed_out", "error": "timed out after 60s", "error_type": "timeout"}
        # the second run of 1_use.R: the time for all scripts together runs out
        return {"outcome": "timed_out", "error": "timed out", "error_type": "deadline"}

    runner.respond = respond
    mo = run(d, timeout=60, total_timeout=8)
    assert mo.run_results.attrs["reran_for_order"] is True
    assert outcomes(mo) == {"1_use.R": "skipped_time_limit", "2_def.R": "timed_out"}
    assert runner.files(1) == ["1_use.R"]
    assert runner.calls[1].kwargs["deadline"] == runner.calls[0].kwargs["deadline"]
    assert (
        "1 script was not started, or stopped early, because the time for all scripts together"
        in report_text(mo)
    )


def test_a_script_relying_on_another_scripts_library_calls_gets_them_added(
    tmp_path: Path, runner: Runner
) -> None:
    # port of metacheck's test of the same name: 1_prep.R loads dplyr, 2_analysis.R uses
    # %>% and mutate() without loading it, and nothing sources it
    d = deposit(
        tmp_path,
        {
            "1_prep.R": "library(dplyr)\ny <- 1\n",
            "2_analysis.R": "x <- data.frame(v = 1:3) %>% mutate(w = v * 2)\nprint(x)\n",
        },
    )
    runner.respond = lambda n, f, call: (
        no_function("%>%")
        if f == "2_analysis.R" and "library(dplyr)" not in call.texts[f]
        else None
    )
    mo = run(d)
    assert outcomes(mo)["2_analysis.R"] == "ran_ok"
    assert mods(mo, "library_injected") == [("2_analysis.R", "library(dplyr)")]
    assert runner.files(1) == ["2_analysis.R"]
    text = report_text(mo)
    assert "Reproducible after adding `library()` calls: 2_analysis.R" in text
    assert "**`library()` calls added**" in text
    assert 'a script hit a `could not find function "..."` error' in text


def test_the_second_run_runs_only_what_it_changes(tmp_path: Path, runner: Runner) -> None:
    # port of metacheck's "the second pass runs only what it changes" (#581): a.R gets
    # library(magrittr); b.R, which timed out, is not run again
    d = deposit(
        tmp_path,
        {
            "a.R": "y <- c(1, 2, 3) %>% sum()\nprint(y)\n",
            "b.R": "library(magrittr)\nx <- 1\nSys.sleep(60)\n",
        },
    )

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if f == "b.R":
            return {"outcome": "timed_out", "error": "timed out after 10s", "error_type": "timeout"}
        return None if "library(magrittr)" in call.texts[f] else no_function("%>%")

    runner.respond = respond
    mo = run(d, timeout=10, total_timeout=35)
    assert outcomes(mo) == {"a.R": "ran_ok", "b.R": "timed_out"}
    assert [c.files for c in runner.calls] == [["a.R", "b.R"], ["a.R"]]


# -- the later runs: old defaults, a fetched file, a file written later ----------------------------


def test_old_defaults_run_again_with_the_script_waiting_for_its_file(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(
        tmp_path,
        {
            "data.csv": "g,v\na,1\nb,2\n",
            "m.R": 'd <- read.csv("data.csv")\nd$g <- relevel(d$g, "a")\nwrite.csv(d, "out.csv")\n',
            "r.R": 'o <- read.csv("out.csv")\n',
        },
    )
    factors = "formals(f)$stringsAsFactors <- TRUE"

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if factors in call.texts[f]:
            return None
        if f == "m.R":
            return errored("'relevel' only for (unordered) factors")
        return errored(NOT_FOUND.format("out.csv"))

    runner.respond = respond
    mo = run(d)
    assert runner.files(1) == ["m.R", "r.R"]
    assert outcomes(mo) == {"m.R": "ran_ok", "r.R": "ran_ok"}
    assert [k for k, _ in mods(mo, "old_default")] == ["m.R", "r.R"]
    assert (
        "**2 scripts ran again with a default R had when the code was written** (text read as "
        "factors, as R did before version 4.0)" in report_text(mo)
    )


def test_a_file_a_script_could_not_open_is_fetched_and_the_script_runs_again(
    paper_p1: pc.Paper, tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # port of metacheck's "a file the code names, or a script could not open, is fetched"
    from metacheck.archives import download

    d = deposit(
        tmp_path,
        {
            "named.R": 'stopifnot(file.exists("fig.png"))\n',
            "built.R": 'f <- paste0("la", "te.csv")\nx <- read.csv(f)\nstopifnot(nrow(x) > 0)\n',
        },
    )
    code_tbl = code_tbl_row(
        paper_p1, ["named.R", "built.R"], [str(d / "named.R"), str(d / "built.R")]
    )
    structure_df = pd.DataFrame(
        {
            "paper_id": "p1",
            "repo_url": "https://zenodo.org/records/1",
            "file_name": ["fig.png", "late.csv"],
            "file_path": ["fig.png", "late.csv"],
            "file_url": ["https://zenodo.org/f/1", "https://zenodo.org/f/2"],
            "file_location": [None, None],
            "file_size": [100, 100],
        }
    )
    asked: list[list[str]] = []
    got = tmp_path / "got"
    got.mkdir()

    def fake_download(files: pd.DataFrame, **kw: Any) -> pd.DataFrame:
        asked.append(files["file_name"].tolist())
        out = files.copy()
        locs = []
        for f in files["file_name"]:
            (got / f).write_text("a\n1\n")
            locs.append(str(got / f))
        out["file_location"] = locs
        return out

    monkeypatch.setattr(download, "download_repo_files", fake_download)

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        need = {"named.R": "fig.png", "built.R": "late.csv"}[f]
        return None if exists_from(call, f, need) else errored(NOT_FOUND.format(need))

    runner.respond = respond
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = module_run(
            chain(paper_p1, code_tbl, structure_df),
            "reproducibility_check",
            execute=True,
            sandbox="process",
        )
    assert asked == [["fig.png"], ["late.csv"]]
    assert outcomes(mo) == {"named.R": "ran_ok", "built.R": "ran_ok"}
    assert [c.files for c in runner.calls] == [["named.R", "built.R"], ["built.R"]]


def test_a_file_that_was_not_downloaded_means_the_script_could_not_be_tried(
    paper_p1: pc.Paper, tmp_path: Path, runner: Runner
) -> None:
    d = deposit(tmp_path, {"big.R": 'x <- read.csv(paste0("bi", "g.csv"))\n'})
    code_tbl = code_tbl_row(paper_p1, "big.R", str(d / "big.R"))
    structure_df = pd.DataFrame(
        {"paper_id": "p1", "file_name": ["big.csv"], "file_location": [None], "file_size": [500]}
    )
    runner.respond = lambda n, f, call: errored(NOT_FOUND.format("big.csv"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = module_run(
            chain(paper_p1, code_tbl, structure_df),
            "reproducibility_check",
            execute=True,
            sandbox="process",
        )
    assert outcomes(mo) == {"big.R": "not_downloaded"}
    assert mo.traffic_light != "red"
    text = report_text(mo)
    assert "**1 script could not be tried, because a file it needs was not downloaded**" in text
    assert "Of 0 scripts:" in text  # it is not counted as a script that did not reproduce


def test_a_file_another_script_writes_later_runs_the_script_again(
    tmp_path: Path, runner: Runner
) -> None:
    # names built while the scripts run: the order cannot see them
    d = deposit(
        tmp_path,
        {
            "a.R": 'x <- read.csv(paste0("ge", "n.csv"))\n',
            "b.R": 'write.csv(data.frame(a = 1), paste0("ge", "n.csv"))\n',
        },
    )

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if f == "b.R":
            Path(call.run_dirs[f], "gen.csv").write_text("a\n1\n")
            return None
        # a.R ran before b.R wrote the file
        return (
            None
            if n > 0 and exists_from(call, f, "gen.csv")
            else errored(NOT_FOUND.format("gen.csv"))
        )

    runner.respond = respond
    mo = run(d)
    assert [c.files for c in runner.calls] == [["a.R", "b.R"], ["a.R"]]
    assert outcomes(mo) == {"a.R": "ran_ok", "b.R": "ran_ok"}
    assert mods(mo, "run_again") == [("a.R", "run once more after another script wrote gen.csv")]
    assert "**1 script stopped on a file another script writes**" in report_text(mo)


def test_a_script_still_stopping_on_the_same_file_is_not_run_again_and_again(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(
        tmp_path,
        {
            "a.R": 'x <- read.csv(paste0("ge", "n.csv"))\n',
            "b.R": 'write.csv(data.frame(a = 1), paste0("ge", "n.csv"))\n',
        },
    )

    def respond(n: int, f: str, call: Call) -> Mapping[str, Any] | None:
        if f == "b.R":
            Path(call.run_dirs[f], "gen.csv").write_text("a\n1\n")
            return None
        return errored(NOT_FOUND.format("gen.csv"))

    runner.respond = respond
    mo = run(d)
    assert len(runner.calls) == 2
    assert outcomes(mo)["a.R"] == "errored"


def test_a_file_the_deposit_holds_in_another_folder_is_copied_where_the_script_looks(
    tmp_path: Path, runner: Runner
) -> None:
    d = deposit(
        tmp_path,
        {
            "exploratory/VEG.csv": "a\n1\n",
            "exploratory/forest_age.R": 'd <- read.csv(paste0("./data/", "VEG.csv"))\n',
        },
    )
    runner.respond = lambda n, f, call: (
        None
        if exists_from(call, f, "data/VEG.csv")
        else errored(NOT_FOUND.format("./data/VEG.csv"))
    )
    mo = run(d)
    assert outcomes(mo) == {"forest_age.R": "ran_ok"}
    assert len(runner.calls) == 2
    (row,) = mods(mo, "file_placed")
    assert row[0] == "forest_age.R"
    assert "**A script looked for a file in another folder than the deposit has it**" in (
        report_text(mo)
    )


def test_a_git_lfs_pointer_the_script_stopped_on_is_a_missing_file(
    tmp_path: Path, runner: Runner
) -> None:
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
        "size 12345\n"
    )
    d = deposit(tmp_path, {"m.rds": pointer, "a.R": 'm <- readRDS(paste0("m", ".rds"))\n'})
    runner.respond = lambda n, f, call: errored('Error in readRDS("m.rds") : unknown input format')
    mo = run(d)
    assert mo.run_results["reason"].tolist() == ["file"]
    assert "while reading a file the deposit holds only as a Git LFS pointer**" in report_text(mo)


# -- what is installed -------------------------------------------------------------------------------


def _installs(calls: list[dict[str, Any]], failed: tuple[str, ...] = ()) -> Callable[..., Any]:
    def fake(install_deps: pd.DataFrame, lib_dir: str, *args: Any, **kw: Any) -> pd.DataFrame:
        pkgs = install_deps["package"].tolist()
        calls.append({"packages": pkgs, "lib_dir": lib_dir, **kw})
        os.makedirs(lib_dir, exist_ok=True)
        return pd.DataFrame(
            {
                "package": pkgs,
                "source": ["cran"] * len(pkgs),
                "installed": [p not in failed for p in pkgs],
                "message": ["boom" if p in failed else "" for p in pkgs],
                "category": ["source_unknown" if p in failed else None for p in pkgs],
            }
        )

    return fake


def test_installs_have_a_limit_only_when_one_was_asked_for(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(core, "repro_install_deps", _installs(calls, failed=("mypkg",)))
    d = deposit(tmp_path, {"a.R": "library(digest)\nlibrary(mypkg)\nx <- 1\n"})
    start = time.time()
    mo = run(d, install_missing=True)
    assert calls[0]["packages"] == ["digest", "mypkg"]
    assert "deadline" not in calls[0]  # as before: no limit
    assert runner.calls[0].kwargs["failed_deps"] == ["mypkg"]
    assert runner.calls[0].kwargs["lib_dir"] == calls[0]["lib_dir"]
    text = report_text(mo)
    assert (
        "Dependencies were installed into a throwaway library before running: 1 succeeded, "
        "1 failed. digest, mypkg were installed." in text
    )
    assert (
        "**Failed:** mypkg [not on CRAN or Bioconductor, and the code does not say where it "
        "comes from] (boom)" in text
    )

    calls.clear()
    run(d, install_missing=True, install_timeout=120)
    assert abs(calls[0]["deadline"] - (start + 120)) < 30
    calls.clear()
    run(d, install_missing=True, total_timeout=dt.datetime.now() + dt.timedelta(hours=2))
    assert calls[0]["deadline"] is not None


def test_nothing_installed_means_a_missing_package_is_unavailable_not_an_error(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core, "repro_install_deps", lambda *a, **k: pytest.fail("installed"))
    run(deposit(tmp_path, {"a.R": "library(digest)\nx <- 1\n"}), install_missing=False)
    assert runner.calls[0].kwargs["failed_deps"] == ["digest"]


def test_docker_installs_in_its_image_within_the_per_script_time(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(docker, "repro_install_deps_docker", _installs(calls))
    d = deposit(tmp_path, {"a.R": 'install.packages("digest")\nlibrary(digest)\nx <- 1\n'})
    mo = run(d, sandbox="docker", install_missing=True)
    assert calls[0]["image"] == "ghcr.io/scienceverse/metacheck_r_large:latest"
    assert calls[0]["timeout"] == 600
    # the library is a host folder in the sandbox, mounted into the containers, and the
    # runs get it
    assert os.path.basename(calls[0]["lib_dir"]) == "_lib"
    assert runner.calls[0].kwargs["lib_dir"] == calls[0]["lib_dir"]
    # the network is off in Docker: the scripts' own installs are replaced
    assert 'install.packages("digest")' not in runner.calls[0].texts["a.R"]
    assert "**Calls that install packages in the code**" in report_text(mo)


def test_recorded_versions_are_installed_load_checked_and_reported(
    tmp_path: Path, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    monkeypatch.setattr(core, "repro_install_deps", _installs(calls))

    def load_check(install_results: pd.DataFrame, lib_dir: str, **kw: Any) -> pd.DataFrame:
        checked.append(kw)
        return install_results

    monkeypatch.setattr(recorded, "_repro_load_check", load_check)
    # the checklist joins this sentence to its text as a list, which fails (not this step)
    monkeypatch.setattr(recorded, "_repro_recorded_summary", lambda *a, **k: None)
    lock = {
        "R": {"Version": "4.3.1"},
        "Packages": {
            "R6": {"Package": "R6", "Version": "2.4.0", "Source": "Repository"},
            "notloaded": {"Package": "notloaded", "Version": "1.0", "Source": "Repository"},
        },
    }
    d = deposit(
        tmp_path,
        {
            "analysis.R": 'library(R6)\ncat("VERSION", as.character(packageVersion("R6")))\n',
            "renv.lock": orjson.dumps(lock).decode(),
        },
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            pc.test_paper(),
            "reproducibility_check",
            local_path=str(d),
            local_only=True,
            execute=True,
            sandbox="process",
            recorded_versions=True,
        )
    assert any("The R version is not changed" in str(x.message) for x in w)
    assert calls[0]["packages"] == ["R6"]  # only the loaded package
    assert calls[0]["deadline"] is not None  # recorded versions have a limit
    assert checked and checked[0]["image"] is None
    text = report_text(mo)
    assert "Recorded package versions" in text
    assert "Running against current package versions" not in text


# -- other changes made to run the code --------------------------------------------------------


def test_stan_in_the_r_code_gets_the_newer_syntax(tmp_path: Path, runner: Runner) -> None:
    code = (
        'write("data {\n  int N;\n  real y[N];\n}\nparameters {\n  real mu;\n}\nmodel {\n'
        '  y ~ normal(mu, 1);\n}", "m.stan")\n'
    )
    mo = run(deposit(tmp_path, {"stan.R": code}))
    assert "array[N] real y;" in runner.calls[0].texts["stan.R"]
    ((fn, detail),) = mods(mo, "stan_syntax")
    assert fn == "stan.R"
    assert detail.startswith("Stan in the R code, from before 2.33: ")
    assert "**Other changes we made to run the code**" in report_text(mo)


def test_a_file_choice_the_code_names_is_filled_in_and_reported(
    tmp_path: Path, runner: Runner
) -> None:
    # part of metacheck's "scripts that ask a person, or need what a person's computer has"
    d = deposit(
        tmp_path,
        {
            "only.csv": "a\n1\n",
            "second.csv": "a\n1\n",
            "named.R": "# the file is second.csv\na <- read.csv(file.choose())\n",
            "unnamed.R": "a <- read.csv(file.choose())\n",
        },
    )
    runner.respond = lambda n, f, call: (
        errored("file choice cancelled") if "file.choose()" in call.texts[f] else None
    )
    mo = run(d)
    assert outcomes(mo) == {"named.R": "ran_ok", "unnamed.R": "needs_a_person"}
    assert mods(mo, "file_choice_filled") == [("named.R", "second.csv")]
    assert mo.traffic_light != "red"
    text = report_text(mo)
    assert "**1 `file.choose()` call was replaced by the file the code names**" in text
    assert "**1 script needs a person** to choose a file" in text


# -- the install report ------------------------------------------------------------------------------


def test_the_install_report_labels_failures_and_names_unknown_sources() -> None:
    ir = pd.DataFrame(
        {
            "package": ["dplyr", "ggplot2", "oldpkg", "lme4", "mine"],
            "source": ["cran", "github", "cran", "cran", "cran"],
            "installed": [True, False, True, False, False],
            "message": ["", "boom", "", "", ""],
            "via_archive": [False, False, True, False, False],
            "category": [None, "network", None, "weird", "source_unknown"],
        }
    )
    out = rx._r_install_report(ir)
    assert out[0] == (
        "Dependencies were installed into a throwaway library before running: 2 succeeded, "
        "3 failed. dplyr, ggplot2, oldpkg, lme4, mine were installed."
    )
    assert out[1].startswith("**1 of these were no longer on live CRAN**")
    assert out[2] == (
        "**Failed:** ggplot2 [network issue] (boom); lme4 [NA]; mine [not on CRAN or "
        "Bioconductor, and the code does not say where it comes from]"
    )
    assert "mine" in out[3]


# -- R's goldens, replayed -----------------------------------------------------------------------------

#: cases whose statistical output also comes from the objects R captured, which the replay
#: does not give the module
_CAPTURED = {"exec_ok", "exec_ok_no_keep", "exec_jasp", "exec_model_object"}

#: the first run of a case that ran twice: the outcome R's first run had
_FIRST_RUN: dict[str, dict[str, dict[str, Any]]] = {
    "exec_undefined": {"undef_user.R": undefined("helper_value")},
    "exec_library": {"needs_stringr.R": no_function("str_detect")},
}

_REPLAYED = sorted(
    {
        "exec_ok",
        "exec_ok_no_keep",
        "exec_errors",
        "exec_setwd",
        "exec_mixed",
        "exec_font",
        "exec_chatty",
        "exec_jasp",
        "exec_model_object",
        "exec_timeout_10s",
        *_FIRST_RUN,
    }
)


def _golden_rows(golden: dict[str, Any]) -> dict[str, dict[str, Any]]:
    top = dict(zip(golden["value"]["names"], golden["value"]["v"], strict=True))
    rr = top["run_results"]
    cols = {n: c.get("v") for n, c in zip(rr["names"], rr["v"], strict=True)}
    rows = {}
    for i, f in enumerate(cols["file_name"]):
        lines = cols["script_lines"][i]
        rows[f] = {
            "outcome": cols["outcome"][i],
            "error": cols["error"][i],
            "error_type": cols["error_type"][i],
            "undefined_var": cols["undefined_var"][i],
            "stdout": cols["stdout"][i],
            "stderr": cols["stderr"][i],
            "script_lines": list(lines.get("v") or []) if isinstance(lines, dict) else [],
        }
    return rows


@pytest.mark.filterwarnings("ignore::metacheck.core.errors.PytacheckWarning")  # sandbox="process"
@pytest.mark.parametrize("name", _REPLAYED)
def test_rs_recorded_outcomes_give_rs_report(
    name: str, runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parity.canonical import canonical
    from parity.cases import load_cases, run_python
    from parity.compare import Options, compare

    (case,) = [c for c in load_cases("mod_repro") if c.id == f"reproducibility_check.{name}"]
    golden = orjson.loads(case.golden_path.read_bytes())
    if not golden["ok"]:
        pytest.skip(f"R's golden is an error: {golden['error']}")
    rows = _golden_rows(golden)
    first = _FIRST_RUN.get(name, {})
    runner.parses = False  # R's outcome of a file that does not parse is in its golden too
    runner.respond = lambda n, f, call: first[f] if n == 0 and f in first else rows[f]
    # the package that exports the missing function, which R would be asked for
    monkeypatch.setattr(runsupport, "_repro_missing_fn_pkgs", lambda *a, **k: ["stringr"])
    result = run_python(case)
    spec = dict(case.spec.get("compare") or {})
    ignore = ["run_results.captures"]
    if name in _CAPTURED:
        ignore += ["stat_output", "summary_text"]
    spec["ignore"] = [*spec.get("ignore", []), *ignore]
    assert compare(golden["value"], canonical(result), Options.from_case(spec)) == []
    assert len(runner.calls) == (2 if first else 1)


@pytest.fixture
def paper_p1() -> pc.Paper:
    p = pc.test_paper()
    p.paper_id = "p1"
    return p


# -- upstream 0.4.3.28: lib.loc, made folders, files not read, error messages, no time ----------


def static(d: Path, **kw: Any) -> ModuleOutput:
    """``module_run(local_path = d)`` without running anything."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return module_run(
            pc.test_paper(), "reproducibility_check", local_path=str(d), local_only=True, **kw
        )


@pytest.fixture
def unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``code_read()`` fails for fail.R and fail.py, as with a stringi that cannot load."""
    from metacheck.codecheck import core as cc

    real = cc.code_read

    def code_read(path: str, *a: Any, **k: Any) -> Any:
        if str(path).endswith(("fail.R", "fail.py")):
            raise RuntimeError("stringi package required for encoding operations")
        return real(path, *a, **k)

    monkeypatch.setattr(cc, "code_read", code_read)


def test_a_lib_loc_of_the_authors_computer_is_removed_and_reported(
    tmp_path: Path, runner: Runner
) -> None:
    lib = "C:/Users/someone/Documents/R/win-library/4.1"
    d = deposit(
        tmp_path,
        {"libloc.R": f'library(stats, lib.loc = "{lib}")\nx <- median(1:5)\n', "b.R": "y <- 1\n"},
    )
    mo = run(d)
    assert "lib.loc" not in runner.calls[0].texts["libloc.R"]
    assert "library(stats)" in runner.calls[0].texts["libloc.R"]
    assert mods(mo, "lib_loc_removed") == [("libloc.R", lib)]
    assert mo.run_results.set_index("file_name").loc["libloc.R", "outcome"] == "ran_ok"


def test_a_folder_a_write_stopped_on_is_made_and_the_script_run_again(
    tmp_path: Path, runner: Runner
) -> None:
    def respond(n: int, f: str, call: Call) -> dict[str, Any] | None:
        if f == "writes.R" and not exists_from(call, f, "results/tables"):
            return errored(
                "cannot open the connection\ncannot create file 'results/tables/a.csv', "
                "reason 'No such file or directory'"
            )
        return None

    runner.respond = respond
    mo = run(deposit(tmp_path, {"writes.R": 'write.csv(1, file.path(out(), "a.csv"))\n'}))
    assert len(runner.calls) == 2
    assert outcomes(mo) == {"writes.R": "ran_ok"}
    ((fn, detail),) = mods(mo, "folder_made")
    assert fn == "writes.R"
    assert "results/tables" in detail
    assert "wrote into a folder that the deposit does not have" in report_text(mo)


def test_a_script_that_could_not_be_read_is_not_run_and_an_empty_one_is_not_counted(
    tmp_path: Path, runner: Runner, unreadable: None
) -> None:
    d = deposit(
        tmp_path,
        {"fail.R": "stop('boom')\n", "empty.R": "", "ok.R": "x <- median(1:5)\n"},
    )
    mo = run(d)
    assert outcomes(mo) == {"fail.R": "not_read", "empty.R": "empty", "ok.R": "ran_ok"}
    parses = runner.calls[0].kwargs["parses"]
    assert [bool(parses[f]) for f in ("fail.R", "empty.R", "ok.R")] == [False, False, True]
    assert mo.traffic_light == "yellow"
    t = mo.table.set_index("file_name")
    assert t.loc["fail.R", "error_message"].startswith(
        "metacheck could not read the file: stringi package required for encoding operations"
    )
    assert pd.isna(t.loc["empty.R", "error_message"])
    assert int(mo.summary_table["repro_scripts_n"].iloc[0]) == 1
    assert "1 file could not be read" in report_text(mo)


def test_the_static_check_says_a_script_that_could_not_be_read_is_not_run(
    tmp_path: Path, unreadable: None
) -> None:
    d = deposit(tmp_path, {"fail.R": "stop('boom')\n", "empty.R": "", "ok.R": "x <- median(1:5)\n"})
    mo = static(d)
    reason = dict(zip(mo.table["file_name"], mo.table["not_runnable_reason"], strict=True))
    assert reason["fail.R"] == "not_read"
    assert reason["empty.R"] == "empty"
    assert pd.isna(reason["ok.R"])
    assert mo.traffic_light == "yellow"
    assert "1 file could not be read" in report_text(mo)


def test_a_python_script_that_could_not_be_read_is_not_run(
    tmp_path: Path, unreadable: None
) -> None:
    mo = static(deposit(tmp_path, {"fail.py": "print(1)\n"}))
    t = mo.table.set_index("file_name")
    assert t.loc["fail.py", "not_runnable_reason"] == "not_read"
    assert "stringi package required" in t.loc["fail.py", "error_message"]
    assert mo.traffic_light != "green"


def test_the_table_gives_the_error_of_a_script_that_failed_short(
    tmp_path: Path, runner: Runner
) -> None:
    runner.respond = lambda n, f, call: errored("deliberate failure for testing " + "x" * 400)
    mo = run(deposit(tmp_path, {"a.R": "stop('x')\n"}))
    msg = mo.table["error_message"].iloc[0]
    assert "deliberate failure for testing" in msg
    assert len(msg) <= 300


def test_with_too_little_time_left_nothing_is_copied_or_run(tmp_path: Path, runner: Runner) -> None:
    left = dt.datetime.now() + dt.timedelta(seconds=300)  # under repro_min_time (900 s)
    # the real runner starts nothing after the deadline
    runner.respond = lambda n, f, call: {"outcome": "skipped_time_limit"}
    mo = run(deposit(tmp_path, {"a.R": "x <- 1\n"}), total_timeout=left)
    # the runner is given a deadline that has passed, so it starts nothing
    assert runner.calls[0].kwargs["deadline"] <= time.time()
    assert "too little to copy the repository, install its packages and run its scripts" in (
        report_text(mo)
    )


def test_the_minimum_time_is_an_option(tmp_path: Path, runner: Runner) -> None:
    from metacheck.utils import options

    left = dt.datetime.now() + dt.timedelta(seconds=300)
    old = options({"metacheck.repro_min_time": 60})
    try:
        mo = run(deposit(tmp_path, {"a.R": "x <- 1\n"}), total_timeout=left)
    finally:
        options(old)
    assert outcomes(mo) == {"a.R": "ran_ok"}
