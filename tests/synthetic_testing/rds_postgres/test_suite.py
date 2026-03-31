from __future__ import annotations

import pytest

from app.agent.tools.clients import llm_client
from tests.synthetic_testing.rds_postgres.run_suite import run_scenario
from tests.synthetic_testing.rds_postgres.scenario_loader import load_all_scenarios
from tests.synthetic_testing.schemas import VALID_EVIDENCE_SOURCES


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def with_config(self, **_kwargs) -> _FakeLLM:
        return self

    def invoke(self, prompt: str) -> _FakeLLMResponse:
        for scenario_id, response in self._responses.items():
            if scenario_id in prompt:
                return _FakeLLMResponse(response)
        raise AssertionError("Scenario id not found in diagnosis prompt")


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
    responses = {current.scenario_id: current.answer_key.model_response for current in load_all_scenarios()}
    monkeypatch.setattr(llm_client, "_llm", _FakeLLM(responses))

    diagnosis, score = run_scenario(fixture)

    assert diagnosis["root_cause"]
    assert score.actual_category == fixture.answer_key.root_cause_category
    assert score.missing_keywords == []
    assert score.passed is True

    monkeypatch.setattr(llm_client, "_llm", None)
