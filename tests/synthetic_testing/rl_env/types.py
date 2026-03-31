"""Data types for observations and step results in the RDS RCA RL environment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """Everything the agent can see at a given step."""

    alert: dict[str, Any]
    """The triggering alert (always visible from step 0)."""

    gathered_evidence: dict[str, Any]
    """Evidence unlocked so far; keys are a subset of rds_metrics / rds_events / performance_insights."""

    step: int
    """Current step index within the episode (0 = after reset)."""

    done: bool
    """True when the episode has ended."""


@dataclass
class StepResult:
    observation: Observation
    reward: float
    done: bool
    info: dict[str, Any] = field(default_factory=dict)
