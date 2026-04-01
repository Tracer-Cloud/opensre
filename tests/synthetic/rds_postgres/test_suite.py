from __future__ import annotations

import pytest

from app.agent.tools.clients import llm_client
from tests.synthetic.rds_postgres.run_suite import run_scenario
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios
from tests.synthetic.schemas import VALID_EVIDENCE_SOURCES

_DEFAULT_PLANNING_ACTIONS = [
    "query_grafana_logs",
    "query_grafana_metrics",
    "query_grafana_alert_rules",
]


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStructuredLLM:
    """Fake structured output client used by node_plan_actions.

    For alert extraction (AlertDetails), we intentionally raise so the production
    code's except-clause fires _fallback_details — which correctly reads the alert_name
    from state (set by make_initial_state) and puts the real alert title into problem_md.
    """

    def __init__(self, model: object, planning_actions: list[str]) -> None:
        self._model = model
        self._planning_actions = planning_actions

    def with_config(self, **_kwargs) -> _FakeStructuredLLM:
        return self

    def invoke(self, _prompt: str) -> object:
        model_name = getattr(self._model, "__name__", "")
        if model_name == "AlertDetails":
            # Let node_extract_alert fall back to _fallback_details so that the real
            # alert_name from state populates problem_md (needed for _FakeLLM matching).
            raise ValueError("Fake LLM: use _fallback_details for alert extraction")

        # For InvestigationPlan: use per-scenario planning actions (from optimal_trajectory).
        planning_actions = self._planning_actions

        class _Plan:
            actions = planning_actions
            rationale = "Fake plan for synthetic test"

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
        # Match on any key that appears in the prompt (covers scenario_id and alert title).
        for key, response in self._responses.items():
            if key and key in prompt:
                return _FakeLLMResponse(response)
        # Fallback: return the first response when nothing matches (shouldn't happen in practice).
        if self._responses:
            return _FakeLLMResponse(next(iter(self._responses.values())))
        raise AssertionError("No responses configured in _FakeLLM")


def test_load_all_scenarios_reads_benchmark_cases() -> None:
    fixtures = load_all_scenarios()

    scenario_ids = [fixture.scenario_id for fixture in fixtures]
    assert "000-healthy" in scenario_ids
    assert "001-replication-lag" in scenario_ids
    assert "002-connection-exhaustion" in scenario_ids


def test_scenario_metadata_is_valid() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        meta = fixture.metadata
        assert meta.schema_version, f"{fixture.scenario_id}: schema_version must be set"
        assert meta.engine, f"{fixture.scenario_id}: engine must be set"
        assert meta.failure_mode, f"{fixture.scenario_id}: failure_mode must be set"
        assert meta.region, f"{fixture.scenario_id}: region must be set"
        assert meta.available_evidence, f"{fixture.scenario_id}: available_evidence must not be empty"
        unknown = set(meta.available_evidence) - VALID_EVIDENCE_SOURCES
        assert not unknown, f"{fixture.scenario_id}: unknown evidence sources {unknown}"


def test_scenario_evidence_matches_available_evidence() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        evidence_dict = fixture.evidence.as_dict()
        assert set(evidence_dict.keys()) == set(fixture.metadata.available_evidence), (
            f"{fixture.scenario_id}: evidence keys {set(evidence_dict.keys())} "
            f"do not match available_evidence {fixture.metadata.available_evidence}"
        )


_ALL_SCENARIOS = load_all_scenarios()


def _by_difficulty(level: int) -> list:
    return [f for f in _ALL_SCENARIOS if f.metadata.scenario_difficulty == level]


def _run_scenario_test(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Shared core logic: wire fake LLM and assert the scenario passes scoring."""
    # Key responses by scenario_id AND alert title so the fake LLM can match on whichever
    # identifier ends up in the diagnosis prompt (depends on whether the full pipeline runs
    # through node_extract_alert or uses the scenario's own problem_md).
    responses: dict[str, str] = {}
    for current in load_all_scenarios():
        responses[current.scenario_id] = current.answer_key.model_response
        title = str(current.alert.get("title", ""))
        if title:
            responses[title] = current.answer_key.model_response

    # Use the scenario's declared optimal_trajectory as the fake LLM's plan so that the
    # trajectory score captures exactly what each scenario expects from the agent.
    planning_actions = list(fixture.answer_key.optimal_trajectory) or _DEFAULT_PLANNING_ACTIONS
    fake_llm = _FakeLLM(responses, planning_actions)
    monkeypatch.setattr(llm_client, "_llm", fake_llm)
    monkeypatch.setattr(llm_client, "_llm_for_tools", fake_llm)

    # use_mock_grafana=True runs the full pipeline: plan → investigate (mock backend) → diagnose.
    final_state, score = run_scenario(fixture, use_mock_grafana=True)

    assert final_state["root_cause"]
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

    monkeypatch.setattr(llm_client, "_llm", None)
    monkeypatch.setattr(llm_client, "_llm_for_tools", None)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(1), ids=lambda f: f.scenario_id)
def test_level1_scenario(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Level 1 — single dominant signal, all evidence consistent."""
    _run_scenario_test(monkeypatch, fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(2), ids=lambda f: f.scenario_id)
def test_level2_scenario(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Level 2 — one confounder present, second evidence source needed to rule it out."""
    _run_scenario_test(monkeypatch, fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(3), ids=lambda f: f.scenario_id)
def test_level3_scenario(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Level 3 — absent or indirect evidence, key metric missing."""
    _run_scenario_test(monkeypatch, fixture)


@pytest.mark.synthetic
@pytest.mark.parametrize("fixture", _by_difficulty(4), ids=lambda f: f.scenario_id)
def test_level4_scenario(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    """Level 4 — compositional fault, two failure modes causally linked."""
    _run_scenario_test(monkeypatch, fixture)
