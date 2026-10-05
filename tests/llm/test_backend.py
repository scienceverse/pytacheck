"""What the SDK back end sends to each provider family, and how it reads the answer.

One contract test per request family -- OpenAI chat completions (Groq, Mistral,
DeepSeek, Ollama, ...), OpenAI Responses, Anthropic Messages and Gemini
``generateContent`` -- asserts the model, the messages, the schema and the
parameters of the request the SDK makes, against a mock transport (no network:
:func:`tests.httpmock.no_network` refuses any connection).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

import metacheck.llm as L
from metacheck import http
from metacheck.llm import _backend, core, types
from metacheck.llm._backend import LLMError, complete
from metacheck.utils import local_options
from tests.httpmock import _httpx2_bridge, no_network
from tests.llm.support import KEYS

pytest.importorskip("openai")
pytest.importorskip("anthropic")
pytest.importorskip("google.genai")

PARAMS = {"temperature": 0.0, "max_tokens": 4096}

ITEMS = L.type_object(
    items=L.type_array(
        L.type_object(
            name=L.type_string("The name"),
            tag=L.type_enum(["a", "b", None], "A tag", required=False),
        ),
        "What was found",
    )
)
ITEMS_REPLY = {"items": [{"name": "dv", "tag": "a"}, {"name": "iv", "tag": None}]}


def chat_reply(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


def responses_reply(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "r1",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "m",
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "type": "message",
                    "id": "m1",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ],
        },
    )


def anthropic_reply(*blocks: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": list(blocks),
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def gemini_reply(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "index": 0,
                    "finishReason": "STOP",
                    "content": {"role": "model", "parts": [{"text": text}]},
                }
            ]
        },
    )


class Wire:
    """The mock transport: answers the SDKs' requests in order, remembers what they sent."""

    def __init__(self) -> None:
        self.replies: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []

    def serve(self, *replies: httpx.Response) -> None:
        self.replies.extend(replies)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies.pop(0) if self.replies else httpx.Response(500, text="no reply left")

    @property
    def sent(self) -> httpx.Request:
        assert len(self.requests) == 1, [str(r.url) for r in self.requests]
        return self.requests[0]

    @property
    def url(self) -> str:
        return str(self.sent.url)

    @property
    def body(self) -> dict[str, Any]:
        body = json.loads(self.sent.content)
        assert isinstance(body, dict)
        return body


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    for name in (*KEYS, "GOOGLE_API_KEY", "ANTHROPIC_BASE_URL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name in KEYS:
        monkeypatch.setenv(name, "test-key")
    monkeypatch.setattr(http, "sleep", lambda s: None)
    monkeypatch.setattr(http, "_host_reset", {})  # a 429 here must not slow another test
    _backend._clients.clear()
    transport = Wire()
    with no_network(), respx.mock(assert_all_called=False) as router, _httpx2_bridge():
        router.route().mock(side_effect=transport.handle)
        yield transport
    _backend._clients.clear()


def strict(schema_type: Any = ITEMS) -> dict[str, Any]:
    """The schema as OpenAI's strict mode takes it."""
    return types.type_as_json(schema_type, "openai")  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# OpenAI chat completions: Groq, Mistral, DeepSeek, Ollama
# ---------------------------------------------------------------------------


def test_groq_chat_completions_request(wire: Wire) -> None:
    wire.serve(chat_reply(" TRUE "))
    out = complete(
        "groq/openai/gpt-oss-20b",
        "Be brief.",
        "hello",
        None,
        {**PARAMS, "seed": 3, "top_p": 0.5, "stop_sequences": ["END"]},
        {"reasoning_effort": "low"},
    )
    assert out == " TRUE "
    assert wire.url == "https://api.groq.com/openai/v1/chat/completions"
    assert wire.sent.headers["authorization"] == "Bearer test-key"
    assert wire.body == {
        "model": "openai/gpt-oss-20b",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello"},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 4096,
        "seed": 3,
        "top_p": 0.5,
        "stop": ["END"],
        "reasoning_effort": "low",
    }


def test_chat_completions_structured_request_and_reply(wire: Wire) -> None:
    wire.serve(chat_reply(json.dumps(ITEMS_REPLY)))
    out = complete("groq/openai/gpt-oss-20b", "Extract", "two vars", ITEMS, PARAMS)
    assert out == ITEMS_REPLY
    assert wire.body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "structured_data", "schema": strict(), "strict": True},
    }
    item = wire.body["response_format"]["json_schema"]["schema"]["properties"]["items"]["items"]
    assert item["required"] == ["name", "tag"]  # strict: every property listed
    assert item["properties"]["tag"]["type"] == ["string", "null"]  # an optional one is nullable
    assert item["additionalProperties"] is False


