"""Observe log query tool using the OPAL pipeline API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate

_DEFAULT_LIMIT = 50
_MAX_HARD_LIMIT = 200
_OPAL_SUMMARY_LEN = 80


def _map_observe_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    parts = [f"{count_label} record(s)"]
    opal = truncate(
        str(output.get("opal", "")).replace("\n", " "),
        _OPAL_SUMMARY_LEN,
    )
    if opal:
        parts.append(f"query '{opal}'")
    record_evidence_entry(
        evidence,
        source="query_observe_logs",
        label="Observe Logs",
        summary=", ".join(parts),
    )


def _observe_available(sources: dict[str, dict[str, Any]]) -> bool:
    obs = sources.get("observe", {})
    return bool(
        obs.get("connection_verified")
        and obs.get("customer_id")
        and obs.get("api_token")
    )


def _observe_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    obs = sources["observe"]
    customer_id = str(obs.get("customer_id", "")).strip()
    base_url = str(obs.get("base_url", "")).strip() or f"https://{customer_id}.observeinc.com"
    return {
        "base_url": base_url,
        "customer_id": customer_id,
        "api_token": str(obs.get("api_token", "")).strip(),
        "dataset": str(obs.get("dataset", "")).strip(),
        "time_range_minutes": int(obs.get("time_range_minutes", 60) or 60),
        "limit": _DEFAULT_LIMIT,
        "integration_id": str(obs.get("integration_id", "")).strip(),
    }


def _call_query(
    base_url: str,
    customer_id: str,
    api_token: str,
    opal: str,
    dataset: str,
    time_range_minutes: int,
    limit: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=max(1, time_range_minutes))
    stage: dict[str, Any] = {"pipeline": opal}
    if dataset:
        stage["input"] = [{"inputName": "main", "datasetId": dataset}]
    payload: dict[str, Any] = {
        "stages": [stage],
        "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rowCount": limit,
    }
    resp = httpx.post(
        f"{base_url.rstrip('/')}/v1/meta/export/query",
        json=payload,
        headers={"Authorization": f"Bearer {customer_id} {api_token}"},
        timeout=max(1.0, timeout_seconds),
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


@tool(
    name="query_observe_logs",
    description="Query Observe (observe.inc) logs using an OPAL pipeline statement.",
    source="observe",
    surfaces=(ToolSurface.CHAT,),
    requires=["customer_id", "api_token"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "customer_id": {"type": "string"},
            "api_token": {"type": "string"},
            "opal": {
                "type": "string",
                "description": "OPAL pipeline — e.g. 'filter level = \"error\"'",
            },
            "dataset": {"type": "string", "default": ""},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["customer_id", "api_token", "opal"],
    },
    is_available=_observe_available,
    injected_params=("base_url", "customer_id"),
    extract_params=_observe_extract_params,
    evidence_mapper=_map_observe_logs,
)
def query_observe_logs(
    base_url: str = "",
    customer_id: str = "",
    api_token: str = "",
    opal: str = "",
    dataset: str = "",
    time_range_minutes: int = 60,
    limit: int = _DEFAULT_LIMIT,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Observe via the OPAL export API."""
    cid = customer_id.strip()
    token = api_token.strip()
    if not cid or not token:
        return tool_unavailable("observe", "Missing customer_id or api_token.", records=[])
    base = base_url.strip().rstrip("/") or f"https://{cid}.observeinc.com"
    opal_query = opal.strip()
    if not opal_query:
        return tool_unavailable("observe", "No OPAL query provided.", records=[])

    effective_limit = max(1, min(limit, _MAX_HARD_LIMIT))
    try:
        body = _call_query(
            base_url=base,
            customer_id=cid,
            api_token=token,
            opal=opal_query,
            dataset=dataset.strip(),
            time_range_minutes=time_range_minutes,
            limit=effective_limit,
            timeout_seconds=timeout_seconds,
        )
    except Exception as err:
        report_run_error(
            err,
            tool_name="query_observe_logs",
            source="observe",
            component="integrations.observe.tools.observe_logs_tool",
            method="httpx.post",
            extras={"integration_id": integration_id},
        )
        return tool_unavailable("observe", str(err), records=[])

    rows = body if isinstance(body, list) else body.get("rows", [])
    records = [r for r in rows if isinstance(r, dict)][:effective_limit]
    return {
        "source": "observe",
        "available": True,
        "integration_id": integration_id,
        "opal": opal_query,
        "total_returned": len(records),
        "effective_limit": effective_limit,
        "records": records,
    }
