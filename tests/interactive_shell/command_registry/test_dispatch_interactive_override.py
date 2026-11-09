"""A non-TTY slash command's ``OPENSRE_INTERACTIVE=0`` stays in its own context.

The gateway dispatches slash commands from concurrent turns in one process, so
the override must never be a process-wide ``os.environ`` write.
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from rich.console import Console

from config.constants import OPENSRE_INTERACTIVE_ENV
from config.repl_config import ReplConfig
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, cli_parity, dispatch_slash
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.session import Session

_WAIT_SECONDS = 5.0


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


def test_concurrent_dispatches_each_keep_their_own_interactive_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping commands must neither leak nor clobber the setting.

    Under the old write-and-restore of ``os.environ``: a TTY command running
    beside a non-TTY one read "0"; a non-TTY command lost its "0" once an
    overlapping one restored the env first; and a late restore could leave "0"
    in the process env for good.
    """
    monkeypatch.setenv(OPENSRE_INTERACTIVE_ENV, "1")
    all_inside = threading.Barrier(3, timeout=_WAIT_SECONDS)
    first_done = threading.Event()
    seen: dict[str, bool] = {}

    def _probe(_session: Session, _out: Console, args: list[str]) -> bool:
        label = args[0]
        all_inside.wait()
        if label == "second":
            assert first_done.wait(_WAIT_SECONDS)
        seen[label] = ReplConfig.load().enabled
        return True

    monkeypatch.setitem(
        SLASH_COMMANDS,
        "/probe",
        SlashCommand(name="/probe", description="test probe", handler=_probe, mutating=False),
    )

    def _dispatch(label: str, *, is_tty: bool) -> None:
        dispatch_slash(f"/probe {label}", Session(), _console(), is_tty=is_tty)
        if label == "first":
            first_done.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_dispatch, "first", is_tty=False),
            pool.submit(_dispatch, "tty", is_tty=True),
            pool.submit(_dispatch, "second", is_tty=False),
        ]
        for future in futures:
            future.result(timeout=_WAIT_SECONDS)

    assert seen == {"first": False, "tty": True, "second": False}
    assert os.environ[OPENSRE_INTERACTIVE_ENV] == "1"


def test_non_tty_dispatch_passes_the_override_to_delegated_cli_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child process inherits the env, not the dispatching context."""
    monkeypatch.setenv(OPENSRE_INTERACTIVE_ENV, "1")
    child_values: list[str | None] = []

    def _fake_run(
        cmd: list[str],
        *,
        check: bool,
        timeout: float | None,
        capture_output: bool,
        text: bool,
        encoding: str,
        errors: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout, capture_output, text, encoding, errors
        child_values.append(env.get(OPENSRE_INTERACTIVE_ENV))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_parity.subprocess, "run", _fake_run)

    dispatch_slash("/auth status", Session(), _console(), is_tty=False)
    dispatch_slash("/auth status", Session(), _console(), is_tty=True)

    assert child_values == ["0", "1"]
    assert os.environ[OPENSRE_INTERACTIVE_ENV] == "1"
