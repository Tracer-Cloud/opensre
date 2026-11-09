from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from infrastructure.observability.errors.service import capture_service_error
from integrations.config_models import TemporalIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_DEFAULT_PAGE_SIZE = 10
TemporalConfig = TemporalIntegrationConfig


class TemporalClient:
    def __init__(self, config: TemporalConfig):
        self.config = config
        self._client: httpx.Client = httpx.Client(
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url) and bool(self.config.namespace)

    def list_workflow_executions(self, next_page_token: str | None = None) -> dict[str, Any]:
        """List recent workflow executions with status and failure reason.

        Returns paginated workflow executions for the configured namespace.
        Each execution includes workflowId, type, status, taskQueue, and timing info.
        """
        params: dict[str, str | int | bool] = {
            "pageSize": _DEFAULT_PAGE_SIZE,
        }
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token

        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/workflows", params=params
            )
            r.raise_for_status()
            data = r.json()

            executions = data.get("executions", [])
            return {
                "success": True,
                "executions": executions,
                "next_page_token": data.get("nextPageToken", ""),
                "total": len(executions),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="list_workflow_executions",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="list_workflow_executions",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def get_workflow_history(
        self, workflow_id: str, run_id: str | None = None, next_page_token: str | None = None
    ) -> dict[str, Any]:
        """Fetch the event history for a specific workflow execution.

        Returns the ordered sequence of events (started, activity scheduled,
        activity failed, workflow failed, etc.) that tells the story of what
        happened during the execution. Essential for diagnosing why a workflow failed.
        """
        params: dict[str, str | int | bool] = {
            "pageSize": _DEFAULT_PAGE_SIZE,
        }
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token
        if run_id is not None:
            params["execution.runId"] = run_id
        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}"
                f"/workflows/{quote(workflow_id, safe='')}/history",
                params=params,
            )
            r.raise_for_status()
            data = r.json()
            events = (data.get("history") or {}).get("events", [])
            return {
                "success": True,
                "events": events,
                "next_page_token": data.get("nextPageToken", ""),
                "archived": data.get("archived", False),
                "total": len(events),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_workflow_history",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_workflow_history",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def describe_task_queue(self, task_queue_name: str) -> dict[str, Any]:
        """Describe a task queue's pollers and backlog stats.

        The Temporal HTTP API does not expose a "list all task queues" endpoint.
        Instead, task queue names are discovered from workflow executions (each
        execution reports which task queue it ran on). This method describes a
        single queue by name — returning active pollers (workers) and backlog
        metrics (approximate count, age, add/dispatch rates).

        Use the taskQueue field from list_workflow_executions() results to
        identify which queues to inspect.
        """
        params: dict[str, str | int | bool] = {
            "reportStats": True,
            "taskQueueType": "TASK_QUEUE_TYPE_WORKFLOW",
        }
        try:
            r = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}"
                f"/task-queues/{quote(task_queue_name, safe='')}",
                params=params,
            )
            r.raise_for_status()
            data = r.json()

            pollers = data.get("pollers", [])
            return {
                "success": True,
                "pollers": pollers,
                "stats": data.get("stats", {}),
                "total": len(pollers),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="describe_task_queue",
                extras={"query": params},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="describe_task_queue",
                extras={"query": params},
            )
            return {"success": False, "error": str(exc)}

    def get_namespace_info(self) -> dict[str, Any]:
        """Fetch namespace state and workflow counts grouped by execution status.

        Combines DescribeNamespace (state, config) with CountWorkflowExecutions
        (grouped by ExecutionStatus) to provide namespace-level health metrics.
        """
        try:
            ns_resp = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}",
            )
            ns_resp.raise_for_status()
            ns_data = ns_resp.json()

            count_resp = self._client.get(
                f"/api/v1/namespaces/{self.config.namespace}/workflow-count",
                params={"query": "GROUP BY ExecutionStatus"},
            )
            count_resp.raise_for_status()
            count_data = count_resp.json()

            namespace_info = ns_data.get("namespaceInfo", {})
            return {
                "success": True,
                "name": namespace_info.get("name", ""),
                "state": namespace_info.get("state", ""),
                "workflow_count": count_data.get("count", "0"),
                "groups": self._flatten_status_groups(count_data.get("groups", [])),
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_namespace_info",
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="get_namespace_info",
            )
            return {"success": False, "error": str(exc)}

    def _flatten_status_groups(self, groups: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Flatten CountWorkflowExecutions GROUP BY results into [{status, count}].

        The raw response nests each bucket as
        ``{"groupValues": [{"data": "<base64 Payload>"}], "count": "1"}``.
        The status name is a base64-encoded Temporal Payload, so we decode it
        and drop the metadata/encoding noise the LLM does not need.
        """
        flattened: list[dict[str, str]] = []
        for group in groups:
            values = group.get("groupValues") or []
            decoded = [str(self._decode_payload_data(v.get("data", ""))) for v in values]
            flattened.append(
                {
                    "status": ", ".join(decoded),
                    "count": str(group.get("count", "0")),
                }
            )
        return flattened

    @staticmethod
    def _decode_payload_data(data: str) -> Any:
        """Decode a Temporal HTTP API Payload ``data`` field.

        The JSON/HTTP API base64-encodes every Payload value (e.g. the status
        name ``"Failed"`` arrives as ``"IkZhaWxlZCI="``). Decode the base64 then
        JSON-parse it. Fall back to the raw string if it is not a standard
        base64/JSON payload, so a format change never crashes the caller.
        """
        if not data:
            return data
        try:
            return json.loads(base64.b64decode(data))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return data

    def probe_access(self) -> ProbeResult:
        if not self.is_configured:
            return ProbeResult.failed("Temporal Client is not configured.")

        try:
            r = self._client.get(f"/api/v1/namespaces/{self.config.namespace}")
            r.raise_for_status()

            return ProbeResult.passed("Successfully connected to Temporal.")
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="probe_access",
            )
            return ProbeResult.failed(
                f"Failed to connect to Temporal: {exc.response.status_code}: {exc.response.text[:200]}."
            )
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="temporal",
                method="probe_access",
            )
            return ProbeResult.failed(f"Failed to connect to Temporal: {str(exc)}.")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TemporalClient:
        return self

    def __exit__(self, exc_type, _exc_val, _exc_tb) -> None:
        self.close()
