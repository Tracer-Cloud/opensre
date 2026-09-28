"""A CLI process is one analytics session from ``cli_invoked`` onward; hosted turns keep their own.

Production showed ``cli_invoked`` and the startup skill in one session and the
shell's (or ``opensre ask``'s) turns in another, seconds apart in one process:
the shell and ask minted a fresh session id instead of adopting the process id.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
from config.constants.skills import ONBOARDING_SKILL_NAME
from config.prompt_log import PromptLogConfig
from core.agent.run_io import AgentRunResult
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns import action_driver
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from infrastructure.analytics import provider, usage_context
from infrastructure.analytics.capture import capture_cli_invoked, capture_skill_executed
from infrastructure.analytics.events import Event
from infrastructure.analytics.usage_context import (
    UsageSurface,
    bound_usage_context,
    merge_usage_enrichment,
)
from infrastructure.turn_host.turn_runner import TurnRunner
from surfaces.cli.ask import service
from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash
from surfaces.interactive_shell.runtime.context import ReplRuntime, create_repl_runtime
from surfaces.interactive_shell.runtime.turn_host import AgentTurnResources, run_agent_turn
from surfaces.interactive_shell.session import Session


class _Analytics:
    """Stamps usage context on the capturing thread, as the real provider does."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def set_persistent_property(self, _key: str, _value: object) -> None:
        """Persistent properties never carry the session id."""

    def capture(self, event: Event, properties: dict[str, Any] | None = None) -> None:
        self.events.append((str(event), merge_usage_enrichment(dict(properties or {}))))

    def shutdown(self, **_kwargs: object) -> None:
        """Nothing is queued."""

    def session_ids(self, event: Event) -> list[object]:
        return [props.get("session_id") for name, props in self.events if name == event]


class _LLM:
    _model = "identity-test-model"
    _provider_label = "OpenAI"


class _AnsweringAgent:
    _react_iterations_used = 1
    _react_hit_iteration_cap = False

    def __init__(self) -> None:
        self._react_executed: list[Any] = []

    def run(self, _messages: Any) -> AgentRunResult:
        return AgentRunResult(
            messages=[], final_text="Answered", executed=[], llm_iterations_used=1
        )


class _GatewayOutput(BufferOutputSink):
    def set_tool_status(self, status: str) -> None:
        self.lines.append(status)

    def finalize(self, answer: str) -> None:
        self.lines.append(answer)


def _answering_plan(**kwargs: Any) -> action_driver.ActionTurnPlan:
    return action_driver.ActionTurnPlan(
        agent=_AnsweringAgent(),  # type: ignore[arg-type]
        user_message=kwargs["message"],
        llm=_LLM(),
        max_iterations=8,
    )


@pytest.fixture
def analytics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Analytics:
    recorder = _Analytics()
    monkeypatch.setattr(provider, "_instance", recorder)
    monkeypatch.setattr(usage_context, "_PROCESS_SESSION_ID", None)
    monkeypatch.setattr(usage_context, "_CLAIMED_PROCESS_SESSION_ID", None)
    config = PromptLogConfig(log_path=tmp_path / "prompts.jsonl")
    monkeypatch.setattr(PromptLogConfig, "load", lambda: config)
    monkeypatch.setattr(action_driver, "_build_action_agent", _answering_plan)
    monkeypatch.setattr(
        "infrastructure.turn_host.session_lock.sessions_dir", lambda: tmp_path / "sessions"
    )
    return recorder


def test_shell_turns_join_the_cli_invoked_session_until_new(
    analytics: _Analytics, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    session_ids: list[str] = []

    def _runtime(*, session: Session | None = None, **_kwargs: object) -> ReplRuntime:
        session = session or Session()
        session.store = InMemorySessionStore()
        return create_repl_runtime(
            session=session, hydrate_integrations=False, persistent_tasks=False
        )

    def _offer_demo(*_args: object, **_kwargs: object) -> bool:
        capture_skill_executed(skill_name=ONBOARDING_SKILL_NAME, entrypoint="host")
        return True

    class _Controller:
        def __init__(self, runtime: ReplRuntime, **_kwargs: object) -> None:
            self._resources = AgentTurnResources(
                session=runtime.session,
                state=runtime.state,
                spinner=runtime.spinner,
                invalidate_prompt=lambda: None,
                console=console,
            )

        async def start_interactive_shell(self) -> None:
            session = self._resources.session
            await run_agent_turn(self._resources, "Why is checkout slow?")
            session_ids.append(session.session_id)
            dispatch_slash("/new", session, console)
            await run_agent_turn(self._resources, "What changed since then?")
            session_ids.append(session.session_id)

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(main_entrypoint, "create_repl_runtime", _runtime)
    monkeypatch.setattr(main_entrypoint, "offer_demo", _offer_demo)
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)

    capture_cli_invoked({"entrypoint": "opensre"})
    assert asyncio.run(main_entrypoint.run_repl_async(console=console)) == 0

    [invoked] = analytics.session_ids("cli_command_opensre")
    first, after_new = session_ids
    assert first == invoked
    assert after_new != invoked
    assert analytics.session_ids(Event.SKILL_EXECUTED) == [invoked]
    assert analytics.session_ids(Event.REACT_TURN_COMPLETED) == [invoked, after_new]
    assert analytics.session_ids(Event.AI_GENERATION) == [invoked, after_new]


def test_ask_turn_joins_the_cli_invoked_session(analytics: _Analytics) -> None:
    capture_cli_invoked({"entrypoint": "opensre"})

    outcome = service.run_ask("Explain the outage", allowed_tools=(), bypass_approvals=False)

    [invoked] = analytics.session_ids("cli_command_opensre")
    assert outcome.status is service.AskStatus.SUCCESS
    assert outcome.session_id == invoked
    assert analytics.session_ids(Event.REACT_TURN_COMPLETED) == [invoked]
    assert analytics.session_ids(Event.AI_GENERATION) == [invoked]


def test_gateway_turn_keeps_its_bound_session_in_a_cli_started_process(
    analytics: _Analytics,
) -> None:
    # ``opensre gateway start`` enters through the CLI, so the process id exists.
    capture_cli_invoked({"entrypoint": "opensre"})
    [process_session_id] = analytics.session_ids("cli_command_opensre")
    session = SessionCore(store=InMemorySessionStore())
    session.resolved_integrations_cache = {}
    handler = TurnRunner(console=Console(file=io.StringIO(), force_terminal=False))

    with bound_usage_context(surface=UsageSurface.SLACK, user_id="U1"):
        handler("hi", session, _GatewayOutput(), logging.getLogger("test.identity"))

    assert session.session_id != process_session_id
    runs = [props for name, props in analytics.events if name == Event.REACT_TURN_COMPLETED]
    assert [run["session_id"] for run in runs] == [session.session_id]
    assert [run["cli_session_id"] for run in runs] == [session.session_id]
    assert analytics.session_ids(Event.AI_GENERATION) == [session.session_id]
