from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import tomllib
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


def test_benchmark_cli_defaults_to_a_generated_full_size_synthetic_profile() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.benchmark_default_modules",
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
        "input_sha256",
        "profile_kind",
        "profile_name",
        "serialized_bytes",
        "text_rows",
        "text_characters",
        "iterations",
        "python_version",
        "platform",
        "cold_latency_ms",
        "warm_p50_ms",
        "warm_p95_ms",
        "papers_per_second",
        "end_to_end_p50_ms",
        "end_to_end_p95_ms",
        "per_module_ms",
    } <= payload.keys()
    assert payload["iterations"] == 2
    assert payload["profile_kind"] == "generated_full_size_synthetic"
    assert payload["profile_name"] == "full_size_synthetic_v1"
    assert payload["serialized_bytes"] >= 100_000
    assert payload["text_rows"] >= 500
    assert payload["text_characters"] >= 80_000
    assert list(payload["per_module_ms"]) == list(DEFAULT_MODULES)
    for module_timing in payload["per_module_ms"].values():
        assert {"p50_ms", "p95_ms"} <= module_timing.keys()


def test_end_to_end_benchmark_decodes_json_for_every_iteration(monkeypatch: Any) -> None:
    from benchmarks import benchmark_default_modules as benchmark

    original_loads = benchmark.json.loads
    decode_calls = 0

    def tracked_loads(value: str | bytes | bytearray, *args: Any, **kwargs: Any) -> Any:
        nonlocal decode_calls
        decode_calls += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(benchmark.json, "loads", tracked_loads)
    report = benchmark.run_benchmark(None, 2, synthetic_text_rows=24)

    assert decode_calls >= 3
    assert report["profile_kind"] == "generated_full_size_synthetic"
    assert report["text_rows"] == 24


def test_python_distribution_declares_runtime_and_linux_arm64_contracts() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    environments = pyproject["tool"]["uv"]["environments"]

    assert "starlette>=1.3.1,<1.4.0" in dependencies
    assert "sys_platform == 'linux' and platform_machine == 'aarch64'" in environments
    assert "NOTICE.md" in pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]

    lockfile = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "platform_machine == 'aarch64' and sys_platform == 'linux'" in lockfile


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
    assert "NOTICE.md" in dockerfile
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
        "docker buildx build",
    ):
        assert command in workflow

    assert "docker/setup-qemu-action@v3" in workflow
    assert "linux/amd64" in workflow
    assert "linux/arm64" in workflow
    assert "--push" not in workflow


def test_notice_and_deployment_guidance_are_explicit() -> None:
    notice = (PROJECT_ROOT / "NOTICE.md").read_text(encoding="utf-8")
    for provenance in (
        "https://github.com/scienceverse/metacheck",
        "0291d575628b0c8cec56eb64c944ad269c91edc4",
        "inst/modules/power.R",
        "inst/modules/marginal.R",
        "inst/modules/stat_check.R",
        "inst/modules/stat_effect_size.R",
        "inst/modules/stat_p_exact.R",
        "inst/modules/stat_p_nonsig.R",
        "R/text-extractors.R",
        "2026-07-14",
    ):
        assert provenance in notice

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()
    architecture = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8").lower()
    assert "one uvicorn worker per container" in readme
    assert "corresponding source" in readme and "public" in readme
    for extension in (
        "bounded extraction and output budgets",
        "malformed, out-of-domain, and non-finite",
        "exact-zero lexical semantics",
        "match-local",
        "partial omega",
    ):
        assert extension in architecture
