"""VictoriaLogs HTTP API client.

Wraps the LogsQL query endpoint used for log-evidence enrichment during
investigations. Credentials come from the user's VictoriaLogs integration
stored locally or via env vars (``VICTORIA_LOGS_URL``).

Auth: VictoriaLogs is typically deployed without auth on internal networks.
For multi-tenant deployments the ``AccountID`` header is sent only when
``tenant_id`` is explicitly configured — we never send ``AccountID: 0``
implicitly, since that targets the default tenant on every request.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_QUERY_PATH = "/select/logsql/query"


class VictoriaLogsConfig(StrictConfigModel):
    """Normalized VictoriaLogs connection settings."""

    base_url: str
    tenant_id: str | None = None
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _normalize_tenant_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @property
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/stream+json"}
        if self.tenant_id:
            headers["AccountID"] = self.tenant_id
        return headers


class VictoriaLogsClient:
    """Synchronous client for querying the VictoriaLogs LogsQL API."""

    def __init__(self, config: VictoriaLogsConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> VictoriaLogsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def query_logs(
        self,
        query: str,
        limit: int = 50,
        start: str = "-1h",
    ) -> dict[str, Any]:
        """Run a LogsQL query and return parsed log entries.

        Args:
            query: LogsQL query string (e.g. ``_stream_id:* AND error``).
            limit: Maximum number of log entries to return.
            start: Time range expression accepted by VictoriaLogs (e.g. ``-1h``).
        """
        if not self.config.base_url:
            return {
                "success": False,
                "error": "VictoriaLogs base_url is not configured.",
            }

        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "start": start,
        }

        try:
            resp = self._get_client().get(_QUERY_PATH, params=params)
            resp.raise_for_status()
            rows = _parse_ndjson(resp.text, limit=limit)
            return {"success": True, "rows": rows, "total": len(rows)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[victoria_logs] Query HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[victoria_logs] Query error: %s", e)
            return {"success": False, "error": str(e)}


def _parse_ndjson(text: str, *, limit: int) -> list[dict[str, Any]]:
    """Parse VictoriaLogs newline-delimited JSON into a list of dicts.

    Lines that fail to parse are silently skipped — VictoriaLogs may emit
    an empty trailing newline, and partial responses should not abort the
    entire result set.
    """
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
                if len(rows) >= limit:
                    break
    return rows


def make_victoria_logs_client(
    base_url: str | None,
    tenant_id: str | None = None,
) -> VictoriaLogsClient | None:
    """Create a ``VictoriaLogsClient`` if a valid base_url is provided."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return None
    try:
        return VictoriaLogsClient(VictoriaLogsConfig(base_url=url, tenant_id=tenant_id))
    except Exception:
        return None
