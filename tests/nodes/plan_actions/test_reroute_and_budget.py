"""Tests for rerouting and tool budget enforcement."""

import importlib

from pydantic import BaseModel

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.models import InvestigateInput
from app.nodes.investigate.processing.post_process import (
    summarize_execution_results,
    track_hypothesis,
)
from app.nodes.plan_actions.build_prompt import apply_tool_budget, select_actions
from app.nodes.plan_actions.plan_actions import (
    _ensure_seed_actions_available,
    _seed_plan_actions,
    detect_reroute_trigger,
    plan_actions,
)
from app.tools.investigation_registry.prioritization import DETERMINISTIC_FALLBACK_REASON

plan_actions_module = importlib.import_module("app.nodes.plan_actions.plan_actions")


class MockAction:
    """Mock action for testing."""

    def __init__(self, name: str, source: str = "test"):
        self.name = name
        self.source = source
        self.use_cases = [f"test_{name}"]

    def is_available(self, _sources: dict) -> bool:
        return True


class MockPlan(BaseModel):
    actions: list[str]
    rationale: str


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


def test_detect_reroute_no_reroute_when_grafana_query_exhausted():
    """An exhausted Grafana log query should not keep triggering reroutes."""
    evidence = {
        "grafana_service_names": ["service-1"],
        "grafana_logs": [],
    }
    available_sources = {}
    executed_hypotheses = [{"actions": [], "exhausted_actions": ["query_grafana_logs"]}]

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


def test_seed_plan_actions_prepends_openclaw_search():
    seeded = _seed_plan_actions(
        planned_actions=["query_datadog_logs", "search_openclaw_conversations"],
        available_action_names=["search_openclaw_conversations", "query_datadog_logs"],
        available_sources={"openclaw": {"connection_verified": True}},
    )

    assert seeded[0] == "search_openclaw_conversations"
    assert seeded[1] == "query_datadog_logs"


def test_seed_plan_actions_keeps_s3_audit_first():
    seeded = _seed_plan_actions(
        planned_actions=["query_datadog_logs"],
        available_action_names=["get_s3_object", "query_datadog_logs"],
        available_sources={"s3_audit": {"bucket": "b", "key": "k"}},
    )

    assert seeded[0] == "get_s3_object"


def test_seed_plan_actions_filters_unavailable_actions():
    seeded = _seed_plan_actions(
        planned_actions=["query_grafana_logs", "query_grafana_alert_rules"],
        available_action_names=["query_grafana_alert_rules"],
        available_sources={},
    )

    assert seeded == ["query_grafana_alert_rules"]


def test_plan_actions_falls_back_when_planner_returns_only_executed_action(monkeypatch):
    actions = [
        MockAction("query_grafana_logs", "grafana"),
        MockAction("query_grafana_alert_rules", "grafana"),
    ]

    def _mock_detect_sources(raw_alert, context, resolved_integrations=None):
        _ = (raw_alert, context, resolved_integrations)
        return {"grafana": {"connection_verified": True}}

    def _mock_get_available_actions():
        return actions

    def _mock_get_prioritized_actions_with_reasons(sources=None, keywords=None):
        _ = (sources, keywords)
        return actions, []

    def _mock_plan_actions_with_llm(**kwargs):
        return kwargs["plan_model"](
            actions=["query_grafana_logs"],
            rationale="Mocked planner repeated a previous action",
        )

    monkeypatch.setattr(plan_actions_module, "detect_sources", _mock_detect_sources)
    monkeypatch.setattr(plan_actions_module, "get_available_actions", _mock_get_available_actions)
    monkeypatch.setattr(
        plan_actions_module,
        "get_prioritized_actions_with_reasons",
        _mock_get_prioritized_actions_with_reasons,
    )
    monkeypatch.setattr(plan_actions_module, "get_llm_for_tools", object)
    monkeypatch.setattr(
        plan_actions_module,
        "plan_actions_with_llm",
        _mock_plan_actions_with_llm,
    )

    input_data = InvestigateInput(
        raw_alert={"alert_name": "RDS write latency critical"},
        context={},
        problem_md="# RDS write latency critical",
        alert_name="RDSWriteLatencyCritical",
        executed_hypotheses=[{"actions": ["query_grafana_logs"], "loop_count": 0}],
        tool_budget=10,
    )

    plan, *_ = plan_actions(
        input_data=input_data,
        plan_model=MockPlan,
        resolved_integrations={"grafana": {"connection_verified": True}},
    )

    assert plan is not None
    assert plan.actions == ["query_grafana_alert_rules"]
    assert "already-executed" in plan.rationale


