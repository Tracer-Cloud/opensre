"""
Pure evidence collectors.

No printing, no LLM calls. Just fetch data and return typed results.
"""

from src.agent.infrastructure.clients import (
    S3CheckResult,
    NextflowCheckResult,
    get_s3_client,
    get_nextflow_client,
)


def check_s3_marker(bucket: str, prefix: str) -> S3CheckResult:
    """Check if _SUCCESS marker exists in S3."""
    client = get_s3_client()
    return client.check_marker(bucket, prefix)


def check_nextflow_finalize(pipeline_id: str) -> NextflowCheckResult:
    """Get Nextflow finalize step status and logs."""
    client = get_nextflow_client()
    return client.check_finalize(pipeline_id)

