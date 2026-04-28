from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.state import AgentStateModel, make_chat_state, make_initial_state


def test_make_initial_state_validates_and_sets_defaults() -> None:
    state = make_initial_state(
        alert_name="HighErrorRate",
        pipeline_name="payments",
        severity="critical",
        raw_alert={"source": "grafana"},
    )

    assert state["mode"] == "investigation"
    assert state["raw_alert"] == {"source": "grafana"}
    assert state["planned_actions"] == []
    assert state.get("opensre_evaluate") is False


def test_make_initial_state_strips_rubric_when_not_evaluate() -> None:
    raw = {
        "commonAnnotations": {"summary": "x", "scoring_points": "secret rubric"},
        "foo": 1,
    }
    state = make_initial_state(
        "A",
        "p",
        "warning",
        raw_alert=raw,
        opensre_evaluate=False,
    )
    assert not (state.get("opensre_eval_rubric") or "").strip()
    ra = state["raw_alert"]
    assert isinstance(ra, dict)
    assert "scoring_points" not in (ra.get("commonAnnotations") or {})


def test_make_initial_state_evaluate_strips_scoring_points() -> None:
    raw = {
        "commonAnnotations": {"summary": "x", "scoring_points": "rubric text"},
        "foo": 1,
    }
    state = make_initial_state(
        "A",
        "p",
        "warning",
        raw_alert=raw,
        opensre_evaluate=True,
    )
    assert state["opensre_evaluate"] is True
    assert "rubric text" in (state.get("opensre_eval_rubric") or "")
    ra = state["raw_alert"]
    assert isinstance(ra, dict)
    assert "scoring_points" not in (ra.get("commonAnnotations") or {})


def test_make_chat_state_validates_messages() -> None:
    state = make_chat_state(messages=[{"role": "user", "content": "hello"}])

    assert state["mode"] == "chat"
    assert state["messages"][0]["content"] == "hello"


def test_agent_state_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="mesages.*messages"):
        AgentStateModel.model_validate({"mode": "chat", "mesages": []})
