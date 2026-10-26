"""Tests for shared MCP integration source param parsers."""

from __future__ import annotations

from core.tool_framework.utils.mcp_params import first_list, first_string, string_list


def test_string_list_returns_empty_for_non_list() -> None:
    assert string_list("not-a-list") == []
    assert string_list(None) == []
    assert string_list({}) == []


def test_string_list_strips_and_drops_blank_entries() -> None:
    assert string_list(["  alpha  ", "", "  ", "beta"]) == ["alpha", "beta"]


def test_string_list_coerces_non_string_items() -> None:
    assert string_list([123, "  ok  "]) == ["123", "ok"]


def test_first_string_returns_first_non_empty_key_in_order() -> None:
    source = {"url": "https://example.com", "posthog_url": "https://ignored.example.com"}
    assert first_string(source, "posthog_url", "url") == "https://ignored.example.com"
    assert first_string(source, "missing", "url") == "https://example.com"


def test_first_string_skips_whitespace_only_values() -> None:
    source = {"url": "   ", "mode": "stdio"}
    assert first_string(source, "url", "mode") == "stdio"


def test_first_string_returns_none_when_no_match() -> None:
    assert first_string({}, "url", "mode") is None


def test_first_string_coerces_values_to_string() -> None:
    assert first_string({"port": 443}, "port") == "443"


def test_first_list_returns_first_non_empty_list_in_order() -> None:
    source = {"args": [], "sentry_args": ["mcp", "serve"]}
    assert first_list(source, "args", "sentry_args") == ["mcp", "serve"]


def test_first_list_skips_invalid_or_empty_lists() -> None:
    source = {"args": "not-a-list", "sentry_args": ["  ", ""]}
    assert first_list(source, "args", "sentry_args") == []


def test_first_list_normalizes_entries() -> None:
    source = {"args": ["  mcp  ", "serve"]}
    assert first_list(source, "args") == ["mcp", "serve"]
