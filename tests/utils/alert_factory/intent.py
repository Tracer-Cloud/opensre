from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AlertIntent:
    """Platform-agnostic representation of an alert event."""

    pipeline_name: str
    run_name: str
    status: str
    timestamp: str
    severity: str = "critical"
    alert_name: str = "PipelineFailure"
    environment: str = "production"
    trace_id: str | None = None
    run_url: str | None = None
    external_url: str = ""
    alert_id: str | None = None
    annotations: dict[str, Any] = field(default_factory=dict)
