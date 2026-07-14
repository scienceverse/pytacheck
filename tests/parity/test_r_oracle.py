from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from pytacheck.engine import DEFAULT_MODULES, CheckEngine
from pytacheck.models import BibrPaper

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PAPER_PATH = FIXTURE_DIR / "to_err_is_human.json"
EXPECTED_PATH = FIXTURE_DIR / "to_err_is_human.expected.json"
EXPECTED_FIXTURE_SHA256 = "c99be5ea276910a6e7fbd0301ee0b89e0ccc7c7af5d27727e018d573dc646da4"
EXPECTED_ORACLE_SHA256 = "d19ce1b6e1135cc4a783b6584b2e173b6e45b563bd1cad3c5754cf0ed9c2e723"
EXPECTED_METACHECK_COMMIT = "0291d575628b0c8cec56eb64c944ad269c91edc4"
EXPECTED_MODULE_SHA256 = {
    "power": "f24acc1d512a02a90b31d78d91eec228ae8c209f54688a56a1688a3c98cd90aa",
    "marginal": "d641aaf05288c13ee7d618e7db9cbe0a50d36dadade14796a53ee978c77a9675",
    "stat_check": "1c80c646d46fc90c9b31be745c0ba357e8056f159d54265402670ad271283f87",
    "stat_effect_size": "8afee4b8f4b3e60c5c483adff2c453171cea4237cf3e85d19eb1ed22571205d8",
    "stat_p_exact": "ce99f7d57ee6b97d37a464b68539ba2c0ed216384cd010b6fb24b47a6e1f2392",
    "stat_p_nonsig": "db312ff1dfd096fc311279753fa46e66e29d84478b96f8e00d3eeba454e315b7",
}
EXPECTED_TABLE_FIELDS = {
    "power": [
        "text",
        "paragraph_id",
        "section_id",
        "paper_id",
        "header",
        "section_type",
        "power_type",
        "complete",
        "power_id",
    ],
    "marginal": [
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "paper_id",
        "header",
        "section_type",
    ],
    "stat_check": [
        "test_type",
        "df1",
        "df2",
        "test_comp",
        "test_value",
        "p_comp",
        "reported_p",
        "computed_p",
        "raw",
        "error",
        "decision_error",
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "paper_id",
        "header",
        "section_type",
    ],
    "stat_effect_size": [
        "paper_id",
        "text_id",
        "test",
        "test_text",
        "es",
        "section_id",
        "paragraph_id",
        "text",
        "d_reported",
        "d_reported_text",
        "t_value",
        "df",
        "d_implied_paired_dz",
        "d_implied_paired_drm_r05",
        "d_implied_indep_equal_n",
        "d_implied_indep_unequal_min",
        "d_implied_indep_unequal_max",
        "d_implied_n",
        "d_coherence",
        "d_coherence_assumption",
        "d_coherence_note",
        "f_reported",
        "f_reported_text",
        "df1",
        "df2",
        "eta_implied_partial",
        "omega_implied_partial",
        "eta_coherence",
        "eta_coherence_assumption",
        "eta_coherence_note",
    ],
    "stat_p_exact": [
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "paper_id",
        "header",
        "section_type",
        "p_comp",
        "p_value",
        "expanded",
        "imprecise",
        "zero",
    ],
    "stat_p_nonsig": [
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "paper_id",
        "header",
        "section_type",
        "p_comp",
        "p_value",
        "significance",
        "expanded",
    ],
}
EXPECTED_SUMMARY_FIELDS = {
    "power": ["paper_id", "power_n", "power_complete"],
    "marginal": ["paper_id", "marginal"],
    "stat_check": [
        "paper_id",
        "statcheck_found",
        "statcheck_errors",
        "statcheck_decision_errors",
    ],
    "stat_effect_size": [
        "paper_id",
        "ttests_with_es",
        "ttests_without_es",
        "Ftests_with_es",
        "Ftests_without_es",
    ],
    "stat_p_exact": ["paper_id", "n_imprecise", "n_zero"],
    "stat_p_nonsig": ["paper_id", "n_nonsignificant"],
}
EXPECTED_FLOAT_FIELDS = {"stat_check": "computed_p"}
REQUIRED_CAPTURE_CREDENTIAL_ENV = {
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "CLOUDFLARE_API_KEY",
    "DATABRICKS_HOST",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GITHUB_PAT",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "HUGGINGFACE_API_KEY",
    "MISTRAL_API_KEY",
    "OLLAMA_BASE_URL",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OSF_PAT",
    "PERPLEXITY_API_KEY",
    "PORTKEY_API_KEY",
    "REGCHECK_API_TOKEN",
    "SCIVRS_API_KEY",
}


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
def response():
    return CheckEngine().check(_paper())


