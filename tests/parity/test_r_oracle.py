from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from pytacheck.engine import DEFAULT_MODULES, CheckEngine
from pytacheck.models import BibrPaper

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PAPER_PATH = FIXTURE_DIR / "to_err_is_human.json"
EXPECTED_PATH = FIXTURE_DIR / "to_err_is_human.expected.json"
EXPECTED_FIXTURE_SHA256 = "c99be5ea276910a6e7fbd0301ee0b89e0ccc7c7af5d27727e018d573dc646da4"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _paper() -> BibrPaper:
    payload = _read_json(PAPER_PATH)
    # The upstream demo predates the current bibr object shape and stores its one info row
    # as a JSON array. Preserve the fixture byte-for-byte and normalize only this legacy wrapper.
    info = payload.get("info")
    assert isinstance(info, list) and len(info) == 1 and isinstance(info[0], dict)
    payload["info"] = info[0]
    return BibrPaper.model_validate(payload)


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return _read_json(EXPECTED_PATH)


@pytest.fixture(scope="module")
def response():  # type: ignore[no-untyped-def]
    return CheckEngine().check(_paper())


def test_upstream_fixture_and_oracle_provenance_are_frozen(expected: dict[str, Any]) -> None:
    fixture_hash = hashlib.sha256(PAPER_PATH.read_bytes()).hexdigest()

    assert fixture_hash == EXPECTED_FIXTURE_SHA256
    assert expected["provenance"]["fixture_sha256"] == fixture_hash
    assert expected["provenance"]["statcheck_version"] == "1.5.0"
    assert expected["provenance"]["metacheck_git_commit"]
    assert expected["provenance"]["llm"] == {
        "enabled": False,
        "max_calls": 0,
        "network_model_calls": "blocked",
    }
    assert expected["normalization"]["version"] == "field-aware-v1"
    assert expected["normalization"]["float_abs_tolerance"] == 1e-10


def test_all_six_defaults_return_without_synthetic_failure(response) -> None:  # type: ignore[no-untyped-def]
    assert tuple(response.modules_run) == DEFAULT_MODULES
    assert tuple(response.results) == DEFAULT_MODULES
    assert all(result.traffic_light != "fail" for result in response.results.values())


def test_default_modules_match_field_aware_r_oracle(
    expected: dict[str, Any],
    response,  # type: ignore[no-untyped-def]
) -> None:
    normalization = expected["normalization"]
    expected_modules = expected["modules_run"]
    table_fields = normalization["table_fields"]
    summary_fields = normalization["summary_fields"]
    float_fields = normalization["float_fields"]
    tolerance = normalization["float_abs_tolerance"]

    assert list(DEFAULT_MODULES) == expected_modules
    assert response.modules_run == expected_modules
    assert list(response.results) == expected_modules

    for module_name in expected_modules:
        actual_result = response.results[module_name].model_dump(mode="json")
        expected_result = expected["results"][module_name]

        assert actual_result["module"] == expected_result["module"]
        assert actual_result["title"] == expected_result["title"]
        assert actual_result["traffic_light"] == expected_result["traffic_light"]

        _assert_projected_rows(
            actual_result["table"],
            expected_result["table"],
            fields=table_fields[module_name],
            float_fields=_as_field_set(float_fields.get(module_name, [])),
            tolerance=tolerance,
            module_name=module_name,
            table_name="table",
        )
        _assert_projected_rows(
            actual_result["summary_table"],
            expected_result["summary_table"],
            fields=summary_fields[module_name],
            float_fields=set(),
            tolerance=tolerance,
            module_name=module_name,
            table_name="summary_table",
        )


def _as_field_set(value: str | list[str]) -> set[str]:
    return {value} if isinstance(value, str) else set(value)


def _assert_projected_rows(
    actual_rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
    *,
    fields: list[str],
    float_fields: set[str],
    tolerance: float,
    module_name: str,
    table_name: str,
) -> None:
    assert len(actual_rows) == len(expected_rows), f"{module_name}.{table_name} row count"

    for row_index, (actual_row, expected_row) in enumerate(
        zip(actual_rows, expected_rows, strict=True)
    ):
        assert list(expected_row) == fields, (
            f"{module_name}.{table_name}[{row_index}] oracle field/order drift"
        )
        for field in fields:
            assert field in actual_row, f"{module_name}.{table_name}[{row_index}].{field} missing"
            actual_value = actual_row[field]
            expected_value = expected_row[field]
            if (
                module_name == "power"
                and table_name == "summary_table"
                and field == "power_complete"
                and actual_value is None
            ):
                actual_value = 0

            if field in float_fields and actual_value is not None and expected_value is not None:
                assert isinstance(actual_value, int | float)
                assert isinstance(expected_value, int | float)
                assert math.isclose(
                    actual_value,
                    expected_value,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                ), f"{module_name}.{table_name}[{row_index}].{field}"
            else:
                assert actual_value == expected_value, (
                    f"{module_name}.{table_name}[{row_index}].{field}"
                )
