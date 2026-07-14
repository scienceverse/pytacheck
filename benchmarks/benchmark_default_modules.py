"""Benchmark the validated default modules on a non-empty paper fixture.

The command writes exactly one JSON document to stdout so CI and operators can
archive or compare measurements without scraping human-oriented output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from pytacheck.engine import DEFAULT_MODULES, CheckEngine, CheckResponse
from pytacheck.models import BibrPaper

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = PROJECT_ROOT / "tests" / "parity" / "fixtures" / "to_err_is_human.json"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("iterations must be at least 1")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--iterations", type=_positive_integer, default=20)
    return parser


def _read_fixture(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("benchmark fixture must contain one JSON object")

    # The frozen upstream demo uses the pre-10.2 singleton-list info wrapper.
    # This normalization belongs to the benchmark fixture adapter, not the timed path.
    info = payload.get("info")
    if isinstance(info, list) and len(info) == 1 and isinstance(info[0], dict):
        payload["info"] = info[0]
    return payload, hashlib.sha256(raw).hexdigest()


def _milliseconds(started: float) -> float:
    return (perf_counter() - started) * 1_000


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _assert_valid_response(response: CheckResponse) -> None:
    if tuple(response.modules_run) != DEFAULT_MODULES:
        raise RuntimeError("benchmark did not execute the validated defaults in order")
    failed = [name for name, result in response.results.items() if result.traffic_light == "fail"]
    if failed:
        raise RuntimeError(f"benchmark modules failed: {', '.join(failed)}")


def _timing_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
    }


def run_benchmark(
    fixture: Path,
    iterations: int,
) -> dict[str, Any]:
    """Run cold, warm, and validation-plus-check measurements."""

    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    fixture_payload, fixture_sha256 = _read_fixture(fixture)
    paper = BibrPaper.model_validate(fixture_payload)
    engine = CheckEngine()

    cold_started = perf_counter()
    cold_response = engine.check(paper)
    cold_latency_ms = _milliseconds(cold_started)
    _assert_valid_response(cold_response)

    warm_latencies_ms: list[float] = []
    module_latencies_ms: dict[str, list[float]] = {
        module_name: [] for module_name in DEFAULT_MODULES
    }
    warm_started = perf_counter()
    for _ in range(iterations):
        iteration_started = perf_counter()
        response = engine.check(paper)
        warm_latencies_ms.append(_milliseconds(iteration_started))
        _assert_valid_response(response)
        for module_name in DEFAULT_MODULES:
            module_latencies_ms[module_name].append(response.timings_ms[module_name])
    warm_elapsed_seconds = perf_counter() - warm_started

    validation_and_check_latencies_ms: list[float] = []
    for _ in range(iterations):
        iteration_started = perf_counter()
        parsed_paper = BibrPaper.model_validate(fixture_payload)
        response = engine.check(parsed_paper)
        validation_and_check_latencies_ms.append(_milliseconds(iteration_started))
        _assert_valid_response(response)

    return {
        "fixture_sha256": fixture_sha256,
        "iterations": iterations,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cold_latency_ms": cold_latency_ms,
        "warm_p50_ms": _percentile(warm_latencies_ms, 0.50),
        "warm_p95_ms": _percentile(warm_latencies_ms, 0.95),
        "papers_per_second": iterations / warm_elapsed_seconds,
        "validation_and_check_p50_ms": _percentile(validation_and_check_latencies_ms, 0.50),
        "validation_and_check_p95_ms": _percentile(validation_and_check_latencies_ms, 0.95),
        "per_module_ms": {
            module_name: _timing_summary(module_latencies_ms[module_name])
            for module_name in DEFAULT_MODULES
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    payload = run_benchmark(args.fixture.resolve(), args.iterations)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
