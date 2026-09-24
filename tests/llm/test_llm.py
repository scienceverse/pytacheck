"""Port of metacheck's tests/testthat/test-llm.R (plus pytacheck-specific checks)."""

from __future__ import annotations

import warnings
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

import pytacheck.llm as L
from pytacheck.llm import core, providers
from pytacheck.llm.providers import LLMError
from pytacheck.utils import get_option, local_options, options
from tests.httpmock import replay
from tests.llm.support import MOCKS, FakeChat

# ---------------------------------------------------------------------------
# argument checking (no network)
# ---------------------------------------------------------------------------


def test_llm_argument_errors(llm_on: Path) -> None:
    assert callable(L.llm)
    with pytest.raises(TypeError):
        L.llm()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        L.llm("hi")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="`top_p` must be a number"):
        L.llm("hi", "repeat this", model="groq/x", params={"top_p": "a"})
    with pytest.raises(ValueError, match="`top_p` must be a number"):
        L.llm("hi", "repeat this", model="groq/x", params={"top_p": -3})
    L.llm_use(False)
    with pytest.raises(RuntimeError, match=r"llm_use\(TRUE\)"):
        L.llm("hi", "repeat this", model="groq")


def test_llm_fails_fast_when_model_unset_or_params_malformed(llm_on: Path) -> None:
    with local_options({"metacheck.llm.model": None}):
        with pytest.raises(ValueError, match="No LLM model set"):
            L.llm("hi", "repeat this")
    with pytest.raises(ValueError, match="params must be a named list"):
        L.llm("hi", "repeat this", model="groq/llama-3.1-8b-instant", params=1)  # type: ignore[arg-type]


def test_misspecified_params_message(llm_on: Path) -> None:
    with pytest.raises(ValueError) as err:
        L.llm("hi", "s", model="groq/x", params={"max_tokens": 0})
    assert str(err.value) == (
        "Misspecified params argument:\n`max_tokens` must be a whole number larger than or "
        "equal to 1 or `NULL`, not the number 0."
    )


def test_max_calls(llm_on: Path, restore_llm_options: None) -> None:
    n = L.llm_max_calls()
    assert isinstance(n, int)
    assert n > 0
    assert n == get_option("metacheck.llm_max_calls")
    with pytest.raises(ValueError, match="n must be a number"):
        L.llm_max_calls("a")
    assert get_option("metacheck.llm_max_calls") == n
    with pytest.warns(UserWarning, match="n must be greater than 0"):
        L.llm_max_calls(0)
    assert get_option("metacheck.llm_max_calls") == n
    L.llm_max_calls(8)
    assert get_option("metacheck.llm_max_calls") == 8
    text = pd.DataFrame({"text": range(1, 21), "id": range(1, 21)})
    with pytest.raises(ValueError, match="This would make 20 calls to the LLM"):
        L.llm(text, "summarise", model="groq/llama-3.1-8b-instant")
    L.llm_max_calls(n)
    assert L.llm_max_calls() == n


def test_no_calls(llm_on: Path) -> None:
    with pytest.raises(ValueError, match="No calls to the LLM"):
        L.llm(pd.DataFrame({"x": ["a"]}), "s", model="groq/x")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_llm_error_message_surfaces_provider_body() -> None:
    resp = httpx.Response(
        400, json={"error": {"message": "Please reduce the length of the messages."}}
    )
    e = LLMError("HTTP 400 Bad Request.", resp=resp)
    msg = core._llm_error_message(e)
    assert "HTTP 400 Bad Request" in msg
    assert "Please reduce the length of the messages" in msg
    # the response on the parent condition (ellmer-style wrapping)
    wrapper = LLMError("Failed to call chat API.", parent=e)
    assert "Please reduce the length of the messages" in core._llm_error_message(wrapper)
    # no response attached: the original message comes back unchanged
    assert core._llm_error_message(LLMError("boom")) == "boom"
    assert core._llm_error_message(ValueError("plain")) == "plain"


