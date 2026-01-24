"""Client modules for different services."""

from src.agent.tools.clients.cloudwatch_client import get_metric_statistics
from src.agent.tools.clients.s3_client import S3CheckResult, get_s3_client
from src.agent.tools.clients.tracer_client import (
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
