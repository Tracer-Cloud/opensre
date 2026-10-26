"""Tests for the shared assistant three-tier response formatter."""

from __future__ import annotations

import pytest

from core.agent_harness.prompts.rules import (
    format_agent_response,
    normalize_three_tier_spacing,
)


def test_format_agent_response_full_three_tier() -> None:
    text = format_agent_response(
        "CPU on `prod-api-3` has been above 90% for 45 minutes.",
        "Host: prod-api-3  CPU: 94%  Duration: 47m",
        "check recent deployments and memory pressure on that host?",
    )

    assert "**I found:**" in text
    assert "**Here's what that looks like:**" in text
    assert "**Want me to:**" in text
    assert "prod-api-3" in text


def test_format_agent_response_compact_single_line() -> None:
    text = format_agent_response("No integrations are configured in this session.")

    assert text == "No integrations are configured in this session."
    assert "**I found:**" not in text


def test_format_agent_response_rejects_empty_found_with_detail() -> None:
    with pytest.raises(ValueError, match="found is required"):
        format_agent_response("", "Host: prod-api-3  CPU: 94%", "restart the pod?")


def test_normalize_three_tier_spacing_splits_want_me_to() -> None:
    text = (
        "**I found:** LLM errors are the top priority.\n\n"
        "**Here's what that looks like:**\n"
        " • Export formatter bugs: 3\n"
        "**Want me to:** summarize the top issue titles?"
    )
    normalized = normalize_three_tier_spacing(text)

    assert " • Export formatter bugs: 3\n\n**Want me to:**" in normalized


def test_normalize_three_tier_spacing_tightens_padded_headers() -> None:
    text = (
        "** I found: ** LLM errors are the top priority.\n"
        "** Want me to: ** summarize the top issue titles?"
    )
    normalized = normalize_three_tier_spacing(text)

    assert "**I found:**" in normalized
    assert "**Want me to:**" in normalized
    assert "\n\n**Want me to:**" in normalized


def test_normalize_three_tier_spacing_idempotent() -> None:
    text = format_agent_response("CPU is high.", "Host: prod-api-3", "check memory?")
    assert normalize_three_tier_spacing(text) == text
