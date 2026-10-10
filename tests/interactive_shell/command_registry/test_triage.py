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


@pytest.mark.parametrize(
    ("field", "invalid", "correction", "expected"),
    [("services", "payment,", "\x7f", "payment"), ("name", "x" * 201, "\x15Payments", "Payments")],
)
def test_connect_form_reopens_invalid_field_and_preserves_other_edits(
    field: str, invalid: str, correction: str, expected: str
) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from surfaces.shared.terminal.triage_input import build_connect_form

    original = {
        "name": "Payment source",
        "query_url": "https://signoz.example",
        "services": "payment",
        "ingress_url": "https://gateway.example",
    }
    original[field] = invalid
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_connect_form(original)
        result = app.run(pre_run=lambda: pipe.send_text("\x13" + correction + "\r\x13"))
    assert result is not None
    submitted = dict(zip((a[2:].replace("-", "_") for a in result[::2]), result[1::2], strict=True))
    assert submitted == {**original, field: expected}
    assert original[field] == invalid


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
