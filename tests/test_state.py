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


def test_make_chat_state_validates_messages() -> None:
    state = make_chat_state(messages=[{"role": "user", "content": "hello"}])

    assert state["mode"] == "chat"
    assert state["messages"][0]["content"] == "hello"


def test_agent_state_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="mesages.*messages"):
        AgentStateModel.model_validate({"mode": "chat", "mesages": []})
