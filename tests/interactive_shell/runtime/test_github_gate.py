"""The shell's GitHub gate: what counts as connected, and that a refusal keeps the shell closed."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from config.repl_config import ReplConfig
from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from surfaces.interactive_shell import main as main_entrypoint
from surfaces.interactive_shell.runtime.startup import github_gate
from surfaces.interactive_shell.ui import connect_github
from surfaces.interactive_shell.ui.connect_github import ConnectGithubChoice
from surfaces.shared.terminal.components import rendering


@pytest.mark.parametrize(("verdict", "connected"), [(True, True), (False, False), (None, False)])
def test_only_a_confirmed_github_connection_opens_the_shell(
    monkeypatch: pytest.MonkeyPatch, verdict: bool | None, connected: bool
) -> None:
    """A check that cannot tell keeps the shell closed: the app would refuse the turn."""
    monkeypatch.setattr(github_gate, "resolve_integrations", lambda: {})
    monkeypatch.setattr(github_gate, "skill_prerequisite_verdict", lambda _check, _r: verdict)

    assert github_gate.github_connected() is connected


def test_without_github_the_shell_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """No composer and no startup menu: the REPL body is never entered."""
    started: list[Any] = []

    async def _shell(**kwargs: Any) -> int:
        started.append(kwargs)
        return 0

    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "start_tool_registry_prewarm", lambda: None)
    monkeypatch.setattr(main_entrypoint, "start_integrations_prefetch", lambda: None)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(main_entrypoint, "pass_github_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _shell)

    exit_code = main_entrypoint.run_repl(config=ReplConfig(enabled=True))

    assert exit_code == 0
    assert started == []


@pytest.mark.parametrize(
    ("resolved", "reason"),
    [
        ({}, "no_github_connection"),
        (
            {"github": {"connection_selection_error": "github_connection_required"}},
            "github_connection_required",
        ),
        (
            {"github": {"connection_selection_error": "github_connection_unavailable"}},
            "github_connection_unavailable",
        ),
        ({"github": {"connection_selection_error": "something new"}}, "selection_failed"),
        ({"github": {"connection_origin": "local", "auth_token": "x"}}, "token_missing"),
    ],
)
def test_a_closed_gate_names_why_without_the_credentials(
    monkeypatch: pytest.MonkeyPatch, resolved: dict[str, Any], reason: str
) -> None:
    monkeypatch.setattr(github_gate, "resolve_integrations", lambda: resolved)
    monkeypatch.setattr(github_gate, "skill_prerequisite_verdict", lambda _check, _r: False)

    assert github_gate.github_gate_check() == (False, reason)


def test_a_check_that_cannot_answer_is_its_own_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_gate, "resolve_integrations", lambda: {})
    monkeypatch.setattr(github_gate, "skill_prerequisite_verdict", lambda _check, _r: None)

    assert github_gate.github_gate_check() == (False, "check_unavailable")


def test_a_recheck_asks_the_app_again(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed: list[bool] = []
    monkeypatch.setattr(
        github_gate, "load_account_integrations", lambda *, refresh: refreshed.append(refresh)
    )
    monkeypatch.setattr(github_gate, "resolve_integrations", lambda: {})
    monkeypatch.setattr(github_gate, "skill_prerequisite_verdict", lambda _check, _r: True)

    assert github_gate.github_gate_check(refresh=True) == (True, None)
    assert refreshed == [True]


class _RecordingAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object]]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, dict(properties or {})))


def _gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    checks: list[tuple[bool, str | None]],
    picks: list[ConnectGithubChoice | None],
    interactive: bool = True,
) -> _RecordingAnalytics:
    """Script the app's answers and the menu picks; return the recorded events."""
    analytics = _RecordingAnalytics()
    answers = iter(checks)
    menu = iter(picks)
    monkeypatch.setattr(capture, "get_analytics", lambda: analytics)
    monkeypatch.setattr(github_gate, "is_test_run", lambda: False)
    monkeypatch.setattr(github_gate, "github_setup_url", lambda: "https://app.opensre.test/home")
    monkeypatch.setattr(github_gate, "github_gate_check", lambda **_kwargs: next(answers))
    monkeypatch.setattr(github_gate.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(connect_github, "repl_tty_interactive", lambda: interactive)
    monkeypatch.setattr(connect_github, "render_connect_github_screen", lambda _console: None)
    monkeypatch.setattr(connect_github, "prompt_connect_github", lambda **_kwargs: next(menu))
    monkeypatch.setattr(rendering, "repl_clear_screen", lambda: None)
    return analytics


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=80)


def test_the_gate_records_exposure_dashboard_and_each_recheck_until_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: blocked at launch, open the dashboard, continue too early, then once connected.
    analytics = _gate(
        monkeypatch,
        checks=[
            (False, "no_github_connection"),
            (False, "github_connection_required"),
            (True, None),
        ],
        picks=[ConnectGithubChoice.OPEN, ConnectGithubChoice.CHECK, ConnectGithubChoice.CHECK],
    )

    # Act
    proceed = github_gate.pass_github_gate(_console())

    # Assert: the failed recheck keeps its reason, the passing one says connected.
    assert proceed is True
    assert analytics.events == [
        (
            Event.GITHUB_GATE_PROMPTED,
            {"reason_code": "no_github_connection", "entrypoint": "github_gate"},
        ),
        (Event.BROWSER_OPEN_REQUESTED, {"target": "integration_setup", "opened": True}),
        (
            Event.GITHUB_GATE_RECHECKED,
            {
                "connected": False,
                "recheck_count": 1,
                "reason_code": "github_connection_required",
                "entrypoint": "github_gate",
            },
        ),
        (
            Event.GITHUB_GATE_RECHECKED,
            {"connected": True, "recheck_count": 2, "entrypoint": "github_gate"},
        ),
    ]


@pytest.mark.parametrize(
    ("pick", "method"), [(ConnectGithubChoice.EXIT, "menu"), (None, "dismissed")]
)
def test_leaving_the_gate_records_how_and_after_how_many_rechecks(
    monkeypatch: pytest.MonkeyPatch, pick: ConnectGithubChoice | None, method: str
) -> None:
    analytics = _gate(
        monkeypatch,
        checks=[(False, "token_missing"), (False, "token_missing")],
        picks=[ConnectGithubChoice.CHECK, pick],
    )

    assert github_gate.pass_github_gate(_console()) is False
    assert [event for event, _ in analytics.events] == [
        Event.GITHUB_GATE_PROMPTED,
        Event.GITHUB_GATE_RECHECKED,
        Event.GITHUB_GATE_EXITED,
    ]
    assert analytics.events[-1][1] == {
        "choice_label": ConnectGithubChoice.EXIT.value,
        "method": method,
        "recheck_count": 1,
        "entrypoint": "github_gate",
    }


@pytest.mark.parametrize("interactive", [True, False])
def test_the_gate_records_nothing_without_a_painted_screen(
    monkeypatch: pytest.MonkeyPatch, interactive: bool
) -> None:
    # Connected at launch: no screen. Unconnected but non-interactive: fails
    # closed without a menu. Neither is a decision the member made.
    connected = interactive
    analytics = _gate(
        monkeypatch,
        checks=[(connected, None if connected else "no_github_connection")],
        picks=[],
        interactive=interactive,
    )

    assert github_gate.pass_github_gate(_console()) is connected
    assert analytics.events == []
