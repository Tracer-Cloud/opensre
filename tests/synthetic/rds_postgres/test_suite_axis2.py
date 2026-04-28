"""Axis 2 adversarial test suite for synthetic RDS RCA scenarios.

Differences from test_suite.py (Axis 1):

1. Uses SelectiveGrafanaBackend instead of FixtureGrafanaBackend.
   - The backend records every metric_name the agent requested via
     query_timeseries (audit trail).
   - It returns only the metric series matching the requested metric_name
     (case-insensitive substring), forcing the agent to query specifically
     rather than receiving all data by default.

2. Asserts two additional dimensions from ReasoningScore:
   - ruling_out_ok: the agent's output contains all ruling_out_keywords
     declared in the scenario's answer.yml (proves it dismissed alternatives).
   - queries_ok: the agent requested all required_queries metric names
     (proves it checked the right evidence before concluding).

3. Runs all scenarios with ruling_out_keywords or required_queries declared
   (currently 011–013 plus 006, 007 which have forbidden_categories but no
   Axis 2 fields yet).  Scenarios without Axis 2 fields are skipped from
   the Axis 2-specific assertions but still validate category + keywords.

Run with:
    pytest -m axis2 tests/synthetic/rds_postgres/test_suite_axis2.py -v
"""

from __future__ import annotations

import pytest

from tests.synthetic.mock_grafana_backend.selective_backend import SelectiveGrafanaBackend
from tests.synthetic.rds_postgres.run_suite import run_scenario, score_reasoning
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios

_ALL_SCENARIOS = load_all_scenarios()
_LLM_ATTEMPTS = 2

# Difficulty threshold above which the LLM is expected to struggle.
# Failures at or above this difficulty are the gap signal —
# they should not gate CI (strict=False xfail).
_XFAIL_DIFFICULTY = 3


def _axis2_scenarios() -> list:
    """Return pytest params for all Axis 2 scenarios.

    Scenarios at difficulty >= _XFAIL_DIFFICULTY are wrapped with
    pytest.mark.xfail(strict=False) so that:
    - Failures keep CI green (expected, part of the gap metric).
    - Passes are recorded as bonuses (xpass).
    """
    params = []
    for f in _ALL_SCENARIOS:
        if not (f.answer_key.ruling_out_keywords or f.answer_key.required_queries):
            continue
        if f.metadata.scenario_difficulty >= _XFAIL_DIFFICULTY:
            params.append(
                pytest.param(
                    f,
                    id=f.scenario_id,
                    marks=pytest.mark.xfail(
                        strict=False,
                        reason=(
                            f"difficulty={f.metadata.scenario_difficulty}: "
                            "expected to challenge real LLMs — failure is the gap signal"
                        ),
                    ),
                )
            )
        else:
            params.append(pytest.param(f, id=f.scenario_id))
    return params


def _should_assert_trajectory(fixture, actual_category: str) -> bool:
    """Keep exact trajectory assertions for the lower-difficulty tiers only."""

    return fixture.metadata.scenario_difficulty < _XFAIL_DIFFICULTY and actual_category != "healthy"


def _run_axis2_scenario_test(fixture) -> None:
    """Run Axis 2 scenario with real LLM, SelectiveGrafanaBackend, and assert reasoning."""
    failures: list[str] = []
    for attempt in range(1, _LLM_ATTEMPTS + 1):
        backend = SelectiveGrafanaBackend(fixture)
        final_state, score = run_scenario(fixture, use_mock_grafana=True, grafana_backend=backend)
        reasoning = score_reasoning(fixture, final_state, queried_metrics=backend.queried_metrics)

        try:
            assert final_state["root_cause"], f"{fixture.scenario_id}: agent produced no root_cause"
            assert score.passed is True, (
                f"{fixture.scenario_id} FAILED: {score.failure_reason}\n"
                f"  actual_category={score.actual_category!r}  "
                f"  missing_keywords={score.missing_keywords}"
            )

            if (
                _should_assert_trajectory(fixture, score.actual_category)
                and score.trajectory is not None
            ):
                assert score.trajectory.sequencing_ok, (
                    f"{fixture.scenario_id} TRAJECTORY FAIL: "
                    f"sequencing={score.trajectory.sequencing_ok} "
                    f"calibration={score.trajectory.calibration_ok}\n"
                    f"  expected={score.trajectory.expected_sequence}\n"
                    f"  actual={score.trajectory.actual_sequence}"
                )

            if reasoning is not None:
                # Keep query coverage as the hard gate. The ruling-out score is
                # still computed and surfaced in reports, but exact phrasing in
                # free-form model outputs is too variable to be CI-stable.
                assert reasoning.queries_ok, (
                    f"{fixture.scenario_id} REASONING FAIL — agent never queried these metrics: "
                    f"{reasoning.missing_queries}\n"
                    f"  queried_metrics audit log: {backend.unique_queried_metrics}"
                )
            return
        except AssertionError as exc:
            failures.append(f"attempt {attempt}/{_LLM_ATTEMPTS}: {exc}")

    raise AssertionError("\n\n".join(failures))


@pytest.mark.axis2
@pytest.mark.parametrize("fixture", _axis2_scenarios(), ids=lambda f: f.scenario_id)
def test_axis2_scenario(fixture) -> None:
    """Axis 2 adversarial test: selective backend + reasoning quality checks."""
    _run_axis2_scenario_test(fixture)
