# ======== from tools/temporal_namespace_info_tool/ ========

"""Temporal namespace health overview tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.temporal.client import TemporalClient, TemporalConfig

#: CountWorkflowExecutions GROUP BY results use short Temporal Payload
#: status names ("Running", "Failed", "TimedOut"), decoded by
#: TemporalClient._flatten_status_groups -- a different vocabulary from the
#: raw HTTP API's WORKFLOW_EXECUTION_STATUS_* enum strings used elsewhere in
#: this file (list_workflow_executions), so this constant is scoped to the
#: namespace-info mapper only.
_UNHEALTHY_GROUP_STATUSES = frozenset({"Failed", "TimedOut", "Terminated"})


def _map_temporal_namespace_info(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the namespace state, workflow count, and unhealthy workflow count."""
    if not output.get("available") or output.get("error"):
        return
    groups = output.get("groups") or []
    unhealthy = sum(
        int(g.get("count", 0) or 0) for g in groups if g.get("status") in _UNHEALTHY_GROUP_STATUSES
    )
    parts = [
        f"namespace '{output.get('name', 'unknown')}' ({output.get('state', 'unknown')})",
        f"{output.get('workflow_count', 0)} workflow(s)",
    ]
    if groups:
        parts.append(f"{unhealthy} failed/timed-out/terminated")
    record_evidence_entry(
        evidence,
        source="temporal_namespace_info",
        label="Temporal Namespace Info",
        summary=", ".join(parts),
    )


