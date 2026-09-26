"""Regression tests for the divergences found in the adversarial review of the llm port.

Each behaviour here was checked against R (parity/cases/llm_review.yaml holds the
R-generated cases); these tests pin them without needing R.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

import pytacheck.llm as L
from pytacheck import utils
from pytacheck.llm import cache, core, types
from pytacheck.llm import providers as P
from pytacheck.llm._rds import RInt, RList, RVec
from pytacheck.llm.providers import LLMError
from pytacheck.utils import local_options

# ---------------------------------------------------------------------------
# .onLoad() defaults
# ---------------------------------------------------------------------------


def test_onload_defaults_survive_local_options(restore_llm_options: None) -> None:
    """A local_options() block entered while an option is unset used to delete
    metacheck's .onLoad() default on exit, leaving llm_max_calls() None."""
    for name in ("metacheck.llm_max_calls", "metacheck.llm.use"):
        utils.options({name: None})
    with local_options({"metacheck.llm_max_calls": RInt(5), "metacheck.llm.use": True}):
        assert L.llm_max_calls() == 5
        assert L.llm_use() is True
    assert L.llm_max_calls() == 30
    assert L.llm_use() is False


# ---------------------------------------------------------------------------
# llm() input shapes
# ---------------------------------------------------------------------------


def test_text_frame_shapes() -> None:
    with pytest.raises(ValueError, match="'names' attribute"):
        core._text_frame(None, "text")  # data.frame(text = NULL)
    df = core._text_frame(np.array(["a", "b"]), "text")
    assert df["text"].tolist() == ["a", "b"]
    assert core._col_kind(df["text"]) == "chr"
    assert core._text_frame(pd.Series([3, 1], index=[9, 8]), "x")["x"].dtype == "Int64"
    assert core._text_frame(("a", "b"), "t")["t"].tolist() == ["a", "b"]
    assert core._text_frame(b"caf\xe9", "t")["t"].tolist() == ["caf\udce9"]


def test_object_dtype_text_column_is_character() -> None:
    s = pd.Series(["hello", None], dtype=object)
    assert core._col_kind(s) == "chr"
    assert core._col_kind(pd.Series([[1], [2]], dtype=object)) == "list"
    assert core._col_kind(pd.Series([None], dtype=object)) == "lgl"


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_llm_max_calls_outside_integer_range(restore_llm_options: None) -> None:
    with (
        pytest.warns(UserWarning, match="NAs introduced by coercion to integer range"),
        pytest.raises(ValueError, match="missing value where TRUE/FALSE needed"),
    ):
        L.llm_max_calls(1e10)
    assert L.llm_max_calls() == 30


def test_match_arg() -> None:
    choices = ["low", "medium", "high", "none"]
    assert core._r_match_arg("n", choices) == "none"
    assert core._r_match_arg(choices, choices) == "low"
    with pytest.raises(ValueError, match="'arg' must be of length 1"):
        core._r_match_arg(["low", "high"], choices)
    with pytest.raises(ValueError, match="should be one of"):
        core._r_match_arg("", choices)
    with pytest.raises(ValueError, match="must be NULL or a character vector"):
        core._r_match_arg(1, choices)


def test_llm_model_list_invalid_platforms_are_unique() -> None:
    with pytest.raises(ValueError, match=r"^Invalid platforms: bad, worse$"):
        L.llm_model_list(["bad", "bad", "worse"])


def test_llm_cache_clear_skips_hidden_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTACHECK_LLM_CACHE_DIR", str(tmp_path))
    (tmp_path / "a.rds").write_bytes(b"x")
    (tmp_path / ".hidden.rds").write_bytes(b"x")
    (tmp_path / "dir.rds").mkdir()
    assert L.llm_cache_clear() == 2  # list.files() counts the directory, unlink() keeps it
    assert sorted(p.name for p in tmp_path.iterdir()) == [".hidden.rds", "dir.rds"]


def test_cache_key_null_params() -> None:
    # R: .llm_cache_key("", "", NULL, "m", NULL) (params NULL, not list())
    assert cache._llm_cache_key("", "", None, "m", None) == "627bb1cc85d6fe8e6a1cef82ddc1b619"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def _err(body: object) -> str:
    e = LLMError("HTTP error", resp=httpx.Response(400, json=body))
    return core._llm_error_message(e)


def test_error_message_uses_r_dollar_semantics() -> None:
    assert _err({"errors": {"message": "partial"}}).endswith("Provider says: partial")
    assert _err({"error": {"messages": "inner"}}).endswith("Provider says: inner")
    assert _err({"error": ["a", "b"], "message": "top"}).endswith("Provider says: top")
    assert _err({"error": {"message": 42}}) == "HTTP error"
    assert core._r_dollar({"ab": 1, "ac": 2}, "a") is None  # ambiguous partial match


# ---------------------------------------------------------------------------
# chat(): provider/model names
# ---------------------------------------------------------------------------


def test_chat_name_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert P._r_strsplit_fixed("groq/", "/") == ["groq"]
    assert P._r_strsplit_fixed("groq/a//", "/") == ["groq", "a", ""]
    assert P.chat("groq/a//").model.name == "a/"
    assert P.chat("groq/").model.name == "openai/gpt-oss-20b"  # ellmer's default
    with pytest.raises(LLMError, match="the empty string"):
        P.chat("")
    with pytest.raises(LLMError) as e:
        P.chat("body/x")
    assert str(e.value) == (
        "`ellmer::chat()` does not support `ellmer::chat_body()`, please call it\ndirectly."
    )
    with pytest.raises(LLMError, match="`base_url` must be a single string"):
        P.chat("vllm/x")


