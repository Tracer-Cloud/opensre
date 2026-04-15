"""HTTP client for remote LangGraph API agent deployments."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.remote.stream import StreamEvent, parse_sse_stream

logger = logging.getLogger(__name__)

DEFAULT_PORT = 2024
STREAM_TIMEOUT = 600.0
REQUEST_TIMEOUT = 30.0
PREFLIGHT_TIMEOUT = 5.0

SYNTHETIC_ALERT = (
    "ALERT: Pipeline 'etl_daily_orders' failed at 2025-06-15T08:32:00Z. "
    "Lambda function 'etl-daily-orders-processor' returned error: "
    "'SchemaValidationError: column order_total expected type decimal but got string'. "
    "CloudWatch log group: /aws/lambda/etl-daily-orders-processor. "
    "Please investigate the root cause."
)


@dataclass
class RemoteRunResult:
    """Collected result from a streamed remote investigation run."""

    thread_id: str
    events_received: int = 0
    node_names_seen: list[str] = field(default_factory=list)
    saw_end: bool = False
    final_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightResult:
    """Result of a quick health + capability check against a remote server."""

    ok: bool
    version: str = ""
    server_type: str = "unknown"
    endpoints: list[str] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    system: dict[str, Any] = field(default_factory=dict)

    @property
    def supports_stream(self) -> bool:
        return "/investigate/stream" in self.endpoints

    @property
    def supports_live_stream(self) -> bool:
        return self.supports_stream or "/threads/*/runs/stream" in self.endpoints

    @property
    def supports_investigate(self) -> bool:
        return "/investigate" in self.endpoints

    @property
    def supports_langgraph(self) -> bool:
        return self.server_type == "langgraph"

    @property
    def status_label(self) -> str:
        if not self.ok:
            return "unreachable"
        if self.server_type == "unknown":
            return "degraded"
        return "healthy"


def normalize_url(url: str) -> str:
    """Normalize a URL or bare IP into a full base URL.

    Accepts:
        - "http://1.2.3.4:2024" -> returned as-is
        - "1.2.3.4:2024"        -> "http://1.2.3.4:2024"
        - "1.2.3.4"             -> "http://1.2.3.4:2024"
    """
    url = url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    if url.count(":") == 1 and not url.split("//")[1].count(":"):
        url = f"{url}:{DEFAULT_PORT}"
    return url


class RemoteAgentClient:
    """Client for interacting with a remote LangGraph API deployment.

    The LangGraph API server (deployed on EC2, LangSmith, etc.) exposes:
      - GET  /ok                          Health check
      - POST /threads                     Create a conversation thread
      - POST /threads/{id}/runs/stream    Execute a run with SSE streaming
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = normalize_url(base_url)
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["x-api-key"] = api_key

    def health(self, *, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
        """Check the remote agent health endpoint.

        Returns the parsed JSON body from GET /ok.
        Raises httpx.HTTPStatusError on non-2xx responses.
        """
        url = f"{self.base_url}/ok"
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=self._headers)
            resp.raise_for_status()
            try:
                raw_data = resp.json()
            except ValueError:
                return {"ok": True, "raw": resp.text.strip()}
            if isinstance(raw_data, dict):
                return raw_data
            return {"ok": True, "raw": raw_data}

    def _coerce_json_dict(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"raw": response.text.strip()}
        if isinstance(payload, dict):
            return payload
        return {"raw": payload}

    def _fetch_ok_payload(self, client: httpx.Client) -> tuple[dict[str, Any], int]:
        ok_url = f"{self.base_url}/ok"
        started = time.monotonic()
        ok_resp = client.get(ok_url, headers=self._headers)
        ok_resp.raise_for_status()
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._coerce_json_dict(ok_resp), latency_ms

    def _fetch_remote_version(self, client: httpx.Client, fallback: str) -> tuple[str, str]:
        version_url = f"{self.base_url}/version"
        remote_version = fallback
        version_source = "/ok"
        try:
            version_resp = client.get(version_url, headers=self._headers)
            if version_resp.status_code == 200:
                version_data = self._coerce_json_dict(version_resp)
                parsed = str(version_data.get("version", "")).strip()
                if parsed:
                    remote_version = parsed
                    version_source = "/version"
        except Exception:  # noqa: BLE001
            return remote_version, version_source
        return remote_version, version_source

    def _fetch_deep_checks(self, client: httpx.Client) -> list[dict[str, str]]:
        deep_health_url = f"{self.base_url}/health/deep"
        checks: list[dict[str, str]] = []
        try:
            deep_resp = client.get(deep_health_url, headers=self._headers)
            if deep_resp.status_code != 200:
                return checks
            deep_data = self._coerce_json_dict(deep_resp)
            raw_checks = deep_data.get("checks")
            if not isinstance(raw_checks, list):
                return checks
            for check in raw_checks:
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name", "")).strip() or "Deep check"
                status = str(check.get("status", "unknown")).strip() or "unknown"
                detail = str(check.get("detail", "")).strip() or "-"
                checks.append(
                    {
                        "name": name,
                        "endpoint": "/health/deep",
                        "status": status,
                        "detail": detail,
                    }
                )
        except Exception:  # noqa: BLE001
            return []
        return checks

    def _aggregate_status(self, checks: list[dict[str, str]]) -> str:
        if any(str(check.get("status", "")).lower() in {"failed", "error"} for check in checks):
            return "failed"
        if any(
            str(check.get("status", "")).lower() in {"warn", "warning", "missing"}
            for check in checks
        ):
            return "warn"
        return "passed"

    def _endpoint_exists(self, client: httpx.Client, path: str) -> bool:
        url = f"{self.base_url}{path}"
        try:
            response = client.get(url, headers=self._headers)
        except Exception:  # noqa: BLE001
            return False
        return response.status_code != 404

    def _detect_server_type(self) -> tuple[str, list[str]]:
        endpoints: list[str] = []
        with httpx.Client(timeout=PREFLIGHT_TIMEOUT) as client:
            if self._endpoint_exists(client, "/investigate"):
                endpoints.append("/investigate")
            if self._endpoint_exists(client, "/investigate/stream"):
                endpoints.append("/investigate/stream")
            if self._endpoint_exists(client, "/investigations"):
                endpoints.append("/investigations")
            if self._endpoint_exists(client, "/threads"):
                endpoints.append("/threads")
            if self._endpoint_exists(client, "/threads/*/runs/stream"):
                endpoints.append("/threads/*/runs/stream")

        if any(endpoint.startswith("/investigate") for endpoint in endpoints):
            return "lightweight", endpoints
        if any(endpoint.startswith("/threads") for endpoint in endpoints):
            return "langgraph", endpoints
        return "unknown", endpoints

    def preflight(self, *, timeout: float = PREFLIGHT_TIMEOUT) -> PreflightResult:
        started = time.monotonic()
        try:
            payload = self.health(timeout=timeout)
            latency_ms = int((time.monotonic() - started) * 1000)

            ok = bool(payload.get("ok", True))
            version = str(payload.get("version", "")).strip()
            server_type = str(payload.get("server_type", "unknown") or "unknown")
            raw_system = payload.get("system")
            system: dict[str, Any] = {}
            if isinstance(raw_system, dict):
                system = {str(key): value for key, value in raw_system.items()}

            raw_endpoints = payload.get("endpoints")
            endpoints = (
                [str(ep) for ep in raw_endpoints if isinstance(ep, str)]
                if isinstance(raw_endpoints, list)
                else []
            )

            if not endpoints or server_type == "unknown":
                detected_type, detected_endpoints = self._detect_server_type()
                if not endpoints:
                    endpoints = detected_endpoints
                if server_type == "unknown":
                    server_type = detected_type

            return PreflightResult(
                ok=ok,
                version=version,
                server_type=server_type,
                endpoints=endpoints,
                latency_ms=latency_ms,
                system=system,
            )
        except httpx.TimeoutException:
            return PreflightResult(ok=False, error="connection timed out")
        except httpx.ConnectError:
            return PreflightResult(ok=False, error="connection refused")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            return PreflightResult(ok=False, error=f"HTTP {code}")
        except Exception as exc:  # noqa: BLE001
            return PreflightResult(ok=False, error=str(exc) or "unknown error")

    def probe_health(
        self,
        *,
        local_version: str,
        timeout: float = REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Run a deeper health probe and return a normalized report."""
        with httpx.Client(timeout=timeout) as client:
            ok_data, latency_ms = self._fetch_ok_payload(client)
            remote_version = str(ok_data.get("version", "")).strip()
            remote_version, version_source = self._fetch_remote_version(client, remote_version)
            deep_checks = self._fetch_deep_checks(client)

        version_status = "passed"
        version_detail = "Remote version matches local CLI"
        if not remote_version:
            version_status = "warn"
            version_detail = "Remote did not report a version."
        elif remote_version != local_version:
            version_status = "warn"
            version_detail = (
                f"Remote is {remote_version}; local CLI is {local_version}. Consider redeploying."
            )

        uptime = ok_data.get("uptime_seconds")
        uptime_detail = "No uptime data from server"
        uptime_status = "passed"
        missing_uptime = True
        if isinstance(uptime, int):
            uptime_status = "passed"
            uptime_detail = f"{uptime}s"
            missing_uptime = False

        checks = [
            {
                "name": "Liveness",
                "endpoint": "/ok",
                "status": "passed" if bool(ok_data.get("ok", True)) else "failed",
                "detail": "Remote server responded successfully",
            },
            {
                "name": "Version",
                "endpoint": version_source,
                "status": version_status,
                "detail": version_detail,
            },
            {
                "name": "Uptime",
                "endpoint": "/ok",
                "status": uptime_status,
                "detail": uptime_detail,
            },
        ]
        checks.extend(deep_checks)

        status = self._aggregate_status(checks)

        hints: list[str] = []
        if version_status == "warn":
            hints.append(version_detail)
        if missing_uptime:
            hints.append("Remote /ok endpoint does not expose uptime yet.")

        instance_id = ok_data.get("instance_id")
        region = ok_data.get("region")
        public_ip = ok_data.get("public_ip")

        return {
            "status": status,
            "base_url": self.base_url,
            "latency_ms": latency_ms,
            "local_version": local_version,
            "remote_version": remote_version or "unknown",
            "ok": bool(ok_data.get("ok", True)),
            "started_at": ok_data.get("started_at"),
            "uptime_seconds": uptime if isinstance(uptime, int) else None,
            "instance_id": str(instance_id) if instance_id else None,
            "region": str(region) if region else None,
            "public_ip": str(public_ip) if public_ip else None,
            "checks": checks,
            "hints": hints,
            "raw": ok_data,
        }

    def create_thread(self, *, timeout: float = REQUEST_TIMEOUT) -> str:
        """Create a new conversation thread.

        Returns the thread_id string.
        """
        url = f"{self.base_url}/threads"
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json={}, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()
            thread_id: str = data.get("thread_id", "")
            if not thread_id:
                raise ValueError(f"No thread_id in response: {data}")
            logger.info("Created thread: %s", thread_id)
            return thread_id

    def stream_investigation(
        self,
        thread_id: str,
        alert_payload: dict[str, Any],
        *,
        timeout: float = STREAM_TIMEOUT,
    ) -> Iterator[StreamEvent]:
        """Start an investigation run and stream events via SSE.

        Uses ``stream_mode: ["events"]`` to receive fine-grained events
        (tool calls, LLM reasoning, node transitions) from the LangGraph API.
        """
        url = f"{self.base_url}/threads/{thread_id}/runs/stream"
        body: dict[str, Any] = {
            "input": alert_payload,
            "config": {"metadata": {}},
            "stream_mode": ["events"],
        }

        with (
            httpx.Client(timeout=httpx.Timeout(timeout, connect=REQUEST_TIMEOUT)) as client,
            client.stream("POST", url, json=body, headers=self._headers) as resp,
        ):
            resp.raise_for_status()
            yield from parse_sse_stream(resp)

    def trigger_investigation(
        self,
        alert_payload: dict[str, Any] | None = None,
        *,
        timeout: float = STREAM_TIMEOUT,
    ) -> Iterator[StreamEvent]:
        """Convenience: create a thread, trigger an investigation, and stream results.

        If no alert_payload is provided, uses a built-in synthetic alert.
        """
        if alert_payload is None:
            alert_payload = _build_synthetic_payload()

        thread_id = self.create_thread()
        logger.info("Starting investigation on thread %s", thread_id)
        yield from self.stream_investigation(thread_id, alert_payload, timeout=timeout)

    def run_streamed_investigation(
        self,
        alert_payload: dict[str, Any] | None = None,
        *,
        timeout: float = STREAM_TIMEOUT,
    ) -> RemoteRunResult:
        """Run a streamed investigation and collect a structured result."""
        if alert_payload is None:
            alert_payload = _build_synthetic_payload()

        thread_id = self.create_thread(timeout=REQUEST_TIMEOUT)
        result = RemoteRunResult(thread_id=thread_id)

        for event in self.stream_investigation(thread_id, alert_payload, timeout=timeout):
            result.events_received += 1
            if event.event_type == "end":
                result.saw_end = True
            if event.node_name and event.node_name not in result.node_names_seen:
                result.node_names_seen.append(event.node_name)

            if event.event_type == "updates":
                if not event.node_name:
                    continue
                update = event.data.get(event.node_name, event.data)
                if isinstance(update, dict):
                    result.final_state.update(update)
            elif event.event_type == "events" and event.kind == "on_chain_end":
                output = event.data.get("data", {}).get("output", {})
                if isinstance(output, dict):
                    result.final_state.update(output)

        return result

    # ------------------------------------------------------------------
    # Lightweight server endpoints (app.remote.server)
    # ------------------------------------------------------------------

    def investigate(
        self,
        raw_alert: dict[str, Any],
        *,
        alert_name: str | None = None,
        pipeline_name: str | None = None,
        severity: str | None = None,
        timeout: float = STREAM_TIMEOUT,
    ) -> dict[str, Any]:
        """POST an alert to the lightweight investigation server.

        Returns the JSON response with ``id``, ``report``, ``root_cause``,
        and ``problem_md``.
        """
        url = f"{self.base_url}/investigate"
        body: dict[str, Any] = {"raw_alert": raw_alert}
        if alert_name:
            body["alert_name"] = alert_name
        if pipeline_name:
            body["pipeline_name"] = pipeline_name
        if severity:
            body["severity"] = severity

        with httpx.Client(timeout=httpx.Timeout(timeout, connect=REQUEST_TIMEOUT)) as client:
            resp = client.post(url, json=body, headers=self._headers)
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result

    def stream_investigate(
        self,
        raw_alert: dict[str, Any],
        *,
        alert_name: str | None = None,
        pipeline_name: str | None = None,
        severity: str | None = None,
        timeout: float = STREAM_TIMEOUT,
    ) -> Iterator[StreamEvent]:
        """Stream an investigation from the lightweight server's SSE endpoint.

        Uses ``POST /investigate/stream`` which returns the same SSE
        format as the LangGraph API, so the ``StreamRenderer`` can
        consume either server type identically.
        """
        url = f"{self.base_url}/investigate/stream"
        body: dict[str, Any] = {"raw_alert": raw_alert}
        if alert_name:
            body["alert_name"] = alert_name
        if pipeline_name:
            body["pipeline_name"] = pipeline_name
        if severity:
            body["severity"] = severity

        with (
            httpx.Client(timeout=httpx.Timeout(timeout, connect=REQUEST_TIMEOUT)) as client,
            client.stream("POST", url, json=body, headers=self._headers) as resp,
        ):
            resp.raise_for_status()
            yield from parse_sse_stream(resp)

    def list_investigations(self, *, timeout: float = REQUEST_TIMEOUT) -> list[dict[str, Any]]:
        """GET the list of persisted investigation ``.md`` files."""
        url = f"{self.base_url}/investigations"
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=self._headers)
            resp.raise_for_status()
            items: list[dict[str, Any]] = resp.json()
            return items

    def get_investigation(self, inv_id: str, *, timeout: float = REQUEST_TIMEOUT) -> str:
        """GET the markdown content of a single investigation."""
        url = f"{self.base_url}/investigations/{inv_id}"
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.text


def _build_synthetic_payload() -> dict[str, Any]:
    """Build the default synthetic alert payload for trigger tests."""
    return {
        "mode": "investigation",
        "alert_name": "etl-daily-orders-failure",
        "pipeline_name": "etl_daily_orders",
        "severity": "critical",
        "raw_alert": {"message": SYNTHETIC_ALERT},
    }
