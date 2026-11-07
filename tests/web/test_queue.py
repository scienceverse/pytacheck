"""The job queue: places that run, places that wait, time limits and clean-up."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from metacheck.web import jobs as jobs_module
from metacheck.web.jobs import Job, JobManager, QueueFull
from metacheck.web.worker import STOPPED
from tests.web.helpers import Clock, send, wait_for


def add(manager: JobManager, name: str, kind: str = "report") -> Job:
    job_id, folder = manager.new_folder()
    (folder / "input.pdf").write_bytes(b"%PDF-1.4")
    job = Job(id=job_id, kind=kind, spec={"kind": kind, "name": name}, folder=folder)
    manager.submit(job)
    return job


def until(condition: Callable[[], bool], seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "the condition did not come true"
        time.sleep(0.02)


def release(manager: JobManager) -> None:
    (manager.root / "gate").touch()


def test_two_run_and_the_rest_wait_in_order(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for(max_waiting=3)
    held = [add(manager, f"hold{i}") for i in range(2)]
    until(lambda: manager.counts() == (2, 0))  # the places are taken before the queue fills
    held += [add(manager, f"hold{i}") for i in range(2, 5)]
    until(lambda: manager.counts() == (2, 3))
    assert [j.status for j in held[:2]] == ["running", "running"]
    assert [manager.position(j) for j in held] == [None, None, 1, 2, 3]
    assert [j.status for j in held[2:]] == ["queued"] * 3
    release(manager)
    until(lambda: all(j.status == "done" for j in held))
    assert manager.counts() == (0, 0)
    assert all(manager.position(j) is None for j in held)


def test_the_places_free_up_in_order(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for()
    first, second, third = (add(manager, f"hold{i}") for i in range(3))
    until(lambda: manager.counts() == (2, 1))
    assert third.status == "queued"
    release(manager)
    until(lambda: third.status == "done")
    assert first.started is not None and second.started is not None and third.started is not None
    assert third.started >= min(first.started, second.started)


def test_a_full_queue_refuses_the_next_job(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for(max_waiting=2)
    for i in range(2):
        add(manager, f"hold{i}")
    until(lambda: manager.counts() == (2, 0))  # the places are taken before the queue fills
    for i in range(2, 4):
        add(manager, f"hold{i}")
    until(lambda: manager.counts() == (2, 2))
    with pytest.raises(QueueFull, match="queue is full"):
        manager.check_capacity()
    with pytest.raises(QueueFull):
        add(manager, "one too many")
    # the refused job is not kept
    assert manager.counts() == (2, 2)


def test_a_short_disk_refuses_the_next_job(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for(min_free_bytes=1 << 62)
    with pytest.raises(QueueFull, match="disk"):
        manager.check_capacity()


def test_the_default_limits_are_two_running_and_twenty_waiting(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client()
    manager: JobManager = client.app.state.manager  # type: ignore[attr-defined]
    assert (manager.config.max_running, manager.config.max_waiting) == (2, 20)
    # two jobs take the places, twenty wait, the next one is turned away
    ids = [send(client, "hold.pdf").json()["id"] for _ in range(2)]
    until(lambda: manager.counts() == (2, 0))
    ids += [send(client, "hold.pdf").json()["id"] for _ in range(20)]
    full = send(client, "hold.pdf")
    assert full.status_code == 503
    assert "queue is full" in full.json()["error"]
    # nothing of the refused upload is kept
    assert len([p for p in manager.root.iterdir() if p.is_dir()]) == 22
    # waiting jobs say their place
    body = client.get(f"/api/jobs/{ids[2]}").json()
    assert (body["status"], body["position"]) == ("queued", 1)
    assert client.get(f"/api/jobs/{ids[21]}").json()["position"] == 20
    assert "progress" not in body
    release(manager)
    until(lambda: manager.counts() == (0, 0), 60)
    assert wait_for(client, ids[21], "done")["status"] == "done"


def test_a_queue_with_no_room_refuses_a_one_call_route(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(max_waiting=0)
    refused = send(client, path="/api/report")
    assert refused.status_code == 503


def test_a_finished_job_is_kept_then_deleted(
    manager_for: Callable[..., JobManager], clock: Clock
) -> None:
    manager = manager_for(keep=3600.0)
    release(manager)
    job = add(manager, "quick")
    until(lambda: job.status == "done")
    assert (job.folder / "result.out").exists()
    # the upload is deleted at the end of the job, the result is kept
    assert not list(job.folder.glob("input.*"))
    clock.now += 3599
    assert manager.sweep() == 0
    assert manager.get(job.id) is job
    clock.now += 2
    assert manager.sweep() == 1
    assert manager.get(job.id) is None
    assert not job.folder.exists()
    assert manager.sweep() == 0


def test_a_failed_job_is_deleted_after_the_same_hour(
    manager_for: Callable[..., JobManager], clock: Clock
) -> None:
    manager = manager_for()
    job = add(manager, "fail")
    until(lambda: job.status == "failed")
    assert job.error == "The paper could not be read."
    assert not list(job.folder.glob("input.*"))
    clock.now += 3601
    assert manager.sweep() == 1


def test_a_waiting_or_running_job_is_never_swept(
    manager_for: Callable[..., JobManager], clock: Clock
) -> None:
    manager = manager_for()
    held = [add(manager, f"hold{i}") for i in range(3)]
    until(lambda: manager.counts() == (2, 1))
    clock.now += 10 * 3600
    assert manager.sweep() == 0
    release(manager)
    until(lambda: all(j.status == "done" for j in held))
    clock.now += 3601
    assert manager.sweep() == 3


def test_the_sweeper_deletes_by_itself(
    manager_for: Callable[..., JobManager], clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_module, "SWEEP_EVERY", 0.05)
    manager = manager_for()
    job = add(manager, "quick")
    until(lambda: job.status == "done")
    clock.now += 3601
    until(lambda: manager.get(job.id) is None)
    until(lambda: not job.folder.exists())


def test_a_job_that_takes_too_long_is_stopped(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for(timeout=1.0)
    job = add(manager, "hang")
    until(lambda: job.status == "failed")
    assert job.error is not None and "took longer" in job.error
    # its place is free again
    release(manager)
    other = add(manager, "quick")
    until(lambda: other.status == "done")


def test_a_job_that_dies_fails_with_the_standard_message(
    manager_for: Callable[..., JobManager],
) -> None:
    def die(*args: object) -> None:
        import os

        os._exit(3)

    manager = manager_for()
    manager._job = die
    job = add(manager, "x")
    until(lambda: job.status == "failed")
    assert job.error == STOPPED


def test_ids_are_random_and_folders_private(manager_for: Callable[..., JobManager]) -> None:
    manager = manager_for()
    a, folder_a = manager.new_folder()
    b, _ = manager.new_folder()
    assert a != b and len(a) == 36
    assert folder_a.stat().st_mode & 0o777 == 0o700
    assert manager.root.stat().st_mode & 0o777 == 0o700


def test_a_server_that_made_its_folder_removes_it(tmp_path: Path) -> None:
    from metacheck.web.config import SiteConfig

    manager = JobManager(SiteConfig())
    root = manager.root
    assert root.is_dir()
    manager.close()
    assert not root.exists()
    kept = JobManager(SiteConfig(jobs_dir=tmp_path / "mine"))
    kept.close()
    assert (tmp_path / "mine").is_dir()
