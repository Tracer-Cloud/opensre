"""Headless agent-runner contract for scheduled-delivery tasks.

Some scheduled reports (e.g. the Sentry morning digest) run a single headless
agent turn with skill guidance.
``infrastructure.scheduling.scheduler`` must not import ``tools`` or
``integrations`` directly (T-4 layering audit, issue #3352), so the concrete
runner is built by the composition root and passed in as part of
:class:`~infrastructure.scheduling.scheduler.runners.SchedulerRunners`; this
module only declares the contract.
"""

from __future__ import annotations

from typing import Any, Protocol

from infrastructure.scheduling.scheduler.types import TaskReport

AgentPayload = dict[str, Any]


class AgentRunner(Protocol):
    """Callable that runs a headless agent turn and returns report text."""

    def __call__(self, payload: AgentPayload) -> TaskReport:
        """Run the agent for ``payload`` and return the formatted report."""


__all__ = ["AgentPayload", "AgentRunner"]
