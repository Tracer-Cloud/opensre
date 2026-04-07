"""Tests for RemoteAgentClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.remote.client import (
    DEFAULT_PORT,
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


class TestProbeHealth:
    def test_probe_health_collects_metadata_and_deep_checks(self) -> None:
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {
            "ok": True,
            "version": "2026.4.3",
            "uptime_seconds": 120,
            "started_at": "2026-04-06T10:00:00+00:00",
            "instance_id": "i-123",
            "region": "us-east-1",
            "public_ip": "44.1.2.3",
        }

        version_resp = MagicMock()
        version_resp.status_code = 200
        version_resp.json.return_value = {"version": "2026.4.3"}

        deep_resp = MagicMock()
        deep_resp.status_code = 200
        deep_resp.json.return_value = {
            "status": "warn",
            "checks": [
                {"name": "Disk", "status": "passed", "detail": "33% used"},
                {"name": "Memory", "status": "warn", "detail": "91% used"},
            ],
        }

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [ok_resp, version_resp, deep_resp]
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            report = client.probe_health(local_version="2026.4.5")

        assert report["status"] == "warn"
        assert report["remote_version"] == "2026.4.3"
        assert report["instance_id"] == "i-123"
        assert report["region"] == "us-east-1"
        assert report["public_ip"] == "44.1.2.3"
        assert any(check["name"] == "Disk" for check in report["checks"])
        assert any(check["name"] == "Memory" for check in report["checks"])

    def test_probe_health_without_deep_endpoint_still_succeeds(self) -> None:
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"ok": True, "version": "2026.4.5"}

        version_resp = MagicMock()
        version_resp.status_code = 404
        version_resp.json.return_value = {}

        with patch("app.remote.client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [ok_resp, version_resp, httpx.ConnectError("missing")]
            mock_client_cls.return_value = mock_client

            client = RemoteAgentClient("http://host:2024")
            report = client.probe_health(local_version="2026.4.5")

        assert report["status"] == "passed"
        assert report["remote_version"] == "2026.4.5"
        assert any(
            check["name"] == "Uptime" and check["status"] == "passed" for check in report["checks"]
        )
        assert "Remote /ok endpoint does not expose uptime yet." in report["hints"]


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
                StreamEvent(
                    "updates",
                    node_name="extract_alert",
                    data={"extract_alert": {"alert_name": "a"}},
                ),
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
