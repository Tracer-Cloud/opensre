"""SigNoz v0.145.0 / Alertmanager v4 native webhook contract."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from config.constants.triage import TRIAGE_TEXT_LIMIT
from core.domain.alerts.triage.models import ProviderEvent


class NativeAlert(BaseModel):
    """Individual alerts own lifecycle even when a grouped status differs."""

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str
    endsAt: str = "0001-01-01T00:00:00Z"
    fingerprint: str = Field(min_length=1, max_length=256)

    @field_validator("startsAt")
    @classmethod
    def validate_start(cls, value: str) -> str:
        """Canonicalize timezone-aware occurrence identity."""
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.year < 1970:
            raise ValueError("startsAt must be a timezone-aware alert timestamp")
        # Preserve provider nanosecond precision in occurrence identity while
        # normalizing equivalent offsets and trailing zeroes.
        fraction = re.search(r"\d{2}:\d{2}:\d{2}\.(\d+)", value)
        tail = fraction.group(1).rstrip("0") if fraction else ""
        base = parsed.astimezone(UTC).replace(microsecond=0).isoformat(timespec="seconds")
        return base.replace("+00:00", ("." + tail if tail else "") + "+00:00")

    @field_validator("labels", "annotations")
    @classmethod
    def bound_text(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject oversized text rather than silently changing accepted evidence."""
        if len(value) > 100 or sum(len(k) + len(v) for k, v in value.items()) > TRIAGE_TEXT_LIMIT:
            raise ValueError("Alert text exceeds its bounded contract")
        return value


class NativeNotification(BaseModel):
    """A grouped native notification, including documented delivery tests."""

    version: Literal["4"] = "4"
    alerts: list[NativeAlert] = Field(min_length=1, max_length=1000)
    truncatedAlerts: int = Field(default=0, ge=0)


def parse_notification(payload: Any) -> list[ProviderEvent]:
    """Split native grouped payloads without trusting receiver names or URLs."""
    notification = NativeNotification.model_validate(payload)
    result: list[ProviderEvent] = []
    for alert in notification.alerts:
        # Provider's exact test signature is delivery-only; it cannot grant authority.
        is_test = (
            alert.labels.get("alertname", "").startswith("Test Alert (")
            and alert.annotations.get("description") == "Test alert fired from SigNoz"
            and alert.annotations.get("summary") == "Test alert fired from SigNoz"
        )
        annotations = dict(alert.annotations)
        if notification.truncatedAlerts:
            annotations["provider_truncation"] = str(notification.truncatedAlerts)
        result.append(
            ProviderEvent(
                fingerprint=alert.fingerprint,
                starts_at=alert.startsAt,
                ends_at=alert.endsAt if alert.status == "resolved" else None,
                status=alert.status,
                labels=alert.labels,
                annotations=annotations,
                delivery_test=is_test,
            )
        )
    return result
