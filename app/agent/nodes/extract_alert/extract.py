"""Alert extraction and classification - single LLM call."""

import json
from typing import Any, cast

from app.agent.nodes.extract_alert.models import AlertDetails, AlertExtractionInput
from app.agent.output import debug_print
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm


def extract_alert_details(state: InvestigationState) -> AlertDetails:
    """Single LLM call: classify noise + extract all routing fields simultaneously."""
    raw_alert = state.get("raw_alert")
    if raw_alert is None:
        raise RuntimeError("raw_alert is required for alert extraction")

    text = AlertExtractionInput(raw_alert=_format_raw_alert(raw_alert)).raw_alert

    prompt = f"""Classify and extract fields from this alert message.

is_noise=true ONLY for: casual chat, greetings, trivial messages ("ok", "thanks"), or replies to existing investigation reports.
is_noise=false (default) for: any alert, error, failure, incident, warning, monitoring notification.
When in doubt, set is_noise=false.

Extract these fields from the message text:
- alert_name: The name of the alert (e.g. "Pipeline Error in Logs")
- pipeline_name: The affected pipeline/table/service name
- severity: critical/high/warning/info
- alert_source: Which platform fired this alert. Set to "grafana" if the URL/text mentions grafana.net, Grafana alerting, or grafana_folder. Set to "datadog" if it mentions datadoghq.com or Datadog monitors. Set to "cloudwatch" if it mentions AWS CloudWatch alarms. Set to "eks" if it mentions EKS, CrashLoopBackOff, OOMKilled, Kubernetes pods, or kube_namespace. Leave null if truly unknown.
- kube_namespace: Kubernetes namespace if mentioned (e.g. "tracer-test" from "kube_namespace:tracer-test")
- cloudwatch_log_group: AWS CloudWatch log group if mentioned (e.g. "/aws/ecs/my-service")
- error_message: The actual error line from the alert (e.g. "PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id']")
- log_query: The log search query from the alert body — usually the "Search logs:" or "monitored query" line (e.g. "OOMKilled kube_namespace:tracer-cl" or "PIPELINE_ERROR kube_namespace:tracer-test"). Leave null if not present.
- eks_cluster: EKS cluster name if mentioned (e.g. "tracer-eks-test" from eks_cluster or cluster annotation)
- pod_name: Kubernetes pod name if mentioned (e.g. "etl-worker-7d9f8b-xkp2q")
- deployment: Kubernetes deployment name if mentioned (e.g. "etl-worker")

Message:
{text}
"""
    llm = get_llm()
    try:
        details = cast(
            AlertDetails,
            llm.with_structured_output(AlertDetails)
               .with_config(run_name="LLM – Classify + extract alert")
               .invoke(prompt),
        )
        debug_print(
            f"Alert classified: {'NOISE' if details.is_noise else 'ALERT'} | "
            f"namespace={details.kube_namespace} | error={details.error_message}"
        )
        return details
    except Exception as err:
        debug_print(f"LLM alert extraction failed, using fallback: {err}")
        return _fallback_details(state, raw_alert)


def _fallback_details(state: InvestigationState, raw_alert: str | dict[str, Any]) -> AlertDetails:
    """Best-effort extraction without LLM when it fails."""
    alert_name = state.get("alert_name", "unknown")
    pipeline_name = state.get("pipeline_name", "unknown")
    severity = state.get("severity", "unknown")

    if isinstance(raw_alert, dict):
        labels = raw_alert.get("labels", {})
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        alert_name = labels.get("alertname", alert_name)
        pipeline_name = (
            labels.get("pipeline")
            or annotations.get("pipeline_name")
            or raw_alert.get("pipeline_name")
            or pipeline_name
        )
        severity = labels.get("severity", severity)

    return AlertDetails(
        is_noise=False,
        alert_name=alert_name or "unknown",
        pipeline_name=pipeline_name or "unknown",
        severity=severity or "unknown",
    )


def _format_raw_alert(raw_alert: str | dict[str, Any]) -> str:
    if isinstance(raw_alert, str):
        return raw_alert
    # For Slack alerts, prefer the human-readable text field
    if isinstance(raw_alert, dict) and raw_alert.get("text"):
        return str(raw_alert["text"])
    return json.dumps(raw_alert, indent=2, sort_keys=True)
