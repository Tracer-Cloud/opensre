"""Axis 2 adversarial test suite for synthetic Kubernetes RCA scenarios.

Differences from test_suite.py (Axis 1):

1. Uses SelectiveEKSBackend + SelectiveDatadogBackend instead of the
   straight fixture-backed versions.
   - Each selective backend records every tool invocation into an audit log
     so tests can assert the agent called the right methods.

2. Asserts two additional dimensions from ReasoningScore:
   - ruling_out_ok: the agent's output contains all ruling_out_keywords
     declared in the scenario's answer.yml (proves it dismissed alternatives).
   - queries_ok: the agent invoked all required_queries entries (proves it
     checked the right evidence source before concluding).

3. Runs all scenarios with ruling_out_keywords or required_queries declared.
   Scenarios without Axis 2 fields are skipped from the Axis 2-specific
   assertions.  Higher difficulty scenarios are wrapped with xfail(strict=False)
   so expected failures do not gate CI but passes are recorded as xpass.

Run with:
    pytest -m axis2 tests/synthetic/eks/test_suite_axis2.py -v
"""

from __future__ import annotations

import pytest

from tests.synthetic.eks.run_suite import run_scenario, score_reasoning
from tests.synthetic.eks.scenario_loader import load_all_scenarios
from tests.synthetic.mock_datadog_backend.selective_backend import SelectiveDatadogBackend
from tests.synthetic.mock_eks_backend.selective_backend import SelectiveEKSBackend

_ALL_SCENARIOS = load_all_scenarios()

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


def _run_axis2_scenario_test(fixture) -> None:
    """Run Axis 2 scenario with real LLM and selective backends, then assert reasoning."""
    eks_backend = SelectiveEKSBackend(fixture)
    datadog_backend = SelectiveDatadogBackend(fixture)

    final_state, score = run_scenario(
        fixture,
        use_mock_backends=True,
        eks_backend=eks_backend,
        datadog_backend=datadog_backend,
    )

    assert final_state["root_cause"], f"{fixture.scenario_id}: agent produced no root_cause"
    assert score.passed is True, (
        f"{fixture.scenario_id} FAILED: {score.failure_reason}\n"
        f"  actual_category={score.actual_category!r}  "
        f"  missing_keywords={score.missing_keywords}"
    )

    if score.trajectory is not None:
        assert score.trajectory.efficiency_score >= 1.0, (
            f"{fixture.scenario_id} TRAJECTORY FAIL: "
            f"sequencing={score.trajectory.sequencing_ok} "
            f"calibration={score.trajectory.calibration_ok}\n"
            f"  expected={score.trajectory.expected_sequence}\n"
            f"  actual={score.trajectory.actual_sequence}"
        )

    queried_tools = list(eks_backend.queried_tools) + list(datadog_backend.queried_tools)
    reasoning = score_reasoning(fixture, final_state, queried_tools=queried_tools)

    if reasoning is not None:
        assert reasoning.ruling_out_ok, (
            f"{fixture.scenario_id} REASONING FAIL — missing ruling-out tokens: "
            f"{reasoning.missing_ruling_out}\n"
            f"  (agent must mention these to demonstrate it considered and dismissed alternatives)"
        )
        assert reasoning.queries_ok, (
            f"{fixture.scenario_id} REASONING FAIL — agent never invoked: "
            f"{reasoning.missing_queries}\n"
            f"  queried_tools audit log: "
            f"eks={sorted(eks_backend.unique_queried_tools)} "
            f"datadog={sorted(datadog_backend.unique_queried_tools)}"
        )


_AXIS2_PARAMS = _axis2_scenarios()


@pytest.mark.axis2
@pytest.mark.skipif(not _AXIS2_PARAMS, reason="no Axis 2 K8s scenarios yet")
@pytest.mark.parametrize("fixture", _AXIS2_PARAMS or [None])
def test_axis2_scenario(fixture) -> None:
    """Axis 2 adversarial test: selective backends + reasoning quality checks."""
    _run_axis2_scenario_test(fixture)
