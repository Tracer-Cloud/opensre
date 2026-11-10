"""Pinned evidence reads, population semantics, redaction and bounded SDK failures."""

from __future__ import annotations

import json
from dataclasses import asdict
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from clickhouse_connect.driver import Client
from clickhouse_connect.driver.exceptions import DatabaseError
from urllib3.response import HTTPResponse

from core.telemetry.evidence_queries import InvestigationScope
from core.telemetry.models import MetricMapping, Scope
from integrations.clickhouse.investigation_queries import ClickHouseInvestigationReader
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig


def _scope() -> InvestigationScope:
    return InvestigationScope(
        Scope("payment' OR 1=1 --", "demo", 150, 180), Scope("payment' OR 1=1 --", "demo", 120, 150)
    )


def _client(*responses: list[dict[str, Any]] | Exception | bytes) -> MagicMock:
    client = MagicMock(spec=Client)
    remaining = iter(responses)

    def stream(*_args: Any, **_kwargs: Any) -> BytesIO:
        response = next(remaining)
        if isinstance(response, Exception):
            raise response
        return BytesIO(
            response if isinstance(response, bytes) else json.dumps({"data": response}).encode()
        )

    client.raw_stream.side_effect = stream
    return client


def _span() -> dict[str, Any]:
    return {
        "timestamp": "2026-11-10 12:00:00.000000000",
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": "0" * 16,
        "operation": "Charge",
        "span_kind": "Server",
        "status": "Error",
        "message": "password=test-secret alice@example.com",
        "duration_ms": 12.5,
        "peer_service": "provider",
        "server_address": "provider.invalid",
        "error_type": "ValidationError",
        "service_version": "v42",
        "text_truncated": 0,
    }


def _pattern() -> dict[str, Any]:
    return {
        "operation": "Charge",
        "message": "Invalid token",
        "pattern_hash": "c" * 64,
        "span_count": 3,
        "trace_count": 2,
        "example_trace_id": "a" * 32,
        "text_truncated": 0,
    }


def test_all_queries_bind_scope_and_reuse_the_sdk_limits() -> None:
    log = {
        "timestamp": "2026-11-10 12:00:00.000000000",
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "severity": "ERROR",
        "message": "token expired",
        "text_truncated": 0,
    }
    client = _client(
        [{"readonly": 2}],
        [{"total": 30, "errors": 30, "latest": 180, "invalid": 0}],
        [{"total": 30, "errors": 0, "latest": 150, "invalid": 0}],
        [_pattern()],
        [_span()],
        [log],
    )
    config = ClickHouseEvidenceConfig()
    mapping = MetricMapping()
    reader = ClickHouseInvestigationReader(
        config, "source-password", _scope(), mapping, client=client
    )
    config.host = "changed.invalid"
    mapping.name = "changed"
    comparison = reader.compare_service_windows()
    patterns = reader.summarize_failure_patterns(limit=2)
    trace = reader.get_trace("A" * 32, limit=2)
    logs = reader.get_trace_logs("a" * 32, span_id="b" * 16, limit=2)
    assert comparison.data is not None and comparison.data.error_ratio_delta == 1
    assert patterns.data is not None and patterns.data[0].span_count == 3
    assert trace.data is not None and trace.data[0].parent_span_id == ""
    assert "test-secret" not in str(asdict(trace)) and "alice@example.com" not in str(asdict(trace))
    assert logs.data is not None and logs.data[0].message == "token expired"
    assert reader.config.host == "127.0.0.1"
    assert (
        client.raw_stream.call_count == 6
    )  # Verify once, then two metrics + three evidence reads.
    for call in client.raw_stream.call_args_list[1:]:
        sql, params, settings = call.args[0], call.kwargs["parameters"], call.kwargs["settings"]
        assert _scope().incident.service not in sql
        assert all(
            token in sql
            for token in (
                "{service:String}",
                "{environment:String}",
                "{start:Float64}",
                "{end:Float64}",
            )
        )
        assert params["service"] == _scope().incident.service and params["environment"] == "demo"
        assert settings["readonly"] == "2" and settings["max_result_rows"] == "20"
        assert settings["max_execution_time"] == "5" and settings["max_rows_to_read"] == "500000"
        assert settings["max_bytes_to_read"] == "50000000"
        assert "source-password" not in str(call)
    assert (
        client.raw_stream.call_args_list[1].kwargs["parameters"]["metric"]
        == "traces.span.metrics.calls"
    )
    assert client.raw_stream.call_args_list[-1].kwargs["parameters"]["fetch_limit"] == 3


@pytest.mark.parametrize(
    "baseline",
    [
        Scope("other", "demo", 120, 150),
        Scope("payment", "other", 120, 150),
        Scope("payment", "demo", 140, 170),
        Scope("payment", "demo", 110, 150),
        Scope("payment", "demo", -3600, -3570),
    ],
)
def test_cross_scope_overlapping_or_unbounded_baselines_are_rejected(baseline: Scope) -> None:
    with pytest.raises(ValueError, match="Baseline"):
        InvestigationScope(Scope("payment", "demo", 150, 180), baseline)


