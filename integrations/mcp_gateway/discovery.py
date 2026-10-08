"""Bounded discovery contracts that never rewrite remote argument schemas."""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils import build_mcp_tool_listing
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.text import json_size_up_to
from integrations.mcp_gateway.client import McpGatewayToolDescriptor
from integrations.mcp_gateway.redaction import (
    public_tool_name,
    redacted_text_preview,
    scrub_configured_token,
)

_MAX_SCHEMA_CHARS = 8_192
_MAX_SCHEMA_TOTAL_CHARS = 32_768
_MAX_LISTING_CHARS = 60_000
_MAX_NAME_CHARS = 256


def _serialized_size(value: Any) -> int:
    size = json_size_up_to(value, _MAX_LISTING_CHARS)
    return size if size is not None else _MAX_LISTING_CHARS + 1


def gateway_tool_listing(
    descriptors: list[McpGatewayToolDescriptor],
    *,
    auth_token: str,
    read_only_tools: tuple[str, ...],
    name_filter: str | None,
    include_schema: bool,
) -> dict[str, object]:
    """Return bounded schemas intact, or omit them with an explicit reason."""
    visible: list[dict[str, object]] = []
    omitted: dict[str, str] = {}
    for descriptor in descriptors:
        name_size = json_size_up_to(descriptor["name"], _MAX_NAME_CHARS)
        if name_size is None or name_size > _MAX_NAME_CHARS:
            continue
        name = public_tool_name(descriptor["name"], auth_token)
        if _serialized_size(name) > _MAX_NAME_CHARS:
            continue
        item: dict[str, object] = {
            "name": name,
            "description": redacted_text_preview(
                descriptor["description"], auth_token, max_chars=512
            ),
        }
        if include_schema:
            schema = descriptor["input_schema"]
            size = json_size_up_to(schema, _MAX_SCHEMA_CHARS)
            if size is None or size > _MAX_SCHEMA_CHARS:
                omitted[name] = "Schema omitted because it exceeds the discovery size limit."
            elif redact_sensitive(scrub_configured_token(schema, auth_token)) != schema:
                omitted[name] = (
                    "Schema omitted because credential redaction would alter its contract."
                )
            else:
                item["input_schema"] = schema
        visible.append(item)
    listing = build_mcp_tool_listing(
        visible,
        name_filter=name_filter,
        include_schema=include_schema,
        filter_example="status restart",
    )
    # Echoing a user-supplied filter must not bypass the output budget.
    if name_filter:
        listing["name_filter"] = redacted_text_preview(
            name_filter, auth_token, max_chars=_MAX_NAME_CHARS
        )
    read_only_names = {public_tool_name(name, auth_token) for name in read_only_tools}
    tools = listing.get("tools")
    schema_total = 0
    if isinstance(tools, list):
        for item in tools:
            name = str(item["name"])
            item["access"] = "read_only" if name in read_only_names else "approval_required"
            if name in omitted:
                item["schema_omitted"] = omitted[name]
            schema = item.get("input_schema")
            if schema is not None:
                size = _serialized_size(schema)
                if schema_total + size > _MAX_SCHEMA_TOTAL_CHARS:
                    item.pop("input_schema")
                    item["schema_omitted"] = (
                        "Schema omitted because the listing size budget is full."
                    )
                else:
                    schema_total += size
        if len(visible) != len(descriptors):
            listing["notes"] = str(listing.get("notes", "")) + " Oversized tool names omitted."
        if _serialized_size(listing) > _MAX_LISTING_CHARS:
            listing["notes"] = (
                str(listing.get("notes", "")) + " Listing shortened to its size limit."
            )
            while tools and _serialized_size(listing) > _MAX_LISTING_CHARS:
                tools.pop()
            listing["returned_tools"] = len(tools)
    return listing
