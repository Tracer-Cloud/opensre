"""Trusted sources and normalized provider events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TriageSource(BaseModel):
    """Authority configured by the operator, never by incoming alert text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    query_url: str
    credential_ref: str
    credential_kind: Literal["owned", "environment", "integration"] = "owned"
    credential_instance: str = ""
    services: tuple[str, ...]
    webhook_url: str
    username: str
    password_digest: str
    paused: bool = False
    removed: bool = False
    demo: bool = False
    query_ready: bool = False
    delivery_at: float | None = None
    created_at: float
    generation: int = 0

    @field_validator("services")
    @classmethod
    def validate_services(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require an explicit, bounded service scope."""
        if not value or len(value) > 30 or any(not s.strip() or len(s) > 200 for s in value):
            raise ValueError("Choose between 1 and 30 non-empty service names.")
        return tuple(dict.fromkeys(s.strip() for s in value))


class ProviderEvent(BaseModel):
    """One occurrence update from an authenticated provider notification."""

    fingerprint: str = Field(min_length=1, max_length=256)
    starts_at: str
    ends_at: str | None = None
    status: str
    labels: dict[str, str]
    annotations: dict[str, str]
    delivery_test: bool = False


@dataclass(frozen=True)
class InvestigationClaim:
    """Fenced lease plus the original cumulative investigation budget."""

    id: str
    occurrence_id: str
    token: str
    source: TriageSource
    alert: dict[str, Any]
    question: str
    deadline: float
    tool_calls: int
    model_iterations: int
