"""Unit tests for the RabbitMQ integration module.

Mirrors the test_mariadb.py pattern: config layer + validation against
mocked httpx responses, no real broker connections.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.rabbitmq import (
    DEFAULT_RABBITMQ_MANAGEMENT_PORT,
    DEFAULT_RABBITMQ_VHOST,
    RabbitMQConfig,
    RabbitMQValidationResult,
    build_rabbitmq_config,
    get_broker_overview,
    get_connection_stats,
    get_consumer_health,
    get_node_health,
    get_queue_backlog,
    rabbitmq_config_from_env,
    rabbitmq_extract_params,
    rabbitmq_is_available,
    validate_rabbitmq_config,
)

# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


def _mock_transport(responses: dict[str, Any]) -> httpx.MockTransport:
    """Build an httpx MockTransport that returns fixed JSON per URL path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            payload = responses[path]
            if isinstance(payload, httpx.Response):
                return payload
            return httpx.Response(200, json=payload)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    """Patch the `_get_client` helper so validate/query functions use a MockTransport.

    Returns a callable (responses: dict) that installs the desired mock.
    """
    from app.integrations import rabbitmq as rmq_module

    def install(responses: dict[str, Any]) -> None:
        def _fake_client(config: RabbitMQConfig) -> httpx.Client:
            return httpx.Client(
                base_url=config.base_url,
                auth=(config.username, config.password),
                transport=_mock_transport(responses),
            )

        monkeypatch.setattr(rmq_module, "_get_client", _fake_client)

    return install


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestRabbitMQConfig:
    def test_defaults(self) -> None:
        config = RabbitMQConfig()
        assert config.host == ""
        assert config.management_port == DEFAULT_RABBITMQ_MANAGEMENT_PORT
        assert config.username == ""
        assert config.password == ""
        assert config.vhost == DEFAULT_RABBITMQ_VHOST
        assert config.ssl is False
        assert config.verify_ssl is True
        assert config.timeout_seconds == 10
        assert config.max_results == 50
        assert config.is_configured is False

    def test_is_configured_requires_host_and_username(self) -> None:
        assert RabbitMQConfig(host="h", username="u").is_configured is True
        assert RabbitMQConfig(host="h").is_configured is False
        assert RabbitMQConfig(username="u").is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        assert RabbitMQConfig(host="  broker.internal  ").host == "broker.internal"

    def test_normalize_username_none_becomes_empty(self) -> None:
        assert RabbitMQConfig(username=None).username == ""  # type: ignore[arg-type]

    def test_vhost_defaults_when_blank(self) -> None:
        assert RabbitMQConfig(vhost="").vhost == "/"
        assert RabbitMQConfig(vhost="   ").vhost == "/"

    def test_vhost_preserves_custom_value(self) -> None:
        assert RabbitMQConfig(vhost="/orders").vhost == "/orders"

    def test_management_port_invalid_falls_back(self) -> None:
        config = RabbitMQConfig(management_port="not-a-port")  # type: ignore[arg-type]
        assert config.management_port == DEFAULT_RABBITMQ_MANAGEMENT_PORT

    def test_management_port_string_int(self) -> None:
        config = RabbitMQConfig(management_port="15672")  # type: ignore[arg-type]
        assert config.management_port == 15672

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            RabbitMQConfig(host="h", username="u", timeout_seconds=0)

    def test_max_results_capped_at_200(self) -> None:
        with pytest.raises(ValidationError):
            RabbitMQConfig(host="h", username="u", max_results=201)

    def test_rejects_unknown_field(self) -> None:
        """StrictConfigModel must forbid typos."""
        with pytest.raises(ValidationError):
            RabbitMQConfig(host="h", username="u", bogus_field="x")  # type: ignore[call-arg]

    def test_base_url_uses_http_by_default(self) -> None:
        config = RabbitMQConfig(host="rmq", username="u")
        assert config.base_url == "http://rmq:15672"

    def test_base_url_uses_https_when_ssl_true(self) -> None:
        config = RabbitMQConfig(host="rmq", username="u", ssl=True)
        assert config.base_url == "https://rmq:15672"