def test_a_type_that_is_not_an_object_travels_in_a_wrapper(wire: Wire) -> None:
    wire.serve(chat_reply('{"wrapper": ["a", "b"]}'))
    out = complete("groq/x", "s", "u", L.type_array(L.type_string("A name")), PARAMS)
    assert out == ["a", "b"]
    schema = wire.body["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == ["wrapper"]
    assert schema["properties"]["wrapper"]["type"] == "array"


def test_mistral_wire_names(wire: Wire) -> None:
    wire.serve(chat_reply("TRUE"))
    with pytest.warns(UserWarning, match=r'Ignoring unsupported parameters: "top_k"'):
        complete("mistral/mistral-large-latest", "s", "u", None, {**PARAMS, "seed": 5, "top_k": 3})
    assert wire.url == "https://api.mistral.ai/v1/chat/completions"
    body = wire.body
    assert body["model"] == "mistral-large-latest"
    assert body["max_tokens"] == 4096 and "max_completion_tokens" not in body
    assert body["random_seed"] == 5 and "seed" not in body
    assert "top_k" not in body
    assert body["temperature"] == 0.0


def test_mistral_content_chunks_are_joined(wire: Wire) -> None:
    wire.serve(
        httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "TR"},
                                {"type": "text", "text": "UE"},
                            ],
                        },
                    }
                ],
            },
        )
    )
    assert complete("mistral/m", "s", "u", None, PARAMS) == "TRUE"


def test_deepseek_wire_names(wire: Wire) -> None:
    wire.serve(chat_reply("TRUE"))
    with pytest.warns(UserWarning, match=r'Ignoring unsupported parameters: "seed" and "top_k"'):
        complete("deepseek/deepseek-v4-flash", "s", "u", None, {**PARAMS, "seed": 5, "top_k": 3})
    assert wire.url == "https://api.deepseek.com/chat/completions"
    body = wire.body
    assert body["max_tokens"] == 4096 and "max_completion_tokens" not in body
    assert "seed" not in body and "top_k" not in body


def test_ollama_is_reached_through_its_openai_compatible_endpoint(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire.serve(chat_reply("TRUE"))
    complete("ollama/qwen3:8b", "s", "u", None, {**PARAMS, "think": False})
    assert wire.url == "http://localhost:11434/v1/chat/completions"
    assert wire.body["think"] is False  # a parameter the standard ones do not name: sent as given
    assert wire.body["max_tokens"] == 4096


def test_unlisted_params_and_api_args_reach_the_request_body(wire: Wire) -> None:
    wire.serve(chat_reply("x"))
    complete("groq/x", "s", "u", None, {**PARAMS, "service_tier": "flex"}, {"user": "me"})
    assert wire.body["service_tier"] == "flex"
    assert wire.body["user"] == "me"


def test_openrouter_reports_an_error_inside_a_200(wire: Wire) -> None:
    wire.serve(
        httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [],
                "error": {"message": "provider overloaded", "code": 502},
            },
        )
    )
    with pytest.raises(LLMError, match="provider overloaded"):
        complete("openrouter/m", "s", "u", None, PARAMS)
    assert wire.url == "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# OpenAI Responses
# ---------------------------------------------------------------------------


def test_openai_responses_request(wire: Wire) -> None:
    wire.serve(responses_reply(json.dumps(ITEMS_REPLY)))
    with pytest.warns(UserWarning, match=r'Ignoring unsupported parameters: "seed"'):
        out = complete(
            "openai/gpt-5.6-terra",
            "Extract",
            "two vars",
            ITEMS,
            {**PARAMS, "seed": 1, "reasoning_effort": "low"},
        )
    assert out == ITEMS_REPLY
    assert wire.url == "https://api.openai.com/v1/responses"
    assert wire.sent.headers["authorization"] == "Bearer test-key"
    assert wire.body == {
        "model": "gpt-5.6-terra",
        "input": "two vars",
        "instructions": "Extract",
        "store": False,
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "reasoning": {"effort": "low"},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "structured_data",
                "schema": strict(),
                "strict": True,
            }
        },
    }


def test_openai_responses_plain_reply(wire: Wire) -> None:
    wire.serve(responses_reply("TRUE"))
    assert complete("openai/gpt-5.6-terra", "", "u", None, PARAMS) == "TRUE"
    assert "instructions" not in wire.body  # no system prompt, none sent


# ---------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------


