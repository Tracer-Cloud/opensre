"""Configuring an integration mid-session refreshes the session's known state.

Regression: after ``/integrations setup sentry`` saved sentry to the local
store, the assistant still answered "Sentry is not integrated" because the
session's ``configured_integrations`` snapshot was only taken at REPL boot and
never refreshed. The setup/remove (and /mcp connect|disconnect) paths must
re-resolve the env + store integration set so the same session reflects the
change.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from surfaces.interactive_shell.command_registry import integrations as _integrations
from surfaces.interactive_shell.session import Session


def _console() -> Console:
    return Console(force_terminal=False, no_color=True)


def _noop_cli_command(*_args: Any, **_kwargs: Any) -> bool:
    """Stand in for the setup/remove subprocess so no child process is spawned."""
    return True


def test_refresh_integration_state_rehydrates_and_clears_cache(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: ["gitlab", "sentry"],
    )
    refreshed = {"gitlab": {"token": "x"}, "sentry": {"dsn": "y"}}
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: refreshed,
    )
    session = Session()
    # Stale boot-time snapshot + a cached resolution from an earlier turn.
    session.configured_integrations = ("gitlab",)
    session.configured_integrations_known = True
    session.resolved_integrations_cache = {"gitlab": {}}

    session.refresh_integration_state()

    assert session.resolved_integrations_cache == refreshed
    assert session.configured_integrations_known is True
    assert session.configured_integrations == ("gitlab", "sentry")


def test_setup_subcommand_refreshes_configured_integrations(monkeypatch: Any) -> None:
    # The setup subprocess is replaced with a no-op; the store mutation is
    # simulated by flipping what effective resolution returns afterwards.
    monkeypatch.setattr(_integrations, "run_cli_command", _noop_cli_command)

    store: dict[str, dict[str, Any]] = {"gitlab": {}}
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: list(store),
    )
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: dict(store),
    )

    session = Session()
    session.hydrate_configured_integrations()
    assert session.configured_integrations == ("gitlab",)

    # The user runs `/integrations setup sentry`; setup writes sentry to store.
    store["sentry"] = {}
    handled = _integrations._cmd_integrations(session, _console(), ["setup", "sentry"])

    assert handled is True
    assert "sentry" in session.configured_integrations


def test_remove_subcommand_refreshes_configured_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(_integrations, "run_cli_command", _noop_cli_command)

    store: dict[str, dict[str, Any]] = {"gitlab": {}, "sentry": {}}
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: list(store),
    )
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: dict(store),
    )

    session = Session()
    session.hydrate_configured_integrations()
    assert "sentry" in session.configured_integrations

    store.pop("sentry")
    handled = _integrations._cmd_integrations(session, _console(), ["remove", "sentry"])

    assert handled is True
    assert "sentry" not in session.configured_integrations


def test_mcp_connect_refreshes_configured_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(_integrations, "run_cli_command", _noop_cli_command)

    store: dict[str, dict[str, Any]] = {"gitlab": {}}
    monkeypatch.setattr(
        "infrastructure.harness_providers.configured_integration_services",
        lambda: list(store),
    )
    monkeypatch.setattr(
        "core.agent_harness.session.integration_resolution.resolve_integrations",
        lambda: dict(store),
    )

    session = Session()
    session.hydrate_configured_integrations()

    store["github_mcp"] = {}
    handled = _integrations._cmd_mcp(session, _console(), ["connect", "github_mcp"])

    assert handled is True
    assert "github_mcp" in session.configured_integrations