class TestBuildRabbitMQConfig:
    def test_from_dict(self) -> None:
        config = build_rabbitmq_config({"host": "rmq", "username": "admin"})
        assert config.host == "rmq"
        assert config.username == "admin"

    def test_from_none_yields_defaults(self) -> None:
        config = build_rabbitmq_config(None)
        assert config.host == ""
        assert config.management_port == DEFAULT_RABBITMQ_MANAGEMENT_PORT


class TestRabbitMQConfigFromEnv:
    def test_returns_none_when_host_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RABBITMQ_HOST", raising=False)
        assert rabbitmq_config_from_env() is None

    def test_returns_none_when_username_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RABBITMQ_HOST", "rmq.internal")
        monkeypatch.delenv("RABBITMQ_USERNAME", raising=False)
        assert rabbitmq_config_from_env() is None

    def test_loads_all_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RABBITMQ_HOST", "rmq.internal")
        monkeypatch.setenv("RABBITMQ_MANAGEMENT_PORT", "15672")
        monkeypatch.setenv("RABBITMQ_USERNAME", "admin")
        monkeypatch.setenv("RABBITMQ_PASSWORD", "secret")
        monkeypatch.setenv("RABBITMQ_VHOST", "/prod")
        monkeypatch.setenv("RABBITMQ_SSL", "true")
        monkeypatch.setenv("RABBITMQ_VERIFY_SSL", "false")

        config = rabbitmq_config_from_env()
        assert config is not None
        assert config.host == "rmq.internal"
        assert config.username == "admin"
        assert config.password == "secret"
        assert config.vhost == "/prod"
        assert config.ssl is True
        assert config.verify_ssl is False

    def test_ssl_env_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RABBITMQ_HOST", "rmq")
        monkeypatch.setenv("RABBITMQ_USERNAME", "admin")
        monkeypatch.setenv("RABBITMQ_SSL", "TRUE")
        config = rabbitmq_config_from_env()
        assert config is not None
        assert config.ssl is True


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestRabbitMQHelpers:
    def test_is_available_true(self) -> None:
        assert rabbitmq_is_available({"rabbitmq": {"host": "h", "username": "u"}}) is True

    def test_is_available_requires_host(self) -> None:
        assert rabbitmq_is_available({"rabbitmq": {"host": "", "username": "u"}}) is False

    def test_is_available_requires_username(self) -> None:
        assert rabbitmq_is_available({"rabbitmq": {"host": "h", "username": ""}}) is False

    def test_is_available_missing_key(self) -> None:
        assert rabbitmq_is_available({}) is False

    def test_extract_params_preserves_known_keys(self) -> None:
        params = rabbitmq_extract_params(
            {
                "rabbitmq": {
                    "host": "h",
                    "management_port": 16672,
                    "username": "u",
                    "password": "p",
                    "vhost": "/prod",
                    "ssl": True,
                    "verify_ssl": False,
                }
            }
        )
        assert params == {
            "host": "h",
            "management_port": 16672,
            "username": "u",
            "password": "p",
            "vhost": "/prod",
            "ssl": True,
            "verify_ssl": False,
        }

    def test_extract_params_defaults_missing_keys(self) -> None:
        params = rabbitmq_extract_params({"rabbitmq": {}})
        assert params["management_port"] == DEFAULT_RABBITMQ_MANAGEMENT_PORT
        assert params["vhost"] == DEFAULT_RABBITMQ_VHOST
        assert params["ssl"] is False
        assert params["verify_ssl"] is True


# ---------------------------------------------------------------------------
# Validation + diagnostic query tests (mocked httpx)
# ---------------------------------------------------------------------------


CONFIGURED = RabbitMQConfig(host="rmq", username="admin", password="pw")


