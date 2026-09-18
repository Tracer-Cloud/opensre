"""The production REPL cannot start without a validated OpenSRE account."""

from __future__ import annotations

import asyncio
import io
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
import surfaces.interactive_shell.runtime.startup.account_gate as account_gate
from config.repl_config import ReplConfig
from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.sign_in import SignInChoice


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=80)


class _RecordingAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object]]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, dict(properties or {})))


def _gate_with_choices(
    monkeypatch: Any, choices: list[SignInChoice | None], *, signed_in: bool = False
) -> _RecordingAnalytics:
    analytics = _RecordingAnalytics()
    picks = iter(choices)
    monkeypatch.setattr(capture, "get_analytics", lambda: analytics)
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: signed_in)
    monkeypatch.setattr("surfaces.interactive_shell.ui.sign_in.repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.render_sign_in_screen", lambda _console: None
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.prompt_login_or_exit", lambda: next(picks)
    )
    return analytics


def test_account_is_signed_in_requires_active_webapp_status(monkeypatch: Any) -> None:
    status = SimpleNamespace(authenticated=True)
    monkeypatch.setattr("surfaces.shared.account_session.account_status", lambda: status)

    assert account_gate.account_is_signed_in() is True

    status.authenticated = False
    assert account_gate.account_is_signed_in() is False


def test_account_login_runs_webapp_command_then_validates_session(monkeypatch: Any) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.build_opensre_cli_argv",
        lambda args: ["opensre", *args],
    )

    def _run(
        command: list[str], *, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(account_gate.subprocess, "run", _run)
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: True)

    assert account_gate.account_login(console=_console()) is True
    assert calls[0][0] == ["opensre", "account", "login"]
    assert calls[0][1]["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"


def test_account_login_rejects_failed_or_incomplete_login(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.subprocess_runner.build_opensre_cli_argv",
        lambda args: ["opensre", *args],
    )
    monkeypatch.setattr(
        account_gate.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1),
    )
    console = _console()

    assert account_gate.account_login(console=console) is False
    assert "did not complete" in console.file.getvalue()  # type: ignore[attr-defined]


def test_pass_sign_in_gate_skips_prompts_during_tests(monkeypatch: Any) -> None:
    called: list[bool] = []
    monkeypatch.setattr(account_gate, "is_test_run", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.run_sign_in_gate",
        lambda *_a, **_k: called.append(True) or False,
    )

    assert account_gate.pass_sign_in_gate(_console()) is True
    assert called == []


def test_pass_sign_in_gate_allows_only_valid_account(monkeypatch: Any) -> None:
    monkeypatch.setattr(account_gate, "is_test_run", lambda: False)
    monkeypatch.setattr(account_gate, "account_is_signed_in", lambda: False)
    monkeypatch.setattr("surfaces.interactive_shell.ui.sign_in.repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.render_sign_in_screen", lambda _console: None
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.prompt_login_or_exit",
        lambda: SignInChoice.EXIT,
    )

    assert account_gate.pass_sign_in_gate(_console()) is False


def test_gate_records_exposure_and_every_explicit_choice_before_login_runs(
    monkeypatch: Any,
) -> None:
    analytics = _gate_with_choices(
        monkeypatch, [SignInChoice.LOGIN, SignInChoice.LOGIN, SignInChoice.EXIT]
    )
    login_attempts = 0

    def _failing_login(**_kwargs: Any) -> bool:
        nonlocal login_attempts
        login_attempts += 1
        # The intent must already be queued when the browser flow starts, so an
        # abandoned login still leaves evidence of the click.
        assert analytics.events[-1][0] is Event.SIGN_IN_SELECTED
        return False

    monkeypatch.setattr(account_gate, "account_login", _failing_login)

    assert account_gate.pass_sign_in_gate(_console()) is False
    assert login_attempts == 2
    assert [event for event, _ in analytics.events] == [
        Event.SIGN_IN_PROMPTED,
        Event.SIGN_IN_SELECTED,
        Event.SIGN_IN_SELECTED,
        Event.STAY_SIGNED_OUT_SELECTED,
    ]
    assert analytics.events[1][1] == {
        "choice_label": SignInChoice.LOGIN.value,
        "method": "menu",
        "entrypoint": "sign_in_gate",
    }
    assert analytics.events[3][1] == {
        "choice_label": SignInChoice.EXIT.value,
        "method": "menu",
        "entrypoint": "sign_in_gate",
    }


