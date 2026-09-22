"""Menu state and cancellation must not mutate settings or remove resources."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import integrations, privacy_cmds, settings_cmds
from surfaces.interactive_shell.prompt_history.policy import RedactingFileHistory
from surfaces.interactive_shell.session import Session


def test_trust_marks_active_value_and_cancel_keeps_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.terminal.trust_mode = False

    def cancel(**kwargs: Any) -> None:
        assert kwargs["panel"]
        assert kwargs["initial_value"] == kwargs["current_value"] == "off"
        assert "skip approval prompts" in dict(kwargs["choices"])["on"]

    monkeypatch.setattr(settings_cmds, "repl_choose_one", cancel)
    assert settings_cmds._interactive_trust_menu(session, Console(file=StringIO()))
    assert not session.terminal.trust_mode


def test_verbose_uses_effective_state_and_applies_only_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACER_VERBOSE", "yes")

    def choose(**kwargs: Any) -> str:
        assert kwargs["initial_value"] == kwargs["current_value"] == "on"
        return "off"

    monkeypatch.setattr(settings_cmds, "repl_choose_one", choose)
    assert settings_cmds._interactive_verbose_menu(Session(), Console(file=StringIO()))
    assert not settings_cmds.verbose_output_enabled()


@pytest.mark.parametrize("confirmation", [None, "cancel", "clear"])
def test_history_clear_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch, confirmation: str | None
) -> None:
    picks = iter(["clear", confirmation, None])
    calls: list[dict[str, Any]] = []
    cleared: list[bool] = []

    def choose(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return next(picks)

    monkeypatch.setattr(privacy_cmds, "repl_choose_one", choose)
    monkeypatch.setattr(
        privacy_cmds, "clear_persisted_history", lambda: cleared.append(True) or True
    )
    privacy_cmds._interactive_history_menu(Session(), Console(file=StringIO()))
    assert cleared == ([True] if confirmation == "clear" else [])
    assert calls[1]["initial_value"] == "cancel"
    assert calls[2]["initial_value"] == "clear"


def test_history_retention_marks_custom_current_cap_without_pruning_on_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = Session()
    backend = RedactingFileHistory(str(tmp_path / "history"), max_entries=321)
    session.terminal.prompt_history_backend = backend
    picks = iter(["retention", None, None])
    calls: list[dict[str, Any]] = []

    def choose(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return next(picks)

    monkeypatch.setattr(privacy_cmds, "repl_choose_one", choose)
    privacy_cmds._interactive_history_menu(session, Console(file=StringIO()))
    assert calls[1]["current_value"] == calls[1]["initial_value"] == "321"
    assert ("321", "321 commands") in calls[1]["choices"]
    assert backend.max_entries == 321


def test_mcp_remove_cancel_returns_to_filtered_picker_with_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations import store

    mcp_service = sorted(integrations.MCP_INTEGRATION_SERVICES)[0]
    monkeypatch.setattr(
        integrations.repl_data, "configured_integration_names", lambda: [mcp_service, "other"]
    )
    monkeypatch.setattr(integrations, "repl_tty_interactive", lambda: True)
    picks = iter([mcp_service, None, None])
    calls: list[dict[str, Any]] = []

    def choose(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return next(picks)

    def unexpected_remove(_service: str) -> bool:
        pytest.fail("Cancelling confirmation must not delete a connection")

    monkeypatch.setattr(integrations, "repl_choose_one", choose)
    monkeypatch.setattr(store, "remove_integration", unexpected_remove)
    integrations._handle_remove(Session(), Console(file=StringIO()), None, mcp=True)
    assert calls[0]["choices"] == [(mcp_service, mcp_service)]
    assert calls[1]["initial_value"] == "no"
    assert calls[2]["initial_value"] == mcp_service
    assert all(call["breadcrumb"].startswith("/mcp") for call in calls)


def test_integration_detail_view_preserves_resource_focus_and_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    record = {"service": "github", "status": "ok", "detail": "Connected"}
    monkeypatch.setattr(integrations.repl_data, "configured_integration_names", lambda: ["github"])
    monkeypatch.setattr(integrations.repl_data, "verify_integration", lambda _: record)
    picks = iter(["github", None])
    calls: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    def choose(**kwargs: Any) -> str | None:
        calls.append(kwargs)
        return next(picks)

    monkeypatch.setattr(integrations, "repl_choose_one", choose)
    monkeypatch.setattr(integrations, "repl_show_details", lambda **kwargs: details.append(kwargs))
    integrations._browse_integration_details(session, Console(file=StringIO()))
    assert calls[1]["initial_value"] == "github"
    assert dict(details[0]["fields"])["Status"] == "ok"
    assert "Connected" in session.agent.last_observation


def test_mcp_view_filters_display_without_dropping_general_integration_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    mcp_service = sorted(integrations.MCP_INTEGRATION_SERVICES)[0]
    results = [
        {"service": mcp_service, "status": "ok", "detail": "MCP connected"},
        {"service": "github", "status": "ok", "detail": "GitHub connected"},
    ]
    monkeypatch.setattr(integrations.repl_data, "load_verified_integrations", lambda: results)
    details: list[dict[str, Any]] = []
    monkeypatch.setattr(integrations, "repl_show_details", lambda **kwargs: details.append(kwargs))
    integrations._show_connections(session, Console(file=StringIO()), mcp=True)
    assert list(dict(details[0]["fields"])) == [mcp_service]
    assert "GitHub connected" in session.agent.last_observation
    assert "MCP connected" in session.agent.last_observation
