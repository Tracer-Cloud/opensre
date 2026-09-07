from __future__ import annotations

import signal
import threading

import pytest

from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.domain.types.tools import ToolSurface
from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import ToolExecutionHooks, ToolExecutionRequest
from infrastructure.harness_providers import resolve_surface_tool_map
from surfaces.cli.ask import service
from surfaces.cli.ask.approval import unknown_allowed_tools
from surfaces.cli.ask.service import AskExitCode, AskSignal, AskStatus

_CHAT_ONLY_TOOL = "query_tempo"


def _turn(
    response: str = "answer",
    *,
    cancelled: bool = False,
) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_cancelled" if cancelled else "answer",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        ),
        assistant_response_text=response,
    )


def _risky_request() -> ToolExecutionRequest:
    tool = RegisteredTool(
        name="shell_run",
        description="Run shell",
        input_schema={"type": "object", "properties": {}},
        source="shell",
        run=lambda: None,
        side_effect_level=SideEffectLevel.MUTATING,
    )
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool.name, input={}),
        tool=tool,
        arguments={},
        source=tool.source,
        resolved_integrations={},
    )


def _chat_only_request() -> ToolExecutionRequest:
    action_tools = resolve_surface_tool_map(ToolSurface.ACTION)
    tool = resolve_surface_tool_map(ToolSurface.CHAT)[_CHAT_ONLY_TOOL]
    assert _CHAT_ONLY_TOOL not in action_tools
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-chat", name=tool.name, input={}),
        tool=tool,
        arguments={},
        source=tool.source,
        resolved_integrations={},
    )


class _FakeSessionManager:
    def __init__(self) -> None:
        self.closed: list[tuple[object, bool]] = []

    def close(self, session: object, *, extract_memory: bool) -> None:
        self.closed.append((session, extract_memory))


class _FakeSession:
    def __init__(self) -> None:
        self.available_capabilities: dict[str, object] = {}


class _FakeAgentSession:
    session = _FakeSession()

    @classmethod
    def start(cls, _config: object, **kwargs: object) -> _FakeAgentSession:
        prepare = kwargs.get("prepare_session")
        if callable(prepare):
            prepare(cls.session)
        return cls()

    @property
    def bound_session(self) -> _FakeSession:
        return type(self).session

    def chat_until_goal(self, _prompt: str) -> object:
        raise RuntimeError("turn failed")


class _GoalRun:
    """Stand-in for SessionGoalRunResult: only ``last_result`` is read."""

    def __init__(self, last_result: TurnResult) -> None:
        self.last_result = last_result


def test_run_ask_returns_success(monkeypatch) -> None:
    monkeypatch.setattr(service, "_run_agent_turn", lambda _prompt, _hooks: _turn())

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.SUCCESS
    assert outcome.response == "answer"
    assert outcome.exit_code is AskExitCode.SUCCESS


def test_agent_turn_closes_ephemeral_session_after_failure(monkeypatch) -> None:
    # Arrange: the built session fails its turn; the ephemeral session must
    # still be closed (extract_memory=False) via the finally block.
    manager = _FakeSessionManager()
    _FakeAgentSession.session = _FakeSession()
    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _FakeAgentSession)

    # Act / Assert
    with pytest.raises(RuntimeError, match="turn failed"):
        service._run_agent_turn("prompt", ToolExecutionHooks())

    assert manager.closed == [(_FakeAgentSession.session, False)]


def test_agent_turn_binds_hooks_and_restricts_capabilities_via_start(monkeypatch) -> None:
    """The collapse onto AgentSession.start must still bind the approval hooks
    and strip the one-shot ask agent's forbidden capabilities."""
    # Arrange: record what ask hands AgentSession.start, and let its
    # prepare_session run against a session carrying a forbidden capability.
    manager = _FakeSessionManager()
    session = _FakeSession()
    session.available_capabilities = {"slash_commands": ("live",), "shell": ("keep",)}
    recorded: dict[str, object] = {}
    hooks = ToolExecutionHooks()

    class _RecordingAgentSession:
        @classmethod
        def start(cls, _config: object, **kwargs: object) -> _RecordingAgentSession:
            recorded.update(kwargs)
            prepare = kwargs["prepare_session"]
            assert callable(prepare)
            prepare(session)
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat_until_goal(self, prompt: str) -> _GoalRun:
            # chat_until_goal, not chat: ask must run the session-goal loop so a
            # multi-step turn completes instead of stopping after the first.
            recorded["prompt"] = prompt
            return _GoalRun(_turn())

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _RecordingAgentSession)

    # Act
    result = service._run_agent_turn("hello", hooks)

    # Assert: hooks bound, forbidden capability zeroed (unrelated kept), one-shot
    # dispatched, ephemeral session closed without memory extraction.
    assert recorded["tool_hooks"] is hooks
    assert recorded["is_tty"] is False
    assert session.available_capabilities["slash_commands"] == ()
    assert session.available_capabilities["shell"] == ("keep",)
    assert recorded["prompt"] == "hello"
    assert result.primary_response_text == "answer"
    assert manager.closed == [(session, False)]


