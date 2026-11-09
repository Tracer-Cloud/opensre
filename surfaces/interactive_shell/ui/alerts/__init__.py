"""Rich rendering for incoming alerts surfaced in the REPL loop.

The alert receiver, queue, and listener lifecycle live in
``core.domain.alerts.inbox``.
"""

from __future__ import annotations

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
    """Pop all queued alerts, render each one, and record them in session.

    Returns the number of alerts rendered.
    """
    alerts = inbox.iter_pending()
    count = 0

    for alert in alerts:
        console.print(format_incoming_alert(alert), end="\n")
        session.record_incoming_alert(alert)
        count += 1

    return count


__all__ = ["format_incoming_alert", "drain_and_render_incoming", "time_ago"]
