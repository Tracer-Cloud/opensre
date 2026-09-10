"""Typed proactive-message interaction and decision records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DecisionName = Literal["send", "suppress"]
EvidenceSource = Literal["session", "slack_context", "none"]


@dataclass(frozen=True, slots=True)
class ProactiveTrigger:
    """One completed Slack turn bounded by durable session record ids."""

    session_id: str
    start_record_id: str | None
    end_record_id: str
    channel_id: str
    thread_ts: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ProactiveInteraction:
    """Persisted user interaction and corresponding agent outcome."""

    interaction_id: str
    end_record_id: str
    messages: tuple[tuple[str, str], ...]
    user_message: str
    agent_outcome: str


class ProactiveMessageDecision(BaseModel):
    """Structured send-or-suppress verdict returned by the judgement LLM."""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionName
    rationale: str = Field(default="", max_length=1_000)
    message: str = Field(default="", max_length=1_200)
    signal_key: str = Field(default="", max_length=160)
    new_verified_information: bool = False
    clear_owner_and_action: bool = False
    material_timing: bool = False
    verified_information: str = Field(default="", max_length=1_000)
    evidence_quote: str = Field(default="", max_length=500)
    evidence_source: EvidenceSource = "none"
    owner: str = Field(default="", max_length=200)
    next_action: str = Field(default="", max_length=500)
    material_timing_reason: str = Field(default="", max_length=500)


__all__ = [
    "DecisionName",
    "EvidenceSource",
    "ProactiveInteraction",
    "ProactiveMessageDecision",
    "ProactiveTrigger",
]
