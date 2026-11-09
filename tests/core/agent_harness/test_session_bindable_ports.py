"""Session/console/output bind ports are Protocol-visible for agent reuse."""

from __future__ import annotations

import threading
from typing import Any

from core.agent_harness.ports import (
    CancelCapableConsole,
    ConsoleBindable,
    SessionBindable,
)
from core.agent_harness.runtime import TurnBinding
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    EmptyPromptContextProvider,
    InMemorySessionState,
    NullToolProvider,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.host_cancel import ensure_turn_cancel


class _SpyTools:
    """Minimal SessionBindable + ConsoleBindable + ToolProvider for bind checks."""

    def __init__(self) -> None:
        self.sessions: list[Any] = []
        self.consoles: list[Any] = []

    def bind_session(self, session: Any) -> None:
        self.sessions.append(session)

    def bind_console(self, console: CancelCapableConsole) -> None:
        self.consoles.append(console)

    def action_tools(self, **_kwargs: Any) -> list[Any]:
        return []

    def tool_resources(self) -> dict[str, Any]:
        return {}

    def observer(self, *, message: str) -> Any:
        _ = message

        def _observer(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return _observer


def test_default_and_null_tool_providers_are_session_and_console_bindable() -> None:
    tools = DefaultToolProvider(InMemorySessionState(), console=object())
    assert isinstance(tools, SessionBindable)
    assert isinstance(tools, ConsoleBindable)
    null = NullToolProvider()
    assert isinstance(null, SessionBindable)
    assert isinstance(null, ConsoleBindable)


def test_headless_default_ports_are_session_bindable() -> None:
    assert isinstance(EmptyPromptContextProvider(), SessionBindable)


def test_headless_agent_bind_session_invokes_session_bindable() -> None:
    first = InMemorySessionState(session_id="a")
    second = InMemorySessionState(session_id="b")
    tools = _SpyTools()
    agent = InMemoryHeadlessBuild(session=first).agent(tools=tools)
    agent.bind_session(second)
    assert tools.sessions == [second]


def test_headless_agent_bind_turn_console_invokes_console_bindable() -> None:
    tools = _SpyTools()
    agent = InMemoryHeadlessBuild(session=InMemorySessionState()).agent(tools=tools)

    class _StubConsole:
        cancel_requested = False

        def print(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

    stub = _StubConsole()
    agent.bind_turn(TurnBinding(console=stub))
    assert tools.consoles == [stub]


def test_ensure_turn_cancel_reuses_existing_event() -> None:
    class _Sink:
        turn_cancel = threading.Event()

    sink: Any = _Sink()
    first = ensure_turn_cancel(sink)
    second = ensure_turn_cancel(sink)
    assert first is second is sink.turn_cancel


def test_bindable_output_exposes_turn_cancel_property() -> None:
    from infrastructure.turn_host.bindable_output import BindableOutput

    class _Inner:
        def __init__(self) -> None:
            self.turn_cancel = threading.Event()

        def print(self, message: str = "") -> None:
            _ = message

        def render_response_header(self, label: str) -> None:
            _ = label

        def render_error(self, message: str) -> None:
            _ = message

        def stream(self, *, label: str, chunks: Any, **_kwargs: Any) -> str:
            _ = (label, chunks)
            return ""

        def finalize(self, answer: str) -> None:
            _ = answer

    inner = _Inner()
    bindable = BindableOutput()
    assert bindable.turn_cancel is None
    bindable.bind(inner)  # type: ignore[arg-type]
    assert bindable.turn_cancel is inner.turn_cancel
