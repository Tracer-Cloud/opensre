"""Unit tests for the Better Stack integration module.

Mirrors the test_rabbitmq.py pattern: config layer + validation against
mocked ``httpx.MockTransport`` responses, no real Better Stack calls.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from app.integrations import betterstack as bs_module
from app.integrations.betterstack import (
    DEFAULT_BETTERSTACK_MAX_ROWS,
    DEFAULT_BETTERSTACK_TIMEOUT_S,
    BetterStackConfig,
    BetterStackValidationResult,
    betterstack_config_from_env,
    betterstack_extract_params,
    betterstack_is_available,
    build_betterstack_config,
    query_logs,
    validate_betterstack_config,
)

# ---------------------------------------------------------------------------
# Mock transport helper
# ---------------------------------------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def patched_sql_client(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch ``_sql_client`` to route through an ``httpx.MockTransport``.

    Returns a callable ``install(handler)`` that accepts a per-test request
    handler and wires it into the module under test.
    """

    def install(handler: Handler) -> None:
        def _fake_client(config: BetterStackConfig) -> httpx.Client:
            return httpx.Client(
                auth=(config.username, config.password),
                timeout=float(config.timeout_seconds),
                transport=httpx.MockTransport(handler),
            )

        monkeypatch.setattr(bs_module, "_sql_client", _fake_client)

    return install


def _configured() -> BetterStackConfig:
    return BetterStackConfig(
        query_endpoint="https://eu-nbg-2-connect.betterstackdata.com",
        username="u",
        password="p",
    )


class TestBetterStackConfig:
    def test_defaults(self) -> None:
        c = BetterStackConfig()
        assert c.query_endpoint == ""
        assert c.username == ""
        assert c.password == ""
        assert c.sources == []
        assert c.timeout_seconds == DEFAULT_BETTERSTACK_TIMEOUT_S
        assert c.max_rows == DEFAULT_BETTERSTACK_MAX_ROWS
        assert c.is_configured is False

    def test_is_configured_requires_endpoint_and_username(self) -> None:
        assert BetterStackConfig(query_endpoint="https://x", username="u").is_configured is True
        assert BetterStackConfig(query_endpoint="https://x").is_configured is False
        assert BetterStackConfig(username="u").is_configured is False

    def test_normalize_endpoint_strips_trailing_slash_and_whitespace(self) -> None:
        c = BetterStackConfig(query_endpoint="  https://eu-nbg-2-connect.betterstackdata.com/  ")
        assert c.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"

    def test_normalize_username_strips_whitespace(self) -> None:
        assert BetterStackConfig(username="  u  ").username == "u"

    def test_normalize_password_strips_via_parent_validator(self) -> None:
        # StrictConfigModel's wildcard string validator strips all fields
        # before the field-specific validator runs, including passwords.
        assert BetterStackConfig(password="  p  ").password == "p"

    def test_normalize_password_coerces_none(self) -> None:
        assert BetterStackConfig(password=None).password == ""  # type: ignore[arg-type]

    def test_sources_from_comma_string(self) -> None:
        c = BetterStackConfig(sources="t1_myapp, t2_gateway")
        assert c.sources == ["t1_myapp", "t2_gateway"]

    def test_sources_from_list(self) -> None:
        c = BetterStackConfig(sources=["t1_myapp", "t2_gateway"])
        assert c.sources == ["t1_myapp", "t2_gateway"]

    def test_sources_empty_string(self) -> None:
        assert BetterStackConfig(sources="").sources == []

    def test_sources_none(self) -> None:
        assert BetterStackConfig(sources=None).sources == []  # type: ignore[arg-type]

    def test_sources_strip_whitespace_and_drop_empty(self) -> None:
        assert BetterStackConfig(sources="a, , b").sources == ["a", "b"]

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
                "sources": "t1,t2",
            }
        )
        assert c.query_endpoint == "https://x"
        assert c.username == "u"
        assert c.sources == ["t1", "t2"]