def test_gate_records_a_dismissed_menu_as_staying_signed_out(monkeypatch: Any) -> None:
    # The picker returns None for Esc, q, Ctrl-C, Ctrl-D and EOF alike, so the
    # gate must not claim a more specific key than it can observe.
    analytics = _gate_with_choices(monkeypatch, [None])

    assert account_gate.pass_sign_in_gate(_console()) is False
    assert [event for event, _ in analytics.events] == [
        Event.SIGN_IN_PROMPTED,
        Event.STAY_SIGNED_OUT_SELECTED,
    ]
    assert analytics.events[1][1]["method"] == "dismissed"


def test_gate_records_only_the_prompt_and_choice_on_successful_login(monkeypatch: Any) -> None:
    analytics = _gate_with_choices(monkeypatch, [SignInChoice.LOGIN])
    monkeypatch.setattr(account_gate, "account_login", lambda **_kwargs: True)

    assert account_gate.pass_sign_in_gate(_console()) is True
    # ``account_authenticated`` is owned by the login command, not the gate.
    assert [event for event, _ in analytics.events] == [
        Event.SIGN_IN_PROMPTED,
        Event.SIGN_IN_SELECTED,
    ]


@pytest.mark.parametrize("interactive", [True, False])
def test_gate_emits_nothing_without_a_rendered_menu(monkeypatch: Any, interactive: bool) -> None:
    # Already signed in: the screen is never shown. Signed out but
    # non-interactive: the gate fails closed without a menu. Neither is a choice.
    analytics = _gate_with_choices(monkeypatch, [], signed_in=interactive)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.sign_in.repl_tty_interactive", lambda: interactive
    )

    assert account_gate.pass_sign_in_gate(_console()) is interactive
    assert analytics.events == []


def test_run_repl_stops_before_runtime_when_sign_in_is_declined(monkeypatch: Any) -> None:
    started: list[bool] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda _console: False)

    async def _run_async(**_kwargs: Any) -> int:
        started.append(True)
        return 0

    monkeypatch.setattr(main_entrypoint, "run_repl_async", _run_async)

    assert main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic")) == 0
    assert started == []


def test_run_repl_clears_sign_in_screen_then_starts_banner(monkeypatch: Any) -> None:
    events: list[str] = []
    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda _console: True)
    monkeypatch.setattr(main_entrypoint, "repl_clear_screen", lambda: events.append("clear"))

    def _start_banner(_console: Console) -> Any:
        events.append("banner")
        return lambda: events.append("finish")

    monkeypatch.setattr(main_entrypoint, "_start_launch_banner", _start_banner)

    async def _run_async(**kwargs: Any) -> int:
        assert kwargs["finish_banner"] is not None
        events.append("runtime")
        return 0

    monkeypatch.setattr(main_entrypoint, "run_repl_async", _run_async)

    assert main_entrypoint.run_repl(config=ReplConfig(enabled=True, layout="classic")) == 0
    assert events == ["clear", "banner", "runtime"]


def test_run_repl_async_is_the_already_gated_shell_body(monkeypatch: Any) -> None:
    gated: list[bool] = []
    started: list[bool] = []

    class _Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return

        async def start_interactive_shell(self) -> None:
            started.append(True)

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )
    monkeypatch.setattr(
        main_entrypoint, "pass_sign_in_gate", lambda _console: gated.append(True) or False
    )
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_a, **_k: None)
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)

    class _SessionStore:
        def open_store(self, _session: object) -> None:
            return

        def refresh_from_storage(self, _session: object) -> None:
            return

        def close(self, _session: object) -> None:
            return

    monkeypatch.setattr(main_entrypoint.SessionManager, "for_session", lambda _s: _SessionStore())

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0
    assert gated == []
    assert started == [True]


def _boot_repl_without_prompt(monkeypatch: Any) -> None:
    class _Controller:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return

        async def start_interactive_shell(self) -> None:
            return

    class _SessionStore:
        def open_store(self, _session: object) -> None:
            return

        def refresh_from_storage(self, _session: object) -> None:
            return

        def close(self, _session: object) -> None:
            return

    monkeypatch.setattr(main_entrypoint, "identify_saved_github_username", lambda: None)
    monkeypatch.setattr(
        main_entrypoint,
        "create_repl_runtime",
        lambda **_kwargs: SimpleNamespace(session=Session(), inbox=None),
    )
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)
    monkeypatch.setattr(main_entrypoint.SessionManager, "for_session", lambda _s: _SessionStore())


def test_run_repl_async_always_offers_the_demo(monkeypatch: Any) -> None:
    offered: list[bool] = []
    _boot_repl_without_prompt(monkeypatch)
    monkeypatch.setattr(
        main_entrypoint, "offer_demo", lambda *_a, **_k: offered.append(True) or False
    )

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0
    assert offered == [True]
