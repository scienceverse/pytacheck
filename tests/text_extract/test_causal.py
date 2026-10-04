"""Tests for causal_relations() against a mocked Gradio Space (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from metacheck.text.causal import causal_relations

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


@pytest.mark.parametrize(
    ("value", "text"),
    [
        # jsonlite::toJSON(digits = 4): modp_dtoa2() in (1e-5, 2^31), "%.<n>g" outside
        (0.5, "0.5"),
        (1.0, "1"),
        (1, "1"),
        (0.0, "0"),
        (0.12345, "0.1234"),
        (0.12355, "0.1236"),
        (0.123456789, "0.1235"),
        (0.00049, "0.0005"),
        (0.00005, "0"),
        (0.99999, "1"),
        (1e-5, "1e-05"),
        (1e-10, "1e-10"),
    ],
)
def test_json_number(value: float, text: str) -> None:
    from metacheck.text.causal import _json_number

    assert _json_number(value) == text


def test_request_body_threshold() -> None:
    bodies: list[bytes] = []
    with respx.mock(assert_all_called=False) as router:
        route = _mock(router, {"x": '["[{\\"causal\\": false, \\"relations\\": []}]"]'})
        route.side_effect = (
            lambda f: lambda request: (bodies.append(request.content), f(request))[1]
        )(route.side_effect)
        causal_relations("x", rel_threshold=0.12345)
    assert bodies == [b'{"data":["x","auto",0.1234,"cls+span"]}']


def test_missing_sentences() -> None:
    # R: all(trimws(sentence) == "") is NA -> if() fails
    for sentence in ([None], [None, "  "]):
        with pytest.raises(ValueError, match="missing value"):
            causal_relations(sentence)


def test_missing_sentence_is_sent_as_null() -> None:
    payload = '["[{\\"causal\\": true, \\"relations\\": [{\\"cause\\": \\"a\\", \\"effect\\": \\"b\\"}]}]"]'
    with respx.mock(assert_all_called=False) as router:
        _mock(router, {None: payload, "x": payload})
        out = causal_relations([None, "x"])
    assert out["sentence"].isna().tolist() == [True, False]
    assert out["cause"].tolist() == ["a", "a"]


def test_requests_name_this_package_as_user_agent() -> None:
    from metacheck._version import __version__

    seen: list[str] = []
    payload = '["[{\\"causal\\": false, \\"relations\\": []}]"]'
    with respx.mock(assert_all_called=False) as router:
        route = _mock(router, {"x": payload})
        post = route.side_effect
        route.side_effect = lambda request: (
            seen.append(request.headers["user-agent"]),
            post(request),
        )[1]
        router.get(url__regex=rf"{BASE}/ev\d+").mock(
            side_effect=lambda request: (
                seen.append(request.headers["user-agent"]),
                httpx.Response(200, text=_sse(payload)),
            )[1]
        )
        causal_relations("x")
    assert seen == [f"metacheck/{__version__}"] * 2
