"""Four fixed evidence operations over the pinned ClickStack Map tables."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal

from clickhouse_connect.driver import Client  # type: ignore[import-untyped]
from clickhouse_connect.driver.exceptions import ClickHouseError  # type: ignore[import-untyped]
from urllib3.exceptions import HTTPError

from config.constants.telemetry import TELEMETRY_INVESTIGATION_MAX_ITEMS
from core.telemetry.evidence_queries import (
    EvidenceQueryResult,
    FailurePattern,
    InvestigationScope,
    LogEvidence,
    SpanEvidence,
    WindowComparison,
    WindowObservation,
)
from core.telemetry.investigation import sanitize
from core.telemetry.models import MetricMapping, Scope
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig, ClickHouseEvidenceReader

logger = logging.getLogger(__name__)


def _otel_id(value: str, size: int, *, allow_empty: bool = False) -> str:
    if allow_empty and value in {"", "0" * size}:
        return ""
    if (
        not isinstance(value, str)
        or not re.fullmatch(rf"[0-9a-fA-F]{{{size}}}", value)
        or int(value, 16) == 0
    ):
        raise ValueError("Expected a nonzero OpenTelemetry identifier")
    return value.lower()


def _text(row: dict[str, Any], name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise ValueError("Malformed telemetry text")
    return sanitize(value)


def _bounded_text(column: str, maximum: int) -> str:
    """Project trusted SQL columns without exposing unrecognizable credential fragments."""
    return f"if(lengthUTF8({column}) > {maximum}, '[OMITTED:oversize]', {column})"


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Malformed telemetry count")
    return value


class ClickHouseInvestigationReader(ClickHouseEvidenceReader):
    """A sequential reader for one pinned investigation; not a registered agent toolset."""

    def __init__(
        self,
        config: ClickHouseEvidenceConfig,
        password: str,
        scope: InvestigationScope,
        mapping: MetricMapping | None = None,
        *,
        client: Client | None = None,
    ) -> None:
        super().__init__(config.model_copy(deep=True), password, client=client)
        self._scope = scope
        self._mapping = (mapping or MetricMapping()).model_copy(deep=True)
        self._verified = False
        source = json.dumps(config.model_dump(), sort_keys=True)
        self._source_id = "clickhouse:" + hashlib.sha256(source.encode()).hexdigest()[:16]

    @staticmethod
    def _limit(limit: int) -> int:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= TELEMETRY_INVESTIGATION_MAX_ITEMS
        ):
            raise ValueError("Evidence limit is outside the allowed range")
        return limit

    @staticmethod
    def _where() -> str:
        return (
            " WHERE ServiceName = {service:String} "
            "AND ResourceAttributes['deployment.environment.name'] = {environment:String} "
            "AND Timestamp > fromUnixTimestamp64Milli(toInt64({start:Float64} * 1000)) "
            "AND Timestamp <= fromUnixTimestamp64Milli(toInt64({end:Float64} * 1000)) "
        )

    def _ref(self, kind: str, scope: Scope, identity: Any) -> str:
        value = json.dumps([self._source_id, asdict(scope), identity], sort_keys=True)
        return kind + ":" + hashlib.sha256(value.encode()).hexdigest()

    def _execute[T](
        self,
        windows: tuple[Scope, ...],
        action: Callable[[], tuple[T, bool]],
        limitations: tuple[str, ...],
    ) -> EvidenceQueryResult[T]:
        try:
            if not self._verified:
                self.verify()
                self._verified = True
            data, truncated = action()
            return EvidenceQueryResult(
                "ok" if data else "empty",
                self._source_id,
                windows,
                time.time(),
                data,
                truncated=truncated,
                limitations=limitations,
            )
        except (
            ClickHouseError,
            HTTPError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
        ) as exc:
            logger.exception("ClickHouse investigation evidence unavailable")
            return EvidenceQueryResult(
                "unavailable",
                self._source_id,
                windows,
                time.time(),
                None,
                error_type=type(exc).__name__,
                limitations=limitations,
            )

    def compare_service_windows(self) -> EvidenceQueryResult[WindowComparison]:
        """Compare eligible delta request counters; an earlier baseline is not assumed healthy."""
        incident = self._scope.incident
        baseline = self._scope.window("baseline")

        def read() -> tuple[WindowComparison, bool]:
            observations = []
            for window in (incident, baseline):
                sample = self.measure(window, self._mapping)
                if (
                    not all(math.isfinite(value) for value in (sample.total, sample.errors))
                    or not 0 <= sample.errors <= sample.total
                    or (sample.latest is not None and not math.isfinite(sample.latest))
                ):
                    raise ValueError("Invalid request measurement")
                eligible = (
                    sample.total >= self._scope.minimum_requests
                    and sample.latest is not None
                    and math.isfinite(sample.latest)
                    and window.start < sample.latest <= window.end
                )
                observations.append(WindowObservation(window, sample, eligible))
            current, previous = observations
            delta = None
            if current.eligible and previous.eligible:
                delta = (
                    current.measurement.errors / current.measurement.total
                    - previous.measurement.errors / previous.measurement.total
                )
            return WindowComparison(current, previous, delta), False

        return self._execute(
            (incident, baseline),
            read,
            (
                "Eligibility does not establish a healthy baseline or recovery.",
                "Counters require the configured unsampled monotonic delta SERVER-span mapping; latency is not measured here.",
                "Late arrivals and gaps inside a window are not excluded by a latest timestamp.",
            ),
        )

    def summarize_failure_patterns(
        self,
        *,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[FailurePattern, ...]]:
        """Group stored error spans by exact operation/message and retain one trace example."""
        scope, limit = self._scope.window(window), self._limit(limit)

        def read() -> tuple[tuple[FailurePattern, ...], bool]:
            rows = self._query(
                f"SELECT {_bounded_text('SpanName', 200)} AS operation, "
                f"{_bounded_text('StatusMessage', 400)} AS message, "
                "hex(SHA256(concat(SpanName, '\\0', StatusMessage))) AS pattern_hash, "
                "count() AS span_count, uniqExact(TraceId) AS trace_count, min(TraceId) AS example_trace_id, "
                "lengthUTF8(SpanName) > 200 OR lengthUTF8(StatusMessage) > 400 AS text_truncated "
                "FROM otel_traces" + self._where() + "AND StatusCode = 'Error' "
                "GROUP BY SpanName, StatusMessage ORDER BY span_count DESC, SpanName, StatusMessage "
                "LIMIT {fetch_limit:UInt32}",
                {**self._parameters(scope), "fetch_limit": limit + 1},
            )
            patterns = tuple(
                FailurePattern(
                    self._ref("pattern", scope, row["pattern_hash"]),
                    _text(row, "operation"),
                    _text(row, "message"),
                    _count(row["span_count"]),
                    _count(row["trace_count"]),
                    _otel_id(row["example_trace_id"], 32),
                )
                for row in rows[:limit]
            )
            return patterns, len(rows) > limit or any(row["text_truncated"] for row in rows[:limit])

        return self._execute(
            (scope,),
            read,
            (
                "Counts describe stored error-span rows and distinct stored trace IDs, not failed requests; sampling and duplicate exports may affect them.",
                "Grouping uses exact original operation/message; displayed text is bounded and redacted.",
            ),
        )

    def get_trace(
        self,
        trace_id: str,
        *,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[SpanEvidence, ...]]:
        """Read selected spans only within the pinned service/environment/time window."""
        trace_id = _otel_id(trace_id, 32)
        scope, limit = self._scope.window(window), self._limit(limit)

        def read() -> tuple[tuple[SpanEvidence, ...], bool]:
            rows = self._query(
                "SELECT toString(Timestamp) AS timestamp, TraceId AS trace_id, SpanId AS span_id, "
                f"ParentSpanId AS parent_span_id, {_bounded_text('SpanName', 200)} AS operation, "
                f"SpanKind AS span_kind, StatusCode AS status, {_bounded_text('StatusMessage', 400)} AS message, "
                f"Duration / 1e6 AS duration_ms, {_bounded_text("SpanAttributes['peer.service']", 200)} AS peer_service, "
                f"{_bounded_text("SpanAttributes['server.address']", 200)} AS server_address, "
                f"{_bounded_text("SpanAttributes['error.type']", 200)} AS error_type, "
                f"{_bounded_text("ResourceAttributes['service.version']", 200)} AS service_version, "
                "lengthUTF8(SpanName) > 200 OR lengthUTF8(StatusMessage) > 400 "
                "OR lengthUTF8(SpanAttributes['peer.service']) > 200 OR lengthUTF8(SpanAttributes['server.address']) > 200 "
                "OR lengthUTF8(SpanAttributes['error.type']) > 200 OR lengthUTF8(ResourceAttributes['service.version']) > 200 AS text_truncated "
                "FROM otel_traces" + self._where() + "AND TraceId = {trace_id:String} "
                "ORDER BY Timestamp, SpanId LIMIT {fetch_limit:UInt32}",
                {**self._parameters(scope), "trace_id": trace_id, "fetch_limit": limit + 1},
            )
            spans = []
            for row in rows[:limit]:
                duration = float(row["duration_ms"])
                if not math.isfinite(duration) or duration < 0:
                    raise ValueError("Invalid span duration")
                spans.append(
                    SpanEvidence(
                        self._ref("span", scope, [trace_id, row["span_id"]]),
                        _text(row, "timestamp"),
                        _otel_id(row["trace_id"], 32),
                        _otel_id(row["span_id"], 16),
                        _otel_id(row["parent_span_id"], 16, allow_empty=True),
                        _text(row, "operation"),
                        _text(row, "span_kind"),
                        _text(row, "status"),
                        _text(row, "message"),
                        duration,
                        _text(row, "peer_service"),
                        _text(row, "server_address"),
                        _text(row, "error_type"),
                        _text(row, "service_version"),
                    )
                )
            return tuple(spans), len(rows) > limit or any(
                row["text_truncated"] for row in rows[:limit]
            )

        return self._execute(
            (scope,),
            read,
            (
                "This is a scoped span subset, not proof of a complete distributed trace; other services, times or sampled spans can be absent.",
                "Absent dependency/version attributes remain empty rather than being inferred.",
            ),
        )

    def get_trace_logs(
        self,
        trace_id: str,
        *,
        span_id: str | None = None,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[LogEvidence, ...]]:
        """Read trace-correlated logs with an optional exact span filter."""
        trace_id = _otel_id(trace_id, 32)
        selected_span = _otel_id(span_id, 16) if span_id is not None else ""
        scope, limit = self._scope.window(window), self._limit(limit)

        def read() -> tuple[tuple[LogEvidence, ...], bool]:
            rows = self._query(
                "SELECT toString(Timestamp) AS timestamp, TraceId AS trace_id, SpanId AS span_id, "
                f"{_bounded_text('SeverityText', 200)} AS severity, {_bounded_text('Body', 400)} AS message, "
                "lengthUTF8(Body) > 400 OR lengthUTF8(SeverityText) > 200 AS text_truncated FROM otel_logs"
                + self._where()
                + "AND TraceId = {trace_id:String} AND ({span_id:String} = '' OR SpanId = {span_id:String}) "
                "ORDER BY Timestamp, SpanId, SeverityText, Body LIMIT {fetch_limit:UInt32}",
                {
                    **self._parameters(scope),
                    "trace_id": trace_id,
                    "span_id": selected_span,
                    "fetch_limit": limit + 1,
                },
            )
            logs = tuple(
                LogEvidence(
                    self._ref("log", scope, row),
                    _text(row, "timestamp"),
                    _otel_id(row["trace_id"], 32),
                    _otel_id(row["span_id"], 16, allow_empty=True),
                    _text(row, "severity"),
                    _text(row, "message"),
                )
                for row in rows[:limit]
            )
            return logs, len(rows) > limit or any(row["text_truncated"] for row in rows[:limit])

        return self._execute(
            (scope,),
            read,
            (
                "Only correlated logs inside the pinned scope are returned; empty results do not establish that no logs or failures exist elsewhere.",
                "Log references identify content within scope, not unique physical events; late arrivals remain possible.",
            ),
        )
