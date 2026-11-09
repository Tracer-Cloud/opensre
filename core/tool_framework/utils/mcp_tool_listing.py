"""Bounded, model-safe rendering of MCP server tool catalogs.

MCP servers (PostHog, Sentry, X, ...) can expose dozens to hundreds of
tools, each carrying a full JSON input schema. A discovery tool that returns
every tool *with* its schema produces a payload many times larger than any
model's context window; the agent's context-budget enforcer then trims the
listing away before the model sees a single tool name, and the model loops
re-calling the discovery tool and guessing tool names that don't exist.

This module renders a compact, bounded view by default (names + truncated
descriptions, no schemas) and lets callers narrow with a name filter or pull
the full schema for a small, specific set of tools. It is shared by every
``list_*_tools`` MCP discovery tool so they behave identically.
"""

from __future__ import annotations

import re

# Defaults sized to keep even a full catalog dump well under model context
# windows (the live PostHog server alone is ~580k estimated tokens unbounded).
MAX_DESCRIPTION_CHARS = 160
MAX_TOOLS_RETURNED = 80
MAX_SCHEMAS_RETURNED = 15


def _truncate_description(description: str) -> str:
    cleaned = " ".join(description.split())
    if len(cleaned) <= MAX_DESCRIPTION_CHARS:
        return cleaned
    return cleaned[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "\u2026"


def _filter_tools(
    tools: list[dict[str, object]],
    name_filter: str,
) -> list[dict[str, object]]:
    """Keep tools whose name or description matches any whitespace/comma term."""
    terms = [term for term in re.split(r"[,\s]+", name_filter.lower()) if term]
    if not terms:
        return tools
    matched: list[dict[str, object]] = []
    for descriptor in tools:
        haystack = f"{descriptor.get('name', '')} {descriptor.get('description', '')}".lower()
        if any(term in haystack for term in terms):
            matched.append(descriptor)
    return matched


def _summarize_tool(
    descriptor: dict[str, object],
    *,
    include_schema: bool,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "name": str(descriptor.get("name", "")).strip(),
        "description": _truncate_description(str(descriptor.get("description", "") or "")),
    }
    schema = descriptor.get("input_schema")
    if include_schema and schema is not None:
        summary["input_schema"] = schema
    return summary


def build_mcp_tool_listing(
    tools: list[dict[str, object]],
    *,
    name_filter: str | None,
    include_schema: bool,
    filter_example: str = "events query sql",
) -> dict[str, object]:
    """Render a bounded, model-safe view of discovered MCP tools.

    ``filter_example`` only affects the human-readable ``notes`` hint so each
    integration can suggest filter terms relevant to its own tool catalog.
    """
    total = len(tools)
    filtered = _filter_tools(tools, name_filter) if name_filter else list(tools)
    returned = filtered[:MAX_TOOLS_RETURNED]
    # Only attach full schemas when the result set is small enough that doing so
    # keeps the payload bounded. Otherwise schemas would reintroduce the very
    # context blow-up this listing exists to prevent.
    attach_schema = include_schema and len(returned) <= MAX_SCHEMAS_RETURNED

    summaries = [
        _summarize_tool(descriptor, include_schema=attach_schema) for descriptor in returned
    ]
    notes: list[str] = []
    if len(filtered) > len(returned):
        notes.append(
            f"Showing {len(returned)} of {len(filtered)} matching tools; "
            "pass name_filter to narrow the list."
        )
    elif total > len(returned):
        notes.append(
            f"Showing {len(returned)} of {total} tools; pass name_filter "
            f"(e.g. '{filter_example}') to narrow the list."
        )
    if include_schema and not attach_schema:
        notes.append(
            "input_schema omitted because too many tools matched; narrow to "
            f"{MAX_SCHEMAS_RETURNED} or fewer tools with name_filter to include schemas."
        )
    if not include_schema:
        notes.append(
            "Schemas omitted to save context; call again with include_schema=true and a "
            "name_filter once you know which tool you need."
        )

    listing: dict[str, object] = {
        "total_tools": total,
        "matched_tools": len(filtered),
        "returned_tools": len(summaries),
        "tools": summaries,
    }
    if name_filter:
        listing["name_filter"] = name_filter
    if notes:
        listing["notes"] = " ".join(notes)
    return listing


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_SCHEMAS_RETURNED",
    "MAX_TOOLS_RETURNED",
    "build_mcp_tool_listing",
]
