"""Real OTLP -> pinned ClickStack Collector -> ClickHouse -> bounded evidence reads."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from typing import cast

import httpx
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from config.constants.telemetry import TELEMETRY_CLICKHOUSE_PASSWORD_ENV
from config.llm_credentials import resolve_env_credential
from core.telemetry.evidence_queries import InvestigationScope
from core.telemetry.models import Measurement, Scope
from core.telemetry.reports import evidence_snapshot
from integrations.clickhouse.investigation_queries import ClickHouseInvestigationReader
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig
from tests.e2e.telemetry_evidence.otel_fixtures import payloads


@pytest.mark.e2e
def test_otlp_evidence_queries_against_local_stack(request: pytest.FixtureRequest) -> None:
    port = cast(int | None, request.config.getoption("--evidence-clickhouse-port"))
    endpoint = cast(str | None, request.config.getoption("--evidence-otlp-endpoint"))
    password = resolve_env_credential(TELEMETRY_CLICKHOUSE_PASSWORD_ENV)
    if port is None or not endpoint or not password:
        pytest.skip(
            "Requires explicit local ClickHouse/OTLP selectors and the telemetry reader credential"
        )
    assert port is not None and endpoint and password
    end = float(int(time.time()) - 10)
    service = "payment-evidence-" + uuid.uuid4().hex[:8]
    scope = InvestigationScope(
        Scope(service, "demo", end - 30, end), Scope(service, "demo", end - 60, end - 30)
    )
    data, identifiers = payloads(scope)
    with httpx.Client(timeout=10) as client:
        for signal, payload in data.items():
            response = client.post(endpoint.rstrip("/") + "/v1/" + signal, json=payload)
            response.raise_for_status()
            partial = response.json().get("partialSuccess", {})
            assert not any(
                int(partial.get(key, 0))
                for key in ("rejectedSpans", "rejectedLogRecords", "rejectedDataPoints")
            )
    reader = ClickHouseInvestigationReader(ClickHouseEvidenceConfig(port=port), password, scope)
    try:
        deadline = time.monotonic() + 30
        while True:
            comparison = reader.compare_service_windows()
            patterns = reader.summarize_failure_patterns()
            if (
                comparison.data is not None
                and comparison.data.incident.eligible
                and patterns.data
                and len(patterns.data) == 5
            ):
                break
            if time.monotonic() >= deadline:
                pytest.fail(
                    f"Collector/exporter did not produce expected data: {comparison.status}, {patterns.status}"
                )
            time.sleep(0.5)
        assert comparison.data.incident.measurement.total == 30
        assert comparison.data.incident.measurement.errors == 30
        assert comparison.data.baseline.measurement.total == 30
        assert comparison.data.baseline.measurement.errors == 0
        assert comparison.data.error_ratio_delta == 1
        assert sum(pattern.span_count for pattern in patterns.data) == 5
        messages = {pattern.message for pattern in patterns.data}
        assert {
            "Invalid token",
            "Token expired",
            "Upstream deadline exceeded",
            "getaddrinfo ENOTFOUND provider.invalid",
            "[OMITTED:oversize]",
        } == messages
        oversized_pattern = next(
            pattern
            for pattern in patterns.data
            if pattern.example_trace_id == identifiers["oversize"]
        )
        assert oversized_pattern.operation == "[OMITTED:oversize]" and patterns.truncated
        oversized_trace = reader.get_trace(identifiers["oversize"])
        assert oversized_trace.data is not None and oversized_trace.truncated
        assert oversized_trace.data[0].operation == "[OMITTED:oversize]"
        assert oversized_trace.data[0].message == "[OMITTED:oversize]"
        trace = reader.get_trace(identifiers["auth"])
        assert trace.status == "ok" and trace.data is not None and len(trace.data) == 2
        assert any(span.parent_span_id == identifiers["root_span"] for span in trace.data)
        assert all(span.service_version == "v42" for span in trace.data)
        boundary = reader.get_trace(identifiers["boundary"])
        assert boundary.data is not None and [span.message for span in boundary.data] == [
            "included_end"
        ]
        logs = reader.get_trace_logs(identifiers["auth"], span_id=identifiers["child_span"])
        assert logs.data is not None and len(logs.data) == 3 and logs.truncated
        assert [log.message for log in logs.data[1:]] == ["[OMITTED:oversize]"] * 2
        assert "fixture-secret" not in str(asdict(logs)) and "alice@example.com" not in str(
            asdict(logs)
        )
        limited = reader.get_trace(identifiers["auth"], limit=1)
        assert limited.data is not None and len(limited.data) == 1 and limited.truncated
        assert reader.get_trace("f" * 32).status == "empty"
        # The watch's initial evidence path must preserve the same safe text boundaries.
        initial = reader.evidence(scope.incident)
        assert "ghp_" not in str(initial) and "BEGIN PRIVATE KEY" not in str(initial)
        assert any(row["text_truncated"] for row in initial["traces"])
        records, evidence_payload = evidence_snapshot(
            scope.incident, Measurement(30, 30, end), initial
        )
        assert (
            records
            and "fixture-secret" not in evidence_payload
            and "alice@example.com" not in evidence_payload
        )
        with pytest.raises(DatabaseError):
            reader._get_client().command(
                "INSERT INTO otel_traces SELECT * FROM otel_traces WHERE 0"
            )
    finally:
        reader.close()
