"""Pinned investigation windows and immutable, provenance-bearing evidence results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from config.constants.telemetry import (
    TELEMETRY_INVESTIGATION_MAX_ITEMS,
    TELEMETRY_INVESTIGATION_MAX_LOOKBACK_SECONDS,
    TELEMETRY_INVESTIGATION_MAX_WINDOW_SECONDS,
)
from core.telemetry.models import Measurement, Scope


@dataclass(frozen=True)
class InvestigationScope:
    """Host-selected windows; callers cannot choose a different service or history range."""

    incident: Scope
    baseline: Scope | None = None
    minimum_requests: int = 5

    def __post_init__(self) -> None:
        for window in (self.incident, self.baseline):
            if window is None:
                continue
            if (
                not window.service.strip()
                or not window.environment.strip()
                or len(window.service) > 200
                or len(window.environment) > 200
                or not math.isfinite(window.start)
                or not math.isfinite(window.end)
                or window.start != math.trunc(window.start)
                or window.end != math.trunc(window.end)
                or not 0 < window.end - window.start <= TELEMETRY_INVESTIGATION_MAX_WINDOW_SECONDS
            ):
                raise ValueError("Invalid investigation scope; use bounded whole-second windows")
        if (
            isinstance(self.minimum_requests, bool)
            or not isinstance(self.minimum_requests, int)
            or self.minimum_requests < 1
        ):
            raise ValueError("minimum_requests must be a positive integer")
        if self.baseline is not None and (
            self.baseline.service != self.incident.service
            or self.baseline.environment != self.incident.environment
            or self.baseline.end > self.incident.start
            or self.incident.end - self.baseline.start
            > TELEMETRY_INVESTIGATION_MAX_LOOKBACK_SECONDS
            or self.baseline.end - self.baseline.start != self.incident.end - self.incident.start
        ):
            raise ValueError("Baseline must be an earlier equal-duration window in the same scope")

    def window(self, name: Literal["incident", "baseline"]) -> Scope:
        if name == "incident":
            return self.incident
        if name == "baseline" and self.baseline is not None:
            return self.baseline
        raise ValueError("Window was not authorized by the host")


@dataclass(frozen=True)
class WindowObservation:
    window: Scope
    measurement: Measurement
    eligible: bool


@dataclass(frozen=True)
class WindowComparison:
    incident: WindowObservation
    baseline: WindowObservation
    error_ratio_delta: float | None


@dataclass(frozen=True)
class FailurePattern:
    evidence_ref: str
    operation: str
    message: str
    span_count: int
    trace_count: int
    example_trace_id: str


@dataclass(frozen=True)
class SpanEvidence:
    evidence_ref: str
    timestamp: str
    trace_id: str
    span_id: str
    parent_span_id: str
    operation: str
    span_kind: str
    status: str
    message: str
    duration_ms: float
    peer_service: str
    server_address: str
    error_type: str
    service_version: str


@dataclass(frozen=True)
class LogEvidence:
    evidence_ref: str
    timestamp: str
    trace_id: str
    span_id: str
    severity: str
    message: str


@dataclass(frozen=True)
class EvidenceQueryResult[T]:
    """Execution status is distinct from health, sampling completeness and query truncation."""

    status: Literal["ok", "empty", "unavailable"]
    source_id: str
    windows: tuple[Scope, ...]
    observed_at: float
    data: T | None
    truncated: bool = False
    error_type: str | None = None
    limitations: tuple[str, ...] = ()


class InvestigationReader(Protocol):
    """Evidence capabilities for a host-scoped investigator, independent of storage vendor."""

    def compare_service_windows(self) -> EvidenceQueryResult[WindowComparison]:
        """Compare the incident with its authorized baseline using reliable measurements."""

    def summarize_failure_patterns(
        self,
        *,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[FailurePattern, ...]]:
        """Return bounded stored-span failure groups and representative trace identifiers."""

    def get_trace(
        self,
        trace_id: str,
        *,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[SpanEvidence, ...]]:
        """Return permitted spans for a trace, with explicit completeness limitations."""

    def get_trace_logs(
        self,
        trace_id: str,
        *,
        span_id: str | None = None,
        window: Literal["incident", "baseline"] = "incident",
        limit: int = TELEMETRY_INVESTIGATION_MAX_ITEMS,
    ) -> EvidenceQueryResult[tuple[LogEvidence, ...]]:
        """Return bounded trace-correlated logs, optionally filtered to one span."""
