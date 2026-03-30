"""
Reinforcement Learning environment for RDS root-cause analysis.

The agent starts each episode with only the alert visible. It must choose
which evidence sources to inspect before submitting a diagnosis. A step cost
encourages efficient investigation; the terminal reward is based on diagnosis
accuracy (category match + keyword coverage).

Episode lifecycle
-----------------
    env = RDSRCAEnv()
    obs = env.reset()                         # alert only, no evidence yet
    result = env.step(Action.INSPECT_METRICS) # gather a source
    result = env.step(
        Action.SUBMIT_DIAGNOSIS,
        diagnosis={
            "root_cause_category": "resource_exhaustion",
            "root_cause": "WAL generation exceeded replica replay capacity ...",
        },
    )                                         # terminal step

Reward structure
----------------
    Correct diagnosis (all keywords, right category):  +1.0
    Partial diagnosis (some keywords, right category): fraction of keywords matched
    Wrong category or no root cause:                    0.0
    Per-step cost (applied every step):               -0.05
    Max steps exceeded without diagnosis:              episode ends, reward 0.0
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from tests.synthetic_testing.rds_postgres.scenario_loader import ScenarioFixture, load_all_scenarios

# ---------------------------------------------------------------------------
# Action space
# ---------------------------------------------------------------------------

MAX_STEPS = 10
STEP_COST = -0.05


class Action(IntEnum):
    INSPECT_METRICS = 0
    INSPECT_EVENTS = 1
    INSPECT_PERFORMANCE_INSIGHTS = 2
    SUBMIT_DIAGNOSIS = 3


# Maps gather-actions to the evidence key they unlock
_EVIDENCE_KEY: dict[Action, str] = {
    Action.INSPECT_METRICS: "rds_metrics",
    Action.INSPECT_EVENTS: "rds_events",
    Action.INSPECT_PERFORMANCE_INSIGHTS: "performance_insights",
}


# ---------------------------------------------------------------------------
# Observation and step result
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class RDSRCAEnv:
    """
    Gym-style RL environment wrapping the synthetic RDS RCA benchmark.

    Parameters
    ----------
    fixtures:
        Explicit list of scenario fixtures. Defaults to all scenarios returned
        by ``load_all_scenarios()``. Pass a subset to restrict training scenarios.
    """

    def __init__(self, fixtures: list[ScenarioFixture] | None = None) -> None:
        self._fixtures: list[ScenarioFixture] = fixtures or load_all_scenarios()
        if not self._fixtures:
            raise ValueError("No scenario fixtures available — check your scenario directories.")
        self._fixture: ScenarioFixture | None = None
        self._gathered_evidence: dict[str, Any] = {}
        self._step: int = 0
        self._done: bool = True

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    @property
    def action_space(self) -> list[Action]:
        return list(Action)

    @property
    def fixture(self) -> ScenarioFixture | None:
        """The active scenario fixture (None before first reset)."""
        return self._fixture

    def reset(self, scenario_id: str | None = None) -> Observation:
        """
        Start a new episode.

        Parameters
        ----------
        scenario_id:
            Pin to a specific scenario (e.g. ``"001-replication-lag"``).
            If None, a scenario is chosen at random.
        """
        if scenario_id is not None:
            matches = [f for f in self._fixtures if f.scenario_id == scenario_id]
            if not matches:
                raise ValueError(
                    f"Unknown scenario_id {scenario_id!r}. "
                    f"Available: {[f.scenario_id for f in self._fixtures]}"
                )
            self._fixture = matches[0]
        else:
            self._fixture = random.choice(self._fixtures)

        self._gathered_evidence = {}
        self._step = 0
        self._done = False
        return self._observation()

    def step(
        self,
        action: Action,
        diagnosis: dict[str, Any] | None = None,
    ) -> StepResult:
        """
        Advance the episode by one step.

        Parameters
        ----------
        action:
            One of the ``Action`` enum values.
        diagnosis:
            Required when ``action == Action.SUBMIT_DIAGNOSIS``.
            Expected keys: ``root_cause_category`` (str), ``root_cause`` (str).
        """
        if self._done or self._fixture is None:
            raise RuntimeError("Episode has ended. Call reset() to start a new one.")

        self._step += 1

        if action == Action.SUBMIT_DIAGNOSIS:
            reward, info = self._score_diagnosis(diagnosis or {})
            self._done = True
            return StepResult(
                observation=self._observation(),
                reward=reward + STEP_COST,
                done=True,
                info=info,
            )

        # Gather-evidence action: unlock the corresponding source (idempotent)
        evidence_key = _EVIDENCE_KEY.get(action)
        if evidence_key and evidence_key not in self._gathered_evidence:
            self._gathered_evidence[evidence_key] = self._fixture.evidence.get(evidence_key)

        if self._step >= MAX_STEPS:
            self._done = True
            return StepResult(
                observation=self._observation(),
                reward=STEP_COST,
                done=True,
                info={"reason": "max_steps_exceeded"},
            )

        return StepResult(
            observation=self._observation(),
            reward=STEP_COST,
            done=False,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _observation(self) -> Observation:
        assert self._fixture is not None
        return Observation(
            alert=self._fixture.alert,
            gathered_evidence=dict(self._gathered_evidence),
            step=self._step,
            done=self._done,
        )

    def _score_diagnosis(self, diagnosis: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Return (reward, info) for a submitted diagnosis."""
        assert self._fixture is not None
        answer = self._fixture.answer_key

        root_cause = str(diagnosis.get("root_cause") or "").strip()
        actual_category = str(diagnosis.get("root_cause_category") or "").strip()
        root_cause_present = bool(
            root_cause and root_cause.lower() != "unable to determine root cause"
        )

        if not root_cause_present or actual_category != answer.root_cause_category:
            return 0.0, {
                "passed": False,
                "expected_category": answer.root_cause_category,
                "actual_category": actual_category,
                "keyword_score": 0.0,
                "matched_keywords": [],
                "missing_keywords": list(answer.required_keywords),
            }

        normalized = " ".join(root_cause.lower().split())
        matched = [k for k in answer.required_keywords if k.lower() in normalized]
        missing = [k for k in answer.required_keywords if k not in matched]
        keyword_score = len(matched) / len(answer.required_keywords) if answer.required_keywords else 1.0

        return keyword_score, {
            "passed": not missing,
            "expected_category": answer.root_cause_category,
            "actual_category": actual_category,
            "keyword_score": keyword_score,
            "matched_keywords": matched,
            "missing_keywords": missing,
        }
