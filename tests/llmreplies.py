"""Look up recorded LLM replies by what was asked, not by R's request hash.

metacheck's tests store provider replies under httptest2 paths whose last part
is the first six hex digits of an MD5 of R's serialisation of the request body.
Only R's HTTP client produces that digest, and pytacheck sends its LLM requests
through the official provider SDKs, whose bodies differ from ellmer's. So the
reply files keep their names (R still finds them when the goldens are
regenerated) and each mock directory that holds LLM replies has an
``index.json`` that maps a *semantic key* to the file.

The key (:func:`llm_key`) is made from what the request asks, read from the body
of an OpenAI chat-completions, OpenAI Responses, Anthropic Messages or Gemini
``generateContent`` request: the endpoint, model, system prompt, user text, the
structured-output schema (reduced to its shape) and the parameters that change
a result (temperature, top_p, top_k, seed, max tokens, reasoning effort, stop
sequences). How the SDK words those (``max_tokens`` or ``max_completion_tokens``,
``generationConfig`` or top-level fields, ``required`` lists or nullable types)
does not matter.

``index.json`` is written by :func:`write_index` from the entries
``tests.httpmock.replay`` logs when ``PYTACHECK_LLM_INDEX_LOG`` names a file
(``python -m tests.llmreplies merge LOG``); it is read-only for the tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: the parameters of a request that can change what the model answers
_PARAMS = (
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "max_tokens",
    "reasoning_effort",
    "reasoning_budget",
    "frequency_penalty",
    "presence_penalty",
    "stop",
)
_INDEX = "index.json"


def _num(x: Any) -> Any:
    """A whole float as an int (``0.0`` and ``0`` are one value)."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, float):
        return round(x, 9)
    return x


