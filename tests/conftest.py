"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # makes the `parity` harness importable

# Hermetic module system: no user/project config files and no installed
# packs from the real data dir, unless the caller set these explicitly.
_CALLER_DATA_DIR = os.environ.get("PYTACHECK_DATA_DIR")
os.environ.setdefault("PYTACHECK_CONFIG", "none")

UPSTREAM = ROOT / "upstream" / "metacheck"
FIXTURES = UPSTREAM / "tests" / "testthat" / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    # the parity cases' tiers (parity/corpus.toml): `pytest -m "parity and tier1"`
    config.addinivalue_line(
        "markers", "tier1: parity cases on the realistic corpus (parity/corpus.toml)"
    )
    config.addinivalue_line("markers", "tier2: parity cases on synthetic edge-case inputs")


@pytest.fixture(scope="session")
def upstream_dir() -> Path:
    """The pinned metacheck checkout (git submodule)."""
    if not (UPSTREAM / "DESCRIPTION").exists():
        pytest.skip("upstream/metacheck submodule not checked out")
    return UPSTREAM


@pytest.fixture(scope="session")
def fixtures_dir(upstream_dir: Path) -> Path:
    """metacheck's testthat fixtures directory."""
    return upstream_dir / "tests" / "testthat" / "fixtures"


@pytest.fixture
def demo():
    import pytacheck as pc

    return pc.demopaper()


@pytest.fixture
def psychsci(fixtures_dir: Path):
    import pytacheck as pc

    return pc.read(fixtures_dir / "psychsci")


@pytest.fixture(autouse=True)
def _isolated_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep logs and caches out of the user's home directory."""
    base = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("PYTACHECK_LOG", str(base / "pytacheck.log.jsonl"))
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(base / "cache"))
    if not _CALLER_DATA_DIR:
        monkeypatch.setenv("PYTACHECK_DATA_DIR", str(base / "data"))


@pytest.fixture(autouse=True)
def _no_http_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Courtesy delays and retry backoff are pointless against mocks."""
    monkeypatch.setenv("PYTACHECK_NO_SLEEP", "1")


# -- hygiene: tests write to tmp_path, never into the checkout's root ---------------
#
# A function that saves files takes metacheck's default ``save_path = "."``
# (``paper_write()``, ``grobid_to_bibr()``, ``convert()``, the CLI's ``read``),
# and pytest runs in the checkout, so a test that forgets the path leaves a
# paper JSON in the repository root. Any new entry there fails the run; a
# warning names the test it appeared in (with pytest-xdist, also any test that
# ran at the same time).

# what pytest, its plugins and the linters themselves keep in the root: CI runs
# `pytest --cov --cov-report=xml`, and pytest-cov writes coverage.xml before
# pytest_sessionfinish (and .coverage.<suffix> files in parallel mode)
_ROOT_TOOL_ENTRIES = frozenset(
    {
        ".pytest_cache",
        ".hypothesis",
        ".ruff_cache",
        ".mypy_cache",
        ".benchmarks",
        "coverage.xml",
        "coverage.json",
        "coverage.lcov",
        "htmlcov",
    }
)


def _root_entries() -> frozenset[str]:
    return frozenset(
        p.name
        for p in ROOT.iterdir()
        if p.name not in _ROOT_TOOL_ENTRIES and not p.name.startswith(".coverage")
    )


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


_ROOT_BEFORE = pytest.StashKey[frozenset[str]]()
_ROOT_NEW = pytest.StashKey[list[str]]()


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_ROOT_BEFORE] = _root_entries()


@pytest.fixture(autouse=True)
def _no_files_in_the_root() -> Iterator[None]:
    before = _root_entries()
    yield
    new = sorted(_root_entries() - before)
    if new:
        warnings.warn(
            f"{', '.join(new)} appeared in the repository root during this test: "
            "write to tmp_path instead",
            stacklevel=1,
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _is_xdist_worker(session.config) or _ROOT_BEFORE not in session.config.stash:
        return
    new = sorted(_root_entries() - session.config.stash[_ROOT_BEFORE])
    if new:
        session.config.stash[_ROOT_NEW] = new
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    new = config.stash.get(_ROOT_NEW, None)
    if new:
        terminalreporter.write_sep(
            "=",
            f"tests left {', '.join(new)} in the repository root {ROOT} (see the warnings "
            "for the test; write to tmp_path instead)",
            red=True,
        )
