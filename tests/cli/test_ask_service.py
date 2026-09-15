from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

import pytest

from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.domain.types.tools import ToolSurface
from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from infrastructure.harness_providers import resolve_surface_tool_map
from infrastructure.turn_host.session_lock import session_execution_lock
from surfaces.cli.ask import service
from surfaces.cli.ask import session as ask_session
from surfaces.cli.ask.approval import unknown_allowed_tools
from surfaces.cli.ask.service import AskExitCode, AskSignal, AskStatus

_CHAT_ONLY_TOOL = "query_tempo"


def _turn(
    response: str = "answer",
    *,
    cancelled: bool = False,
    executed_count: int = 0,
) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_cancelled" if cancelled else "answer",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=executed_count,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        ),
        assistant_response_text=response,
    )


def _not_run_turn() -> TurnResult:
    return TurnResult(
        final_intent="agent_failed",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=True,
            handled=True,
            accounting_status="not_run",
        ),
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


def _read_only_request() -> ToolExecutionRequest:
    tool = RegisteredTool(
        name="update_plan",
        description="Update the plan",
        input_schema={"type": "object", "properties": {}},
        source="plan",
        run=lambda: None,
        side_effect_level=SideEffectLevel.READ_ONLY,
    )
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-plan", name=tool.name, input={}),
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
        self.pending_user_choice: PendingUserChoice | None = None
        self.questions_already_answered: set[str] = set()
        self.session_id = "session-123"


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

    def chat(self, _prompt: str, **_kwargs: object) -> object:
        raise RuntimeError("turn failed")


def test_run_ask_returns_success(monkeypatch) -> None:
    monkeypatch.setattr(service, "_run_agent_turn", lambda _prompt, _hooks, **_kwargs: _turn())

    outcome = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.SUCCESS
    assert outcome.response == "answer"
    assert outcome.exit_code is AskExitCode.SUCCESS


def test_run_ask_returns_structured_required_choice(monkeypatch) -> None:
    pending = PendingUserChoice(title="Which environment?", options=("Production", "Staging"))

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        state = kwargs["run_state"]
        assert isinstance(state, service._AskRunState)
        state.session_id = "session-123"
        state.pending_choice = pending
        return _turn("")

    monkeypatch.setattr(service, "_run_agent_turn", run_turn)

    outcome = service.run_ask("deploy", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.NEEDS_INPUT
    assert outcome.exit_code is AskExitCode.NEEDS_INPUT
    assert outcome.session_id == "session-123"
    assert outcome.questions[0].options == ("Production", "Staging")
    assert "1. Production" in outcome.response


def test_run_ask_surfaces_new_choice_after_later_model_failure(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()

    class _ChoiceThenFailureAgentSession:
        @classmethod
        def start(cls, _config: object, **_kwargs: object) -> _ChoiceThenFailureAgentSession:
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            session.pending_user_choice = PendingUserChoice(
                title="Which environment?",
                options=("Production", "Staging"),
            )
            return _not_run_turn()

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _ChoiceThenFailureAgentSession)

    outcome = service.run_ask("deploy", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.NEEDS_INPUT
    assert outcome.session_id == session.session_id
    assert outcome.questions[0].title == "Which environment?"
    assert outcome.exit_code is AskExitCode.NEEDS_INPUT
    assert manager.closed == [(session, False)]


def test_resume_prompt_maps_a_number_to_the_pending_option() -> None:
    session = service.SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )

    resumed = ask_session.resume_prompt(session, "2")

    assert resumed == '1. Which environment?\n@json:"Staging"'
    assert session.pending_user_choice is None


def test_resume_prompt_preserves_blank_lines_in_a_custom_answer() -> None:
    from core.agent_harness.session.pending_choice import parse_ask_user_answers

    session = service.SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Describe the deployment window",
        options=(),
    )

    resumed = ask_session.resume_prompt(session, "First window\n\nSecond window")

    assert parse_ask_user_answers(resumed) == [
        ("Describe the deployment window", "First window\n\nSecond window")
    ]


def test_resume_prompt_resets_clarification_rounds_for_a_new_request() -> None:
    session = service.SessionCore()
    session.ask_user_rounds = 2

    assert ask_session.resume_prompt(session, "check the next deployment") == (
        "check the next deployment"
    )
    assert session.ask_user_rounds == 0

    session.pending_user_choice = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.ask_user_rounds = 2
    ask_session.resume_prompt(session, "1")
    assert session.ask_user_rounds == 2


