"""Tests for Telegram gateway status copy."""

from __future__ import annotations

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from core.tool.contracts import RegisteredTool
from infrastructure.turn_host.status_messages import (
    INITIAL_STATUSES,
    _tool_label,
    chat_status_headline,
    initial_status_message,
    normalize_gateway_status,
    status_from_response_label,
    status_from_tool_start,
    user_facing_error_message,
)
from tools.registry import describe_registered_tool


def test_user_facing_error_hides_raw_exception_detail() -> None:
    # Arrange: an internal error string carrying host/credential detail.
    detail = "ValueError: connection to db-prod-primary:5432 failed for user svc_acct"

    # Act
    shown = user_facing_error_message(detail)

    # Assert: the user sees only generic copy; no internal detail survives.
    assert shown == "Something went wrong handling that request. Please try again."
    assert "db-prod-primary" not in shown
    assert "svc_acct" not in shown


def test_user_facing_error_keeps_credit_exhausted_guidance() -> None:
    # Arrange: an error flagged with the credit-exhausted marker.
    detail = f"anthropic {CREDIT_EXHAUSTED_MARKER}. account balance too low"

    # Act
    shown = user_facing_error_message(detail)

    # Assert: user gets re-auth guidance, not the raw provider billing text.
    assert "opensre auth login" in shown
    assert "balance too low" not in shown


def _make_tool(name: str, description: str) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}, "required": []},
        source="datadog",
        run=lambda **_kwargs: {},
    )


def _register(monkeypatch, tools: list[RegisteredTool]) -> None:
    """Install a controlled registry behind both accessors the label may use."""
    by_name = {tool.name: tool for tool in tools}
    monkeypatch.setattr("tools.registry.get_registered_tools", lambda: tools)
    monkeypatch.setattr("tools.registry.get_registered_tool", by_name.get)
    _tool_label.cache_clear()


def test_initial_status_message_is_non_empty() -> None:
    assert initial_status_message().strip()
    assert initial_status_message() != "Working…"


def test_normalize_gateway_status_rejects_working_placeholder() -> None:
    for banned in ("Working…", "Working...", "working", "  Working  ", ""):
        assert normalize_gateway_status(banned) in INITIAL_STATUSES
    assert normalize_gateway_status("keep this") == "keep this"


def test_response_label_maps_working_to_initial_status() -> None:
    assert status_from_response_label("working") in INITIAL_STATUSES
    assert status_from_response_label("") in INITIAL_STATUSES


def test_response_label_maps_assistant() -> None:
    assert status_from_response_label("assistant") == "💬 Composing your reply…"


def test_response_label_falls_back_for_unknown_label() -> None:
    assert status_from_response_label("gather") == "✨ gather…"


def test_tool_status_uses_registry_description(monkeypatch) -> None:
    tool = _make_tool(
        "query_datadog_monitors",
        "Query Datadog monitors for alert configuration and state.",
    )
    _register(monkeypatch, [tool])

    assert (
        status_from_tool_start("query_datadog_monitors", describe=describe_registered_tool)
        == "⏳ Query Datadog monitors for alert configuration and state…"
    )


def test_tool_status_falls_back_to_humanized_name(monkeypatch) -> None:
    _register(monkeypatch, [])

    assert status_from_tool_start("list_open_pull_requests") == "⏳ list open pull requests…"


def test_slash_invoke_status_does_not_claim_the_interactive_shell() -> None:
    """Gateway progress must not say a slash command ran in the local shell."""
    from tools.interactive_shell.shared.slash_catalog import slash_invoke_tool_description

    status = status_from_tool_start(
        "slash_invoke",
        {"command": "/cron", "args": ["list"]},
        describe=lambda _name: (slash_invoke_tool_description([]),),
    )

    label, argument = status.split("\n", 1)
    assert label == "⏳ Run a slash command…"
    assert "interactive shell" not in label
    assert argument == "(/cron)"


def test_tool_status_includes_first_input_hint(monkeypatch) -> None:
    tool = _make_tool("slash_invoke", "Run a registered interactive-shell slash command.")
    _register(monkeypatch, [tool])

    status = status_from_tool_start(
        "slash_invoke",
        {"command": "/integrations", "args": ["verify", "telegram"]},
        describe=describe_registered_tool,
    )
    assert status.startswith("⏳ Run a registered interactive-shell slash command…")
    assert "/integrations" in status


def test_tool_status_keeps_a_long_argument() -> None:
    """The hint is what gets copied out of the terminal, so it is not shortened."""
    status = status_from_tool_start(
        "shell_run",
        {"command": ("cat /Users/janvincentfranciszek/.opensre/ci-repair/987221fb5e15/result.md")},
    )
    assert "987221fb5e15/result.md" in status
    assert "…" not in status.split("(", 1)[-1]


def _describe_skill(_name: str) -> tuple[str, ...]:
    return (
        "Load the full body of one action-agent skill by name from the "
        "SKILLS INDEX. Call this in the same turn.",
    )


def test_tool_status_keeps_a_long_label_on_its_own_rows() -> None:
    """A gateway status may use three rows; the label and argument stay whole."""
    status = status_from_tool_start(
        "skill_view",
        {"name": "operating-github-ci-repairs"},
        describe=_describe_skill,
    )
    label, argument = status.split("\n", 1)
    assert "SKILLS INDEX" in label
    assert argument == "(operating-github-ci-repairs)"
    assert "……" not in status


def test_chat_status_keeps_the_label_and_drops_the_argument() -> None:
    """A shared chat shows the label. The argument stays on the shell."""
    status = status_from_tool_start(
        "shell_run",
        {"command": "echo sk-secret-token"},
    )

    headline = chat_status_headline(status)

    assert "sk-secret-token" not in headline
    assert "\n" not in headline
    assert headline.startswith("⏳")


def test_tool_label_miss_does_not_scan_the_whole_registry(monkeypatch) -> None:
    """A cache miss must resolve by name, not walk every registered tool.

    ``_tool_label`` is LRU-cached, but the gateway sees a long tail of tool
    names, and every miss used to run ``next(t for t in get_registered_tools()
    if t.name == …)`` — a linear pass over the full registry while a user waits
    on a status line.
    """
    # Arrange
    scans = 0

    def _scanning_list(*_args: object, **_kwargs: object) -> list[RegisteredTool]:
        nonlocal scans
        scans += 1
        return []

    monkeypatch.setattr("tools.registry.get_registered_tools", _scanning_list)
    monkeypatch.setattr("tools.registry.get_registered_tool", lambda _name: None)
    _tool_label.cache_clear()

    # Act: three distinct names, so every call is a cache miss.
    for name in ("alpha_tool", "beta_tool", "gamma_tool"):
        status_from_tool_start(name)

    # Assert
    assert scans == 0, f"label lookup listed the whole registry {scans} times"
