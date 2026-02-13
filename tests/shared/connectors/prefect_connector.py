"""Prefect connector to emit task run events as tool events."""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List

import requests

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    token = os.getenv("PREFECT_API_KEY") or os.getenv("PREFECT_API_TOKEN") or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post_json(url: str, payload: dict, timeout: int = 15) -> dict | None:
    try:
        resp = requests.post(url, json=payload, timeout=timeout, headers=_auth_headers())
        if not resp.ok:
            logger.warning("Prefect API %s returned %s: %s", url, resp.status_code, resp.text[:200])
            return None
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("Prefect API %s request failed: %s", url, exc)
        return None


def _normalize_list_response(data: Any, key: str) -> list[dict]:
    if not data:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def _safe_time_range(tr: dict) -> tuple[str | None, str | None]:
    start_time = tr.get("start_time") or tr.get("expected_start_time")
    end_time = tr.get("end_time")
    if start_time and not end_time:
        end_time = start_time
    return start_time, end_time


def _exit_code_from_state(state_type: str | None) -> int:
    st = (state_type or "").lower()
    if st in {"completed", "success"}:
        return 0
    if st in {"running", "pending", "scheduled"}:
        return 0
    return 1


def fetch_flow_run(prefect_api_url: str, correlation_id: str) -> dict | None:
    """Fetch a Prefect flow run by name (correlation id)."""
    if not prefect_api_url or not correlation_id:
        return None

    url = f"{prefect_api_url.rstrip('/')}/flow_runs/filter"
    payload = {"flow_runs": {"name": {"any_": [correlation_id]}}}
    data = _post_json(url, payload)
    runs = _normalize_list_response(data, "flow_runs")
    if not runs:
        logger.warning("Prefect flow run not found for correlation_id=%s", correlation_id)
        return None
    return runs[0]


def fetch_task_runs(prefect_api_url: str, flow_run_id: str) -> list[dict]:
    """Fetch task runs for a given flow run id."""
    if not prefect_api_url or not flow_run_id:
        return []

    url = f"{prefect_api_url.rstrip('/')}/task_runs/filter"
    payload = {"task_runs": {"flow_run_id": {"any_": [flow_run_id]}}}
    data = _post_json(url, payload)
    return _normalize_list_response(data, "task_runs")


def task_runs_to_tool_events(
    task_runs: Iterable[dict],
    *,
    trace_id: str,
    run_id: str,
    run_name: str,
    flow_run: dict | None = None,
    prefect_api_url: str | None = None,
) -> List[dict[str, Any]]:
    """Map Prefect task runs into tool event payloads."""
    events: list[dict[str, Any]] = []
    flow_run_id = flow_run.get("id") if flow_run else None
    flow_run_name = flow_run.get("name") if flow_run else None

    for tr in task_runs:
        task_id = tr.get("id") or tr.get("task_id")
        task_name = tr.get("name") or tr.get("task_name") or "prefect_task"
        start_time, end_time = _safe_time_range(tr)
        if not start_time or not end_time:
            continue

        state_type = tr.get("state_type") or tr.get("state", {}).get("type")
        state_name = tr.get("state_name") or tr.get("state", {}).get("name")
        state_message = tr.get("state_message") or tr.get("state", {}).get("message")

        exit_code = _exit_code_from_state(state_type)

        metadata = {
            "prefect_flow_run_id": flow_run_id,
            "prefect_flow_run_name": flow_run_name,
            "prefect_task_run_id": task_id,
            "prefect_task_name": task_name,
            "prefect_state_type": state_type,
            "prefect_state_name": state_name,
            "prefect_state_message": state_message,
            "prefect_start_time": start_time,
            "prefect_end_time": end_time,
        }
        base_ui = prefect_api_url.rstrip("/") if prefect_api_url else None
        if base_ui and flow_run_id:
            metadata["prefect_flow_run_url"] = f"{base_ui}/runs/flow-run/{flow_run_id}"
        if base_ui and task_id:
            metadata["prefect_task_run_url"] = f"{base_ui}/runs/task-run/{task_id}"

        events.append(
            {
                "trace_id": trace_id,
                "run_id": run_id,
                "run_name": run_name,
                "tool_id": f"prefect_task:{task_id or task_name}",
                "tool_name": f"Prefect: {task_name}",
                "tool_cmd": "prefect.task",
                "start_time": start_time,
                "end_time": end_time,
                "exit_code": exit_code,
                "metadata": metadata,
            }
        )

    return events