def test_resume_prompt_rejects_custom_answer_when_choice_forbids_it() -> None:
    session = service.SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
        custom_answer=False,
    )

    with pytest.raises(service.OpenSREError, match="numbered options"):
        ask_session.resume_prompt(session, "another environment")

    assert session.pending_user_choice is not None


def test_resume_prompt_rejects_interactive_command_choices() -> None:
    session = service.SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Start over?",
        options=("Start over", "Keep working"),
        commands={"Start over": "/new"},
    )

    with pytest.raises(service.OpenSREError, match="cannot be answered headlessly"):
        ask_session.resume_prompt(session, "1")

    assert session.pending_user_choice is not None


def test_resume_prompt_requires_structured_batch_answers() -> None:
    from core.agent_harness.session.pending_choice import AskUserQuestion

    session = service.SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=("Repository A", "Repository B"),
        questions=(
            AskUserQuestion("Repo", "Which repository?", ("Repository A", "Repository B")),
            AskUserQuestion("Window", "Which window?", ("24 hours", "7 days")),
        ),
    )

    resumed = ask_session.resume_prompt(session, '{"Repo":"2","Window":"1"}')

    from core.agent_harness.session.pending_choice import parse_ask_user_answers

    assert parse_ask_user_answers(resumed) == [
        ("Which repository?", "Repository B"),
        ("Which window?", "24 hours"),
    ]


def test_resumed_session_rejects_overlapping_processes(monkeypatch, tmp_path) -> None:
    session_id = "session-123"
    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir",
        lambda: tmp_path,
    )

    with (
        session_execution_lock(session_id),
        pytest.raises(service.OpenSREError, match="busy"),
        ask_session.ask_session_lock(session_id),
    ):
        pytest.fail("busy session lock was unexpectedly acquired")


