"""LLM chat providers over :mod:`pytacheck.http` (port of the parts of ellmer metacheck uses).

metacheck's ``llm()`` builds a fresh ``ellmer::chat("provider/model")`` per
request. pytacheck has no SDK dependency: each provider family is a small
request builder / response parser that reproduces ellmer's request bodies
byte for byte (so recorded fixtures replay on both sides) and its parsing of
replies into turns and structured data:

* OpenAI-compatible chat completions — Groq, DeepSeek, Mistral, OpenRouter,
  Ollama (``/v1``), vLLM, LM Studio, Hugging Face, Perplexity, Portkey,
  Cloudflare, Azure OpenAI;
* the OpenAI Responses API (``openai``);
* Google Gemini ``generateContent`` (``google_gemini``);
* Anthropic Messages (``anthropic`` / ``claude``).

API keys come from the same environment variables as ellmer
(``GROQ_API_KEY``, ``OPENAI_API_KEY``, ``GOOGLE_API_KEY`` then
``GEMINI_API_KEY``, ``ANTHROPIC_API_KEY``, ``MISTRAL_API_KEY``,
``DEEPSEEK_API_KEY``, ``OPENROUTER_API_KEY``, ``HUGGINGFACE_API_KEY``,
``PERPLEXITY_API_KEY``, ``PORTKEY_API_KEY``, ``VLLM_API_KEY``,
``OLLAMA_API_KEY``, ...); base URLs honour ``OLLAMA_BASE_URL``,
``LMSTUDIO_BASE_URL`` and ``ANTHROPIC_BASE_URL``.
"""

from __future__ import annotations

import http as _http
import math
import os
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pytacheck.llm._json import JSONParseError, Vec, parse_json, to_json
from pytacheck.llm._rds import RInt
from pytacheck.llm.types import (
    Type,
    TypeJsonSchema,
    TypeObject,
    as_type,
    convert_from_type,
    type_as_json,
    type_has_additional_properties,
    type_object,
)

if TYPE_CHECKING:
    import httpx

__all__ = [
    "Chat",
    "LLMError",
    "Model",
    "chat",
    "params",
]

