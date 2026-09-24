"""Tests for causal_relations() against a mocked Gradio Space (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from pytacheck.text.causal import causal_relations

BASE = "https://lakens-causal-sentences.hf.space/gradio_api/call/predict"


def _sse(payload: str) -> str:
    return f"event: generating\ndata: null\n\nevent: complete\ndata: {payload}\n\n"


def _mock(router: respx.Router, results: dict[str, str]) -> respx.Route:
    """POST returns an event id per sentence; GET streams its completion payload."""
    counter = {"n": 0}
    events: dict[str, str] = {}

    def post(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        counter["n"] += 1
        eid = f"ev{counter['n']}"
        events[eid] = results[body["data"][0]]
        return httpx.Response(200, json={"event_id": eid})

    route = router.post(BASE).mock(side_effect=post)
    router.get(url__regex=rf"{BASE}/ev\d+").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=_sse(events[request.url.path.rsplit("/", 1)[-1]]),
            headers={"content-type": "text/event-stream"},
        )
    )
    return route


def test_empty_input() -> None:
    for sentence in ([], ["", "   "]):
        out = causal_relations(sentence)
        assert list(out.columns) == ["sentence", "causal", "cause", "effect"]
        assert len(out) == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rel_mode": "fast"}, "rel_mode"),
        ({"rel_threshold": 2}, r"\[0, 1\]"),
        ({"cause_decision": "both"}, "cause_decision"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_validation(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        causal_relations("Smoking causes cancer.", **kwargs)


def test_relations_double_encoded_payload() -> None:
    final = json.dumps(
        [
            {
                "causal": True,
                "relations": [
                    {"cause": "smoking", "effect": "cancer"},
                    {"cause": "alcohol", "effect": "cancer"},
                ],
            }
        ]
    )
    results = {
        "Smoking and alcohol cause cancer.": json.dumps([final]),
        "Rain is wet.": json.dumps([json.dumps([{"causal": False, "relations": []}])]),
    }
    with respx.mock() as router:
        post = _mock(router, results)
        out = causal_relations(["Smoking and alcohol cause cancer.", "Rain is wet."])
    assert post.call_count == 2
    sent = json.loads(post.calls[0].request.content)
    assert sent == {"data": ["Smoking and alcohol cause cancer.", "auto", 0.5, "cls+span"]}
    assert out["sentence"].tolist() == [
        "Smoking and alcohol cause cancer.",
        "Smoking and alcohol cause cancer.",
        "Rain is wet.",
    ]
    # ordered by input sentence, then cause
    assert out["cause"].tolist()[:2] == ["alcohol", "smoking"]
    assert out["causal"].tolist() == [True, True, False]
    assert out["effect"].isna().tolist() == [False, False, True]


def test_relations_direct_payload() -> None:
    payload = json.dumps([{"causal": True, "relations": [{"cause": "stress", "effect": "bp"}]}])
    with respx.mock() as router:
        _mock(router, {"Stress raises bp.": payload})
        out = causal_relations("Stress raises bp.")
    assert out[["cause", "effect"]].values.tolist() == [["stress", "bp"]]


def test_post_failure() -> None:
    with respx.mock() as router:
        router.post(BASE).mock(return_value=httpx.Response(404, text="nope"))
        with pytest.raises(RuntimeError, match="POST failed"):
            causal_relations("x causes y")


def test_bad_payload() -> None:
    with respx.mock() as router:
        _mock(router, {"x causes y": '"not json"'})
        with pytest.raises(ValueError, match="Unexpected SSE payload"):
            causal_relations("x causes y")


def test_verbose_and_missing_complete(capsys: pytest.CaptureFixture[str]) -> None:
    with respx.mock() as router:
        router.post(BASE).mock(return_value=httpx.Response(200, json={"event_id": "e1"}))
        router.get(f"{BASE}/e1").mock(
            return_value=httpx.Response(200, text="event: generating\ndata: null\n\n")
        )
        with pytest.raises(TimeoutError, match="without `event: complete`"):
            causal_relations("x causes y", verbose=True)
    err = capsys.readouterr().err
    assert "[POST] https://lakens-causal-sentences.hf.space" in err
    assert "[SSE] event: generating" in err


def test_no_event_id() -> None:
    with respx.mock() as router:
        router.post(BASE).mock(return_value=httpx.Response(200, json={"other": 1}))
        with pytest.raises(RuntimeError, match="No `event_id`"):
            causal_relations("x causes y")
