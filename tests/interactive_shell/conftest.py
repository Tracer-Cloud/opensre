"""Shared fixtures for interactive-shell tests."""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _terminal_env_for_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match a normal terminal so layout tests see the width they pass in.

    ``FORCE_COLOR=0`` still marks a Rich console as a terminal, and ``TERM=dumb``
    makes that console report 80 columns instead of an explicit width.
    """
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    if os.environ.get("TERM", "").lower() in {"", "dumb", "unknown"}:
        monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.execution_confirm.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


@pytest.fixture(autouse=True)
def _reset_active_theme() -> None:
    """Reset the active theme to green before each test.

    ``set_active_theme()`` mutates module-level state in
    ``infrastructure.terminal.theme``, which persists across tests
    and can cause order-dependent failures.
    """
    from infrastructure.terminal.theme import set_active_theme

    set_active_theme("green")