def test_run_ask_forwards_a_tool_event_observer(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def observer(_kind: str, _data: dict[str, object]) -> None:
        """Observe agent tool lifecycle events."""

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        recorded.update(kwargs)
        return _turn()

    monkeypatch.setattr(service, "_run_agent_turn", run_turn)

    outcome = service.run_ask(
        "prompt",
        allowed_tools=(),
        bypass_approvals=False,
        tool_event_observer=observer,
    )

    assert outcome.status is AskStatus.SUCCESS
    assert recorded["tool_event_observer"] is observer
    assert recorded["session_id"] is None
    assert isinstance(recorded["fresh_session_id"], str)
    assert recorded["ephemeral"] is False
    assert isinstance(recorded["run_state"], service._AskRunState)


def test_run_ask_leases_a_fresh_persisted_session_before_its_first_turn(monkeypatch) -> None:
    from contextlib import contextmanager

    captured: list[str | None] = []
    turn_args: dict[str, object] = {}

    @contextmanager
    def _lock(session_id: str | None):
        captured.append(session_id)
        yield

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        turn_args.update(kwargs)
        return _turn()

    monkeypatch.setattr(service, "_ask_session_lock", _lock)
    monkeypatch.setattr(service, "_run_agent_turn", run_turn)
    monkeypatch.setattr(service, "uuid4", lambda: "fresh-session-id")

    service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert captured == ["fresh-session-id"]
    assert turn_args["session_id"] is None
    assert turn_args["fresh_session_id"] == "fresh-session-id"


def test_run_ask_resolves_session_prefix_before_resuming(monkeypatch) -> None:
    class _Repo:
        def count_prefix_matches(self, prefix: str) -> int:
            assert prefix == "abc123"
            return 1

        def load_session(self, prefix: str) -> dict[str, str]:
            assert prefix == "abc123"
            return {"session_id": "abc123-full-session-id"}

    recorded: dict[str, object] = {}

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        recorded.update(kwargs)
        return _turn()

    monkeypatch.setattr(ask_session, "default_session_repo", _Repo)
    monkeypatch.setattr(service, "_run_agent_turn", run_turn)

    outcome = service.run_ask(
        "continue",
        allowed_tools=(),
        bypass_approvals=False,
        resume_session_id="abc123",
    )

    assert outcome.status is AskStatus.SUCCESS
    assert recorded["session_id"] == "abc123-full-session-id"


def test_run_ask_reports_a_missing_resume_session(monkeypatch) -> None:
    class _Repo:
        def count_prefix_matches(self, _prefix: str) -> int:
            return 0

    monkeypatch.setattr(ask_session, "default_session_repo", _Repo)

    outcome = service.run_ask(
        "continue",
        allowed_tools=(),
        bypass_approvals=False,
        resume_session_id="missing",
    )

    assert outcome.status is AskStatus.ERROR
    assert outcome.error is not None
    assert "not found" in outcome.error.message


def test_run_ask_rejects_a_non_session_reference() -> None:
    outcome = service.run_ask(
        "continue",
        allowed_tools=(),
        bypass_approvals=False,
        resume_session_id="abc123:old-entry",
    )

    assert outcome.status is AskStatus.ERROR
    assert outcome.error is not None
    assert "invalid" in outcome.error.message


def test_run_ask_returns_the_rendered_answer_not_raw_tool_history(monkeypatch) -> None:
    """One-shot output must not dump command evidence retained by the harness."""

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        output = kwargs["output"]
        assert isinstance(output, service._AskOutputSink)
        output.stream(label="OpenSRE", chunks=iter(["The repository is an SRE agent framework."]))
        output.mark_turn_complete()
        return _turn("git remote -v\nREADME contents\nThe repository is an SRE agent framework.")

    monkeypatch.setattr(service, "_run_agent_turn", run_turn)

    outcome = service.run_ask("what is this repo doing?", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.SUCCESS
    assert outcome.response == "The repository is an SRE agent framework."
    assert "git remote" not in outcome.response


def test_run_ask_preserves_a_rendered_agent_error(monkeypatch) -> None:
    """Suppressing raw tool history must not hide an actionable agent error."""

    def run_turn(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        output = kwargs["output"]
        assert isinstance(output, service._AskOutputSink)
        output.render_error("The configured model is unavailable.")
        output.mark_turn_complete()
        return TurnResult(
            final_intent="agent_completed",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=True,
                handled=False,
                response_text="raw internal diagnostic",
                accounting_status="not_run",
            ),
            assistant_response_text="raw internal diagnostic",
        )

    monkeypatch.setattr(service, "_run_agent_turn", run_turn)

    outcome = service.run_ask("why is checkout slow?", allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.ERROR
    assert outcome.response == ""
    assert outcome.error is not None
    assert outcome.error.message == "The configured model is unavailable."


def test_ask_output_sink_uses_the_latest_rendered_event() -> None:
    """A multi-turn goal exposes only its final response or error."""
    output = service._AskOutputSink()

    output.stream(label="OpenSRE", chunks=iter(["First investigation update."]))
    output.stream(label="OpenSRE", chunks=iter(["Final investigation summary."]))
    assert output.rendered_response == "Final investigation summary."

    output.render_error("The final model request failed.")
    assert output.rendered_response == "The final model request failed."


def test_ask_log_scope_suppresses_unrendered_fallback_warnings(monkeypatch) -> None:
    """Internal tool warnings must not bypass the one-shot answer renderer."""

    class _CaptureHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    logger = logging.getLogger("tests.cli.ask.fallback_warning")
    original_logger_handlers = list(logger.handlers)
    original_logger_propagate = logger.propagate
    original_logger_level = logger.level
    capture = _CaptureHandler()
    for handler in original_handlers:
        root.removeHandler(handler)
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.WARNING)
    root.setLevel(logging.WARNING)
    monkeypatch.setattr(logging, "lastResort", capture)
    try:
        with service._ask_log_scope():
            logger.warning("subprocess error: exit 1")
        assert capture.messages == []
    finally:
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)
        logger.handlers[:] = original_logger_handlers
        logger.propagate = original_logger_propagate
        logger.setLevel(original_logger_level)


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


def test_failed_resumed_turn_restores_pending_choice_state(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    pending = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.pending_user_choice = pending
    session.questions_already_answered = {"earlier question"}
    _FakeAgentSession.session = session
    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _FakeAgentSession)

    with pytest.raises(RuntimeError, match="turn failed"):
        service._run_agent_turn(
            "1",
            ToolExecutionHooks(),
            session_id=session.session_id,
            ephemeral=False,
        )

    assert session.pending_user_choice == pending
    assert session.questions_already_answered == {"earlier question"}
    assert manager.closed == [(session, False)]


@pytest.mark.parametrize("result", [_not_run_turn(), _turn(cancelled=True)])
def test_unsuccessful_resumed_turn_restores_pending_choice_state(
    monkeypatch, result: TurnResult
) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    pending = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.pending_user_choice = pending
    session.questions_already_answered = {"earlier question"}

    class _UnsuccessfulAgentSession:
        @classmethod
        def start(cls, _config: object, **_kwargs: object) -> _UnsuccessfulAgentSession:
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            return result

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _UnsuccessfulAgentSession)

    service._run_agent_turn(
        "1", ToolExecutionHooks(), session_id=session.session_id, ephemeral=False
    )

    assert session.pending_user_choice == pending
    assert session.questions_already_answered == {"earlier question"}
    assert manager.closed == [(session, False)]


