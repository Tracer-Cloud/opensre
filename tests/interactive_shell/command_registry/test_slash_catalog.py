"""Tests for slash-command MCP catalog."""

from __future__ import annotations

from core.agent_harness.tools.action_tools import get_action_tool
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.slash_catalog import (
    MCP_BY_COMMAND,
    build_slash_command_specs,
    format_slash_catalog_text,
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)

_MIN_LLM_DESCRIPTION_LEN = 20


def test_slash_catalog_covers_all_registered_commands() -> None:
    registered = set(SLASH_COMMANDS.keys())
    catalogued = set(MCP_BY_COMMAND)
    missing = sorted(registered - catalogued)
    stale = sorted(catalogued - registered)
    assert not missing, (
        "Add MCP_BY_COMMAND entries in surfaces/interactive_shell/command_registry/"
        f"slash_catalog.py for: {missing}. See surfaces/interactive_shell/AGENTS.md "
        "(Slash commands → REPL + CLI parity)."
    )
    assert not stale, (
        "Remove stale MCP_BY_COMMAND keys from slash_catalog.py (no longer in "
        f"SLASH_COMMANDS): {stale}"
    )
    specs = build_slash_command_specs()
    assert len(specs) == len(SLASH_COMMANDS)


def test_slash_command_specs_have_mcp_metadata() -> None:
    for spec in build_slash_command_specs():
        assert len(spec.llm_description) >= _MIN_LLM_DESCRIPTION_LEN, spec.name
        assert spec.use_cases, spec.name


def test_slash_invoke_tool_description_lists_every_command() -> None:
    description = slash_invoke_tool_description()
    for name in SLASH_COMMANDS:
        assert name in description


def test_slash_invoke_description_is_not_a_natural_language_router() -> None:
    description = slash_invoke_tool_description().lower()

    assert "explicit slash-command operations" in description
    assert "do not use this as a natural-language router" in description
    assert "answer those directly" in description
    assert "read-only discovery" in description


def test_slash_invoke_routes_natural_language_integration_setup() -> None:
    description = slash_invoke_tool_description().lower()

    assert "can you configure telegram/github/posthog for me" in description
    assert 'args=["setup", "telegram"/"github"/"posthog"]' in description
    assert "do not ask for tokens in chat" in description


def test_slash_invoke_schema_enum_matches_slash_commands() -> None:
    schema = slash_invoke_input_schema()
    command = schema["properties"]["command"]
    assert set(command["enum"]) == set(SLASH_COMMANDS.keys())


def test_registered_slash_invoke_uses_catalog() -> None:
    entry = get_action_tool("slash_invoke")
    assert entry is not None
    assert len(entry.description) > 200
    assert set(entry.input_schema["properties"]["command"]["enum"]) == set(SLASH_COMMANDS.keys())


def test_format_slash_catalog_text_compact_is_non_empty() -> None:
    text = format_slash_catalog_text(compact=True)
    assert text
    assert "**/health**" in text


def test_model_catalog_excludes_natural_language_status_questions() -> None:
    spec = next(spec for spec in build_slash_command_specs() if spec.name == "/model")

    assert "explicit /model command operations" in spec.llm_description.lower()
    assert "asks to run /model show" in " ".join(spec.use_cases).lower()

    anti_examples = " ".join(spec.anti_examples).lower()
    assert "which model is being used now" in anti_examples
    assert "what model/provider" in anti_examples
    assert "openai is configured" in anti_examples
    assert "answer directly" in anti_examples


def test_status_and_tools_catalog_exclude_natural_language_status_questions() -> None:
    specs = {spec.name: spec for spec in build_slash_command_specs()}

    status = specs["/status"]
    assert "explicit /status command operation" in status.llm_description.lower()
    assert "explicitly types /status" in " ".join(status.use_cases).lower()
    assert "current session status" in " ".join(status.anti_examples).lower()
    assert "answer directly" in " ".join(status.anti_examples).lower()

    tools = specs["/tools"]
    assert "explicit /tools command operation" in tools.llm_description.lower()
    assert "explicitly types /tools" in " ".join(tools.use_cases).lower()
    assert "what tools or capabilities" in " ".join(tools.anti_examples).lower()
    assert "answer directly" in " ".join(tools.anti_examples).lower()
