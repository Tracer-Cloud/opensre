"""Tests for RedisClientListTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.redis.tools.redis_client_list_tool import get_redis_client_list
from tests.tools.conftest import BaseToolContract


class TestRedisClientListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_redis_client_list.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_redis_client_list.__opensre_registered_tool__
    assert rt.name == "get_redis_client_list"
    assert rt.source == "redis"
    assert "chat" in rt.surfaces


def test_run_happy_path() -> None:
    fake = {"source": "redis", "available": True, "total_clients": 3, "blocked_clients": 1}
    with patch(
        "integrations.redis.tools.redis_client_list_tool.get_client_list", return_value=fake
    ) as mock_fn:
        result = get_redis_client_list(host="localhost")
    assert result["available"] is True
    assert result["blocked_clients"] == 1
    assert mock_fn.call_args.args[0].host == "localhost"


def test_run_error_propagated() -> None:
    with patch(
        "integrations.redis.tools.redis_client_list_tool.get_client_list",
        return_value={"source": "redis", "available": False, "error": "boom"},
    ):
        result = get_redis_client_list(host="invalid")
    assert result["available"] is False
    assert "error" in result
