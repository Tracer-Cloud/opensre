"""The runner a scheduled task dispatches through, bundled as one value.

A scheduled agentic-loop task (Sentry digest, PostHog report, GitHub PR sweep,
manual loop) runs through a registered runner. It used to be installed as a
side effect and then *rewritten* in place by the gateway to add its capacity
gate — a read-modify-write on module state that was not idempotent, so gating
twice made one run cost two permits.

Here the runner is a value instead. :meth:`SchedulerRunners.gated` returns a
new bundle rather than mutating a registered one, so a host states its gate
once, at construction, and double-gating has no shape to happen in.

The bundle is assembled by the composition root (``bootstrap.adapters``), which
is the only layer allowed to see both ``integrations`` and ``tools``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from infrastructure.process.turn_capacity import TurnGate, queued_turn_slot
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload, AgentRunner
from infrastructure.scheduling.scheduler.types import TaskReport


def _gated_agent(runner: AgentRunner, gate: TurnGate) -> AgentRunner:
    def run(payload: AgentPayload) -> TaskReport:
        with queued_turn_slot(gate):
            return runner(payload)

    return run


@dataclass(frozen=True)
class SchedulerRunners:
    """The scheduler's agent-runner seam, ready to install.

    ``host_session`` names the OpenSRE session that hosts this scheduler (the
    interactive shell's), so ticks are traced as part of that session rather
    than as one anonymous session each. Resolved per tick, not captured, so a
    rotated shell session (``/new``) is picked up on the next fire. A daemon
    with no hosting session leaves it ``None`` and ticks group per task.
    """

    agent: AgentRunner
    host_session: Callable[[], str | None] | None = None

    def gated(self, gate: TurnGate) -> SchedulerRunners:
        """Return a bundle whose runs each take one permit from ``gate``."""
        return SchedulerRunners(
            agent=_gated_agent(self.agent, gate), host_session=self.host_session
        )

    def hosted_by(self, resolve_session_id: Callable[[], str | None]) -> SchedulerRunners:
        """Return a bundle whose ticks are traced under the session ``resolve_session_id`` names."""
        return SchedulerRunners(agent=self.agent, host_session=resolve_session_id)

    def host_session_id(self) -> str | None:
        """The hosting session id right now, or ``None`` for a daemon."""
        return self.host_session() if self.host_session is not None else None


__all__ = ["SchedulerRunners", "TurnGate"]
