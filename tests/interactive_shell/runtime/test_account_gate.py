"""The production REPL cannot start without a validated OpenSRE account."""

from __future__ import annotations

import asyncio
import io
import subprocess
from types import SimpleNamespace
from typing import Any

from rich.console import Console

import surfaces.interactive_shell.main as main_entrypoint
import surfaces.interactive_shell.runtime.startup.account_gate as account_gate
from config.repl_config import ReplConfig
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.sign_in import SignInChoice


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False, width=80)


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
    monkeypatch.setattr(main_entrypoint, "offer_loop_suggestions", lambda *_a, **_k: None)
    monkeypatch.setattr(main_entrypoint, "InteractiveShellController", _Controller)

    class _SessionStore:
        def open_store(self, _session: object) -> None:
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


def test_run_repl_async_does_not_open_loops_after_a_failed_demo(monkeypatch: Any) -> None:
    loops: list[str] = []
    _boot_repl_without_prompt(monkeypatch)
    monkeypatch.setattr(main_entrypoint, "should_offer_demo", lambda: True)
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_a, **_k: False)
    monkeypatch.setattr(main_entrypoint, "demo_already_offered", lambda: False)
    monkeypatch.setattr(
        main_entrypoint, "offer_loop_suggestions", lambda *_a, **_k: loops.append("opened")
    )

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0
    assert loops == []


def test_run_repl_async_opens_loops_after_a_skipped_demo(monkeypatch: Any) -> None:
    loops: list[str] = []
    _boot_repl_without_prompt(monkeypatch)
    monkeypatch.setattr(main_entrypoint, "should_offer_demo", lambda: True)
    monkeypatch.setattr(main_entrypoint, "offer_demo", lambda *_a, **_k: False)
    monkeypatch.setattr(main_entrypoint, "demo_already_offered", lambda: True)
    monkeypatch.setattr(
        main_entrypoint, "offer_loop_suggestions", lambda *_a, **_k: loops.append("opened")
    )

    assert asyncio.run(main_entrypoint.run_repl_async()) == 0
    assert loops == ["opened"]
