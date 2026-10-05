"""The model back ends of :func:`metacheck.llm.llm`: the official provider SDKs.

``openai``, ``anthropic`` and ``google-genai`` (the ``metacheck[llm]`` extra)
are imported when a model is first called, never earlier. :func:`complete`
sends one system prompt and one text and returns the reply: a string, or, when
a type is given, the parsed JSON of a structured reply. Four request families
cover every provider:

* ``chat`` -- OpenAI chat completions: Groq, DeepSeek, Mistral, OpenRouter,
  Hugging Face, Perplexity, Portkey, Cloudflare, Azure OpenAI and the local
  servers (Ollama and LM Studio on their ``/v1``, vLLM), through ``openai``;
* ``responses`` -- OpenAI's Responses API (``openai/...``), through ``openai``;
* ``anthropic`` -- Anthropic Messages (``anthropic/...``, ``claude/...``);
* ``gemini`` -- Google ``generateContent`` (``google_gemini/...``).

The SDKs retry nothing (``max_retries=0``); :func:`_send` retries rate limits,
overloads and connection failures once, on the primitives of
:mod:`metacheck.http` (``Retry-After``, the per-host reset times, the
interrupt check, ``skip_on_api_limit``). Errors leave as :class:`LLMError`.
"""

from __future__ import annotations

import http as _http
import json
import os
import textwrap
import threading
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["LLMError", "complete"]

_MAX_TRIES = 3


