"""Triage UI preserves literal data and full IDs at common terminal widths."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.cells import cell_len
from rich.console import Console

from surfaces.interactive_shell.command_registry.triage_cmds import cmd_triage
from surfaces.interactive_shell.runtime.input_policy import turn_needs_exclusive_stdin
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.triage import print_investigations


@pytest.mark.parametrize("width", [40, 100, 160])
def test_triage_table_preserves_literal_source_and_id(width: int) -> None:
    output = io.StringIO()
    console = Console(file=output, width=width)
    identifier = "0123456789abcdef0123456789abcdef"
    print_investigations(
        console,
        [
            {
                "id": identifier,
                "source_id": "[bold]日本 payment[/bold]",
                "lifecycle": "resolved",
                "state": "failed",
                "failure": "query unavailable",
            }
        ],
    )
    text = output.getvalue()
    assert identifier in text
    assert "[bold]" in text and "日本" in text
    assert "resolved" in text and "failed" in text and "query unavailable" in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


def test_triage_demo_leaves_prompt_available_while_child_keeps_running() -> None:
    calls = []

    def run_cli(*args: Any, **kwargs: Any) -> bool:
        calls.append((args, kwargs))
        return True

    assert cmd_triage(Session(), Console(file=io.StringIO()), ["demo"], run_cli=run_cli)
    assert calls[0][1]["subprocess_timeout"] == 3
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["keep_running_hint"]


@pytest.mark.parametrize(
    "command", ["/triage", "/triage list", "/triage show abc", "/triage connect", "/triage demo"]
)
def test_exact_table_and_picker_commands_reserve_stdin(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.input_policy.repl_tty_interactive", lambda: True
    )
    assert turn_needs_exclusive_stdin(command, Session())