class TestBetterStackConfigFromEnv:
    def test_returns_none_without_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BETTERSTACK_QUERY_ENDPOINT", raising=False)
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        assert betterstack_config_from_env() is None

    def test_returns_none_without_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BETTERSTACK_QUERY_ENDPOINT", "https://x")
        monkeypatch.delenv("BETTERSTACK_USERNAME", raising=False)
        assert betterstack_config_from_env() is None

    def test_loads_from_env_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "BETTERSTACK_QUERY_ENDPOINT",
            "https://eu-nbg-2-connect.betterstackdata.com",
        )
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        monkeypatch.setenv("BETTERSTACK_PASSWORD", "p")
        monkeypatch.setenv("BETTERSTACK_SOURCES", "t1_myapp,t2_gateway")
        c = betterstack_config_from_env()
        assert c is not None
        assert c.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"
        assert c.username == "u"
        assert c.password == "p"
        assert c.sources == ["t1_myapp", "t2_gateway"]

    def test_loads_without_optional_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BETTERSTACK_QUERY_ENDPOINT", "https://x")
        monkeypatch.setenv("BETTERSTACK_USERNAME", "u")
        monkeypatch.delenv("BETTERSTACK_SOURCES", raising=False)
        c = betterstack_config_from_env()
        assert c is not None
        assert c.sources == []


class TestBetterStackHelpers:
    def test_is_available_true_with_configured_sources(self) -> None:
        assert (
            betterstack_is_available(
                {
                    "betterstack": {
                        "query_endpoint": "https://x",
                        "username": "u",
                        "sources": ["t1_myapp"],
                    }
                }
            )
            is True
        )

    def test_is_available_true_with_source_hint(self) -> None:
        # Alert-derived source_hint (from betterstack_source annotation) is
        # sufficient to make the tool callable even without a configured
        # sources list.
        assert (
            betterstack_is_available(
                {
                    "betterstack": {
                        "query_endpoint": "https://x",
                        "username": "u",
                        "sources": [],
                        "source_hint": "t1_alert_inferred",
                    }
                }
            )
            is True
        )

    def test_is_available_false_without_endpoint(self) -> None:
        assert betterstack_is_available({"betterstack": {"username": "u"}}) is False

    def test_is_available_false_without_username(self) -> None:
        assert betterstack_is_available({"betterstack": {"query_endpoint": "https://x"}}) is False

    def test_is_available_false_when_source_missing(self) -> None:
        assert betterstack_is_available({}) is False

    def test_is_available_false_when_no_way_to_derive_source(self) -> None:
        # Credentials present but neither a configured sources list nor an
        # alert-derived source_hint — the executor has no path to propagate
        # a source to the tool, so availability must be False to prevent a
        # deterministic empty-source failure at call time.
        assert (
            betterstack_is_available(
                {
                    "betterstack": {
                        "query_endpoint": "https://x",
                        "username": "u",
                        "sources": [],
                    }
                }
            )
            is False
        )

    def test_extract_params_full(self) -> None:
        params = betterstack_extract_params(
            {
                "betterstack": {
                    "query_endpoint": "https://x",
                    "username": "u",
                    "password": "p",
                    "sources": ["t1"],
                    "source_hint": "t2_alert_inferred",
                }
            }
        )
        assert params == {
            "query_endpoint": "https://x",
            "username": "u",
            "password": "p",
            "sources": ["t1"],
            "source": "t2_alert_inferred",
        }

    def test_extract_params_defaults_when_missing(self) -> None:
        assert betterstack_extract_params({}) == {
            "query_endpoint": "",
            "username": "",
            "password": "",
            "sources": [],
            "source": "",
        }

    def test_extract_params_source_from_hint(self) -> None:
        # The alert-derived source_hint surfaces as the scalar ``source`` kwarg
        # so the executor can propagate alert context into the tool.
        params = betterstack_extract_params({"betterstack": {"source_hint": "t99_svc"}})
        assert params["source"] == "t99_svc"

    def test_extract_params_source_empty_when_no_hint(self) -> None:
        # When no alert hint is present, ``source`` is empty and the tool's
        # own fallback picks the first configured source (if any).
        params = betterstack_extract_params({"betterstack": {"sources": ["t1", "t2"]}})
        assert params["source"] == ""
        assert params["sources"] == ["t1", "t2"]

    def test_extract_params_sources_copy_not_alias(self) -> None:
        original = ["t1"]
        params = betterstack_extract_params({"betterstack": {"sources": original}})
        params["sources"].append("t2")
        assert original == ["t1"]