def test_ensure_seed_actions_available_inserts_openclaw_action():
    selected, names = _ensure_seed_actions_available(
        available_actions=[MockAction("query_datadog_logs", "datadog")],
        action_pool=[
            MockAction("query_datadog_logs", "datadog"),
            MockAction("search_openclaw_conversations", "openclaw"),
            MockAction("list_openclaw_tools", "openclaw"),
        ],
        available_sources={"openclaw": {"connection_verified": True}},
        tool_budget=5,
        executed_hypotheses=[],
    )

    assert selected[0].name == "search_openclaw_conversations"
    assert names[0] == "search_openclaw_conversations"
    assert selected[1].name == "list_openclaw_tools"


def test_ensure_seed_actions_available_skips_previously_attempted_openclaw_actions():
    selected, names = _ensure_seed_actions_available(
        available_actions=[MockAction("query_datadog_logs", "datadog")],
        action_pool=[
            MockAction("query_datadog_logs", "datadog"),
            MockAction("search_openclaw_conversations", "openclaw"),
            MockAction("list_openclaw_tools", "openclaw"),
        ],
        available_sources={"openclaw": {"connection_verified": True}},
        tool_budget=5,
        executed_hypotheses=[
            {
                "actions": ["search_openclaw_conversations", "list_openclaw_tools"],
                "loop_count": 0,
            }
        ],
    )

    assert [action.name for action in selected] == ["query_datadog_logs"]
    assert names == ["query_datadog_logs"]


def test_plan_actions_keeps_openclaw_seeded_when_budget_is_full(monkeypatch):
    actions = [MockAction(f"action_{i}", "datadog") for i in range(10)]
    actions.append(MockAction("search_openclaw_conversations", "openclaw"))
    actions.append(MockAction("list_openclaw_tools", "openclaw"))

    def _mock_get_available_actions():
        return actions

    def _mock_get_prioritized_actions_with_reasons(sources=None, keywords=None):
        _ = (sources, keywords)
        return actions, []

    def _mock_plan_actions_with_llm(**kwargs):
        return kwargs["plan_model"](
            actions=["action_0", "action_1"],
            rationale="Mocked planner output",
        )

    monkeypatch.setattr(plan_actions_module, "get_available_actions", _mock_get_available_actions)
    monkeypatch.setattr(
        plan_actions_module,
        "get_prioritized_actions_with_reasons",
        _mock_get_prioritized_actions_with_reasons,
    )
    monkeypatch.setattr(plan_actions_module, "get_llm_for_tools", object)
    monkeypatch.setattr(
        plan_actions_module,
        "plan_actions_with_llm",
        _mock_plan_actions_with_llm,
    )

    input_data = InvestigateInput(
        raw_alert={"alert_name": "Checkout API error rate spike", "service": "checkout-api"},
        context={},
        problem_md="# Checkout API error rate spike",
        alert_name="Checkout API error rate spike",
        tool_budget=10,
    )

    (
        plan,
        available_sources,
        available_action_names,
        available_actions,
        rerouted,
        reroute_reason,
        inclusion_reasons,
    ) = plan_actions(
        input_data=input_data,
        plan_model=MockPlan,
        resolved_integrations={
            "openclaw": {
                "mode": "stdio",
                "command": "openclaw",
                "args": ["mcp", "serve"],
            }
        },
    )

    assert plan is not None
    assert "openclaw" in available_sources
    assert available_action_names[0] == "search_openclaw_conversations"
    assert available_action_names[1] == "list_openclaw_tools"
    assert available_actions[0].name == "search_openclaw_conversations"
    assert plan.actions[0] == "search_openclaw_conversations"
    assert rerouted is False
    assert reroute_reason == ""
    assert inclusion_reasons == []


def test_plan_actions_keeps_deterministic_fallback_when_budget_is_full(monkeypatch):
    actions = [MockAction(f"action_{i}", "datadog") for i in range(10)]
    actions.append(MockAction("get_sre_guidance", "knowledge"))

    def _mock_get_available_actions():
        return actions

    def _mock_get_prioritized_actions_with_reasons(sources=None, keywords=None):
        _ = (sources, keywords)
        return actions, [
            {
                "name": action.name,
                "score": 0,
                "reasons": [DETERMINISTIC_FALLBACK_REASON]
                if action.name == "get_sre_guidance"
                else ["no source or keyword match"],
                "source": action.source,
                "tags": [],
            }
            for action in actions
        ]

    def _mock_plan_actions_with_llm(**kwargs):
        return kwargs["plan_model"](
            actions=["action_0", "action_1"],
            rationale="Mocked planner output",
        )

    monkeypatch.setattr(plan_actions_module, "get_available_actions", _mock_get_available_actions)
    monkeypatch.setattr(
        plan_actions_module,
        "get_prioritized_actions_with_reasons",
        _mock_get_prioritized_actions_with_reasons,
    )
    monkeypatch.setattr(plan_actions_module, "get_llm_for_tools", object)
    monkeypatch.setattr(
        plan_actions_module,
        "plan_actions_with_llm",
        _mock_plan_actions_with_llm,
    )

    input_data = InvestigateInput(
        raw_alert={"alert_name": "Checkout API error rate spike", "service": "checkout-api"},
        context={},
        problem_md="# Checkout API error rate spike",
        alert_name="Checkout API error rate spike",
        tool_budget=10,
    )

    (
        plan,
        _available_sources,
        available_action_names,
        available_actions,
        _rerouted,
        _reroute_reason,
        _inclusion_reasons,
    ) = plan_actions(
        input_data=input_data,
        plan_model=MockPlan,
        resolved_integrations={"datadog": {"api_key": "test"}},
    )

    assert plan is not None
    assert available_action_names[0] == "get_sre_guidance"
    assert available_actions[0].name == "get_sre_guidance"


