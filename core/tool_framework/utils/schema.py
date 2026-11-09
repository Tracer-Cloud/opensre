"""JSON-schema builders for tool input schemas."""

from __future__ import annotations

from typing import Any

ToolSchema = dict[str, Any]


def string_property(
    *,
    description: str,
    enum: tuple[str, ...] | None = None,
    min_length: int | None = None,
) -> ToolSchema:
    schema: ToolSchema = {"type": "string", "description": description}
    if enum:
        schema["enum"] = list(enum)
    if min_length is not None:
        schema["minLength"] = min_length
    return schema


def string_array_property(*, description: str) -> ToolSchema:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


def object_schema(*, properties: dict[str, ToolSchema], required: tuple[str, ...]) -> ToolSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


__all__ = ["ToolSchema", "object_schema", "string_array_property", "string_property"]
