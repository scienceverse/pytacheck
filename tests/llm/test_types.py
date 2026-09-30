"""ellmer type specs: printing (cache keys), request schemas, reply conversion."""

from __future__ import annotations

import pytest

from metacheck.llm import types as T
from metacheck.llm._rds import RList, RVec
from tests.llm.support import load_json

POWER = T.type_object(
    power_analyses=T.type_array(
        description="Power analyses found in the text. Empty array if none.",
        items=T.type_object(
            power_type=T.type_enum(
                ["apriori", "sensitivity", "posthoc", "unknown"],
                description=(
                    "The type of power analysis. 'apriori' calculates the required sample size to "
                    "achieve a desired power given an effect size, statistical test, and alpha level. "
                    "'sensitivity' estimates, given a sample size, which effect sizes a design has "
                    "sufficient power to detect. 'posthoc' (observed/retrospective power) computes "
                    "achieved power for an empirically observed effect size. Use 'unknown' if a power "
                    "analysis is present but its type cannot be determined."
                ),
            ),
            statistical_test=T.type_enum(
                ["paired t-test", "unpaired t-test", "one-sample t-test", "1-way ANOVA",
                 "2-way ANOVA", "3-way ANOVA", "MANOVA", "regression", "chi-square",
                 "correlation", "other", None],
                description="The statistical test used.",
                required=False,
            ),
            statistical_test_other=T.type_string(
                "Free-text description if statistical_test is 'other'.", required=False
            ),
            sample_size=T.type_number(
                "The sample size determined by or used in the power analysis. Give the total "
                "number if this is expressed as number per group.",
                required=False,
            ),
            alpha_level=T.type_number(
                "The alpha threshold used to determine significance.", required=False
            ),
            power=T.type_number(
                "The statistical power, expressed as a number between 0 and 1.", required=False
            ),
            effect_size=T.type_number(
                "The numeric effect size used in or determined from the power analysis.",
                required=False,
            ),
            effect_size_metric=T.type_enum(
                ["Cohen's d", "Hedges' g", "Cohen's f", "partial eta squared", "eta squared",
                 "unstandardised", "other", None],
                description=(
                    "The effect size metric. Use 'unstandardised' for raw/non-standardized effects."
                ),
                required=False,
            ),
            effect_size_metric_other=T.type_string(
                "Free-text description if effect_size_metric is 'other'.", required=False
            ),
            software=T.type_enum(
                ["G*Power", "Superpower", "Pangea", "Morepower", "PASS", "pwr", "simr",
                 "PowerUpR", "simulation", "InteractionPoweR", "pwrss", "other", None],
                description="The software used to conduct the power analysis.",
                required=False,
            ),
        ),
    )
)  # fmt: skip

TYPES = {
    "power": POWER,
    "basic": T.type_object(
        construct=T.type_string("The construct"),
        confidence=T.type_string("high, medium, or low."),
        n=T.type_number(),
        b=T.type_boolean("x", required=False),
        e=T.type_enum(["a", "b"]),
    ),
    "array": T.type_array(T.type_string()),
    "enum10": T.type_enum(list("abcdefghij")),
    "enum30": T.type_enum([f"value_{i}" for i in range(1, 31)], "desc"),
    "schema": T.type_from_schema(
        '{"type":"object","properties":{"a":{"type":"string","x":1,"y":2.5,"z":true,"w":null,'
        '"v":[1,2]}},"required":[]}'
    ),
    "empty": T.type_object(),
    "quoted": T.type_object(description='desc "q"\nnew\tline é中', a=T.type_enum(["x", None])),
    "long": T.type_object(
        a=T.type_string("abcdefghij" * 13), b=T.type_string("x" * 126), c=T.type_string("y" * 127)
    ),
    "nested": T.type_object(
        scales=T.type_array(
            T.type_object(
                scale=T.type_string("s"),
                columns=T.type_array(
                    T.type_string("An exact item column name belonging to this scale.")
                ),
                meta=T.type_object(x=T.type_integer()),
            )
        )
    ),
}


@pytest.mark.parametrize("name", sorted(TYPES))
def test_print_matches_r(name: str) -> None:
    """capture.output(print(type)) -- the text metacheck hashes into cache keys."""
    expected = load_json("type_prints.json")[name]
    assert T.type_print_lines(TYPES[name]) == expected


def test_repr_is_the_r_print() -> None:
    assert repr(T.type_string("x")).startswith("<ellmer::TypeBasic>")


def test_constructors_validate() -> None:
    with pytest.raises(ValueError, match="unknown basic type"):
        T.TypeBasic("date")
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
    expected = T.type_object(
        results=T.type_array(
            T.type_object(
                index=T.type_integer("The number"),
                value=T.type_string("The value", required=False),
                kind=T.type_enum(["a", "b"]),
            )
        )
    )
    assert t == expected
    # a schema with no ellmer equivalent is kept as a raw JSON schema
    odd = T.as_type({"anyOf": [{"type": "string"}, {"type": "integer"}]})
    assert isinstance(odd, T.TypeJsonSchema)
    assert isinstance(T.as_type('{"type": "string"}'), T.TypeJsonSchema)


def test_schema_openai_strict() -> None:
    t = T.type_object(a=T.type_string("A"), b=T.type_number(required=False))
    js = T.type_as_json(t, "openai")
    assert js["required"] == ["a", "b"]
    assert js["additionalProperties"] is False
    assert list(js["properties"]["b"]["type"]) == ["number", "null"]
    assert T.type_as_json(t, "generic")["required"] == ["a"]
    gem = T.type_as_json(t, "gemini")
    assert "description" not in gem
    assert gem["required"] == ["a"]
    assert T.type_as_json(T.type_object(), "gemini") == []
    with pytest.raises(ValueError, match="not supported for OpenAI"):
        T.type_as_json(T.type_object(additional_properties=True), "openai")


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
    tags = out["tags"]
    assert isinstance(tags, RVec)
    assert tags.values == [2, None, None]
    assert tags.attrs["levels"].values == ["x", "y"]
    rows = out["rows"]
    assert isinstance(rows, RList)
    assert rows.names == ["a", "b"]
    assert rows.values[0] == RVec("dbl", [1.0, 2.5])
    assert rows.values[1] == RVec("lgl", [True, None])
    # a missing required scalar is a typed NA
    assert T.convert_from_type(None, T.type_string()) == RVec("chr", [None])
    assert T.convert_from_type(None, T.type_enum(["a"])) == RVec("chr", [None])


def test_list_to_atomic() -> None:
    assert T.list_to_atomic([1, 2.9, "a", None], "integer") == RVec("int", [1, 2, None, None])
    assert T.list_to_atomic([1, 2.5, True], "number") == RVec("dbl", [1.0, 2.5, None])
    assert T.list_to_atomic([], "string") == RVec("chr", [])
