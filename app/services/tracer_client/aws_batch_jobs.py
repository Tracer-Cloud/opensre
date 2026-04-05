"""Batch jobs-related API methods and models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.tracer_client.tracer_client_base import TracerClientBase


@dataclass(frozen=True)
class AWSBatchJobResult:
    """Result from get_batch_jobs."""

    found: bool
    total_jobs: int = 0
    failed_jobs: int = 0
    succeeded_jobs: int = 0
    jobs: list[dict] | None = None
    failure_reason: str | None = None


class AWSBatchJobsMixin(TracerClientBase):
    """Mixin for AWS Batch jobs-related API methods."""

    def get_batch_jobs(
        self,
        trace_id: str | None = None,
        statuses: list[str] | None = None,
        return_dict: bool = False,
    ) -> AWSBatchJobResult | dict:
        """
        Get AWS Batch jobs from /api/aws/batch/jobs/completed endpoint.

        Args:
            trace_id: Required trace ID to look up batch jobs.
            statuses: Optional list of statuses to filter by.
            return_dict: If True, returns raw dict response (for web app API usage).
                        If False, returns AWSBatchJobResult (for staging API usage).

        Returns:
            AWSBatchJobResult if return_dict=False, dict if return_dict=True.
        """
        if not trace_id:
            return AWSBatchJobResult(found=False) if not return_dict else {"success": False, "data": []}

        params: dict[str, Any] = {
            "traceId": trace_id,
            "orgId": self.org_id,
        }

        # Support both old API (statuses as list) and new API (statuses as query params)
        if statuses:
            if isinstance(statuses, list):
                params["status"] = statuses
            else:
                for status in statuses:
                    params.setdefault("status", []).append(status)
        else:
            # Default for legacy API
            params["status"] = ["SUCCEEDED", "FAILED", "RUNNING"]

        data = self._get("/api/aws/batch/jobs/completed", params)

        # Return raw dict for web app API usage
        if return_dict:
            return data

        # Return typed result for staging API usage
        if not data.get("success") or not data.get("data"):
            return AWSBatchJobResult(found=False)

        jobs = []
        failure_reason = None
        failed_count = 0
        succeeded_count = 0

        for row in data["data"]:
            container = row.get("container", {})
            resources = {
                r["type"]: int(r["value"]) for r in container.get("resourceRequirements", [])
            }

            status = row.get("status", "")
            if status == "FAILED":
                failed_count += 1
                if not failure_reason:
                    failure_reason = container.get("reason")
            elif status == "SUCCEEDED":
                succeeded_count += 1

            started_at = None
            if row.get("startedAt"):
                started_at = datetime.fromtimestamp(row["startedAt"] / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            jobs.append(
                {
                    "job_name": row.get("jobName", ""),
                    "status": status,
                    "status_reason": row.get("statusReason", ""),
                    "failure_reason": container.get("reason"),
                    "exit_code": container.get("exitCode"),
                    "vcpu": resources.get("VCPU", 0),
                    "memory_mb": resources.get("MEMORY", 0),
                    "gpu_count": resources.get("GPU", 0),
                    "started_at": started_at,
                }
            )

        return AWSBatchJobResult(
            found=True,
            total_jobs=len(jobs),
            failed_jobs=failed_count,
            succeeded_jobs=succeeded_count,
            jobs=jobs,
            failure_reason=failure_reason,
        )
