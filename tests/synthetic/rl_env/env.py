"""RDSRCAEnv — gym-style RL environment for RDS root-cause analysis.

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
from typing import Any

from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture, load_all_scenarios
from tests.synthetic.rl_env.actions import EVIDENCE_KEY, MAX_STEPS, STEP_COST, Action
from tests.synthetic.rl_env.scoring import score_diagnosis
from tests.synthetic.rl_env.types import Observation, StepResult


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
        """Return actions available for the active scenario.

        Gather actions are restricted to evidence sources declared in
        scenario.yml:available_evidence so unavailable sources are never
        offered to the agent. Falls back to all actions before reset().
        """
        if self._fixture is None:
            return list(Action)
        available = self._fixture.metadata.available_evidence
        gather_actions = [a for a, key in EVIDENCE_KEY.items() if key in available]
        return gather_actions + [Action.SUBMIT_DIAGNOSIS]

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
            reward, info = score_diagnosis(diagnosis or {}, self._fixture.answer_key)
            self._done = True
            return StepResult(
                observation=self._observation(),
                reward=reward + STEP_COST,
                done=True,
                info=info,
            )

        # Gather-evidence action: unlock the corresponding source (idempotent).
        # If the source is absent from this scenario's available_evidence, the
        # action is a no-op so the agent incurs only the step cost.
        evidence_key = EVIDENCE_KEY.get(action)
        if (
            evidence_key
            and evidence_key not in self._gathered_evidence
            and evidence_key in self._fixture.metadata.available_evidence
        ):
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
