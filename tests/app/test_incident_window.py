"""Tests for app.incident_window.

Coverage strategy:
1. ``IncidentWindow`` value object: construction validation, UTC
   normalisation, serialisation round-trip.
2. ``resolve_incident_window`` precedence: override > anchor > default.
3. One real-world payload shape per supported alert format
   (Alertmanager v4, Grafana managed alert, PagerDuty v3 webhook,
   Datadog event_time webhook, CloudWatch SNS-wrapped alarm).
4. Parser-level unit tests for shape variants (epoch ms vs ISO string,
   nested SNS Message, multiple Alertmanager alerts → earliest wins).
5. Edge cases: clock skew, lookback clamp, malformed JSON, naive
   timestamps, zero/negative lookback, empty payload.
6. Defensive guarantees: no parser may raise, no payload shape may
   bring down the resolver.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.incident_window import (
    DEFAULT_LOOKBACK_MINUTES,
    MAX_LOOKBACK_MINUTES,
    SCHEMA_VERSION,
    SOURCE_ACTIVATED_AT,
    SOURCE_DEFAULT,
    SOURCE_FIRED_AT,
    SOURCE_OVERRIDE,
    SOURCE_STARTS_AT,
    IncidentWindow,
    resolve_incident_window,
)

# Reference "now" used across tests to keep window arithmetic predictable.
NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# IncidentWindow construction & invariants
# ---------------------------------------------------------------------------


class TestIncidentWindowConstruction:
    def test_valid_window_constructs(self) -> None:
        w = IncidentWindow(
            since=NOW - timedelta(hours=2),
            until=NOW,
            source="test",
            confidence=1.0,
        )
        assert w.since < w.until
        assert w.confidence == 1.0

    def test_naive_since_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            IncidentWindow(
                since=datetime(2026, 4, 20, 10, 0),  # naive
                until=NOW,
                source="x",
                confidence=1.0,
            )

    def test_naive_until_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            IncidentWindow(
                since=NOW - timedelta(hours=2),
                until=datetime(2026, 4, 20, 12, 0),  # naive
                source="x",
                confidence=1.0,
            )

    def test_inverted_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="since < until"):
            IncidentWindow(
                since=NOW,
                until=NOW - timedelta(hours=1),
                source="x",
                confidence=1.0,
            )

    def test_zero_length_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="since < until"):
            IncidentWindow(since=NOW, until=NOW, source="x", confidence=1.0)

    def test_non_utc_tz_normalised(self) -> None:
        # Eastern offset; should be converted to UTC internally.
        eastern = timezone(timedelta(hours=-5))
        since_local = datetime(2026, 4, 20, 5, 0, tzinfo=eastern)  # = 10:00 UTC
        until_local = datetime(2026, 4, 20, 7, 0, tzinfo=eastern)  # = 12:00 UTC
        w = IncidentWindow(since=since_local, until=until_local, source="x", confidence=1.0)
        assert w.since == datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        assert w.until == datetime(2026, 4, 20, 12, 0, tzinfo=UTC)

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            IncidentWindow(since=NOW - timedelta(hours=1), until=NOW, source="x", confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            IncidentWindow(since=NOW - timedelta(hours=1), until=NOW, source="x", confidence=-0.1)

    def test_empty_source_rejected(self) -> None:
        with pytest.raises(ValueError, match="source"):
            IncidentWindow(since=NOW - timedelta(hours=1), until=NOW, source="", confidence=1.0)

    def test_non_datetime_rejected(self) -> None:
        with pytest.raises(TypeError, match="datetime"):
            IncidentWindow(since="not-a-date", until=NOW, source="x", confidence=1.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    def test_to_dict_carries_schema_version(self) -> None:
        w = IncidentWindow(since=NOW - timedelta(hours=2), until=NOW, source="x", confidence=0.5)
        d = w.to_dict()
        assert d["_schema_version"] == SCHEMA_VERSION
        assert d["since"] == "2026-04-20T10:00:00Z"
        assert d["until"] == "2026-04-20T12:00:00Z"

    def test_round_trip(self) -> None:
        original = IncidentWindow(
            since=NOW - timedelta(hours=3), until=NOW, source="alert.startsAt", confidence=1.0
        )
        rebuilt = IncidentWindow.from_dict(original.to_dict())
        assert rebuilt is not None
        assert rebuilt == original

    def test_from_dict_returns_none_on_bad_shape(self) -> None:
        assert IncidentWindow.from_dict(None) is None
        assert IncidentWindow.from_dict("string") is None
        assert IncidentWindow.from_dict({}) is None
        assert IncidentWindow.from_dict({"since": "garbage", "until": "x"}) is None

    def test_from_dict_returns_none_when_invariants_violated(self) -> None:
        # Inverted window in the dict — should not raise, returns None.
        bad = {
            "_schema_version": 1,
            "since": "2026-04-20T12:00:00Z",
            "until": "2026-04-20T10:00:00Z",
            "source": "x",
            "confidence": 1.0,
        }
        assert IncidentWindow.from_dict(bad) is None


# ---------------------------------------------------------------------------
# Adaptation: expanded()
# ---------------------------------------------------------------------------


class TestExpanded:
    """``IncidentWindow.expanded()`` — used by the adapt_window node when
    the deploy timeline came back empty for a shared-window query. The
    method is deliberately tiny: it widens the lookback, clamps to
    MAX_LOOKBACK_MINUTES, and preserves every other field. The history of
    expansions is tracked separately in ``state.incident_window_history``.
    """

    def _two_hour_window(self) -> IncidentWindow:
        return IncidentWindow(
            since=NOW - timedelta(hours=2),
            until=NOW,
            source=SOURCE_STARTS_AT,
            confidence=1.0,
        )

    def test_doubles_lookback_with_default_factor(self) -> None:
        original = self._two_hour_window()
        widened = original.expanded()
        assert (widened.until - widened.since) == timedelta(hours=4)
        assert widened.until == original.until  # anchor preserved

    def test_custom_factor_scales_lookback(self) -> None:
        original = self._two_hour_window()
        widened = original.expanded(factor=3.0)
        assert (widened.until - widened.since) == timedelta(hours=6)

    def test_clamps_to_max_lookback_minutes(self) -> None:
        # Start at 5 days, doubling would be 10 days; cap is 7 days.
        original = IncidentWindow(
            since=NOW - timedelta(days=5),
            until=NOW,
            source=SOURCE_STARTS_AT,
            confidence=1.0,
        )
        widened = original.expanded(factor=2.0)
        actual_lookback_min = (widened.until - widened.since).total_seconds() / 60.0
        assert actual_lookback_min == float(MAX_LOOKBACK_MINUTES)

    def test_already_at_cap_returns_same_width(self) -> None:
        # When we're already at the cap, expansion is a no-op for width.
        # The rule layer detects this via a strictly-wider check.
        original = IncidentWindow(
            since=NOW - timedelta(minutes=MAX_LOOKBACK_MINUTES),
            until=NOW,
            source=SOURCE_STARTS_AT,
            confidence=1.0,
        )
        widened = original.expanded(factor=2.0)
        assert (widened.until - widened.since) == (original.until - original.since)

    def test_returns_new_instance_not_mutation(self) -> None:
        # Frozen dataclass; identity must differ even if values match.
        original = self._two_hour_window()
        widened = original.expanded(factor=2.0)
        assert widened is not original
        assert original.since == NOW - timedelta(hours=2)  # unchanged

    def test_preserves_until_anchor(self) -> None:
        original = self._two_hour_window()
        widened = original.expanded(factor=4.0)
        assert widened.until == original.until

    def test_preserves_source_and_confidence(self) -> None:
        original = IncidentWindow(
            since=NOW - timedelta(hours=2),
            until=NOW,
            source=SOURCE_FIRED_AT,
            confidence=1.0,
        )
        widened = original.expanded(factor=2.0)
        assert widened.source == SOURCE_FIRED_AT
        assert widened.confidence == 1.0

    def test_factor_one_is_rejected(self) -> None:
        # factor=1.0 is a no-op width but the API contract is "expansion
        # only" so we reject it explicitly rather than silently no-op.
        with pytest.raises(ValueError, match="factor > 1.0"):
            self._two_hour_window().expanded(factor=1.0)

    def test_factor_below_one_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="factor > 1.0"):
            self._two_hour_window().expanded(factor=0.5)


# ---------------------------------------------------------------------------
# Resolver precedence
# ---------------------------------------------------------------------------


class TestResolverPrecedence:
    def test_override_always_wins(self) -> None:
        override = IncidentWindow(
            since=NOW - timedelta(days=3),
            until=NOW - timedelta(days=2),
            source=SOURCE_OVERRIDE,
            confidence=1.0,
        )
        result = resolve_incident_window(
            {"startsAt": "2026-04-20T08:00:00Z"},  # would normally match
            override=override,
            now=NOW,
        )
        assert result is override

    def test_default_when_no_anchor(self) -> None:
        result = resolve_incident_window({}, now=NOW)
        assert result.source == SOURCE_DEFAULT
        assert result.confidence == 0.0
        assert result.until == NOW
        assert result.since == NOW - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)

    def test_default_when_raw_alert_is_none(self) -> None:
        result = resolve_incident_window(None, now=NOW)
        assert result.source == SOURCE_DEFAULT

    def test_default_when_raw_alert_is_malformed_json(self) -> None:
        result = resolve_incident_window("{not valid json", now=NOW)
        assert result.source == SOURCE_DEFAULT


# ---------------------------------------------------------------------------
# Real-world payload shapes — one per format
# ---------------------------------------------------------------------------


class TestRealWorldPayloads:
    """One canonical webhook shape per format, lifted from vendor docs."""

    def test_alertmanager_v4_webhook(self) -> None:
        # Stripped from https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
        payload = {
            "version": "4",
            "groupKey": '{}:{alertname="HighCPUUsage"}',
            "status": "firing",
            "receiver": "team-X",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighCPUUsage", "severity": "critical"},
                    "annotations": {"summary": "CPU 92%"},
                    "startsAt": "2026-04-20T09:30:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                }
            ],
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_STARTS_AT
        assert result.confidence == 1.0
        # until = anchor (09:30) + default 10min buffer = 09:40Z
        assert result.until == datetime(2026, 4, 20, 9, 40, tzinfo=UTC)
        assert result.since == result.until - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)

    def test_grafana_managed_alert(self) -> None:
        # Grafana uses the Alertmanager schema but tags the source differently.
        payload = {
            "receiver": "grafana-default",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"grafana_folder": "Prod"},
                    "annotations": {"summary": "p95 over budget"},
                    "startsAt": "2026-04-20T10:15:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
            "commonAnnotations": {},
            "externalURL": "https://grafana.example.com",
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_STARTS_AT
        assert result.until == datetime(2026, 4, 20, 10, 25, tzinfo=UTC)

    def test_pagerduty_v3_webhook(self) -> None:
        # PagerDuty webhook v3 nests incident under event.data.
        payload = {
            "event": {
                "id": "evt-123",
                "event_type": "incident.triggered",
                "occurred_at": "2026-04-20T11:00:00Z",
                "data": {
                    "type": "incident",
                    "id": "PINC123",
                    "title": "Database connection exhausted",
                    "triggered_at": "2026-04-20T10:55:30Z",
                    "created_at": "2026-04-20T10:55:30Z",
                    "status": "triggered",
                },
            }
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_FIRED_AT
        assert result.until == datetime(2026, 4, 20, 11, 5, 30, tzinfo=UTC)

    def test_datadog_event_time_milliseconds(self) -> None:
        # Datadog webhook payload (representative subset).
        # event_time in milliseconds since epoch.
        payload = {
            "alert_id": "abc-123",
            "alert_status": "Triggered",
            "event_msg": "CPU > 80%",
            "event_time": 1745136000000,  # 2025-04-20T08:00:00Z in ms
        }
        result = resolve_incident_window(payload, now=datetime(2025, 4, 20, 12, 0, tzinfo=UTC))
        assert result.source == SOURCE_FIRED_AT
        # Anchor is the epoch ms value.
        assert result.until.year == 2025

    def test_cloudwatch_alarm_sns_wrapped(self) -> None:
        # SNS wraps the actual alarm message as a JSON-string in the Message field.
        inner_alarm = {
            "AlarmName": "HighErrorRate",
            "NewStateValue": "ALARM",
            "StateUpdatedTimestamp": "2026-04-20T10:30:00.123+0000",
            "Region": "us-east-1",
        }
        payload = {
            "Type": "Notification",
            "MessageId": "abc",
            "Message": json.dumps(inner_alarm),
            "Timestamp": "2026-04-20T10:30:01.000Z",
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_ACTIVATED_AT
        # Anchor 10:30:00.123, plus 10 min buffer = 10:40:00.123
        assert result.until == datetime(2026, 4, 20, 10, 40, 0, 123000, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Parser shape variants
# ---------------------------------------------------------------------------


class TestParserVariants:
    def test_alertmanager_multiple_alerts_picks_earliest(self) -> None:
        payload = {
            "alerts": [
                {"startsAt": "2026-04-20T11:00:00Z"},
                {"startsAt": "2026-04-20T09:30:00Z"},  # earliest
                {"startsAt": "2026-04-20T10:15:00Z"},
            ]
        }
        result = resolve_incident_window(payload, now=NOW)
        # until = 09:30 + 10min buffer
        assert result.until == datetime(2026, 4, 20, 9, 40, tzinfo=UTC)

    def test_alertmanager_top_level_starts_at(self) -> None:
        # Older grouped payloads can carry startsAt at the top level too.
        payload = {"startsAt": "2026-04-20T08:00:00Z"}
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_STARTS_AT

    def test_datadog_event_time_seconds(self) -> None:
        # Some Datadog payloads carry seconds, not ms. We tolerate both.
        payload = {"event_time": 1745136000}  # seconds
        result = resolve_incident_window(payload, now=datetime(2025, 4, 20, 12, 0, tzinfo=UTC))
        assert result.source == SOURCE_FIRED_AT

    def test_datadog_iso_string(self) -> None:
        payload = {"event_time": "2026-04-20T09:00:00Z"}
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_FIRED_AT

    def test_pagerduty_v2_top_level_incident(self) -> None:
        # Older shape that doesn't go through event.data.
        payload = {
            "incident": {
                "incident_number": 42,
                "triggered_at": "2026-04-20T10:00:00Z",
            }
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_FIRED_AT

    def test_cloudwatch_top_level_state_updated(self) -> None:
        # When the alarm dict arrives un-SNS-wrapped (e.g. EventBridge → Lambda).
        payload = {"StateUpdatedTimestamp": "2026-04-20T10:00:00Z"}
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_ACTIVATED_AT


# ---------------------------------------------------------------------------
# Edge cases & defensive guarantees
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_clock_skew_anchor_in_future_pinned_to_now(self) -> None:
        # Anchor 30 minutes in the future → until pinned to now.
        future_now = NOW
        payload = {"startsAt": "2026-04-20T12:30:00Z"}  # 30 min after NOW
        result = resolve_incident_window(payload, now=future_now)
        assert result.until == future_now

    def test_lookback_clamped_to_max(self) -> None:
        result = resolve_incident_window({}, lookback_minutes=10 * MAX_LOOKBACK_MINUTES, now=NOW)
        assert result.until - result.since == timedelta(minutes=MAX_LOOKBACK_MINUTES)

    def test_lookback_zero_falls_back_to_default(self) -> None:
        result = resolve_incident_window({}, lookback_minutes=0, now=NOW)
        assert result.until - result.since == timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)

    def test_lookback_negative_falls_back_to_default(self) -> None:
        result = resolve_incident_window({}, lookback_minutes=-5, now=NOW)
        assert result.until - result.since == timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)

    def test_buffer_negative_treated_as_zero(self) -> None:
        payload = {"startsAt": "2026-04-20T09:00:00Z"}
        result = resolve_incident_window(payload, forward_buffer_minutes=-30, now=NOW)
        # No buffer added.
        assert result.until == datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

    def test_naive_iso_string_treated_as_utc(self) -> None:
        # No tz suffix in the alert payload — must not raise, must assume UTC.
        payload = {"startsAt": "2026-04-20T09:00:00"}
        result = resolve_incident_window(payload, now=NOW)
        assert result.until.tzinfo == UTC

    def test_non_dict_non_string_raw_alert(self) -> None:
        # If the raw_alert is, say, an int (shouldn't happen but might),
        # the resolver must not crash.
        result = resolve_incident_window(42, now=NOW)
        assert result.source == SOURCE_DEFAULT

    def test_alertmanager_alerts_list_with_garbage_entries(self) -> None:
        payload = {
            "alerts": [
                "not-a-dict",
                {"startsAt": 12345},  # wrong type
                {"startsAt": "garbage"},  # unparseable
                {"startsAt": "2026-04-20T09:00:00Z"},  # the real one
            ]
        }
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_STARTS_AT

    def test_datadog_bool_event_time_ignored(self) -> None:
        # bool is a subclass of int in Python — must not be treated as epoch.
        payload = {"event_time": True}
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_DEFAULT  # parser correctly skipped

    def test_cloudwatch_message_invalid_json_falls_through(self) -> None:
        payload = {"Message": "not valid json"}
        result = resolve_incident_window(payload, now=NOW)
        assert result.source == SOURCE_DEFAULT

    def test_until_pinned_to_now_for_very_recent_anchor(self) -> None:
        # Anchor exactly at NOW → buffer would push past now → pinned to now.
        payload = {"startsAt": NOW.isoformat().replace("+00:00", "Z")}
        result = resolve_incident_window(payload, now=NOW, forward_buffer_minutes=10)
        assert result.until == NOW
        assert result.since == NOW - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)


# ---------------------------------------------------------------------------
# Resolver fuzz: arbitrary garbage must never raise
# ---------------------------------------------------------------------------


class TestResolverFuzz:
    """Property: the resolver returns a valid window for any input."""

    @pytest.mark.parametrize(
        "garbage",
        [
            None,
            "",
            "x",
            "{",
            "{}",
            "[]",
            "null",
            42,
            3.14,
            True,
            [],
            {},
            {"unknown_key": "value"},
            {"alerts": None},
            {"alerts": "not-a-list"},
            {"alerts": [None, None, None]},
            {"event": None},
            {"event": {"data": None}},
            {"Message": None},
            {"Message": json.dumps({"AlarmName": "x"})},  # no timestamp
            {"startsAt": ""},
            {"startsAt": None},
            {"startsAt": 12345},
            {"event_time": "not-a-number"},
            {"triggered_at": ""},
        ],
    )
    def test_never_raises(self, garbage: object) -> None:
        # The hard property: the resolver must never raise. The window it
        # returns is always valid because IncidentWindow.__post_init__ enforces
        # that.
        result = resolve_incident_window(garbage, now=NOW)
        assert isinstance(result, IncidentWindow)
        assert result.since < result.until
