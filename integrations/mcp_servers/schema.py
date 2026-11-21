"""Turn an MCP tool's ``inputSchema`` into one every LLM provider route accepts.

Provider sanitizers drop ``$ref``/``$defs`` without resolving them, which would
leave a referenced argument as ``{}``. Local references are therefore inlined
here, before any provider sees the schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_DEFINITION_KEYS = ("$defs", "definitions")
#: Recursive models cannot be fully inlined; past this depth a reference becomes ``{}``.
_MAX_REF_DEPTH = 8


def _local_definitions(schema: Mapping[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for key in _DEFINITION_KEYS:
        section = schema.get(key)
        if isinstance(section, Mapping):
            for name, value in section.items():
                definitions[f"#/{key}/{name}"] = value
    return definitions


def _inline(node: Any, definitions: Mapping[str, Any], active: frozenset[str]) -> Any:
    if isinstance(node, list):
        return [_inline(item, definitions, active) for item in node]
    if not isinstance(node, Mapping):
        return node
    siblings = {
        key: _inline(value, definitions, active)
        for key, value in node.items()
        if key != "$ref" and key not in _DEFINITION_KEYS
    }
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return siblings
    target = definitions.get(ref)
    if target is None or ref in active or len(active) >= _MAX_REF_DEPTH:
        # Unknown, remote or recursive reference: keep only what is stated here.
        return siblings
    resolved = _inline(target, definitions, active | {ref})
    if not isinstance(resolved, dict):
        return siblings
    return {**resolved, **siblings}


def bridged_input_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """An object schema with local ``$ref`` targets inlined and ``$defs`` removed."""
    if schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    normalized = _inline(schema, _local_definitions(schema), frozenset())
    normalized.setdefault("properties", {})
    return dict(normalized)


__all__ = ["bridged_input_schema"]
