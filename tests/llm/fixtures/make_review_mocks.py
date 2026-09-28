"""Writes the provider replies of the review cases (parity/cases/llm_review.yaml).

Run from the repo root. Each reply is stored under the httptest2 mock path R
computes for the request (read "Expected mock file" / the path in the golden
of a case generated before its mock existed), next to make_mocks.py's.
"""

import json
import os

root = "tests/llm/mocks"
G = "api.groq.com/openai/v1/chat/completions"


def groq(content):
    return {
        "id": "chatcmpl-review",
        "object": "chat.completion",
        "created": 1760000000,
        "model": "probe-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    }


def w(hash_, content):
    full = os.path.join(root, f"{G}-{hash_}-POST.json")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(groq(content), fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# "probe <x>" (type: items = array of objects)
w(
    "f3889b",  # probe types
    '{"items":[{"name":"a","count":2.0,"score":3,"flag":true,"kind":"x","tags":["t1","t2"]},'
    '{"name":"b","count":7,"score":1.5,"flag":false,"kind":"z","tags":[]}]}',
)
w(
    "36389c",  # probe nulls
    '{"items":[{"name":null,"count":null,"score":null,"flag":null,"kind":null,"tags":null}]}',
)
w("63a01b", "{}")  # probe missing
w(
    "a825ea",  # probe overflow
    '{"items":[{"name":"big","count":3000000000,"score":12345678901234567890},'
    '{"name":"frac","count":2.7,"score":-0.0},{"name":"min","count":-2147483648,"score":1e-320}]}',
)
w(
    "7a0249",  # probe strings
    '{"items":[{"name":5,"count":"3","score":"1.5","flag":"true","kind":1,"tags":[1,"a",true,null]}]}',
)
w("9ffb08", '{"items":{"name":"a","count":1}}')  # probe object
w("954aca", '{"items":[{"name":"a"')  # probe truncated
w("365cd7", '{"items":[]}')  # probe empty
w("82c1d8", '{"items":[{"name":"a","extra":1}],"other":"x"}')  # probe extra

# "single <x>" (type: one object)
w("4ae626", '{"name":"s","n":4,"tags":["x","y","q"],"sub":{"a":2.5}}')  # single full
w("9309f5", '{"name":null,"n":null,"tags":null,"sub":null}')  # single nulls
w("bff231", "{}")  # single missing
w("864f8f", '{"name":"t","n":1.0,"tags":[],"sub":{}}')  # single tags


def raw(path, obj):
    full = os.path.join(root, f"{path}.json")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def groq_msg(message):
    out = groq("")
    out["choices"][0]["message"] = message
    return out


EMPTY_CHOICES = {"id": "chatcmpl-review", "object": "chat.completion", "choices": []}
NO_CHOICES = {"id": "chatcmpl-review", "object": "chat.completion"}
NULL_CONTENT = groq_msg({"role": "assistant", "content": None})
PARTS = groq_msg({"role": "assistant", "content": [{"type": "text", "text": "hi"}]})
# "reply <x>" with the plain prompt, then with the structured type
for plain, structured, reply in (
    ("335358", "ecbe51", EMPTY_CHOICES),
    ("41b706", "c53eda", NULL_CONTENT),
    ("4bd6c1", "c99609", PARTS),
    ("63fc28", "cd8afd", NO_CHOICES),
):
    raw(f"{G}-{plain}-POST", reply)
    raw(f"{G}-{structured}-POST", reply)

GEM = "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-generateContent"
USAGE = {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}
BLOCKED = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": USAGE}
NO_CANDIDATES = {"candidates": [], "usageMetadata": USAGE}
TWO_PARTS = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": '{"name":"a","n":1}'}, {"text": '{"name":"b"}'}],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": USAGE,
}
for plain, structured, reply in (
    ("c3dc19", "48ce1c", BLOCKED),
    ("9f76ca", "4bd554", NO_CANDIDATES),
    ("08d0e3", "c3857c", TWO_PARTS),
):
    raw(f"{GEM}-{plain}-POST", reply)
    raw(f"{GEM}-{structured}-POST", reply)

# ---- model listings (models_*()) -----------------------------------------------------------
raw(
    "api.openai.com/v1/models",
    {
        "object": "list",
        "data": [
            {"id": "gpt-4.1-mini", "object": "model", "created": 1744316542, "owned_by": "system"},
            {"id": "gpt-4o", "object": "model", "created": 1715367049, "owned_by": "system"},
            {"id": "my-custom", "object": "model", "created": 1744300000, "owned_by": "user-x"},
            {
                "id": "gpt-4.1-mini-2025-04-14",
                "object": "model",
                "created": 1744315746,
                "owned_by": "system",
            },
        ],
    },
)
raw(
    "api.anthropic.com/v1/models",
    {
        "data": [
            {
                "type": "model",
                "id": "claude-3-5-haiku-20241022",
                "display_name": "Claude Haiku 3.5",
                "created_at": "2024-10-22T00:00:00Z",
            },
            {
                "type": "model",
                "id": "claude-sonnet-4-5-20250929",
                "display_name": "Claude Sonnet 4.5",
                "created_at": "2025-09-29T00:00:00Z",
            },
            {
                "type": "model",
                "id": "claude-sonnet-4-5",
                "display_name": "Claude Sonnet 4.5 (alias)",
                "created_at": "2025-09-29T18:30:00Z",
            },
            {
                "type": "model",
                "id": "claude-late",
                "display_name": "Later same day",
                "created_at": "2025-09-29T23:59:59Z",
            },
        ],
        "has_more": False,
        "first_id": "claude-3-5-haiku-20241022",
        "last_id": "claude-late",
    },
)
raw(
    "generativelanguage.googleapis.com/v1beta/models",
    {
        "models": [
            {
                "name": "models/gemini-2.5-flash-lite",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/Gemma-3-1b-it", "supportedGenerationMethods": ["generateContent"]},
            {
                "name": "models/gemini-2.5-flash-image",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    },
)
raw(
    "api.mistral.ai/v1/models",
    {
        "object": "list",
        "data": [
            {
                "id": "mistral-small-latest",
                "object": "model",
                "created": 1760000000,
                "owned_by": "mistralai",
                "name": "mistral-small-2506",
            },
            {
                "id": "open-mistral-nemo",
                "object": "model",
                "created": 1760000001,
                "owned_by": "mistralai",
                "name": "open-mistral-nemo",
            },
        ],
    },
)
raw(
    "api.deepseek.com/models",
    {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
        ],
    },
)
raw(
    "api.portkey.ai/v1/models",
    {
        "data": [
            {"id": "gpt-4o", "slug": "openai-gpt-4o"},
            {"id": "claude", "slug": "anthropic-claude"},
        ]
    },
)
raw(
    "localhost-1234/v1/models",
    {"object": "list", "data": [{"id": "qwen2.5-7b", "object": "model"}]},
)
raw("vllm.test/v1/models", {"object": "list", "data": [{"id": "GLM-5", "object": "model"}]})

# ---- type_from_schema(): raw JSON reaches .unnest_result() ------------------------------
w("f957d3", '{"a": 2, "tags": ["x", "y"]}')  # schema two
w("8234bb", '{"a": 1, "tags": ["z"]}')  # schema one
