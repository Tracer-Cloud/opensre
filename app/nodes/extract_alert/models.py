"""Models for alert extraction."""

from pydantic import BaseModel, Field


class AlertExtractionInput(BaseModel):
    """Normalized input for alert extraction."""

    raw_alert: str = Field(description="Raw alert payload as a string")


class AlertDetails(BaseModel):
    """Structured alert details extracted from raw input."""

    is_noise: bool = Field(description="True if message is noise/chat, False if it's a real alert")
    alert_name: str = Field(description="Name of the alert")
    pipeline_name: str = Field(description="Primary affected table or pipeline")
    severity: str = Field(description="Severity of the alert (e.g. critical, high, warning, info)")
    alert_source: str | None = Field(
        default=None,
        description=(
            "Platform that fired the alert: 'grafana', 'datadog', 'honeycomb', "
            "'coralogix', 'cloudwatch', or None if unknown"
        ),
    )
    environment: str | None = Field(default=None, description="Environment, if present")
    summary: str | None = Field(default=None, description="Short alert summary, if present")
    # Structured routing fields extracted from alert text
    kube_namespace: str | None = Field(default=None, description="Kubernetes namespace if mentioned (e.g. tracer-test)")
    cloudwatch_log_group: str | None = Field(default=None, description="CloudWatch log group if mentioned")
    error_message: str | None = Field(default=None, description="The actual error message or PIPELINE_ERROR content from the alert")
    log_query: str | None = Field(default=None, description="Datadog/log search query from the alert body (e.g. 'OOMKilled kube_namespace:tracer-cl' or 'PIPELINE_ERROR kube_namespace:tracer-test')")
    eks_cluster: str | None = Field(default=None, description="EKS cluster name if mentioned (e.g. tracer-eks-test)")
    pod_name: str | None = Field(default=None, description="Kubernetes pod name if mentioned (e.g. etl-worker-7d9f8b-xkp2q)")
    deployment: str | None = Field(default=None, description="Kubernetes deployment name if mentioned (e.g. etl-worker)")
