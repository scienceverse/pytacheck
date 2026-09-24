"""Port of metacheck tests/testthat/test-db-regcheck.R (RegCheck API client)."""

from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.db.regcheck import (
    RegCheckError,
    _regcheck_sanitize,
    regcheck_base_url,
    regcheck_compare,
    regcheck_tidy,
)

CANNED = {
    "items": [
        {
            "dimension": "Sample size",
            "deviation_judgement": "yes",
            "paper_content_summary": "The paper reports 120 participants [PAPER_0001].",
            "registration_content_summary": "The registration plans 100 participants [REG_0001].",
            "deviation_information": "The paper sample (120) exceeds the preregistered sample (100).",
            "paper_content_quotes": "[PAPER_0001] We recruited 120 participants.",
            "registration_content_quotes": "[REG_0001] We will collect 100 participants.",
        },
        {
            "dimension": "Hypotheses",
            "deviation_judgement": "no",
            "paper_content_summary": "H1: condition A > B.",
            "registration_content_summary": "H1: condition A > B.",
            "deviation_information": "The hypotheses are consistent.",
            "paper_content_quotes": "[PAPER_0002] We predicted A > B.",
            "registration_content_quotes": "[REG_0002] We predict A > B.",
        },
        {
            "dimension": "Exclusion criteria",
            "deviation_judgement": "missing",
            "paper_content_summary": "Not described.",
            "registration_content_summary": "Exclude RTs under 200 ms.",
            "deviation_information": "The paper does not report exclusions, so this cannot be assessed.",
            "paper_content_quotes": "",
            "registration_content_quotes": "[REG_0003] We will exclude RTs under 200 ms.",
        },
    ]
}
COLUMNS = [
    "dimension",
    "deviation_judgement",
    "paper_summary",
    "prereg_summary",
    "deviation_information",
    "paper_quotes",
    "prereg_quotes",
]


def test_regcheck_base_url_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_BASE_URL", "")
    assert regcheck_base_url("ollama") == "http://localhost:8000"
    assert regcheck_base_url("groq").startswith("https://")
    assert regcheck_base_url("openai").startswith("https://")
    assert regcheck_base_url("ollama", "https://my.server/") == "https://my.server"
    assert regcheck_base_url("groq", "http://localhost:9999//") == "http://localhost:9999"

    monkeypatch.setenv("REGCHECK_BASE_URL", "https://env.server/")
    assert regcheck_base_url("ollama") == "https://env.server"
    assert regcheck_base_url("ollama", "https://arg.server") == "https://arg.server"


def test_regcheck_compare_input_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RegCheckError, match="REGCHECK_API_TOKEN"):
        regcheck_compare("paper text", "prereg text", client="groq")

    monkeypatch.setenv("REGCHECK_API_TOKEN", "test-token")
    with pytest.raises(ValueError, match="exactly one"):
        regcheck_compare("paper text")
    with pytest.raises(ValueError, match="exactly one"):
        regcheck_compare("paper text", "prereg text", registration_id="NCT01234567")
    with pytest.raises(ValueError, match="paper_text"):
        regcheck_compare("", "prereg text")
    with pytest.raises(ValueError, match="paper_text"):
        regcheck_compare(["a", "b"], "prereg text")


def test_regcheck_sanitize() -> None:
    assert _regcheck_sanitize("α = .05, η² = 0.15") == "alpha = .05, eta2 = 0.15"
    assert _regcheck_sanitize("p ≤ .05 – a “result”") == 'p <= .05 - a "result"'
    out = _regcheck_sanitize("ok 中文 ok")
    assert out is not None
    assert out == "ok  ok"
    assert _regcheck_sanitize("Daniël Lakens") == "Daniël Lakens"
    assert _regcheck_sanitize(None) is None


def test_unknown_client_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_API_TOKEN", "tok")
    with pytest.raises(ValueError, match=r"(?i)groq|openai|deepseek") as err:
        regcheck_compare("paper text", "prereg text", client="gpt5")
    assert "regcheck" in str(err.value).lower()


def test_client_partial_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    # match.arg() accepts unique abbreviations
    monkeypatch.setenv("REGCHECK_API_TOKEN", "")
    with pytest.raises(RegCheckError, match="'groq' client"):
        regcheck_compare("paper text", "prereg text", client="gr")


def test_missing_token_for_hosted_client() -> None:
    with pytest.raises(RegCheckError, match="REGCHECK_API_TOKEN") as err:
        regcheck_compare("paper text", "prereg text", client="groq")
    assert "edit_r_environ" in str(err.value)


def test_401_for_ollama_points_to_regcheck_start_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_API_TOKEN", "bad-token")
    with respx.mock() as router:
        router.route().mock(return_value=httpx.Response(401))
        with pytest.raises(RegCheckError, match="local RegCheck server rejected"):
            regcheck_compare("paper text", "prereg text", client="ollama")


