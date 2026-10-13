"""``module_progress()``: what the app's progress bar hears while a run goes through its modules."""

from __future__ import annotations

import threading
import warnings

import pytest

import metacheck as pc
from metacheck.core.scope import module_progress, note_module
from metacheck.report.report import report, report_module_run

MODULES = ["marginal", "stat_p_exact", "ref_summary"]


@pytest.fixture
def paper() -> pc.Paper:
    return pc.demopaper()


def test_each_module_is_announced_before_it_runs_and_the_end_after_the_last(
    paper: pc.Paper,
) -> None:
    heard: list[tuple[int, int, str]] = []
    with module_progress(lambda done, total, label: heard.append((done, total, label))):
        report_module_run(paper, MODULES)
    assert heard == [
        (0, 3, "marginal"),
        (1, 3, "stat_p_exact"),
        (2, 3, "ref_summary"),
        (3, 3, ""),
    ]


def test_nothing_is_heard_outside_the_block_or_from_other_threads(paper: pc.Paper) -> None:
    heard: list[tuple[int, int, str]] = []
    with module_progress(lambda *step: heard.append(step)):
        other = threading.Thread(target=report_module_run, args=(paper, ["marginal"]))
        other.start()
        other.join()
    report_module_run(paper, ["marginal"])
    assert heard == []


def test_a_module_that_fails_still_counts_and_the_run_goes_on(
    paper: pc.Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.report.report as rep

    real = rep.module_run

    def failing(op: object, module: object, **kwargs: object) -> object:
        if module == "stat_p_exact":
            raise RuntimeError("boom")
        return real(op, module, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rep, "module_run", failing)
    heard: list[tuple[int, int, str]] = []
    with module_progress(lambda *step: heard.append(step)), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = report_module_run(paper, MODULES)
    assert [h[2] for h in heard] == ["marginal", "stat_p_exact", "ref_summary", ""]
    assert out["stat_p_exact"].traffic_light == "fail"


def test_a_callback_that_fails_does_not_stop_the_run(paper: pc.Paper) -> None:
    def broken(done: int, total: int, label: str) -> None:
        raise RuntimeError("the page is gone")

    with module_progress(broken):
        out = report_module_run(paper, ["marginal"])
    assert "marginal" in out
    note_module(0, 1, "x")  # and with no callback it does nothing


def test_report_runs_its_modules_through_the_same_hook(paper: pc.Paper, tmp_path) -> None:
    heard: list[tuple[int, int, str]] = []
    with module_progress(lambda *step: heard.append(step)):
        report(paper, ["marginal"], tmp_path / "r.md", "md")
    assert heard == [(0, 1, "marginal"), (1, 1, "")]


def test_the_hook_is_taken_down_after_the_block(paper: pc.Paper) -> None:
    heard: list[tuple[int, int, str]] = []
    with module_progress(lambda *step: heard.append(step)):
        pass
    report_module_run(paper, ["marginal"])
    assert heard == []
