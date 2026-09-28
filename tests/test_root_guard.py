"""tests/conftest.py fails a pytest run that leaves a new entry in the repository
root (a paper saved with metacheck's default ``save_path = "."``), but not for
what pytest's own plugins write there (CI runs ``pytest --cov --cov-report=xml``)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _project(tmp_path: Path, test_body: str) -> Path:
    """A throwaway checkout: this conftest.py and one test module."""
    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "conftest.py", proj / "tests" / "conftest.py")
    (proj / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    return proj


def _pytest(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args, "tests"],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_file_left_in_the_root_fails_the_run(tmp_path) -> None:
    proj = _project(
        tmp_path,
        "def test_writes():\n    open('stray.json', 'w').write('{}')\n\n"
        "def test_clean():\n    pass\n",
    )
    run = _pytest(proj)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "tests left stray.json in the repository root" in run.stdout
    assert "stray.json appeared in the repository root during this test" in run.stdout
    assert "2 passed" in run.stdout


def test_coverage_reports_are_not_strays(tmp_path) -> None:
    pytest.importorskip("pytest_cov")
    proj = _project(tmp_path, "def test_clean():\n    pass\n")
    run = _pytest(proj, "--cov", "--cov-report=xml")
    assert (proj / "coverage.xml").exists(), run.stdout + run.stderr
    assert run.returncode == 0, run.stdout + run.stderr
