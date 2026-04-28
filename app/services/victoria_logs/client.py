from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx

from app.integrations.models import VictoriaLogsIntegrationConfig


class VictoriaLogsClient:
    def __init__(self, config: VictoriaLogsIntegrationConfig) -> None:
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url)

    def query_logs(self, query: str, limit: int = 50, start: str = "-1h") -> dict[str, Any]:
        """Synchronous query for logs."""
        url = f"{self.config.base_url.rstrip('/')}/select/logsql/query"
        params = {"query": query, "limit": str(limit), "start": start}
        headers = {}
        if self.config.tenant_id is not None:
            headers["AccountID"] = self.config.tenant_id

        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
            resp.raise_for_status()

            rows = []
            for line in resp.text.splitlines():
                if not line.strip():
                    continue
                with contextlib.suppress(Exception):
                    rows.append(json.loads(line))

            return {"success": True, "rows": rows}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def async_query_logs(
        self, query: str, limit: int = 50, start: str = "-1h"
    ) -> dict[str, Any]:
        """Asynchronous query for logs to avoid blocking the event loop."""
        url = f"{self.config.base_url.rstrip('/')}/select/logsql/query"
        params = {"query": query, "limit": str(limit), "start": start}
        headers = {}
        if self.config.tenant_id is not None:
            headers["AccountID"] = self.config.tenant_id

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, params=params, headers=headers, timeout=30.0)
                resp.raise_for_status()

                rows = []
                for line in resp.text.splitlines():
                    if not line.strip():
                        continue
                    with contextlib.suppress(Exception):
                        rows.append(json.loads(line))

                return {"success": True, "rows": rows}
            except Exception as e:
                return {"success": False, "error": str(e)}