class TestValidateRabbitMQConfig:
    def test_missing_host_or_username_fails_without_http(self) -> None:
        result = validate_rabbitmq_config(RabbitMQConfig())
        assert isinstance(result, RabbitMQValidationResult)
        assert result.ok is False
        assert "required" in result.detail.lower()

    def test_happy_path(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/overview": {
                    "rabbitmq_version": "3.13.0",
                    "cluster_name": "rmq@node1",
                }
            }
        )
        result = validate_rabbitmq_config(CONFIGURED)
        assert result.ok is True
        assert "3.13.0" in result.detail
        assert "rmq@node1" in result.detail

    def test_404_surfaces_management_plugin_message(self, patched_client: Any) -> None:
        patched_client({"/api/overview": httpx.Response(404, text="Not Found")})
        result = validate_rabbitmq_config(CONFIGURED)
        assert result.ok is False
        assert "rabbitmq_management" in result.detail

    def test_401_surfaces_auth_message(self, patched_client: Any) -> None:
        patched_client({"/api/overview": httpx.Response(401, text="Unauthorized")})
        result = validate_rabbitmq_config(CONFIGURED)
        assert result.ok is False
        assert "authentication" in result.detail.lower()

    def test_500_surfaces_http_status(self, patched_client: Any) -> None:
        patched_client({"/api/overview": httpx.Response(500, text="boom")})
        result = validate_rabbitmq_config(CONFIGURED)
        assert result.ok is False
        assert "500" in result.detail


# ---------------------------------------------------------------------------
# Diagnostic query tests (mocked httpx) — smoke-level per function
# ---------------------------------------------------------------------------


class TestGetQueueBacklog:
    def test_returns_queues_sorted_by_backlog(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/queues//": [
                    {
                        "name": "small-q",
                        "vhost": "/",
                        "state": "running",
                        "messages_ready": 1,
                        "messages_unacknowledged": 0,
                        "messages": 1,
                        "consumers": 1,
                    },
                    {
                        "name": "huge-q",
                        "vhost": "/",
                        "state": "running",
                        "messages_ready": 9000,
                        "messages_unacknowledged": 1000,
                        "messages": 10000,
                        "consumers": 0,
                    },
                ]
            }
        )
        result = get_queue_backlog(CONFIGURED)
        assert result["available"] is True
        assert result["source"] == "rabbitmq"
        assert result["total_queues"] == 2
        # huge-q (10k backlog) should come before small-q (1)
        assert result["queues"][0]["name"] == "huge-q"
        assert result["queues"][1]["name"] == "small-q"

    def test_error_path_returns_available_false(self, patched_client: Any) -> None:
        patched_client({"/api/queues//": httpx.Response(500, text="fail")})
        result = get_queue_backlog(CONFIGURED)
        assert result["available"] is False
        assert "500" in result["error"]

    def test_unconfigured(self) -> None:
        result = get_queue_backlog(RabbitMQConfig())
        assert result["available"] is False
        assert result["error"] == "Not configured."


class TestGetConsumerHealth:
    def test_returns_consumers(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/consumers//": [
                    {
                        "consumer_tag": "amq.ctag-1",
                        "queue": {"name": "orders", "vhost": "/"},
                        "prefetch_count": 10,
                        "ack_required": True,
                        "active": True,
                        "channel_details": {
                            "name": "127.0.0.1:54321 -> rmq:5672 (1)",
                            "connection_name": "127.0.0.1:54321",
                            "peer_host": "127.0.0.1",
                        },
                    }
                ]
            }
        )
        result = get_consumer_health(CONFIGURED)
        assert result["available"] is True
        assert result["total_consumers"] == 1
        assert result["consumers"][0]["queue"] == "orders"
        assert result["consumers"][0]["prefetch_count"] == 10


