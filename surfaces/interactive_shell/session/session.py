"""Interactive-shell session: SessionCore plus terminal UI state.

Extends :class:`~core.agent_harness.session.session_core.SessionCore` with the
shell-only facets (``terminal`` UI/background state and the ``alerts`` inbox) and
the methods that drive them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.agent_harness import SessionCore
from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.session.alert_inbox import SessionAlertInbox
from surfaces.interactive_shell.session.terminal_session import TerminalSession


@dataclass
class Session(SessionCore):
    """Per-REPL-process session: :class:`SessionCore` plus interactive-shell state.

    Adds the shell-only ``terminal`` facet (UI/theme/prompt-toolkit/background)
    and the ``alerts`` inbox on top of the surface-agnostic core.
    """

    terminal: TerminalSession = field(default_factory=TerminalSession)
    """Interactive-shell (terminal) session facet — shell-only UI/theme/background state.

    Always present (empty for non-shell sessions) so shell code needs no None-guard;
    ``core``/``gateway``/``tools`` consumers ignore it. Holds the theme, prompt-toolkit,
    pending-prompt/stdin, background-jobs, and metrics clusters (#3690)."""

    alerts: SessionAlertInbox = field(default_factory=SessionAlertInbox)
    """Inbox of externally-received alerts (shell alert listener → ``/status``).

    A surface facet: the bounded alert list + cap live on ``SessionAlertInbox`` so
    core-session consumers that never touch alerts don't see the field."""

    def record_incoming_alert(self, alert: IncomingAlert) -> None:
        """Persist the alert, then mirror it into in-memory session state.

        Persistence comes first: a store that raises, or signals failure with
        an empty record id (the JSONL backend suppresses write errors and
        returns '' when the record was not persisted), must leave no history or
        ``alerts``-facet entry behind — otherwise a retry would duplicate the
        logical alert. On success there is exactly one durable record and one
        logical history entry. Also stores the full IncomingAlert (received_at,
        severity, source, alert_name) in the ``alerts`` facet for /status.

        Raises:
            OSError: when the store did not persist the record; the alert stays
                recoverable by the caller (the inbox drain requeues it).
        """
        entry_id = self.store.append_turn(self, "incoming_alert", alert.text)
        if not entry_id:
            raise OSError("session store did not persist the incoming alert")
        self.history.append({"type": "incoming_alert", "text": alert.text, "ok": True})
        self.alerts.add(alert)

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset the session — core state plus the shell facets — for /new and /resume."""
        self.terminal.history_generation += 1
        super().clear(rotate_identity=rotate_identity)
        self.alerts.clear()
        self.terminal.metrics.reset()
        self.terminal.pending_prompt_default = None
        self.terminal.pending_prompt_autosubmit = False
        self.terminal.pending_prompt_plain_turn = False
        self.terminal.last_input_autosubmitted = False
        self.terminal.pending_choice_response = None
        self.terminal.setup_resume = None
        self.terminal.dispatch_active = False
        self.terminal.exclusive_stdin_active = False
        # trust_mode and reasoning_effort are intentionally preserved across /new

    def release_resources(self) -> None:
        """Cancel background work and drop loop-owned UI references for teardown.

        Extends :meth:`SessionCore.release_resources` (which cancels the
        integration-warm task) with the shell facet's own teardown.
        """
        super().release_resources()
        self.terminal.prompt_refresh_fn = None
        self.terminal.fleet_sampler_starter = None
        self.terminal.startup_work_release = None
