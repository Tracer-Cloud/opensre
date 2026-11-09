"""Shared strict JSON Schema normalization for LLM tool ``parameters`` / ``inputSchema``.

Provider adapters choose which keys to strip (e.g. Bedrock Converse rejects
``additionalProperties``; OpenAI-compatible APIs should keep it for strict mode).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.llm.types import SchemaDescribedTool

# Keys rejected or unresolvable by strict HTTP tool-schema APIs.
COMMON_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "title",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "not",
        "nullable",
    }
)

# Bedrock Converse (incl. Mistral/Llama) rejects additionalProperties even when false.
BEDROCK_UNSUPPORTED_SCHEMA_KEYS = COMMON_UNSUPPORTED_SCHEMA_KEYS | frozenset(
    {"additionalProperties"}
)

_DEFAULT_ARRAY_ITEMS: dict[str, str] = {"type": "string"}


def _pick_non_null_schema_variant(variants: list[Any]) -> dict[str, Any] | None:
    """Return the first ``anyOf`` / ``oneOf`` branch with a concrete non-null type."""
    for item in variants:
        if not isinstance(item, dict):
            continue
        branch_type = item.get("type")
        if branch_type and branch_type != "null":
            return item
        if "properties" in item:
            return item
    return None


def _merge_all_of_subschemas(variants: list[Any]) -> dict[str, Any]:
    """Merge ``allOf`` branches (e.g. Pydantic constrained fields) into one schema dict."""
    merged: dict[str, Any] = {}
    for item in variants:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key == "properties" and isinstance(value, dict):
                props = merged.setdefault("properties", {})
                if isinstance(props, dict):
                    props.update(value)
                else:
                    merged["properties"] = dict[Any, Any](value)
            elif key == "required" and isinstance(value, list):
                required = merged.setdefault("required", [])
                if isinstance(required, list):
                    for name in value:
                        if name not in required:
                            required.append(name)
            elif key not in merged:
                merged[key] = value
    return merged


def _flatten_composite_keywords(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``allOf`` / ``anyOf`` / ``oneOf`` into explicit ``type`` fields."""
    flattened = dict(schema)
    if "allOf" in flattened:
        variants = flattened.pop("allOf")
        if isinstance(variants, list):
            for key, value in _merge_all_of_subschemas(variants).items():
                if key == "properties" and isinstance(value, dict):
                    existing = flattened.get("properties")
                    if isinstance(existing, dict):
                        existing.update(value)
                    else:
                        flattened["properties"] = dict(value)
                elif key not in flattened:
                    flattened[key] = value
    for composite in ("anyOf", "oneOf"):
        if composite not in flattened:
            continue
        variants = flattened.pop(composite)
        if not isinstance(variants, list):
            continue
        picked = _pick_non_null_schema_variant(variants)
        if picked:
            for key, value in picked.items():
                flattened.setdefault(key, value)
        elif "type" not in flattened:
            flattened["type"] = "string"
    return flattened


def _coerce_schema_type(node: dict[str, Any]) -> str | None:
    """Return a single ``type`` string (strict APIs reject ``type`` arrays)."""
    schema_type = node.get("type")
    if isinstance(schema_type, list):
        for candidate in schema_type:
            if isinstance(candidate, str) and candidate != "null":
                node["type"] = candidate
                return candidate
        node["type"] = "string"
        return "string"
    if isinstance(schema_type, str):
        return schema_type
    return None


def _ensure_schema_node(node: dict[str, Any]) -> None:
    """Mutate *node* so strict JSON Schema validation receives explicit types."""
    if "properties" in node and "type" not in node:
        node["type"] = "object"

    schema_type = _coerce_schema_type(node)
    if schema_type == "object" and "properties" not in node:
        node["properties"] = {}

    if schema_type == "array":
        items = node.get("items")
        if items is None:
            node["items"] = dict(_DEFAULT_ARRAY_ITEMS)
        elif isinstance(items, dict):
            if "type" not in items and "properties" not in items:
                node["items"] = dict(_DEFAULT_ARRAY_ITEMS)
            else:
                _ensure_schema_node(items)
        return

    items = node.get("items")
    if isinstance(items, dict):
        _ensure_schema_node(items)


def sanitize_strict_tool_schema(
    schema: dict[str, Any],
    *,
    unsupported_keys: frozenset[str] = COMMON_UNSUPPORTED_SCHEMA_KEYS,
) -> dict[str, Any]:
    """Return a strict-API copy of *schema* with required ``type`` / ``items`` filled in."""
    cleaned: dict[str, Any] = {}
    for key, value in _flatten_composite_keywords(schema).items():
        if key in unsupported_keys:
            continue
        # Property / patternProperty names are field identifiers, not schema
        # keywords — do not filter them against unsupported_keys (e.g. a field
        # named ``title`` must survive even though ``title`` is stripped as a
        # schema-metadata key elsewhere).
        if key in {"properties", "patternProperties"} and isinstance(value, dict):
            cleaned[key] = {
                prop_name: (
                    sanitize_strict_tool_schema(prop_schema, unsupported_keys=unsupported_keys)
                    if isinstance(prop_schema, dict)
                    else prop_schema
                )
                for prop_name, prop_schema in value.items()
            }
        elif isinstance(value, dict):
            cleaned[key] = sanitize_strict_tool_schema(value, unsupported_keys=unsupported_keys)
        elif isinstance(value, list):
            cleaned[key] = [
                sanitize_strict_tool_schema(item, unsupported_keys=unsupported_keys)
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            cleaned[key] = value

    _ensure_schema_node(cleaned)
    return cleaned


def normalize_object_tool_input_schema(
    schema: dict[str, Any] | None,
    *,
    unsupported_keys: frozenset[str] = COMMON_UNSUPPORTED_SCHEMA_KEYS,
) -> dict[str, Any]:
    """Normalize a tool's public input schema to a top-level JSON object."""
    cleaned = sanitize_strict_tool_schema(dict(schema or {}), unsupported_keys=unsupported_keys)
    if cleaned.get("type") != "object":
        return {"type": "object", "properties": {}}
    if "properties" not in cleaned:
        cleaned["properties"] = {}
    return cleaned


def normalize_openai_tool_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize tool parameters for OpenAI-compatible chat ``tools`` APIs."""
    return normalize_object_tool_input_schema(
        schema, unsupported_keys=COMMON_UNSUPPORTED_SCHEMA_KEYS
    )


def _openai_tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": normalize_openai_tool_input_schema(tool.public_input_schema),
        },
    }


def build_openai_tool_specs(tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
    """Build OpenAI chat ``tools`` entries from registered tool objects."""
    return [_openai_tool_schema(t) for t in tools]