def test_anthropic_request(wire: Wire) -> None:
    wire.serve(anthropic_reply(text_block("TRUE")))
    with pytest.warns(UserWarning, match=r'Ignoring unsupported parameters: "seed"'):
        out = complete(
            "anthropic/claude-sonnet-5",
            "Be brief.",
            "hello",
            None,
            {**PARAMS, "seed": 1, "top_p": 0.9},
        )
    assert out == "TRUE"
    assert wire.url == "https://api.anthropic.com/v1/messages"
    assert wire.sent.headers["x-api-key"] == "test-key"
    assert "anthropic-version" in wire.sent.headers
    assert wire.body == {
        "model": "claude-sonnet-5",
        "max_tokens": 4096,
        "system": [
            {
                "type": "text",
                "text": "Be brief.",
                "cache_control": {"type": "ephemeral", "ttl": "5m"},
            }
        ],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        "temperature": 0.0,  # the typed call has no such argument: sent through extra_body
        "top_p": 0.9,
    }


def test_anthropic_structured_output_uses_the_native_format(wire: Wire) -> None:
    wire.serve(anthropic_reply(text_block(json.dumps(ITEMS_REPLY))))
    out = complete("anthropic/claude-sonnet-5", "Extract", "two vars", ITEMS, PARAMS)
    assert out == ITEMS_REPLY
    fmt = wire.body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    item = fmt["schema"]["properties"]["items"]["items"]
    assert item["required"] == ["name"]  # not the strict form: an optional property stays optional
    assert "tools" not in wire.body


def test_anthropic_structured_output_of_an_older_model_forces_a_tool(wire: Wire) -> None:
    wire.serve(
        anthropic_reply(
            {
                "type": "tool_use",
                "id": "t1",
                "name": "_structured_tool_call",
                "input": {"data": ITEMS_REPLY},
            }
        )
    )
    out = complete("anthropic/claude-3-5-haiku-latest", "Extract", "two vars", ITEMS, PARAMS)
    assert out == ITEMS_REPLY
    body = wire.body
    assert body["tool_choice"] == {"type": "tool", "name": "_structured_tool_call"}
    (tool,) = body["tools"]
    assert tool["name"] == "_structured_tool_call"
    assert list(tool["input_schema"]["properties"]) == ["data"]  # the type, wrapped under `data`
    assert "output_config" not in body


def test_anthropic_reasoning_effort(wire: Wire) -> None:
    wire.serve(anthropic_reply(text_block("x")))
    complete("claude/claude-sonnet-5", "s", "u", None, {**PARAMS, "reasoning_effort": "low"})
    body = wire.body
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}


def test_anthropic_empty_text_is_not_sent_empty(wire: Wire) -> None:
    wire.serve(anthropic_reply(text_block("x")))
    complete("anthropic/claude-sonnet-5", "s", "  ", None, PARAMS)
    assert wire.body["messages"][0]["content"] == [{"type": "text", "text": "[empty string]"}]


def test_anthropic_base_url_override(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.test/v1")
    wire.serve(anthropic_reply(text_block("x")))
    complete("anthropic/claude-sonnet-5", "s", "u", None, PARAMS)
    assert wire.url == "https://proxy.test/v1/messages"


# ---------------------------------------------------------------------------
# Gemini generateContent
# ---------------------------------------------------------------------------


def test_gemini_request(wire: Wire) -> None:
    wire.serve(gemini_reply(json.dumps(ITEMS_REPLY)))
    out = complete(
        "google_gemini/gemini-3.7-flash",
        "Extract",
        "two vars",
        ITEMS,
        {**PARAMS, "seed": 1, "top_k": 3, "reasoning_effort": "low"},
    )
    assert out == ITEMS_REPLY
    assert wire.url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent"
    )
    assert wire.sent.headers["x-goog-api-key"] == "test-key"
    body = wire.body
    assert body["contents"] == [{"role": "user", "parts": [{"text": "two vars"}]}]
    assert body["systemInstruction"]["parts"] == [{"text": "Extract"}]
    cfg = body["generationConfig"]
    assert cfg["temperature"] == 0.0
    assert cfg["maxOutputTokens"] == 4096
    assert cfg["seed"] == 1
    assert cfg["topK"] == 3
    assert cfg["thinkingConfig"] == {"thinking_level": "LOW"}
    assert cfg["responseMimeType"] == "application/json"
    schema = cfg["responseSchema"]  # as written (a null among the enum values), as ellmer sends it
    assert schema["properties"]["items"]["items"]["required"] == ["name"]
    assert schema["properties"]["items"]["items"]["properties"]["tag"]["enum"] == ["a", "b", None]
    assert "responseJsonSchema" not in cfg


