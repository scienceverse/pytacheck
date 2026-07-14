"""Benchmark the validated default modules on a deterministic full-size profile.

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
DEFAULT_SYNTHETIC_TEXT_ROWS = 720
SYNTHETIC_PROFILE_NAME = "full_size_synthetic_v1"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("iterations must be at least 1")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        help=(
            "optional JSON fixture; omitted by default so performance is measured on the "
            "deterministic full-size synthetic profile"
        ),
    )
    parser.add_argument("--iterations", type=_positive_integer, default=20)
    parser.add_argument(
        "--synthetic-text-rows",
        type=_positive_integer,
        default=DEFAULT_SYNTHETIC_TEXT_ROWS,
        help="row count for the generated profile when --fixture is omitted",
    )
    return parser


def _synthetic_row_text(index: int) -> str:
    findings = {
        0: (
            "An a priori power analysis determined that 240 participants were required "
            "to achieve statistical power of .80 for the planned comparison."
        ),
        4: "One exploratory contrast was described as marginally significant.",
        8: "The confirmatory contrast yielded t(198) = 2.10, p = .037, d = 0.30.",
        12: "The omnibus analysis yielded F(2, 197) = 4.21, p = .016, eta^2 = .04.",
        16: "A secondary comparison was nonsignificant, p = .140.",
        20: "The planned directional result was statistically significant, p < .05.",
    }
    if index in findings:
        return findings[index]
    return (
        f"Synthetic benchmark narrative row {index + 1:04d} exercises deterministic full-document "
        "traversal with ordinary methodological prose, stable identifiers, repeated lexical "
        "content, and enough material to represent a substantial extracted research article."
    )


def _build_synthetic_profile(text_rows: int) -> bytes:
    if text_rows < 1:
        raise ValueError("synthetic_text_rows must be at least 1")

    sections = [
        {"section_id": 1, "header": "Methods", "section_type": "method"},
        {"section_id": 2, "header": "Results", "section_type": "results"},
        {"section_id": 3, "header": "Discussion", "section_type": "discussion"},
    ]
    rows = [
        {
            "paragraph_id": index // 3 + 1,
            "section_id": 1 if index < text_rows // 3 else 2 if index < 2 * text_rows // 3 else 3,
            "text": _synthetic_row_text(index),
            "text_id": index + 1,
        }
        for index in range(text_rows)
    ]
    payload = {
        "paper_id": "pytacheck-full-size-synthetic-v1",
        "info": {"schema_version": "10.6"},
        "section": sections,
        "text": rows,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_payload(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("benchmark input must contain one JSON object")

    # The frozen upstream demo uses the pre-10.2 singleton-list info wrapper.
    # Decode this wrapper on every end-to-end iteration when a fixture is supplied.
    info = payload.get("info")
    if isinstance(info, list) and len(info) == 1 and isinstance(info[0], dict):
        payload["info"] = info[0]
    return payload


def _profile_metadata(raw: bytes, payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("text")
    text_rows = text if isinstance(text, list) else []
    text_characters = sum(
        len(row_text)
        for row in text_rows
        if isinstance(row, dict) and isinstance((row_text := row.get("text")), str)
    )
    return {
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "serialized_bytes": len(raw),
        "text_rows": len(text_rows),
        "text_characters": text_characters,
    }


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
    fixture: Path | None,
    iterations: int,
    *,
    synthetic_text_rows: int = DEFAULT_SYNTHETIC_TEXT_ROWS,
) -> dict[str, Any]:
    """Run cold, warm, and raw-JSON-to-check measurements."""

    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if fixture is None:
        raw = _build_synthetic_profile(synthetic_text_rows)
        profile_kind = "generated_full_size_synthetic"
        profile_name = SYNTHETIC_PROFILE_NAME
    else:
        raw = fixture.read_bytes()
        profile_kind = "provided_fixture"
        profile_name = fixture.name

    initial_payload = _decode_payload(raw)
    metadata = _profile_metadata(raw, initial_payload)
    paper = BibrPaper.model_validate(initial_payload)
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

    end_to_end_latencies_ms: list[float] = []
    for _ in range(iterations):
        iteration_started = perf_counter()
        parsed_payload = _decode_payload(raw)
        parsed_paper = BibrPaper.model_validate(parsed_payload)
        response = engine.check(parsed_paper)
        end_to_end_latencies_ms.append(_milliseconds(iteration_started))
        _assert_valid_response(response)

    return {
        **metadata,
        "profile_kind": profile_kind,
        "profile_name": profile_name,
        "iterations": iterations,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cold_latency_ms": cold_latency_ms,
        "warm_p50_ms": _percentile(warm_latencies_ms, 0.50),
        "warm_p95_ms": _percentile(warm_latencies_ms, 0.95),
        "papers_per_second": iterations / warm_elapsed_seconds,
        "end_to_end_p50_ms": _percentile(end_to_end_latencies_ms, 0.50),
        "end_to_end_p95_ms": _percentile(end_to_end_latencies_ms, 0.95),
        "per_module_ms": {
            module_name: _timing_summary(module_latencies_ms[module_name])
            for module_name in DEFAULT_MODULES
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    fixture = args.fixture.resolve() if args.fixture is not None else None
    payload = run_benchmark(
        fixture,
        args.iterations,
        synthetic_text_rows=args.synthetic_text_rows,
    )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