def test_401_for_hosted_client_points_to_renviron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_API_TOKEN", "bad-token")
    with respx.mock() as router:
        router.route().mock(return_value=httpx.Response(401))
        with pytest.raises(RegCheckError, match="REGCHECK_API_TOKEN"):
            regcheck_compare("paper text", "prereg text", client="groq")


def test_connection_refused_for_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_API_TOKEN", "tok")
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(RegCheckError, match="regcheck_start_local"):
            regcheck_compare("paper text", "prereg text", client="ollama")


def test_regcheck_tidy() -> None:
    tidy = regcheck_tidy(CANNED)
    assert isinstance(tidy, pd.DataFrame)
    assert len(tidy) == 3
    assert list(tidy.columns) == COLUMNS
    assert tidy["dimension"].tolist() == ["Sample size", "Hypotheses", "Exclusion criteria"]
    assert tidy["deviation_judgement"].tolist() == ["yes", "no", "missing"]

    partial = regcheck_tidy({"items": [{"dimension": "Sample size"}]})
    assert len(partial) == 1
    assert pd.isna(partial["deviation_judgement"].iat[0])

    empty = regcheck_tidy({"items": []})
    assert len(empty) == 0
    assert list(empty.columns) == COLUMNS
    assert len(regcheck_tidy({})) == 0


def test_full_comparison_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submit (JSON endpoint), poll until success, tidy; texts are sanitised."""
    monkeypatch.setenv("REGCHECK_API_TOKEN", "tok")
    polls = iter(
        [
            {"state": "queued", "processed_dimensions": 0, "total_dimensions": 3},
            {"state": "in_progress", "processed_dimensions": 2, "total_dimensions": 3},
            {"state": "success", "result": CANNED},
        ]
    )
    with respx.mock() as router:
        submit = router.post("http://localhost:8000/api/v1/comparisons/text").mock(
            return_value=httpx.Response(202, json={"task_id": "t1", "status_url": "/x"})
        )
        poll = router.get("http://localhost:8000/api/v1/comparisons/t1").mock(
            side_effect=lambda request: httpx.Response(200, json=next(polls))
        )
        dims = pd.DataFrame({"dimension": ["Sample size"], "definition": ["N"]})
        tidy = regcheck_compare("paper α text", "prereg text", dimensions=dims)
    body = json.loads(submit.calls.last.request.content)
    assert body == {
        "paper_text": "paper alpha text",
        "client": "ollama",
        "reasoning_effort": "medium",
        "append_previous_output": True,
        "multiple_experiments": False,
        "registration_text": "prereg text",
        "dimensions": [{"dimension": "Sample size", "definition": "N"}],
    }
    assert submit.calls.last.request.headers["Authorization"] == "Bearer tok"
    assert poll.call_count == 3
    assert tidy["dimension"].tolist() == ["Sample size", "Hypotheses", "Exclusion criteria"]
    assert tidy.attrs["regcheck_result"] == CANNED


def test_default_local_token_and_multipart_fallback() -> None:
    """Without a token, ollama uses the built-in one; 404 on /text falls back to multipart."""
    with respx.mock() as router:
        router.post("http://localhost:8000/api/v1/comparisons/text").mock(
            return_value=httpx.Response(404)
        )
        multipart = router.post("http://localhost:8000/api/v1/comparisons").mock(
            return_value=httpx.Response(202, json={"task_id": "t2"})
        )
        router.get("http://localhost:8000/api/v1/comparisons/t2").mock(
            return_value=httpx.Response(200, json={"state": "success", "result": {"items": []}})
        )
        tidy = regcheck_compare("paper", registration_id="NCT01234567")
    request = multipart.calls.last.request
    assert request.headers["Authorization"] == "Bearer metacheck-local"
    content = request.content.decode()
    assert 'name="registration_id"' in content and "NCT01234567" in content
    assert 'name="append_previous_output"' in content and "yes" in content
    assert 'filename="regcheck_paper.txt"' in content
    assert len(tidy) == 0


def test_poll_failure_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGCHECK_API_TOKEN", "tok")
    with respx.mock() as router:
        router.post("http://localhost:8000/api/v1/comparisons/text").mock(
            return_value=httpx.Response(202, json={"task_id": "t3"})
        )
        router.get("http://localhost:8000/api/v1/comparisons/t3").mock(
            return_value=httpx.Response(200, json={"state": "failure", "status": "LLM down"})
        )
        with pytest.raises(RegCheckError, match="RegCheck comparison failed: LLM down"):
            regcheck_compare("paper", "prereg")
    with respx.mock() as router:
        router.post("http://localhost:8000/api/v1/comparisons/text").mock(
            return_value=httpx.Response(202, json={"task_id": "t4"})
        )
        router.get("http://localhost:8000/api/v1/comparisons/t4").mock(
            return_value=httpx.Response(200, json={"state": "queued"})
        )
        with pytest.raises(RegCheckError, match="Timed out waiting for RegCheck task t4"):
            regcheck_compare("paper", "prereg", timeout=-1)
