"""State constructors and defaults."""

from __future__ import annotations

import time
from typing import Any, cast

from app.alerts import normalize_alert_payload
from app.integrations.opensre.hf_remote import (
    extract_openrca_scoring_points,
    strip_scoring_points_from_alert,
)
from app.state.agent_state import AgentState, AgentStateModel
from app.state.types import ChatMessage

STATE_DEFAULTS: dict[str, Any] = {
    "mode": "chat",
    "route": "",
    "is_noise": False,
    "org_id": "",
    "user_id": "",
    "user_email": "",
    "user_name": "",
    "organization_slug": "",
    "messages": [],
    "planned_actions": [],
    "plan_rationale": "",
    "resolved_integrations": {},
    "available_sources": {},
    "available_action_names": [],
    "context": {},
    "evidence": {},
    "root_cause": "",
    "root_cause_category": "",
    "validated_claims": [],
    "non_validated_claims": [],
    "validity_score": 0.0,
    "investigation_recommendations": [],
    "remediation_steps": [],
    "investigation_loop_count": 0,
    "hypotheses": [],
    "executed_hypotheses": [],
    "masking_map": {},
    "slack_context": {},
    "discord_context": {},
    "telegram_context": {},
    "thread_id": "",
    "run_id": "",
    "_auth_token": "",
    "slack_message": "",
    "problem_md": "",
    "report": "",
}


def make_initial_state(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
    *,
    opensre_evaluate: bool = False,
) -> AgentState:
    """Create initial state for investigation mode."""
    rubric = ""
    alert_payload: str | dict[str, Any] = raw_alert if raw_alert is not None else {}
    if isinstance(alert_payload, dict):
        if opensre_evaluate:
            rubric = extract_openrca_scoring_points(alert_payload)
            if rubric:
                alert_payload = strip_scoring_points_from_alert(dict(alert_payload))
        elif extract_openrca_scoring_points(alert_payload):
            # Blind investigation: drop rubric from agent-visible alert (file may include it).
            alert_payload = strip_scoring_points_from_alert(dict(alert_payload))

        # Normalize source-specific payloads into a canonical alert shape once,
        # before any downstream extraction/planning nodes run.
        alert_payload = normalize_alert_payload(alert_payload)

    state = AgentStateModel.model_validate(
        {
            "mode": "investigation",
            "alert_name": alert_name,
            "pipeline_name": pipeline_name,
            "severity": severity,
            "raw_alert": alert_payload,
            "investigation_started_at": time.monotonic(),
            "opensre_evaluate": opensre_evaluate,
            "opensre_eval_rubric": rubric,
            **{k: v for k, v in STATE_DEFAULTS.items() if k not in ("mode", "messages")},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))


def make_chat_state(
    org_id: str = "",
    user_id: str = "",
    user_email: str = "",
    user_name: str = "",
    organization_slug: str = "",
    messages: list[ChatMessage] | None = None,
) -> AgentState:
    """Create initial state for chat mode."""
    state = AgentStateModel.model_validate(
        {
            "mode": "chat",
            "org_id": org_id,
            "user_id": user_id,
            "user_email": user_email,
            "user_name": user_name,
            "organization_slug": organization_slug,
            "messages": messages or [],
            "context": {},
        }
    )
    return cast(AgentState, state.model_dump(mode="python", by_alias=True, exclude_none=True))
