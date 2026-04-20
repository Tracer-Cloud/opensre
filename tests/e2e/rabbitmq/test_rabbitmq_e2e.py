"""RabbitMQ E2E tests verifying integration with the investigation pipeline.

Coverage:
- RabbitMQ config resolution from store and env
- Source detection from alert annotations
- End-to-end flow: alert JSON → resolved_integrations → detect_sources →
  tools registered and callable.

All HTTP traffic is mocked; no real broker is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.integrations.catalog import classify_integrations
from app.integrations.rabbitmq import RabbitMQConfig
from app.nodes.plan_actions.detect_sources import detect_sources
from app.tools.registry import get_registered_tools

ALERT_PATH = Path(__file__).parent / "rabbitmq_alert.json"


# ---------------------------------------------------------------------------
# 1. Config resolution (store + env)
# ---------------------------------------------------------------------------


class TestRabbitMQConfigResolution:
    def test_resolution_from_store(self) -> None:
        integrations = [
            {
                "id": "rmq-prod",
                "service": "rabbitmq",
                "status": "active",
                "credentials": {
                    "host": "rmq.prod.internal",
                    "management_port": 15672,
                    "username": "sre_ro",
                    "password": "s3cr3t",
                    "vhost": "/orders",
                    "ssl": False,
                    "verify_ssl": True,
                },
            }
        ]
        resolved = classify_integrations(integrations)
        assert "rabbitmq" in resolved
        assert resolved["rabbitmq"]["host"] == "rmq.prod.internal"
        assert resolved["rabbitmq"]["management_port"] == 15672
        assert resolved["rabbitmq"]["username"] == "sre_ro"
        assert resolved["rabbitmq"]["vhost"] == "/orders"

    def test_amqp_service_alias_resolves_to_rabbitmq(self) -> None:
        integrations = [
            {
                "id": "amqp-prod",
                "service": "amqp",
                "status": "active",
                "credentials": {
                    "host": "rmq.prod.internal",
                    "username": "admin",
                },
            }
        ]
        resolved = classify_integrations(integrations)
        assert "rabbitmq" in resolved
        assert resolved["rabbitmq"]["host"] == "rmq.prod.internal"

    def test_missing_host_skipped(self) -> None:
        integrations = [
            {
                "id": "bad-rmq",
                "service": "rabbitmq",
                "status": "active",
                "credentials": {"host": "", "username": "admin"},
            }
        ]
        resolved = classify_integrations(integrations)
        assert resolved.get("rabbitmq") is None

    def test_missing_username_skipped(self) -> None:
        integrations = [
            {
                "id": "bad-rmq",
                "service": "rabbitmq",
                "status": "active",
                "credentials": {"host": "rmq.prod.internal", "username": ""},
            }
        ]
        resolved = classify_integrations(integrations)
        assert resolved.get("rabbitmq") is None


# ---------------------------------------------------------------------------
# 2. Source detection from alert annotations
# ---------------------------------------------------------------------------


class TestRabbitMQSourceDetection:
    def test_detect_source_populates_credentials(self) -> None:
        raw_alert = {
            "commonAnnotations": {
                "rabbitmq_cluster": "rmq@prod-01",
                "queue_name": "orders.process",
            }
        }
        resolved = {
            "rabbitmq": {
                "host": "rmq.prod.internal",
                "management_port": 15672,
                "username": "sre_ro",
                "password": "s3cr3t",
                "vhost": "/orders",
                "ssl": False,
                "verify_ssl": True,
            }
        }
        sources = detect_sources(raw_alert, {}, resolved)
        assert "rabbitmq" in sources
        assert sources["rabbitmq"]["host"] == "rmq.prod.internal"
        assert sources["rabbitmq"]["management_port"] == 15672
        assert sources["rabbitmq"]["username"] == "sre_ro"
        assert sources["rabbitmq"]["vhost"] == "/orders"
        assert sources["rabbitmq"]["connection_verified"] is True

    def test_missing_resolved_integration_no_source(self) -> None:
        raw_alert = {"commonAnnotations": {"rabbitmq_cluster": "rmq@prod-01"}}
        sources = detect_sources(raw_alert, {}, {})
        assert "rabbitmq" not in sources


# ---------------------------------------------------------------------------
# 3. Tool registration check — all 5 RabbitMQ tools are discoverable
# ---------------------------------------------------------------------------


class TestRabbitMQToolRegistration:
    def test_all_five_tools_registered(self) -> None:
        tool_names = {t.name for t in get_registered_tools() if t.source == "rabbitmq"}
        assert tool_names == {
            "get_rabbitmq_queue_backlog",
            "get_rabbitmq_consumer_health",
            "get_rabbitmq_broker_overview",
            "get_rabbitmq_node_health",
            "get_rabbitmq_connection_stats",
        }

    def test_tools_report_available_when_credentials_present(self) -> None:
        sources = {"rabbitmq": {"host": "rmq.prod.internal", "username": "admin"}}
        rmq_tools = [t for t in get_registered_tools() if t.source == "rabbitmq"]
        for tool in rmq_tools:
            if tool.is_available is not None:
                assert tool.is_available(sources) is True, tool.name


# ---------------------------------------------------------------------------
# 4. Full pipeline flow against the sample alert
# ---------------------------------------------------------------------------


class TestRabbitMQPipelineFlow:
    def test_alert_fixture_triggers_source_detection(self) -> None:
        """Loading the sample rabbitmq_alert.json drives detect_sources to
        populate sources['rabbitmq'] when credentials are present."""
        alert = json.loads(ALERT_PATH.read_text())
        resolved = {
            "rabbitmq": {
                "host": "rmq.prod.internal",
                "management_port": 15672,
                "username": "sre_ro",
                "password": "s3cr3t",
                "vhost": "/orders",
                "ssl": False,
                "verify_ssl": True,
            }
        }

        sources = detect_sources(alert, {}, resolved)
        assert "rabbitmq" in sources
        assert sources["rabbitmq"]["vhost"] == "/orders"

    def test_tool_invocation_with_mocked_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Call the queue-backlog tool end-to-end with a mocked Management
        API response and verify the evidence dict shape the pipeline expects."""

        from app.integrations import rabbitmq as rmq_module
        from app.tools.RabbitMQQueueBacklogTool import get_rabbitmq_queue_backlog

        def fake_client(config: RabbitMQConfig) -> httpx.Client:
            queues_payload: list[dict[str, Any]] = [
                {
                    "name": "orders.process",
                    "vhost": "/orders",
                    "state": "running",
                    "messages_ready": 50000,
                    "messages_unacknowledged": 2400,
                    "messages": 52400,
                    "consumers": 2,
                },
                {
                    "name": "billing.retry",
                    "vhost": "/orders",
                    "state": "running",
                    "messages_ready": 10,
                    "messages_unacknowledged": 0,
                    "messages": 10,
                    "consumers": 1,
                },
            ]

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/api/queues//orders":
                    return httpx.Response(200, json=queues_payload)
                return httpx.Response(404, text="not found")

            return httpx.Client(
                base_url=config.base_url,
                auth=(config.username, config.password),
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(rmq_module, "_get_client", fake_client)

        result = get_rabbitmq_queue_backlog(
            host="rmq.prod.internal",
            username="sre_ro",
            password="s3cr3t",
            management_port=15672,
            vhost="/orders",
            max_results=10,
        )

        assert result["source"] == "rabbitmq"
        assert result["available"] is True
        assert result["total_queues"] == 2
        # The orders.process queue (52k backlog) must be ranked first — this
        # is what the agent would surface as the top culprit.
        assert result["queues"][0]["name"] == "orders.process"
        assert result["queues"][0]["messages_total"] == 52400