class LLMError(RuntimeError):
    """An error from a model request.

    *status* is the HTTP status of a failed request, *detail* the provider's
    own reason (the text :func:`metacheck.llm.core.llm` shows after
    ``Provider says:``), *headers* the response headers, *timeout* a request
    that ran out of time and *transport* one that never got an answer.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: bool = False,
        transport: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.headers = headers
        self.timeout = timeout
        self.transport = transport


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

# the request names each family knows for the standard parameters (ellmer's
# names: what ``llm(params = list(...))`` takes); a parameter missing from a
# provider's table is dropped with a warning
_CHAT = {
    "temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
    "frequency_penalty": "frequency_penalty", "presence_penalty": "presence_penalty",
    "seed": "seed", "max_tokens": "max_completion_tokens", "log_probs": "logprobs",
    "stop_sequences": "stop",
}  # fmt: skip
_ANTHROPIC = {
    "temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "max_tokens": "max_tokens",
    "stop_sequences": "stop_sequences", "reasoning_tokens": "budget_tokens",
    "reasoning_effort": "reasoning_effort",
}  # fmt: skip
_GEMINI = {
    "temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
    "frequency_penalty": "frequency_penalty", "presence_penalty": "presence_penalty",
    "seed": "seed", "max_tokens": "max_output_tokens", "log_probs": "response_logprobs",
    "stop_sequences": "stop_sequences", "reasoning_tokens": "thinking_budget",
    "reasoning_effort": "thinking_level",
}  # fmt: skip
_RESPONSES = {  # no seed, stop or presence penalty
    "temperature": "temperature", "top_p": "top_p", "max_tokens": "max_output_tokens",
    "reasoning_effort": "reasoning_effort",
}  # fmt: skip


def _chat_params(drop: tuple[str, ...] = (), **names: str) -> dict[str, str]:
    """The chat-completions table minus *drop*, with *names* as request names (added if new)."""
    return {k: v for k, v in _CHAT.items() if k not in drop} | names


@dataclass(frozen=True)
class _Provider:
    name: str
    family: str  # chat | responses | anthropic | gemini
    host: str  # the host rate limits are kept for
    params: Mapping[str, str]
    key_env: str
    base_url: str | None = None
    default_model: str | None = None
    schema: str = "openai"  # the schema dialect: openai | ollama | gemini | generic
    key_required: bool = True
    transient: tuple[int, ...] = (429, 503)


_MAX = "max_tokens"
# fmt: off
_PROVIDERS: dict[str, _Provider] = {p.name: p for p in (
    _Provider("groq", "chat", "api.groq.com", _chat_params(), "GROQ_API_KEY",
              "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    _Provider("openai", "responses", "api.openai.com", _RESPONSES, "OPENAI_API_KEY",
              None, "gpt-5.6-terra"),
    _Provider("deepseek", "chat", "api.deepseek.com",
              _chat_params(("seed", "top_k"), max_tokens=_MAX), "DEEPSEEK_API_KEY",
              "https://api.deepseek.com", "deepseek-v4-flash"),
    _Provider("mistral", "chat", "api.mistral.ai",
              _chat_params(("top_k",), seed="random_seed", max_tokens=_MAX), "MISTRAL_API_KEY",
              "https://api.mistral.ai/v1", "mistral-large-latest"),
    _Provider("openrouter", "chat", "openrouter.ai", _chat_params(max_tokens=_MAX),
              "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "gpt-5.6-terra"),
    _Provider("huggingface", "chat", "router.huggingface.co", _chat_params(),
              "HUGGINGFACE_API_KEY", "https://router.huggingface.co/v1/",
              "Qwen/Qwen3-235B-A22B-Instruct-2507"),
    _Provider("perplexity", "chat", "api.perplexity.ai",
              _chat_params(("seed", "log_probs", "stop_sequences"), max_tokens=_MAX),
              "PERPLEXITY_API_KEY", "https://api.perplexity.ai/", "sonar"),
    _Provider("portkey", "chat", "api.portkey.ai", _chat_params(), "PORTKEY_API_KEY",
              "https://api.portkey.ai/v1"),
    _Provider("cloudflare", "chat", "api.cloudflare.com", _chat_params(), "CLOUDFLARE_API_KEY",
              None, "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    _Provider("azure_openai", "chat", "", _chat_params(), "AZURE_OPENAI_API_KEY"),
    _Provider("vllm", "chat", "", _chat_params(), "VLLM_API_KEY", key_required=False),
    _Provider("lmstudio", "chat", "localhost", _chat_params(("log_probs",), max_tokens=_MAX),
              "LMSTUDIO_API_KEY", key_required=False),
    _Provider("ollama", "chat", "localhost",
              _chat_params(("log_probs",), max_tokens=_MAX, reasoning_effort="reasoning_effort"),
              "OLLAMA_API_KEY", schema="ollama", key_required=False),
    _Provider("anthropic", "anthropic", "api.anthropic.com", _ANTHROPIC, "ANTHROPIC_API_KEY",
              None, "claude-sonnet-5", "generic", transient=(429, 503, 529)),
    _Provider("google_gemini", "gemini", "generativelanguage.googleapis.com", _GEMINI,
              "GEMINI_API_KEY", None, "gemini-3.7-flash", "gemini"),
)}
# fmt: on
_PROVIDERS["claude"] = _PROVIDERS["anthropic"]

#: providers of ellmer that need cloud SDK credentials; not offered
_UNSUPPORTED = ("aws_bedrock", "databricks", "google_vertex", "posit", "snowflake")


def _resolve(model: str) -> tuple[_Provider, str]:
    """The provider and the model name in ``"provider/model"``."""
    from metacheck.llm.core import _message

    provider, _, name = model.partition("/")
    if provider in _UNSUPPORTED:
        raise LLMError(
            f"The `{provider}` provider is not available; use an OpenAI-compatible, "
            "Gemini or Anthropic model."
        )
    if provider == "github":
        raise LLMError("GitHub Models was retired; the `github` provider is gone.")
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise LLMError(f"Unknown LLM provider `{provider}`. Name a model as 'provider/model'.")
    if not name:
        if spec.default_model is None:
            raise LLMError(f"Must specify `model`: name it as '{provider}/<model>'.")
        name = spec.default_model
        _message(f'Using model = "{name}".')
    return spec, name


def _key(env: str | None, required: bool = True) -> str:
    """The API key in *env* (Gemini: ``GOOGLE_API_KEY`` too)."""
    value = os.environ.get(env, "") if env else ""
    if env == "GEMINI_API_KEY":
        value = os.environ.get("GOOGLE_API_KEY", "") or value
    if not value and required:
        raise LLMError(f"Can't find env var `{env}`.")
    return value


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------


def _wire(name: str, value: Any) -> Any:
    """Whole-number parameters leave as integers (``4096.0`` is not a token count)."""
    if name in ("max_tokens", "seed", "top_k", "reasoning_tokens") and isinstance(value, float):
        return int(value) if value.is_integer() else value
    return value


def _quoted_and(items: list[str]) -> str:
    quoted = [f'"{i}"' for i in items]
    if len(quoted) <= 2:
        return " and ".join(quoted)
    return ", ".join(quoted[:-1]) + ", and " + quoted[-1]


_STANDARD = (*_CHAT, "reasoning_tokens", "reasoning_effort")


def _split_params(
    spec: _Provider, params: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The request names of the standard parameters, and the rest (sent as given)."""
    std: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    unsupported: list[str] = []
    for k, v in params.items():
        if v is None or (isinstance(v, list | tuple | dict) and not v):
            continue
        if k not in _STANDARD:
            extra[k] = v
        elif k in spec.params:
            std[spec.params[k]] = _wire(k, list(v) if isinstance(v, tuple) else v)
        else:
            unsupported.append(k)
    if unsupported:
        text = f"Ignoring unsupported parameters: {_quoted_and(unsupported)}"
        warnings.warn(textwrap.fill(text, 80, break_long_words=False), stacklevel=4)
    return std, extra


