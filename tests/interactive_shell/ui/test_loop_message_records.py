"""Responsive loop inbox records keep identifiers separate from bounded previews."""

from __future__ import annotations

import io

import pytest
from rich.cells import cell_len
from rich.console import Console

from infrastructure.scheduling.scheduler.local_delivery import LocalLoopMessage
from surfaces.interactive_shell.command_registry import loops_cmds
from surfaces.interactive_shell.session import Session


@pytest.mark.parametrize("width", [40, 80, 160])
def test_loop_messages_keep_ids_and_bound_message_preview(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setenv("TERM", "xterm")
    message = LocalLoopMessage(
        message_id="local:20261009T090000123456Z",
        task_id="task-123",
        loop_id="checkout-loop-123456789",
        name="[literal] 日本 checkout checks",
        created_at="2026-10-09T14:30:00+05:30",
        message="[literal] " + "verified evidence " * 20 + "HIDDEN TAIL",
        prompt="PRIVATE PROMPT",
    )

    def get_messages(*, limit: int) -> list[LocalLoopMessage]:
        assert limit == 7
        return [message]

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.local_delivery.get_loop_messages", get_messages
    )
    output = io.StringIO()
    loops_cmds._cmd_loops_messages(Session(), Console(file=output, width=width), ["--limit", "7"])
    text = output.getvalue()
    assert message.message_id in text
    assert message.loop_id in text
    assert "[literal]" in text and "09:00:00" in text
    assert "HIDDEN TAIL" not in text and "PRIVATE PROMPT" not in text
    assert "…" in text
    assert "\n\n[literal] verified" in text
    assert max(cell_len(line) for line in text.splitlines()) <= width
    if width == 40:
        assert "Created:" in text
