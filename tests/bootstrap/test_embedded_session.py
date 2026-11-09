"""Embedded hosts get local adapters without ``core`` importing ``bootstrap``."""

from __future__ import annotations

from typing import Any

from bootstrap.embedded import embedded_boot_step, start_embedded_session
from core.agent_harness.harness import SessionConfig


def test_start_embedded_session_supplies_the_boot_step(monkeypatch: Any) -> None:
    """Without a boot step an embedded caller resolves zero integrations."""
    # Arrange.
    seen: list[str] = []
    captured: dict[str, Any] = {}

    def _fake_start(config: SessionConfig | None = None, **_kw: Any) -> object:
        captured["config"] = config
        return object()

    monkeypatch.setattr("bootstrap.embedded.AgentSession.start", staticmethod(_fake_start))
    monkeypatch.setattr(
        "bootstrap.embedded.configure_process", lambda profile: seen.append(profile.name)
    )

    # Act.
    start_embedded_session()
    captured["config"].boot_process()

    # Assert — the default step is wired in, and it boots the embedded profile.
    assert captured["config"].boot_process is embedded_boot_step
    assert seen and "embedded" in str(seen[0]).lower()


def test_a_caller_supplied_boot_step_is_kept(monkeypatch: Any) -> None:
    """A host that boots its own profile must not be overridden."""
    # Arrange.
    captured: dict[str, Any] = {}

    def _fake_start(config: SessionConfig | None = None, **_kw: Any) -> object:
        captured["config"] = config
        return object()

    def _mine() -> None:
        return None

    monkeypatch.setattr("bootstrap.embedded.AgentSession.start", staticmethod(_fake_start))

    # Act.
    start_embedded_session(SessionConfig(boot_process=_mine))

    # Assert.
    assert captured["config"].boot_process is _mine


def test_embedded_session_boots_adapters_so_integrations_resolve() -> None:
    """The embedded entry must boot adapters.

    Without them chat claims every vendor is disconnected. ``core`` may not
    import ``bootstrap``, so ``AgentSession.start`` cannot supply the step —
    ``start_embedded_session`` is the documented headless entry that does.
    """
    from bootstrap.process import reset_process_runtime_for_tests
    from core.domain.types.tools import ToolSurface
    from infrastructure.harness_providers import (
        reset_harness_providers,
        resolve_integrations,
        resolve_surface_tools,
    )

    reset_harness_providers()
    reset_process_runtime_for_tests()
    assert resolve_integrations() == {}

    start_embedded_session(
        SessionConfig(open_store=False, persistent_tasks=False, warm_integrations=True)
    )

    chat_tools = resolve_surface_tools(ToolSurface.CHAT)
    assert any(tool.name.startswith("query_grafana") for tool in chat_tools)