# ---------------------------------------------------------------------------
# errors and retries
# ---------------------------------------------------------------------------


def _phrase(status: int) -> str:
    try:
        return _http.HTTPStatus(status).phrase
    except ValueError:
        return "Unknown"


def _body_detail(text: str) -> tuple[str | None, str | None]:
    """The provider's reason in an error body, and its error type when it names one.

    The reason is ``error`` (a string), ``error.message`` or ``message``; a body that is
    not JSON is the reason as it stands.
    """
    try:
        body = json.loads(text)
    except ValueError:
        return text or None, None
    if not isinstance(body, dict):
        return text or None, None
    err = body.get("error")
    kind = err.get("type") if isinstance(err, dict) and body.get("type") == "error" else None
    detail = err if isinstance(err, str) else err.get("message") if isinstance(err, dict) else None
    if detail is None:
        detail = body.get("message")
    if detail is None:
        detail = text
    return (detail if isinstance(detail, str) and detail else None), kind


def _bullet(text: str) -> str:
    """An ``i`` bullet as the console shows it: whitespace collapsed, wrapped at 80 columns."""
    return textwrap.fill(
        " ".join(text.split()), 80, initial_indent="ℹ ", subsequent_indent="  ",
        break_long_words=False, break_on_hyphens=False,
    )  # fmt: skip


def _translate(exc: Exception) -> Exception:
    """An SDK's API error as :class:`LLMError`; any other error as it is."""
    if isinstance(exc, LLMError):
        return exc
    names = {c.__name__ for c in type(exc).__mro__}
    resp = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if isinstance(status, int) and status >= 400:
        try:
            text = resp.text if resp is not None else ""
        except Exception:
            text = ""
        if not text:
            details = getattr(exc, "details", None)
            text = json.dumps(details) if details is not None else ""
        detail, kind = _body_detail(text) if text else (None, None)
        msg = f"HTTP {status} {_phrase(status)}."
        if detail:  # Anthropic names the error type after its message
            msg += "\n" + _bullet(f"{detail} [{kind}]" if kind else detail)
        return LLMError(
            msg, status=status, detail=detail, headers=getattr(resp, "headers", None) or {}
        )
    if "APITimeoutError" in names or any("Timeout" in n for n in names):
        return LLMError(
            f"Failed to perform HTTP request.\n! Timeout was reached: {exc}",
            timeout=True,
            transport=True,
        )
    if "APIConnectionError" in names or any(n in names for n in ("TransportError", "ConnectError")):
        cause = exc.__cause__ or exc
        return LLMError(f"Failed to perform HTTP request.\n! {cause}", transport=True)
    return exc


