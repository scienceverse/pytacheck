"""Fixtures of the web interface tests: an app whose jobs are small fakes (helpers.py).

A job runs in a process of its own (``JobRunner``), started as the site starts it, with
``spawn``. The tests that need their fakes and settings in the job's process fork it
instead (the ``forked`` fixture), on Linux only: macOS's ``fork`` is not safe once threads
run, and Windows has none. ``METACHECK_TEST_FORK=0`` skips those tests on Linux too.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from metacheck.app import hosted
from metacheck.web import app as web_app
from metacheck.web.config import SiteConfig
from metacheck.web.jobs import JobManager
from tests.web.helpers import Clock, fake_job

#: whether the tests that need it may fork a job's process (see the top of this file)
FORK = os.environ.get("METACHECK_TEST_FORK", "1" if sys.platform == "linux" else "0") == "1"


@pytest.fixture(autouse=True)
def _start_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted, "START_METHOD", "spawn")


@pytest.fixture
def forked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job's process is forked, so this test's fakes and settings reach it."""
    if not FORK:
        pytest.skip("the job's process sees this test's fakes only when it is forked (Linux)")
    monkeypatch.setattr(hosted, "START_METHOD", "fork")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def manager_for(tmp_path: Path, clock: Clock) -> Iterator[Callable[..., JobManager]]:
    """Make a :class:`JobManager` that runs the fake job; every one made is shut down."""
    made: list[JobManager] = []

    def make(**settings: Any) -> JobManager:
        settings.setdefault("jobs_dir", tmp_path / f"jobs{len(made)}")
        settings.setdefault("min_free_bytes", 0)
        manager = JobManager(SiteConfig(**settings), job=fake_job, clock=clock)
        made.append(manager)
        return manager

    yield make
    for manager in made:
        (manager.root / "gate").touch()
        end = time.monotonic() + 20
        while manager.counts() != (0, 0) and time.monotonic() < end:
            time.sleep(0.05)
        manager.close()


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: Clock
) -> Iterator[Callable[..., TestClient]]:
    """Make a test client of the site whose jobs are the fake job."""
    clients: list[TestClient] = []

    def make(fake: bool = True, **settings: Any) -> TestClient:
        settings.setdefault("jobs_dir", tmp_path / f"site{len(clients)}")
        settings.setdefault("min_free_bytes", 0)
        if fake:
            monkeypatch.setattr(
                web_app,
                "JobManager",
                lambda config: JobManager(config, job=fake_job, clock=clock),
            )
        client = TestClient(web_app.create_app(SiteConfig(**settings)))
        clients.append(client)
        return client

    yield make
    for client in clients:
        manager: JobManager = client.app.state.manager  # type: ignore[attr-defined]
        (manager.root / "gate").touch()
        end = time.monotonic() + 20
        while manager.counts() != (0, 0) and time.monotonic() < end:
            time.sleep(0.05)
        client.close()
        manager.close()


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
