"""Client modules for different services."""

from app.agent.tools.clients.cloudwatch_client import get_metric_statistics
from app.agent.tools.clients.grafana import (
    GrafanaAccountConfig,
    GrafanaClient,
    get_grafana_client_from_credentials,
)
from app.agent.tools.clients.llm_client import (
    RootCauseResult,
    get_llm_for_reasoning,
    get_llm_for_tools,
    parse_root_cause,
    reset_llm_singletons,
)
from app.agent.tools.clients.s3_client import S3CheckResult, get_s3_client
from app.agent.tools.clients.tracer_client import (
    AWSBatchJobResult,
    LogResult,
    PipelineRunSummary,
    PipelineSummary,
    TracerClient,
    TracerRunResult,
    TracerTaskResult,
    get_tracer_client,
    get_tracer_web_client,
)

__all__ = [
    # CloudWatch client
    "get_metric_statistics",
    # Grafana client
    "GrafanaAccountConfig",
    "GrafanaClient",
    "get_grafana_client_from_credentials",
    # LLM client
    "RootCauseResult",
    "get_llm_for_reasoning",
    "get_llm_for_tools",
    "parse_root_cause",
    "reset_llm_singletons",
    # S3 client
    "S3CheckResult",
    "get_s3_client",
    # Tracer client
    "AWSBatchJobResult",
    "LogResult",
    "PipelineRunSummary",
    "PipelineSummary",
    "TracerClient",
    "TracerRunResult",
    "TracerTaskResult",
    "get_tracer_client",
    "get_tracer_web_client",
]