def _send(spec: _Provider, call: Callable[[], Any]) -> Any:
    """Run one SDK request, retrying rate limits, overloads and lost connections."""
    import time

    from metacheck import http

    for attempt in range(1, _MAX_TRIES + 1):
        http.check_interrupt()
        reset = http.host_reset_at(spec.host) if spec.host else None
        if reset is not None:
            if http.skipping_api_limits():
                raise LLMError("HTTP 429 Too Many Requests.", status=429)
            http.sleep(reset - time.time())
        try:
            return call()
        except Exception as exc:
            err = _translate(exc)
            if not isinstance(err, LLMError):
                raise
            retry = err.transport or err.status in spec.transient
            if not retry or attempt == _MAX_TRIES:
                raise err from exc
            wait: float | None = None
            if err.status == 429 and err.headers is not None:
                from types import SimpleNamespace

                until = http._parse_reset(SimpleNamespace(headers=err.headers))  # type: ignore[arg-type]
                if until is not None:
                    if spec.host:
                        http._record_reset(spec.host, until)
                    wait = max(0.0, until - time.time())
                if http.skipping_api_limits():
                    raise err from exc
            http.sleep(wait if wait is not None else http._backoff(attempt))
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------

_clients: dict[tuple[Any, ...], Any] = {}
_clients_lock = threading.Lock()


def _client(key: tuple[Any, ...], make: Callable[[], Any]) -> Any:
    with _clients_lock:
        if key not in _clients:
            _clients[key] = make()
        return _clients[key]


def _missing_extra(exc: ImportError) -> LLMError:
    return LLMError(
        "The model SDKs are not installed. Install them with "
        '`pip install "metacheck[llm]"` (or `uv pip install "metacheck[llm]"`). '
        f"({exc})"
    )


def _openai_client(spec: _Provider) -> Any:
    from metacheck.utils import get_option

    key = _key(spec.key_env, spec.key_required)
    base = spec.base_url
    headers: dict[str, str] = {}
    azure = spec.name == "azure_openai"
    if spec.name == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/v1"
    elif spec.name == "lmstudio":
        base = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234").rstrip("/") + "/v1"
    elif spec.name == "vllm":
        base = get_option("metacheck.llm.vllm.base_url")
        if not base:
            raise LLMError(
                "Set options(metacheck.llm.vllm.base_url = '<vllm-endpoint>/v1') to use vllm models"
            )
    elif spec.name == "cloudflare":
        account = _key("CLOUDFLARE_ACCOUNT_ID")
        base = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/"
    elif spec.name == "portkey":
        headers["x-portkey-api-key"] = key
        key = "portkey"
    elif azure:
        base = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if not base:
            raise LLMError("Can't find env var `AZURE_OPENAI_ENDPOINT`.")

    def make() -> Any:
        try:
            import openai
        except ImportError as exc:
            raise _missing_extra(exc) from exc
        if azure:
            return openai.AzureOpenAI(
                api_key=key, azure_endpoint=str(base), api_version="2024-10-21", max_retries=0
            )
        return openai.OpenAI(
            api_key=key or "none", base_url=base, default_headers=headers or None, max_retries=0
        )

    return _client(("openai", spec.name, base, key, tuple(headers.items())), make)


def _anthropic_client() -> Any:
    key = _key("ANTHROPIC_API_KEY")
    base = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if base.endswith("/v1"):  # the SDK adds the version itself
        base = base[:-3]

    def make() -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise _missing_extra(exc) from exc
        return anthropic.Anthropic(api_key=key, base_url=base or None, max_retries=0)

    return _client(("anthropic", base, key), make)


def _gemini_client() -> Any:
    key = _key("GEMINI_API_KEY")

    def make() -> Any:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise _missing_extra(exc) from exc
        options = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
        return genai.Client(api_key=key, vertexai=False, http_options=options)

    return _client(("gemini", key), make)


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

_MISTRAL_THROTTLE: Any = None


def _throttle(spec: _Provider) -> None:
    """Mistral allows one request a second."""
    global _MISTRAL_THROTTLE
    if spec.name != "mistral":
        return
    from metacheck.http import Throttle

    if _MISTRAL_THROTTLE is None:
        _MISTRAL_THROTTLE = Throttle(capacity=1, fill_time_s=1)
    _MISTRAL_THROTTLE.acquire(spec.host)


def _json(text: str | None) -> Any:
    """The parsed JSON of a reply; an empty or malformed one is an :class:`LLMError` that ``llm()`` retries."""
    if text is None:
        raise LLMError("Data extraction failed: no JSON responses found.")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LLMError(f"Failed to parse JSON: {exc}\n  {text[:120]!r}") from None


