from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

from pytacheck.engine import DEFAULT_MODULES, CheckEngine
from pytacheck.models import BibrPaper

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "parity" / "fixtures" / "to_err_is_human.json"


def _fixture_paper() -> BibrPaper:
    payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    info = payload["info"]
    assert isinstance(info, list) and len(info) == 1
    payload["info"] = info[0]
    return BibrPaper.model_validate(payload)


def test_warm_default_check_stays_under_generous_ci_smoke_budget() -> None:
    paper = _fixture_paper()
    engine = CheckEngine()
    engine.check(paper)

    started = time.perf_counter()
    response = engine.check(paper)
    elapsed = time.perf_counter() - started

    assert tuple(response.modules_run) == DEFAULT_MODULES
    assert all(result.traffic_light != "fail" for result in response.results.values())
    assert elapsed < 2.0


def test_high_df_effect_size_check_has_bounded_python_memory() -> None:
    paper = BibrPaper.model_validate(
        {
            "paper_id": "high-df",
            "text": [
                {
                    "text": "The large study found t(1000000) = 2.00, d = 0.004.",
                    "text_id": 1,
                    "paragraph_id": 1,
                    "section_id": 1,
                }
            ],
            "section": [{"section_id": 1, "header": "Results", "section_type": "results"}],
        }
    )
    engine = CheckEngine()

    tracemalloc.start()
    try:
        response = engine.check(paper, modules=["stat_effect_size"])
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response.results["stat_effect_size"].traffic_light != "fail"
    assert peak_bytes < 16 * 1024 * 1024


def test_benchmark_cli_emits_machine_readable_contract() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.benchmark_default_modules",
            "--fixture",
            str(FIXTURE_PATH),
            "--iterations",
            "2",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert {
        "fixture_sha256",
        "iterations",
        "python_version",
        "platform",
        "cold_latency_ms",
        "warm_p50_ms",
        "warm_p95_ms",
        "papers_per_second",
        "validation_and_check_p50_ms",
        "validation_and_check_p95_ms",
        "per_module_ms",
    } <= payload.keys()
    assert payload["iterations"] == 2
    assert payload["fixture_sha256"] == (
        "c99be5ea276910a6e7fbd0301ee0b89e0ccc7c7af5d27727e018d573dc646da4"
    )
    assert list(payload["per_module_ms"]) == list(DEFAULT_MODULES)
    for module_timing in payload["per_module_ms"].values():
        assert {"p50_ms", "p95_ms"} <= module_timing.keys()


def test_container_contract_is_non_root_and_excludes_developer_inputs() -> None:
    dockerfile_path = PROJECT_ROOT / "Dockerfile"
    dockerignore_path = PROJECT_ROOT / ".dockerignore"
    assert dockerfile_path.is_file()
    assert dockerignore_path.is_file()

    dockerfile = dockerfile_path.read_text(encoding="utf-8")
    assert len(re.findall(r"(?im)^FROM\s+", dockerfile)) >= 2
    assert re.search(r"(?im)^USER\s+(?!root\b)\S+", dockerfile)
    assert re.search(r"(?im)^HEALTHCHECK\b", dockerfile)
    assert "pytacheck serve" in dockerfile
    assert "--host 0.0.0.0" in dockerfile
    assert "--port 2005" in dockerfile
    assert not re.search(r"(?i)apt(?:-get)?\s+install[^\n]*(?:r-base|rscript|r-cran)", dockerfile)

    ignored = dockerignore_path.read_text(encoding="utf-8")
    for pattern in (".git", ".venv", "tests", "*.pdf", "*.docx", "*.R", ".env"):
        assert pattern in ignored


def test_ci_runs_quality_build_benchmark_and_container_gates() -> None:
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")

    for command in (
        "uv lock --check",
        "ruff check",
        "ruff format --check",
        "mypy pytacheck",
        "pytest",
        "python -m build",
        "benchmarks.benchmark_default_modules",
        "docker build",
    ):
        assert command in workflow
