"""Characterization: AgentSession is the public host API (``start`` + ``chat``)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _stub_turn_result() -> TurnResult:
    return TurnResult(
        final_intent="answered",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text="ok",
    )


def test_startup_runs_the_boot_step_the_host_supplied() -> None:
    """An embedded host must not resolve zero integrations.

    ``core`` may not import ``bootstrap``, so the host passes the boot step in
    rather than the harness reaching up a tier for it.
    """
    # Arrange — what an embedded caller supplies.
    from bootstrap.process import EMBEDDED_PROFILE, configure_process

    seen: list[Any] = []

    def _boot() -> None:
        seen.append(EMBEDDED_PROFILE.name)
        configure_process(EMBEDDED_PROFILE)

    # Act.
    AgentSession(
        SessionConfig(open_store=False, persistent_tasks=False, boot_process=_boot)
    ).startup()

    # Assert.
    assert seen == [EMBEDDED_PROFILE.name]


def test_startup_boots_nothing_when_the_host_supplies_no_step() -> None:
    """CLI, gateway and web boot their own profile; the harness must not add one."""
    # Arrange.
    seen: list[Any] = []

    def _boot() -> None:
        seen.append("booted")

    # Act — the default leaves boot_process unset.
    AgentSession(SessionConfig(open_store=False, persistent_tasks=False)).startup()

    # Assert.
    assert seen == []
    assert _boot  # the step exists but was never wired in


def test_start_registers_harness_adapters_so_integrations_resolve() -> None:
    """Without adapters, resolve is empty; the host's boot step must install them.

    ``core`` may not import ``bootstrap``, so the caller passes an explicit
    ``boot_process`` (or uses :func:`bootstrap.embedded.start_embedded_session`).
    """
    from bootstrap.process import (
        EMBEDDED_PROFILE,
        configure_process,
        reset_process_runtime_for_tests,
    )
    from core.domain.types.tools import ToolSurface
    from infrastructure.harness_providers import (
        configured_integration_services,
        reset_harness_providers,
        resolve_integrations,
        resolve_surface_tools,
    )

    reset_harness_providers()
    reset_process_runtime_for_tests()
    assert configured_integration_services() == ()
    assert resolve_integrations() == {}
    assert resolve_surface_tools(ToolSurface.CHAT) == []

    AgentSession.start(
        SessionConfig(
            open_store=False,
            persistent_tasks=False,
            warm_integrations=True,
            boot_process=lambda: configure_process(EMBEDDED_PROFILE),
        )
    )

    # Tool registry is populated even when the local store is empty (CI).
    chat_tools = resolve_surface_tools(ToolSurface.CHAT)
    assert any(tool.name.startswith("query_grafana") for tool in chat_tools)


def test_start_does_not_inject_an_embedded_boot_step() -> None:
    """CLI/gateway leave ``boot_process`` unset; ``start`` must not fill one in.

    ``core`` may not import ``bootstrap``. Embedded hosts use
    ``start_embedded_session`` (or pass an explicit boot step).
    """
    session = AgentSession.start(SessionConfig(open_store=False, persistent_tasks=False))
    assert session._config.boot_process is None


def test_chat_is_the_public_verb(monkeypatch: Any) -> None:
    session = AgentSession.start()
    captured: list[str] = []

    def _fake_dispatch(message: str) -> TurnResult:
        captured.append(message)
        return _stub_turn_result()

    assert session.agent is not None
    monkeypatch.setattr(session.agent, "dispatch", _fake_dispatch)

    result = session.chat("why is checkout-api slow?")
    assert captured == ["why is checkout-api slow?"]
    assert result.answered is True
    # Compatibility alias
    session.chat("follow-up")
    assert captured == ["why is checkout-api slow?", "follow-up"]


def test_one_shot_turn_reuses_the_start_bootstrap_instead_of_repeating_it() -> None:
    """``start`` is the single headless bootstrap; the one-shot delegates to it.

    ``start`` and ``run_headless_turn`` each used to run the same sequence —
    create session, resolve sink, resolve prompts, build the default agent,
    attach it — so a change to headless startup had to be made twice. Pinning
    the delegation keeps one bootstrap and makes the multi-turn shape
    (``start()`` once, ``chat()`` per turn) the same code path as the one-shot.
    """
    # Arrange: record every start() call and hand back a stub session.
    calls: list[dict[str, Any]] = []
    turns: list[str] = []

    class _StubSession:
        def chat(self, message: str, **_kwargs: Any) -> str:
            turns.append(message)
            return "answered"

    def _start(config=None, **kwargs: Any) -> Any:
        calls.append({"config": config, **kwargs})
        return _StubSession()

    bootstraps: list[str] = []

    def _startup(_self: Any) -> Any:
        bootstraps.append("startup")
        raise AssertionError("bootstrap ran outside start()")

    def _start_classmethod(_cls: Any, *args: Any, **kwargs: Any) -> Any:
        return _start(*args, **kwargs)

    with (
        patch.object(AgentSession, "start", classmethod(_start_classmethod)),
        patch.object(AgentSession, "startup", _startup),
    ):
        # Act.
        result = AgentSession.run_headless_turn("summarize open incidents")

    # Assert: exactly one bootstrap, and it happened inside start().
    assert len(calls) == 1, "run_headless_turn re-implemented the bootstrap"
    assert bootstraps == [], "run_headless_turn ran its own startup alongside start()"
    assert turns == ["summarize open incidents"]
    assert result == "answered"


def test_the_harness_exposes_one_name_per_concept() -> None:
    """No compatibility aliases: a second name for a live concept is drift waiting to happen.

    ``AgentHarness``/``HarnessConfig``/``HarnessStartupResult``/``dispatch_message``
    were removed once every caller moved. AGENTS.md forbids keeping
    compatibility-only paths after a refactor, so this fails if one returns.
    """
    # Arrange / Act.
    from core import agent_harness as package
    from core.agent_harness import harness as harness_module

    retired = ("AgentHarness", "HarnessConfig", "HarnessStartupResult")

    # Assert.
    for name in retired:
        assert not hasattr(harness_module, name), f"{name} alias is back in harness.py"
        assert not hasattr(package, name), f"{name} alias is back in the package API"
    assert not hasattr(AgentSession, "dispatch_message"), "dispatch_message alias is back"
