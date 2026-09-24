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
