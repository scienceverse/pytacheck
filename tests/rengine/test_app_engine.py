"""The demo app's "Run checks with: R" (with the stand-in Rscript)."""

from __future__ import annotations

from pathlib import Path

import pytest

import metacheck as pc
from metacheck.app import run as app_run
from metacheck.app.progress import paper_steps


def test_begin_with_r(fake_r: Path, tmp_path: Path) -> None:
    said: list[str] = []
    run = app_run.begin(
        pc.demofile("json"),
        workdir=tmp_path,
        checks=["marginal", "stat_check", "author_check"],
        progress=lambda _f, text: said.append(text),
        engine="r",
    )
    analysis = run.analysis
    # author_check is a Python-only check: R runs the checks it has
    assert [r.module for r in analysis.rows] == ["marginal", "stat_check"]
    assert analysis.rows[0].result == "Yellow: marginal ran on to_err_is_human.json"
    assert analysis.engine == "R metacheck 9.9.9"
    assert "9.9.9 (R metacheck on R 4.5.3" in analysis.html
    assert "Running the checks with R metacheck" in said
    assert run.r_source is None  # no data check, so no copy of the paper for it


def test_only_python_checks_is_nothing_to_run(fake_r: Path, tmp_path: Path) -> None:
    with pytest.raises(app_run.UserError, match="No check is ticked"):
        app_run.begin(pc.demofile("json"), workdir=tmp_path, checks=["author_check"], engine="r")


def test_r_failure_is_said_in_words(
    fake_r: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck import rengine

    rengine.info()  # R is there ...
    monkeypatch.setenv("FAKE_R_MODE", "error")  # ... but cannot read the paper
    with pytest.raises(app_run.UserError, match="R metacheck could not run the checks"):
        app_run.begin(pc.demofile("json"), workdir=tmp_path, checks=["marginal"], engine="r")


def test_the_data_check_with_r(
    fake_r: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_run, "_online", lambda: True)
    run = app_run.begin(
        pc.demofile("json"), workdir=tmp_path, checks=["marginal"], data=True, engine="r"
    )
    copy = run.r_source
    assert copy is not None and copy.is_file() and copy.parent != tmp_path
    job = run.data_job()
    job.start()
    assert job.wait(30)
    assert job.error is None, job.error
    assert job.output.summary_text == "data_check ran on to_err_is_human.json"
    assert not copy.parent.exists()  # the job removes R's copy of the paper
    final = run.finish(job)
    assert [r.module for r in final.rows] == ["marginal", "data_check"]


def test_steps_name_r() -> None:
    steps = paper_steps("paper.json", "grobid", False, data_in_run=False, engine="r")
    assert [s.title for s in steps.steps] == [
        "Read the paper",
        "Run the checks with R metacheck",
        "Write the report",
    ]
    assert "does not say how far it is" in steps.steps[1].note


@pytest.mark.parametrize(
    ("setting", "hosted", "offered"),
    [
        ("", False, True),  # on this computer: wherever R runs metacheck
        ("off", False, False),
        ("", True, False),  # a shared server offers it only when told to
        ("on", True, True),
    ],
)
def test_where_the_page_offers_r(
    fake_r: Path, monkeypatch: pytest.MonkeyPatch, setting: str, hosted: bool, offered: bool
) -> None:
    pytest.importorskip("gradio")
    from metacheck.app import ui

    monkeypatch.setenv("METACHECK_APP_R_ENGINE", setting)
    assert ui.r_engine_offered(hosted) is offered


def test_no_r_is_not_offered(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("gradio")
    from metacheck.app import ui

    monkeypatch.setenv("FAKE_R_MODE", "crash")
    monkeypatch.setenv("METACHECK_APP_R_ENGINE", "on")
    assert not ui.r_engine_offered(True)


def test_choosing_r_on_the_page(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("gradio")
    from metacheck.app import ui

    monkeypatch.delenv("METACHECK_APP_R_ENGINE", raising=False)
    blocks = ui.build_app()
    fns = {fn.name: fn.fn for fn in blocks.fns.values()}
    picked, switch = fns["on_engine"]("r", ["marginal", "author_check", "stat_check"])
    # only R metacheck's checks are offered, and the switch stays on Paper
    assert [name for _, name in picked["choices"]] == ["marginal", "stat_check"]
    assert picked["value"] == ["marginal", "stat_check"]
    assert switch["value"] == ui.PAPER_MODE and switch["interactive"] is False
    picked, switch = fns["on_engine"]("python", ["marginal"])
    assert "author_check" in [name for _, name in picked["choices"]]
    assert switch["interactive"] is True
    # the online box and a preset tick only what R has
    assert fns["on_online"](True, ["marginal"], "r") == ["marginal"]


def test_r_ends_with_a_hosted_run_ended_at_its_limit(
    fake_r: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R runs in a session of its own, so ending the run's process must end R as well."""
    import importlib
    import os
    import time

    from metacheck.app import hosted

    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    monkeypatch.setenv("FAKE_R_MODE", "hang")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parent))  # for the spawned process
    job = importlib.import_module("_r_jobs").run_r
    pidfile = fake_r.parent / "pid"
    runner = hosted.JobRunner(timeout=8)
    with pytest.raises(TimeoutError):
        runner.run(job, str(pc.demofile("json")), on_timeout=TimeoutError)
    assert pidfile.exists()  # R was running
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("R outlived the run's process")
