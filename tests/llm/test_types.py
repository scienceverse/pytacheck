"""Structured-output types: the builders, the request schema per provider, reply conversion."""

from __future__ import annotations

import pytest

from metacheck.llm import types as T


def test_builders_make_plain_json_schema() -> None:
    t = T.type_object(
        {"name": T.type_string("The name")},
        tags=T.type_array(T.type_enum(["x", "y"]), "Some tags", required=False),
        n=T.type_integer(),
        description="A thing",
    )
    assert t["type"] == "object"
    assert t["description"] == "A thing"
    assert t["required"] == ["name", "n"]  # `tags` is optional
    assert t["additionalProperties"] is False
    assert t["properties"]["name"] == {"type": "string", "description": "The name"}
    assert t["properties"]["tags"]["items"] == {"type": "string", "enum": ["x", "y"]}
    assert T.type_boolean()["type"] == "boolean"
    assert T.type_number()["type"] == "number"
    assert T.type_enum(["a", None])["enum"] == ["a", None]  # NA is a value


def test_constructors_validate() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        T.type_from_schema()
    with pytest.raises(TypeError):
        T.as_type(3)


def test_as_type_from_json_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "The number"},
                        "value": {"type": ["string", "null"], "description": "The value"},
                        "kind": {"type": "string", "enum": ["a", "b"]},
                    },
                    "required": ["index", "kind"],
                },
            }
        },
        "required": ["results"],
    }
    t = T.as_type(schema)
    assert not t.raw  # converted to columns like the builders' output
    wire = T.type_as_json(t, "openai")
    item = wire["properties"]["results"]["items"]
    assert item["required"] == ["index", "value", "kind"]  # strict: all, optional ones nullable
    assert item["properties"]["value"]["type"] == ["string", "null"]
    # a schema with no builder equivalent is sent as written, and its reply is not converted
    odd = T.as_type({"anyOf": [{"type": "string"}, {"type": "integer"}]})
    assert odd.raw
    assert T.type_as_json(odd, "openai") == {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert T.as_type('{"type": "string"}').raw
    assert T.convert_from_type({"a": 1}, odd) == {"a": 1}


def test_schema_dialects() -> None:
    t = T.type_object(a=T.type_string("A"), b=T.type_number(required=False))
    strict = T.type_as_json(t, "openai")
    assert strict["required"] == ["a", "b"]
    assert strict["additionalProperties"] is False
    assert strict["properties"]["b"]["type"] == ["number", "null"]
    generic = T.type_as_json(t, "generic")  # Anthropic, Ollama
    assert generic["required"] == ["a"]
    assert generic["properties"]["b"]["type"] == "number"
    gemini = T.type_as_json(t, "gemini")
    assert gemini["required"] == ["a"]
    assert "additionalProperties" not in gemini
    with pytest.raises(ValueError, match="not supported for openai"):
        T.type_as_json(T.type_object(additional_properties=True), "openai")
    assert T.type_has_additional_properties(T.type_object(additional_properties=True))
    assert not T.type_has_additional_properties(t)


def test_convert_from_type() -> None:
    t = T.type_object(
        name=T.type_string(),
        n=T.type_integer(required=False),
        tags=T.type_array(T.type_enum(["x", "y"])),
        rows=T.type_array(T.type_object(a=T.type_number(), b=T.type_boolean())),
    )
    out = T.convert_from_type(
        {"name": "p", "tags": ["y", "q", None], "rows": [{"a": 1, "b": True}, {"a": 2.5}]}, t
    )
    assert out["name"] == "p"
    assert out["n"] is None  # optional and missing stays NULL
    assert out["tags"].tolist()[0] == "y"
    assert out["tags"].isna().tolist() == [False, True, True]  # a value outside the enum is NA
    rows = out["rows"]  # an array of objects is a data frame
    assert list(rows.columns) == ["a", "b"]
    assert rows["a"].tolist() == [1.0, 2.5]
    assert bool(rows["b"].iloc[0]) is True
    assert rows["b"].isna().tolist() == [False, True]
    # a missing required scalar is a typed NA
    assert T.convert_from_type(None, T.type_string()).isna().tolist() == [True]
    assert T.convert_from_type(None, T.type_enum(["a"])).isna().tolist() == [True]


def test_scalar_arrays_are_typed_columns() -> None:
    ints = T.convert_from_type([1, 2.9, "a", None], T.type_array(T.type_integer()))
    assert ints.isna().tolist() == [False, False, True, True]  # a string is NA
    assert ints.dropna().tolist() == [1, 2]  # a float truncates
    nums = T.convert_from_type([1, 2.5, True], T.type_array(T.type_number()))
    assert nums.isna().tolist() == [False, False, True]
    assert T.convert_from_type([], T.type_array(T.type_string())).tolist() == []
