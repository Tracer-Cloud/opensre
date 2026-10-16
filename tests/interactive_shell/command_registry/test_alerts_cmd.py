"""Tests for the ``/alerts`` slash command's inbox status display."""

from __future__ import annotations

from rich.console import Console

from core.domain.alerts.inbox import AlertInbox, IncomingAlert, set_current_inbox
from surfaces.interactive_shell.command_registry.alerts import _cmd_alerts
from surfaces.interactive_shell.runtime import Session


def _render(inbox: AlertInbox) -> str:
    """Run the ``/alerts`` command against ``inbox`` and return the text output."""
    set_current_inbox(inbox)
    try:
        console = Console(record=True)
        assert _cmd_alerts(Session(), console, []) is True
        return console.export_text()
    finally:
        set_current_inbox(None)


def test_alerts_cmd_shows_pending_retries_while_queue_is_empty() -> None:
    """A failed alert waiting out its retry delay must be visible in /alerts.

    ``requeue`` holds alerts outside the main queue, so ``queue depth`` reads
    zero for them; the pending-retries row is what keeps them from looking
    lost (Greptile P2: waiting alerts look gone).
    """
    inbox = AlertInbox(maxsize=5)
    assert inbox.requeue(IncomingAlert(text="disk full"), delay_seconds=30.0)
    assert inbox.qsize == 0  # not drainable yet — but it must not look gone

    output = _render(inbox)

    assert "pending retries" in output
    assert "1" in output
    assert "queue depth" in output
    assert inbox.pending_retries == 1


def test_alerts_cmd_reports_zero_pending_retries_when_none_waiting() -> None:
    """With no failures, the row renders as a plain zero."""
    inbox = AlertInbox(maxsize=5)

    output = _render(inbox)

    assert "pending retries" in output
    assert "0" in output
    assert inbox.pending_retries == 0
