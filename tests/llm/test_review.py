"""Regression tests for the R behaviours of llm()'s inputs and output frame.

Each behaviour here was checked against R (parity/cases/llm_review.yaml holds the
R-generated cases); these tests pin them without needing R.
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck.llm as L
from metacheck import utils
from metacheck.llm import _backend, cache, core, types
from metacheck.llm._backend import LLMError
from metacheck.utils import local_options

# ---------------------------------------------------------------------------
# .onLoad() defaults
# ---------------------------------------------------------------------------


def test_onload_defaults_survive_local_options(restore_llm_options: None) -> None:
    """A local_options() block entered while an option is unset used to delete
    metacheck's .onLoad() default on exit, leaving llm_max_calls() None."""
    for name in ("metacheck.llm_max_calls", "metacheck.llm.use"):
        utils.options({name: None})
    with local_options({"metacheck.llm_max_calls": 5, "metacheck.llm.use": True}):
        assert L.llm_max_calls() == 5
        assert L.llm_use() is True
    assert L.llm_max_calls() == 30
    assert L.llm_use() is False


# ---------------------------------------------------------------------------
# llm() input shapes
# ---------------------------------------------------------------------------


def test_text_frame_shapes() -> None:
    import numpy as np

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


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_error_message_shows_the_providers_reason() -> None:
    err = LLMError("HTTP 400 Bad Request.", status=400, detail="the reason")
    assert core._llm_error_message(err) == "HTTP 400 Bad Request.\n  Provider says: the reason"
    assert core._llm_error_message(LLMError("HTTP 400 Bad Request.", status=400)) == (
        "HTTP 400 Bad Request."
    )
    long = LLMError("HTTP 400 Bad Request.", detail="x" * 600)
    assert core._llm_error_message(long).endswith("x" * 500 + " [truncated]")
    assert core._llm_error_message(RuntimeError("plain")) == "plain"


@pytest.mark.parametrize(
    ("body", "detail", "kind"),
    [
        ('{"error": {"message": "inner"}}', "inner", None),
        ('{"error": "flat"}', "flat", None),
        ('{"message": "top"}', "top", None),
        (
            '{"type": "error", "error": {"type": "overloaded_error", "message": "m"}}',
            "m",
            "overloaded_error",
        ),
        ("plain text", "plain text", None),
    ],
)
def test_body_detail(body: str, detail: str, kind: str | None) -> None:
    assert _backend._body_detail(body) == (detail, kind)


# ---------------------------------------------------------------------------
# structured output: the conversion and as.data.frame()
# ---------------------------------------------------------------------------


def test_convert_keeps_list_columns() -> None:
    t = L.type_array(L.type_object(tags=L.type_array(L.type_string(), required=False)))
    tib = types.convert_from_type([{"tags": ["a", "b"]}, {"tags": None}, {"tags": []}], t)
    df = core._unnest_result({"items": tib})
    assert df.columns.tolist() == ["tags"]  # a one-column data frame argument keeps its name
    assert df["tags"].tolist() == [["a", "b"], None, []]


def test_optional_properties_of_a_json_schema_dict_stay_null() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id"],
                },
            }
        },
        "required": ["rows"],
    }
    out = types.convert_from_type({"rows": [{"id": 1, "tags": ["a"]}, {"id": 2}]}, schema)
    assert out["rows"]["tags"].tolist() == [["a"], None]


def test_subscript_semantics() -> None:
    t = L.type_object(name=L.type_string())
    assert types.convert_from_type([1, 2], t)["name"].isna().tolist() == [True]  # list: NULL
    with pytest.raises(ValueError, match="subscript out of bounds"):
        types.convert_from_type("a string", t)  # atomic: error


def test_as_data_frame_r_rules() -> None:
    with pytest.raises(ValueError, match="differing number of rows: 1, 0"):
        core._as_data_frame({"a": 1, "b": {"c": None}})
    df = core._as_data_frame({"a": 1, "tags": core._RList(["x", "y"])})
    assert df.columns.tolist() == ["a", "tags..x.", "tags..y."]
    assert core._make_names(['"x"', "1a", ".5", "if", "a b"]) == [
        "X.x.",
        "X1a",
        "X.5",
        "if.",
        "a.b",
    ]
    assert core._deparse_vec(pd.Series([6, 7], dtype="Int64")) == "6:7"
    assert core._deparse_vec(pd.Series(["a", None], dtype="string")) == 'c("a", NA)'
    assert core._deparse_vec(pd.Series([None], dtype="float64")) == "NA_real_"


def test_schema_compact_rules() -> None:
    t = L.type_object(a=L.type_string(required=False))
    assert types.type_as_json(t, "gemini")["required"] == []
    assert types.type_as_json(L.type_object(), "generic")["properties"] == {}


# ---------------------------------------------------------------------------
# the cache folder
# ---------------------------------------------------------------------------


def test_cache_key_is_a_hash_of_the_question() -> None:
    a = cache._llm_cache_key("text", "system", None, "m", None)
    assert a == cache._llm_cache_key("text", "system", None, "m", None)
    assert a != cache._llm_cache_key("text", "system", None, "other", None)
    assert a != cache._llm_cache_key("text", "system", None, "m", {"temperature": 0})
    assert len(a) == 64 and set(a) <= set("0123456789abcdef")