def test_incomplete_resumed_turn_keeps_a_newly_queued_choice(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    replacement = PendingUserChoice(
        title="Which region?",
        options=("us-east-1", "eu-west-1"),
    )

    class _ReplacementChoiceAgentSession:
        @classmethod
        def start(cls, _config: object, **_kwargs: object) -> _ReplacementChoiceAgentSession:
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            session.pending_user_choice = replacement
            return _not_run_turn()

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _ReplacementChoiceAgentSession)
    run_state = service._AskRunState()

    service._run_agent_turn(
        "1",
        ToolExecutionHooks(),
        session_id=session.session_id,
        ephemeral=False,
        run_state=run_state,
    )

    assert session.pending_user_choice == replacement
    assert run_state.pending_choice == replacement
    assert run_state.pending_choice_is_new is True
    assert manager.closed == [(session, False)]


def test_cancelled_resumed_turn_after_tool_work_does_not_restore_choice(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.questions_already_answered = {"earlier question"}

    class _CancelledAfterActionAgentSession:
        @classmethod
        def start(cls, _config: object, **_kwargs: object) -> _CancelledAfterActionAgentSession:
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            return _turn(cancelled=True, executed_count=1)

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _CancelledAfterActionAgentSession)

    service._run_agent_turn(
        "1", ToolExecutionHooks(), session_id=session.session_id, ephemeral=False
    )

    assert session.pending_user_choice is None
    assert session.questions_already_answered == {"earlier question", "which environment?"}
    assert manager.closed == [(session, False)]


@pytest.mark.parametrize(
    ("request_factory", "restores_choice"),
    [(_risky_request, False), (_read_only_request, True)],
)
def test_not_run_resumed_turn_restores_choice_until_mutating_work_starts(
    monkeypatch,
    request_factory: Callable[[], ToolExecutionRequest],
    restores_choice: bool,
) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    pending = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.pending_user_choice = pending
    session.questions_already_answered = {"earlier question"}

    class _FailedAfterToolAgentSession:
        hooks: ToolExecutionHooks | None = None

        @classmethod
        def start(cls, _config: object, **kwargs: object) -> _FailedAfterToolAgentSession:
            cls.hooks = kwargs["tool_hooks"]
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            assert type(self).hooks is not None
            assert type(self).hooks.before_tool_call is not None
            type(self).hooks.before_tool_call(request_factory())
            return _not_run_turn()

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _FailedAfterToolAgentSession)

    service._run_agent_turn(
        "1", ToolExecutionHooks(), session_id=session.session_id, ephemeral=False
    )

    assert session.pending_user_choice == (pending if restores_choice else None)
    assert session.questions_already_answered == (
        {"earlier question"} if restores_choice else {"earlier question", "which environment?"}
    )
    assert manager.closed == [(session, False)]


def test_not_run_resumed_turn_after_blocked_tool_restores_choice(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    pending = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.pending_user_choice = pending
    session.questions_already_answered = {"earlier question"}

    class _BlockedToolAgentSession:
        hooks: ToolExecutionHooks | None = None

        @classmethod
        def start(cls, _config: object, **kwargs: object) -> _BlockedToolAgentSession:
            cls.hooks = kwargs["tool_hooks"]
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            assert type(self).hooks is not None
            assert type(self).hooks.before_tool_call is not None
            type(self).hooks.before_tool_call(_risky_request())
            return _not_run_turn()

    blocked_hooks = ToolExecutionHooks(
        before_tool_call=lambda _request: BeforeToolCallResult(blocked=True),
    )
    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _BlockedToolAgentSession)

    service._run_agent_turn("1", blocked_hooks, session_id=session.session_id, ephemeral=False)

    assert session.pending_user_choice == pending
    assert session.questions_already_answered == {"earlier question"}
    assert manager.closed == [(session, False)]


