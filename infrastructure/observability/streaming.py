"""Parse-rate tracking for streaming JSON / NDJSON clients.

Vendors occasionally emit a flaky line in an NDJSON stream, so dropping the
odd malformed frame is expected. The risk is when the drop ratio crosses a
threshold (content-type drift, schema break, vendor migration) and we keep
returning empty results to the agent.

``StreamingParseStats`` accumulates per-response parse outcomes;
``report_if_unhealthy`` emits a single Sentry event (with the failure
histogram in extras) when the skip ratio exceeds the threshold, so we do
not spam one event per dropped line.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from infrastructure.observability.errors.boundary import report_exception

#: Default fraction of skipped lines tolerated before a stream is flagged.
DEFAULT_SKIP_THRESHOLD: float = 0.10
#: Decimal places for ``skip_ratio`` in Sentry extras.
_SKIP_RATIO_DECIMAL_PLACES = 3


@dataclass
class StreamingParseStats:
    """Counters for one streaming-parse pass over a vendor response."""

    parsed: int = 0
    skipped: int = 0
    errors: Counter[str] = field(default_factory=Counter)

    def record_parsed(self) -> None:
        self.parsed += 1

    def record_error(self, exc: BaseException) -> None:
        self.skipped += 1
        self.errors[type(exc).__name__] += 1

    @property
    def total(self) -> int:
        return self.parsed + self.skipped

    @property
    def skip_ratio(self) -> float:
        return self.skipped / self.total if self.total else 0.0

    def report_if_unhealthy(
        self,
        *,
        logger: logging.Logger,
        integration: str,
        source: str,
        threshold: float = DEFAULT_SKIP_THRESHOLD,
    ) -> None:
        """Capture a single Sentry event when the skip ratio exceeds ``threshold``.

        No-op for empty streams or healthy ratios. ``integration`` is the vendor
        name (``splunk``, ``coralogix``, ...); ``source`` identifies the
        endpoint or parse site so dashboards can group by both.
        """
        if self.total == 0 or self.skip_ratio <= threshold:
            return
        # Synthesize an exception so report_exception (which routes through
        # Sentry's capture_exception) has something to attach the histogram to.
        # Keep the message static per integration so Sentry groups every
        # unhealthy response into a single issue; dynamic counts live in
        # extras where the dashboard can break them down.
        synthetic = RuntimeError(f"{integration}: streaming parse rate unhealthy")
        report_exception(
            synthetic,
            logger=logger,
            message="streaming parse-rate unhealthy",
            severity="warning",
            tags={
                "surface": "service_client",
                "integration": integration,
                "source": source,
                "event": "streaming_parse_unhealthy",
            },
            extras={
                "parsed": self.parsed,
                "skipped": self.skipped,
                "skip_ratio": round(self.skip_ratio, _SKIP_RATIO_DECIMAL_PLACES),
                "errors": dict(self.errors),
                "threshold": threshold,
            },
        )


__all__ = ["DEFAULT_SKIP_THRESHOLD", "StreamingParseStats"]