@pytest.mark.parametrize(
    "start,end",
    [
        (float("nan"), 180),
        (150, float("inf")),
        (180, 150),
        (0, 301),
        (150.0005, 180),
        (150, 180.0005),
        (150.001, 180.001),
    ],
)
def test_windows_have_finite_bounded_whole_second_ranges(start: float, end: float) -> None:
    with pytest.raises(ValueError, match="scope"):
        InvestigationScope(Scope("payment", "demo", start, end))


def test_unauthorized_windows_bad_ids_and_limits_do_not_query() -> None:
    client = _client()
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(),
        "secret",
        InvestigationScope(Scope("payment", "demo", 150, 180)),
        client=client,
    )
    with pytest.raises(ValueError, match="authorized"):
        reader.compare_service_windows()
    for trace_id in ("trace-1", "0" * 32, "a' OR 1=1 --"):
        with pytest.raises(ValueError, match="identifier"):
            reader.get_trace(trace_id)
    with pytest.raises(ValueError, match="range"):
        reader.summarize_failure_patterns(limit=11)
    client.raw_stream.assert_not_called()


def test_missing_or_insufficient_metrics_never_produce_a_ratio_delta() -> None:
    client = _client(
        [{"readonly": 2}],
        [{"total": 0, "errors": 0, "latest": 0, "invalid": 0}],
        [{"total": 4, "errors": 0, "latest": 150, "invalid": 0}],
    )
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(), "secret", _scope(), client=client
    )
    result = reader.compare_service_windows()
    assert result.status == "ok" and result.data is not None
    assert not result.data.incident.eligible and not result.data.baseline.eligible
    assert result.data.error_ratio_delta is None


def test_empty_results_are_distinct_from_source_and_query_limit_failures() -> None:
    for response, expected in (
        ([], "empty"),
        (DatabaseError("backend details and secret"), "unavailable"),
        (b"x" * 65537, "unavailable"),
    ):
        client = _client([{"readonly": 2}], response)
        reader = ClickHouseInvestigationReader(
            ClickHouseEvidenceConfig(), "secret", _scope(), client=client
        )
        result = reader.get_trace_logs("a" * 32)
        assert result.status == expected
        assert "backend details" not in str(asdict(result))
        assert (result.data == ()) if expected == "empty" else (result.data is None)


def test_dedicated_readonly_check_precedes_evidence_access() -> None:
    client = _client([{"readonly": 0}])
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(), "secret", _scope(), client=client
    )
    assert reader.get_trace("a" * 32).status == "unavailable"
    assert client.raw_stream.call_count == 1


@pytest.mark.parametrize("error_type", ["ProtocolError", "ReadTimeoutError"])
def test_interrupted_http_response_is_unavailable_without_partial_evidence(error_type: str) -> None:
    class TimedOutBody(BytesIO):
        def read(self, _size: int | None = -1, /) -> bytes:
            raise TimeoutError("backend details and secret")

    client = _client([{"readonly": 2}])
    interrupted = HTTPResponse(
        body=TimedOutBody() if error_type == "ReadTimeoutError" else BytesIO(b'{"data":['),
        headers={"Content-Length": "100"},
        preload_content=False,
    )
    client.raw_stream.side_effect = [BytesIO(b'{"data":[{"readonly":2}]}'), interrupted]
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(), "secret", _scope(), client=client
    )
    result = reader.get_trace_logs("a" * 32)
    assert result.status == "unavailable" and result.data is None
    assert result.error_type == error_type
    assert "IncompleteRead" not in str(asdict(result)) and "backend details" not in str(
        asdict(result)
    )


@pytest.mark.parametrize("total,errors", [(float("nan"), 1), (30, float("inf")), (30, 31)])
def test_invalid_measurements_are_unavailable_instead_of_exported_as_facts(
    total: float, errors: float
) -> None:
    client = _client(
        [{"readonly": 2}], [{"total": total, "errors": errors, "latest": 180, "invalid": 0}]
    )
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(), "secret", _scope(), client=client
    )
    result = reader.compare_service_windows()
    assert result.status == "unavailable" and result.data is None


def test_row_and_text_truncation_are_visible_and_references_are_stable() -> None:
    pattern = _pattern()
    client = _client(
        [{"readonly": 2}], [pattern, pattern], [pattern], [{**pattern, "text_truncated": 1}]
    )
    reader = ClickHouseInvestigationReader(
        ClickHouseEvidenceConfig(), "secret", _scope(), client=client
    )
    first = reader.summarize_failure_patterns(limit=1)
    second = reader.summarize_failure_patterns(limit=1)
    third = reader.summarize_failure_patterns(limit=1)
    assert first.truncated and not second.truncated and third.truncated
    assert first.data is not None and second.data is not None
    assert first.data[0].evidence_ref == second.data[0].evidence_ref
