"""Read-only client for Dynatrace's Grail DQL Query API."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.dynatrace import (
    DYNATRACE_POLL_INTERVAL_SECONDS,
    DYNATRACE_QUERY_TIMEOUT_SECONDS,
)
from infrastructure.observability.errors.service import capture_service_error
from integrations.dynatrace.config import DynatraceIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_EXECUTE_PATH = "/platform/storage/query/v1/query:execute"
_POLL_PATH = "/platform/storage/query/v1/query:poll"
_TERMINAL_FAILURE_STATES = {"CANCELLED", "FAILED"}


def _error(message: str, *, error_type: str) -> dict[str, Any]:
    return {"success": False, "error": message, "error_type": error_type}


def _http_error_type(status_code: int) -> str:
    if status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return "auth_error"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return "rate_limited"
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "server_error"
    return "http_error"


class DynatraceClient:
    """Synchronous client for bounded, read-only Grail queries."""

    def __init__(self, config: DynatraceIntegrationConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.config.tenant_url and self.config.platform_token)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.tenant_url,
                headers={
                    "Authorization": f"Bearer {self.config.platform_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout_seconds: float,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._get_client().request(
                method,
                path,
                json=json,
                params=params,
                timeout=max(1.0, timeout_seconds),
            )
        except httpx.TimeoutException:
            return _error("Dynatrace query timed out.", error_type="timeout")
        except httpx.RequestError as exc:
            capture_service_error(exc, logger=logger, integration="dynatrace", method="request")
            return _error(
                f"Could not reach Dynatrace: {type(exc).__name__}.",
                error_type="unreachable",
            )
        except Exception as exc:  # pragma: no cover - defensive client boundary
            capture_service_error(exc, logger=logger, integration="dynatrace", method="request")
            return _error("Unexpected error while calling Dynatrace.", error_type="unreachable")

        if response.status_code != HTTPStatus.OK:
            return _error(
                f"Dynatrace API returned HTTP {response.status_code}.",
                error_type=_http_error_type(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            return _error("Dynatrace returned a non-JSON response.", error_type="invalid_response")
        if not isinstance(payload, dict):
            return _error("Dynatrace returned an invalid response.", error_type="invalid_response")
        return {"success": True, "payload": payload}

    def query(
        self,
        dql: str,
        *,
        timeout_seconds: float = DYNATRACE_QUERY_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Execute DQL and poll until Grail returns records or a terminal error."""
        if not self.is_configured:
            return _error(
                "Missing Dynatrace tenant URL or platform token.",
                error_type="not_configured",
            )

        deadline = time.monotonic() + max(1.0, timeout_seconds)
        executed = self._request(
            "POST",
            _EXECUTE_PATH,
            json={"query": dql},
            timeout_seconds=timeout_seconds,
        )
        if not executed["success"]:
            return executed

        execute_payload = executed["payload"]
        request_token = execute_payload.get("requestToken")
        if not isinstance(request_token, str) or not request_token:
            return _error(
                "Dynatrace did not return a query request token.",
                error_type="invalid_response",
            )

        while time.monotonic() < deadline:
            polled = self._request(
                "GET",
                _POLL_PATH,
                params={"request-token": request_token},
                timeout_seconds=max(1.0, deadline - time.monotonic()),
            )
            if not polled["success"]:
                return polled
            payload = polled["payload"]
            state = str(payload.get("state", "")).upper()
            if state == "SUCCEEDED":
                result = payload.get("result")
                if not isinstance(result, dict):
                    return _error(
                        "Dynatrace returned no query result.",
                        error_type="invalid_response",
                    )
                records = result.get("records", [])
                if not isinstance(records, list):
                    return _error(
                        "Dynatrace returned invalid query records.",
                        error_type="invalid_response",
                    )
                return {
                    "success": True,
                    "records": [row for row in records if isinstance(row, dict)],
                    "metadata": result.get("metadata", {}),
                }
            if state in _TERMINAL_FAILURE_STATES:
                return _error(
                    f"Dynatrace query ended with state {state}.",
                    error_type="query_failed",
                )
            time.sleep(DYNATRACE_POLL_INTERVAL_SECONDS)

        return _error("Dynatrace query timed out.", error_type="timeout")

    def probe_access(self) -> ProbeResult:
        """Verify that the token can read logs from Grail."""
        if not self.is_configured:
            return ProbeResult.missing("Missing Dynatrace tenant URL or platform token.")
        outcome = self.query("fetch logs, from: -5m, scanLimitGBytes: 0.1 | limit 1")
        if not outcome.get("success"):
            return ProbeResult.failed(str(outcome.get("error", "Dynatrace verification failed.")))
        return ProbeResult.passed(f"Connected to Dynatrace Grail at {self.config.tenant_url}.")


__all__ = ["DynatraceClient"]
