"""Interactive-shell session: the ``Session`` subclass and its UI facets.

The shell-only half of the session, layered on
:class:`~core.agent_harness.session.session_core.SessionCore`: the ``terminal``
facet (theme, prompt-toolkit, background jobs, metrics) and the ``alerts`` inbox.
Core, gateway, and headless surfaces use ``SessionCore`` directly and never import
this package.
"""

from __future__ import annotations

from surfaces.interactive_shell.session.alert_inbox import SessionAlertInbox
from surfaces.interactive_shell.session.session import Session
from surfaces.interactive_shell.session.terminal_metrics import (
    InterventionKind,
    TerminalMetrics,
    TerminalMetricsSnapshot,
)
from surfaces.interactive_shell.session.terminal_session import TerminalSession

__all__ = [
    "InterventionKind",
    "Session",
    "SessionAlertInbox",
    "TerminalMetrics",
    "TerminalMetricsSnapshot",
    "TerminalSession",
]
