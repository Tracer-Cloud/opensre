"""Tools and tasks-related API methods and models."""

import os
from dataclasses import dataclass

from app.agent.tools.clients.tracer_client.tracer_client_base import TracerClientBase

DEMO_TRACE_ID = "efb797c9-0226-4932-8eb0-704f03d1752f"


@dataclass(frozen=True)
class TracerTaskResult:
    """Result from get_run_tasks."""

    found: bool
    total_tasks: int = 0
    failed_tasks: int = 0
    completed_tasks: int = 0
    tasks: list[dict] | None = None
    failed_task_details: list[dict] | None = None


class TracerToolsMixin(TracerClientBase):
    """Mixin for Tracer tools and tasks-related API methods."""

    def get_run_tasks(self, run_id: str) -> TracerTaskResult:  # noqa: ARG002
        """Get tasks from /api/tools endpoint."""
        trace_id = os.getenv("TRACER_TRACE_ID", DEMO_TRACE_ID)
        data = self._get(f"/api/tools/{trace_id}", {"orgId": self.org_id})

        if not data.get("success") or not data.get("data"):
            return TracerTaskResult(found=False)

        tasks = []
        failed_details = []
        for row in data["data"]:
            task = {
                "tool_name": row.get("tool_name", ""),
                "exit_code": row.get("exit_code"),
                "runtime_ms": float(row.get("runtime_ms", 0) or 0),
            }
            tasks.append(task)

            exit_code = row.get("exit_code")
            if exit_code and exit_code not in ("0", "", None):
                failed_details.append(
                    {
                        **task,
                        "tool_cmd": row.get("tool_cmd", ""),
                        "reason": row.get("reason"),
                        "explanation": row.get("explanation"),
                    }
                )

        return TracerTaskResult(
            found=True,
            total_tasks=len(tasks),
            failed_tasks=len(failed_details),
            completed_tasks=len(tasks) - len(failed_details),
            tasks=tasks,
            failed_task_details=failed_details,
        )

    def get_tools(self, trace_id: str) -> dict:
        """Fetch tools for a trace from /api/tools/[traceId]."""
        params = {}
        data = self._get(f"/api/tools/{trace_id}", params)
        return data