def test_unsupported_cloud_providers_report_rs_argument_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    with pytest.raises(LLMError, match=r"^`location` must be a single string, not the empty"):
        P.chat("google_vertex/x")
    with pytest.raises(LLMError, match=r"^Can.t find env var `SNOWFLAKE_ACCOUNT`\.$"):
        P.chat("snowflake/x")


# ---------------------------------------------------------------------------
# structured output: ellmer's conversion and as.data.frame()
# ---------------------------------------------------------------------------


def test_list_to_atomic_integer_range() -> None:
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")
        out = types.list_to_atomic([3000000000, 2.7, -2147483648, 5], "integer")
    assert out.values == [None, 2, None, 5]
    assert [str(w.message) for w in ws] == ["NAs introduced by coercion to integer range"] * 2


def test_convert_keeps_list_columns() -> None:
    t = L.type_array(L.type_object(tags=L.type_array(L.type_string(), required=False)))
    tib = types.convert_from_type([{"tags": ["a", "b"]}, {"tags": None}, {"tags": []}], t)
    df = core._unnest_result({"items": tib})
    assert df.columns.tolist() == ["tags"]  # a one-column data frame argument keeps its name
    assert df["tags"].tolist() == [["a", "b"], None, []]


def test_subscript_semantics() -> None:
    t = L.type_object(name=L.type_string())
    assert types.convert_from_type([1, 2], t) == {"name": RVec("chr", [None])}  # list: NULL
    with pytest.raises(ValueError, match="subscript out of bounds"):
        types.convert_from_type("a string", t)  # atomic: error


def test_as_data_frame_r_rules() -> None:
    with pytest.raises(ValueError, match="differing number of rows: 1, 0"):
        core._as_data_frame({"a": 1, "b": {"c": None}})
    df = core._as_data_frame({"a": 1, "tags": RList(["x", "y"])})
    assert df.columns.tolist() == ["a", "tags..x.", "tags..y."]
    assert core._make_names(['"x"', "1a", ".5", "if", "a b"]) == [
        "X.x.",
        "X1a",
        "X.5",
        "if.",
        "a.b",
    ]
    assert core._deparse_vec(RVec("int", [6, 7])) == "6:7"
    assert core._deparse_vec(RVec("chr", ["a", None])) == 'c("a", NA)'
    assert core._deparse_vec(RVec("dbl", [None])) == "NA_real_"


# ---------------------------------------------------------------------------
# provider request bodies and replies
# ---------------------------------------------------------------------------


def test_schema_compact_rules() -> None:
    t = L.type_object(a=L.type_string(required=False))
    assert "required" not in types.type_as_json(t, "gemini")
    assert types.type_as_json(L.type_object(), "generic")["properties"] == {}


def test_openai_compatible_reply_edges() -> None:
    prov = P.ProviderGroq(base_url="https://api.groq.com/openai/v1", credentials=lambda: "k")
    model = P.Model("m", {})
    with pytest.raises(LLMError, match="subscript out of bounds"):
        prov.value_turn(model, {"choices": []}, False)
    parts = {"choices": [{"message": {"content": [{"type": "text", "text": "hi"}]}}]}
    with pytest.raises(LLMError, match="must be made up strings or <content> objects, not a list"):
        prov.value_turn(model, parts, False)
    assert prov.value_turn(model, {"id": "x"}, False).text == ""


def test_openrouter_drops_empty_text_content() -> None:
    prov = P.ProviderOpenRouter(base_url="https://openrouter.ai/api/v1", credentials=lambda: "k")
    msgs = prov.turn_json(P.Turn("user", [P.ContentText("")]))
    assert msgs == [{"role": "user", "content": []}]


# ---------------------------------------------------------------------------
# model listings
# ---------------------------------------------------------------------------


def test_match_prices() -> None:
    prices = P.match_prices("OpenAI", ["gpt-4o", "no-such-model"])
    assert prices["input"].tolist()[0] == 2.5
    assert np.isnan(prices["input"].tolist()[1])


@respx.mock
def test_models_anthropic_reads_dates_like_r(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    data = [
        {"id": "a", "display_name": "A", "created_at": "2025-09-29T00:00:00Z"},
        {"id": "b", "display_name": "B", "created_at": "2025-09-29T23:00:00Z"},
        {"id": "c", "display_name": "C", "created_at": "2024-01-01T00:00:00Z"},
    ]
    respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": data})
    )
    df = P.models_anthropic()
    assert df["id"].tolist() == ["a", "b", "c"]  # same day: API order kept
    assert df.columns.tolist() == ["id", "name", "created_at", "cached_input", "input", "output"]


def test_parse_r_datetime_uses_local_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    if hasattr(time, "tzset"):  # POSIX; elsewhere TZ is read on each call
        time.tzset()
    try:
        t = P._parse_r_datetime("2025-09-29T18:30:00Z")
        assert t.timestamp() == 1759118400  # R: as.numeric(as.POSIXct(...)) in that zone
    finally:
        monkeypatch.undo()
        if hasattr(time, "tzset"):
            time.tzset()


@respx.mock
def test_local_providers_missing_vs_null_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """``chat_lmstudio()`` lacks a model; ``chat("lmstudio")`` passes ``model = NULL``."""
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    respx.get("http://localhost:1234/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
    )
    with pytest.raises(LLMError) as e:
        P.chat_lmstudio()
    assert str(e.value) == 'Must specify `model`.\nℹ Locally available models: "a" and "b".'
    with pytest.raises(LLMError, match=r"^argument is of length zero$"):
        P.chat("lmstudio")
    with pytest.raises(LLMError, match="Download the model using the LM Studio GUI"):
        P.chat("lmstudio/c")