class TestValidateBetterStackConfig:
    def test_returns_not_configured_when_missing_creds(self) -> None:
        result = validate_betterstack_config(BetterStackConfig())
        assert isinstance(result, BetterStackValidationResult)
        assert result.ok is False
        assert "required" in result.detail.lower()

    def test_ok_on_200_probe(self, patched_sql_client) -> None:
        patched_sql_client(lambda _req: httpx.Response(200, text='{"1":1}\n'))
        result = validate_betterstack_config(_configured())
        assert result.ok is True

    def test_fails_on_empty_body(self, patched_sql_client) -> None:
        patched_sql_client(lambda _req: httpx.Response(200, text=""))
        result = validate_betterstack_config(_configured())
        assert result.ok is False
        assert "empty body" in result.detail.lower()

    def test_fails_on_401_with_auth_hint(self, patched_sql_client) -> None:
        patched_sql_client(lambda _req: httpx.Response(401, text="bad creds"))
        result = validate_betterstack_config(_configured())
        assert result.ok is False
        assert "authentication" in result.detail.lower()
        assert "BETTERSTACK_USERNAME" in result.detail

    def test_fails_on_404_with_endpoint_hint(self, patched_sql_client) -> None:
        patched_sql_client(lambda _req: httpx.Response(404, text="not found"))
        result = validate_betterstack_config(_configured())
        assert result.ok is False
        assert "endpoint" in result.detail.lower()
        assert "BETTERSTACK_QUERY_ENDPOINT" in result.detail

    def test_fails_on_500_includes_status_and_body(self, patched_sql_client) -> None:
        patched_sql_client(lambda _req: httpx.Response(500, text="boom"))
        result = validate_betterstack_config(_configured())
        assert result.ok is False
        assert "500" in result.detail
        assert "boom" in result.detail

    def test_fails_on_request_error(self, patched_sql_client) -> None:
        def _raiser(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failed")

        patched_sql_client(_raiser)
        result = validate_betterstack_config(_configured())
        assert result.ok is False
        assert "request failed" in result.detail.lower()
        assert "dns failed" in result.detail

    def test_probe_uses_required_wire_contract(self, patched_sql_client) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text='{"1":1}\n')

        patched_sql_client(_capturing)
        validate_betterstack_config(_configured())

        req = captured["req"]
        assert req.method == "POST"
        assert req.url.params.get("output_format_pretty_row_numbers") == "0"
        assert req.headers.get("content-type") == "text/plain"
        assert req.content == b"SELECT 1 FORMAT JSONEachRow"
        # Basic auth header is populated by httpx from the client's ``auth`` tuple.
        assert req.headers.get("authorization", "").lower().startswith("basic ")


