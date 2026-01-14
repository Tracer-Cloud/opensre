"""
Pure evidence collectors.

No printing, no LLM calls. Just fetch data and return typed results.
"""

from src.agent.infrastructure.clients import (
    S3CheckResult,
    TracerRunResult,
    TracerTaskResult,
    AWSBatchJobResult,
    get_s3_client,
    get_tracer_client,
)


def check_s3_marker(bucket: str, prefix: str) -> S3CheckResult:
    """Check if _SUCCESS marker exists in S3."""
    client = get_s3_client()
    return client.check_marker(bucket, prefix)


def get_tracer_run(pipeline_name: str | None = None) -> TracerRunResult:
    """Get the latest pipeline run from Tracer."""
    client = get_tracer_client()
    return client.get_latest_run(pipeline_name)


def get_tracer_tasks(run_id: str) -> TracerTaskResult:
    """Get tasks for a pipeline run from Tracer."""
    client = get_tracer_client()
    return client.get_run_tasks(run_id)


def get_batch_jobs() -> AWSBatchJobResult:
    """Get AWS Batch job status from Tracer."""
    client = get_tracer_client()
    return client.get_batch_jobs()