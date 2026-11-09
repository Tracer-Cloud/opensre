"""List-first integration browsing preserves safe command dispatch."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from rich.console import Console
from rich.text import Text

from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.command_registry import integrations as commands
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.connection_checks import ConnectionCheck
from surfaces.interactive_shell.ui.integration_browser import IntegrationEntry


@pytest.mark.parametrize(
    "command, expected",
    [
        ("/integrations list", ["github", "grafana"]),
        ("/mcp list", ["github"]),
    ],
)
def test_list_subcommand_opens_browser_before_any_blocking_verification(
    monkeypatch: pytest.MonkeyPatch, command: str, expected: list[str]
) -> None:
    seen: list[list[str]] = []

    def browse(
        entries: list[IntegrationEntry], *, mcp: bool, verify: object, checks: object
    ) -> None:
        assert verify is commands.verify_connection
        assert mcp == command.startswith("/mcp ")
        seen.append([entry.service for entry in entries])

    def no_probe(*_args: object) -> None:
        pytest.fail("Dispatch must not block on verification or open an action submenu")

    monkeypatch.setattr(commands, "browse_integrations", browse, raising=False)
    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        commands.repl_data, "configured_integration_names", lambda: ["github", "grafana"]
    )
    monkeypatch.setattr(commands.repl_data, "load_verified_integrations", no_probe)
    monkeypatch.setattr(commands, "repl_choose_one", no_probe)
    console = Console(file=io.StringIO(), force_terminal=True)
    session = Session()

    assert dispatch_slash(command, session, console, is_tty=True)
    assert seen == [expected]
    assert session.history[-1]["response_text"] == f"slash {command} (succeeded)"


@pytest.mark.parametrize(
    "command, usage", [("/integrations", "/integrations list"), ("/mcp", "/mcp list")]
)
def test_bare_command_shows_usage_instead_of_opening_browser(
    monkeypatch: pytest.MonkeyPatch, command: str, usage: str
) -> None:
    def no_browser(*_args: object, **_kwargs: object) -> None:
        pytest.fail("The connection browser belongs to the explicit list subcommand")

    monkeypatch.setattr(commands, "browse_integrations", no_browser)
    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    output = io.StringIO()
    session = Session()

    assert dispatch_slash(command, session, Console(file=output, force_terminal=True), is_tty=True)
    assert usage in Text.from_ansi(output.getvalue()).plain
    assert session.history[-1]["ok"] is False


@pytest.mark.parametrize("command", ["/integrations ls", "/mcp ls"])
def test_ls_alias_is_not_supported(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    def no_browser(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Only the explicit list subcommand opens the connection browser")

    monkeypatch.setattr(commands, "browse_integrations", no_browser)
    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    output = io.StringIO()
    session = Session()

    assert dispatch_slash(command, session, Console(file=output, force_terminal=True), is_tty=True)
    assert "unknown subcommand" in Text.from_ansi(output.getvalue()).plain
    assert session.history[-1]["ok"] is False


@pytest.mark.parametrize("command", ["/integrations list", "/mcp list"])
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
    session = Session()
    assert dispatch_slash(command, session, Console(file=output, force_terminal=True), is_tty=False)
    assert "github" in output.getvalue()
    assert "github" in session.history[-1]["response_text"]


@pytest.mark.parametrize("command", ["/integrations list", "/mcp list"])
def test_redirected_output_keeps_table_in_turn_result(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def no_browser(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Redirected output must not open the connection browser")

    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(commands, "browse_integrations", no_browser)
    monkeypatch.setattr(
        commands.repl_data,
        "load_verified_integrations",
        lambda: [{"service": "github", "status": "ok"}],
    )
    session = Session()

    assert dispatch_slash(command, session, Console(file=io.StringIO()), is_tty=None)
    assert "github" in session.history[-1]["response_text"]


def test_browser_setup_preserves_wizard_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_harness.spi.session_state import set_turn_outcome_hint
    from surfaces.interactive_shell.ui.integration_browser import IntegrationSelection

    expected = "opensre integrations setup github: interactive wizard cancelled"

    def setup(session: Session, _console: Console, _args: list[str]) -> bool:
        set_turn_outcome_hint(session, expected)
        return True

    monkeypatch.setattr(commands, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(commands.repl_data, "configured_integration_names", lambda: ["github"])
    monkeypatch.setattr(
        commands,
        "browse_integrations",
        lambda *_args, **_kwargs: IntegrationSelection("setup", "github"),
    )
    monkeypatch.setattr(commands, "_run_integrations_setup", setup)
    session = Session()

    assert dispatch_slash(
        "/integrations list",
        session,
        Console(file=io.StringIO(), force_terminal=True),
        is_tty=True,
    )
    assert session.history[-1]["response_text"] == expected


@pytest.mark.parametrize("command", ["/integrations list", "/mcp list"])
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
    def browse(
        entries: list[IntegrationEntry], *, mcp: bool, verify: object, checks: object
    ) -> None:
        assert verify is commands.verify_connection
        assert [(entry.service, entry.can_verify) for entry in entries] == [
            ("airflow", False),
            ("github", True),
        ]

    monkeypatch.setattr(
        commands.repl_data, "configured_integration_names", lambda: ["airflow", "github"]
    )
    monkeypatch.setattr(commands, "browse_integrations", browse)
    commands._browse_connections(Session(), Console(file=io.StringIO()), mcp=False)


@pytest.mark.parametrize("organization,actor", [("team-b", "alice"), ("team-a", "bob")])
def test_connection_lists_reuse_only_the_same_scope_and_source_snapshot(
    monkeypatch: pytest.MonkeyPatch, organization: str, actor: str
) -> None:
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope

    seen: list[dict[str, ConnectionCheck]] = []
    stamp = (1, 2, 3)

    def browse(
        _entries: list[IntegrationEntry],
        *,
        checks: dict[str, ConnectionCheck] | None = None,
        **_kwargs: object,
    ) -> None:
        assert checks is not None, "The list must receive session-local check history"
        seen.append(checks)

    monkeypatch.setattr(commands, "browse_integrations", browse)
    monkeypatch.setattr(commands.repl_data, "configured_integration_names", lambda: ["github"])
    monkeypatch.setattr("infrastructure.harness_providers.integration_sources_stamp", lambda: stamp)
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=True)
    scope = StorageScope(principal=Principal.org("team-a"), actor=Actor(id="alice"))
    with bound_storage_scope(scope):
        assert commands._browse_connections(session, console, mcp=False)
        checked = ConnectionCheck(
            "fixture", "passed", "Reachable", datetime(2026, 1, 1, tzinfo=UTC)
        )
        seen[-1]["github"] = checked
        assert commands._browse_connections(session, console, mcp=True)
        assert seen[-1]["github"] == checked
        stamp = (2, 2, 3)
        assert commands._browse_connections(session, console, mcp=False)
        assert seen[-1] == {} and seen[-1] is not seen[-2]
        seen[-1]["github"] = checked
    other = StorageScope(principal=Principal.org(organization), actor=Actor(id=actor))
    with bound_storage_scope(other):
        assert commands._browse_connections(session, console, mcp=False)
        assert seen[-1] == {} and seen[-1] is not seen[-2]


def test_connection_check_history_is_session_local_and_cleared_with_new() -> None:
    first, second = Session(), Session()
    first.terminal.connection_checks["grafana"] = ConnectionCheck(
        "fixture", "failed", "Offline", datetime(2026, 1, 1, tzinfo=UTC)
    )
    first.terminal.connection_check_key = (None, (1, 2, 3))
    assert second.terminal.connection_checks == {}
    first.clear()
    assert first.terminal.connection_checks == {}
    assert first.terminal.connection_check_key is None
