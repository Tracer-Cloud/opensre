from __future__ import annotations

import pytest

from app.agent.tools.clients import llm_client
from tests.synthetic_testing.rds_postgres.run_suite import run_scenario
from tests.synthetic_testing.rds_postgres.scenario_loader import load_all_scenarios
from tests.synthetic_testing.schemas import VALID_EVIDENCE_SOURCES


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStructuredLLM:
    """Fake structured output client used by node_plan_actions.

    For alert extraction (AlertDetails), we intentionally raise so the production
    code's except-clause fires _fallback_details — which correctly reads the alert_name
    from state (set by make_initial_state) and puts the real alert title into problem_md.
    """

    def __init__(self, model: object) -> None:
        self._model = model

    def with_config(self, **_kwargs) -> _FakeStructuredLLM:
        return self

    def invoke(self, _prompt: str) -> object:
        model_name = getattr(self._model, "__name__", "")
        if model_name == "AlertDetails":
            # Let node_extract_alert fall back to _fallback_details so that the real
            # alert_name from state populates problem_md (needed for _FakeLLM matching).
            raise ValueError("Fake LLM: use _fallback_details for alert extraction")

        # For InvestigationPlan: return grafana actions so mock evidence is gathered.
        class _Plan:
            actions = ["query_grafana_logs", "query_grafana_metrics", "query_grafana_alert_rules"]
            rationale = "Fake plan for synthetic test"

        return _Plan()


class _FakeLLM:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def with_config(self, **_kwargs) -> _FakeLLM:
        return self

    def with_structured_output(self, model: object) -> _FakeStructuredLLM:
        return _FakeStructuredLLM(model)

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


_FAULT_SCENARIOS = [f for f in load_all_scenarios() if f.metadata.failure_mode != "healthy"]


@pytest.mark.parametrize("fixture", _FAULT_SCENARIOS, ids=lambda fixture: fixture.scenario_id)
def test_run_scenario_scores_expected_rds_answer(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    # Key responses by scenario_id AND alert title so the fake LLM can match on whichever
    # identifier ends up in the diagnosis prompt (depends on whether the full pipeline runs
    # through node_extract_alert or uses the scenario's own problem_md).
    responses: dict[str, str] = {}
    for current in load_all_scenarios():
        responses[current.scenario_id] = current.answer_key.model_response
        title = str(current.alert.get("title", ""))
        if title:
            responses[title] = current.answer_key.model_response
    monkeypatch.setattr(llm_client, "_llm", _FakeLLM(responses))

    # use_mock_grafana=True runs the full pipeline: plan → investigate (mock backend) → diagnose.
    final_state, score = run_scenario(fixture, use_mock_grafana=True)

    assert final_state["root_cause"]
    assert score.actual_category == fixture.answer_key.root_cause_category
    assert score.missing_keywords == []
    assert score.passed is True

    monkeypatch.setattr(llm_client, "_llm", None)
