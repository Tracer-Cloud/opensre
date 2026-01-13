"""
Client interfaces for external services.

Defines protocols and returns typed dataclasses.
Mock implementations delegate to existing mocks.
"""

from dataclasses import dataclass
from typing import Protocol


# ─────────────────────────────────────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class S3CheckResult:
    marker_exists: bool
    file_count: int
    files: list[str]


@dataclass(frozen=True)
class NextflowCheckResult:
    found: bool
    status: str | None
    error: str | None
    logs: str | None


# ─────────────────────────────────────────────────────────────────────────────
# Protocols (interfaces)
# ─────────────────────────────────────────────────────────────────────────────

class S3ClientProtocol(Protocol):
    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult: ...


class NextflowClientProtocol(Protocol):
    def check_finalize(self, pipeline_id: str) -> NextflowCheckResult: ...


# ─────────────────────────────────────────────────────────────────────────────
# Mock Implementations
# ─────────────────────────────────────────────────────────────────────────────

class MockS3Client:
    """S3 client backed by mock data."""
    
    def __init__(self):
        from src.mocks.s3 import get_s3_client
        self._client = get_s3_client()
    
    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult:
        files = self._client.list_objects(bucket, prefix)
        marker_exists = self._client.object_exists(bucket, f"{prefix}_SUCCESS")
        return S3CheckResult(
            marker_exists=marker_exists,
            file_count=len(files),
            files=[f["key"] for f in files],
        )


class MockNextflowClient:
    """Nextflow client backed by mock data."""
    
    def __init__(self):
        from src.mocks.nextflow import get_nextflow_client
        self._client = get_nextflow_client()
    
    def check_finalize(self, pipeline_id: str) -> NextflowCheckResult:
        run = self._client.get_latest_run(pipeline_id)
        if not run:
            return NextflowCheckResult(found=False, status=None, error=None, logs=None)
        
        steps = self._client.get_steps(run["run_id"])
        finalize = next((s for s in steps if s["step_name"] == "finalize"), None)
        logs = self._client.get_step_logs(run["run_id"], "finalize") if finalize else None
        
        return NextflowCheckResult(
            found=True,
            status=finalize["status"] if finalize else None,
            error=finalize.get("error") if finalize else None,
            logs=logs,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def get_s3_client() -> S3ClientProtocol:
    return MockS3Client()


def get_nextflow_client() -> NextflowClientProtocol:
    return MockNextflowClient()