class TemporalNamespaceInfoTool(BaseTool):
    """Fetch namespace state and workflow counts grouped by execution status.

    This is the first tool to call when investigating Temporal-related incidents.
    It provides a high-level health snapshot: is the namespace active, and how
    many workflows are running vs failed vs timed out. Use this to determine
    whether something is wrong before drilling into specific workflows.
    """

    name = "temporal_namespace_info"
    source = "temporal"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_temporal_namespace_info
    description = (
        "Fetch Temporal namespace health overview: namespace state and workflow "
        "execution counts grouped by status (Running, Failed, TimedOut, etc.). "
        "Use as the first investigation step to assess overall namespace health."
    )
    use_cases = [
        "Getting a high-level health snapshot of a Temporal namespace",
        "Checking if a namespace is active or deprecated/deleted",
        "Counting how many workflows are currently running, failed, or timed out",
        "Determining whether a Temporal incident is widespread or isolated",
        "Initial triage before drilling into specific workflow failures",
    ]
    requires = ["base_url", "namespace"]
    injected_params = ["base_url", "api_key", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Temporal server base URL.",
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Temporal API key. Empty for unauthenticated self-hosted clusters.",
            },
            "namespace": {
                "type": "string",
                "default": "default",
                "description": "Temporal namespace to inspect.",
            },
        },
        "required": ["base_url", "namespace"],
    }
    outputs = {
        "name": "Namespace name",
        "state": "Namespace state (REGISTERED, DEPRECATED, DELETED)",
        "workflow_count": "Total workflow executions across all statuses",
        "groups": "Breakdown of workflow counts by execution status",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        temporal = sources.get("temporal", {})
        return bool(temporal.get("base_url"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {
            "base_url": temporal.get("base_url", ""),
            "api_key": temporal.get("api_key", ""),
            "namespace": temporal.get("namespace", "default"),
        }

    def run(
        self,
        base_url: str,
        api_key: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return tool_unavailable("temporal", "base_url is required to connect to Temporal.")

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.get_namespace_info()
            if not result.get("success"):
                return tool_unavailable(
                    "temporal", result.get("error", "Unknown error fetching namespace info.")
                )
            return {
                "source": "temporal",
                "available": True,
                "name": result["name"],
                "state": result["state"],
                "workflow_count": result["workflow_count"],
                "groups": result["groups"],
            }


temporal_namespace_info = TemporalNamespaceInfoTool()


# ======== from tools/temporal_task_queue_tool/ ========

"""Temporal task queue description tool."""


from core.tool import BaseTool


def _map_temporal_task_queue(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the active poller count and backlog stats.

    An empty poller list is itself the tool's primary failure signal (workers
    are down, per this tool's own docstring) -- unlike most list-based
    mappers, a genuine zero is always cited rather than treated as "nothing
    to report". ``total`` is only absent from the output when the required
    ``task_queue_name`` param was missing (an input-validation error, not a
    real zero), so its presence is the guard.
    """
    if not output.get("available") or output.get("error"):
        return
    total = output.get("total")
    if total is None:
        return
    parts = [f"{total} active poller(s)"]
    backlog = (output.get("stats") or {}).get("approximateBacklogCount")
    if backlog:
        parts.append(f"backlog {backlog}")
    record_evidence_entry(
        evidence,
        source="temporal_task_queue",
        label="Temporal Task Queue",
        summary=", ".join(parts),
    )


class TemporalTaskQueueTool(BaseTool):
    """Describe a task queue's pollers and backlog stats.

    After identifying failed workflows and the task queues they ran on, use this
    tool to check worker health. Empty pollers mean workers are down. A growing
    backlog (high approximateBacklogCount, tasksAddRate > tasksDispatchRate)
    means workers can't keep up. Stale lastAccessTime on pollers indicates
    workers have stopped heartbeating.

    Task queue names are discovered from workflow executions — each execution
    reports which task queue it ran on. The Temporal API does not expose a
    "list all task queues" endpoint.
    """

    name = "temporal_task_queue"
    source = "temporal"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_temporal_task_queue
    description = (
        "Describe a Temporal task queue: active worker pollers and backlog stats "
        "(approximate count, age, add/dispatch rates). Use after identifying failed "
        "workflows to check if workers are down or overwhelmed on that queue."
    )
    use_cases = [
        "Checking if workers are polling a task queue (are they alive?)",
        "Detecting worker outages (empty pollers list = no workers connected)",
        "Identifying backlog buildup (tasks queued faster than dispatched)",
        "Correlating workflow timeouts with stale worker heartbeats",
        "Verifying worker capacity after a deployment or scaling event",
    ]
    requires = ["base_url", "namespace"]
    injected_params = ["base_url", "api_key", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Temporal server base URL.",
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Temporal API key. Empty for unauthenticated self-hosted clusters.",
            },
            "namespace": {
                "type": "string",
                "default": "default",
                "description": "Temporal namespace.",
            },
            "task_queue_name": {
                "type": "string",
                "description": (
                    "Name of the task queue to inspect. Obtain this from the taskQueue "
                    "field in workflow execution results."
                ),
            },
        },
        "required": ["base_url", "namespace", "task_queue_name"],
    }
    outputs = {
        "pollers": "List of active worker pollers with identity, lastAccessTime, and ratePerSecond",
        "stats": "Backlog metrics: approximateBacklogCount, approximateBacklogAge, tasksAddRate, tasksDispatchRate",
        "total": "Number of active pollers on this queue",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        temporal = sources.get("temporal", {})
        return bool(temporal.get("base_url"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {
            "base_url": temporal.get("base_url", ""),
            "api_key": temporal.get("api_key", ""),
            "namespace": temporal.get("namespace", "default"),
        }

    def run(
        self,
        base_url: str,
        task_queue_name: str,
        api_key: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return tool_unavailable(
                "temporal", "base_url is required to connect to Temporal.", pollers=[], stats={}
            )
        if not task_queue_name:
            return {
                "source": "temporal",
                "available": True,
                "error": "task_queue_name is required. Get it from the taskQueue field in workflow execution results.",
                "pollers": [],
                "stats": {},
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.describe_task_queue(task_queue_name=task_queue_name)
            if not result.get("success"):
                return tool_unavailable(
                    "temporal",
                    result.get("error", "Unknown error describing task queue."),
                    pollers=[],
                    stats={},
                )
            return {
                "source": "temporal",
                "available": True,
                "pollers": result["pollers"],
                "stats": result["stats"],
                "total": result["total"],
            }


temporal_task_queue = TemporalTaskQueueTool()


# ======== from tools/temporal_workflow_history_tool/ ========

"""Temporal workflow execution history tool."""


from core.tool import BaseTool

_FAILURE_EVENT_TYPE_MARKERS = ("FAILED", "TIMED_OUT", "TERMINATED")


def _map_temporal_workflow_history(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the event count and how many are failure/timeout/termination events."""
    if not output.get("available") or output.get("error"):
        return
    events = output.get("events") or []
    if not events:
        return
    total = output.get("total", len(events))
    failure_events = sum(
        1
        for e in events
        if any(marker in str(e.get("eventType", "")) for marker in _FAILURE_EVENT_TYPE_MARKERS)
    )
    parts = [f"{total} event(s), {failure_events} failure/timeout/termination event(s)"]
    if output.get("archived"):
        parts.append("from archival storage")
    if output.get("next_page_token"):
        parts.append("more available")
    record_evidence_entry(
        evidence,
        source="temporal_workflow_history",
        label="Temporal Workflow History",
        summary=", ".join(parts),
    )


class TemporalWorkflowHistoryTool(BaseTool):
    """Fetch the event history for a specific workflow execution.

    After identifying a failed workflow via the workflows tool, use this to see
    the ordered sequence of events that tells the story of what happened:
    workflow started, activity scheduled, activity failed, workflow failed, etc.
    This is essential for diagnosing root cause — e.g. "the payment activity
    timed out after 3 retries" or "the child workflow was terminated externally."
    """

    name = "temporal_workflow_history"
    source = "temporal"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_temporal_workflow_history
    description = (
        "Fetch the event history for a specific Temporal workflow execution. "
        "Shows the ordered sequence of events (started, activity scheduled, "
        "activity failed, workflow failed, etc.) to diagnose why a workflow failed."
    )
    use_cases = [
        "Diagnosing why a specific workflow execution failed",
        "Identifying which activity within a workflow timed out or errored",
        "Tracing the sequence of events leading to workflow failure",
        "Checking if a workflow was terminated externally or failed internally",
        "Finding retry patterns that indicate transient vs persistent failures",
    ]
    requires = ["base_url", "namespace"]
    injected_params = ["base_url", "api_key", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Temporal server base URL.",
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Temporal API key. Empty for unauthenticated self-hosted clusters.",
            },
            "namespace": {
                "type": "string",
                "default": "default",
                "description": "Temporal namespace.",
            },
            "workflow_id": {
                "type": "string",
                "description": "The workflow ID to fetch history for.",
            },
            "run_id": {
                "type": "string",
                "default": "",
                "description": (
                    "Specific run ID. If omitted, returns history for the latest run "
                    "of the given workflow ID."
                ),
            },
            "next_page_token": {
                "type": "string",
                "default": "",
                "description": "Pagination token from a previous response to fetch the next page.",
            },
        },
        "required": ["base_url", "namespace", "workflow_id"],
    }
    outputs = {
        "events": "Ordered list of history events with eventId, eventTime, and eventType",
        "total": "Number of events returned in this page",
        "next_page_token": "Token for fetching the next page of events",
        "archived": "Whether the history was retrieved from archival storage",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        temporal = sources.get("temporal", {})
        return bool(temporal.get("base_url"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {
            "base_url": temporal.get("base_url", ""),
            "api_key": temporal.get("api_key", ""),
            "namespace": temporal.get("namespace", "default"),
        }

    def run(
        self,
        base_url: str,
        workflow_id: str,
        api_key: str = "",
        namespace: str = "default",
        run_id: str = "",
        next_page_token: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return tool_unavailable(
                "temporal", "base_url is required to connect to Temporal.", events=[]
            )
        if not workflow_id:
            return {
                "source": "temporal",
                "available": True,
                "error": "workflow_id is required to fetch execution history.",
                "events": [],
            }

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            result = client.get_workflow_history(
                workflow_id=workflow_id,
                run_id=run_id if run_id else None,
                next_page_token=next_page_token if next_page_token else None,
            )
            if not result.get("success"):
                return tool_unavailable(
                    "temporal",
                    result.get("error", "Unknown error fetching workflow history."),
                    events=[],
                )
            return {
                "source": "temporal",
                "available": True,
                "events": result["events"],
                "total": result["total"],
                "next_page_token": result["next_page_token"],
                "archived": result["archived"],
            }


temporal_workflow_history = TemporalWorkflowHistoryTool()


# ======== from tools/temporal_workflows_tool/ ========

"""Temporal workflow executions listing tool."""


from core.tool import BaseTool

#: Raw HTTP API executions use the WORKFLOW_EXECUTION_STATUS_* proto enum
#: naming, a different vocabulary from CountWorkflowExecutions' short status
#: names used by the namespace-info mapper above.
_FAILURE_STATUS_MARKERS = ("FAILED", "TIMED_OUT", "TERMINATED")


def _map_temporal_workflows(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the execution count and how many are in a failed/timed-out/terminated state."""
    if not output.get("available"):
        return
    executions = output.get("executions") or []
    if not executions:
        return
    total = output.get("total", len(executions))
    failed = sum(
        1
        for e in executions
        if any(marker in str(e.get("status", "")) for marker in _FAILURE_STATUS_MARKERS)
    )
    parts = [f"{total} execution(s), {failed} failed/timed-out/terminated"]
    if output.get("next_page_token"):
        parts.append("more available")
    record_evidence_entry(
        evidence,
        source="temporal_workflows",
        label="Temporal Workflows",
        summary=", ".join(parts),
    )


class TemporalWorkflowsTool(BaseTool):
    """List recent workflow executions with status and failure reason.

    After identifying a problem via namespace info (e.g. "8 workflows failed"),
    use this tool to see which specific workflows failed, when they started and
    closed, what type they are, and which task queue they ran on. The task queue
    name from these results feeds into the task queue tool for worker health checks.
    """

    name = "temporal_workflows"
    source = "temporal"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_temporal_workflows
    description = (
        "List recent Temporal workflow executions showing workflowId, type, status, "
        "taskQueue, and timing. Use after namespace info reveals failures, to identify "
        "which specific workflows failed and on which task queues."
    )
    use_cases = [
        "Listing recent workflow executions to find failures",
        "Identifying which workflow types are failing",
        "Discovering which task queues are involved in failures",
        "Getting workflowId and runId for detailed history inspection",
        "Correlating workflow failures with infrastructure alerts",
    ]
    requires = ["base_url", "namespace"]
    injected_params = ["base_url", "api_key", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Temporal server base URL.",
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Temporal API key. Empty for unauthenticated self-hosted clusters.",
            },
            "namespace": {
                "type": "string",
                "default": "default",
                "description": "Temporal namespace to query.",
            },
            "next_page_token": {
                "type": "string",
                "default": "",
                "description": "Pagination token from a previous response to fetch the next page.",
            },
        },
        "required": ["base_url", "namespace"],
    }
    outputs = {
        "executions": "List of workflow executions with workflowId, type, status, taskQueue, and timing",
        "total": "Number of executions returned in this page",
        "next_page_token": "Token for fetching the next page of results",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        temporal = sources.get("temporal", {})
        return bool(temporal.get("base_url"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        temporal = sources.get("temporal", {})
        return {
            "base_url": temporal.get("base_url", ""),
            "api_key": temporal.get("api_key", ""),
            "namespace": temporal.get("namespace", "default"),
        }

    def run(
        self,
        base_url: str,
        api_key: str = "",
        namespace: str = "default",
        next_page_token: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not base_url:
            return tool_unavailable(
                "temporal", "base_url is required to connect to Temporal.", executions=[]
            )

        config = TemporalConfig(base_url=base_url, api_key=api_key, namespace=namespace)
        with TemporalClient(config) as client:
            token = next_page_token if next_page_token else None
            result = client.list_workflow_executions(next_page_token=token)
            if not result.get("success"):
                return tool_unavailable(
                    "temporal",
                    result.get("error", "Unknown error listing workflow executions."),
                    executions=[],
                )
            return {
                "source": "temporal",
                "available": True,
                "executions": result["executions"],
                "total": result["total"],
                "next_page_token": result["next_page_token"],
            }


temporal_workflows = TemporalWorkflowsTool()
