"""Writes the hand-made provider replies in tests/llm/mocks/ (run from the repo root).

Each file is a reply a provider could send, stored under the httptest2 mock path
of the request metacheck's llm() makes (R computes the path: run the parity case
without the fixture and read "Expected mock file" from its golden). Errors are
httr2 response objects (.R files).
"""

import json
import os

root = "tests/llm/mocks"


def w(path, obj):
    full = os.path.join(root, path + ".json")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def werr(path, url, status, obj):
    full = os.path.join(root, path + ".R")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    hexes = ", ".join(f"0x{b:02x}" for b in raw)
    txt = (
        f'structure(list(method = "POST", url = "{url}", \n'
        f'    status_code = {status}L, headers = structure(list(`content-type` = "application/json"), class = "httr2_headers"), \n'
        f"    body = as.raw(c({hexes})), \n"
        f'    cache = new.env(parent = emptyenv())), class = "httr2_response")\n'
    )
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(txt)


G = "api.groq.com/openai/v1/chat/completions"
GURL = "https://api.groq.com/openai/v1/chat/completions"


def groq(content, reasoning=None, model="test-model"):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1760000000,
        "model": model,
        "choices": [{"index": 0, "message": msg, "logprobs": None, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    }


w(G + "-80bd23-POST", groq("NA answer"))
w(G + "-50df8b-POST", groq("TRUE"))
w(G + "-ff894d-POST", groq("TRUE"))
werr(
    G + "-690641-POST",
    GURL,
    401,
    {
        "error": {
            "message": "Invalid API Key",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    },
)
werr(
    G + "-d76349-POST",
    GURL,
    400,
    {
        "error": {
            "message": "Please reduce the length of the messages or completion.",
            "type": "invalid_request_error",
            "param": "messages",
            "code": "context_length_exceeded",
        }
    },
)
two = {
    "variables": [
        {"variable_name": "dv", "label": "outcome", "kind": "a", "n": 3, "ok": True},
        {"variable_name": "iv", "label": None, "kind": None, "n": None, "ok": False},
    ]
}
w(
    G + "-189f2e-POST",
    groq(json.dumps(two), reasoning="Found two variables: dv and iv.", model="openai/gpt-oss-20b"),
)
w(
    G + "-b9a3b2-POST",
    groq('{"variables": []}', reasoning="Nothing to extract.", model="openai/gpt-oss-20b"),
)
w(G + "-27f076-POST", groq('{"variables":[]}', model="openai/gpt-oss-20b"))
werr(
    G + "-3c015b-POST",
    GURL,
    400,
    {
        "error": {
            "message": "Failed to validate JSON. Please adjust your prompt. See 'failed_generation' for more details.",
            "type": "invalid_request_error",
            "code": "json_validate_failed",
            "failed_generation": '{"variables": "oops"}',
        }
    },
)
w(G + "-1ff8f1-POST", groq('```json\n{"variables": []}\n```', model="openai/gpt-oss-20b"))
w(
    G + "-81e1a0-POST",
    groq(
        json.dumps({"tags": ["x", "z", "q"], "meta": {"a": 2, "b": "hello"}, "text": "extracted"})
    ),
)
w(G + "-1e91ce-POST", groq(json.dumps({"wrapper": ["alpha", "beta"]})))
w(G + "-5a2a67-POST", groq("  Hi there  "))
GE = "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-generateContent"


def gem(parts):
    return {
        "candidates": [
            {"content": {"parts": parts, "role": "model"}, "finishReason": "STOP", "index": 0}
        ],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 1, "totalTokenCount": 13},
        "modelVersion": "gemini-2.5-flash",
        "responseId": "resp-test",
    }


w(GE + "-af9b10-POST", gem([{"text": "TRUE"}]))
w(GE + "-854bfb-POST", gem([{"text": "B is a consonant.", "thought": True}, {"text": "FALSE\n"}]))
w(
    GE + "-8b0dc6-POST",
    gem(
        [
            {"text": "I considered the variables.", "thought": True},
            {
                "text": json.dumps(
                    {
                        "variables": [
                            {
                                "variable_name": "dv",
                                "label": "outcome",
                                "kind": "b",
                                "n": 1,
                                "ok": True,
                            }
                        ]
                    }
                )
            },
        ]
    ),
)
A = "api.anthropic.com/v1/messages"


def anth(content, model):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 10},
    }


w(A + "-d8359d-POST", anth([{"type": "text", "text": "Hello!"}], "claude-sonnet-4-5"))
w(
    A + "-f4c1ba-POST",
    anth(
        [
            {"type": "thinking", "thinking": "Let me look.", "signature": "sig"},
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "variables": [
                            {
                                "variable_name": "dv",
                                "label": "outcome",
                                "kind": "a",
                                "n": 2,
                                "ok": True,
                            }
                        ]
                    }
                ),
            },
        ],
        "claude-sonnet-4-5",
    ),
)
w(
    A + "-0beae8-POST",
    anth(
        [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": "_structured_tool_call",
                "input": {
                    "data": {
                        "variables": [
                            {
                                "variable_name": "x1",
                                "label": "first",
                                "kind": "b",
                                "n": 5,
                                "ok": False,
                            }
                        ]
                    }
                },
            }
        ],
        "claude-3-5-haiku-latest",
    ),
)
OPENAI = "api.openai.com/v1/responses"


def oai(output):
    return {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "model": "test",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


w(
    OPENAI + "-d5b011-POST",
    oai(
        [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello from OpenAI", "annotations": []}
                ],
            }
        ]
    ),
)
w(
    OPENAI + "-e1a8ff-POST",
    oai(
        [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [
                    {"type": "summary_text", "text": "Summarised "},
                    {"type": "summary_text", "text": "reasoning."},
                ],
            },
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "variables": [
                                    {
                                        "variable_name": "dv",
                                        "label": None,
                                        "kind": None,
                                        "n": None,
                                        "ok": True,
                                    }
                                ]
                            }
                        ),
                        "annotations": [],
                    }
                ],
            },
        ]
    ),
)
w("api.mistral.ai/v1/chat/completions-f25595-POST", groq("Bonjour", model="mistral-small-latest"))
print("done")
w(G + "-79d99c-POST", groq("Hi!"))
# llm(c("hello", "12"), "Is this a number? Answer TRUE or FALSE", model = "groq/test-model")
w(G + "-cea654-POST", groq("  FALSE\n"))
w(G + "-9661ae-POST", groq("TRUE", reasoning="12 is digits"))
