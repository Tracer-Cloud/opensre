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
    entries: list[str] = []
    for item in value:
        # ``None`` is an absent value, not the four-letter word "None": a JSON
        # ``null`` in the list would otherwise reach the runtime as a real entry.
        if item is None:
            continue
        text = str(item).strip()
        if text:
            entries.append(text)
    return entries


def first_string(source: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        raw = source.get(key)
        # ``first_string`` promises the first *non-empty* value across aliases.
        # The ``.get(key, "")`` default only covers an absent key: a key that is
        # present and ``null`` would stringify to "None" and shadow a later
        # alias that does carry the setting.
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return None


def first_list(source: dict[str, object], *keys: str) -> list[str]:
    for key in keys:
        values = string_list(source.get(key, []))
        if values:
            return values
    return []