def _wrapped(type: Any) -> tuple[Any, bool]:
    """Types that are not objects travel inside ``{"wrapper": ...}`` (strict schemas need an object)."""
    from metacheck.llm.types import type_object

    if type.get("type") == "object":
        return type, False
    return type_object(wrapper=type), True


def _unwrap(data: Any, wrapped: bool) -> Any:
    return (data.get("wrapper") if isinstance(data, dict) else None) if wrapped else data


def _chat(
    spec: _Provider, model: str, system: str, user: str, type: Any,
    std: dict[str, Any], extra: dict[str, Any],
) -> Any:  # fmt: skip
    from metacheck.llm.core import llm_timeout
    from metacheck.llm.types import type_as_json

    client = _openai_client(spec)
    typed = {"temperature", "top_p", "seed", "stop", "frequency_penalty", "presence_penalty",
             "logprobs", "max_tokens", "max_completion_tokens"}  # fmt: skip
    kwargs: dict[str, Any] = {k: v for k, v in std.items() if k in typed}
    body = {k: v for k, v in std.items() if k not in typed} | extra
    wrapped = False
    if type is not None:
        t, wrapped = _wrapped(type)
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_data",
                "schema": type_as_json(t, spec.schema),
                "strict": True,
            },
        }
    messages = [{"role": "user", "content": user}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    _throttle(spec)
    r = _send(
        spec,
        lambda: client.chat.completions.create(
            model=model, messages=messages, extra_body=body or None,
            timeout=float(llm_timeout()), **kwargs,
        ),
    )  # fmt: skip
    error = getattr(r, "error", None)  # OpenRouter reports some failures inside a 200
    if error:
        raise LLMError(str(error.get("message") if isinstance(error, dict) else error))
    if not r.choices:
        raise LLMError("The reply has no choices.")
    content = r.choices[0].message.content
    if isinstance(content, list):  # Mistral: a list of chunks
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    if type is None:
        return content or ""
    return _unwrap(_json(content), wrapped)


def _responses(
    spec: _Provider, model: str, system: str, user: str, type: Any,
    std: dict[str, Any], extra: dict[str, Any],
) -> Any:  # fmt: skip
    from metacheck.llm.core import llm_timeout
    from metacheck.llm.types import type_as_json

    client = _openai_client(spec)
    kwargs: dict[str, Any] = {k: v for k, v in std.items() if k != "reasoning_effort"}
    if "reasoning_effort" in std:
        kwargs["reasoning"] = {"effort": std["reasoning_effort"]}
    wrapped = False
    if type is not None:
        t, wrapped = _wrapped(type)
        kwargs["text"] = {
            "format": {
                "type": "json_schema",
                "name": "structured_data",
                "schema": type_as_json(t, "openai"),
                "strict": True,
            }
        }
    if system:
        kwargs["instructions"] = system
    r = _send(
        spec,
        lambda: client.responses.create(
            model=model, input=user, store=False, extra_body=extra or None,
            timeout=float(llm_timeout()), **kwargs,
        ),
    )  # fmt: skip
    text = r.output_text
    if type is None:
        return text or ""
    return _unwrap(_json(text or None), wrapped)


_ANTHROPIC_STRUCTURED = "^claude-[a-z]+-(4-[5-9]|[5-9]|[1-9]\\d)(-|$)"


def _anthropic(
    spec: _Provider, model: str, system: str, user: str, type: Any,
    std: dict[str, Any], extra: dict[str, Any],
) -> Any:  # fmt: skip
    import re

    from metacheck.llm.core import llm_timeout
    from metacheck.llm.types import type_as_json, type_has_additional_properties, type_object

    client = _anthropic_client()
    body = dict(extra)
    kwargs: dict[str, Any] = {"max_tokens": int(std.pop("max_tokens", None) or 4096)}
    for k in ("temperature", "top_p", "top_k"):  # not parameters of the typed call
        if k in std:
            body[k] = std.pop(k)
    effort = std.pop("reasoning_effort", None)
    budget = std.pop("budget_tokens", None)
    kwargs.update(std)
    output_config: dict[str, Any] = {}
    tool = False
    if type is not None:
        native = re.match(_ANTHROPIC_STRUCTURED, model) is not None
        if native and not type_has_additional_properties(type):
            output_config["format"] = {
                "type": "json_schema",
                "schema": type_as_json(type, "generic"),
            }
        else:
            tool = True
            schema = type_as_json(type_object(data=type), "generic")
            kwargs["tools"] = [
                {
                    "name": "_structured_tool_call",
                    "description": "Extract structured data",
                    "input_schema": {k: v for k, v in schema.items() if v not in ([], {}, None)},
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "_structured_tool_call"}
    if effort is not None:
        kwargs["thinking"] = {"type": "adaptive"}
        output_config["effort"] = effort
    elif budget is not None:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if output_config:
        kwargs["output_config"] = output_config
    if system:
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "5m"}}
        ]
    text = user if user.strip() else "[empty string]"
    r = _send(
        spec,
        lambda: client.messages.create(
            model=model, messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
            extra_body=body or None, timeout=float(llm_timeout()), **kwargs,
        ),
    )  # fmt: skip
    blocks = r.content or []
    if tool:
        for b in blocks:
            if b.type == "tool_use":
                inp = b.input
                return _json(inp) if isinstance(inp, str) else (inp or {}).get("data")
        raise LLMError("Data extraction failed: no JSON responses found.")
    answer = "".join(b.text for b in blocks if b.type == "text")
    return answer if type is None else _json(answer or None)


