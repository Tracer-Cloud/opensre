"""Tests for the LangGraph wrapper ``node_adapt_window``.

The pure rule logic is exhaustively covered in ``test_rules.py``. These
tests focus on the wrapper's contract:

- it returns the rule's output unchanged when the rule emits a delta;
- it returns ``{}`` (LangGraph's "no state change") when the rule no-ops;
- it accepts a real ``InvestigationState`` shape (the TypedDict from
  ``app.state``) — not just bare dicts;
- it logs at INFO when an expansion happens, but never raises;
- the wrapper does not mutate the state passed in.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.incident_window import SOURCE_STARTS_AT, IncidentWindow
from app.nodes.adapt_window import node_adapt_window
from app.nodes.adapt_window.rules import (
    DEPLOY_TIMELINE_ACTION,
    EXPAND_REASON_EMPTY_DEPLOY_TIMELINE,
    SHARED_WINDOW_SOURCE,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


def _two_hour_window_dict() -> dict[str, Any]:
    return IncidentWindow(
        since=NOW - timedelta(hours=2),
        until=NOW,
        source=SOURCE_STARTS_AT,
        confidence=1.0,
    ).to_dict()


def _firing_state() -> dict[str, Any]:
    """A state dict that should fire the rule."""
    return {
        "incident_window": _two_hour_window_dict(),
        "incident_window_history": None,
        "executed_hypotheses": [{"actions": [DEPLOY_TIMELINE_ACTION]}],
        "evidence": {
            "git_deploy_timeline_count": 0,
            "git_deploy_timeline_window": {"source": SHARED_WINDOW_SOURCE},
        },
    }


def _no_op_state() -> dict[str, Any]:
    """A state dict that should NOT fire the rule (deploy didn't run)."""
    return {
        "incident_window": _two_hour_window_dict(),
        "incident_window_history": None,
        "executed_hypotheses": [{"actions": ["query_grafana_logs"]}],
        "evidence": {
            "git_deploy_timeline_count": 0,
            "git_deploy_timeline_window": {"source": SHARED_WINDOW_SOURCE},
        },
    }


def test_node_returns_state_delta_when_rule_fires() -> None:
    delta = node_adapt_window(_firing_state())  # type: ignore[arg-type]
    assert "incident_window" in delta
    assert "incident_window_history" in delta
    assert len(delta["incident_window_history"]) == 1


def test_node_returns_empty_dict_on_no_op() -> None:
    delta = node_adapt_window(_no_op_state())  # type: ignore[arg-type]
    assert delta == {}


def test_node_handles_completely_empty_state() -> None:
    # No fields populated yet — early in the pipeline, before extract_alert.
    delta = node_adapt_window({})  # type: ignore[arg-type]
    assert delta == {}


def test_node_does_not_mutate_input_state() -> None:
    state = _firing_state()
    snapshot_window = dict(state["incident_window"])
    snapshot_evidence = {
        k: dict(v) if isinstance(v, dict) else v for k, v in state["evidence"].items()
    }
    node_adapt_window(state)  # type: ignore[arg-type]
    assert state["incident_window"] == snapshot_window
    assert (
        state["evidence"]["git_deploy_timeline_window"]
        == snapshot_evidence["git_deploy_timeline_window"]
    )


def test_node_logs_at_info_when_expansion_happens(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="app.nodes.adapt_window.node"):
        node_adapt_window(_firing_state())  # type: ignore[arg-type]
    assert any(
        "widened incident window" in rec.message
        and EXPAND_REASON_EMPTY_DEPLOY_TIMELINE in rec.message
        for rec in caplog.records
    )


def test_node_does_not_log_at_info_on_no_op(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="app.nodes.adapt_window.node"):
        node_adapt_window(_no_op_state())  # type: ignore[arg-type]
    info_records = [rec for rec in caplog.records if rec.levelno >= logging.INFO]
    assert not any("widened incident window" in rec.message for rec in info_records)


def test_node_accepts_config_kwarg() -> None:
    # LangGraph passes a RunnableConfig; the node must accept and ignore it.
    delta = node_adapt_window(_firing_state(), config={"configurable": {}})  # type: ignore[arg-type]
    assert "incident_window" in delta
