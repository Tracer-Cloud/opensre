"""Logs-related API methods and models."""

from dataclasses import dataclass

from app.agent.tools.clients.tracer_client.tracer_client_base import TracerClientBase


@dataclass(frozen=True)
class LogResult:
    """Result from get_logs."""

    success: bool
    data: list[dict]
    total_logs: int
    took: int
    pagination: dict | None = None


class TracerLogsMixin(TracerClientBase):
    """Mixin for Tracer logs-related API methods."""

    def get_logs(
        self, trace_id: str | None = None, run_id: str | None = None, size: int = 100
    ) -> dict:
        """Fetch logs from /api/opensearch/logs."""
        params = {"orgId": self.org_id, "size": size}
        if trace_id:
            params["runId"] = trace_id
        elif run_id:
            params["runId"] = run_id
        data = self._get("/api/opensearch/logs", params)
        return data
