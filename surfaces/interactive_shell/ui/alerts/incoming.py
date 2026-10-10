"""Rendering and draining of incoming alerts surfaced in the REPL loop.

The alert receiver, queue, listener lifecycle, and retry timing live in
``core.domain.alerts.inbox``; this module is the REPL surface of that flow:
render one incoming alert as a panel, and drain the inbox into the session
(record-then-render, requeue on failure).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from core.domain.alerts.inbox import IncomingAlert
from infrastructure.terminal.theme import (
    DIM,
    INCOMING_ALERT_ACCENT,
    TEXT,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

    from core.domain.alerts.inbox import AlertInbox
    from surfaces.interactive_shell.runtime import Session

logger = logging.getLogger(__name__)


def time_ago(then: datetime | None) -> str:
    """Format a relative time string like '5 seconds ago', '1 minute ago', etc."""
    if then is None:
        return "unknown"

    # POST /alerts accepts sender-supplied timestamps, so ``received_at`` may be
    # offset-naive; treat it as UTC like format_repl_timestamp does.
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    delta = now - then
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago" if seconds != 1 else "1s ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago" if minutes != 1 else "1m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago" if hours != 1 else "1h ago"
    else:
        days = seconds // 86400
        return f"{days}d ago" if days != 1 else "1d ago"


def format_incoming_alert(alert: IncomingAlert) -> RenderableType:
    """Format an incoming alert as a Rich renderable with distinct styling.

    Returns a Panel with:
    - Header showing incoming alert label, source, and severity (if present)
    - Relative received time
    - Alert text body
    """
    # Build the header line: source and severity
    header_parts: list[str] = ["incoming alert"]
    if alert.source:
        header_parts.append(f"from {escape(alert.source)}")
    if alert.severity:
        # Escape the whole `[severity]` fragment so Rich cannot treat `[bold ...]` etc. as tags.
        header_parts.append(escape(f"[{alert.severity}]"))

    header = " | ".join(header_parts)

    # Format the alert body with timestamp
    timestamp_str = time_ago(alert.received_at)
    body_lines = [
        f"[{DIM}]received {timestamp_str}[/]",
        "",
        f"[{TEXT}]{escape(alert.text)}[/]",
    ]
    body = "\n".join(body_lines)

    # Create a panel with the distinct accent
    panel = Panel(
        body,
        title=f"[{INCOMING_ALERT_ACCENT}]⚠  {header}[/]",
        expand=False,
        border_style=INCOMING_ALERT_ACCENT,
    )

    return panel


def drain_and_render_incoming(
    session: Session,
    console: Console,
    inbox: AlertInbox,
) -> int:
    """Pop all queued alerts, record each in session, then render it.

    ``iter_pending`` has already popped every alert, so each one is recorded
    before it is rendered: a render failure must not lose an alert the inbox
    no longer holds. A record failure requeues the alert with a retry delay
    (``inbox.requeue``) so a later drain retries it instead of silently
    dropping it or spinning on immediate retries, and every alert is handled
    independently so one failure never aborts the drain. Returns the number of
    alerts recorded.
    """
    alerts = inbox.iter_pending()
    count = 0

    for alert in alerts:
        try:
            session.record_incoming_alert(alert)
        except Exception as exc:
            # Not in the inbox anymore: requeue with a delay so a later drain
            # retries it (the watcher's poll drives the retry; no tight loop).
            logger.warning("Recording incoming alert failed; requeued: %s", exc)
            if not inbox.requeue(alert):
                logger.warning("Retry inbox full; requeued alert evicted an older alert")
            continue
        try:
            console.print(format_incoming_alert(alert), end="\n")
        except Exception as exc:
            # Display-only failure: the alert is already recorded, and
            # requeueing it here would record it a second time.
            logger.warning("Rendering incoming alert failed (recorded): %s", exc)
        count += 1

    return count
