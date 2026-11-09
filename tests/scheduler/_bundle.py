"""Test helpers to build the ``SchedulerRunners`` bundle scheduler tests pass in.

The runner re-reads its concrete implementation on each call, so a bundle
built once still honours a test's monkeypatch of those attributes.
"""

from __future__ import annotations

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload, AgentRunner
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest


def real_runners() -> SchedulerRunners:
    """The production runner bundle (real agent digest)."""
    return SchedulerRunners(agent=run_scheduled_agent_digest)


def runners_with_agent(agent: AgentRunner) -> SchedulerRunners:
    """A bundle whose agent runner is ``agent``."""
    return SchedulerRunners(agent=agent)


__all__ = ["AgentPayload", "real_runners", "runners_with_agent"]