class TestGetBrokerOverview:
    def test_includes_alarms(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/overview": {
                    "cluster_name": "rmq@node1",
                    "rabbitmq_version": "3.13.0",
                    "erlang_version": "26.0",
                    "queue_totals": {
                        "messages": 42,
                        "messages_ready": 40,
                        "messages_unacknowledged": 2,
                    },
                    "message_stats": {
                        "publish_details": {"rate": 10.0},
                        "deliver_get_details": {"rate": 8.5},
                    },
                    "object_totals": {
                        "queues": 3,
                        "consumers": 2,
                        "connections": 1,
                        "channels": 1,
                    },
                },
                "/api/healthchecks/alarms": {"status": "ok"},
            }
        )
        result = get_broker_overview(CONFIGURED)
        assert result["available"] is True
        assert result["rabbitmq_version"] == "3.13.0"
        assert result["messages_total"] == 42
        assert result["alarms"]["ok"] is True

    def test_alarm_failure_surfaces_detail(self, patched_client: Any) -> None:
        # RabbitMQ returns HTTP 503 (not 200) when alarms are active.
        patched_client(
            {
                "/api/overview": {
                    "cluster_name": "rmq@node1",
                    "rabbitmq_version": "3.13.0",
                    "queue_totals": {},
                    "object_totals": {},
                    "message_stats": {},
                },
                "/api/healthchecks/alarms": httpx.Response(
                    503,
                    json={
                        "status": "failed",
                        "reason": "resource alarm active on node rmq@node1",
                    },
                ),
            }
        )
        result = get_broker_overview(CONFIGURED)
        assert result["alarms"]["ok"] is False
        assert "resource alarm" in result["alarms"]["detail"]


class TestGetNodeHealth:
    def test_detects_partitions(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/nodes": [
                    {
                        "name": "rmq@node1",
                        "running": True,
                        "type": "disc",
                        "mem_used": 1024,
                        "mem_limit": 10240,
                        "mem_alarm": False,
                        "disk_free": 500000,
                        "disk_free_limit": 50000,
                        "disk_free_alarm": False,
                        "fd_used": 100,
                        "fd_total": 1000,
                        "sockets_used": 10,
                        "sockets_total": 100,
                        "proc_used": 500,
                        "proc_total": 1048576,
                        "partitions": ["rmq@node2"],
                    },
                    {
                        "name": "rmq@node2",
                        "running": True,
                        "partitions": [],
                    },
                ]
            }
        )
        result = get_node_health(CONFIGURED)
        assert result["available"] is True
        assert result["node_count"] == 2
        assert result["any_partitioned"] is True
        assert result["nodes"][0]["partitions"] == ["rmq@node2"]


class TestGetConnectionStats:
    def test_sorts_by_recv_rate(self, patched_client: Any) -> None:
        patched_client(
            {
                "/api/connections": [
                    {
                        "name": "slow",
                        "user": "u",
                        "vhost": "/",
                        "recv_oct_details": {"rate": 10.0},
                        "send_oct_details": {"rate": 5.0},
                    },
                    {
                        "name": "fast",
                        "user": "u",
                        "vhost": "/",
                        "recv_oct_details": {"rate": 1000.0},
                        "send_oct_details": {"rate": 500.0},
                    },
                ]
            }
        )
        result = get_connection_stats(CONFIGURED)
        assert result["available"] is True
        assert result["connections"][0]["name"] == "fast"
        assert result["connections"][1]["name"] == "slow"
        # broker_total_connections = all connections; vhost_connections = after filter.
        assert result["broker_total_connections"] == 2
        assert result["vhost_connections"] == 2

    def test_filters_by_vhost(self, patched_client: Any) -> None:
        """Connections from other vhosts are excluded from results."""
        patched_client(
            {
                "/api/connections": [
                    {
                        "name": "orders-conn",
                        "user": "u",
                        "vhost": "/orders",
                        "recv_oct_details": {"rate": 100.0},
                        "send_oct_details": {"rate": 50.0},
                    },
                    {
                        "name": "billing-conn",
                        "user": "u",
                        "vhost": "/billing",
                        "recv_oct_details": {"rate": 200.0},
                        "send_oct_details": {"rate": 100.0},
                    },
                ]
            }
        )
        config = RabbitMQConfig(host="rmq", username="admin", password="pw", vhost="/orders")
        result = get_connection_stats(config)
        assert result["available"] is True
        assert result["broker_total_connections"] == 2
        assert result["vhost_connections"] == 1
        assert result["connections"][0]["name"] == "orders-conn"
