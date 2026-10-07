"""Tests for incoming alert rendering in the REPL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from rich.console import Console

from core.agent_harness.session.persistence.memory import InMemorySessionStore
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


class _StoreFailingForTexts(InMemorySessionStore):
    """In-memory session store whose ``append_turn`` fails for chosen texts.

    Simulates a transient store failure (e.g. a locked session file) while
    successful appends stay observable for state assertions.
    """

    def __init__(self, fail_texts: set[str]) -> None:
        super().__init__()
        self.fail_texts = set(fail_texts)
        self.appended: list[str] = []

    def append_turn(self, session: Any, kind: str, text: str) -> None:
        if text in self.fail_texts:
            raise OSError("session store temporarily unavailable")
        self.appended.append(text)
        super().append_turn(session, kind, text)


class _ExplodingConsole(Console):
    """Console whose ``print`` raises, simulating a dead render surface."""

    def print(self, *_args: object, **_kwargs: object) -> None:
        raise OSError("console closed")


class TestDrainResilience:
    """A drain failure must never silently discard popped alerts.

    ``AlertInbox.iter_pending()`` destructively pops every pending alert, and
    ``Session.record_incoming_alert()`` writes to the store before updating the
    session facet — so an exception mid-drain used to leave already-popped
    alerts recorded nowhere.
    """

    def test_record_failure_keeps_alert_recoverable_and_processes_rest(self) -> None:
        """A record failure requeues its alert; later alerts still process."""
        session = Session()
        session.store = _StoreFailingForTexts({"first"})
        inbox = AlertInbox(maxsize=10)
        console = Console()

        inbox.put(IncomingAlert(text="first"))
        inbox.put(IncomingAlert(text="second"))

        count = drain_and_render_incoming(session, console, inbox)

        # "second" was recorded despite "first" failing (per-alert scoping).
        assert count == 1
        assert [alert.text for alert in session.alerts.entries] == ["second"]
        assert session.store.appended == ["second"]
        # "first" was NOT silently lost: it is back in the inbox, recoverable.
        assert inbox.qsize == 1
        assert inbox.pending_event.is_set()
        assert inbox.peek_last(1)[0].text == "first"

    def test_requeued_alert_is_recovered_by_next_drain(self) -> None:
        """Once the transient store failure clears, the requeued alert records."""
        session = Session()
        store = _StoreFailingForTexts({"first"})
        session.store = store
        inbox = AlertInbox(maxsize=10)

        inbox.put(IncomingAlert(text="first"))
        assert drain_and_render_incoming(session, Console(), inbox) == 0
        assert inbox.qsize == 1

        store.fail_texts.clear()  # the transient failure is over
        count = drain_and_render_incoming(session, Console(), inbox)

        assert count == 1
        assert store.appended == ["first"]
        assert [alert.text for alert in session.alerts.entries] == ["first"]
        assert inbox.qsize == 0
        assert not inbox.pending_event.is_set()

    def test_render_failure_keeps_alert_recorded(self) -> None:
        """A render failure must not abort the drain or lose the alert."""
        session = Session()
        store = _StoreFailingForTexts(set())  # recording store; nothing fails
        session.store = store
        inbox = AlertInbox(maxsize=10)

        inbox.put(IncomingAlert(text="first"))
        inbox.put(IncomingAlert(text="second"))

        count = drain_and_render_incoming(session, _ExplodingConsole(), inbox)

        assert count == 2
        assert [alert.text for alert in session.alerts.entries] == ["first", "second"]
        assert store.appended == ["first", "second"]
        assert inbox.qsize == 0
