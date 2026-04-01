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

from app.agent.tools.clients import llm_client
from tests.synthetic.mock_grafana_backend.selective_backend import SelectiveGrafanaBackend
from tests.synthetic.rds_postgres.run_suite import run_scenario, score_reasoning
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios

_DEFAULT_PLANNING_ACTIONS = [
    "query_grafana_logs",
    "query_grafana_metrics",
    "query_grafana_alert_rules",
]


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStructuredLLM:
    """Fake structured output client for node_plan_actions.

    Same semantics as in test_suite.py — raises for AlertDetails so
    _fallback_details fires, returns _Plan for InvestigationPlan.
    """

    def __init__(self, model: object, planning_actions: list[str]) -> None:
        self._model = model
        self._planning_actions = planning_actions

    def with_config(self, **_kwargs) -> _FakeStructuredLLM:
        return self

    def invoke(self, _prompt: str) -> object:
        model_name = getattr(self._model, "__name__", "")
        if model_name == "AlertDetails":
            raise ValueError("Fake LLM: use _fallback_details for alert extraction")

        planning_actions = self._planning_actions

        class _Plan:
            actions = planning_actions
            rationale = "Axis 2 fake plan"

        return _Plan()


class _FakeLLM:
    def __init__(self, responses: dict[str, str], planning_actions: list[str]) -> None:
        self._responses = responses
        self._planning_actions = planning_actions

    def with_config(self, **_kwargs) -> _FakeLLM:
        return self

    def with_structured_output(self, model: object) -> _FakeStructuredLLM:
        return _FakeStructuredLLM(model, self._planning_actions)

    def invoke(self, prompt: str) -> _FakeLLMResponse:
        for key, response in self._responses.items():
            if key and key in prompt:
                return _FakeLLMResponse(response)
        if self._responses:
            return _FakeLLMResponse(next(iter(self._responses.values())))
        raise AssertionError("No responses configured in _FakeLLM")


_ALL_SCENARIOS = load_all_scenarios()

# Difficulty threshold above which a real LLM is expected to fail.
# The fake-LLM infrastructure tests always pass regardless.
# With a real LLM, failures at or above this difficulty are the gap signal —
# they should not gate CI (strict=False xfail).
_XFAIL_DIFFICULTY = 3


def _axis2_scenarios() -> list:
    """Return pytest params for all Axis 2 scenarios.

    Scenarios at difficulty >= _XFAIL_DIFFICULTY are wrapped with
    pytest.mark.xfail(strict=False) so that:
    - Failures with a real LLM keep CI green (expected, part of the gap metric).
    - Passes with a real LLM are recorded as bonuses (xpass).
    - The fake-LLM infrastructure tests always xpass (fine with strict=False).
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


def _run_axis2_scenario_test(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Core Axis 2 test logic: wire SelectiveGrafanaBackend + fake LLM, assert reasoning."""
    responses: dict[str, str] = {}
    for current in _ALL_SCENARIOS:
        responses[current.scenario_id] = current.answer_key.model_response
        title = str(current.alert.get("title", ""))
        if title:
            responses[title] = current.answer_key.model_response

    planning_actions = list(fixture.answer_key.optimal_trajectory) or _DEFAULT_PLANNING_ACTIONS
    fake_llm = _FakeLLM(responses, planning_actions)
    monkeypatch.setattr(llm_client, "_llm", fake_llm)
    monkeypatch.setattr(llm_client, "_llm_for_tools", fake_llm)

    backend = SelectiveGrafanaBackend(fixture)

    final_state, score = run_scenario(fixture, use_mock_grafana=True, grafana_backend=backend)

    # --- Standard Axis 1 assertions ---
    assert final_state["root_cause"], (
        f"{fixture.scenario_id}: agent produced no root_cause"
    )
    assert score.passed is True, (
        f"{fixture.scenario_id} FAILED: {score.failure_reason}\n"
        f"  actual_category={score.actual_category!r}  "
        f"  missing_keywords={score.missing_keywords}"
    )

    # --- Axis 2: trajectory ---
    if score.trajectory is not None:
        assert score.trajectory.efficiency_score >= 1.0, (
            f"{fixture.scenario_id} TRAJECTORY FAIL: "
            f"sequencing={score.trajectory.sequencing_ok} "
            f"calibration={score.trajectory.calibration_ok}\n"
            f"  expected={score.trajectory.expected_sequence}\n"
            f"  actual={score.trajectory.actual_sequence}"
        )

    # --- Axis 2: reasoning quality ---
    # Re-score with the backend's audit log so required_queries is checked.
    reasoning = score_reasoning(fixture, final_state, queried_metrics=backend.queried_metrics)

    if reasoning is not None:
        assert reasoning.ruling_out_ok, (
            f"{fixture.scenario_id} REASONING FAIL — missing ruling-out tokens: "
            f"{reasoning.missing_ruling_out}\n"
            f"  (agent must mention these to demonstrate it considered and dismissed alternatives)"
        )
        assert reasoning.queries_ok, (
            f"{fixture.scenario_id} REASONING FAIL — agent never queried these metrics: "
            f"{reasoning.missing_queries}\n"
            f"  queried_metrics audit log: {backend.unique_queried_metrics}"
        )

    monkeypatch.setattr(llm_client, "_llm", None)
    monkeypatch.setattr(llm_client, "_llm_for_tools", None)


@pytest.mark.axis2
@pytest.mark.parametrize("fixture", _axis2_scenarios(), ids=lambda f: f.scenario_id)
def test_axis2_scenario(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Axis 2 adversarial test: selective backend + reasoning quality checks."""
    _run_axis2_scenario_test(monkeypatch, fixture)