def test_llm_error_message_truncates_long_bodies() -> None:
    resp = httpx.Response(500, content=b"x" * 600, headers={"content-type": "text/plain"})
    msg = core._llm_error_message(LLMError("HTTP 500 Internal Server Error.", resp=resp))
    assert msg.endswith("x" * 500 + " [truncated]")


def test_llm_json_retryable() -> None:
    assert core._llm_json_retryable(LLMError("Failed to generate JSON"))
    assert core._llm_json_retryable(
        LLMError('lexical error: invalid char in json text. ```json { "studies": [] }')
    )
    assert not core._llm_json_retryable(LLMError("HTTP 401 Unauthorized"))


def test_llm_is_systemic_error() -> None:
    assert core._llm_is_systemic_error(LLMError("x", resp=httpx.Response(401)))
    assert core._llm_is_systemic_error(LLMError("x", resp=httpx.Response(503)))
    assert not core._llm_is_systemic_error(LLMError("x", resp=httpx.Response(400)))
    assert core._llm_is_systemic_error(LLMError("Could not resolve host: api.groq.com"))
    assert not core._llm_is_systemic_error(LLMError("Operation timed out", timeout=True))
    assert not core._llm_is_systemic_error(LLMError("parse error: premature EOF"))
    assert not core._llm_is_systemic_error(LLMError("boom"))


def test_systemic_notice_once(capsys: pytest.CaptureFixture[str]) -> None:
    notice = core._SystemicNotice()
    notice.trip("first")
    notice.trip("second")
    err = capsys.readouterr().err
    assert "first" in err
    assert "second" not in err
    notice.reset()
    notice.trip("third")
    assert "third" in capsys.readouterr().err


def test_sanitise_text() -> None:
    out = core._llm_sanitise_text(["a\x01b", "tab\tnew\nline\r", None, 12.5])
    assert out == ["ab", "tab\tnew\nline\r", None, "12.5"]
    # invalid UTF-8 is reinterpreted as Latin-1
    assert core._llm_sanitise_text([b"caf\xe9"]) == ["café"]
    assert core._llm_sanitise_text(None) is None
    assert core._llm_sanitise_text([]) == []


def test_apply_reasoning_defaults_to_low(restore_llm_options: None) -> None:
    options({"metacheck.llm_reasoning": None})
    out = core._llm_apply_reasoning({}, "groq/openai/gpt-oss-20b")
    assert out == {"params_list": {}, "api_args": {"reasoning_effort": "low"}}
    L.llm_reasoning("none")
    assert core._llm_apply_reasoning({}, "groq/openai/gpt-oss-20b")["api_args"] == {
        "reasoning_effort": "low"
    }
    assert core._llm_apply_reasoning({}, "groq/qwen/qwen3-32b")["api_args"] == {
        "reasoning_effort": "none"
    }
    assert core._llm_apply_reasoning({}, "ollama/qwen3:8b")["params_list"] == {"think": False}
    assert core._llm_apply_reasoning({}, "groq/llama") == {"params_list": {}, "api_args": {}}


# ---------------------------------------------------------------------------
# vllm routing
# ---------------------------------------------------------------------------


