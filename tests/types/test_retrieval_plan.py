"""Tests for InvestigationPlan retrieval controls integration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.types.retrieval import (
    AggregationSpec,
    RetrievalControlsMap,
    RetrievalIntent,
    TimeBounds,
)


class InvestigationPlan(BaseModel):
    """Minimal InvestigationPlan for testing (isolated from nodes module)."""

    actions: list[str] = Field(description="List of action names to execute")
    rationale: str = Field(description="Rationale for the chosen actions")
    retrieval_controls: RetrievalControlsMap | None = Field(
        default=None,
        description="Optional structured retrieval intent per action",
    )

    def get_retrieval_intent(self, action_name: str) -> RetrievalIntent | None:
        """Get retrieval intent for a specific action if set."""
        if self.retrieval_controls is None:
            return None
        return self.retrieval_controls.get(action_name)


class TestInvestigationPlanRetrieval:
    """Tests for InvestigationPlan with retrieval controls."""

    def test_investigation_plan_backward_compatibility(self) -> None:
        """InvestigationPlan without retrieval_controls is valid (backward compat)."""
        plan = InvestigationPlan(
            actions=["get_logs"],
            rationale="Test rationale",
        )
        assert plan.actions == ["get_logs"]
        assert plan.rationale == "Test rationale"
        assert plan.retrieval_controls is None
        assert plan.get_retrieval_intent("get_logs") is None

    def test_investigation_plan_with_retrieval_controls(self) -> None:
        """InvestigationPlan with retrieval_controls."""
        intent = RetrievalIntent(
            time_bounds=TimeBounds(lookback_minutes=30),
            limit=100,
        )
        plan = InvestigationPlan(
            actions=["get_logs", "get_metrics"],
            rationale="Test with retrieval",
            retrieval_controls={"get_logs": intent},
        )
        assert plan.retrieval_controls is not None
        assert "get_logs" in plan.retrieval_controls
        assert "get_metrics" not in plan.retrieval_controls

        retrieved_intent = plan.get_retrieval_intent("get_logs")
        assert retrieved_intent is not None
        assert retrieved_intent.limit == 100

        # Missing action returns None
        assert plan.get_retrieval_intent("get_metrics") is None

    def test_investigation_plan_multiple_actions_with_intent(self) -> None:
        """InvestigationPlan with different retrieval intent per action."""
        logs_intent = RetrievalIntent(
            time_bounds=TimeBounds(lookback_minutes=30),
            limit=100,
        )
        metrics_intent = RetrievalIntent(
            aggregation=AggregationSpec(function="avg", field="cpu_percent"),
        )
        plan = InvestigationPlan(
            actions=["get_logs", "get_metrics"],
            rationale="Test with multiple intents",
            retrieval_controls={
                "get_logs": logs_intent,
                "get_metrics": metrics_intent,
            },
        )

        assert plan.get_retrieval_intent("get_logs").limit == 100
        assert plan.get_retrieval_intent("get_metrics").aggregation.function == "avg"

    def test_investigation_plan_empty_retrieval_controls(self) -> None:
        """InvestigationPlan with empty retrieval_controls dict."""
        plan = InvestigationPlan(
            actions=["get_logs"],
            rationale="Test",
            retrieval_controls={},
        )
        assert plan.retrieval_controls == {}
        assert plan.get_retrieval_intent("get_logs") is None
