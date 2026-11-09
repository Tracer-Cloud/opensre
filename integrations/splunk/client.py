"""Splunk REST API client for RCA log searches."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from infrastructure.observability.errors.service import capture_service_error
from infrastructure.observability.streaming import StreamingParseStats
from integrations.config_models import SplunkIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0


def build_splunk_spl_query(
    *,
    raw_query: str = "",
    index: str = "main",
    error_message: str = "",
    alert_name: str = "",
    trace_id: str = "",
    limit: int = 50,
) -> str:
    """Build an SPL query for RCA investigation.

    Priority:
      1. raw_query — operator supplied a verbatim SPL (from annotations.query)
      2. Construct from available signals (index + keyword filters)
    """
    if raw_query.strip():
        # Ensure a head clause to prevent runaway queries
        spl = raw_query.strip()
        if "| head " not in spl.lower():
            spl = f"{spl} | head {limit}"
        return spl

    # Build keyword filter from alert signals
    keyword = (error_message or alert_name or "").strip()
    if trace_id:
        keyword_clause = f'index={index} trace_id="{trace_id}"'
    elif keyword:
        # Escape double-quotes inside keyword
        safe_keyword = keyword.replace('"', '\\"')
        keyword_clause = f'index={index} "{safe_keyword}"'
    else:
        keyword_clause = f"index={index}"

    return f"search {keyword_clause} | head {limit}"


SplunkConfig = SplunkIntegrationConfig


class SplunkClient:
    """Synchronous Splunk REST API client using the export (streaming) endpoint."""

    def __init__(self, config: SplunkConfig) -> None:
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _export_url(self) -> str:
        return f"{self.config.base_url}/services/search/jobs/export"

    def search_logs(
        self,
        query: str,
        *,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Run a blocking SPL search via the export endpoint.

        Returns normalized log rows. The export endpoint streams results
        immediately — no job polling needed, making it ideal for RCA.
        """
        now = datetime.now(UTC)
        earliest = now - timedelta(minutes=max(int(time_range_minutes), 1))

        params = {
            "search": query if query.startswith("search ") else f"search {query}",
            "earliest_time": earliest.strftime("%Y-%m-%dT%H:%M:%S"),
            "latest_time": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "output_mode": "json",
            "count": str(limit),
        }

        try:
            response = httpx.post(
                self._export_url(),
                headers=self._headers(),
                data=params,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
                verify=self.config.ssl_verify,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            capture_service_error(exc, logger=logger, integration="splunk", method="search_logs")
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="splunk", method="search_logs")
            return {"success": False, "error": str(exc)}

        logs = self._parse_export_response(response.text)
        return {
            "success": True,
            "logs": logs,
            "total": len(logs),
            "query": query,
        }

    def _parse_export_response(self, response_text: str) -> list[dict[str, Any]]:
        """Parse the NDJSON export stream into normalized log dicts."""
        logs: list[dict[str, Any]] = []
        stats = StreamingParseStats()
        for line in response_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                stats.record_error(exc)
                continue
            stats.record_parsed()
            if not isinstance(payload, dict):
                continue
            result_type = payload.get("result")
            if isinstance(result_type, dict):
                logs.append(self._normalize_row(result_type))
        stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs/export")
        return logs

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Splunk result row to a common log shape."""
        return {
            "timestamp": str(row.get("_time", "")),
            "message": str(row.get("_raw", row.get("message", ""))),
            "level": str(row.get("log_level", row.get("severity", ""))),
            "source": str(row.get("source", "")),
            "host": str(row.get("host", "")),
            "sourcetype": str(row.get("sourcetype", "")),
            "index": str(row.get("index", "")),
            "raw": row,
        }

    def validate_access(self) -> dict[str, Any]:
        """Validate Splunk credentials by hitting the server info endpoint."""
        info_url = f"{self.config.base_url}/services/server/info"
        try:
            response = httpx.get(
                info_url,
                headers={"Authorization": f"Bearer {self.config.token}"},
                params={"output_mode": "json"},
                timeout=10.0,
                verify=self.config.ssl_verify,
            )
            response.raise_for_status()
            data = response.json()
            version = data.get("entry", [{}])[0].get("content", {}).get("version", "unknown")
            return {
                "success": True,
                "detail": f"Connected to Splunk {version}",
            }
        except httpx.HTTPStatusError as exc:
            capture_service_error(
                exc, logger=logger, integration="splunk", method="validate_access"
            )
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="splunk", method="validate_access"
            )
            return {"success": False, "error": str(exc)}

    def probe_access(self) -> ProbeResult:
        """Validate Splunk connectivity by calling the server info endpoint."""
        if not (self.config.base_url and self.config.token):
            return ProbeResult.missing("Missing base_url or token.")

        result = self.validate_access()
        if not result.get("success"):
            return ProbeResult.failed(
                f"Server info check failed: {result.get('error', 'unknown error')}"
            )
        return ProbeResult.passed(result.get("detail", "Connected."))