def test_llm_routes_vllm_through_chat_vllm(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_chat_vllm(**kwargs: object) -> FakeChat:
        seen.update(kwargs)
        seen["token"] = kwargs["credentials"]()  # type: ignore[operator]
        return FakeChat(chat=lambda text: "TRUE")

    monkeypatch.setattr(providers, "chat_vllm", fake_chat_vllm)
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    with local_options({"metacheck.llm.vllm.base_url": "https://example.test/v1"}):
        out = L.llm("hello", "Answer TRUE", model="vllm/GLM-5.2-NVFP4")
    assert seen["model"] == "GLM-5.2-NVFP4"
    assert seen["base_url"] == "https://example.test/v1"
    assert seen["token"] == "test-key"
    assert out["answer"].iloc[0] == "TRUE"


def test_llm_reports_clear_error_when_vllm_base_url_missing(llm_on: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = L.llm("hello", "Answer TRUE", model="vllm/GLM-5.2-NVFP4")
    assert bool(out["error"].iloc[0]) is True
    assert "metacheck.llm.vllm.base_url" in out["error_msg"].iloc[0]


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_llm_use(restore_llm_options: None) -> None:
    with pytest.raises(ValueError, match="TRUE or FALSE"):
        L.llm_use("no")
    assert L.llm_use(True) is True
    assert L.llm_use() is True
    assert L.llm_use(False) is False
    assert L.llm_use() is False


def test_llm_model(restore_llm_options: None) -> None:
    orig = L.llm_model()
    with pytest.raises(ValueError, match="set llm_model with the name of a model"):
        L.llm_model(True)
    assert L.llm_model() == orig
    L.llm_model("groq/llama-3.1-8b-instant")
    assert L.llm_model() == "groq/llama-3.1-8b-instant"
    L.llm_model(None)
    assert L.llm_model() is None
    L.llm_model(orig)
    assert L.llm_model() == orig


def test_llm_max_tokens_timeout_reasoning(restore_llm_options: None) -> None:
    options({"metacheck.llm_max_tokens": None, "metacheck.llm_timeout": None})
    assert L.llm_max_tokens() is None
    assert L.llm_max_tokens(8192) == 8192
    assert L.llm_timeout() == 180
    assert L.llm_timeout(60) == 60
    with pytest.raises(ValueError, match="single positive number"):
        L.llm_timeout(0)
    assert L.llm_reasoning("hi") == "high"
    with pytest.raises(ValueError, match="should be one of"):
        L.llm_reasoning("max")


def test_default_model_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, env in core._API_KEY_ENV:
        monkeypatch.delenv(env, raising=False)
    assert core._default_model_from_env() is None
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert core._default_model_from_env() == "google_gemini"
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert core._default_model_from_env() == "groq"


# ---------------------------------------------------------------------------
# llm() with a mocked chat (R: local_mocked_bindings(chat = ...))
# ---------------------------------------------------------------------------


def test_llm_warns_on_unrecognised_provider(llm_on: Path) -> None:
    with pytest.warns(UserWarning, match="Can't find provider"):
        L.llm("hi", "repeat this", model="not a model")


def test_llm_use_true_with_mocked_chat(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def is_num(text: str) -> str:
        try:
            float(text)
        except ValueError:
            return "FALSE"
        return "TRUE"

    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat=is_num))
    text = ["hello", "number", "ten", "12"]
    out = L.llm(text, "Is this a number? Answer only 'TRUE' or 'FALSE'", model="groq/x")
    assert out["text"].tolist() == text
    assert out["answer"].iloc[0] == "FALSE"
    assert out["answer"].iloc[3] == "TRUE"

    # duplicates generate one call per unique text
    calls = []

    def letter(text: str) -> str:
        calls.append(text)
        return "TRUE" if text.isalpha() and len(text) == 1 else "FALSE"

    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat=letter))
    text = ["A", "A", "1", "1"]
    out = L.llm(text, "Is this a letter A-Z? Answer only 'TRUE' or 'FALSE'", model="groq/x")
    assert out["text"].tolist() == text
    assert out["answer"].iloc[0] == out["answer"].iloc[1]
    assert out["answer"].iloc[2] == out["answer"].iloc[3]
    assert len(calls) == 2


def test_gemini_with_mocked_chat(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        providers,
        "chat",
        lambda *a, **k: FakeChat(chat=lambda t: "TRUE" if t in "AEIOU" else "FALSE"),
    )
    out = L.llm(
        ["A", "B"], "Is this a vowel? Answer only 'TRUE' or 'FALSE'.", model="google_gemini"
    )
    assert out["answer"].tolist() == ["TRUE", "FALSE"]