def _text(content: Any) -> str:
    """The text of a message ``content``: a string or a list of text parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    out = []
    for part in content:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return "".join(out)


# -- schemas -----------------------------------------------------------------------------


def _node(s: Any) -> dict[str, Any]:
    """The shape of one schema node, whichever dialect it is written in."""
    if not isinstance(s, dict):
        return {"t": "object", "d": "", "p": []} if s in ([], None) else {"t": "other"}
    typ = s.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), "null")
    typ = str(typ).lower() if typ is not None else None
    out: dict[str, Any] = {"t": typ, "d": s.get("description") or ""}
    if "enum" in s:
        out["e"] = s["enum"]
    if typ == "array" or "items" in s:
        out["i"] = _node(s.get("items"))
    if typ == "object" or "properties" in s:
        props = s.get("properties") or {}
        required = s.get("required")
        rows = []
        for name, sub in props.items() if isinstance(props, dict) else []:
            nullable = isinstance(sub, dict) and (
                (isinstance(sub.get("type"), list) and "null" in sub["type"])
                or sub.get("nullable") is True
            )
            optional = nullable or (isinstance(required, list) and name not in required)
            rows.append([name, bool(optional), _node(sub)])
        out["p"] = rows
    return out


def _schema(s: Any) -> Any:
    return None if s is None else _node(s)


def _unwrap_tool(tools: Any) -> Any:
    """The schema inside an Anthropic ``_structured_tool_call`` tool (``data``)."""
    if isinstance(tools, list) and tools and isinstance(tools[0], dict):
        schema = tools[0].get("input_schema") or {}
        return (schema.get("properties") or {}).get("data", schema)
    return None


# -- request families --------------------------------------------------------------------


def _chat(body: dict[str, Any]) -> dict[str, Any]:
    system, user = [], []
    for m in body.get("messages") or []:
        role = m.get("role")
        (system if role in ("system", "developer") else user if role == "user" else []).append(
            _text(m.get("content"))
        )
    fmt = body.get("response_format") or {}
    schema = (
        (fmt.get("json_schema") or {}).get("schema") if fmt.get("type") == "json_schema" else None
    )
    stop = body.get("stop")
    return {
        "model": body.get("model"),
        "system": "\n".join(system),
        "user": "\n".join(user),
        "schema": _schema(schema),
        "params": {
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "top_k": body.get("top_k"),
            "seed": body.get("seed", body.get("random_seed")),
            "max_tokens": body.get("max_tokens", body.get("max_completion_tokens")),
            "reasoning_effort": body.get("reasoning_effort"),
            "frequency_penalty": body.get("frequency_penalty"),
            "presence_penalty": body.get("presence_penalty"),
            "stop": [stop] if isinstance(stop, str) else stop,
        },
    }


def _responses(body: dict[str, Any]) -> dict[str, Any]:
    system, user = [], []
    if body.get("instructions"):
        system.append(_text(body["instructions"]))
    inp = body.get("input")
    if isinstance(inp, str):
        user.append(inp)
    for m in inp if isinstance(inp, list) else []:
        role = m.get("role")
        (system if role in ("system", "developer") else user if role == "user" else []).append(
            _text(m.get("content"))
        )
    fmt = (body.get("text") or {}).get("format") or {}
    return {
        "model": body.get("model"),
        "system": "\n".join(system),
        "user": "\n".join(user),
        "schema": _schema(fmt.get("schema")) if fmt.get("type") == "json_schema" else None,
        "params": {
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "max_tokens": body.get("max_output_tokens"),
            "reasoning_effort": (body.get("reasoning") or {}).get("effort"),
            "frequency_penalty": body.get("frequency_penalty"),
        },
    }


def _messages(body: dict[str, Any]) -> dict[str, Any]:
    out_cfg = body.get("output_config") or {}
    fmt = out_cfg.get("format") or {}
    schema = fmt.get("schema") if fmt.get("type") == "json_schema" else None
    if schema is None:
        schema = _unwrap_tool(body.get("tools"))
    thinking = body.get("thinking") or {}
    return {
        "model": body.get("model"),
        "system": _text(body.get("system")),
        "user": "\n".join(
            _text(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "user"
        ),
        "schema": _schema(schema),
        "params": {
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "top_k": body.get("top_k"),
            "max_tokens": body.get("max_tokens"),
            "reasoning_effort": out_cfg.get("effort"),
            "reasoning_budget": thinking.get("budget_tokens"),
            "stop": body.get("stop_sequences") or None,
        },
    }


def _generate(body: dict[str, Any], path: str) -> dict[str, Any]:
    cfg = body.get("generationConfig") or body.get("generation_config") or {}
    sysi = body.get("systemInstruction") or body.get("system_instruction") or {}
    parts = sysi.get("parts") if isinstance(sysi, dict) else None
    schema = None
    for key in ("responseSchema", "response_schema", "responseJsonSchema", "response_json_schema"):
        if cfg.get(key) is not None:
            schema = cfg[key]
    thinking = cfg.get("thinkingConfig") or cfg.get("thinking_config") or {}
    model = re.search(r"models/([^/:]+)", path)
    return {
        "model": model.group(1) if model else None,
        "system": _text(parts),
        "user": "\n".join(
            _text(c.get("parts")) for c in body.get("contents") or [] if c.get("role") == "user"
        ),
        "schema": _schema(schema),
        "params": {
            "temperature": cfg.get("temperature"),
            "top_p": cfg.get("topP", cfg.get("top_p")),
            "top_k": cfg.get("topK", cfg.get("top_k")),
            "seed": cfg.get("seed"),
            "max_tokens": cfg.get("maxOutputTokens", cfg.get("max_output_tokens")),
            "reasoning_effort": thinking.get("thinkingLevel", thinking.get("thinking_level")),
            "reasoning_budget": thinking.get("thinkingBudget", thinking.get("thinking_budget")),
            "frequency_penalty": cfg.get("frequencyPenalty", cfg.get("frequency_penalty")),
            "presence_penalty": cfg.get("presencePenalty", cfg.get("presence_penalty")),
            "stop": cfg.get("stopSequences", cfg.get("stop_sequences")) or None,
        },
    }


def llm_request(method: str, url: str, content: bytes) -> dict[str, Any] | None:
    """What an LLM request asks (``None`` when it is not an LLM request)."""
    if method != "POST" or not content:
        return None
    try:
        body = json.loads(content)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    parts = urlsplit(url)
    path = parts.path
    if path.endswith("/chat/completions") and "messages" in body:
        asked = _chat(body)
    elif path.endswith("/responses") and "input" in body:
        asked = _responses(body)
    elif path.endswith("/messages") and "messages" in body:
        asked = _messages(body)
    elif path.endswith(":generateContent") and "contents" in body:
        asked = _generate(body, path)
    else:
        return None
    asked["params"] = {
        k: _num(asked["params"][k]) for k in _PARAMS if asked["params"].get(k) is not None
    }
    endpoint = f"{parts.hostname or ''}{':' + str(parts.port) if parts.port else ''}{path}"
    return {"endpoint": endpoint.replace(":", "-"), **asked}


def llm_key(asked: dict[str, Any]) -> str:
    """The lookup key of :func:`llm_request`'s result."""
    raw = json.dumps(asked, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# -- index files -------------------------------------------------------------------------

_loaded: dict[Path, dict[str, str] | None] = {}


def load_index(root: Path) -> dict[str, str] | None:
    """``{key: file}`` of the mock directory *root* (``None`` without ``index.json``)."""
    if root not in _loaded:
        f = root / _INDEX
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            _loaded[root] = {e["key"]: e["file"] for e in data["replies"]}
        else:
            _loaded[root] = None
    return _loaded[root]


def write_index(root: Path, entries: list[dict[str, Any]]) -> None:
    """Write ``root/index.json``: every reply file with the request it answers."""
    keyed: dict[str, dict[str, Any]] = {}
    for e in entries:
        old = keyed.get(e["key"])
        if old is not None and old["file"] != e["file"]:
            raise SystemExit(
                f"{root}: two reply files answer one request:\n  {old['file']}\n  {e['file']}\n"
                f"  {json.dumps(e['request'], ensure_ascii=False)[:300]}"
            )
        keyed[e["key"]] = e
    replies = sorted(keyed.values(), key=lambda e: e["file"])
    text = json.dumps({"version": 1, "replies": replies}, indent=1, ensure_ascii=False)
    (root / _INDEX).write_text(text + "\n", encoding="utf-8")


def merge_log(log: Path) -> None:
    """Merge the ``PYTACHECK_LLM_INDEX_LOG`` lines into the ``index.json`` files."""
    by_root: dict[Path, list[dict[str, Any]]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            by_root.setdefault(Path(e.pop("root")), []).append(e)
    for root, entries in by_root.items():
        existing = []
        f = root / _INDEX
        if f.exists():
            existing = json.loads(f.read_text(encoding="utf-8"))["replies"]
        write_index(root, existing + entries)
        print(f"{root}: {len({e['key'] for e in existing + entries})} replies")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "merge":
        merge_log(Path(sys.argv[2]))
    else:
        raise SystemExit("usage: python -m tests.llmreplies merge LOGFILE")
