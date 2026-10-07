"""Tests for the alert inbox domain queue (HTTP intake lives in gateway.web.webapp)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from core.domain.alerts.inbox import AlertInbox, IncomingAlert


class TestIncomingAlert:
    def test_valid_minimal(self) -> None:
        alert = IncomingAlert(text="CPU spike")
        assert alert.text == "CPU spike"

    def test_valid_full(self) -> None:
        alert = IncomingAlert.model_validate(
            {
                "text": "disk full",
                "alert_name": "DiskAlert",
                "severity": "critical",
                "source": "datadog",
                "received_at": datetime.now(UTC).isoformat(),
            }
        )
        assert alert.alert_name == "DiskAlert"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError, match="Unexpected field"):
            IncomingAlert.model_validate({"text": "x", "unknown": "y"})

    def test_text_is_required(self) -> None:
        with pytest.raises(ValueError, match="Field required"):
            IncomingAlert.model_validate({})


class TestAlertInbox:
    def test_put_and_pop(self) -> None:
        inbox = AlertInbox(maxsize=3)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        assert inbox.qsize == 2
        assert inbox.pop_nowait() is not None
        assert inbox.qsize == 1

    def test_iter_pending_drains(self) -> None:
        inbox = AlertInbox(maxsize=5)
        for i in range(3):
            inbox.put(IncomingAlert(text=f"alert {i}"))
        items = inbox.iter_pending()
        assert len(items) == 3
        assert inbox.qsize == 0

    def test_pop_nowait_returns_none_when_empty(self) -> None:
        assert AlertInbox().pop_nowait() is None

    def test_drop_oldest_on_overflow(self) -> None:
        inbox = AlertInbox(maxsize=2)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        inbox.put(IncomingAlert(text="c"))
        assert inbox.qsize == 2
        assert inbox.dropped == 1
        assert [a.text for a in inbox.iter_pending()] == ["b", "c"]


class TestAlertInboxRetryQueue:
    """Failed alerts wait out a delay before the next drain may retry them.

    Requeueing deliberately does not set ``pending_event`` — the watcher's 1 s
    poll drives the retry — so a persistent failure cannot spin
    failure → requeue → immediate-wake loops.
    """

    def test_requeue_does_not_wake_or_release_before_delay(self) -> None:
        inbox = AlertInbox(maxsize=5)
        assert inbox.requeue(IncomingAlert(text="a"), delay_seconds=5.0)

        assert not inbox.pending_event.is_set()
        assert inbox.iter_pending() == []  # not due yet
        assert inbox.qsize == 0
        assert inbox.pending_retries == 1

    def test_requeued_alert_becomes_due_after_delay(self) -> None:
        inbox = AlertInbox(maxsize=5)
        assert inbox.requeue(IncomingAlert(text="a"), delay_seconds=0.05)

        time.sleep(0.1)
        items = inbox.iter_pending()

        assert [a.text for a in items] == ["a"]
        assert inbox.pending_retries == 0
        assert inbox.qsize == 0

    def test_requeue_does_not_block_fresh_alerts(self) -> None:
        """A delayed retry must not hold back alerts that arrive meanwhile."""
        inbox = AlertInbox(maxsize=5)
        assert inbox.requeue(IncomingAlert(text="waiting"), delay_seconds=30.0)

        inbox.put(IncomingAlert(text="fresh"))

        assert [a.text for a in inbox.iter_pending()] == ["fresh"]
        assert inbox.pending_retries == 1

    def test_requeue_overflow_drops_oldest_retry(self) -> None:
        inbox = AlertInbox(maxsize=1)
        assert inbox.requeue(IncomingAlert(text="a"), delay_seconds=0.05)
        assert not inbox.requeue(IncomingAlert(text="b"), delay_seconds=0.05)

        assert inbox.dropped == 1
        time.sleep(0.1)  # both delays have passed; only "b" survived eviction

        assert [a.text for a in inbox.iter_pending()] == ["b"]
        assert inbox.pending_retries == 0