def test_llm_handles_an_empty_structured_result(
    llm_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = L.type_object(
        variables=L.type_array(L.type_object(variable_name=L.type_string(), label=L.type_string()))
    )
    monkeypatch.setattr(
        providers,
        "chat",
        lambda *a, **k: FakeChat(chat_structured=lambda text, type: {"variables": []}),
    )
    res = L.llm(pd.DataFrame({"text": ["a", "b"]}), "x", type=ts, text_col="text", model="groq/x")
    assert len(res) == 2
    assert "variable_name" not in res.columns or res["variable_name"].isna().all()

    n = {"calls": 0}

    def mixed(text: str, type: object) -> dict[str, list[dict[str, str]]]:
        n["calls"] += 1
        if n["calls"] == 1:
            return {"variables": [{"variable_name": "dv", "label": "outcome"}]}
        return {"variables": []}

    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat_structured=mixed))
    res2 = L.llm(
        pd.DataFrame({"text": ["has", "empty"]}), "x", type=ts, text_col="text", model="groq/x"
    )
    assert res2["variable_name"].tolist()[0] == "dv"
    assert pd.isna(res2["variable_name"].tolist()[1])


def test_structured_retries_malformed_json(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def flaky(text: str, type: object) -> dict[str, str]:
        attempts.append(text)
        if len(attempts) < 3:
            raise LLMError("HTTP 400 Bad Request.\nℹ Failed to generate JSON")
        return {"a": "ok"}

    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat_structured=flaky))
    out = L.llm("t", "s", type=L.type_object(a=L.type_string()), model="groq/x")
    assert len(attempts) == 3
    assert out["a"].tolist() == ["ok"]


def test_llm_result_attributes(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat=lambda t: " x "))
    out = L.llm("t", "the prompt", model="groq/x")
    assert out["answer"].tolist() == ["x"]
    assert out.attrs["llm"] == {"system_prompt": "the prompt", "model": "groq/x", "type": None}
    assert out.attrs["class"] == ["metacheck_llm", "data.frame"]


def test_llm_parallel_workers_keep_order(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "chat", lambda *a, **k: FakeChat(chat=lambda t: t.upper()))
    texts = [f"t{i}" for i in range(12)]
    with local_options({"pytacheck.llm.workers": 4}):
        out = L.llm(texts, "s", model="groq/x")
    assert out["answer"].tolist() == [t.upper() for t in texts]


# ---------------------------------------------------------------------------
# recorded responses
# ---------------------------------------------------------------------------


def test_llm_model_list(upstream_dir: Path) -> None:
    with pytest.raises(ValueError, match="Invalid platform"):
        L.llm_model_list("notamodel")
    with replay("apis"):
        o = L.llm_model_list("ollama")
    assert len(o) == 2
    assert o.columns.tolist() == ["platform", "id", "created_at", "size", "capabilities"]
    assert o["capabilities"].tolist() == ["completion,tools", "completion"]
    # without internet: every request fails, the platform is skipped
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectError("no internet"))
        o = L.llm_model_list("ollama")
    assert len(o) == 0