def test_run_ask_reports_denial_before_agent_failure(monkeypatch) -> None:
    def deny_then_fail(_prompt: str, hooks) -> TurnResult:
        assert hooks.before_tool_call is not None
        decision = hooks.before_tool_call(_risky_request())
        assert decision is not None and decision.blocked
        raise RuntimeError("downstream detail")

    monkeypatch.setattr(service, "_run_agent_turn", deny_then_fail)

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.APPROVAL_DENIED
    assert outcome.denied_tools == ("shell_run",)
    assert outcome.exit_code is AskExitCode.APPROVAL_DENIED
    assert "downstream detail" not in outcome.response
    # The denial is actionable: it names the exact flags that unblock the run.
    assert "--allowed-tool shell_run" in outcome.response
    assert "--dangerously-bypass-approvals" in outcome.response


def test_chat_only_tool_denial_suggests_valid_authorized_rerun(monkeypatch) -> None:
    request = _chat_only_request()

    def run_tool(_prompt: str, hooks: ToolExecutionHooks) -> TurnResult:
        assert hooks.before_tool_call is not None
        decision = hooks.before_tool_call(request)
        assert decision is not None
        if decision.blocked:
            raise RuntimeError("tool call blocked")
        assert decision.approved
        return _turn("trace results")

    monkeypatch.setattr(service, "_run_agent_turn", run_tool)

    denied = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert denied.status is AskStatus.APPROVAL_DENIED
    assert denied.denied_tools == (_CHAT_ONLY_TOOL,)
    assert f"--allowed-tool {_CHAT_ONLY_TOOL}" in denied.response
    assert unknown_allowed_tools((_CHAT_ONLY_TOOL, "query_temop")) == ("query_temop",)

    authorized = service.run_ask(
        "prompt",
        allowed_tools=(_CHAT_ONLY_TOOL,),
        bypass_approvals=False,
    )

    assert authorized.status is AskStatus.SUCCESS
    assert authorized.response == "trace results"


def test_run_ask_maps_hosted_credit_exhaustion_to_nonzero_upgrade_error(
    monkeypatch,
) -> None:
    from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError

    upgrade_url = "https://app.opensre.test/usage"

    def exhaust_credits(_prompt: str, _hooks: ToolExecutionHooks) -> TurnResult:
        raise OpenSRECreditsExhaustedError(
            "OpenSRE hosted credits are exhausted.",
            upgrade_url=upgrade_url,
        )

    monkeypatch.setattr(service, "_run_agent_turn", exhaust_credits)

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.ERROR
    assert outcome.exit_code is AskExitCode.ERROR
    assert outcome.error is not None
    assert outcome.error.suggestion is not None
    assert upgrade_url in outcome.error.suggestion


def test_run_ask_maps_incomplete_and_cancelled_turns(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda _prompt, _hooks: _turn(""),
    )
    incomplete = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda _prompt, _hooks: _turn("stopped", cancelled=True),
    )
    cancelled = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert incomplete.status is AskStatus.ERROR
    assert incomplete.exit_code is AskExitCode.ERROR
    assert cancelled.status is AskStatus.CANCELLED
    assert cancelled.exit_code is AskExitCode.SIGINT


@pytest.mark.parametrize(
    ("signum", "exit_code"),
    [(signal.SIGINT, AskExitCode.SIGINT), (signal.SIGTERM, AskExitCode.SIGTERM)],
)
def test_run_ask_maps_signals(monkeypatch, signum: int, exit_code: AskExitCode) -> None:
    def raise_signal(_prompt: str, _hooks) -> TurnResult:
        raise AskSignal(signum)

    monkeypatch.setattr(service, "_run_agent_turn", raise_signal)

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.CANCELLED
    assert outcome.exit_code is exit_code


def test_signal_scope_requests_cancel_and_restores_handlers(monkeypatch) -> None:
    prior = {signal.SIGINT: object(), signal.SIGTERM: object()}
    installed: dict[signal.Signals, object] = {}
    restored: dict[signal.Signals, object] = {}

    monkeypatch.setattr(signal, "getsignal", lambda sig: prior[sig])

    def fake_signal(sig: signal.Signals, handler: object) -> None:
        if sig in installed:
            restored[sig] = handler
        else:
            installed[sig] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    cancel_event = threading.Event()

    with pytest.raises(AskSignal), service.ask_signal_scope(cancel_event):
        handler = installed[signal.SIGINT]
        assert callable(handler)
        handler(signal.SIGINT, None)

    assert cancel_event.is_set()
    assert restored == prior
