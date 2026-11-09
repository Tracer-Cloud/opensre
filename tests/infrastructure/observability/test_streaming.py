"""Tests for ``infrastructure.observability.streaming.StreamingParseStats``."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from infrastructure.observability.streaming import DEFAULT_SKIP_THRESHOLD, StreamingParseStats


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test._streaming")


class TestCounters:
    def test_starts_empty(self) -> None:
        stats = StreamingParseStats()
        assert stats.parsed == 0
        assert stats.skipped == 0
        assert stats.total == 0
        assert stats.skip_ratio == 0.0

    def test_record_parsed_increments(self) -> None:
        stats = StreamingParseStats()
        stats.record_parsed()
        stats.record_parsed()
        assert stats.parsed == 2
        assert stats.skipped == 0
        assert stats.total == 2
        assert stats.skip_ratio == 0.0

    def test_record_error_increments_and_histograms(self) -> None:
        stats = StreamingParseStats()
        stats.record_error(json.JSONDecodeError("bad", "x", 0))
        stats.record_error(json.JSONDecodeError("bad", "y", 0))
        stats.record_error(ValueError("also bad"))
        assert stats.skipped == 3
        assert stats.errors["JSONDecodeError"] == 2
        assert stats.errors["ValueError"] == 1

    def test_skip_ratio(self) -> None:
        stats = StreamingParseStats()
        for _ in range(9):
            stats.record_parsed()
        stats.record_error(json.JSONDecodeError("bad", "x", 0))
        assert stats.total == 10
        assert stats.skip_ratio == pytest.approx(0.1)


class TestReportIfUnhealthy:
    def test_empty_stream_is_silent(self, logger: logging.Logger) -> None:
        stats = StreamingParseStats()
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")
        mock.assert_not_called()

    def test_clean_stream_is_silent(self, logger: logging.Logger) -> None:
        stats = StreamingParseStats()
        for _ in range(50):
            stats.record_parsed()
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")
        mock.assert_not_called()

    def test_below_threshold_is_silent(self, logger: logging.Logger) -> None:
        # 5/100 = 5%, well below the 10% default.
        stats = StreamingParseStats()
        for _ in range(95):
            stats.record_parsed()
        for _ in range(5):
            stats.record_error(json.JSONDecodeError("bad", "x", 0))
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")
        mock.assert_not_called()

    def test_at_threshold_is_silent(self, logger: logging.Logger) -> None:
        # Exactly the threshold should not trip; only strictly above does.
        stats = StreamingParseStats()
        for _ in range(90):
            stats.record_parsed()
        for _ in range(10):
            stats.record_error(json.JSONDecodeError("bad", "x", 0))
        assert stats.skip_ratio == pytest.approx(DEFAULT_SKIP_THRESHOLD)
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")
        mock.assert_not_called()

    def test_above_threshold_reports_once(self, logger: logging.Logger) -> None:
        stats = StreamingParseStats()
        for _ in range(80):
            stats.record_parsed()
        for _ in range(20):
            stats.record_error(json.JSONDecodeError("bad", "x", 0))
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")
        assert mock.call_count == 1
        kwargs = mock.call_args.kwargs
        assert kwargs["severity"] == "warning"
        assert kwargs["tags"] == {
            "surface": "service_client",
            "integration": "splunk",
            "source": "search/jobs",
            "event": "streaming_parse_unhealthy",
        }
        assert kwargs["extras"]["parsed"] == 80
        assert kwargs["extras"]["skipped"] == 20
        assert kwargs["extras"]["skip_ratio"] == 0.2
        assert kwargs["extras"]["errors"] == {"JSONDecodeError": 20}

    def test_synthetic_message_is_stable_per_integration(self, logger: logging.Logger) -> None:
        # Sentry groups events by exception type + message. The message must
        # not embed the ratio or counts, otherwise every distinct response
        # would create a new issue and defeat the one-event-per-response
        # design. Two unrelated responses with very different ratios should
        # carry identical synthetic messages.
        sent: list[BaseException] = []

        def _record(exc: BaseException, **_: object) -> None:
            sent.append(exc)

        with patch("infrastructure.observability.streaming.report_exception", side_effect=_record):
            a = StreamingParseStats()
            for _ in range(80):
                a.record_parsed()
            for _ in range(20):
                a.record_error(json.JSONDecodeError("bad", "x", 0))
            a.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")

            b = StreamingParseStats()
            for _ in range(50):
                b.record_parsed()
            for _ in range(50):
                b.record_error(json.JSONDecodeError("bad", "x", 0))
            b.report_if_unhealthy(logger=logger, integration="splunk", source="search/jobs")

        assert len(sent) == 2
        assert str(sent[0]) == str(sent[1])
        # And the message must not leak the counts that vary per response.
        assert "20" not in str(sent[0])
        assert "80" not in str(sent[0])
        assert "%" not in str(sent[0])

    def test_histogram_aggregates_mixed_error_types(self, logger: logging.Logger) -> None:
        stats = StreamingParseStats()
        for _ in range(50):
            stats.record_parsed()
        for _ in range(8):
            stats.record_error(json.JSONDecodeError("bad", "x", 0))
        for _ in range(5):
            stats.record_error(ValueError("nope"))
        for _ in range(2):
            stats.record_error(TypeError("also nope"))
        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(logger=logger, integration="coralogix", source="dataprime")
        mock.assert_called_once()
        extras = mock.call_args.kwargs["extras"]
        assert extras["errors"] == {
            "JSONDecodeError": 8,
            "ValueError": 5,
            "TypeError": 2,
        }

    def test_custom_threshold_honored(self, logger: logging.Logger) -> None:
        # 5% skip should report only when threshold is lowered below it.
        stats = StreamingParseStats()
        for _ in range(95):
            stats.record_parsed()
        for _ in range(5):
            stats.record_error(json.JSONDecodeError("bad", "x", 0))

        with patch("infrastructure.observability.streaming.report_exception") as mock:
            stats.report_if_unhealthy(
                logger=logger, integration="vercel", source="logs/stream", threshold=0.01
            )
        mock.assert_called_once()
