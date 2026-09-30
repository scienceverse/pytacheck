"""Provider request bodies and reply parsing (ellmer's providers, no SDKs)."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import metacheck.llm as L
from metacheck.llm import providers as P
from metacheck.llm._rds import RInt
from metacheck.llm.providers import LLMError
from metacheck.utils import local_options
from tests.httpmock import mock_path
from tests.llm.support import load_json

TS = L.type_object(
    variables=L.type_array(
        L.type_object(
            variable_name=L.type_string("Exact name"),
            label=L.type_string("Label", required=False),
            kind=L.type_enum(["a", "b", None], "Kind", required=False),
            n=L.type_integer(required=False),
            ok=L.type_boolean(),
        )
    )
)


def _maxtok() -> Any:
    with local_options({"metacheck.llm_max_tokens": RInt(8192)}):
        return L.llm(
            "hello",
            "Sys",
            model="groq/m",
            params={"frequency_penalty": 0.1, "presence_penalty": -0.2, "log_probs": True},
        )


SCENARIOS: dict[str, Callable[[], Any]] = {
    "groq_plain": lambda: L.llm(
        'hello é "q"\n',
        "Sys prompt",
        model="groq/test-model",
        params={"seed": 42, "top_p": 0.5, "stop_sequences": "END"},
    ),
    "groq_struct": lambda: L.llm("text one", "Extract", type=TS, model="groq/openai/gpt-oss-20b"),
    "groq_qwen3": lambda: L.llm("x", "S", model="groq/qwen/qwen3-32b"),
    "gemini_plain": lambda: L.llm(
        "hello", "Sys", model="google_gemini/gemini-2.5-flash", params={"seed": 1, "top_k": 3}
    ),
    "gemini_struct": lambda: L.llm("hello", "Sys", type=TS, model="google_gemini/gemini-2.5-flash"),
    "anthropic_plain": lambda: L.llm("hello", "Sys", model="anthropic/claude-sonnet-4-5"),
    "anthropic_struct": lambda: L.llm("hello", "Sys", type=TS, model="anthropic/claude-sonnet-4-5"),
    "anthropic_tool": lambda: L.llm(
        "hello", "Sys", type=TS, model="anthropic/claude-3-5-haiku-latest"
    ),
    "openai_plain": lambda: L.llm("hello", "Sys", model="openai/gpt-4.1-mini"),
    "openai_struct": lambda: L.llm("hello", "Sys", type=TS, model="openai/gpt-5-mini"),
    "mistral_plain": lambda: L.llm(
        "hello", "Sys", model="mistral/mistral-small-latest", params={"seed": 7}
    ),
    "deepseek_plain": lambda: L.llm("hello", "Sys", model="deepseek/deepseek-chat"),
    "openrouter_plain": lambda: L.llm("hello", "Sys", model="openrouter/openai/gpt-4o"),
    "vllm_plain": lambda: L.llm("hello", "Sys", model="vllm/GLM-5"),
    "groq_array_top": lambda: L.llm(
        "hello", "Sys", type=L.type_array(L.type_string()), model="groq/m"
    ),
    "gemini_default": lambda: L.llm("hello", "Sys", model="google_gemini"),
    "groq_maxtok": _maxtok,
}


@pytest.mark.parametrize("case", load_json("request_bodies.json"), ids=lambda c: c["name"])
def test_request_body_matches_r(case: dict[str, str], llm_on: Path) -> None:
    """The exact bytes ellmer sends (so recorded fixtures replay on both sides)."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((mock_path(request), request.content.decode()))
        return httpx.Response(404, json={"error": "none"})

    with (
        local_options({"metacheck.llm.vllm.base_url": "https://vllm.example.test/v1"}),
        respx.mock() as router,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        router.route().mock(side_effect=handler)
        SCENARIOS[case["name"]]()
    assert seen, "no request was made"
    path, body = seen[0]
    assert body == case["body"]
    assert path == case["path"]


# ---------------------------------------------------------------------------
# ellmer::params()
# ---------------------------------------------------------------------------


def test_params_validation_messages() -> None:
    cases = {
        "top_p": ("a", '`top_p` must be a number or `NULL`, not the string "a".'),
        "temperature": (
            -1,
            "`temperature` must be a number larger than or equal to 0 or `NULL`, not the number -1.",
        ),
        "max_tokens": (
            0,
            "`max_tokens` must be a whole number larger than or equal to 1 or `NULL`, "
            "not the number 0.",
        ),
        "seed": (1.5, "`seed` must be a whole number or `NULL`, not the number 1.5."),
        "log_probs": ("a", '`log_probs` must be `TRUE`, `FALSE`, or `NULL`, not the string "a".'),
        "stop_sequences": (1, "`stop_sequences` must be a character vector or `NULL`, not the number 1."),
        "reasoning_effort": (1, "`reasoning_effort` must be a single string or `NULL`, not the number 1."),
        "top_k": (-1, "`top_k` must be a whole number larger than or equal to 0 or `NULL`, not the number -1."),
    }  # fmt: skip
    for name, (value, message) in cases.items():
        with pytest.raises(ValueError) as err:
            P.params(**{name: value})
        assert str(err.value) == message


def test_params_order_and_extra_args() -> None:
    p = P.params(max_tokens=4096, think=False, temperature=0.0, stop_sequences="END")
    assert list(p) == ["temperature", "max_tokens", "stop_sequences", "extra_args"]
    assert p["extra_args"] == {"think": False}
    assert p["stop_sequences"] == ["END"]


def test_standardise_params_warns_on_unsupported() -> None:
    with pytest.warns(UserWarning, match='Ignoring unsupported parameters: "seed"'):
        out = P.ProviderOpenAI("https://x").chat_params({"temperature": 0.0, "seed": 1})
    assert out == {"temperature": 0.0}


# ---------------------------------------------------------------------------
# chat() dispatch and credentials
# ---------------------------------------------------------------------------


def test_chat_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    chat = P.chat("groq/openai/gpt-oss-20b", system_prompt="s")
    assert isinstance(chat.provider, P.ProviderGroq)
    assert chat.model.name == "openai/gpt-oss-20b"
    with pytest.raises(LLMError, match=r"Can't find provider `ellmer::chat_nope\(\)`"):
        P.chat("nope/x")
    with pytest.raises(LLMError, match="does not implement"):
        P.chat("aws_bedrock/x")
    with pytest.raises(LLMError, match="defunct"):
        P.chat("github/x")


def test_default_model_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    chat = P.chat("groq")
    assert chat.model.name == "openai/gpt-oss-20b"
    assert 'Using model = "openai/gpt-oss-20b".' in capsys.readouterr().err


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    chat = P.chat("anthropic/claude-sonnet-4-5")
    with pytest.raises(LLMError, match=r"Can.t find env var `ANTHROPIC_API_KEY`\."):
        chat.chat("hi")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="No Google credentials"):
        P.chat("google_gemini/gemini-2.5-flash")


