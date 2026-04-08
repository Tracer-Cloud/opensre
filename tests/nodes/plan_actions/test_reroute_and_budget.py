"""Tests for rerouting and tool budget enforcement."""

from app.nodes.investigate.processing.post_process import track_hypothesis
from app.nodes.plan_actions.build_prompt import apply_tool_budget, select_actions
from app.nodes.plan_actions.plan_actions import detect_reroute_trigger


class MockAction:
    """Mock action for testing."""

    def __init__(self, name: str, source: str = "test"):
        self.name = name
        self.source = source
        self.use_cases = [f"test_{name}"]

    def is_available(self, _sources: dict) -> bool:
        return True


def test_apply_tool_budget_within_budget():
    """Test that budget doesn't truncate when under limit."""
    actions = [MockAction(f"action_{i}") for i in range(5)]
    result = apply_tool_budget(actions, 10)
    assert len(result) == 5


def test_apply_tool_budget_truncates():
    """Test that budget truncates actions exceeding limit."""
    actions = [MockAction(f"action_{i}") for i in range(15)]
    result = apply_tool_budget(actions, 10)
    assert len(result) == 10
    assert result[0].name == "action_0"
    assert result[9].name == "action_9"


def test_select_actions_applies_budget():
    """Test that select_actions respects tool_budget parameter."""
    actions = [MockAction(f"action_{i}") for i in range(15)]
    available_sources = {"test": {}}
    executed_hypotheses = []

    # With budget of 5, should only get 5 actions
    available, names = select_actions(
        actions, available_sources, executed_hypotheses, tool_budget=5
    )
    assert len(available) == 5
    assert len(names) == 5


def test_select_actions_default_budget():
    """Test that select_actions uses default budget of 10."""
    actions = [MockAction(f"action_{i}") for i in range(20)]
    available_sources = {"test": {}}
    executed_hypotheses = []

    # With default budget (10), should get 10 actions
    available, names = select_actions(actions, available_sources, executed_hypotheses)
    assert len(available) == 10
    assert len(names) == 10


def test_detect_reroute_trigger_s3_audit_discovery():
    """Test rerouting triggers when s3_audit source discovered but not used."""
    evidence = {}
    available_sources = {"s3_audit": {"bucket": "test", "key": "audit.json"}}
    executed_hypotheses = []

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    assert rerouted is True
    assert "s3_audit" in reason


def test_detect_reroute_trigger_no_reroute_after_audit_used():
    """Test no rerouting after audit already executed."""
    evidence = {}
    available_sources = {"s3_audit": {"bucket": "test", "key": "audit.json"}}
    executed_hypotheses = [{"actions": ["get_s3_object"], "loop_count": 0}]

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    assert rerouted is False
    assert reason == ""


def test_detect_reroute_trigger_grafana_service_discovery():
    """Test rerouting triggers when grafana service names discovered but logs not fetched."""
    evidence = {"grafana_service_names": ["service-1", "service-2"]}
    available_sources = {}
    executed_hypotheses = []

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    assert rerouted is True
    assert "grafana" in reason.lower()


def test_detect_reroute_trigger_no_reroute_when_logs_already_fetched():
    """Test no rerouting when grafana logs already fetched."""
    evidence = {"grafana_service_names": ["service-1"], "grafana_logs": ["log1"]}
    available_sources = {}
    executed_hypotheses = [{"actions": ["query_grafana_logs"], "loop_count": 0}]

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    # Should not trigger because logs already exist
    assert rerouted is False


def test_detect_reroute_no_reroute_when_grafana_query_ran_but_empty():
    """Grafana query ran, returned no logs - should not re-trigger reroute."""
    evidence = {
        "grafana_service_names": ["service-1"],
        "grafana_logs": [],
    }
    available_sources = {}
    executed_hypotheses = [{"actions": ["query_grafana_logs"], "loop_count": 0}]

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    assert rerouted is False
    assert reason == ""


def test_detect_reroute_trigger_vendor_audit_discovery():
    """Vendor audit evidence should trigger reroute even if s3 audit was previously used."""
    evidence = {"vendor_audit_from_logs": {"api": "stripe", "status": 500}}
    available_sources = {}
    executed_hypotheses = [{"actions": ["get_s3_object"], "loop_count": 0}]

    rerouted, reason = detect_reroute_trigger(evidence, available_sources, executed_hypotheses)
    assert rerouted is True
    assert "vendor" in reason.lower()


def test_track_hypothesis_with_audit():
    """Test that track_hypothesis records audit data."""
    executed_hypotheses = []
    plan_audit = {
        "loop": 1,
        "tool_budget": 10,
        "planned_count": 3,
        "rerouted": True,
        "reroute_reason": "Test reroute",
    }

    result = track_hypothesis(
        executed_hypotheses,
        ["action1", "action2"],
        "Test rationale",
        1,
        plan_audit,
    )

    assert len(result) == 1
    assert result[0]["actions"] == ["action1", "action2"]
    assert result[0]["rationale"] == "Test rationale"
    assert result[0]["loop_count"] == 1
    assert "audit" in result[0]
    assert result[0]["audit"]["rerouted"] is True


def test_track_hypothesis_without_audit():
    """Test that track_hypothesis works without audit data."""
    executed_hypotheses = []

    result = track_hypothesis(
        executed_hypotheses,
        ["action1"],
        "Test rationale",
        0,
    )

    assert len(result) == 1
    assert result[0]["actions"] == ["action1"]
    assert "audit" not in result[0]
