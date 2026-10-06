"""Tests for terminal-turn analytics outcome formatting."""

from __future__ import annotations

from surfaces.interactive_shell.telemetry.turn_outcome import (
    format_terminal_turn_outcome,
    format_wizard_cli_outcome,
    slash_command_is_interactive_wizard,
    slash_command_is_summary_only,
    truncate_analytics_text,
)


def test_slash_command_is_interactive_wizard() -> None:
    assert slash_command_is_interactive_wizard("/onboard")
    assert slash_command_is_interactive_wizard("/integrations setup")
    assert slash_command_is_interactive_wizard("/integrations list")
    assert slash_command_is_interactive_wizard("/mcp list")
    assert not slash_command_is_interactive_wizard("/integrations")
    assert not slash_command_is_interactive_wizard("/mcp")
    assert not slash_command_is_interactive_wizard("/health")
    assert not slash_command_is_interactive_wizard("/status")


def test_format_wizard_cli_outcome() -> None:
    assert "completed successfully" in format_wizard_cli_outcome(["onboard"], exit_code=0)
    assert "failed (exit 1)" in format_wizard_cli_outcome(["onboard"], exit_code=1)
    assert "cancelled" in format_wizard_cli_outcome(["onboard"], exit_code=None)


def test_format_terminal_turn_outcome_prefers_hint() -> None:
    text = format_terminal_turn_outcome(
        "/onboard",
        kind="slash",
        ok=True,
        captured_output="",
        outcome_hint="opensre onboard: interactive wizard completed successfully",
    )
    assert text == "opensre onboard: interactive wizard completed successfully"


def test_slash_command_is_summary_only() -> None:
    assert slash_command_is_summary_only("/help")
    assert slash_command_is_summary_only("/help /model")
    assert slash_command_is_summary_only("/onboard")
    assert not slash_command_is_summary_only("/integrations list")
    assert not slash_command_is_summary_only("/mcp list")
    assert not slash_command_is_summary_only("/status")


def test_format_terminal_turn_outcome_omits_help_table() -> None:
    text = format_terminal_turn_outcome(
        "/help",
        kind="slash",
        ok=True,
        captured_output="/exit — quit\n/model — change model",
    )
    assert text == "slash /help (succeeded)"


def test_format_terminal_turn_outcome_includes_captured_output() -> None:
    text = format_terminal_turn_outcome(
        "/status",
        kind="slash",
        ok=True,
        captured_output="integrations: datadog",
    )
    assert text.startswith("slash /status (succeeded)")
    assert "datadog" in text


def test_bare_connection_command_returns_list_usage_to_the_agent() -> None:
    text = format_terminal_turn_outcome(
        "/integrations",
        kind="slash",
        ok=False,
        captured_output="usage: /integrations list",
    )

    assert text == "slash /integrations (failed)\nusage: /integrations list"


def test_truncate_analytics_text() -> None:
    long_text = "x" * 100
    truncated = truncate_analytics_text(long_text, max_chars=50)
    assert len(truncated) <= 50
    assert truncated.endswith("[truncated]")


def test_exit_does_not_replay_its_farewell_to_the_user() -> None:
    """``/exit`` records the outcome line only, not the console text it just printed.

    Every slash command runs under ``capture_console_segment``, which tees: the
    output renders live *and* is recorded, and the recording becomes the turn's
    ``response_text``. For ``/exit`` that meant the resume hint and the goodbye
    were printed once by the command and once more as the turn's response.

    The spinner made it obvious. ``console.status`` animates by rewriting one
    line, but ``export_text`` records every frame it wrote, so the replay showed
    ``⠋ finishing up…⠙ finishing up…⠹ finishing up…`` as plain text.
    """
    # Arrange: what the exit path actually prints, spinner frames included.
    captured = (
        "Resume this session with:\n"
        "/resume f659a4f9-3911-4ed2-bc8c-8e02da5f887c\n"
        "opensre --resume f659a4f9-3911-4ed2-bc8c-8e02da5f887c\n"
        "⠋ finishing up…⠙ finishing up…⠹ finishing up…\n"
        "goodbye."
    )

    # Act
    outcome = format_terminal_turn_outcome("/exit", kind="slash", ok=True, captured_output=captured)

    # Assert
    assert outcome == "slash /exit (succeeded)"
    assert "goodbye." not in outcome
    assert "finishing up" not in outcome


def test_quit_is_summary_only_like_its_alias() -> None:
    """``/quit`` runs the same handler, so it must not replay either."""
    # Assert
    assert slash_command_is_summary_only("/exit") is True
    assert slash_command_is_summary_only("/quit") is True
