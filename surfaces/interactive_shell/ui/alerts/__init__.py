"""Public surface for incoming-alert rendering and inbox draining in the REPL.

Implementation lives in
:mod:`surfaces.interactive_shell.ui.alerts.incoming`; the alert receiver,
queue, and listener lifecycle live in ``core.domain.alerts.inbox``.
"""

from surfaces.interactive_shell.ui.alerts.incoming import (
    drain_and_render_incoming,
    format_incoming_alert,
    time_ago,
)

__all__ = [
    "drain_and_render_incoming",
    "format_incoming_alert",
    "time_ago",
]