def test_gemini_plain_reply(wire: Wire) -> None:
    wire.serve(gemini_reply("TRUE"))
    assert complete("google_gemini", "", "hello", None, PARAMS) == "TRUE"  # the default model
    assert "gemini-3.7-flash:generateContent" in wire.url
    assert "systemInstruction" not in wire.body


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"GEMINI_API_KEY": "gem"}, "gem"),
        ({"GOOGLE_API_KEY": "goog"}, "goog"),  # either name selects Gemini
        ({"GEMINI_API_KEY": "gem", "GOOGLE_API_KEY": "goog"}, "goog"),  # the SDK's own order
    ],
)
def test_gemini_key_names(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    wire.serve(gemini_reply("x"))
    complete("google_gemini/gemini-3.7-flash", "s", "u", None, PARAMS)
    assert wire.sent.headers["x-goog-api-key"] == expected


# ---------------------------------------------------------------------------
# errors, retries, settings
# ---------------------------------------------------------------------------


def test_a_provider_error_becomes_an_llm_error_with_its_reason(wire: Wire) -> None:
    wire.serve(httpx.Response(400, json={"error": {"message": "Please reduce the length."}}))
    with pytest.raises(LLMError) as exc:
        complete("groq/x", "s", "u", None, PARAMS)
    err = exc.value
    assert err.status == 400
    assert err.detail == "Please reduce the length."
    assert str(err) == "HTTP 400 Bad Request.\nℹ Please reduce the length."
    assert core._llm_error_message(err).endswith("Provider says: Please reduce the length.")
    assert len(wire.requests) == 1  # a rejected request is not repeated


def test_anthropic_error_names_the_error_type(wire: Wire) -> None:
    wire.serve(
        httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
        )
    )
    with pytest.raises(LLMError) as exc:
        complete("anthropic/claude-sonnet-5", "s", "u", None, PARAMS)
    assert str(exc.value) == "HTTP 400 Bad Request.\nℹ bad [invalid_request_error]"


def test_gemini_error_body(wire: Wire) -> None:
    wire.serve(httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid."}}))
    with pytest.raises(LLMError) as exc:
        complete("google_gemini/gemini-3.7-flash", "s", "u", None, PARAMS)
    assert exc.value.status == 400
    assert exc.value.detail == "API key not valid."


def test_a_rate_limit_is_retried_after_the_wait_the_provider_names(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(http, "sleep", slept.append)
    wire.serve(
        httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow down"}}),
        chat_reply("TRUE"),
    )
    assert complete("groq/x", "s", "u", None, PARAMS) == "TRUE"
    assert len(wire.requests) == 2
    assert slept and 0 < slept[0] <= 2.5


def test_an_overloaded_provider_gives_up_after_three_tries(wire: Wire) -> None:
    wire.serve(*[httpx.Response(503, json={"error": "busy"}) for _ in range(5)])
    with pytest.raises(LLMError) as exc:
        complete("groq/x", "s", "u", None, PARAMS)
    assert exc.value.status == 503
    assert len(wire.requests) == 3


def test_anthropic_overload_529_is_transient(wire: Wire) -> None:
    wire.serve(
        httpx.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        ),
        anthropic_reply(text_block("TRUE")),
    )
    assert complete("anthropic/claude-sonnet-5", "s", "u", None, PARAMS) == "TRUE"
    assert len(wire.requests) == 2


def test_authentication_errors_are_not_retried(wire: Wire) -> None:
    wire.serve(httpx.Response(401, json={"error": {"message": "Invalid API Key"}}), chat_reply("x"))
    with pytest.raises(LLMError) as exc:
        complete("groq/x", "s", "u", None, PARAMS)
    assert exc.value.status == 401
    assert len(wire.requests) == 1


def test_a_lost_connection_is_retried_then_reported() -> None:
    class APIConnectionError(Exception):  # the name the SDKs give it
        pass

    calls = []

    def lost() -> Any:
        calls.append(1)
        raise APIConnectionError("Connection error.")

    spec = _backend._PROVIDERS["groq"]
    with pytest.raises(LLMError, match="Failed to perform HTTP request") as exc:
        _backend._send(spec, lost)
    assert exc.value.transport
    assert len(calls) == 3


def test_a_timeout_is_marked_as_one() -> None:
    class APITimeoutError(Exception):
        pass

    err = _backend._translate(APITimeoutError("Request timed out."))
    assert isinstance(err, LLMError) and err.timeout and err.transport


def test_each_request_is_bounded_by_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def create(**kwargs: Any) -> Any:
        seen.append(kwargs["timeout"])
        msg = SimpleNamespace(content="TRUE")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], error=None)

    stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(_backend, "_openai_client", lambda spec: stub)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    old = L.llm_timeout()
    try:
        L.llm_timeout(42)
        assert complete("groq/x", "s", "u", None, PARAMS) == "TRUE"
    finally:
        L.llm_timeout(old)
    assert seen == [42.0]


