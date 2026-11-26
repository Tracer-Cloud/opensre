"""Turn an MCP tool's ``inputSchema`` into one every LLM provider route accepts.

Provider sanitizers drop ``$ref``/``$defs`` without resolving them, which would
leave a referenced argument as ``{}``. Local references are therefore inlined
here, before any provider sees the schema: ``#/$defs/...`` targets, and any
other JSON pointer into the schema, such as the ``#/properties/start`` that
zod-to-json-schema (the MCP TypeScript SDK's zod v3 path) emits for a reused
argument schema.

The walk follows JSON Schema structure: argument names under ``properties``
are never treated as keywords, and values such as ``enum`` or ``default`` are
copied untouched. Expansion is capped, since a few references per level can
grow exponentially; past the cap, references are left unresolved instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

_DEFINITION_KEYS = frozenset({"$defs", "definitions"})
#: Keywords whose value is a mapping of names to schemas.
_NAMED_SCHEMA_KEYS = frozenset({"properties", "patternProperties", "dependentSchemas"})
#: Keywords whose value is one schema.
_SCHEMA_KEYS = frozenset(
    {
        "items",
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "additionalItems",
        "unevaluatedItems",
    }
)
#: Keywords whose value is a list of schemas.
_SCHEMA_LIST_KEYS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
#: Recursive models cannot be fully inlined; past this depth a reference becomes ``{}``.
_MAX_REF_DEPTH = 8
#: Upper bound on schema nodes produced while inlining one tool's arguments.
_MAX_INLINED_NODES = 5_000


class _BudgetExceeded(Exception):
    """Inlining would produce more nodes than :data:`_MAX_INLINED_NODES`."""


class _Inliner:
    """Inlines local references of one schema within a shared node budget."""

    def __init__(self, root: Mapping[str, Any], *, expand: bool) -> None:
        self._root = root
        self._expand = expand
        self._nodes = 0

    def schema(self, node: Any, active: frozenset[str]) -> Any:
        if not isinstance(node, Mapping):
            return node
        if self._expand:
            self._nodes += 1
            if self._nodes > _MAX_INLINED_NODES:
                raise _BudgetExceeded
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" or key in _DEFINITION_KEYS:
                continue
            if key in _NAMED_SCHEMA_KEYS and isinstance(value, Mapping):
                result[key] = {name: self.schema(sub, active) for name, sub in value.items()}
            elif key in _SCHEMA_KEYS:
                result[key] = self.schema(value, active)
            elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
                result[key] = [self.schema(sub, active) for sub in value]
            else:
                result[key] = value
        ref = node.get("$ref")
        if not self._expand or not isinstance(ref, str):
            return result
        target = _resolve_pointer(self._root, ref)
        if target is None or ref in active or len(active) >= _MAX_REF_DEPTH:
            # Unknown, remote or recursive reference: keep only what is stated here.
            return result
        resolved = self.schema(target, active | {ref})
        if not isinstance(resolved, dict):
            return result
        return {**resolved, **result}


def _resolve_pointer(root: Mapping[str, Any], ref: str) -> Any | None:
    """The node a local ``#/...`` JSON pointer (RFC 6901) names in ``root``, or ``None``."""
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for token in ref[2:].split("/"):
        key = unquote(token).replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isascii() and key.isdecimal() and int(key) < len(node):
            node = node[int(key)]
        else:
            return None
    return node


def bridged_input_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """An object schema with local ``$ref`` targets inlined and ``$defs`` removed."""
    if schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    try:
        normalized = _Inliner(schema, expand=True).schema(schema, frozenset())
    except _BudgetExceeded:
        normalized = _Inliner(schema, expand=False).schema(schema, frozenset())
    normalized.setdefault("properties", {})
    return dict(normalized)


__all__ = ["bridged_input_schema"]