def test_google_api_key_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    chat = P.chat("google_gemini/gemini-2.5-flash")
    assert chat.provider.auth_headers() == {"x-goog-api-key": "google"}


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------


def _reply(json: Any, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=json)


def test_openai_compatible_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    reply = {"choices": [{"message": {"content": '{"a": "x", "n": 2}', "reasoning": "because"}}]}
    with respx.mock() as router:
        router.route().mock(side_effect=_reply(reply))
        chat = P.chat("groq/m", system_prompt="s")
        out = chat.chat_structured("t", type=L.type_object(a=L.type_string(), n=L.type_integer()))
    assert out == {"a": "x", "n": 2}
    from metacheck.llm.core import _llm_extract_thinking

    assert _llm_extract_thinking(chat) == "because"


def test_gemini_reply_skips_thoughts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    reply = {
        "candidates": [{"content": {"parts": [{"text": "hm", "thought": True}, {"text": "Yes"}]}}]
    }
    with respx.mock() as router:
        router.route().mock(side_effect=_reply(reply))
        assert P.chat("google_gemini/gemini-2.5-flash").chat("q") == "Yes"


def test_anthropic_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    with respx.mock() as router:
        router.route().mock(side_effect=_reply(err, 400))
        with pytest.raises(LLMError) as e:
            P.chat("anthropic/claude-sonnet-4-5").chat("q")
    assert str(e.value) == "HTTP 400 Bad Request.\nℹ Overloaded [overloaded_error]"
    assert e.value.resp is not None and e.value.resp.status_code == 400


def test_no_json_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with respx.mock() as router:
        router.route().mock(side_effect=_reply({"choices": [{"message": {"content": "Sure! {}"}}]}))
        with pytest.raises(Exception, match="lexical error: invalid char in json text"):
            P.chat("groq/m").chat_structured("t", type=L.type_object(a=L.type_string()))


def test_transport_errors_are_llm_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck.llm.core import _llm_is_systemic_error

    monkeypatch.setenv("GROQ_API_KEY", "k")
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectError("[Errno -2] Name or service not known"))
        with pytest.raises(LLMError) as e:
            P.chat("groq/m").chat("q")
    assert "Could not resolve host: api.groq.com" in str(e.value)
    assert _llm_is_systemic_error(e.value)
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(LLMError) as e:
            P.chat("groq/m").chat("q")
    assert e.value.timeout
    assert not _llm_is_systemic_error(e.value)


def test_cli_bullet_wrapping() -> None:
    text = "Failed to validate JSON. Please adjust your prompt. See 'failed_generation' for more details."
    assert P._cli_bullet(text) == (
        "ℹ Failed to validate JSON. Please adjust your prompt. See 'failed_generation'\n"
        "  for more details."
    )
    assert P._cli_bullet("multi\nline\ttext") == "ℹ multi line text"


def test_recorded_upstream_replies_parse(upstream_dir: Path) -> None:
    """metacheck's recorded Groq/Gemini replies parse as ellmer parses them."""
    from metacheck.llm._json import parse_json

    apis = upstream_dir / "tests" / "testthat" / "apis"
    groq = sorted((apis / "api.groq.com" / "openai" / "v1" / "chat").glob("completions-*.json"))
    gemini = sorted((apis / "generativelanguage.googleapis.com").rglob("*generateContent*.json"))
    assert groq and gemini
    model = P.Model("m")
    for f in groq:
        result = parse_json(f.read_text(encoding="utf-8"))
        turn = P.ProviderGroq("https://x").value_turn(model, result, has_type=False)
        assert turn.text == result["choices"][0]["message"]["content"]
    for f in gemini:
        result = parse_json(f.read_text(encoding="utf-8"))
        turn = P.ProviderGoogleGemini("https://x").value_turn(model, result, has_type=False)
        parts = result["candidates"][0]["content"]["parts"]
        assert turn.text == "".join(p["text"] for p in parts if not p.get("thought"))
