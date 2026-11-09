"""List Sentry uptime monitors for investigations and chat."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from integrations.sentry import (
    SentryConfig,
    build_sentry_config,
    sentry_config_from_env,
)
from integrations.sentry.uptime import list_sentry_uptime_monitors


def _map_list_sentry_uptime_alerts(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the monitor count and how many are currently down."""
    if not output.get("available"):
        return
    monitors = output.get("monitors") or []
    if not monitors:
        return
    down_count = output.get("down_count", 0)
    summary = f"{output.get('monitor_count', len(monitors))} uptime monitor(s)"
    if down_count:
        summary += f", {down_count} down"
    record_evidence_entry(
        evidence,
        source="list_sentry_uptime_alerts",
        label="Sentry Uptime Alerts",
        summary=summary,
    )


def _resolve_config(
    sentry_url: str | None,
    organization_slug: str | None,
    sentry_token: str | None,
    project_slug: str | None = None,
) -> SentryConfig | None:
    env_config = sentry_config_from_env()
    config = build_sentry_config(
        {
            "base_url": sentry_url or (env_config.base_url if env_config else ""),
            "organization_slug": organization_slug
            or (env_config.organization_slug if env_config else ""),
            "auth_token": sentry_token or (env_config.auth_token if env_config else ""),
            "project_slug": project_slug or (env_config.project_slug if env_config else ""),
        }
    )
    if not config.organization_slug or not config.auth_token:
        return None
    return config


def _sentry_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("sentry", {}).get("connection_verified"))


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        "organization_slug": sentry.get("organization_slug", ""),
        "sentry_token": sentry.get("sentry_token") or sentry.get("auth_token", ""),
        "sentry_url": sentry.get("sentry_url") or sentry.get("base_url") or "https://sentry.io",
        "project_slug": sentry.get("project_slug", ""),
    }


@tool(
    name="list_sentry_uptime_alerts",
    source="sentry",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "List Sentry uptime monitors and their current health (up/down). "
        "Use for downtime/alerts status — not for clustering error Issues."
    ),
    use_cases=[
        "Checking which domains or URLs are currently down in Sentry uptime",
        "Confirming whether a critical downtime alert is still firing",
        "Listing uptime monitors before or after a recovery",
    ],
    requires=["organization_slug", "sentry_token"],
    input_schema={
        "type": "object",
        "properties": {
            "organization_slug": {"type": "string"},
            "sentry_token": {"type": "string"},
            "sentry_url": {"type": "string", "default": ""},
            "project_slug": {"type": "string", "default": ""},
        },
        "required": ["organization_slug", "sentry_token"],
    },
    injected_params=("organization_slug", "sentry_token", "sentry_url", "project_slug"),
    is_available=_sentry_available,
    extract_params=_extract_params,
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_list_sentry_uptime_alerts,
)
def list_sentry_uptime_alerts(
    organization_slug: str,
    sentry_token: str,
    sentry_url: str = "",
    project_slug: str = "",
) -> dict[str, Any]:
    """Return normalized Sentry uptime monitor health."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {
            "source": "sentry",
            "available": False,
            "error": "Sentry organization_slug and auth token are required.",
            "monitors": [],
        }

    try:
        monitors = list_sentry_uptime_monitors(config=config)
    except RuntimeError as err:
        # HTTP failures are wrapped as RuntimeError by list_sentry_uptime_monitors.
        report_run_error(
            err,
            tool_name="list_sentry_uptime_alerts",
            source="sentry",
            component="integrations.sentry.tools.sentry_list_uptime_alerts_tool",
            method="list_sentry_uptime_monitors",
            severity="warning",
            extras={"organization_slug": config.organization_slug},
        )
        return {
            "source": "sentry",
            "available": False,
            "error": str(err),
            "monitors": [],
        }
    except Exception as err:
        report_run_error(
            err,
            tool_name="list_sentry_uptime_alerts",
            source="sentry",
            component="integrations.sentry.tools.sentry_list_uptime_alerts_tool",
            method="list_sentry_uptime_monitors",
            extras={"organization_slug": config.organization_slug},
        )
        return {
            "source": "sentry",
            "available": False,
            "error": f"Failed to list Sentry uptime monitors: {err}",
            "monitors": [],
        }

    down = [m for m in monitors if m.health == "down"]
    return {
        "source": "sentry",
        "available": True,
        "monitor_count": len(monitors),
        "down_count": len(down),
        "monitors": [
            {
                "id": m.id,
                "name": m.name,
                "url": m.url,
                "project_slug": m.project_slug,
                "health": m.health,
                "uptime_status": m.uptime_status,
                "status": m.status,
                "severity": "critical" if m.health == "down" else "ok",
            }
            for m in monitors
        ],
    }
