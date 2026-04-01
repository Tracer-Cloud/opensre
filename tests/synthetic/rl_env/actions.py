"""Action space definition for the RDS RCA reinforcement learning environment."""

from __future__ import annotations

from enum import IntEnum

MAX_STEPS = 10
STEP_COST = -0.05


class Action(IntEnum):
    INSPECT_METRICS = 0
    INSPECT_EVENTS = 1
    INSPECT_PERFORMANCE_INSIGHTS = 2
    SUBMIT_DIAGNOSIS = 3


# Maps gather-actions to the evidence key they unlock
EVIDENCE_KEY: dict[Action, str] = {
    Action.INSPECT_METRICS: "rds_metrics",
    Action.INSPECT_EVENTS: "rds_events",
    Action.INSPECT_PERFORMANCE_INSIGHTS: "performance_insights",
}
