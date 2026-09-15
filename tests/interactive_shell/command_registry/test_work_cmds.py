"""Regression tests for interactive work-item commands."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import dispatch_slash, work_cmds
from surfaces.interactive_shell.session import Session


@pytest.mark.parametrize(
    "command",
    [
        "/work add rotate API key --remind 2026-09-12T09:00",
        "/work add rotate API key --remind=2026-09-12T09:00",
        "/work add rotate API key --remind-at 2026-09-12T09:00",
    ],
)
def test_work_add_rejects_unscheduled_reminder_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    confirm_calls: list[str] = []

    def _confirm(prompt: str) -> str:
        confirm_calls.append(prompt)
        return "y"

    def _unexpected_add_work_item(**_kwargs: object) -> object:
        raise AssertionError("an unsupported reminder must not be persisted")

    monkeypatch.setattr(work_cmds, "add_work_item", _unexpected_add_work_item)
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False)
    session = Session()

    assert (
        dispatch_slash(
            command,
            session,
            console,
            confirm_fn=_confirm,
            is_tty=True,
        )
        is True
    )

    rendered = output.getvalue()
    assert "cannot schedule reminders" in rendered
    assert "opensre work add" in rendered
    assert "--target <provider>:<chat-id>" in rendered
    assert confirm_calls == []
    assert session.history[-1]["ok"] is False


def test_work_help_explains_how_to_schedule_reminders() -> None:
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False)

    assert dispatch_slash("/help /work", Session(), console) is True

    rendered = output.getvalue()
    assert "To schedule a reminder" in rendered
    assert "--remind-at <datetime>" in rendered
    assert "--target <provider>:<chat-id>" in rendered