def test_the_sdks_retry_nothing_themselves(wire: Wire) -> None:
    # max_retries=0: the 3 tries are ours (one place), not 3 x the SDK's own
    wire.serve(*[httpx.Response(500, json={"error": "x"}) for _ in range(9)])
    with pytest.raises(LLMError):
        complete("groq/x", "s", "u", None, PARAMS)
    assert len(wire.requests) == 1  # a 500 is not transient


# ---------------------------------------------------------------------------
# provider names, keys, the optional extra
# ---------------------------------------------------------------------------


def test_a_missing_key_is_named(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(LLMError, match="Can't find env var `GROQ_API_KEY`"):
        complete("groq/x", "s", "u", None, PARAMS)
    assert not wire.requests


def test_local_servers_need_no_key(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VLLM_API_KEY")
    wire.serve(chat_reply("TRUE"))
    with local_options({"metacheck.llm.vllm.base_url": "http://gpu.test:8000/v1"}):
        assert complete("vllm/some-model", "s", "u", None, PARAMS) == "TRUE"
    assert wire.url == "http://gpu.test:8000/v1/chat/completions"


@pytest.mark.parametrize(
    ("model", "message"),
    [
        ("aws_bedrock/x", "not available"),
        ("snowflake/x", "not available"),
        ("github/x", "retired"),
        ("nonsense/x", "Unknown LLM provider `nonsense`"),
        ("portkey", "Must specify `model`"),
    ],
)
def test_providers_that_are_not_offered(model: str, message: str, wire: Wire) -> None:
    with pytest.raises(LLMError, match=message):
        complete(model, "s", "u", None, PARAMS)
    assert not wire.requests


@pytest.mark.parametrize(
    ("module", "model"),
    [
        ("openai", "groq/x"),
        ("openai", "openai/gpt-5.6-terra"),
        ("anthropic", "anthropic/claude-sonnet-5"),
        ("google.genai", "google_gemini/gemini-3.7-flash"),
    ],
)
def test_a_missing_extra_is_reported_when_a_model_is_called(
    module: str, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in KEYS:
        monkeypatch.setenv(name, "k")
    monkeypatch.setitem(sys.modules, module, None)  # `import <module>` now fails
    _backend._clients.clear()
    with pytest.raises(LLMError, match=r"metacheck\[llm\]"):
        complete(model, "s", "u", None, PARAMS)


def test_importing_metacheck_imports_no_sdk() -> None:
    import subprocess

    code = (
        "import sys, metacheck, metacheck.llm, metacheck.llm._backend\n"
        "bad = [m for m in ('openai', 'anthropic', 'google.genai', 'pydantic') if m in sys.modules]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_whole_number_params_leave_as_integers(wire: Wire) -> None:
    wire.serve(chat_reply("x"))
    complete("groq/x", "s", "u", None, {"temperature": 0.0, "max_tokens": 4096.0, "seed": 7.0})
    body = wire.body
    assert body["max_completion_tokens"] == 4096 and isinstance(body["max_completion_tokens"], int)
    assert isinstance(body["seed"], int)


def test_malformed_structured_json_is_an_error_that_llm_retries(wire: Wire) -> None:
    wire.serve(chat_reply("```json {"))
    with pytest.raises(LLMError, match="Failed to parse JSON") as exc:
        complete("groq/x", "s", "u", ITEMS, PARAMS)
    assert core._llm_json_retryable(exc.value)


def test_llm_end_to_end_over_the_wire(
    wire: Wire, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path))
    wire.serve(chat_reply(json.dumps(ITEMS_REPLY)))
    with local_options({"metacheck.llm.use": True, "metacheck.llm.cache": False}):
        out = L.llm("two vars", "Extract", type=ITEMS, model="groq/openai/gpt-oss-20b")
    assert out["items.name"].tolist() == ["dv", "iv"]
    assert out["items.tag"].isna().tolist() == [False, True]
    body = wire.body
    assert body["temperature"] == 0.0 and body["max_completion_tokens"] == 4096
    assert body["reasoning_effort"] == "low"  # llm_reasoning() unset: low for gpt-oss
    assert out.attrs["llm"]["model"] == "groq/openai/gpt-oss-20b"
