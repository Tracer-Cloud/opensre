import uuid
from typing import Any, Dict, Optional
from .intent import AlertIntent
from .formatters.grafana import format_as_grafana

def from_pipeline_run(
    pipeline_name: str,
    run_name: str,
    status: str,
    timestamp: str,
    severity: str = "critical",
    alert_name: str = "PipelineFailure",
    environment: str = "production",
    trace_id: Optional[str] = None,
    run_url: Optional[str] = None,
    external_url: str = "",
    alert_id: Optional[str] = None,
    annotations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pure constructor: creates a pipeline run alert payload (Grafana format by default)."""
    intent = AlertIntent(
        pipeline_name=pipeline_name,
        run_name=run_name,
        status=status,
        timestamp=timestamp,
        severity=severity,
        alert_name=alert_name,
        environment=environment,
        trace_id=trace_id,
        run_url=run_url,
        external_url=external_url,
        alert_id=alert_id or str(uuid.uuid4()),
        annotations=annotations or {},
    )
    
    return format_as_grafana(intent)


def create_alert(
    pipeline_name: str,
    run_name: str,
    status: str,
    timestamp: str,
    annotations: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    run_url: Optional[str] = None,
    external_url: str = "",
    alert_id: Optional[str] = None,
    severity: str = "critical",
    alert_name: str = "PipelineFailure",
    environment: str = "production",
) -> Dict[str, Any]:
    """
    Source-agnostic constructor for standardized alerts.
    
    Assembles an AlertIntent and renders it via the default formatter (Grafana).
    """
    return from_pipeline_run(
        pipeline_name=pipeline_name,
        run_name=run_name,
        status=status,
        timestamp=timestamp,
        annotations=annotations,
        trace_id=trace_id,
        run_url=run_url,
        external_url=external_url,
        alert_id=alert_id,
        severity=severity,
        alert_name=alert_name,
        environment=environment,
    )
