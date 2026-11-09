"""Regression tests for interactive work-item commands."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import list_work_items
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
    assert "reminder not scheduled:" in rendered
    assert "/work has no delivery target" in rendered
    assert "opensre work add" in rendered
    assert "--target <provider>:<chat-id>" in rendered
    assert confirm_calls == []
    assert session.history[-1]["ok"] is False


@pytest.mark.parametrize(
    ("arguments", "flag"),
    [
        ("Audit --project --priority urgent", "--project"),
        ('Audit --project " --priority"', "--project"),
        ("Audit --owner --unknown value", "--owner"),
        ("Audit --priority --due 2026-10-06", "--priority"),
        ("Audit --due", "--due"),
        ('Audit --project ""', "--project"),
    ],
)
def test_work_add_rejects_missing_option_value_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: str,
    flag: str,
) -> None:
    confirm_calls: list[str] = []

    def _confirm(prompt: str) -> str:
        confirm_calls.append(prompt)
        return "y"

    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False)
    session = Session()

    assert (
        dispatch_slash(f"/work add {arguments}", session, console, confirm_fn=_confirm, is_tty=True)
        is True
    )

    assert f"{flag} requires a value" in output.getvalue()
    assert confirm_calls == []
    assert session.history[-1]["ok"] is False
    assert not work_cmds.work_items_path().exists()


def test_work_add_preserves_title_and_option_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False)

    assert (
        dispatch_slash(
            '/work add Audit access --project "Security review" --priority urgent '
            '--owner "On call" --due 2026-10-06',
            Session(),
            console,
        )
        is True
    )

    [item] = list_work_items(status=None)
    assert item.title == "Audit access"
    assert item.project == "Security review"
    assert item.priority.value == "urgent"
    assert item.owner == "On call"
    assert item.due_at == "2026-10-06"


def test_work_help_explains_how_to_schedule_reminders() -> None:
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False)

    assert dispatch_slash("/help /work", Session(), console) is True

    rendered = output.getvalue()
    assert "To schedule a reminder" in rendered
    assert "--remind-at <datetime>" in rendered
    assert "--target <provider>:<chat-id>" in rendered
