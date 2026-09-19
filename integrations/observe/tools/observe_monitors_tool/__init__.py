"""Observe monitor listing tool."""

from __future__ import annotations

from typing import Any

import httpx

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable


def _map_observe_monitors(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    record_evidence_entry(
        evidence,
        source="list_observe_monitors",
        label="Observe Monitors",
        summary=f"{total} monitor(s)",
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
        "integration_id": str(obs.get("integration_id", "")).strip(),
    }


def _call_monitors(
    base_url: str,
    customer_id: str,
    api_token: str,
    state_filter: str,
    limit: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if state_filter:
        params["state"] = state_filter
    resp = httpx.get(
        f"{base_url.rstrip('/')}/v1/monitors",
        params=params,
        headers={"Authorization": f"Bearer {customer_id} {api_token}"},
        timeout=max(1.0, timeout_seconds),
    )
    resp.raise_for_status()
    body = resp.json()
    return body if isinstance(body, list) else body.get("monitors", [])  # type: ignore[no-any-return]


@tool(
    name="list_observe_monitors",
    description="List Observe (observe.inc) monitors and their current state.",
    source="observe",
    surfaces=(ToolSurface.CHAT,),
    requires=["customer_id", "api_token"],
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "customer_id": {"type": "string"},
            "api_token": {"type": "string"},
            "state_filter": {
                "type": "string",
                "description": "Filter by state, e.g. 'alerting' or 'ok'",
                "default": "",
            },
            "limit": {"type": "integer", "default": 50},
            "integration_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 20.0},
        },
        "required": ["customer_id", "api_token"],
    },
    is_available=_observe_available,
    injected_params=("base_url", "customer_id"),
    extract_params=_observe_extract_params,
    evidence_mapper=_map_observe_monitors,
)
def list_observe_monitors(
    base_url: str = "",
    customer_id: str = "",
    api_token: str = "",
    state_filter: str = "",
    limit: int = 50,
    integration_id: str = "",
    timeout_seconds: float = 20.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List monitors from Observe."""
    cid = customer_id.strip()
    token = api_token.strip()
    if not cid or not token:
        return tool_unavailable("observe", "Missing customer_id or api_token.", monitors=[])
    base = base_url.strip().rstrip("/") or f"https://{cid}.observeinc.com"

    try:
        monitors = _call_monitors(
            base_url=base,
            customer_id=cid,
            api_token=token,
            state_filter=state_filter.strip(),
            limit=max(1, min(limit, 200)),
            timeout_seconds=timeout_seconds,
        )
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_observe_monitors",
            source="observe",
            component="integrations.observe.tools.observe_monitors_tool",
            method="httpx.get",
            extras={"integration_id": integration_id},
        )
        return tool_unavailable("observe", str(err), monitors=[])

    return {
        "source": "observe",
        "available": True,
        "integration_id": integration_id,
        "total_returned": len(monitors),
        "monitors": monitors,
    }