class TestQueryLogs:
    def test_not_configured(self) -> None:
        result = query_logs(BetterStackConfig(), source="t1_x")
        assert result["available"] is False
        assert "not configured" in result["error"].lower()
        assert result["rows"] == []
        assert result["row_count"] == 0

    def test_invalid_source_name_rejected(self, patched_sql_client) -> None:
        called = {"n": 0}

        def _never(_req: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, text="")

        patched_sql_client(_never)
        result = query_logs(_configured(), source="t1; DROP TABLE users;--")
        assert result["available"] is False
        assert "invalid" in result["error"].lower()
        assert called["n"] == 0  # must not fire a request

    def test_empty_source_rejected(self) -> None:
        result = query_logs(_configured(), source="")
        assert result["available"] is False
        assert "invalid" in result["error"].lower()

    @pytest.mark.parametrize(
        "payload",
        [
            "t1; DROP TABLE users;--",
            "'; DROP TABLE logs--",
            "source OR 1=1",
            "../../../etc/passwd",
            "t1_logs UNION SELECT password FROM users",
            "t1_logs/*comment*/",
            "t1_logs -- trailing comment",
            "t1_logs`",
            "t1_logs\nDROP TABLE secrets",
            "t1_logs'",
        ],
    )
    def test_source_name_rejects_sql_injection_patterns(self, payload: str) -> None:
        assert bs_module._validate_source_name(payload) is None

    def test_invalid_since_rejected(self, patched_sql_client) -> None:
        patched_sql_client(lambda _r: httpx.Response(200, text=""))
        result = query_logs(_configured(), source="t1_myapp", since="not-a-timestamp")
        assert result["available"] is False
        assert "since" in result["error"].lower()

    def test_invalid_until_rejected(self, patched_sql_client) -> None:
        patched_sql_client(lambda _r: httpx.Response(200, text=""))
        result = query_logs(_configured(), source="t1_myapp", until="nope")
        assert result["available"] is False
        assert "until" in result["error"].lower()

    def test_happy_path_parses_jsoneachrow(self, patched_sql_client) -> None:
        body = (
            '{"dt":"2026-04-20T00:00:00Z","raw":"hello"}\n'
            '{"dt":"2026-04-20T00:00:01Z","raw":"world"}\n'
        )
        patched_sql_client(lambda _r: httpx.Response(200, text=body))
        result = query_logs(_configured(), source="t1_myapp")
        assert result["available"] is True
        assert result["betterstack_source"] == "t1_myapp"
        assert result["row_count"] == 2
        assert result["rows"][0]["raw"] == "hello"
        assert result["rows"][1]["raw"] == "world"

    def test_empty_body_is_zero_rows(self, patched_sql_client) -> None:
        patched_sql_client(lambda _r: httpx.Response(200, text=""))
        result = query_logs(_configured(), source="t1_myapp")
        assert result["available"] is True
        assert result["rows"] == []
        assert result["row_count"] == 0

    def test_skips_malformed_rows(self, patched_sql_client) -> None:
        body = '{"ok":1}\nnot-json\n{"ok":2}\n'
        patched_sql_client(lambda _r: httpx.Response(200, text=body))
        result = query_logs(_configured(), source="t1_myapp")
        assert result["row_count"] == 2
        assert result["rows"] == [{"ok": 1}, {"ok": 2}]

    def test_401_reported_as_auth(self, patched_sql_client) -> None:
        patched_sql_client(lambda _r: httpx.Response(401, text="nope"))
        result = query_logs(_configured(), source="t1_myapp")
        assert result["available"] is False
        assert "authentication" in result["error"].lower()

    def test_5xx_reports_status_and_body(self, patched_sql_client) -> None:
        patched_sql_client(lambda _r: httpx.Response(500, text="boom"))
        result = query_logs(_configured(), source="t1_myapp")
        assert result["available"] is False
        assert "500" in result["error"]
        assert "boom" in result["error"]

    def test_request_error_surfaced(self, patched_sql_client) -> None:
        def _raiser(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns failed")

        patched_sql_client(_raiser)
        result = query_logs(_configured(), source="t1_myapp")
        assert result["available"] is False
        assert "request failed" in result["error"].lower()

    def test_limit_clamped_to_config_max_rows(self, patched_sql_client) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text="")

        patched_sql_client(_capturing)
        config = BetterStackConfig(
            query_endpoint="https://x",
            username="u",
            password="p",
            max_rows=50,
        )
        result = query_logs(config, source="t1_myapp", limit=9999)
        assert result["limit"] == 50
        body = captured["req"].content.decode()
        assert "LIMIT 50" in body

    def test_negative_limit_clamped_to_one(self, patched_sql_client) -> None:
        # A negative ``limit`` must never reach the SQL as ``LIMIT -1`` — ClickHouse
        # rejects that with a 400 parse error. The integration clamps with max(1, ...).
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text="")

        patched_sql_client(_capturing)
        query_logs(_configured(), source="t1_myapp", limit=-1)
        body = captured["req"].content.decode()
        assert "LIMIT 1" in body
        assert "LIMIT -1" not in body

    def test_sql_unions_recent_and_historical(self, patched_sql_client) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text="")

        patched_sql_client(_capturing)
        query_logs(_configured(), source="t1_myapp")
        body = captured["req"].content.decode()
        # Both branches of the UNION must be present, with the expected suffixes.
        assert "remote(t1_myapp_logs)" in body
        assert "s3Cluster(primary, t1_myapp_s3)" in body
        assert "UNION ALL" in body
        # _row_type=1 always filters the s3 branch down to log rows.
        assert "_row_type = 1" in body
        assert body.rstrip().endswith("FORMAT JSONEachRow")

    def test_sql_contains_time_bounds_when_set(self, patched_sql_client) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text="")

        patched_sql_client(_capturing)
        query_logs(
            _configured(),
            source="t1_myapp",
            since="2026-04-20T00:00:00Z",
            until="2026-04-20T01:00:00Z",
        )
        body = captured["req"].content.decode()
        assert "dt >= parseDateTime64BestEffort('2026-04-20T00:00:00Z'" in body
        assert "dt <= parseDateTime64BestEffort('2026-04-20T01:00:00Z'" in body

    def test_sql_omits_time_bounds_when_unset(self, patched_sql_client) -> None:
        captured: dict[str, httpx.Request] = {}

        def _capturing(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(200, text="")

        patched_sql_client(_capturing)
        query_logs(_configured(), source="t1_myapp")
        body = captured["req"].content.decode()
        # No explicit time bounds in the WHERE clauses — the s3 branch still
        # carries _row_type = 1 but there's no parseDateTime64BestEffort call.
        assert "parseDateTime64BestEffort" not in body
        assert "ORDER BY dt DESC" in body
