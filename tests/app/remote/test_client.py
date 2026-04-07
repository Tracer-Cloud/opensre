"""Tests for RemoteAgentClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.remote.client import (
    DEFAULT_PORT,
    PreflightResult,
    RemoteAgentClient,
    _build_synthetic_payload,
    normalize_url,
)
from app.remote.stream import StreamEvent


class TestNormalizeUrl:
    def test_full_url_passthrough(self) -> None:
        assert normalize_url("http://1.2.3.4:2024") == "http://1.2.3.4:2024"

    def test_https_passthrough(self) -> None:
        assert normalize_url("https://agent.example.com:2024") == "https://agent.example.com:2024"

    def test_bare_ip_adds_scheme_and_port(self) -> None:
        assert normalize_url("1.2.3.4") == f"http://1.2.3.4:{DEFAULT_PORT}"

    def test_ip_with_port_adds_scheme(self) -> None:
        assert normalize_url("1.2.3.4:2024") == "http://1.2.3.4:2024"

    def test_strips_trailing_slash(self) -> None:
        assert normalize_url("http://host:2024/") == "http://host:2024"

    def test_hostname_without_port(self) -> None:
        assert normalize_url("http://agent.local") == f"http://agent.local:{DEFAULT_PORT}"


class TestRemoteAgentClientInit:
    def test_base_url_normalized(self) -> None:
        client = RemoteAgentClient("10.0.0.1")
        assert client.base_url == f"http://10.0.0.1:{DEFAULT_PORT}"

    def test_api_key_header(self) -> None:
        client = RemoteAgentClient("http://host:2024", api_key="test-key")
        assert client._headers["x-api-key"] == "test-key"

    def test_no_api_key(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        assert "x-api-key" not in client._headers


class TestHealth:
    def test_health_success(self) -> None:
        health_data = {"ok": True, "version": "0.1.0"}
        mock_resp = MagicMock()
        mock_resp.json.return_value = health_data
        mock_resp.raise_for_status = MagicMock()

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            result = client.health()

        assert result == health_data
        mock_client.get.assert_called_once()

    def test_health_http_error_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock()
        )

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            with pytest.raises(httpx.HTTPStatusError):
                client.health()

    def test_health_non_json_response_returns_ok_payload(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "ok"

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            result = client.health()

        assert result == {"ok": True, "raw": "ok"}


class TestCreateThread:
    def test_returns_thread_id(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"thread_id": "t-123"}
        mock_resp.raise_for_status = MagicMock()

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            tid = client.create_thread()

        assert tid == "t-123"

    def test_missing_thread_id_raises(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            with pytest.raises(ValueError, match="No thread_id"):
                client.create_thread()


class TestBuildSyntheticPayload:
    def test_has_required_fields(self) -> None:
        payload = _build_synthetic_payload()
        assert payload["mode"] == "investigation"
        assert payload["alert_name"]
        assert payload["pipeline_name"]
        assert payload["severity"]
        assert isinstance(payload["raw_alert"], dict)


class TestRunStreamedInvestigation:
    def test_collects_stream_result(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        events = iter(
            [
                StreamEvent("metadata", data={"run_id": "r-1"}),
                StreamEvent("updates", node_name="extract_alert", data={"extract_alert": {"alert_name": "a"}}),
                StreamEvent(
                    "updates",
                    node_name="diagnose",
                    data={"diagnose": {"root_cause": "Schema mismatch"}},
                ),
                StreamEvent("end", data={}),
            ]
        )

        with (
            patch.object(client, "create_thread", return_value="thread-123"),
            patch.object(client, "stream_investigation", return_value=events),
        ):
            result = client.run_streamed_investigation()

        assert result.thread_id == "thread-123"
        assert result.events_received == 4
        assert result.saw_end is True
        assert result.node_names_seen == ["extract_alert", "diagnose"]
        assert result.final_state["root_cause"] == "Schema mismatch"


class TestPreflightResult:
    def test_supports_stream_present(self) -> None:
        r = PreflightResult(ok=True, endpoints=["/investigate", "/investigate/stream"])
        assert r.supports_stream is True

    def test_supports_stream_absent(self) -> None:
        r = PreflightResult(ok=True, endpoints=["/investigate"])
        assert r.supports_stream is False

    def test_supports_investigate(self) -> None:
        r = PreflightResult(ok=True, endpoints=["/investigate"])
        assert r.supports_investigate is True

    def test_supports_langgraph(self) -> None:
        r = PreflightResult(ok=True, server_type="langgraph")
        assert r.supports_langgraph is True

    def test_supports_live_stream_for_lightweight_endpoint(self) -> None:
        r = PreflightResult(ok=True, endpoints=["/investigate", "/investigate/stream"])
        assert r.supports_live_stream is True

    def test_supports_live_stream_for_langgraph_endpoint(self) -> None:
        r = PreflightResult(
            ok=True,
            server_type="langgraph",
            endpoints=["/threads", "/threads/*/runs/stream"],
        )
        assert r.supports_live_stream is True

    def test_supports_live_stream_absent(self) -> None:
        r = PreflightResult(ok=True, endpoints=["/investigate"])
        assert r.supports_live_stream is False

    def test_not_langgraph(self) -> None:
        r = PreflightResult(ok=True, server_type="lightweight")
        assert r.supports_langgraph is False

    def test_status_label_unreachable(self) -> None:
        r = PreflightResult(ok=False, error="connection refused")
        assert r.status_label == "unreachable"

    def test_status_label_healthy(self) -> None:
        r = PreflightResult(ok=True, server_type="lightweight")
        assert r.status_label == "healthy"

    def test_status_label_degraded(self) -> None:
        r = PreflightResult(ok=True, server_type="unknown")
        assert r.status_label == "degraded"


class TestPreflight:
    def test_preflight_healthy_with_capabilities(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        health_data = {
            "ok": True,
            "version": "0.5.2",
            "server_type": "lightweight",
            "endpoints": ["/investigate", "/investigate/stream", "/investigations"],
        }
        with patch.object(client, "health", return_value=health_data):
            result = client.preflight()

        assert result.ok is True
        assert result.version == "0.5.2"
        assert result.server_type == "lightweight"
        assert result.supports_stream is True
        assert result.supports_investigate is True
        assert result.latency_ms >= 0

    def test_preflight_old_server_no_capabilities_detects_lightweight(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        health_data = {"ok": True, "version": "0.4.0"}
        with (
            patch.object(client, "health", return_value=health_data),
            patch.object(
                client,
                "_detect_server_type",
                return_value=("lightweight", ["/investigate"]),
            ),
        ):
            result = client.preflight()

        assert result.ok is True
        assert result.server_type == "lightweight"
        assert result.supports_stream is False
        assert result.supports_investigate is True

    def test_preflight_old_server_detects_langgraph(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        health_data = {"ok": True}
        with (
            patch.object(client, "health", return_value=health_data),
            patch.object(
                client,
                "_detect_server_type",
                return_value=("langgraph", ["/threads", "/threads/*/runs/stream"]),
            ),
        ):
            result = client.preflight()

        assert result.ok is True
        assert result.server_type == "langgraph"
        assert result.supports_langgraph is True

    def test_preflight_timeout(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        with patch.object(client, "health", side_effect=httpx.TimeoutException("timed out")):
            result = client.preflight()

        assert result.ok is False
        assert result.error == "connection timed out"

    def test_preflight_connection_refused(self) -> None:
        client = RemoteAgentClient("http://host:2024")
        with patch.object(
            client, "health", side_effect=httpx.ConnectError("refused")
        ):
            result = client.preflight()

        assert result.ok is False
        assert "connection refused" in (result.error or "")

    def test_preflight_http_error(self) -> None:
        resp = MagicMock()
        resp.status_code = 403
        client = RemoteAgentClient("http://host:2024")
        with patch.object(
            client,
            "health",
            side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=resp),
        ):
            result = client.preflight()

        assert result.ok is False
        assert "403" in (result.error or "")
