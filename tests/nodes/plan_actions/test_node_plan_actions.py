from __future__ import annotations

from typing import Any

from app.nodes.investigate.models import InvestigateInput
from app.nodes.plan_actions import node as node_module
from app.types.retrieval import RetrievalIntent


class _TrackerStub:
    def __init__(self) -> None:
        self.fields_updated: list[str] = []

    def start(self, _name: str, _message: str) -> None:
        return

    def complete(self, _name: str, *, fields_updated: list[str], message: str) -> None:
        self.fields_updated = fields_updated
        assert message


def test_node_plan_actions_emits_retrieval_controls(monkeypatch: Any) -> None:
    tracker = _TrackerStub()
    plan = node_module.InvestigationPlan(
        actions=["get_logs"],
        rationale="Inspect logs around failures.",
        retrieval_controls={"get_logs": RetrievalIntent(limit=25)},
    )

    monkeypatch.setattr(
        node_module.InvestigateInput,
        "from_state",
        lambda _state: InvestigateInput(raw_alert={}, context={}, tool_budget=10),
    )
    monkeypatch.setattr(
        node_module,
        "build_plan_actions",
        lambda **_kwargs: (plan, {"knowledge": {}}, ["get_logs"], [], False, "", []),
    )
    monkeypatch.setattr(node_module, "get_tracker", lambda: tracker)

    result = node_module.node_plan_actions(
        {
            "raw_alert": {},
            "context": {},
            "resolved_integrations": {},
            "investigation_loop_count": 0,
        }
    )

    assert result["planned_actions"] == ["get_logs"]
    assert result["retrieval_controls"] == {"get_logs": RetrievalIntent(limit=25)}
    assert "retrieval_controls" in tracker.fields_updated
