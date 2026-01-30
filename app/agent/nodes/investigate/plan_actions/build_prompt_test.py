"""Tests for investigation prompt construction."""

from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import MagicMock

from pydantic import BaseModel

from app.agent.nodes.investigate.plan_actions.build_prompt import (
    _build_available_sources_hint,
    _format_action_metadata,
    _get_executed_sources,
    build_investigation_prompt,
    plan_actions_with_llm,
    select_actions,
)
from app.agent.state import EvidenceSource


@dataclass
class MockAction:
    """Mock action for testing."""

    name: str
    description: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    use_cases: list[str]
    source: EvidenceSource
    availability_check: Callable[[dict[str, dict]], bool] | None = None


class MockPlanModel(BaseModel):
    """Mock plan model for testing."""

    actions: list[str]
    rationale: str


def test_get_executed_sources():
    """Test extracting sources from hypotheses."""
    executed_hypotheses = [
        {"sources": ["cloudwatch", "s3"]},
        {"source": "tracer_web"},
    ]

    sources = _get_executed_sources(executed_hypotheses)

    assert sources == {"cloudwatch", "s3", "tracer_web"}


def test_get_executed_sources_empty():
    """Test extracting sources from empty hypotheses."""
    assert _get_executed_sources([]) == set()


def test_build_available_sources_hint():
    """Test building hint for available sources."""
    available_sources = {
        "cloudwatch": {"log_group": "/aws/batch/job", "log_stream": "job-12345"},
        "s3": {"bucket": "my-bucket"},
    }

    hint = _build_available_sources_hint(available_sources)

    assert "CloudWatch Logs Available" in hint
    assert "S3 Storage Available" in hint


def test_build_available_sources_hint_empty():
    """Test building hint with no sources."""
    assert _build_available_sources_hint({}) == ""


def test_format_action_metadata():
    """Test formatting action metadata."""
    action = MockAction(
        name="get_logs",
        description="Get logs",
        inputs={"log_file": "Path to log file"},
        outputs={"logs": "Log entries"},
        use_cases=["When debugging"],
        source="cloudwatch",
    )

    formatted = _format_action_metadata(action)

    assert "Action: get_logs" in formatted
    assert "Description: Get logs" in formatted


def test_build_investigation_prompt():
    """Test building investigation prompt."""
    available_actions = [
        MockAction(
            name="get_logs",
            description="Get logs",
            inputs={},
            outputs={},
            use_cases=[],
            source="cloudwatch",
        )
    ]

    prompt = build_investigation_prompt(
        problem_md="Pipeline failed",
        investigation_recommendations=["Check logs"],
        executed_hypotheses=[],
        available_actions=available_actions,
        available_sources={"cloudwatch": {}},
    )

    assert "Pipeline failed" in prompt
    assert "Action: get_logs" in prompt


def test_build_investigation_prompt_filters_executed():
    """Test prompt filters out executed actions."""
    executed_hypotheses = [{"sources": ["cloudwatch"], "actions": ["get_logs"]}]
    available_actions = [
        MockAction(name="get_logs", description="", inputs={}, outputs={}, use_cases=[], source="cloudwatch"),
        MockAction(name="check_s3", description="", inputs={}, outputs={}, use_cases=[], source="storage"),
    ]

    prompt = build_investigation_prompt(
        problem_md="Pipeline failed",
        investigation_recommendations=[],
        executed_hypotheses=executed_hypotheses,
        available_actions=available_actions,
        available_sources={},
    )

    assert "Action: check_s3" in prompt
    assert "Action: get_logs" not in prompt


def test_select_actions():
    """Test selecting actions based on availability."""
    def cloudwatch_available(sources: dict[str, dict]) -> bool:
        return "cloudwatch" in sources

    actions = [
        MockAction(
            name="cloudwatch_action",
            description="",
            inputs={},
            outputs={},
            use_cases=[],
            source="cloudwatch",
            availability_check=cloudwatch_available,
        ),
        MockAction(
            name="s3_action",
            description="",
            inputs={},
            outputs={},
            use_cases=[],
            source="storage",
            availability_check=lambda s: "s3" in s,
        ),
    ]

    selected_actions, action_names = select_actions(
        actions=actions,
        available_sources={"cloudwatch": {"log_group": "/aws/batch/job"}},
        executed_hypotheses=[],
    )

    assert len(selected_actions) == 1
    assert selected_actions[0].name == "cloudwatch_action"


def test_select_actions_filters_executed():
    """Test selecting actions filters executed ones."""
    actions = [
        MockAction(name="action1", description="", inputs={}, outputs={}, use_cases=[], source="cloudwatch"),
        MockAction(name="action2", description="", inputs={}, outputs={}, use_cases=[], source="storage"),
    ]

    selected_actions, _ = select_actions(
        actions=actions,
        available_sources={},
        executed_hypotheses=[{"actions": ["action1"]}],
    )

    assert len(selected_actions) == 1
    assert selected_actions[0].name == "action2"


def test_plan_actions_with_llm():
    """Test planning actions with LLM."""
    mock_llm = MagicMock()
    mock_invoke = MagicMock(return_value=MockPlanModel(actions=["action1"], rationale="Test"))

    mock_llm.with_structured_output.return_value.with_config.return_value.invoke = mock_invoke

    result = plan_actions_with_llm(
        llm=mock_llm,
        plan_model=MockPlanModel,
        problem_md="Pipeline failed",
        investigation_recommendations=[],
        executed_hypotheses=[],
        available_actions=[],
        available_sources={},
    )

    assert isinstance(result, MockPlanModel)
    assert result.actions == ["action1"]
    mock_invoke.assert_called_once()
