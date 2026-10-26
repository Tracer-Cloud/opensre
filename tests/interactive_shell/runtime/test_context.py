"""Tests for validated interactive-shell runtime context assembly."""

from __future__ import annotations

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from pydantic import ValidationError

from infrastructure.scheduling.task_registry import TaskRegistry
from surfaces.interactive_shell.controller import InteractiveShellController
from surfaces.interactive_shell.runtime.context import (
    ReplRuntime,
    SessionBootstrapSpec,
    create_repl_runtime,
)
from surfaces.interactive_shell.runtime.core.state import (
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.session import Session


def _prompt_session() -> PromptSession[str]:
    return PromptSession(history=InMemoryHistory())


def test_create_context_applies_canonical_session_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = TaskRegistry()
    hydrate_calls: list[str] = []

    def _hydrate(self: Session) -> None:
        hydrate_calls.append(self.session_id)
        self.configured_integrations = ("github",)
        self.configured_integrations_known = True

    monkeypatch.setattr(Session, "hydrate_configured_integrations", _hydrate)
    monkeypatch.setattr(TaskRegistry, "persistent", staticmethod(lambda: registry))

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = _prompt_session()
        session = Session()
        context = create_repl_runtime(
            session=session,
            pt_session=prompt,
            active_theme_name="pink",
        )

    assert context.session is session
    assert session.terminal.active_theme_name == "pink"
    assert session.configured_integrations == ("github",)
    assert session.configured_integrations_known is True
    assert hydrate_calls == [session.session_id]
    assert session.task_registry is registry
    assert session.terminal.prompt_history_backend is prompt.history


def test_context_supports_lightweight_bootstrap_for_unit_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Session,
        "hydrate_configured_integrations",
        lambda _self: (_ for _ in ()).throw(AssertionError("hydrated")),
    )
    monkeypatch.setattr(
        TaskRegistry,
        "persistent",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("persistent"))),
    )

    context = create_repl_runtime(
        active_theme_name="green",
        hydrate_integrations=False,
        persistent_tasks=False,
    )

    assert context.session.terminal.active_theme_name == "green"
    assert isinstance(context.state, ReplState)
    assert isinstance(context.spinner, SpinnerState)


def test_create_context_registers_jsonl_session_trace_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPL boot wires the JSONL session-trace sink for ATM / span metrics."""
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        get_session_trace_store,
        is_session_trace_active,
        set_session_trace_store,
    )
    from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore

    monkeypatch.setattr(
        Session,
        "hydrate_configured_integrations",
        lambda _self: None,
    )
    monkeypatch.setattr(
        TaskRegistry,
        "persistent",
        staticmethod(TaskRegistry),
    )
    set_session_trace_store(NoopSessionTraceStore())
    try:
        create_repl_runtime(
            hydrate_integrations=False,
            persistent_tasks=False,
        )
        assert is_session_trace_active()
        assert isinstance(get_session_trace_store(), JsonlSessionTraceStore)
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_create_context_uses_noop_trace_store_for_in_memory_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_harness.session import InMemorySessionStore
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        get_session_trace_store,
        is_session_trace_active,
        set_session_trace_store,
    )

    monkeypatch.setattr(
        Session,
        "hydrate_configured_integrations",
        lambda _self: None,
    )
    set_session_trace_store(NoopSessionTraceStore())
    try:
        create_repl_runtime(
            session=Session(store=InMemorySessionStore()),
            hydrate_integrations=False,
            persistent_tasks=False,
        )
        assert not is_session_trace_active()
        assert isinstance(get_session_trace_store(), NoopSessionTraceStore)
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_context_uses_canonical_initial_mutable_state() -> None:
    context = ReplRuntime(session=Session())

    assert isinstance(context.state, ReplState)
    assert isinstance(context.spinner, SpinnerState)
    assert context.state.exit_requested is False
    assert context.spinner.streaming is False


def test_context_preserves_explicit_partial_mutable_state() -> None:
    state = ReplState()
    context = ReplRuntime(session=Session(), state=state)

    assert context.state is state
    assert isinstance(context.spinner, SpinnerState)


def test_context_rejects_invalid_state_contracts() -> None:
    with pytest.raises(ValidationError):
        ReplRuntime(session=object())  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        SessionBootstrapSpec(active_theme_name=" ")


def test_context_assignment_validates_inbox_type() -> None:
    context = ReplRuntime(session=Session())

    with pytest.raises(ValidationError):
        context.inbox = object()  # type: ignore[assignment]


def test_controller_reuses_validated_runtime_context() -> None:
    session = Session()
    state = ReplState()
    spinner = SpinnerState()
    context = ReplRuntime(session=session, state=state, spinner=spinner)

    controller = InteractiveShellController(context)

    assert controller.runtime_context is context
    assert controller.session is session
    assert controller.state is state
    assert controller.spinner is spinner
    assert controller.input_reader.state is state
