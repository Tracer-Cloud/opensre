from __future__ import annotations

import pytest

from app.agent.tools.clients import llm_client
from tests.synthetic_testing.rds_postgres.run_suite import run_scenario
from tests.synthetic_testing.rds_postgres.scenario_loader import load_all_scenarios


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


def test_load_all_scenarios_reads_five_benchmark_cases() -> None:
    fixtures = load_all_scenarios()

    assert len(fixtures) == 5
    assert [fixture.scenario_id for fixture in fixtures] == [
        "001-replication-lag",
        "002-connection-exhaustion",
        "003-storage-full",
        "004-cpu-saturation-bad-queries",
        "005-failover",
    ]


@pytest.mark.parametrize("fixture", load_all_scenarios(), ids=lambda fixture: fixture.scenario_id)
def test_run_scenario_scores_expected_rds_answer(monkeypatch: pytest.MonkeyPatch, fixture) -> None:
    responses = {current.scenario_id: current.answer_key.model_response for current in load_all_scenarios()}
    monkeypatch.setattr(llm_client, "_llm", _FakeLLM(responses))

    diagnosis, score = run_scenario(fixture)

    assert diagnosis["root_cause"]
    assert score.actual_category == fixture.answer_key.root_cause_category
    assert score.missing_keywords == []
    assert score.passed is True

    monkeypatch.setattr(llm_client, "_llm", None)
