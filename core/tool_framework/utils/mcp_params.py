"""Normalize MCP integration source dicts for tool param extraction.

MCP-backed tools read connection settings from the verified integration
source (e.g. ``posthog_mcp``, ``sentry_mcp``). Catalog and
runtime configs may use prefixed keys (``posthog_url``) or short aliases
(``url``). These helpers pick the first non-empty value across alias keys
and coerce list fields into stripped string lists.
"""

from __future__ import annotations

__all__ = ["first_list", "first_string", "string_list"]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def first_string(source: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = str(source.get(key, "")).strip()
        if value:
            return value
    return None


def first_list(source: dict[str, object], *keys: str) -> list[str]:
    for key in keys:
        values = string_list(source.get(key, []))
        if values:
            return values
    return []