def test_upstream_fixture_and_oracle_provenance_are_frozen(expected: dict[str, Any]) -> None:
    fixture_hash = hashlib.sha256(PAPER_PATH.read_bytes()).hexdigest()
    oracle_hash = hashlib.sha256(EXPECTED_PATH.read_bytes()).hexdigest()
    module_hashes = expected["provenance"]["module_sha256"]

    assert fixture_hash == EXPECTED_FIXTURE_SHA256
    assert oracle_hash == EXPECTED_ORACLE_SHA256
    assert expected["provenance"]["fixture_sha256"] == fixture_hash
    assert expected["provenance"]["statcheck_version"] == "1.5.0"
    assert expected["provenance"]["metacheck_git_commit"] == EXPECTED_METACHECK_COMMIT
    assert expected["provenance"]["metacheck_relevant_source_dirty"] is False
    assert module_hashes == EXPECTED_MODULE_SHA256
    assert all(
        isinstance(module_hash, str)
        and len(module_hash) == 64
        and set(module_hash) <= set("0123456789abcdef")
        for module_hash in module_hashes.values()
    )
    assert expected["provenance"]["llm"] == {
        "enabled": False,
        "max_calls": 0,
        "network_model_calls": "blocked",
    }
    assert expected["normalization"]["version"] == "field-aware-v1"
    assert expected["normalization"]["float_abs_tolerance"] == 1e-10
    assert expected["normalization"]["table_fields"] == EXPECTED_TABLE_FIELDS
    assert expected["normalization"]["summary_fields"] == EXPECTED_SUMMARY_FIELDS
    assert expected["normalization"]["float_fields"] == EXPECTED_FLOAT_FIELDS


def test_capture_script_sanitizes_network_before_loading_and_rejects_dirty_source() -> None:
    script = (Path(__file__).parents[2] / "scripts" / "capture_r_oracle.R").read_text(
        encoding="utf-8"
    )

    sanitize_call = script.index("sanitize_capture_environment()")
    load_call = script.index("pkgload::load_all")
    llm_trap_call = script.index("block_llm_calls()")
    assert sanitize_call < load_call < llm_trap_call
    assert '"OSF_PAT"' in script
    assert '"HTTP_PROXY"' in script
    assert '"HTTPS_PROXY"' in script
    assert '"ALL_PROXY"' in script
    assert '"NO_PROXY"' in script
    assert '"--untracked-files=all"' in script
    assert "Refusing to capture from dirty Metacheck source" in script


def test_capture_script_clears_complete_frozen_metacheck_credential_set_before_loading() -> None:
    script = (Path(__file__).parents[2] / "scripts" / "capture_r_oracle.R").read_text(
        encoding="utf-8"
    )
    credential_block = re.search(
        r"PROVIDER_CREDENTIAL_ENV\s*<-\s*c\((?P<body>.*?)\n\)",
        script,
        flags=re.DOTALL,
    )
    sanitizer_definition = re.search(
        r"sanitize_capture_environment\s*<-\s*function\(\)\s*\{(?P<body>.*?)\n\}",
        script,
        flags=re.DOTALL,
    )

    assert credential_block is not None
    assert sanitizer_definition is not None
    declared_credentials = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', credential_block.group("body")))
    assert declared_credentials == REQUIRED_CAPTURE_CREDENTIAL_ENV
    assert "Sys.unsetenv(PROVIDER_CREDENTIAL_ENV)" in sanitizer_definition.group("body")
    assert script.index("sanitize_capture_environment()") < script.index("pkgload::load_all")


def test_all_six_defaults_return_without_synthetic_failure(response) -> None:
    assert tuple(response.modules_run) == DEFAULT_MODULES
    assert tuple(response.results) == DEFAULT_MODULES
    assert all(result.traffic_light != "fail" for result in response.results.values())


def test_default_modules_match_field_aware_r_oracle(
    expected: dict[str, Any],
    response,
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
