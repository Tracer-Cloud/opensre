"""The two-line entry point: start a harness, dispatch a message.

``main.py`` is the file newcomers read first, so the example in it has to be the
API we actually want people to use — not a transcription of the wiring. Before
this, a headless caller assembled config, startup, a console, a logger, an output
sink and a factory call, then attached the result. All of that is defaultable.
"""

from __future__ import annotations

from typing import Any


def test_start_returns_a_ready_harness() -> None:
    """One call: env resolved, session created, default agent attached."""
    # Arrange / Act
    from core.agent_harness.harness import AgentSession

    harness = AgentSession.start()

    # Assert: dispatch works without any further wiring.
    assert harness.agent is not None


def test_start_accepts_a_config_for_callers_that_need_one() -> None:
    """Surfaces that resume a session must still be able to pass config."""
    # Arrange
    from core.agent_harness.harness import AgentSession, SessionConfig

    # Act
    harness = AgentSession.start(SessionConfig())

    # Assert
    assert harness.agent is not None


def test_started_harness_dispatches_without_extra_wiring(monkeypatch: Any) -> None:
    """The documented two-liner must actually run end to end."""
    # Arrange
    from core.agent_harness.harness import AgentSession

    harness = AgentSession.start()
    captured: list[str] = []

    def _fake_dispatch(message: str) -> Any:
        captured.append(message)
        return "ok"

    monkeypatch.setattr(harness.agent, "dispatch", _fake_dispatch)

    # Act
    harness.chat("why is checkout-api slow?")

    # Assert
    assert captured == ["why is checkout-api slow?"]


def _headless_config(**overrides: Any) -> Any:
    """A config that touches no env, no storage, no integrations."""
    from core.agent_harness.harness import SessionConfig

    return SessionConfig(
        load_env=False,
        hydrate_integrations=False,
        persistent_tasks=False,
        open_store=False,
        **overrides,
    )


def test_configured_prompts_reach_the_agent() -> None:
    """A configured provider has to reach the agent.

    ``startup()`` loaded the provider and returned it, but ``start()`` built the
    agent without passing it — so a caller's grounding context was discarded
    with no error, and the built-in one answered instead. Reaching into
    ``_prompts`` pins the wiring directly; there is no public accessor yet.
    """
    # Arrange
    from core.agent_harness.harness import AgentSession

    class _CallerPrompts:
        """Stands in for a caller's own grounding-context provider."""

    supplied = _CallerPrompts()

    # Act
    harness = AgentSession.start(_headless_config(prompts=supplied))

    # Assert
    assert harness.agent is not None
    assert harness.agent._prompts is supplied


def test_omitting_prompts_keeps_the_built_in_context() -> None:
    """No provider configured must still ground the agent, not leave it bare."""
    # Arrange
    from core.agent_harness.harness import AgentSession

    # Act
    harness = AgentSession.start(_headless_config())

    # Assert
    assert harness.agent is not None
    assert type(harness.agent._prompts).__name__ == "DefaultPromptContextProvider"


def test_default_prompt_provider_is_exported_from_spi_defaults() -> None:
    from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
    from core.agent_harness.spi import defaults

    assert defaults.DefaultPromptContextProvider is DefaultPromptContextProvider


def test_builder_exposes_the_prompts_port() -> None:
    """The port has to be reachable on the documented second path too.

    The README shows callers building the agent themselves via
    ``DefaultHeadlessBuild(...).agent(...)``; a port only ``start()`` can reach is not
    exposed.
    """
    # Arrange
    import inspect

    from core.agent_harness.turns.headless_build import DefaultHeadlessBuild

    # Act / Assert
    assert "prompts" in inspect.signature(DefaultHeadlessBuild.agent).parameters


def test_a_falsy_prompts_provider_is_still_used() -> None:
    """Selection must be ``is not None``, not truthiness.

    A provider defining ``__bool__`` would be silently swapped for the built-in
    one — the same quiet substitution this seam already suffered once.
    """
    # Arrange
    from core.agent_harness.harness import AgentSession

    class _FalsyPrompts:
        def __bool__(self) -> bool:
            return False

    supplied = _FalsyPrompts()

    # Act
    harness = AgentSession.start(_headless_config(prompts=supplied))

    # Assert
    assert harness.agent is not None
    assert harness.agent._prompts is supplied