def test_signal_during_resumed_turn_restores_pending_choice_state(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    pending = PendingUserChoice(
        title="Which environment?",
        options=("Production", "Staging"),
    )
    session.pending_user_choice = pending
    session.questions_already_answered = {"earlier question"}

    class _SignalAgentSession:
        @classmethod
        def start(cls, _config: object, **_kwargs: object) -> _SignalAgentSession:
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str) -> TurnResult:
            raise service.AskSignal(signal.SIGINT)

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _SignalAgentSession)

    with pytest.raises(service.AskSignal):
        service._run_agent_turn(
            "1", ToolExecutionHooks(), session_id=session.session_id, ephemeral=False
        )

    assert session.pending_user_choice == pending
    assert session.questions_already_answered == {"earlier question"}
    assert manager.closed == [(session, False)]


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

        def chat(self, prompt: str, **_kwargs: object) -> TurnResult:
            recorded["prompt"] = prompt
            return _turn()

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _RecordingAgentSession)

    # Act
    result = service._run_agent_turn("hello", hooks)

    # Assert: hooks bound, forbidden capability zeroed (unrelated kept), one-shot
    # dispatched, ephemeral session closed without memory extraction.
    assert recorded["tool_hooks"] is hooks
    assert recorded["is_tty"] is False
    assert recorded["surface"] == "headless_cli"
    assert session.available_capabilities["slash_commands"] == ()
    assert session.available_capabilities["ask_user_choice"] == ()
    assert session.available_capabilities["shell"] == ("keep",)
    assert recorded["prompt"] == "hello"
    assert result.primary_response_text == "answer"
    assert manager.closed == [(session, False)]


def test_agent_turn_passes_tool_events_to_the_default_agent_build(monkeypatch) -> None:
    manager = _FakeSessionManager()
    session = _FakeSession()
    recorded: dict[str, object] = {}

    class _RecordingAgentSession:
        @classmethod
        def start(cls, _config: object, **kwargs: object) -> _RecordingAgentSession:
            recorded.update(kwargs)
            return cls()

        @property
        def bound_session(self) -> _FakeSession:
            return session

        def chat(self, _prompt: str, **_kwargs: object) -> TurnResult:
            return _turn()

    def observer(_kind: str, _data: dict[str, object]) -> None:
        """Observe agent tool lifecycle events."""

    monkeypatch.setattr(service, "SessionManager", lambda: manager)
    monkeypatch.setattr(service, "AgentSession", _RecordingAgentSession)

    service._run_agent_turn("hello", ToolExecutionHooks(), tool_event_observer=observer)

    assert recorded["tool_event_observer"] is observer
    assert manager.closed == [(session, False)]


def test_run_ask_reports_denial_before_agent_failure(monkeypatch) -> None:
    def deny_then_fail(_prompt: str, hooks, **_kwargs: object) -> TurnResult:
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

    def run_tool(_prompt: str, hooks: ToolExecutionHooks, **_kwargs: object) -> TurnResult:
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

    def exhaust_credits(_prompt: str, _hooks: ToolExecutionHooks, **_kwargs: object) -> TurnResult:
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
        lambda _prompt, _hooks, **_kwargs: _turn(""),
    )
    incomplete = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    monkeypatch.setattr(
        service,
        "_run_agent_turn",
        lambda _prompt, _hooks, **_kwargs: _turn("stopped", cancelled=True),
    )
    cancelled = service.run_ask("prompt", allowed_tools=(), bypass_approvals=False)

    assert incomplete.status is AskStatus.ERROR
    assert incomplete.exit_code is AskExitCode.ERROR
    assert cancelled.status is AskStatus.CANCELLED
    assert cancelled.exit_code is AskExitCode.SIGINT


@pytest.mark.parametrize(
    ("result", "status", "exit_code"),
    [
        (_not_run_turn(), AskStatus.ERROR, AskExitCode.ERROR),
        (_turn("stopped", cancelled=True), AskStatus.CANCELLED, AskExitCode.SIGINT),
    ],
)
def test_run_ask_reports_unsuccessful_resume_before_pending_choice(
    monkeypatch,
    result: TurnResult,
    status: AskStatus,
    exit_code: AskExitCode,
) -> None:
    pending = PendingUserChoice(title="Which environment?", options=("Production", "Staging"))

    def failed_resume(_prompt: str, _hooks: ToolExecutionHooks, **kwargs: object) -> TurnResult:
        run_state = kwargs["run_state"]
        assert isinstance(run_state, service._AskRunState)
        run_state.session_id = "session-123"
        run_state.pending_choice = pending
        return result

    monkeypatch.setattr(service, "_resolve_resume_session_id", lambda session_id: session_id)
    monkeypatch.setattr(service, "_run_agent_turn", failed_resume)

    outcome = service.run_ask(
        "1",
        allowed_tools=(),
        bypass_approvals=False,
        resume_session_id="session-123",
    )

    assert outcome.status is status
    assert outcome.exit_code is exit_code
    assert outcome.questions == ()


@pytest.mark.parametrize(
    ("signum", "exit_code"),
    [(signal.SIGINT, AskExitCode.SIGINT), (signal.SIGTERM, AskExitCode.SIGTERM)],
)
def test_run_ask_maps_signals(monkeypatch, signum: int, exit_code: AskExitCode) -> None:
    def raise_signal(_prompt: str, _hooks, **_kwargs: object) -> TurnResult:
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
