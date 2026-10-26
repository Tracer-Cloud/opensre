"""Pin scoped SDK queries, bounded responses and connection ownership."""

from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from clickhouse_connect.driver import Client
from urllib3 import PoolManager

from core.telemetry.models import MetricMapping, Scope
from integrations.clickhouse import telemetry
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig, ClickHouseEvidenceReader


class TrackedStream(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


def _client_for(payload: bytes) -> MagicMock:
    client = MagicMock(spec=Client)

    def stream(*_args: Any, **_kwargs: Any) -> BytesIO:
        return BytesIO(payload)

    client.raw_stream.side_effect = stream
    return client


def test_parameters_are_bound_and_every_signal_has_scope_and_budgets() -> None:
    client = _client_for(b'{"data":[{"total":20,"errors":4,"latest":180,"invalid":0}]}')
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    scope = Scope("payment' OR 1=1 --", "demo", 150, 180)
    sample = reader.measure(scope, MetricMapping())
    reader.evidence(scope)
    assert sample.total == 20
    calls = client.raw_stream.call_args_list
    assert len(calls) == 3
    for call in calls:
        query = call.args[0]
        assert scope.service not in query
        assert "{service:String}" in query and "{environment:String}" in query
        assert "{start:Float64}" in query and "{end:Float64}" in query
        assert call.kwargs["parameters"]["service"] == scope.service
        assert call.kwargs["parameters"]["environment"] == scope.environment
        assert call.kwargs["parameters"]["start"] == scope.start
        assert call.kwargs["parameters"]["end"] == scope.end
        settings = call.kwargs["settings"]
        assert settings["readonly"] == "2"
        assert settings["max_execution_time"] == "5"
        assert settings["max_rows_to_read"] == "500000"
        assert settings["max_bytes_to_read"] == "50000000"
        assert settings["max_result_rows"] == "20"
        assert settings["result_overflow_mode"] == "throw"
        assert call.kwargs["fmt"] == "JSON"
        assert "secret" not in str(call)
    assert "LIMIT 10" in calls[1].args[0]
    assert "LIMIT 10" in calls[2].args[0]


def test_cumulative_counters_fail_closed() -> None:
    reader = ClickHouseEvidenceReader(
        ClickHouseEvidenceConfig(),
        "secret",
        client=_client_for(b'{"data":[{"total":20,"errors":4,"latest":180,"invalid":1}]}'),
    )
    with pytest.raises(ValueError, match="delta"):
        reader.measure(Scope("payment", "demo", 150, 180), MetricMapping())


def test_oversized_response_is_bounded_before_decoding_and_closed() -> None:
    stream = TrackedStream(b'{"data":["' + b"x" * 100000 + b'"]}')
    client = MagicMock(spec=Client)
    client.raw_stream.return_value = stream
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    with pytest.raises(ValueError, match="byte limit"):
        reader.evidence(Scope("payment", "demo", 150, 180))
    assert stream.read_sizes == [65537]
    assert stream.closed


def test_startup_rejects_admin_credentials() -> None:
    client = _client_for(b'{"data":[{"readonly":0}]}')
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    with pytest.raises(ValueError, match="read-only"):
        reader.verify()
    assert "readonly" not in client.raw_stream.call_args.kwargs["settings"]


def test_result_row_limit_rejects_small_but_excessive_payloads() -> None:
    stream = TrackedStream(b'{"data":[' + b",".join([b"{}"] * 21) + b"]}")
    client = MagicMock(spec=Client)
    client.raw_stream.return_value = stream
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    with pytest.raises(ValueError, match="row limit"):
        reader.evidence(Scope("payment", "demo", 150, 180))
    assert stream.closed


def test_owned_sdk_connection_is_lazy_direct_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_for(b'{"data":[{"readonly":2}]}')
    pool = MagicMock(spec=PoolManager)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(telemetry, "PoolManager", lambda: pool)
    monkeypatch.setattr(telemetry.clickhouse_connect, "get_client", factory)
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(host="::1", secure=True), "secret")
    factory.assert_not_called()
    reader.verify()
    kwargs = factory.call_args.kwargs
    assert kwargs["host"] == "[::1]"
    assert kwargs["pool_mgr"] is pool
    assert kwargs["secure"] is True
    assert kwargs["interface"] == "https"
    assert kwargs["connect_timeout"] == kwargs["send_receive_timeout"] == 8
    assert kwargs["password"] == "secret"
    assert kwargs["query_retries"] == 0
    assert kwargs["autogenerate_session_id"] is False
    reader.close()
    client.close.assert_called_once()
    pool.clear.assert_called_once()

    external = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    external.close()
    client.close.assert_called_once()


def test_sdk_initialization_failure_releases_its_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MagicMock(spec=PoolManager)
    factory = MagicMock(side_effect=OSError("connection failed"))
    monkeypatch.setattr(telemetry, "PoolManager", lambda: pool)
    monkeypatch.setattr(telemetry.clickhouse_connect, "get_client", factory)
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret")
    with pytest.raises(OSError, match="connection failed"):
        reader.verify()
    reader.close()
    pool.clear.assert_called_once()
