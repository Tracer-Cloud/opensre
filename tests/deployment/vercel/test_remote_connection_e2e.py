"""End-to-end test: deploy to Vercel and exercise the remote connection path.

Validates that the ``RemoteAgentClient`` can reach a real Vercel deployment,
perform preflight checks, and hit the health / ok endpoints — the same flow
used by ``opensre remote`` CLI commands.

Requires ``VERCEL_API_TOKEN``.
Run with::

    pytest tests/deployment/vercel/test_remote_connection_e2e.py -v -s

Environment variables
~~~~~~~~~~~~~~~~~~~~~
``VERCEL_API_TOKEN``  (required)
    A Vercel personal-access token with Read/Write on Projects & Deployments.
    Create one at https://vercel.com/account/tokens.

``VERCEL_TEAM_ID``  (optional)
    Explicit team scope.  Auto-detected from the token when omitted.

Refs
~~~~
- GitHub issue: https://github.com/Tracer-Cloud/opensre/issues/390
- Follow-up to #273 (Vercel deploy) and #302 (CLI remote connection).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pytest

from app.remote.client import PreflightResult, RemoteAgentClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _health_url(base_url: str) -> str:
    """Build the ``/api/health`` URL for the Vercel deployment."""
    return f"{base_url.rstrip('/')}/api/health"


def _ok_url(base_url: str) -> str:
    """Build the ``/api/ok`` URL mirroring the remote server ``/ok`` endpoint."""
    return f"{base_url.rstrip('/')}/api/ok"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestVercelRemoteConnection:
    """Exercise the remote-connection flow against a live Vercel deployment.

    These tests use the ``vercel_deployment`` session fixture defined in
    ``conftest.py``, which provisions a real Vercel serverless deployment
    and tears it down automatically after the session.
    """

    # -- 1. Basic connectivity -------------------------------------------------

    def test_health_reachable_via_httpx(self, vercel_deployment: dict[str, Any]) -> None:
        """Sanity-check: the raw ``/api/health`` endpoint responds 200."""
        url = _health_url(vercel_deployment["DeploymentUrl"])
        with httpx.Client(timeout=30) as client:
            resp = client.get(url)

        assert resp.status_code == 200, (
            f"/api/health returned {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "opensre"
        logger.info("Raw health reachable: %s", body)

    def test_ok_endpoint_reachable(self, vercel_deployment: dict[str, Any]) -> None:
        """The ``/api/ok`` endpoint mirrors ``/ok`` for ``RemoteAgentClient``."""
        url = _ok_url(vercel_deployment["DeploymentUrl"])
        with httpx.Client(timeout=30) as client:
            resp = client.get(url)

        assert resp.status_code == 200, (
            f"/api/ok returned {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        assert body.get("ok") is True, f"Expected ok=true, got: {body}"
        logger.info("/api/ok reachable: %s", body)

    # -- 2. RemoteAgentClient health -------------------------------------------

    def test_remote_client_health(self, vercel_deployment: dict[str, Any]) -> None:
        """``RemoteAgentClient.health()`` succeeds against the Vercel URL.

        The client GETs ``<base>/ok``; the Vercel deployment routes this
        to the ``/api/ok`` serverless function.
        """
        base = vercel_deployment["DeploymentUrl"]
        client = RemoteAgentClient(base)
        data = client.health()

        assert data.get("ok") is True, f"Unexpected health payload: {data}"
        logger.info("RemoteAgentClient.health() OK: %s", data)

    # -- 3. Preflight check ----------------------------------------------------

    def test_preflight_succeeds(self, vercel_deployment: dict[str, Any]) -> None:
        """``RemoteAgentClient.preflight()`` returns a healthy result.

        Preflight is the first thing the CLI does when connecting to a
        remote agent.  It probes ``/ok``, discovers endpoints, and
        reports connectivity status — exactly the flow we want to
        validate end-to-end.
        """
        base = vercel_deployment["DeploymentUrl"]
        client = RemoteAgentClient(base)
        result: PreflightResult = client.preflight()

        assert result.ok, (
            f"Preflight failed: error={result.error!r}, "
            f"status={result.status_label}"
        )
        assert result.latency_ms >= 0, (
            f"Unexpected negative latency: {result.latency_ms}"
        )
        logger.info(
            "Preflight OK: status=%s server_type=%s latency=%dms endpoints=%s",
            result.status_label,
            result.server_type,
            result.latency_ms,
            result.endpoints,
        )

    # -- 4. Connection latency / reliability -----------------------------------

    def test_connection_latency_reasonable(
        self, vercel_deployment: dict[str, Any]
    ) -> None:
        """The round-trip latency to Vercel should be under 10 s.

        This is a generous upper bound; real latency should be well
        under 2 s for a cold-start serverless function.
        """
        base = vercel_deployment["DeploymentUrl"]
        client = RemoteAgentClient(base)

        start = time.monotonic()
        data = client.health()
        elapsed_ms = int((time.monotonic() - start) * 1000)

        assert data.get("ok") is True
        assert elapsed_ms < 10_000, (
            f"Health check took {elapsed_ms}ms — expected < 10 000 ms"
        )
        logger.info("Connection latency: %d ms", elapsed_ms)

    def test_multiple_requests_succeed(
        self, vercel_deployment: dict[str, Any]
    ) -> None:
        """Three consecutive health requests all succeed (no flakiness)."""
        base = vercel_deployment["DeploymentUrl"]
        client = RemoteAgentClient(base)

        for i in range(3):
            data = client.health()
            assert data.get("ok") is True, f"Request {i + 1} failed: {data}"
        logger.info("3/3 consecutive health requests succeeded")

    # -- 5. HTTPS enforcement --------------------------------------------------

    def test_deployment_uses_https(self, vercel_deployment: dict[str, Any]) -> None:
        """Vercel deployments must be served over HTTPS."""
        url = vercel_deployment["DeploymentUrl"]
        assert url.startswith("https://"), f"Expected HTTPS, got: {url}"

    # -- 6. Error surface / debug context --------------------------------------

    def test_connection_error_surfaces_context(self) -> None:
        """When the server is unreachable, ``preflight()`` exposes debug info.

        This does *not* require a live deployment — it verifies that
        failures produce actionable diagnostics (error string, status
        label) rather than silently swallowing exceptions.
        """
        client = RemoteAgentClient("https://this-host-does-not-exist.invalid")
        result = client.preflight()

        assert result.ok is False, "Expected preflight to report not-ok"
        assert result.error, "Error field should contain a diagnostic message"
        assert result.status_label == "unreachable"
        logger.info(
            "Unreachable diagnostics: error=%r status=%s",
            result.error,
            result.status_label,
        )

    def test_invalid_url_preflight_error(self) -> None:
        """Preflight against a valid host but wrong path still gives diagnostics."""
        # Use example.com which exists but doesn't run OpenSRE.
        client = RemoteAgentClient("https://example.com")
        result = client.preflight()

        # The host is reachable but doesn't serve /ok properly, so
        # preflight should report a non-healthy state with diagnostics.
        assert isinstance(result, PreflightResult)
        assert result.ok is False or result.error or result.status_label != "healthy", (
            f"Expected degraded/failed preflight against example.com, "
            f"got ok={result.ok} error={result.error!r} status={result.status_label}"
        )
        logger.info(
            "Wrong-host diagnostics: ok=%s error=%r status=%s",
            result.ok,
            result.error,
            result.status_label,
        )

    # -- 7. Response body validation -------------------------------------------

    def test_health_body_schema(self, vercel_deployment: dict[str, Any]) -> None:
        """The ``/api/health`` response matches the expected JSON schema."""
        url = _health_url(vercel_deployment["DeploymentUrl"])
        with httpx.Client(timeout=30) as client:
            resp = client.get(url)

        assert resp.status_code == 200, (
            f"/api/health returned {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        assert "status" in body, f"Missing 'status' key: {body}"
        assert "service" in body, f"Missing 'service' key: {body}"

    def test_ok_body_schema(self, vercel_deployment: dict[str, Any]) -> None:
        """The ``/api/ok`` response includes the ``ok`` flag expected by the client."""
        url = _ok_url(vercel_deployment["DeploymentUrl"])
        with httpx.Client(timeout=30) as client:
            resp = client.get(url)

        assert resp.status_code == 200, (
            f"/api/ok returned {resp.status_code}: {resp.text[:300]}"
        )
        body = resp.json()
        assert "ok" in body, f"Missing 'ok' key: {body}"
        assert body["ok"] is True