ELLMER_VERSION = "0.5.0"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """An error from an LLM request.

    Mirrors the R conditions metacheck inspects: ``resp`` is the HTTP
    response of a failed request (httr2's ``e$resp``), ``parent`` a wrapped
    condition, and ``timeout`` marks a per-request timeout (curl's
    ``curl_error_operation_timedout`` class).
    """

    def __init__(
        self,
        message: str,
        resp: httpx.Response | None = None,
        parent: BaseException | None = None,
        timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.resp = resp
        self.parent = parent
        self.timeout = timeout


# ---------------------------------------------------------------------------
# ellmer::params()
# ---------------------------------------------------------------------------

_PARAM_ORDER = (
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "max_tokens",
    "log_probs",
    "stop_sequences",
    "reasoning_effort",
    "reasoning_tokens",
)


def _is_number(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _friendly(x: Any) -> str:
    """``rlang::obj_type_friendly()`` for the values params() sees."""
    from pytacheck._r import format_num

    if x is None:
        return "NULL"
    if isinstance(x, bool):
        return "`TRUE`" if x else "`FALSE`"
    if isinstance(x, int | float):
        if isinstance(x, float) and math.isnan(x):
            return "`NA`"
        if isinstance(x, float) and math.isinf(x):
            return "`Inf`" if x > 0 else "`-Inf`"
        return f"the number {x if isinstance(x, int) else format_num(x, 7)}"
    if isinstance(x, str):
        return f'the string "{x}"'
    if isinstance(x, Mapping):
        return "a list"
    if isinstance(x, list | tuple):
        if not x:
            return "an empty list"
        if all(isinstance(v, str) for v in x):
            return "a character vector"
        if all(_is_number(v) for v in x):
            return "a double vector"
        if all(isinstance(v, bool) for v in x):
            return "a logical vector"
        return "a list"
    return f"a <{type(x).__name__}> object"


def _check_number(name: str, x: Any, whole: bool, min_: float | None) -> None:
    what = "a whole number" if whole else "a number"
    ok = _is_number(x) and not (isinstance(x, float) and math.isnan(x))
    if ok and whole and isinstance(x, float) and (math.isinf(x) or x != int(x)):
        ok = False
    if not ok:
        raise ValueError(f"`{name}` must be {what} or `NULL`, not {_friendly(x)}.")
    if min_ is not None and x < min_:
        bound = int(min_) if float(min_).is_integer() else min_
        raise ValueError(
            f"`{name}` must be {what} larger than or equal to {bound} or `NULL`, "
            f"not {_friendly(x)}."
        )


def params(**kwargs: Any) -> dict[str, Any]:
    """Port of ``ellmer::params()``: validated, standardised model parameters.

    Returns the parameters in ellmer's order with ``None`` dropped; unknown
    names are collected under ``extra_args`` (sent to the provider verbatim).
    """
    std = {k: kwargs.pop(k, None) for k in _PARAM_ORDER}
    for name in ("temperature", "top_p"):
        if std[name] is not None:
            _check_number(name, std[name], False, 0)
    if std["top_k"] is not None:
        _check_number("top_k", std["top_k"], True, 0)
    for name in ("frequency_penalty", "presence_penalty"):
        if std[name] is not None:
            _check_number(name, std[name], False, None)
    if std["seed"] is not None:
        _check_number("seed", std["seed"], True, None)
    if std["max_tokens"] is not None:
        _check_number("max_tokens", std["max_tokens"], True, 1)
    lp = std["log_probs"]
    if lp is not None and not isinstance(lp, bool):
        raise ValueError(f"`log_probs` must be `TRUE`, `FALSE`, or `NULL`, not {_friendly(lp)}.")
    ss = std["stop_sequences"]
    if ss is not None:
        if isinstance(ss, str):
            std["stop_sequences"] = [ss]
        elif not (isinstance(ss, list | tuple) and all(isinstance(s, str) for s in ss)):
            raise ValueError(
                f"`stop_sequences` must be a character vector or `NULL`, not {_friendly(ss)}."
            )
        else:
            std["stop_sequences"] = list(ss)
    re_ = std["reasoning_effort"]
    if re_ is not None and not isinstance(re_, str):
        raise ValueError(
            f"`reasoning_effort` must be a single string or `NULL`, not {_friendly(re_)}."
        )
    if std["reasoning_tokens"] is not None:
        _check_number("reasoning_tokens", std["reasoning_tokens"], True, 0)
    out: dict[str, Any] = {k: v for k, v in std.items() if not _empty(v)}
    if kwargs:
        out["extra_args"] = dict(kwargs)
    return out


def _empty(v: Any) -> bool:
    """``length(v) == 0`` (what ellmer's ``compact()`` drops)."""
    if v is None:
        return True
    if isinstance(v, list | tuple | dict):
        return len(v) == 0
    return False


def standardise_params(p: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    """Port of ``ellmer:::standardise_params()`` (provider name -> ellmer name map)."""
    standard = {k: v for k, v in p.items() if k != "extra_args"}
    known = set(mapping.values())
    unknown = [k for k in standard if k not in known]
    if unknown:
        quoted = ", ".join(f'"{u}"' for u in unknown)
        quoted = _cli_and(quoted.split(", "))
        warnings.warn(f"Ignoring unsupported parameters: {quoted}", stacklevel=3)
        standard = {k: v for k, v in standard.items() if k in known}
    inverse = {v: k for k, v in mapping.items()}
    out = {inverse[k]: v for k, v in standard.items()}
    out.update(p.get("extra_args") or {})
    return out


def _cli_and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _r_vec(v: Any) -> Any:
    """Parameter values: character vectors unbox when length 1 (jsonlite)."""
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return Vec(v)
    if isinstance(v, RInt):
        return int(v)
    return v


# ---------------------------------------------------------------------------
# turns and contents
# ---------------------------------------------------------------------------


class ContentText:
    def __init__(self, text: str | None) -> None:
        self.text = text


class ContentThinking:
    def __init__(self, thinking: str, extra: Any = None) -> None:
        self.thinking = thinking
        self.extra = extra


class ContentJson:
    """Structured output; ``parsed`` parses ``string`` lazily (ellmer's getter)."""

    def __init__(self, data: Any = None, string: str | None = None) -> None:
        self.data = data
        self.string = string

    @property
    def parsed(self) -> Any:
        if self.string is not None:
            return parse_json(self.string)
        return self.data


class ContentToolRequest:
    def __init__(self, id: str | None, name: str, arguments: Any) -> None:
        self.id, self.name, self.arguments = id, name, arguments


class Turn:
    def __init__(self, role: str, contents: list[Any], json: Any = None) -> None:
        self.role = role
        self.contents = contents
        self.json = json

    @property
    def text(self) -> str:
        return "".join(
            c.text or "" for c in self.contents if isinstance(c, ContentText) and c.text is not None
        )


class Model:
    """An ellmer ``Model``: name, standardised params and extra body fields."""

    def __init__(
        self, name: str, params: Mapping[str, Any] | None = None, extra_args: Any = None
    ) -> None:
        self.name = name
        self.params = dict(params or {})
        self.extra_args = dict(extra_args or {})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _timeout_default() -> float:
    from pytacheck.utils import get_option

    return float(get_option("ellmer_timeout_s", 5 * 60))


class _Recorder:
    """Wraps the shared client to remember the last transport exception."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.exc: BaseException | None = None

    def request(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self.inner.request(*args, **kwargs)
        except Exception as e:
            self.exc = e
            raise


def _transport_error(exc: BaseException | None, url: str) -> LLMError:
    """httr2's "Failed to perform HTTP request." with curl's reason."""
    import httpx

    host = urlsplit(url).hostname or ""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    text = str(exc) if exc is not None else ""
    timeout = False
    if isinstance(exc, httpx.ConnectTimeout):
        reason = f"Connection timed out after {int(_timeout_default() * 1000)} milliseconds"
    elif isinstance(exc, httpx.TimeoutException):
        reason = f"Timeout was reached [{host}]: Operation timed out with 0 bytes received"
        timeout = True
    elif "Name or service not known" in text or "nodename nor servname" in text or (
        "getaddrinfo" in text
    ) or "Temporary failure in name resolution" in text:
        reason = f"Could not resolve host: {host}"
    elif "Connection refused" in text or "ConnectionRefused" in text:
        reason = f"Failed to connect to {host} port {port}: Connection refused"
    elif "reset" in text.lower():
        reason = f"Connection reset by peer [{host}]"
    elif "ssl" in text.lower() or "certificate" in text.lower():
        reason = f"SSL connect error [{host}]: {text}"
    else:
        reason = f"Failed to connect to {host} port {port}: {text or 'Could not connect to server'}"
    msg = (
        "Failed to perform HTTP request.\n"
        "Caused by error in `curl::curl_fetch_memory()`:\n"
        f"! {reason}"
    )
    return LLMError(msg, parent=exc, timeout=timeout)


def _status_error(resp: httpx.Response, info: str | None) -> LLMError:
    try:
        phrase = _http.HTTPStatus(resp.status_code).phrase
    except ValueError:
        phrase = "Unknown"
    msg = f"HTTP {resp.status_code} {phrase}."
    if info:
        msg += "\n• " + info
    return LLMError(msg, resp=resp)


def perform(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    timeout: float | None = None,
    max_tries: int = 3,
    transient: Sequence[int] = (429, 503),
    error_body: Callable[[httpx.Response], str | None] | None = None,
) -> httpx.Response:
    """``httr2::req_perform()`` with ellmer's robustify policy and error handling."""
    from pytacheck import http

    hdrs = dict(headers or {})
    kwargs: dict[str, Any] = {"headers": hdrs, "timeout": timeout or _timeout_default()}
    if body is not None:
        kwargs["content"] = to_json(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    rec = _Recorder(http.client())
    resp = http.request(
        method, url, max_tries=max_tries, retry_statuses=tuple(transient), http=rec, **kwargs
    )
    if resp is None:
        raise _transport_error(rec.exc, url)
    if resp.status_code >= 400:
        info = None
        if error_body is not None:
            try:
                info = error_body(resp)
            except Exception:
                info = None
        raise _status_error(resp, info)
    return resp


def _content_type(resp: httpx.Response) -> str:
    return (resp.headers.get("content-type") or "").split(";")[0].strip().lower()


def resp_body_json(resp: httpx.Response) -> Any:
    """``httr2::resp_body_json()`` (jsonlite parsing semantics)."""
    return parse_json(resp.content.decode("utf-8", "replace"))


def _openai_error_body(resp: httpx.Response) -> str | None:
    ct = _content_type(resp)
    if ct == "application/json":
        body = resp_body_json(resp)
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, str):
            return err
        if isinstance(err, dict):
            return err.get("message")
        import json

        return json.dumps(body, indent=2, ensure_ascii=False)
    if ct == "text/plain":
        return resp.text
    return None


def _url(base: str, *parts: str) -> str:
    out = base.rstrip("/")
    for p in parts:
        out += "/" + p.strip("/")
    return out


def _key_get(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    raise LLMError(f"Can't find env var `{name}`.")


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


class Provider:
    """Base provider (ellmer ``Provider``)."""

    name = "Provider"
    schema_kind = "generic"
    transient: tuple[int, ...] = (429, 503)

    def __init__(
        self,
        base_url: str,
        credentials: Callable[[], Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url
        self.credentials = credentials or (lambda: "")
        self.extra_headers = dict(extra_headers or {})

    # request ---------------------------------------------------------------
    def auth_headers(self) -> dict[str, str]:
        cred = self.credentials()
        if isinstance(cred, Mapping):
            return {str(k): str(v) for k, v in cred.items()}
        return {"Authorization": f"Bearer {cred}"} if cred else {}

    def chat_path(self) -> str:
        return "/chat/completions"

    def chat_url(self, model: Model) -> str:
        return _url(self.base_url, self.chat_path())

    def chat_body(self, model: Model, turns: list[Turn], type: Type | None) -> dict[str, Any]:
        raise NotImplementedError

    def chat_params(self, p: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def build_request(
        self, model: Model, turns: list[Turn], type: Type | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        body = self.chat_body(model, turns, type)
        body = _modify_list(body, model.extra_args)
        headers = {**self.auth_headers(), **self.extra_headers}
        return self.chat_url(model), headers, body

    def error_body(self, resp: httpx.Response) -> str | None:
        return None

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        raise NotImplementedError

    def uses_tool_structured_output(self, model: Model, type: Type | None) -> bool:
        return False

    def needs_wrapper(self, type: Type) -> bool:
        return False

    def perform(self, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        return perform(
            "POST",
            url,
            headers=headers,
            body=body,
            transient=self.transient,
            error_body=self.error_body,
        )


def _modify_list(x: dict[str, Any], y: Mapping[str, Any]) -> dict[str, Any]:
    """``utils::modifyList(x, y)``."""
    if not y:
        return x
    out = dict(x)
    for k, v in y.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = _modify_list(dict(out[k]), v)
        else:
            out[k] = v
    return out


class ProviderOpenAICompatible(Provider):
    name = "OpenAI-compatible"
    schema_kind = "openai"
    param_map: Mapping[str, str] = {
        "frequency_penalty": "frequency_penalty",
        "logprobs": "log_probs",
        "max_completion_tokens": "max_tokens",
        "presence_penalty": "presence_penalty",
        "seed": "seed",
        "stop": "stop_sequences",
        "temperature": "temperature",
        "top_k": "top_k",
        "top_p": "top_p",
    }
    stream_options = True

    def chat_params(self, p: Mapping[str, Any]) -> dict[str, Any]:
        return standardise_params(p, self.param_map)

    def turn_json(self, turn: Turn) -> list[dict[str, Any]]:
        if turn.role == "system":
            return [{"role": "system", "content": turn.contents[0].text}]
        if turn.role == "user":
            return [{"role": "user", "content": [self.content_json(c) for c in turn.contents]}]
        contents = [c for c in turn.contents if not (isinstance(c, ContentText) and not c.text)]
        if not contents:
            return []
        other = [c for c in contents if isinstance(c, ContentText)]
        out: dict[str, Any] = {"role": "assistant"}
        if other:
            out["content"] = [self.content_json(c) for c in other]
        return [out]

    def content_json(self, c: ContentText) -> Any:
        return {"type": "text", "text": c.text}

    def chat_body(self, model: Model, turns: list[Turn], type: Type | None) -> dict[str, Any]:
        messages: list[Any] = []
        for t in turns:
            messages.extend(self.turn_json(t))
        body: dict[str, Any] = {"messages": messages, "model": model.name}
        for k, v in self.chat_params(model.params).items():
            body[k] = _r_vec(v)
        body["stream"] = False
        if type is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_data",
                    "schema": type_as_json(type, self.schema_kind),
                    "strict": True,
                },
            }
        return body

    def error_body(self, resp: httpx.Response) -> str | None:
        return _openai_error_body(resp)

    def needs_wrapper(self, type: Type) -> bool:
        return not isinstance(type, TypeObject | TypeJsonSchema)

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        choice = (result.get("choices") or [{}])[0] if isinstance(result, dict) else {}
        message = choice.get("delta") if "delta" in choice else choice.get("message")
        message = message or {}
        contents: list[Any] = []
        reasoning = message.get("reasoning")
        if reasoning is None:
            reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            contents.append(ContentThinking(reasoning))
        content = message.get("content")
        if has_type:
            if isinstance(content, str):
                contents.append(ContentJson(string=content))
            else:
                contents.append(ContentJson(data=content))
        elif isinstance(content, str):
            if content:
                contents.append(ContentText(content))
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, str):
                    contents.append(ContentText(c))
                elif isinstance(c, dict) and "text" in c:
                    contents.append(ContentText(c.get("text")))
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = parse_json(fn.get("arguments") or "")
            except JSONParseError:
                args = {}
            contents.append(ContentToolRequest(call.get("id"), fn.get("name", ""), args or {}))
        return Turn("assistant", contents, result)


class ProviderGroq(ProviderOpenAICompatible):
    name = "Groq"


class ProviderDeepSeek(ProviderOpenAICompatible):
    name = "DeepSeek"
    param_map = {
        "frequency_penalty": "frequency_penalty",
        "max_tokens": "max_tokens",
        "presence_penalty": "presence_penalty",
        "stop": "stop_sequences",
        "temperature": "temperature",
        "top_p": "top_p",
        "logprobs": "log_probs",
    }

    def turn_json(self, turn: Turn) -> list[dict[str, Any]]:
        if turn.role == "user":
            return [
                {"role": "user", "content": c.text}
                for c in turn.contents
                if isinstance(c, ContentText)
            ]
        if turn.role == "assistant":
            text = next((c for c in turn.contents if isinstance(c, ContentText)), None)
            out: dict[str, Any] = {"role": "assistant"}
            if text is not None:
                out["content"] = text.text
            return [out]
        return super().turn_json(turn)


class ProviderMistral(ProviderOpenAICompatible):
    name = "Mistral"
    param_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
        "random_seed": "seed",
        "max_tokens": "max_tokens",
        "logprobs": "log_probs",
        "stop": "stop_sequences",
    }

    def error_body(self, resp: httpx.Response) -> str | None:
        if _content_type(resp) == "application/json":
            body = resp_body_json(resp)
            return body.get("message") if isinstance(body, dict) else None
        return None

    def perform(self, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        _MISTRAL_THROTTLE.acquire(urlsplit(url).hostname or "")
        return super().perform(url, headers, body)


class ProviderOllama(ProviderOpenAICompatible):
    name = "Ollama"
    schema_kind = "ollama"
    param_map = {
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
        "seed": "seed",
        "stop": "stop_sequences",
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "max_tokens": "max_tokens",
        "reasoning_effort": "reasoning_effort",
    }


class ProviderOpenRouter(ProviderOpenAICompatible):
    name = "OpenRouter"
    param_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
        "seed": "seed",
        "max_tokens": "max_tokens",
        "logprobs": "log_probs",
        "stop": "stop_sequences",
    }

    def auth_headers(self) -> dict[str, str]:
        return {
            **super().auth_headers(),
            "HTTP-Referer": "https://ellmer.tidyverse.org",
            "X-Title": "ellmer",
        }

    def content_json(self, c: ContentText) -> Any:
        return None if c.text == "" else {"type": "text", "text": c.text}

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        err = result.get("error") if isinstance(result, dict) else None
        if err:
            raise LLMError(str(err.get("message") if isinstance(err, dict) else err))
        return super().value_turn(model, result, has_type)


class ProviderVllm(ProviderOpenAICompatible):
    name = "VLLM"


class ProviderLMStudio(ProviderOpenAICompatible):
    name = "LM Studio"
    param_map = {
        "frequency_penalty": "frequency_penalty",
        "max_tokens": "max_tokens",
        "presence_penalty": "presence_penalty",
        "seed": "seed",
        "stop": "stop_sequences",
        "temperature": "temperature",
        "top_k": "top_k",
        "top_p": "top_p",
    }


class ProviderHuggingFace(ProviderOpenAICompatible):
    name = "HuggingFace"


class ProviderPerplexity(ProviderOpenAICompatible):
    name = "Perplexity"
    param_map = {
        "max_tokens": "max_tokens",
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "presence_penalty": "presence_penalty",
        "frequency_penalty": "frequency_penalty",
    }


class ProviderPortkeyAI(ProviderOpenAICompatible):
    name = "PortkeyAI"

    def auth_headers(self) -> dict[str, str]:
        cred = self.credentials()
        return {"x-portkey-api-key": str(cred)} if cred else {}


class ProviderCloudflare(ProviderOpenAICompatible):
    name = "Cloudflare"


class ProviderAzureOpenAI(ProviderOpenAICompatible):
    name = "Azure/OpenAI"

    def __init__(self, base_url: str, api_version: str, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self.api_version = api_version

    def auth_headers(self) -> dict[str, str]:
        cred = self.credentials()
        if isinstance(cred, Mapping):
            return {str(k): str(v) for k, v in cred.items()}
        return {"api-key": str(cred)} if cred else {}

    def chat_url(self, model: Model) -> str:
        return _url(self.base_url, self.chat_path()) + f"?api-version={self.api_version}"


class ProviderOpenAI(ProviderOpenAICompatible):
    """OpenAI's Responses API (``/responses``)."""

    name = "OpenAI"
    param_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "frequency_penalty": "frequency_penalty",
        "max_output_tokens": "max_tokens",
        "log_probs": "log_probs",
        "reasoning_effort": "reasoning_effort",
    }

    def __init__(self, *args: Any, service_tier: str = "auto", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.service_tier = service_tier

    def chat_path(self) -> str:
        return "/responses"

    def chat_body(self, model: Model, turns: list[Turn], type: Type | None) -> dict[str, Any]:
        inputs: list[Any] = []
        for t in turns:
            for c in t.contents:
                if isinstance(c, ContentText):
                    kind = "input_text" if t.role in ("user", "system") else "output_text"
                    inputs.append({"role": t.role, "content": [{"type": kind, "text": c.text}]})
        prm = self.chat_params(model.params)
        reasoning = None
        if "reasoning_effort" in prm:
            reasoning = {"effort": prm.pop("reasoning_effort"), "summary": "auto"}
        include = []
        if prm.get("log_probs") is True:
            include.append("message.output_text.logprobs")
        if model.name.startswith("o") or model.name.startswith("gpt-5"):
            include.append("reasoning.encrypted_content")
        prm.pop("log_probs", None)
        body: dict[str, Any] = {"input": inputs}
        if include:
            body["include"] = include
        body["model"] = model.name
        for k, v in prm.items():
            body[k] = _r_vec(v)
        body["stream"] = False
        if type is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "structured_data",
                    "schema": type_as_json(type, "openai"),
                    "strict": True,
                }
            }
        if reasoning is not None:
            body["reasoning"] = reasoning
        body["store"] = False
        body["service_tier"] = self.service_tier
        return body

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        contents: list[Any] = []
        for output in (result or {}).get("output") or []:
            kind = output.get("type")
            if kind == "message":
                for content in output.get("content") or []:
                    ctype = content.get("type")
                    if ctype is not None and ctype != "output_text":
                        continue
                    if has_type:
                        contents.append(ContentJson(data=parse_json(content.get("text") or "")))
                    else:
                        contents.append(ContentText(content.get("text")))
            elif kind == "function_call":
                args = parse_json(output.get("arguments") or "{}")
                contents.append(ContentToolRequest(output.get("id"), output.get("name", ""), args))
            elif kind == "reasoning":
                summary = "".join(s.get("text", "") for s in output.get("summary") or [])
                contents.append(ContentThinking(summary, extra=output))
            else:
                raise LLMError(f'Unknown content type "{kind}".')
        return Turn("assistant", contents, result)


_ANTHROPIC_STRUCTURED = "^claude-[a-z]+-(4-[5-9]|[5-9]|[1-9]\\d)(-|$)"


def has_claude_structured_output(model: str) -> bool:
    """Port of ``ellmer:::has_claude_structured_output()``."""
    from pytacheck._r import grepl

    return bool(grepl(_ANTHROPIC_STRUCTURED, [model])[0])


class ProviderAnthropic(Provider):
    name = "Anthropic"
    transient = (429, 503, 529)
    param_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "max_tokens": "max_tokens",
        "stop_sequences": "stop_sequences",
        "budget_tokens": "reasoning_tokens",
        "reasoning_effort": "reasoning_effort",
    }

    def __init__(self, *args: Any, cache: str = "5m", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cache = cache

    def auth_headers(self) -> dict[str, str]:
        cred = self.credentials()
        out = {"anthropic-version": "2023-06-01"}
        if isinstance(cred, Mapping):
            out.update({str(k): str(v) for k, v in cred.items()})
        elif cred:
            out["x-api-key"] = str(cred)
        return out

    def chat_path(self) -> str:
        return "messages"

    def cache_control(self) -> dict[str, str] | None:
        return None if self.cache == "none" else {"type": "ephemeral", "ttl": self.cache}

    def chat_params(self, p: Mapping[str, Any]) -> dict[str, Any]:
        out = standardise_params(p, self.param_map)
        out["max_tokens"] = out.get("max_tokens") or 4096
        out["stop_sequences"] = list(out.get("stop_sequences") or [])
        return out

    def uses_tool_structured_output(self, model: Model, type: Type | None) -> bool:
        return type is not None and (
            not has_claude_structured_output(model.name) or type_has_additional_properties(type)
        )

    def chat_body(self, model: Model, turns: list[Turn], type: Type | None) -> dict[str, Any]:
        system = None
        if turns and turns[0].role == "system":
            block: dict[str, Any] = {"type": "text", "text": turns[0].contents[0].text}
            cc = self.cache_control()
            if cc is not None:
                block["cache_control"] = cc
            system = [block]
        messages = []
        for i, t in enumerate(turns):
            if t.role == "system":
                continue
            content = []
            for c in t.contents:
                if isinstance(c, ContentText):
                    text = c.text or ""
                    content.append(
                        {"type": "text", "text": "[empty string]" if not text.strip() else text}
                    )
                elif isinstance(c, ContentThinking) and c.thinking:
                    content.append(
                        {
                            "type": "thinking",
                            "thinking": c.thinking,
                            "signature": (c.extra or {}).get("signature"),
                        }
                    )
            if not content:
                if t.role != "assistant":
                    continue
                content = [{"type": "text", "text": "[empty string]"}]
            if i == len(turns) - 1:
                cc = self.cache_control()
                if cc is not None:
                    content[-1]["cache_control"] = cc
            messages.append({"role": t.role, "content": content})
        tools: list[Any] = []
        tool_choice = None
        output_config: dict[str, Any] | None = None
        if type is not None:
            if has_claude_structured_output(model.name) and not type_has_additional_properties(type):
                output_config = {
                    "format": {"type": "json_schema", "schema": type_as_json(type, "generic")}
                }
            else:
                schema = type_as_json(type_object(data=type), "generic")
                tools.append(
                    {
                        "name": "_structured_tool_call",
                        "description": "Extract structured data",
                        "input_schema": {k: v for k, v in schema.items() if not _empty(v)},
                    }
                )
                tool_choice = {"type": "tool", "name": "_structured_tool_call"}
        prm = self.chat_params(model.params)
        thinking = None
        if "reasoning_effort" in prm:
            thinking = {"type": "adaptive"}
            output_config = _modify_list(output_config or {}, {"effort": prm.pop("reasoning_effort")})
        elif "budget_tokens" in prm:
            thinking = {"type": "enabled", "budget_tokens": prm.pop("budget_tokens")}
        body: dict[str, Any] = {"model": model.name}
        if system is not None:
            body["system"] = system
        if messages:
            body["messages"] = messages
        body["stream"] = False
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if thinking is not None:
            body["thinking"] = thinking
        if output_config:
            body["output_config"] = output_config
        for k, v in prm.items():
            if _empty(v):
                continue
            body[k] = v if k != "stop_sequences" else list(v)
        return body

    def error_body(self, resp: httpx.Response) -> str | None:
        if _content_type(resp) == "application/json":
            body = resp_body_json(resp)
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                return f"{err.get('message')} [{err.get('type')}]"
        return None

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        contents: list[Any] = []
        for content in (result or {}).get("content") or []:
            kind = content.get("type")
            if kind == "text":
                if has_type and has_claude_structured_output(model.name):
                    contents.append(ContentJson(string=content.get("text")))
                else:
                    contents.append(ContentText(content.get("text")))
            elif kind == "tool_use":
                inp = content.get("input")
                if has_type:
                    contents.append(ContentJson(data=(inp or {}).get("data")))
                else:
                    if isinstance(inp, str):
                        inp = parse_json(inp)
                    contents.append(ContentToolRequest(content.get("id"), content.get("name"), inp))
            elif kind == "thinking":
                contents.append(
                    ContentThinking(content.get("thinking", ""), {"signature": content.get("signature")})
                )
            elif kind in ("fallback", "server_tool_use", "web_search_tool_result",
                          "web_fetch_tool_result"):
                continue
            else:
                raise LLMError(f'Unknown content type "{kind}".')
        return Turn("assistant", contents, result)


class ProviderGoogleGemini(Provider):
    name = "Google/Gemini"
    schema_kind = "gemini"
    param_map = {
        "temperature": "temperature",
        "topP": "top_p",
        "topK": "top_k",
        "frequencyPenalty": "frequency_penalty",
        "presencePenalty": "presence_penalty",
        "seed": "seed",
        "maxOutputTokens": "max_tokens",
        "responseLogprobs": "log_probs",
        "stopSequences": "stop_sequences",
        "thinkingBudget": "reasoning_tokens",
        "thinkingLevel": "reasoning_effort",
    }

    def auth_headers(self) -> dict[str, str]:
        cred = self.credentials()
        if isinstance(cred, Mapping):
            return {str(k): str(v) for k, v in cred.items()}
        return {"x-goog-api-key": str(cred)} if cred else {}

    def chat_url(self, model: Model) -> str:
        return _url(self.base_url, "models", f"{model.name}:generateContent")

    def chat_params(self, p: Mapping[str, Any]) -> dict[str, Any]:
        return standardise_params(p, self.param_map)

    def chat_body(self, model: Model, turns: list[Turn], type: Type | None) -> dict[str, Any]:
        if turns and turns[0].role == "system":
            system = {"parts": {"text": turns[0].contents[0].text}}
        else:
            system = {"parts": {"text": ""}}
        gen = {k: _r_vec(v) for k, v in self.chat_params(model.params).items()}
        if type is not None:
            gen["response_mime_type"] = "application/json"
            gen["response_schema"] = type_as_json(type, "gemini")
        if "thinkingBudget" in gen or "thinkingLevel" in gen:
            tc = {}
            if "thinkingBudget" in gen:
                tc["thinkingBudget"] = gen.pop("thinkingBudget")
            if "thinkingLevel" in gen:
                tc["thinkingLevel"] = gen.pop("thinkingLevel")
            tc["includeThoughts"] = True
            gen["thinkingConfig"] = tc
        contents = []
        for t in turns:
            if t.role == "system":
                continue
            parts = []
            for c in t.contents:
                if isinstance(c, ContentText):
                    if c.text != "":
                        parts.append({"text": c.text})
                elif isinstance(c, ContentThinking):
                    parts.append({"thought": True, "text": c.thinking})
            if t.role == "assistant":
                if parts:
                    contents.append({"role": "model", "parts": parts})
            else:
                contents.append({"role": t.role, "parts": parts})
        body: dict[str, Any] = {}
        if contents:
            body["contents"] = contents
        body["systemInstruction"] = system
        if gen:
            body["generationConfig"] = gen
        return body

    def error_body(self, resp: httpx.Response) -> str | None:
        body = resp_body_json(resp)
        err = body.get("error") if isinstance(body, dict) else None
        return err.get("message") if isinstance(err, dict) else None

    def value_turn(self, model: Model, result: Any, has_type: bool) -> Turn:
        cands = (result or {}).get("candidates") or [{}]
        message = cands[0].get("content") or {}
        contents: list[Any] = []
        for part in message.get("parts") or []:
            if part.get("thought") is True and "text" in part:
                contents.append(ContentThinking(part["text"]))
            elif "text" in part:
                if has_type:
                    contents.append(ContentJson(string=part["text"]))
                else:
                    contents.append(ContentText(part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                contents.append(ContentToolRequest(fc.get("name"), fc.get("name", ""), fc.get("args")))
        return Turn("assistant", contents, result)


class _MistralThrottle:
    def __init__(self) -> None:
        from pytacheck.http import Throttle

        self._t = Throttle(capacity=1, fill_time_s=1)

    def acquire(self, host: str) -> None:
        self._t.acquire(host)


class _LazyThrottle:
    _inner: _MistralThrottle | None = None

    def acquire(self, host: str) -> None:
        if self._inner is None:
            self._inner = _MistralThrottle()
        self._inner.acquire(host)


_MISTRAL_THROTTLE = _LazyThrottle()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class Chat:
    """A conversation with one provider/model (ellmer's ``Chat``)."""

    def __init__(self, provider: Provider, model: Model, system_prompt: str | None = None) -> None:
        self.provider = provider
        self.model = model
        self._turns: list[Turn] = []
        if system_prompt is not None:
            if isinstance(system_prompt, list | tuple):
                system_prompt = "\n\n".join(system_prompt)
            self._turns.append(Turn("system", [ContentText(system_prompt)]))

    def get_turns(self) -> list[Turn]:
        return list(self._turns)

    def last_turn(self) -> Turn | None:
        for t in reversed(self._turns):
            if t.role == "assistant":
                return t
        return None

    def _submit(self, text: str | None, type: Type | None) -> Turn:
        user = Turn("user", [ContentText(text)])
        turns = [*self._turns, user]
        url, headers, body = self.provider.build_request(self.model, turns, type)
        resp = self.provider.perform(url, headers, body)
        result = resp_body_json(resp)
        turn = self.provider.value_turn(self.model, result, type is not None)
        self._turns.extend([user, turn])
        return turn

    def chat(self, text: str | None) -> str:
        """Send *text*; return the reply text (``chat$chat(text, echo = FALSE)``)."""
        turn = self._submit(text, None)
        return turn.text

    def chat_structured(self, text: str | None, type: Any, convert: bool = True) -> Any:
        """Port of ``chat$chat_structured(text, type = type)``."""
        t = as_type(type)
        wrap = self.provider.needs_wrapper(t)
        if wrap:
            t = type_object(wrapper=t)
        turn = self._submit(text, t)
        return extract_data(turn, t, convert, wrap)


def extract_data(turn: Turn, type: Type, convert: bool = True, needs_wrapper: bool = False) -> Any:
    """Port of ``ellmer:::extract_data()``."""
    jsons = [c for c in turn.contents if isinstance(c, ContentJson)]
    if not jsons:
        raise LLMError("Data extraction failed: no JSON responses found.")
    if len(jsons) > 1:
        warnings.warn(f"Found {len(jsons)} JSON responses, using the first.", stacklevel=3)
    out = jsons[0].parsed
    t = type
    if needs_wrapper:
        out = out.get("wrapper") if isinstance(out, dict) else None
        assert isinstance(t, TypeObject)
        t = next(iter(t.properties.values()))
    if convert:
        out = convert_from_type(out, t)
    return out


# ---------------------------------------------------------------------------
# ellmer::chat() and the chat_*() constructors
# ---------------------------------------------------------------------------


def _inform(msg: str) -> None:
    from pytacheck.llm.core import _message

    _message(msg)


def _set_default(value: str | None, default: str, arg: str = "model") -> str:
    if value is None:
        _inform(f'Using {arg} = "{default}".')
        return default
    return value


def _ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def _google_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise LLMError(
            "No Google credentials are available.\n"
            "ℹ Try suppling an API key or configuring Google's application default credentials."
        )
    return key


def _anthropic_base_url() -> str:
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base:
        return "https://api.anthropic.com/v1"
    base = base.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _mk(
    provider: Provider, model: str, prm: Mapping[str, Any] | None, api_args: Any, sp: str | None
) -> Chat:
    return Chat(provider, Model(model, prm if prm is not None else params(), api_args), sp)


def chat_openai_compatible_like(
    cls: type[ProviderOpenAICompatible],
    base_url: str,
    key_env: str | None,
    default_model: str | None,
    model: str | None,
    system_prompt: str | None,
    prm: Mapping[str, Any] | None,
    api_args: Any,
) -> Chat:
    if default_model is not None:
        model = _set_default(model, default_model)
    elif model is None:
        raise LLMError("Must specify `model`.")

    def cred() -> str:
        return _key_get(key_env) if key_env else ""

    return _mk(cls(base_url, cred), model, prm, api_args, system_prompt)


def chat_groq(model: str | None = None, system_prompt: str | None = None, params: Any = None,
              api_args: Any = None, base_url: str = "https://api.groq.com/openai/v1") -> Chat:
    """Port of ``ellmer::chat_groq()``."""
    return chat_openai_compatible_like(
        ProviderGroq, base_url, "GROQ_API_KEY", "openai/gpt-oss-20b", model, system_prompt,
        params, api_args,
    )


def chat_openai(model: str | None = None, system_prompt: str | None = None, params: Any = None,
                api_args: Any = None, base_url: str = "https://api.openai.com/v1",
                service_tier: str = "auto") -> Chat:
    """Port of ``ellmer::chat_openai()`` (Responses API)."""
    model = _set_default(model, "gpt-5.6-terra")
    prov = ProviderOpenAI(base_url, lambda: _key_get("OPENAI_API_KEY"), service_tier=service_tier)
    return _mk(prov, model, params, api_args, system_prompt)


def chat_deepseek(model: str | None = None, system_prompt: str | None = None, params: Any = None,
                  api_args: Any = None, base_url: str = "https://api.deepseek.com") -> Chat:
    """Port of ``ellmer::chat_deepseek()``."""
    return chat_openai_compatible_like(
        ProviderDeepSeek, base_url, "DEEPSEEK_API_KEY", "deepseek-v4-flash", model,
        system_prompt, params, api_args,
    )


def chat_mistral(model: str | None = None, system_prompt: str | None = None, params: Any = None,
                 api_args: Any = None) -> Chat:
    """Port of ``ellmer::chat_mistral()``."""
    return chat_openai_compatible_like(
        ProviderMistral, "https://api.mistral.ai/v1/", "MISTRAL_API_KEY", "mistral-large-latest",
        model, system_prompt, params, api_args,
    )


def chat_openrouter(model: str | None = None, system_prompt: str | None = None,
                    params: Any = None, api_args: Any = None) -> Chat:
    """Port of ``ellmer::chat_openrouter()``."""
    return chat_openai_compatible_like(
        ProviderOpenRouter, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        "gpt-5.6-terra", model, system_prompt, params, api_args,
    )


def chat_huggingface(model: str | None = None, system_prompt: str | None = None,
                     params: Any = None, api_args: Any = None) -> Chat:
    """Port of ``ellmer::chat_huggingface()``."""
    return chat_openai_compatible_like(
        ProviderHuggingFace, "https://router.huggingface.co/v1/", "HUGGINGFACE_API_KEY",
        "Qwen/Qwen3-235B-A22B-Instruct-2507", model, system_prompt, params, api_args,
    )


def chat_perplexity(model: str | None = None, system_prompt: str | None = None,
                    params: Any = None, api_args: Any = None,
                    base_url: str = "https://api.perplexity.ai/") -> Chat:
    """Port of ``ellmer::chat_perplexity()``."""
    return chat_openai_compatible_like(
        ProviderPerplexity, base_url, "PERPLEXITY_API_KEY", "sonar", model, system_prompt,
        params, api_args,
    )


def chat_portkey(model: str | None = None, system_prompt: str | None = None, params: Any = None,
                 api_args: Any = None, base_url: str = "https://api.portkey.ai/v1") -> Chat:
    """Port of ``ellmer::chat_portkey()``."""
    if not isinstance(model, str):
        raise LLMError(f"`model` must be a single string, not {_friendly(model)}.")
    vk = os.environ.get("PORTKEY_VIRTUAL_KEY", "")
    if not model.startswith("@") and vk:
        model = f"@{vk}/{model}"
    prov = ProviderPortkeyAI(base_url, lambda: _key_get("PORTKEY_API_KEY"))
    return _mk(prov, model, params, api_args, system_prompt)


def chat_vllm(base_url: str | None = None, model: str | None = None,
              system_prompt: str | None = None, params: Any = None, api_args: Any = None,
              credentials: Callable[[], str] | None = None) -> Chat:
    """Port of ``ellmer::chat_vllm()``."""
    if not isinstance(base_url, str):
        raise LLMError(f"`base_url` must be a single string, not {_friendly(base_url)}.")
    cred = credentials or (lambda: _key_get("VLLM_API_KEY"))
    token = cred()
    if not isinstance(token, str):
        raise LLMError(f"`credentials()` must be a single string, not {_friendly(token)}.")
    if model is None:
        raise LLMError("Must specify `model`.")
    return _mk(ProviderVllm(base_url, cred), model, params, api_args, system_prompt)


def chat_lmstudio(model: str | None = None, system_prompt: str | None = None,
                  params: Any = None, api_args: Any = None,
                  base_url: str | None = None) -> Chat:
    """Port of ``ellmer::chat_lmstudio()``."""
    base = base_url or os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234")
    key = os.environ.get("LMSTUDIO_API_KEY", "")
    try:
        ids = [m["id"] for m in _openai_models(_url(base, "v1"), key)]
    except Exception:
        raise LLMError("Can't find locally running LM Studio.") from None
    if model is None:
        raise LLMError(
            f"Must specify `model`.\nℹ Locally available models: {', '.join(repr(i) for i in ids)}."
        )
    if model not in ids:
        raise LLMError(f'Model "{model}" is not available in LM Studio.')
    return _mk(ProviderLMStudio(_url(base, "v1"), lambda: key), model, params, api_args,
               system_prompt)


def chat_ollama(model: str | None = None, system_prompt: str | None = None, params: Any = None,
                api_args: Any = None, base_url: str | None = None) -> Chat:
    """Port of ``ellmer::chat_ollama()`` (OpenAI-compatible ``/v1`` endpoint)."""
    base = base_url or _ollama_base()
    key = os.environ.get("OLLAMA_API_KEY", "")
    host = urlsplit(base).hostname
    local = host in ("localhost", "127.0.0.1", "::1")
    try:
        names = [n for n, _, _ in _ollama_tags(base, key)]
    except Exception:
        if local:
            raise LLMError("Can't find locally running ollama.") from None
        raise LLMError(f"Can't connect to ollama at <{base}>.") from None
    if model is None:
        raise LLMError(f"Must specify `model`.\nℹ Available models: {', '.join(names)}.")
    if model not in names:
        if local:
            raise LLMError(f'Model "{model}" is not installed locally.')
        raise LLMError(f'Model "{model}" is not available on <{base}>.')
    prov = ProviderOllama(_url(base, "v1"), lambda: key)
    return _mk(prov, model, params, api_args, system_prompt)


def chat_google_gemini(model: str | None = None, system_prompt: str | None = None,
                       params: Any = None, api_args: Any = None,
                       base_url: str = "https://generativelanguage.googleapis.com/v1beta/") -> Chat:
    """Port of ``ellmer::chat_google_gemini()``."""
    model = _set_default(model, "gemini-3.7-flash")
    key = _google_key()
    return _mk(ProviderGoogleGemini(base_url, lambda: key), model, params, api_args,
               system_prompt)


def chat_anthropic(model: str | None = None, system_prompt: str | None = None,
                   params: Any = None, api_args: Any = None, base_url: str | None = None,
                   cache: str = "5m") -> Chat:
    """Port of ``ellmer::chat_anthropic()`` / ``chat_claude()``."""
    model = _set_default(model, "claude-sonnet-5")
    prov = ProviderAnthropic(
        base_url or _anthropic_base_url(), lambda: _key_get("ANTHROPIC_API_KEY"), cache=cache
    )
    return _mk(prov, model, params, api_args, system_prompt)


def chat_cloudflare(model: str | None = None, system_prompt: str | None = None,
                    params: Any = None, api_args: Any = None) -> Chat:
    """Port of ``ellmer::chat_cloudflare()``."""
    model = _set_default(model, "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    account = _key_get("CLOUDFLARE_ACCOUNT_ID")
    base = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/"
    prov = ProviderCloudflare(base, lambda: _key_get("CLOUDFLARE_API_KEY"))
    return _mk(prov, model, params, api_args, system_prompt)


def chat_azure_openai(model: str | None = None, system_prompt: str | None = None,
                      params: Any = None, api_args: Any = None, endpoint: str | None = None,
                      api_version: str | None = None) -> Chat:
    """Port of ``ellmer::chat_azure_openai()`` (API-key authentication)."""
    endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT") or None
    if not isinstance(endpoint, str):
        raise LLMError(f"`endpoint` must be a single string, not {_friendly(endpoint)}.")
    if not isinstance(model, str):
        raise LLMError(f"`model` must be a single string, not {_friendly(model)}.")
    prov = ProviderAzureOpenAI(
        f"{endpoint}/openai/deployments/{model}",
        api_version or "2024-10-21",
        credentials=lambda: _key_get("AZURE_OPENAI_API_KEY"),
    )
    return _mk(prov, model, params, api_args, system_prompt)


def chat_github(*args: Any, **kwargs: Any) -> Chat:
    """``ellmer::chat_github()`` is defunct (GitHub Models was retired)."""
    raise LLMError(
        "`chat_github()` was deprecated in ellmer 0.5.0 and is now defunct.\n"
        "ℹ GitHub Models was retired on 2026-07-30.\n"
        "ℹ `chat_google_gemini()` offers a free tier and `chat_posit()` offers a free trial."
    )


_CHAT_FUNS: dict[str, Callable[..., Chat]] = {
    "anthropic": chat_anthropic,
    "claude": chat_anthropic,
    "azure_openai": chat_azure_openai,
    "cloudflare": chat_cloudflare,
    "deepseek": chat_deepseek,
    "github": chat_github,
    "google_gemini": chat_google_gemini,
    "groq": chat_groq,
    "huggingface": chat_huggingface,
    "lmstudio": chat_lmstudio,
    "mistral": chat_mistral,
    "ollama": chat_ollama,
    "openai": chat_openai,
    "openrouter": chat_openrouter,
    "perplexity": chat_perplexity,
    "portkey": chat_portkey,
}
#: ellmer providers pytacheck does not implement (they need cloud SDK auth).
_UNSUPPORTED = ("aws_bedrock", "databricks", "google_vertex", "posit", "snowflake")


def chat(
    name: str,
    system_prompt: str | None = None,
    params: Mapping[str, Any] | None = None,
    api_args: Mapping[str, Any] | None = None,
) -> Chat:
    """Port of ``ellmer::chat("provider/model", ...)``."""
    if not isinstance(name, str) or not name:
        raise LLMError(f"`name` must be a single string, not {_friendly(name)}.")
    pieces = name.split("/")
    provider = pieces[0]
    model = "/".join(pieces[1:]) if len(pieces) > 1 else None
    if provider in _UNSUPPORTED:
        raise LLMError(
            f"pytacheck does not implement the `{provider}` provider; use an "
            "OpenAI-compatible, Gemini or Anthropic model."
        )
    fun = _CHAT_FUNS.get(provider)
    if fun is None:
        raise LLMError(f"Can't find provider `ellmer::chat_{provider}()`.")
    kwargs: dict[str, Any] = {"system_prompt": system_prompt, "params": params}
    if api_args:
        kwargs["api_args"] = dict(api_args)
    return fun(model=model, **kwargs)


# ---------------------------------------------------------------------------
# model listings (ellmer::models_*())
# ---------------------------------------------------------------------------


def _ollama_tags(base_url: str, key: str = "") -> list[tuple[str, str, float]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = perform("GET", _url(base_url, "api/tags"), headers=headers, max_tries=3)
    json = resp_body_json(resp)
    out = []
    for m in json.get("models") or []:
        name = str(m.get("name"))
        if name.endswith(":latest"):
            name = name[: -len(":latest")]
        out.append((name, str(m.get("modified_at")), float(m.get("size") or 0)))
    return out


_OLLAMA_SHOW_CACHE: dict[tuple[str, str], Any] = {}


def _ollama_capabilities(base_url: str, model: str, key: str = "") -> str:
    ck = (base_url, model)
    if ck not in _OLLAMA_SHOW_CACHE:
        try:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            resp = perform(
                "POST",
                _url(base_url, "api/show"),
                headers=headers,
                body={"model": model, "verbose": False},
            )
            _OLLAMA_SHOW_CACHE[ck] = resp_body_json(resp)
        except Exception:
            return ""
    caps = (_OLLAMA_SHOW_CACHE[ck] or {}).get("capabilities") or []
    return ",".join(str(c) for c in caps)


def _parse_r_datetime(s: str) -> Any:
    """``as.POSIXct(s)`` with R's default formats (date part only when no space follows)."""
    import pandas as pd

    from pytacheck._r import regextract

    date = regextract("^[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}", [s])[0]
    rest = s[len(date) :] if date else ""
    time = regextract("^ [0-9]{1,2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]*)?)?", [rest])[0] if date else None
    if not date:
        return pd.NaT
    text = date.replace("/", "-") + (time or "")
    return pd.Timestamp(text, tz="UTC")


def models_ollama(base_url: str = "http://localhost:11434") -> Any:
    """Port of ``ellmer::models_ollama()``: id, created_at, size, capabilities."""
    import pandas as pd

    key = os.environ.get("OLLAMA_API_KEY", "")
    tags = _ollama_tags(base_url, key)
    df = pd.DataFrame(
        {
            "id": pd.Series([t[0] for t in tags], dtype="string"),
            "created_at": pd.Series([_parse_r_datetime(t[1]) for t in tags], dtype="datetime64[ns, UTC]")
            if tags
            else pd.Series([], dtype="datetime64[ns, UTC]"),
            "size": pd.Series([t[2] for t in tags], dtype="float64"),
            "capabilities": pd.Series(
                [_ollama_capabilities(base_url, t[0], key) for t in tags], dtype="string"
            ),
        }
    )
    if len(df):
        order = sorted(range(len(df)), key=lambda i: -df["created_at"].iloc[i].value)
        df = df.iloc[order]
    return df


def _openai_models(base_url: str, key: str) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    resp = perform("GET", _url(base_url, "/models"), headers=headers,
                   error_body=_openai_error_body)
    data = resp_body_json(resp).get("data") or []
    return [d for d in data if isinstance(d, dict)]


def _models_openai_compatible(base_url: str, key: str) -> Any:
    import pandas as pd

    data = _openai_models(base_url, key)
    created = [
        pd.Timestamp(float(d.get("created") or 0), unit="s").normalize().date() for d in data
    ]
    df = pd.DataFrame(
        {
            "id": pd.Series([str(d.get("id")) for d in data], dtype="string"),
            "created_at": pd.Series(created, dtype=object),
            "owned_by": pd.Series([d.get("owned_by") for d in data], dtype="string"),
            "cached_input": pd.Series([math.nan] * len(data), dtype="float64"),
            "input": pd.Series([math.nan] * len(data), dtype="float64"),
            "output": pd.Series([math.nan] * len(data), dtype="float64"),
        }
    )
    order = sorted(range(len(df)), key=lambda i: created[i], reverse=True)
    return df.iloc[order]


def models_openai(base_url: str = "https://api.openai.com/v1") -> Any:
    """Port of ``ellmer::models_openai()`` (prices are not bundled: ``NA``)."""
    return _models_openai_compatible(base_url, _key_get("OPENAI_API_KEY"))


def models_deepseek(base_url: str = "https://api.deepseek.com") -> Any:
    """Port of ``ellmer::models_deepseek()``."""
    return _models_openai_compatible(base_url, _key_get("DEEPSEEK_API_KEY"))


def models_mistral() -> Any:
    """Port of ``ellmer::models_mistral()``."""
    import pandas as pd

    key = _key_get("MISTRAL_API_KEY")
    data = _openai_models("https://api.mistral.ai/v1/", key)
    return pd.DataFrame(
        {
            "id": pd.Series([str(d.get("id")) for d in data], dtype="string"),
            "name": pd.Series([d.get("name") for d in data], dtype="string"),
            "created_at": pd.to_datetime([d.get("created") for d in data], unit="s", utc=True),
        }
    )


def models_vllm(base_url: str) -> Any:
    """Port of ``ellmer::models_vllm()``."""
    return _models_openai_compatible(base_url, _key_get("VLLM_API_KEY"))


def models_lmstudio(base_url: str = "http://localhost:1234") -> Any:
    """Port of ``ellmer::models_lmstudio()``."""
    return _models_openai_compatible(_url(base_url, "v1"), os.environ.get("LMSTUDIO_API_KEY", ""))


def models_portkey(base_url: str = "https://api.portkey.ai/v1") -> Any:
    """Port of ``ellmer::models_portkey()``."""
    import pandas as pd

    key = _key_get("PORTKEY_API_KEY")
    resp = perform("GET", _url(base_url, "/models"), headers={"x-portkey-api-key": key})
    data = resp_body_json(resp).get("data") or []
    return pd.DataFrame({"id": pd.Series([str(d.get("id")) for d in data], dtype="string")})


def models_google_gemini(
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/",
) -> Any:
    """Port of ``ellmer::models_google_gemini()``."""
    import pandas as pd

    key = _google_key()
    resp = perform("GET", _url(base_url, "/models"), headers={"x-goog-api-key": key})
    models = resp_body_json(resp).get("models") or []
    rows = []
    for m in models:
        name = str(m.get("name", ""))
        if name.startswith("models/"):
            name = name[len("models/") :]
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            rows.append(name)
    from pytacheck._r import r_sorted

    rows = r_sorted(rows)
    n = len(rows)
    return pd.DataFrame(
        {
            "id": pd.Series(rows, dtype="string"),
            "cached_input": pd.Series([math.nan] * n, dtype="float64"),
            "input": pd.Series([math.nan] * n, dtype="float64"),
            "output": pd.Series([math.nan] * n, dtype="float64"),
        }
    )


def models_anthropic(base_url: str | None = None) -> Any:
    """Port of ``ellmer::models_anthropic()``."""
    import pandas as pd

    key = _key_get("ANTHROPIC_API_KEY")
    resp = perform(
        "GET",
        _url(base_url or _anthropic_base_url(), "/models"),
        headers={"anthropic-version": "2023-06-01", "x-api-key": key},
    )
    data = resp_body_json(resp).get("data") or []
    df = pd.DataFrame(
        {
            "id": pd.Series([str(d.get("id")) for d in data], dtype="string"),
            "name": pd.Series([d.get("display_name") for d in data], dtype="string"),
            "created_at": pd.to_datetime([d.get("created_at") for d in data], utc=True),
            "cached_input": pd.Series([math.nan] * len(data), dtype="float64"),
            "input": pd.Series([math.nan] * len(data), dtype="float64"),
            "output": pd.Series([math.nan] * len(data), dtype="float64"),
        }
    )
    return df.sort_values("created_at", ascending=False, kind="stable")
