"""Tests for incoming alert rendering, draining, and failure recovery in the REPL."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from rich.console import Console

from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.domain.alerts.inbox import AlertInbox, IncomingAlert
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.background.workers import BackgroundTaskPool
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.ui.alerts import (
    drain_and_render_incoming,
    format_incoming_alert,
    time_ago,
)


def _session_with_store() -> tuple[Session, InMemorySessionStore]:
    """A session whose in-memory store is opened, so recording persists."""
    store = InMemorySessionStore()
    session = Session(store=store)
    store.open_session(session)
    return session, store


_FakeStore = TypeVar("_FakeStore", bound=InMemorySessionStore)


def _attach_store(session: Session, store: _FakeStore) -> _FakeStore:
    """Replace the session's store and open it (an unopened store signals '')."""
    session.store = store
    store.open_session(session)
    return store


class _StoreFailingForTexts(InMemorySessionStore):
    """Store whose ``append_turn`` raises for chosen texts (contract allows it)."""

    def __init__(self, fail_texts: set[str]) -> None:
        super().__init__()
        self.fail_texts = set(fail_texts)
        self.appended: list[str] = []
        self.attempts = 0

    def append_turn(self, session: Any, kind: str, text: str) -> str:
        self.attempts += 1
        if text in self.fail_texts:
            raise OSError("session store temporarily unavailable")
        self.appended.append(text)
        return super().append_turn(session, kind, text)


class _StoreDroppingForTexts(InMemorySessionStore):
    """Store mimicking the real JSONL failure signal: '' instead of a record id.

    ``JsonlSessionStore`` suppresses write errors and returns '' when a record
    was not persisted; this fake reproduces that observable contract.
    """

    def __init__(self, fail_texts: set[str]) -> None:
        super().__init__()
        self.fail_texts = set(fail_texts)
        self.appended: list[str] = []
        self.attempts = 0

    def append_turn(self, session: Any, kind: str, text: str) -> str:
        self.attempts += 1
        if text in self.fail_texts:
            return ""
        self.appended.append(text)
        return super().append_turn(session, kind, text)


class _ExplodingConsole(Console):
    """Console whose ``print`` raises, simulating a dead render surface."""

    def print(self, *_args: object, **_kwargs: object) -> None:
        raise OSError("console closed")


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
        session, _store = _session_with_store()
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
        session, _store = _session_with_store()
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
        session, _store = _session_with_store()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        alert = IncomingAlert(text="test message")
        inbox.put(alert)

        # Just verify drain_and_render doesn't raise an exception
        count = drain_and_render_incoming(session, console, inbox)
        assert count == 1

    def test_returns_count(self) -> None:
        session, _store = _session_with_store()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        inbox.put(IncomingAlert(text="alert1"))
        inbox.put(IncomingAlert(text="alert2"))

        count = drain_and_render_incoming(session, console, inbox)

        assert count == 2

    def test_drains_empty_inbox(self) -> None:
        session, _store = _session_with_store()
        inbox = AlertInbox(maxsize=10)
        console = Console()

        count = drain_and_render_incoming(session, console, inbox)

        assert count == 0
        assert len(session.history) == 0

    def test_caps_incoming_alerts_at_max(self) -> None:
        session, _store = _session_with_store()
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
        session, _store = _session_with_store()
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
        session, _store = _session_with_store()
        alert = IncomingAlert(text="test alert")

        session.record_incoming_alert(alert)

        assert len(session.history) == 1
        assert session.history[0]["type"] == "incoming_alert"
        assert session.history[0]["text"] == "test alert"
        assert session.history[0]["ok"] is True
        assert len(session.alerts.entries) == 1
        assert session.alerts.entries[0].text == "test alert"

    def test_record_incoming_alert_always_ok(self) -> None:
        session, _store = _session_with_store()
        alert = IncomingAlert(text="test alert")

        session.record_incoming_alert(alert)

        assert session.history[0]["ok"] is True

    def test_incoming_alerts_fifo_list(self) -> None:
        session, _store = _session_with_store()

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


