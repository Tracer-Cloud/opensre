"""Backend-neutral contracts for a single-service telemetry watch."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field

from config.strict_config import StrictConfigModel

Health = Literal["bad", "healthy", "unknown"]
Evidence = dict[str, list[dict[str, object]]]


class MetricMapping(StrictConfigModel):
    """Map a delta request counter and its error dimension to the detector."""

    name: str = Field(default="traces.span.metrics.calls", min_length=1, max_length=200)
    status_attribute: str = Field(default="status.code", min_length=1, max_length=100)
    error_value: str = Field(default="STATUS_CODE_ERROR", min_length=1, max_length=100)
    kind_attribute: str = Field(default="span.kind", min_length=1, max_length=100)
    server_value: str = Field(default="SPAN_KIND_SERVER", min_length=1, max_length=100)


@dataclass(frozen=True)
class Scope:
    service: str
    environment: str
    start: float
    end: float


@dataclass(frozen=True)
class Measurement:
    total: float
    errors: float
    latest: float | None


class WatchRule(StrictConfigModel):
    service: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=200)
    metric: MetricMapping = Field(default_factory=MetricMapping)
    window_seconds: int = Field(default=30, ge=10, le=300)
    ingestion_delay_seconds: int = Field(default=15, ge=5, le=120)
    minimum_requests: int = Field(default=5, ge=1)
    error_ratio: float = Field(default=0.2, gt=0, le=1, allow_inf_nan=False)
    healthy_windows: int = Field(default=2, ge=2, le=10)

    def scope(self, now: float) -> Scope:
        """Select the most recent completed window, allowing for ingestion lag."""
        end = math.floor((now - self.ingestion_delay_seconds) / self.window_seconds)
        end *= self.window_seconds
        return Scope(self.service, self.environment, end - self.window_seconds, end)

    def classify(self, scope: Scope, sample: Measurement) -> Health:
        """Reject missing, stale, non-finite or inconsistent request counters."""
        if (
            sample.latest is None
            or not math.isfinite(sample.latest)
            or not scope.start <= sample.latest <= scope.end
            or not math.isfinite(sample.total)
            or not math.isfinite(sample.errors)
            or sample.total < self.minimum_requests
            or not 0 <= sample.errors <= sample.total
        ):
            return "unknown"
        return "bad" if sample.errors / sample.total >= self.error_ratio else "healthy"


class EvidenceReader(Protocol):
    def measure(self, scope: Scope, mapping: MetricMapping) -> Measurement:
        """Read a service/environment-scoped, delta request-counter window."""

    def evidence(self, scope: Scope) -> Evidence:
        """Read bounded traces and logs from exactly the supplied scope."""
