from dataclasses import dataclass, field
from typing import Any, Dict, Optional

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
    trace_id: Optional[str] = None
    run_url: Optional[str] = None
    external_url: str = ""
    alert_id: Optional[str] = None
    annotations: Dict[str, Any] = field(default_factory=dict)