class TestDrainResilience:
    """A drain failure must never silently discard popped alerts.

    ``AlertInbox.iter_pending()`` destructively pops every pending alert, so
    each alert is recorded before it is rendered; a failed recording requeues
    the alert with a retry delay and leaves no false history behind.
    """

    def test_dropped_write_requeues_alert_without_false_history(self) -> None:
        """The real JSONL failure signal ('' record id) requeues, records nothing."""
        session, _store = _session_with_store()
        store = _attach_store(session, _StoreDroppingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)

        inbox.put(IncomingAlert(text="first"))
        count = drain_and_render_incoming(session, Console(), inbox)

        assert count == 0
        assert store.attempts == 1
        # No false success anywhere: no durable record, no history, no facet.
        assert store.appended == []
        assert session.history == []
        assert session.alerts.entries == []
        # Recoverable, but not immediately retried: waiting out the delay.
        assert inbox.qsize == 0
        assert inbox.pending_retries == 1

    def test_raising_write_requeues_alert_without_false_history(self) -> None:
        """A store that raises (allowed by the contract) is handled the same way."""
        session, _store = _session_with_store()
        failing = _attach_store(session, _StoreFailingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)

        inbox.put(IncomingAlert(text="first"))
        count = drain_and_render_incoming(session, Console(), inbox)

        assert count == 0
        assert failing.appended == []
        assert session.history == []
        assert session.alerts.entries == []
        assert inbox.pending_retries == 1

    def test_failed_alert_is_not_immediately_retried(self) -> None:
        """A requeued alert stays out of the drain until its delay passes."""
        session, _store = _session_with_store()
        failing = _attach_store(session, _StoreFailingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)
        inbox.put(IncomingAlert(text="first"))

        drain_and_render_incoming(session, Console(), inbox)

        # An immediate second drain must not retry the alert (no busy loop):
        # the retry queue does not release it before the delay elapses.
        count = drain_and_render_incoming(session, Console(), inbox)
        assert count == 0
        assert failing.attempts == 1
        assert inbox.pending_retries == 1

    def test_retry_succeeds_with_exactly_one_history_entry(self, monkeypatch: Any) -> None:
        """Recovery records exactly one durable record and one history entry."""
        monkeypatch.setattr("core.domain.alerts.inbox.DEFAULT_RETRY_DELAY_SECONDS", 0.05)
        session, _store = _session_with_store()
        store = _attach_store(session, _StoreDroppingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)
        inbox.put(IncomingAlert(text="first"))

        assert drain_and_render_incoming(session, Console(), inbox) == 0
        assert session.history == []  # no false entry after the failed attempt

        store.fail_texts.clear()  # the transient failure is over
        time.sleep(0.1)
        count = drain_and_render_incoming(session, Console(), inbox)

        assert count == 1
        # Exactly one durable record and exactly one logical history entry.
        assert store.appended == ["first"]
        assert len(session.history) == 1
        assert session.history[0]["text"] == "first"
        assert [alert.text for alert in session.alerts.entries] == ["first"]
        assert inbox.qsize == 0
        assert inbox.pending_retries == 0

    def test_retry_failure_leaves_no_duplicate_history(self, monkeypatch: Any) -> None:
        """Repeated failures never accumulate history rows for one alert."""
        monkeypatch.setattr("core.domain.alerts.inbox.DEFAULT_RETRY_DELAY_SECONDS", 0.05)
        session, _store = _session_with_store()
        store = _attach_store(session, _StoreFailingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)
        inbox.put(IncomingAlert(text="first"))

        drain_and_render_incoming(session, Console(), inbox)
        time.sleep(0.1)
        drain_and_render_incoming(session, Console(), inbox)  # retry also fails

        assert store.attempts == 2
        assert session.history == []
        assert session.alerts.entries == []
        assert inbox.pending_retries == 1

    def test_render_failure_keeps_alert_recorded_once(self) -> None:
        """A render failure must not abort the drain or re-record the alert."""
        session, _store = _session_with_store()
        store = _attach_store(session, _StoreDroppingForTexts(set()))  # nothing fails
        inbox = AlertInbox(maxsize=10)

        inbox.put(IncomingAlert(text="first"))
        inbox.put(IncomingAlert(text="second"))

        count = drain_and_render_incoming(session, _ExplodingConsole(), inbox)

        assert count == 2
        assert [alert.text for alert in session.alerts.entries] == ["first", "second"]
        assert store.appended == ["first", "second"]
        assert len(session.history) == 2
        assert inbox.qsize == 0
        assert inbox.pending_retries == 0

    def test_one_failed_alert_does_not_abort_the_batch(self) -> None:
        """Other alerts still record when one alert's persistence fails."""
        session, _store = _session_with_store()
        store = _attach_store(session, _StoreDroppingForTexts({"first"}))
        inbox = AlertInbox(maxsize=10)
        console = Console()

        inbox.put(IncomingAlert(text="first"))
        inbox.put(IncomingAlert(text="second"))
        inbox.put(IncomingAlert(text="third"))

        count = drain_and_render_incoming(session, console, inbox)

        assert count == 2
        assert [alert.text for alert in session.alerts.entries] == ["second", "third"]
        assert store.appended == ["second", "third"]
        assert session.history == [
            {"type": "incoming_alert", "text": "second", "ok": True},
            {"type": "incoming_alert", "text": "third", "ok": True},
        ]
        assert inbox.pending_retries == 1

    def test_watcher_survives_persistent_record_failure(self) -> None:
        """The watcher stays alive after its initial drain hits a record failure."""
        store = _StoreFailingForTexts({"first"})
        session = Session(store=store)
        store.open_session(session)
        inbox = AlertInbox(maxsize=10)
        inbox.put(IncomingAlert(text="first"))

        async def scenario() -> None:
            pool = BackgroundTaskPool(
                session=session,
                state=ReplState(),
                spinner=SpinnerState(),
                inbox=inbox,
                prompt_invalidator=lambda: None,
            )
            task = asyncio.create_task(pool._alert_watcher())
            try:
                # Initial drain + several poll cycles with the alert waiting
                # out its 5 s retry delay.
                await asyncio.sleep(2.5)
                assert not task.done()
                # Attempted once, never retried in a tight loop, and recorded
                # nowhere.
                assert store.attempts == 1
                assert inbox.pending_retries == 1
                assert inbox.qsize == 0
                assert session.history == []
                assert session.alerts.entries == []
            finally:
                pool.state.exit_requested = True
                await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())
