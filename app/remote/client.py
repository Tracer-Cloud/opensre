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

    def preflight(self, *, timeout: float = PREFLIGHT_TIMEOUT) -> PreflightResult:
        """Quick health + capability detection.

        Calls ``GET /ok``, measures latency, and infers ``server_type``
        from the response.  If the server doesn't advertise endpoints
        (old version), probes ``/threads`` to detect a LangGraph deployment.
        """
        start = time.monotonic()
        try:
            data = self.health(timeout=timeout)
        except httpx.TimeoutException:
            return PreflightResult(ok=False, error="connection timed out")
        except httpx.ConnectError as exc:
            return PreflightResult(ok=False, error=f"connection refused ({exc})")
        except httpx.HTTPStatusError as exc:
            return PreflightResult(
                ok=False, error=f"HTTP {exc.response.status_code}"
            )
        except Exception as exc:  # noqa: BLE001
            return PreflightResult(ok=False, error=str(exc))

        latency = int((time.monotonic() - start) * 1000)
        version = str(data.get("version", ""))
        server_type = str(data.get("server_type", ""))
        endpoints: list[str] = data.get("endpoints") or []

        if not server_type and not endpoints:
            server_type, endpoints = self._detect_server_type(timeout=timeout)

        system: dict[str, Any] = data.get("system") or {}

        return PreflightResult(
            ok=True,
            version=version,
            server_type=server_type or "unknown",
            endpoints=endpoints,
            latency_ms=latency,
            system=system,
        )

    def _detect_server_type(
        self, *, timeout: float = PREFLIGHT_TIMEOUT
    ) -> tuple[str, list[str]]:
        """Probe endpoints to infer the server type when ``/ok`` omits capability info.

        Uses **read-only GET requests only** so that detection never triggers
        a real investigation.  FastAPI returns 405 (Method Not Allowed) for
        routes that exist but don't accept GET, and 404 for undefined routes.
        """
        route_exists = self._route_exists

        if route_exists("/investigations", timeout=timeout):
            endpoints = ["/investigate"]
            if route_exists("/investigate/stream", timeout=timeout):
                endpoints.append("/investigate/stream")
            return "lightweight", endpoints

        if route_exists("/threads", timeout=timeout):
            return "langgraph", ["/threads", "/threads/*/runs/stream"]

        return "unknown", []

    def _route_exists(self, path: str, *, timeout: float = PREFLIGHT_TIMEOUT) -> bool:
        """Check whether a server route exists using a read-only GET probe.

        Returns ``True`` when the server responds with anything other than 404,
        indicating the path is defined (even if GET isn't the right method).
        """
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(
                    f"{self.base_url}{path}", headers=self._headers
                )
                return resp.status_code != 404
        except Exception:  # noqa: BLE001
            return False

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
