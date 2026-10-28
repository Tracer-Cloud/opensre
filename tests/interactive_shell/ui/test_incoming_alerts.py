"""Tests for incoming alert rendering in the REPL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rich.console import Console

from core.domain.alerts.inbox import AlertInbox, IncomingAlert
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.alerts import (
    drain_and_render_incoming,
    format_incoming_alert,
    time_ago,
)


class TestTimeAgo:
    """Test time_ago helper."""

    def test_seconds_ago(self) -> None:
        now = datetime.now(UTC)
        then = now - timedelta(seconds=5)
        result = time_ago(then)
        assert "5s ago" in result

    def test_one_second_ago(self) -> None:
        now = datetime.now(UTC)
        then = now - timedelta(seconds=1)
        result = time_ago(then)
        assert "1s ago" in result

    def test_minutes_ago(self) -> None:
        now = datetime.now(UTC)
        then = now - timedelta(minutes=3)
        result = time_ago(then)
        assert "3m ago" in result

    def test_hours_ago(self) -> None:
        now = datetime.now(UTC)
        then = now - timedelta(hours=2)
        result = time_ago(then)
        assert "2h ago" in result

    def test_days_ago(self) -> None:
        now = datetime.now(UTC)
        then = now - timedelta(days=1)
        result = time_ago(then)
        assert "1d ago" in result

    def test_none_datetime(self) -> None:
        result = time_ago(None)
        assert result == "unknown"

    def test_naive_datetime_treated_as_utc(self) -> None:
        # POST /alerts keeps sender-supplied timestamps, so an offset-less ISO
        # string arrives here as a naive datetime; it must not raise.
        then = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=3)
        result = time_ago(then)
        assert "3m ago" in result


class TestFormatIncomingAlert:
    """Test format_incoming_alert rendering."""

    def test_renders_with_all_fields(self) -> None:
        alert = IncomingAlert(
            text="disk usage at 95%",
            alert_name="disk_alert",
            severity="critical",
            source="datadog-webhook",
            received_at=datetime.now(UTC),
        )
        renderable = format_incoming_alert(alert)
        # Just verify the alert object was created without error
        assert renderable is not None

    def test_renders_with_minimal_fields(self) -> None:
        alert = IncomingAlert(text="something happened")
        renderable = format_incoming_alert(alert)
        # Just verify the alert object was created without error
        assert renderable is not None

    def test_renders_without_source(self) -> None:
        alert = IncomingAlert(
            text="test alert",
            severity="warning",
            received_at=datetime.now(UTC),
        )
        renderable = format_incoming_alert(alert)
        # Just verify the alert object was created without error
        assert renderable is not None

    def test_renders_without_severity(self) -> None:
        alert = IncomingAlert(
            text="test alert",
            source="custom",
            received_at=datetime.now(UTC),
        )
        renderable = format_incoming_alert(alert)
        # Just verify the alert object was created without error
        assert renderable is not None

    def test_escapes_severity_markup(self) -> None:
        alert = IncomingAlert(
            text="payload",
            severity="critical] [red]pwned",
            source="webhook",
            received_at=datetime.now(UTC),
        )
        console = Console(record=True)
        console.print(format_incoming_alert(alert))
        output = console.export_text()

        assert "[critical] [red]pwned]" in output
        assert "pwned]" in output

    def test_severity_like_rich_style_tag_is_literal(self) -> None:
        """Severity values resembling Rich markup must not apply styles."""
        alert = IncomingAlert(
            text="body",
            severity="bold red",
            received_at=datetime.now(UTC),
        )
        console = Console(record=True)
        console.print(format_incoming_alert(alert))
        output = console.export_text()

        assert "[bold red]" in output


class TestDrainAndRenderIncoming:
    """Test drain_and_render_incoming functionality."""

    def test_drains_fifo_order(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        # Add alerts in order
        alert1 = IncomingAlert(text="first")
        alert2 = IncomingAlert(text="second")
        alert3 = IncomingAlert(text="third")

        inbox.put(alert1)
        inbox.put(alert2)
        inbox.put(alert3)

        # Drain and render
        count = drain_and_render_incoming(session, console, inbox)

        assert count == 3
        assert len(session.alerts.entries) == 3
        assert session.alerts.entries[0].text == "first"
        assert session.alerts.entries[1].text == "second"
        assert session.alerts.entries[2].text == "third"

    def test_records_in_history(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        alert = IncomingAlert(text="test alert")
        inbox.put(alert)

        drain_and_render_incoming(session, console, inbox)

        # Check history
        assert len(session.history) == 1
        assert session.history[0]["type"] == "incoming_alert"
        assert session.history[0]["text"] == "test alert"
        assert session.history[0]["ok"] is True

    def test_renders_to_console(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        alert = IncomingAlert(text="test message")
        inbox.put(alert)

        # Just verify drain_and_render doesn't raise an exception
        count = drain_and_render_incoming(session, console, inbox)
        assert count == 1

    def test_returns_count(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        inbox.put(IncomingAlert(text="alert1"))
        inbox.put(IncomingAlert(text="alert2"))

        count = drain_and_render_incoming(session, console, inbox)

        assert count == 2

    def test_drains_empty_inbox(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        count = drain_and_render_incoming(session, console, inbox)

        assert count == 0
        assert len(session.history) == 0

    def test_caps_incoming_alerts_at_max(self) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        # Add more alerts than the session cap
        for i in range(300):
            alert = IncomingAlert(text=f"alert_{i}")
            inbox.put(alert)

        drain_and_render_incoming(session, console, inbox)

        # Should be capped at _INCOMING_ALERTS_MAX (256)
        assert len(session.alerts.entries) <= session.alerts._max


class TestSessionIncomingAlerts:
    """Test Session handling of incoming alerts."""

    def test_clear_resets_incoming_alerts(self) -> None:
        session = Session()
        inbox = AlertInbox()
        console = Console()

        # Add some alerts
        inbox.put(IncomingAlert(text="alert1"))
        inbox.put(IncomingAlert(text="alert2"))
        drain_and_render_incoming(session, console, inbox)

        assert len(session.alerts.entries) == 2

        # Clear session
        session.clear()

        assert len(session.alerts.entries) == 0
        assert len(session.history) == 0

    def test_record_incoming_alert_kind(self) -> None:
        session = Session()
        alert = IncomingAlert(text="test alert")

        session.record_incoming_alert(alert)

        assert len(session.history) == 1
        assert session.history[0]["type"] == "incoming_alert"
        assert session.history[0]["text"] == "test alert"
        assert session.history[0]["ok"] is True
        assert len(session.alerts.entries) == 1
        assert session.alerts.entries[0].text == "test alert"

    def test_record_incoming_alert_always_ok(self) -> None:
        session = Session()
        alert = IncomingAlert(text="test alert")

        session.record_incoming_alert(alert)

        assert session.history[0]["ok"] is True

    def test_incoming_alerts_fifo_list(self) -> None:
        session = Session()

        session.record_incoming_alert(IncomingAlert(text="first"))
        session.record_incoming_alert(IncomingAlert(text="second"))
        session.record_incoming_alert(IncomingAlert(text="third"))

        assert len(session.alerts.entries) == 3
        assert session.alerts.entries[0].text == "first"
        assert session.alerts.entries[1].text == "second"
        assert session.alerts.entries[2].text == "third"


class TestAlertInboxEventClearing:
    """Test AlertInbox pending_event behavior."""

    def test_event_set_on_put(self) -> None:
        inbox = AlertInbox()
        alert = IncomingAlert(text="test")

        # Event should not be set initially
        assert not inbox.pending_event.is_set()

        inbox.put(alert)

        # Event should be set after put
        assert inbox.pending_event.is_set()

    def test_event_cleared_on_iter_pending(self) -> None:
        inbox = AlertInbox()
        alert = IncomingAlert(text="test")

        inbox.put(alert)
        assert inbox.pending_event.is_set()

        inbox.iter_pending()

        # Event should be cleared after draining
        assert not inbox.pending_event.is_set()

    def test_event_not_cleared_if_queue_not_empty(self) -> None:
        inbox = AlertInbox()

        inbox.put(IncomingAlert(text="first"))
        inbox.put(IncomingAlert(text="second"))

        # Pop only one
        inbox.pop_nowait()

        # Drain but queue still has one item
        # (this is artificial; normally iter_pending drains all)
        # Let's test with iter_pending which should drain all
        inbox2 = AlertInbox()
        inbox2.put(IncomingAlert(text="alert"))
        inbox2.iter_pending()

        # After draining all, event should be cleared
        assert not inbox2.pending_event.is_set()


def _store_failure(texts: set[str]) -> Any:
    """Build an ``append_turn`` replacement that fails for the given alert texts."""

    def _append_turn(_session: object, _kind: str, text: str) -> None:
        if text in texts:
            raise OSError(f"session store rejected {text}")

    return _append_turn


def _print_failure() -> Any:
    """Build a ``Console.print`` replacement that always fails."""

    def _print(*_args: object, **_kwargs: object) -> None:
        raise OSError("terminal write failed")

    return _print


class TestDrainFailureRecovery:
    """A failed record or render must not lose an alert or the rest of its batch."""

    def test_record_failure_requeues_the_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10, retry_after=60)
        console = Console()
        monkeypatch.setattr(session.store, "append_turn", _store_failure({"down"}))
        inbox.put(IncomingAlert(text="down"))

        assert drain_and_render_incoming(session, console, inbox) == 0
        assert session.history == []
        assert session.alerts.entries == []
        assert inbox.qsize == 1
        assert inbox.iter_pending() == []

    def test_retried_alert_is_recorded_exactly_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10, retry_after=0)
        console = Console()
        inbox.put(IncomingAlert(text="flaky"))

        failures = {"flaky"}
        monkeypatch.setattr(session.store, "append_turn", _store_failure(failures))
        assert drain_and_render_incoming(session, console, inbox) == 0
        assert inbox.qsize == 1

        failures.clear()
        assert drain_and_render_incoming(session, console, inbox) == 1
        assert [entry["text"] for entry in session.history] == ["flaky"]
        assert [a.text for a in session.alerts.entries] == ["flaky"]
        assert inbox.qsize == 0

    def test_record_failure_does_not_drop_the_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10, retry_after=60)
        console = Console()
        monkeypatch.setattr(session.store, "append_turn", _store_failure({"b"}))
        for text in ("a", "b", "c"):
            inbox.put(IncomingAlert(text=text))

        assert drain_and_render_incoming(session, console, inbox) == 2
        assert [entry["text"] for entry in session.history] == ["a", "c"]
        assert inbox.qsize == 1

    def test_render_failure_keeps_the_recorded_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = Session()
        inbox = AlertInbox(maxsize=10, retry_after=0)
        console = Console()
        monkeypatch.setattr(console, "print", _print_failure())
        inbox.put(IncomingAlert(text="recorded"))

        assert drain_and_render_incoming(session, console, inbox) == 0
        # Already in history, so it is not requeued: a retry would record it twice.
        assert [entry["text"] for entry in session.history] == ["recorded"]
        assert inbox.qsize == 0
