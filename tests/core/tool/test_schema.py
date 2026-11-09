"""Unit tests for core.tool.contracts."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from core.tool.contracts import (
    _value_matches_schema,
    infer_input_schema,
    model_to_json_schema,
)

# ---------------------------------------------------------------------------
# infer_input_schema — type mapping
# ---------------------------------------------------------------------------


def test_infer_str_param() -> None:
    def fn(query: str) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["query"] == {"type": "string"}
    assert "query" in schema["required"]


def test_infer_int_param() -> None:
    def fn(limit: int) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["limit"] == {"type": "integer"}
    assert "limit" in schema["required"]


def test_infer_float_param() -> None:
    def fn(threshold: float) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["threshold"] == {"type": "number"}


def test_infer_bool_param() -> None:
    def fn(verbose: bool) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["verbose"] == {"type": "boolean"}


def test_infer_list_param() -> None:
    def fn(tags: list) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["tags"] == {"type": "array"}


def test_infer_dict_param() -> None:
    def fn(meta: dict) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["meta"] == {"type": "object"}


# ---------------------------------------------------------------------------
# infer_input_schema — required vs optional
# ---------------------------------------------------------------------------


def test_required_params_are_in_required_list() -> None:
    def fn(a: str, b: int) -> None:
        pass

    schema = infer_input_schema(fn)
    assert set(schema["required"]) == {"a", "b"}


def test_optional_params_not_in_required_list() -> None:
    def fn(a: str, b: str | None = None) -> None:
        pass  # noqa: UP007

    schema = infer_input_schema(fn)
    assert "a" in schema["required"]
    assert "b" not in schema["required"]


def test_union_none_marks_nullable() -> None:
    def fn(val: str | None) -> None:
        pass

    schema = infer_input_schema(fn)
    assert schema["properties"]["val"].get("nullable") is True


def test_defaulted_non_optional_param_not_required() -> None:
    def fn(a: str, b: str = "default") -> None:
        pass

    schema = infer_input_schema(fn)
    assert "a" in schema["required"]
    assert "b" not in schema["required"]


# ---------------------------------------------------------------------------
# infer_input_schema — param exclusions
# ---------------------------------------------------------------------------


def test_underscore_prefixed_params_skipped() -> None:
    def fn(query: str, _internal: str = "x") -> None:
        pass

    schema = infer_input_schema(fn)
    assert "_internal" not in schema["properties"]
    assert "query" in schema["properties"]


def test_var_positional_and_var_keyword_skipped() -> None:
    def fn(a: str, *args: Any, **kwargs: Any) -> None:
        pass

    schema = infer_input_schema(fn)
    assert "args" not in schema["properties"]
    assert "kwargs" not in schema["properties"]
    assert "a" in schema["properties"]


# ---------------------------------------------------------------------------
# model_to_json_schema
# ---------------------------------------------------------------------------


class _SampleModel(BaseModel):
    name: str
    count: int = 0


def test_model_to_json_schema_has_type_object() -> None:
    schema = model_to_json_schema(_SampleModel)
    assert schema["type"] == "object"


def test_model_to_json_schema_has_properties() -> None:
    schema = model_to_json_schema(_SampleModel)
    assert "name" in schema["properties"]
    assert "count" in schema["properties"]


def test_model_to_json_schema_has_required() -> None:
    schema = model_to_json_schema(_SampleModel)
    assert "name" in schema["required"]
    assert "count" not in schema["required"]


def test_model_to_json_schema_additional_properties_false() -> None:
    schema = model_to_json_schema(_SampleModel)
    assert schema.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# _value_matches_schema
# ---------------------------------------------------------------------------


def test_nullable_accepts_none() -> None:
    assert _value_matches_schema(None, {"type": "string", "nullable": True}) is True


def test_non_nullable_rejects_none() -> None:
    assert _value_matches_schema(None, {"type": "string"}) is False


def test_enum_accepts_valid_value() -> None:
    assert _value_matches_schema("foo", {"enum": ["foo", "bar"]}) is True


def test_enum_rejects_invalid_value() -> None:
    assert _value_matches_schema("baz", {"enum": ["foo", "bar"]}) is False


def test_one_of_matches_any_option() -> None:
    schema: dict[str, Any] = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert _value_matches_schema("hello", schema) is True
    assert _value_matches_schema(42, schema) is True
    assert _value_matches_schema(3.14, schema) is False


def test_any_of_matches_any_option() -> None:
    schema: dict[str, Any] = {"anyOf": [{"type": "boolean"}, {"type": "integer"}]}
    assert _value_matches_schema(True, schema) is True
    assert _value_matches_schema(1, schema) is True
    assert _value_matches_schema("x", schema) is False


def test_type_as_list_matches_any() -> None:
    schema: dict[str, Any] = {"type": ["string", "integer"]}
    assert _value_matches_schema("hello", schema) is True
    assert _value_matches_schema(5, schema) is True
    assert _value_matches_schema([], schema) is False


@pytest.mark.parametrize(
    "type_name,value,expected",
    [
        ("string", "hello", True),
        ("string", 1, False),
        ("integer", 1, True),
        ("integer", True, False),  # bool is NOT integer
        ("integer", 1.5, False),
        ("number", 1.5, True),
        ("number", 1, True),
        ("number", True, False),
        ("boolean", True, True),
        ("boolean", 1, False),
        ("array", [1, 2], True),
        ("array", {}, False),
        ("object", {"k": "v"}, True),
        ("object", [], False),
    ],
)
def test_json_type_matching(type_name: str, value: Any, expected: bool) -> None:
    assert _value_matches_schema(value, {"type": type_name}) is expected


# ---------------------------------------------------------------------------
# Union type inference
# ---------------------------------------------------------------------------


def test_infer_union_param_emits_one_of() -> None:
    """int | str must produce a oneOf schema, not fall back to string."""

    def fn(value: int | str) -> None:
        pass

    schema = infer_input_schema(fn)
    prop = schema["properties"]["value"]
    assert "oneOf" in prop, f"Expected oneOf, got: {prop}"
    types_in_one_of = [s.get("type") for s in prop["oneOf"]]
    assert "integer" in types_in_one_of
    assert "string" in types_in_one_of


def test_infer_union_with_none_emits_one_of_and_nullable() -> None:
    """int | str | None must produce oneOf with nullable=True."""

    def fn(value: int | str | None) -> None:
        pass

    schema = infer_input_schema(fn)
    prop = schema["properties"]["value"]
    assert prop.get("nullable") is True
    assert "oneOf" in prop
    types_in_one_of = [s.get("type") for s in prop["oneOf"]]
    assert "integer" in types_in_one_of
    assert "string" in types_in_one_of


def test_infer_optional_simple_type_not_one_of() -> None:
    """str | None is still a simple nullable type, not a oneOf (regression guard)."""

    def fn(value: str | None) -> None:
        pass

    schema = infer_input_schema(fn)
    prop = schema["properties"]["value"]
    assert prop.get("nullable") is True
    assert prop.get("type") == "string"
    assert "oneOf" not in prop
