"""Unit tests for the Better Stack integration module.

Mirrors the test_rabbitmq.py pattern: config layer + helper coverage. HTTP
interaction is mocked via ``httpx.MockTransport`` in follow-up commits once
``validate_betterstack_config`` and ``query_logs`` land.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.betterstack import (
    DEFAULT_BETTERSTACK_MAX_ROWS,
    DEFAULT_BETTERSTACK_TIMEOUT_S,
    BetterStackConfig,
    betterstack_config_from_env,
    betterstack_extract_params,
    betterstack_is_available,
    build_betterstack_config,
)


class TestBetterStackConfig:
    def test_defaults(self) -> None:
        c = BetterStackConfig()
        assert c.query_endpoint == ""
        assert c.username == ""
        assert c.password == ""
        assert c.tables == []
        assert c.timeout_seconds == DEFAULT_BETTERSTACK_TIMEOUT_S
        assert c.max_rows == DEFAULT_BETTERSTACK_MAX_ROWS
        assert c.is_configured is False

    def test_is_configured_requires_endpoint_and_username(self) -> None:
        assert BetterStackConfig(
            query_endpoint="https://x", username="u"
        ).is_configured is True
        assert BetterStackConfig(query_endpoint="https://x").is_configured is False
        assert BetterStackConfig(username="u").is_configured is False

    def test_normalize_endpoint_strips_trailing_slash_and_whitespace(self) -> None:
        c = BetterStackConfig(
            query_endpoint="  https://eu-nbg-2-connect.betterstackdata.com/  "
        )
        assert c.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"

    def test_normalize_username_strips_whitespace(self) -> None:
        assert BetterStackConfig(username="  u  ").username == "u"

    def test_normalize_password_strips_via_parent_validator(self) -> None:
        # StrictConfigModel's wildcard string validator strips all fields
        # before the field-specific validator runs, including passwords.
        assert BetterStackConfig(password="  p  ").password == "p"

    def test_normalize_password_coerces_none(self) -> None:
        assert BetterStackConfig(password=None).password == ""  # type: ignore[arg-type]

    def test_tables_from_comma_string(self) -> None:
        c = BetterStackConfig(tables="t1_myapp_logs, t2_gateway_logs")
        assert c.tables == ["t1_myapp_logs", "t2_gateway_logs"]

    def test_tables_from_list(self) -> None:
        c = BetterStackConfig(tables=["t1_myapp_logs", "t2_gateway_logs"])
        assert c.tables == ["t1_myapp_logs", "t2_gateway_logs"]

    def test_tables_empty_string(self) -> None:
        assert BetterStackConfig(tables="").tables == []

    def test_tables_none(self) -> None:
        assert BetterStackConfig(tables=None).tables == []  # type: ignore[arg-type]

    def test_tables_strip_whitespace_and_drop_empty(self) -> None:
        assert BetterStackConfig(tables="a, , b").tables == ["a", "b"]

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            BetterStackConfig(timeout_seconds=0)

    def test_max_rows_exceeds_cap_raises(self) -> None:
        with pytest.raises(ValidationError):
            BetterStackConfig(max_rows=99_999)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BetterStackConfig(endpoint_url="https://x")  # type: ignore[call-arg]


class TestBuildBetterStackConfig:
    def test_empty_input(self) -> None:
        c = build_betterstack_config(None)
        assert c.query_endpoint == ""
        assert c.is_configured is False

    def test_dict_input(self) -> None:
        c = build_betterstack_config(
            {
                "query_endpoint": "https://x",
                "username": "u",
                "password": "p",
                "tables": "t1,t2",
            }
        )
        assert c.query_endpoint == "https://x"
        assert c.username == "u"
        assert c.tables == ["t1", "t2"]


class TestBetterStackConfigFromEnv:
    def test_returns_none_without_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BETTERSTACK_QUERY_ENDPOINT", raising=False)
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        assert betterstack_config_from_env() is None

    def test_returns_none_without_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BETTERSTACK_QUERY_ENDPOINT", "https://x")
        monkeypatch.delenv("BETTERSTACK_USERNAME", raising=False)
        assert betterstack_config_from_env() is None

    def test_loads_from_env_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "BETTERSTACK_QUERY_ENDPOINT",
            "https://eu-nbg-2-connect.betterstackdata.com",
        )
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        monkeypatch.setenv("BETTERSTACK_PASSWORD", "p")
        monkeypatch.setenv(
            "BETTERSTACK_TABLES", "t1_myapp_logs,t2_gateway_logs"
        )
        c = betterstack_config_from_env()
        assert c is not None
        assert c.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"
        assert c.username == "u"
        assert c.password == "p"
        assert c.tables == ["t1_myapp_logs", "t2_gateway_logs"]

    def test_loads_without_optional_tables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BETTERSTACK_QUERY_ENDPOINT", "https://x")
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        monkeypatch.delenv("BETTERSTACK_TABLES", raising=False)
        c = betterstack_config_from_env()
        assert c is not None
        assert c.tables == []


class TestBetterStackHelpers:
    def test_is_available_true(self) -> None:
        assert betterstack_is_available(
            {"betterstack": {"query_endpoint": "https://x", "username": "u"}}
        ) is True

    def test_is_available_false_without_endpoint(self) -> None:
        assert betterstack_is_available({"betterstack": {"username": "u"}}) is False

    def test_is_available_false_without_username(self) -> None:
        assert betterstack_is_available(
            {"betterstack": {"query_endpoint": "https://x"}}
        ) is False

    def test_is_available_false_when_source_missing(self) -> None:
        assert betterstack_is_available({}) is False

    def test_is_available_does_not_require_tables(self) -> None:
        # tables is an optional planner hint; its absence must NOT block availability
        assert betterstack_is_available(
            {"betterstack": {"query_endpoint": "https://x", "username": "u", "tables": []}}
        ) is True

    def test_extract_params_full(self) -> None:
        params = betterstack_extract_params(
            {
                "betterstack": {
                    "query_endpoint": "https://x",
                    "username": "u",
                    "password": "p",
                    "tables": ["t1"],
                }
            }
        )
        assert params == {
            "query_endpoint": "https://x",
            "username": "u",
            "password": "p",
            "tables": ["t1"],
        }

    def test_extract_params_defaults_when_missing(self) -> None:
        assert betterstack_extract_params({}) == {
            "query_endpoint": "",
            "username": "",
            "password": "",
            "tables": [],
        }

    def test_extract_params_tables_copy_not_alias(self) -> None:
        original = ["t1"]
        params = betterstack_extract_params(
            {"betterstack": {"tables": original}}
        )
        params["tables"].append("t2")
        assert original == ["t1"]
