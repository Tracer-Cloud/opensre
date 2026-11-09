"""PagerDuty REST API v2 client.

Wraps the PagerDuty API endpoints used for incident investigation, on-call
lookups, and service/escalation-policy discovery during RCA.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from infrastructure.observability.errors.service import capture_service_error
from integrations.config_models import PagerDutyIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30
PagerDutyConfig = PagerDutyIntegrationConfig


class PagerDutyClient:
    """Synchronous client for querying the PagerDuty REST API v2."""

    def __init__(self, config: PagerDutyConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def probe_access(self) -> ProbeResult:
        """Validate PagerDuty credentials with a minimal incidents list call."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API key.")

        try:
            with self:
                resp = self._get_client().get("/incidents", params={"limit": 1})
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return ProbeResult.failed(f"Connection failed: {exc.response.status_code}")
        except Exception as exc:
            return ProbeResult.failed(f"Connection failed: {exc}")

        return ProbeResult.passed(
            "Connected to PagerDuty; API key accepted.",
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> PagerDutyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def list_incidents(
        self,
        *,
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
        service_ids: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """List PagerDuty incidents, optionally filtered by status/urgency/service/time."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if statuses:
            params["statuses[]"] = statuses
        if urgencies:
            params["urgencies[]"] = urgencies
        if service_ids:
            params["service_ids[]"] = service_ids
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        try:
            resp = self._get_client().get("/incidents", params=params)
            resp.raise_for_status()
            data = resp.json()

            incidents = []
            for inc in data.get("incidents", []):
                incidents.append(
                    {
                        "id": inc.get("id", ""),
                        "incident_number": inc.get("incident_number"),
                        "title": inc.get("title", ""),
                        "status": inc.get("status", ""),
                        "urgency": inc.get("urgency", ""),
                        "priority": _extract_priority(inc),
                        "service": _extract_ref(inc.get("service")),
                        "escalation_policy": _extract_ref(inc.get("escalation_policy")),
                        "assigned_to": [
                            _extract_ref(a.get("assignee")) for a in inc.get("assignments", [])
                        ],
                        "created_at": inc.get("created_at", ""),
                        "updated_at": inc.get("last_status_change_at", ""),
                        "html_url": inc.get("html_url", ""),
                    }
                )

            return {"success": True, "incidents": incidents, "total": len(incidents)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incidents",
                extras={"statuses": statuses, "service_ids": service_ids},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incidents",
            )
            return {"success": False, "error": str(exc)}

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch full details for a specific PagerDuty incident."""
        try:
            resp = self._get_client().get(f"/incidents/{incident_id}")
            resp.raise_for_status()
            data = resp.json().get("incident", {})

            incident = {
                "id": data.get("id", ""),
                "incident_number": data.get("incident_number"),
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "status": data.get("status", ""),
                "urgency": data.get("urgency", ""),
                "priority": _extract_priority(data),
                "service": _extract_ref(data.get("service")),
                "escalation_policy": _extract_ref(data.get("escalation_policy")),
                "teams": [_extract_ref(t) for t in data.get("teams", [])],
                "assigned_to": [
                    _extract_ref(a.get("assignee")) for a in data.get("assignments", [])
                ],
                "acknowledgements": [
                    {
                        "acknowledger": _extract_ref(ack.get("acknowledger")),
                        "at": ack.get("at", ""),
                    }
                    for ack in data.get("acknowledgements", [])
                ],
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("last_status_change_at", ""),
                "resolved_at": data.get("resolved_at", ""),
                "html_url": data.get("html_url", ""),
                "alert_counts": data.get("alert_counts", {}),
            }

            return {"success": True, "incident": incident}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident",
                extras={"incident_id": incident_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_incident",
                extras={"incident_id": incident_id},
            )
            return {"success": False, "error": str(exc)}

    def list_incident_log_entries(self, incident_id: str, *, limit: int = 25) -> dict[str, Any]:
        """Fetch the activity log (timeline) for a specific PagerDuty incident."""
        params: dict[str, Any] = {"limit": min(limit, 100)}

        try:
            resp = self._get_client().get(f"/incidents/{incident_id}/log_entries", params=params)
            resp.raise_for_status()
            data = resp.json()

            log_entries = []
            for entry in data.get("log_entries", []):
                log_entries.append(
                    {
                        "id": entry.get("id", ""),
                        "type": entry.get("type", ""),
                        "summary": entry.get("summary", ""),
                        "created_at": entry.get("created_at", ""),
                        "agent": _extract_ref(entry.get("agent")),
                        "channel": entry.get("channel", {}),
                    }
                )

            return {"success": True, "log_entries": log_entries, "total": len(log_entries)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incident_log_entries",
                extras={"incident_id": incident_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_incident_log_entries",
                extras={"incident_id": incident_id},
            )
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # On-call
    # ------------------------------------------------------------------

    def get_oncalls(
        self,
        *,
        escalation_policy_ids: list[str] | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Fetch current on-call responders, optionally filtered by escalation policy."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if escalation_policy_ids:
            params["escalation_policy_ids[]"] = escalation_policy_ids

        try:
            resp = self._get_client().get("/oncalls", params=params)
            resp.raise_for_status()
            data = resp.json()

            oncalls = []
            for oc in data.get("oncalls", []):
                oncalls.append(
                    {
                        "user": _extract_ref(oc.get("user")),
                        "escalation_policy": _extract_ref(oc.get("escalation_policy")),
                        "escalation_level": oc.get("escalation_level"),
                        "schedule": _extract_ref(oc.get("schedule")),
                        "start": oc.get("start", ""),
                        "end": oc.get("end", ""),
                    }
                )

            return {"success": True, "oncalls": oncalls, "total": len(oncalls)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_oncalls",
                extras={"escalation_policy_ids": escalation_policy_ids},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_oncalls",
            )
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Services & escalation policies
    # ------------------------------------------------------------------

    def list_services(self, *, limit: int = 25) -> dict[str, Any]:
        """List PagerDuty services with their escalation policies."""
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "include[]": ["escalation_policies", "integrations"],
        }

        try:
            resp = self._get_client().get("/services", params=params)
            resp.raise_for_status()
            data = resp.json()

            services = []
            for svc in data.get("services", []):
                services.append(
                    {
                        "id": svc.get("id", ""),
                        "name": svc.get("name", ""),
                        "description": svc.get("description", ""),
                        "status": svc.get("status", ""),
                        "escalation_policy": _extract_ref(svc.get("escalation_policy")),
                        "teams": [_extract_ref(t) for t in svc.get("teams", [])],
                        "alert_creation": svc.get("alert_creation", ""),
                        "integrations": [
                            {
                                "id": i.get("id", ""),
                                "name": i.get("name", ""),
                                "type": i.get("type", ""),
                            }
                            for i in svc.get("integrations", [])
                        ],
                        "html_url": svc.get("html_url", ""),
                    }
                )

            return {"success": True, "services": services, "total": len(services)}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_services",
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="list_services",
            )
            return {"success": False, "error": str(exc)}

    def get_service(self, service_id: str) -> dict[str, Any]:
        """Fetch full details for a specific PagerDuty service including routing rules."""
        params: dict[str, Any] = {
            "include[]": ["escalation_policies", "integrations"],
        }

        try:
            resp = self._get_client().get(f"/services/{service_id}", params=params)
            resp.raise_for_status()
            data = resp.json().get("service", {})

            service = {
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "status": data.get("status", ""),
                "escalation_policy": _extract_ref(data.get("escalation_policy")),
                "teams": [_extract_ref(t) for t in data.get("teams", [])],
                "alert_creation": data.get("alert_creation", ""),
                "incident_urgency_rule": data.get("incident_urgency_rule", {}),
                "integrations": [
                    {
                        "id": i.get("id", ""),
                        "name": i.get("name", ""),
                        "type": i.get("type", ""),
                        "vendor": _extract_ref(i.get("vendor")),
                    }
                    for i in data.get("integrations", [])
                ],
                "html_url": data.get("html_url", ""),
            }

            return {"success": True, "service": service}
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_service",
                extras={"service_id": service_id},
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="pagerduty",
                method="get_service",
                extras={"service_id": service_id},
            )
            return {"success": False, "error": str(exc)}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_ref(obj: dict[str, Any] | None) -> dict[str, str]:
    """Extract a compact {id, summary, type} reference from a PagerDuty object ref."""
    if not obj:
        return {}
    return {
        "id": obj.get("id", ""),
        "summary": obj.get("summary", ""),
        "type": obj.get("type", ""),
    }


def _extract_priority(incident: dict[str, Any]) -> dict[str, str]:
    """Extract priority info from an incident, which may be null."""
    priority = incident.get("priority")
    if not priority:
        return {}
    return {
        "id": priority.get("id", ""),
        "name": priority.get("name", ""),
        "summary": priority.get("summary", ""),
    }


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


def make_pagerduty_client(
    api_key: str | None, base_url: str | None = None
) -> PagerDutyClient | None:
    """Create a PagerDutyClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        config_kwargs: dict[str, Any] = {"api_key": token}
        if base_url:
            config_kwargs["base_url"] = base_url
        return PagerDutyClient(PagerDutyConfig(**config_kwargs))
    except Exception as exc:
        logger.warning("Failed to create PagerDuty client: %s", exc, exc_info=True)
        return None