def test_llm_model_list_groq(upstream_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    import pytacheck.utils

    monkeypatch.setattr(pytacheck.utils, "online", lambda *a, **k: True)
    with pytest.raises(TypeError):
        core._llm_model_list_groq(1)  # type: ignore[call-arg]
    with replay("apis"):
        g1 = core._llm_model_list_groq()
        g2 = L.llm_model_list("groq")
    assert "platform" in g2.columns
    assert "platform" not in g1.columns
    assert set(g1["id"]) == set(g2["id"])
    assert all(isinstance(d, dt.date) for d in g1["created_at"])
    assert not g1["id"].str.contains("whisper|vision").any()


def test_llm_ollama_native(upstream_dir: Path, llm_on: Path) -> None:
    system_prompt = "Is this a vowel? Answer only 'TRUE' or 'FALSE'."
    with replay("apis"):
        resp = core._llm_ollama_native("A", system_prompt, "qwen2.5:3b")
        assert resp in ("TRUE", "FALSE")
        resp2 = L.llm("A", system_prompt, model="ollama/qwen2.5:3b")
        resp3 = L.llm("A", system_prompt, model="ollama")
        assert resp2.columns.tolist() == ["text", "answer"]
        assert resp3.columns.tolist() == ["text", "answer"]
        with pytest.raises(LLMError, match="HTTP 404 Not Found"):
            core._llm_ollama_native("A", system_prompt, "notamodel")
        with pytest.raises(
            RuntimeError, match="Ollama is installed, but the model notamodel is not available"
        ):
            L.llm("A", system_prompt, model="ollama/notamodel")


def test_ollama_not_running(llm_on: Path) -> None:
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(RuntimeError, match="Ollama is not running at http://localhost:11434"):
            L.llm("A", "s", model="ollama/x")


def test_unnest_result() -> None:
    with pytest.raises(NameError):
        core._unnest_result(bad_arg)  # type: ignore[name-defined]  # noqa: F821
    df = core._unnest_result({"n_letters": 5, "is_number": False})
    assert df.columns.tolist() == ["n_letters", "is_number"]
    assert df["n_letters"].tolist() == [5]
    assert df["is_number"].tolist() == [False]


def test_llm_groq_recorded(llm_on: Path) -> None:
    """Hand-recorded Groq replies (the fixtures the parity cases use)."""
    with replay(MOCKS):
        out = L.llm(
            ["hello", "12", "hello"],
            "Is this a number? Answer TRUE or FALSE",
            model="groq/test-model",
        )
    assert out["answer"].tolist() == ["FALSE", "TRUE", "FALSE"]


def test_llm_error_row_and_systemic_notice(
    llm_on: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    core._llm_systemic_notice.reset()
    with (
        replay(MOCKS),
        pytest.warns(UserWarning, match="There were errors in the following rows: 1"),
    ):
        out = L.llm("unauthorized", "Sys", model="groq/test-model")
    assert out["error_msg"].iloc[0] == (
        "HTTP 401 Unauthorized.\nℹ Invalid API Key\n  Provider says: Invalid API Key"
    )
    assert "LLM appears unavailable this run" in capsys.readouterr().err


def test_llm_mixed_success_and_error_fails_like_r(llm_on: Path) -> None:
    """R's bind_rows() cannot combine an ellmer_output answer with a failed row."""
    with replay(MOCKS), pytest.raises(TypeError, match="Can't combine"):
        L.llm(["hello", "unauthorized"], "Sys", model="groq/test-model")


def test_llm_structured_recorded(llm_on: Path) -> None:
    ts = L.type_object(
        variables=L.type_array(
            L.type_object(
                variable_name=L.type_string("Exact variable name/code in the data file"),
                label=L.type_string("Verbatim description text from the codebook", required=False),
                kind=L.type_enum(["a", "b", None], "Kind", required=False),
            )
        )
    )
    with replay(MOCKS), pytest.warns(UserWarning, match="1 of 6 LLM extractions failed"):
        out = L.llm(
            ["two vars", "none", "bad schema", "two vars"],
            "Extract variables",
            type=ts,
            model="groq/openai/gpt-oss-20b",
        )
    assert out.columns.tolist() == [
        "text",
        "variables.variable_name",
        "variables.label",
        "variables.kind",
        ".error",
        ".error_msg",
    ]
    assert out["variables.variable_name"].tolist()[:2] == ["dv", "iv"]
    assert isinstance(out["variables.kind"].dtype, pd.CategoricalDtype)
    assert bool(out[".error"].iloc[3]) is True
