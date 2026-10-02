"""A queued setup wizard ends the action turn instead of looping into the iteration limit.

Incident: with no GitHub token, ``analyze_github_ci_reliability`` returned a
``tool_unavailable`` envelope naming ``/integrations setup github``. The model
queued that wizard through ``slash_invoke`` and was told ``{"ok": true}``, but the
turn went on: the goal reviewer rejected the stop because the failed analyzer
was the last work tool, the model retried it, the duplicate guard blocked the
identical ``slash_invoke``, and the turn ended at the iteration limit with an
error status. This drives the real analyzer, loop, hook chain, and goal reviewer.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from config.constants.github import GITHUB_INTEGRATION_SETUP_SLASH
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
_OPEN_SETUP = {"command": "/integrations", "args": ["setup", "github"]}
_CLOSING = "Opening the GitHub setup wizard; run the analysis again once it is connected."


def _no_github_token(_token: str | None = None) -> str:
    """No token resolves: the analyzer answers with its setup envelope."""
    return ""


def _action_tool(name: str) -> RegisteredTool:
    tool = get_action_tool(name)
    assert tool is not None, name
    return tool


def test_a_setup_wizard_queued_after_a_missing_token_ends_the_turn(
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
    monkeypatch.setattr(
        "integrations.github.tools.ci_analytics.tool.resolve_github_token", _no_github_token
    )
    # The model as observed live: had the turn gone on, it would have retried
    # the analyzer and then repeated the identical slash_invoke.
    llm = FakeActionLLM(
        [
            tool_response(_ANALYZER, _REPOSITORY),
            tool_response("slash_invoke", _OPEN_SETUP),
            no_tool_response(_CLOSING),
            tool_response(_ANALYZER, _REPOSITORY),
            tool_response("slash_invoke", _OPEN_SETUP),
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
        precomputed_action_tools=[_action_tool(_ANALYZER), _action_tool("slash_invoke")],
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

    # Assert: the wizard waits in the auto-submit slot and the turn ended there.
    assert llm.invocations == 2
    assert session.terminal.pending_prompt_default == GITHUB_INTEGRATION_SETUP_SLASH
    assert session.terminal.pending_prompt_autosubmit is True
    assert ports.dispatched == []
    assert [end["stop_reason"] for end in loop_ends] == ["tool_terminated"]
    assert loop_ends[0]["hit_iteration_cap"] is False
    assert turn.action_result is not None
    assert turn.action_result.hit_iteration_cap is False
    assert "blocked" not in [call["outcome"] for call in tool_calls]
    assert [generation["$ai_is_error"] for generation in generations] == [False]
    # With no model closing, the tool's reply text closes the turn: it must be
    # the user's line, not the instructions written for the model.
    for closing in ("\n".join(output.streamed), turn.primary_response_text):
        assert "GitHub isn't connected yet" in closing
        assert "opensre integrations setup github" in closing
        assert "slash_invoke" not in closing
        assert "end the turn" not in closing