def test_summarize_execution_results_tracks_retryable_failure_without_exhausting():
    """Retryable failures are audited but remain available before the retry limit."""
    execution_results = {
        "search_openclaw_conversations": ActionExecutionResult(
            action_name="search_openclaw_conversations",
            success=False,
            data={},
            error="Connection closed",
        )
    }

    evidence, executed_hypotheses, evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence={},
        executed_hypotheses=[],
        investigation_loop_count=0,
        rationale="Try OpenClaw first",
        plan_audit={},
    )

    assert evidence == {}
    assert executed_hypotheses[0]["actions"] == []
    assert executed_hypotheses[0]["failed_actions"][0]["action"] == "search_openclaw_conversations"
    assert executed_hypotheses[0]["failed_actions"][0]["failure_kind"] == "retryable"
    assert "exhausted_actions" not in executed_hypotheses[0]
    assert "FAILED" in evidence_summary


def test_non_retryable_failed_action_is_not_reselected_after_summarize():
    """A deterministic tool failure should not be offered again next loop."""
    execution_results = {
        "run_diagnostic_code": ActionExecutionResult(
            action_name="run_diagnostic_code",
            success=False,
            data={},
            error="TypeError: run_diagnostic_code() missing 1 required positional argument: 'code'",
        )
    }

    _evidence, executed_hypotheses, _evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence={},
        executed_hypotheses=[],
        investigation_loop_count=1,
        rationale="Try diagnostic code",
        plan_audit={},
    )

    actions = [
        MockAction("run_diagnostic_code"),
        MockAction("query_grafana_logs"),
    ]

    _available_actions, available_action_names = select_actions(
        actions=actions,
        available_sources={"test": {}},
        executed_hypotheses=executed_hypotheses,
        tool_budget=10,
    )

    assert available_action_names == ["query_grafana_logs"]


def test_retryable_failed_action_is_reselected_before_retry_limit():
    """A retryable failure should remain available until it reaches the limit."""
    execution_results = {
        "query_grafana_logs": ActionExecutionResult(
            action_name="query_grafana_logs",
            success=False,
            data={},
            error="Connection closed",
        )
    }

    _evidence, executed_hypotheses, _evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence={},
        executed_hypotheses=[],
        investigation_loop_count=1,
        rationale="Try Grafana logs",
        plan_audit={},
    )

    actions = [
        MockAction("query_grafana_logs"),
        MockAction("query_grafana_alert_rules"),
    ]

    _available_actions, available_action_names = select_actions(
        actions=actions,
        available_sources={"test": {}},
        executed_hypotheses=executed_hypotheses,
        tool_budget=10,
    )

    assert available_action_names == ["query_grafana_logs", "query_grafana_alert_rules"]


def test_retryable_failed_action_is_exhausted_at_retry_limit():
    """Repeated retryable failures should eventually be removed from planning."""
    execution_results = {
        "query_grafana_logs": ActionExecutionResult(
            action_name="query_grafana_logs",
            success=False,
            data={},
            error="Connection closed",
        )
    }

    _evidence, executed_hypotheses, _evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence={},
        executed_hypotheses=[],
        investigation_loop_count=1,
        rationale="Try Grafana logs",
        plan_audit={},
    )
    _evidence, executed_hypotheses, _evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence={},
        executed_hypotheses=executed_hypotheses,
        investigation_loop_count=2,
        rationale="Retry Grafana logs",
        plan_audit={},
    )

    actions = [
        MockAction("query_grafana_logs"),
        MockAction("query_grafana_alert_rules"),
    ]

    _available_actions, available_action_names = select_actions(
        actions=actions,
        available_sources={"test": {}},
        executed_hypotheses=executed_hypotheses,
        tool_budget=10,
    )

    assert executed_hypotheses[-1]["exhausted_actions"] == ["query_grafana_logs"]
    assert available_action_names == ["query_grafana_alert_rules"]


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
