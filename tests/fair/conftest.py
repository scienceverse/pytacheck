"""Fixtures for the FAIR_repo tests: no LLM, messages on, a fixture folder, and a runner."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fair.fair_helpers import fair_fixture


@pytest.fixture(autouse=True)
def _llm_off() -> Iterator[None]:
    """``withr::local_options(metacheck.llm.use = FALSE)``."""
    from metacheck.llm.core import llm_use

    old = llm_use()
    llm_use(False)
    yield
    llm_use(old)


@pytest.fixture(autouse=True)
def _messages_on() -> Iterator[None]:
    """FAIR_repo's messages are asserted on (stderr), so ``verbose()`` must be on."""
    from metacheck.config import verbose

    old = verbose()
    verbose(True)
    yield
    verbose(old)


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """``fair_fixture()``: the small research folder, written to ``<tmp_path>/proj``."""
    return fair_fixture(tmp_path / "proj")


@pytest.fixture
def fair_run(tmp_path: Path) -> Callable[..., dict[str, Any]]:
    """``fair_run(d, ...)``: FAIR_repo() into a new folder next to the test's other folders."""
    from metacheck.fair.repo import FAIR_repo

    counter = itertools.count(1)

    def run(d: Path, **kwargs: Any) -> dict[str, Any]:
        out = tmp_path / "outputs-of-runs" / f"run{next(counter)}" / "out"
        return FAIR_repo(str(d), output_dir=str(out), **kwargs)  # type: ignore[no-any-return]

    return run
