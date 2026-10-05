"""List-first integration browsing preserves safe command dispatch."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.command_registry import integrations as commands
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.integration_browser import IntegrationEntry


@pytest.mark.parametrize(
    "command, expected", [("/integrations", ["github", "grafana"]), ("/mcp", ["github"])]
)
def test_bare_command_lists_configured_items_without_verifying(
    monkeypatch: pytest.MonkeyPatch, command: str, expected: list[str]
) -> None:
    seen: list[list[str]] = []

    def browse(entries: list[IntegrationEntry], *, mcp: bool) -> None:
        assert mcp == (command == "/mcp")
        seen.append([entry.service for entry in entries])

    def no_probe(*_args: object) -> None:
        pytest.fail("Browsing must not probe services or open an action submenu")

    monkeypatch.setattr(commands, "browse_integrations", browse, raising=False)
    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        commands.repl_data, "configured_integration_names", lambda: ["github", "grafana"]
    )
    monkeypatch.setattr(commands.repl_data, "load_verified_integrations", no_probe)
    monkeypatch.setattr(commands, "repl_choose_one", no_probe)
    console = Console(file=io.StringIO(), force_terminal=True)

    assert dispatch_slash(command, Session(), console, is_tty=True)
    assert seen == [expected]


@pytest.mark.parametrize("command", ["/integrations", "/mcp"])
def test_headless_dispatch_on_inherited_tty_keeps_table(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def no_browser(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Headless dispatch must not read terminal input")

    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(commands, "repl_choose_one", no_browser)
    monkeypatch.setattr(commands, "browse_integrations", no_browser, raising=False)
    monkeypatch.setattr(
        commands.repl_data,
        "load_verified_integrations",
        lambda: [{"service": "github", "status": "ok"}],
    )
    output = io.StringIO()
    assert dispatch_slash(
        command, Session(), Console(file=output, force_terminal=True), is_tty=False
    )
    assert "github" in output.getvalue()


@pytest.mark.parametrize("command", ["/integrations", "/mcp"])
@pytest.mark.parametrize("confirmation, removed", [("no", False), ("cancel", False), ("yes", True)])
def test_browser_removal_keeps_confirmation(
    monkeypatch: pytest.MonkeyPatch, command: str, confirmation: str, removed: bool
) -> None:
    from surfaces.interactive_shell.ui.integration_browser import IntegrationSelection

    deletions: list[str] = []

    def select(*_args: object, **_kwargs: object) -> IntegrationSelection:
        return IntegrationSelection("remove", "github")

    def confirm(*, title: str, breadcrumb: str, choices: list[tuple[str, str]]) -> str:
        assert "github" in title and "github" in breadcrumb
        assert choices[0][0] == "no"
        return confirmation

    def remove(service: str) -> bool:
        deletions.append(service)
        return True

    monkeypatch.setattr(commands, "browse_integrations", select)
    monkeypatch.setattr(commands, "repl_choose_one", confirm)
    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(commands.repl_data, "configured_integration_names", lambda: ["github"])
    monkeypatch.setattr("integrations.store.remove_integration", remove)
    monkeypatch.setattr(
        "integrations.webapp_vault.delete_webapp_org_integration", lambda _service: None
    )
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_integration_removed", lambda _service: None
    )
    monkeypatch.setattr(Session, "refresh_integration_state", lambda _self: None)
    assert dispatch_slash(
        command, Session(), Console(file=io.StringIO(), force_terminal=True), is_tty=True
    )
    assert deletions == (["github"] if removed else [])


def test_browser_marks_integrations_without_a_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def browse(entries: list[IntegrationEntry], *, mcp: bool) -> None:
        assert [(entry.service, entry.can_verify) for entry in entries] == [
            ("airflow", False),
            ("github", True),
        ]

    monkeypatch.setattr(
        commands.repl_data, "configured_integration_names", lambda: ["airflow", "github"]
    )
    monkeypatch.setattr(commands, "browse_integrations", browse)
    commands._browse_connections(Session(), Console(file=io.StringIO()), mcp=False)