def _gemini(
    spec: _Provider, model: str, system: str, user: str, type: Any,
    std: dict[str, Any], extra: dict[str, Any],
) -> Any:  # fmt: skip
    from metacheck.llm.core import llm_timeout
    from metacheck.llm.types import type_as_json

    client = _gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - the client above imported it
        raise _missing_extra(exc) from exc
    cfg: dict[str, Any] = {k: v for k, v in std.items() if not k.startswith("thinking_")}
    cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    thinking = {k: v for k, v in std.items() if k.startswith("thinking_")}
    if thinking:
        cfg["thinking_config"] = types.ThinkingConfig(**thinking)
    if system:
        cfg["system_instruction"] = system
    if type is not None:
        cfg["response_mime_type"] = "application/json"
        # The schema goes in `responseSchema`, as ellmer sends it, through the body the
        # SDK merges in: the typed `response_schema` refuses a null among the values of
        # an enum, and `responseJsonSchema` changed what `power` returned (an extra
        # all-null row; 11 to 13 values differed on 14 paragraphs, against 3 for the
        # model's own run-to-run noise).
        extra = {
            **(extra or {}),
            "generationConfig": {"responseSchema": type_as_json(type, "gemini")},
        }
    cfg["http_options"] = types.HttpOptions(
        timeout=int(float(llm_timeout()) * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
        extra_body=extra or None,
    )
    r = _send(
        spec,
        lambda: client.models.generate_content(
            model=model, contents=user, config=types.GenerateContentConfig(**cfg)
        ),
    )
    if not r.candidates:
        raise LLMError("The reply has no candidates.")
    text = r.text
    return (text or "") if type is None else _json(text)


_FAMILIES = {"chat": _chat, "responses": _responses, "anthropic": _anthropic, "gemini": _gemini}


def complete(
    model: str,
    system: str,
    user: str,
    type: Any = None,
    params: Mapping[str, Any] | None = None,
    api_args: Mapping[str, Any] | None = None,
) -> Any:
    """Ask *model* (``"provider/model"``) one question.

    Returns the reply text, or, when *type* (a structured-output type) is
    given, the parsed JSON that matches it (before conversion to columns).
    *params* are ellmer's standard parameter names (``temperature``,
    ``max_tokens``, ``seed``, ``top_p``, ``top_k``, ``stop_sequences``,
    ``reasoning_effort``, ...; others go to the request as given), *api_args*
    extra request fields.
    """
    spec, name = _resolve(model)
    std, extra = _split_params(spec, params or {})
    extra.update(api_args or {})
    return _FAMILIES[spec.family](spec, name, system, user, type, std, extra)
