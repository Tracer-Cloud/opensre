"""The real analyzer closes a missing-app-connection turn without repeated calls."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from core.agent_harness.ports import TurnBinding
from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.tools.action_tools import get_action_tool
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.tool import RegisteredTool
from infrastructure.analytics.prompt_log import recorder as prompt_log
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    FakeSlashPorts,
    no_tool_response,
    tool_response,
)

_ANALYZER = "analyze_github_ci_reliability"
_REPOSITORY = {"owner": "acme", "repo": "app"}
_CLOSING = "Connect GitHub in the OpenSRE app to continue."


def _no_github_token(**_kwargs: object) -> str:
    """No token resolves: the analyzer answers with its setup envelope."""
    return ""


def _action_tool(name: str) -> RegisteredTool:
    tool = get_action_tool(name)
    assert tool is not None, name
    return tool


@pytest.mark.parametrize(
    "tool_name,arguments",
    [(_ANALYZER, _REPOSITORY), ("scan_github_ci_health", {"owners": ["acme"]})],
)
def test_missing_app_connection_ends_the_real_turn(
    tool_name: str,
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: analytics are captured instead of sent.
    generations: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(prompt_log, "capture_ai_generation", generations.append)
    monkeypatch.setattr("config.prompt_log.read_prompt_log_settings", dict)
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_agent_tool_call_completed",
        lambda **properties: tool_calls.append(properties),
    )
    # The model as observed live: had the turn gone on, it would have retried
    # the analyzer and then repeated the identical slash_invoke.
    llm = FakeActionLLM(
        [
            tool_response(tool_name, arguments),
            no_tool_response(_CLOSING),
            tool_response(tool_name, arguments),
            no_tool_response(_CLOSING),
        ]
    )
    session = Session(store=InMemorySessionStore())
    session.resolved_integrations_cache = {}
    session.store.open_session(session)
    ports = FakeSlashPorts(tty=True)
    output = BufferOutputSink()
    loop_ends: list[dict[str, Any]] = []

    def _observe(kind: str, data: dict[str, Any]) -> None:
        if kind == "agent_end":
            loop_ends.append(data)

    provider = DefaultToolProvider(
        session,
        Console(file=io.StringIO(), force_terminal=False),
        precomputed_action_tools=[_action_tool(tool_name), _action_tool("slash_invoke")],
        slash_ports_factory=lambda: ports,
        observer_factory=lambda _message: _observe,
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )

    # Act
    turn = agent.handle(
        "Analyze the CI reliability of acme/app",
        TurnBinding(session=session, is_tty=True),
    )

    # Assert: the app blocker closes the turn without setup or repeated tool calls.
    assert llm.invocations == 2
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    assert ports.dispatched == []
    assert [end["stop_reason"] for end in loop_ends] == ["completed"]
    assert loop_ends[0]["hit_iteration_cap"] is False
    assert turn.action_result is not None
    assert turn.action_result.hit_iteration_cap is False
    assert "blocked" not in [call["outcome"] for call in tool_calls]
    assert [generation["$ai_is_error"] for generation in generations] == [False]
    # With no model closing, the tool's reply text closes the turn: it must be
    # the user's line, not the instructions written for the model.
    for closing in ("\n".join(output.streamed), turn.primary_response_text):
        assert "GitHub" in closing
        assert "OpenSRE app" in closing
        assert "opensre integrations setup github" not in closing
        assert "slash_invoke" not in closing
        assert "end the turn" not in closing
