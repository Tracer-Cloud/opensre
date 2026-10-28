"""Tests for the alert inbox domain queue (HTTP intake lives in gateway.web.webapp)."""

from __future__ import annotations

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


class TestRequeue:
    """Alerts handed back by a failed drain stay recoverable instead of being lost."""

    def test_requeued_alert_waits_until_it_is_due(self) -> None:
        inbox = AlertInbox(maxsize=4, retry_after=60)
        inbox.requeue(IncomingAlert(text="later"))

        assert inbox.qsize == 1
        assert inbox.iter_pending() == []
        assert not inbox.pending_event.is_set()

    def test_due_requeue_is_drained(self) -> None:
        inbox = AlertInbox(maxsize=4, retry_after=0)
        alert = IncomingAlert(text="now")
        inbox.requeue(alert)

        assert inbox.iter_pending() == [alert]
        assert inbox.qsize == 0

    def test_requeued_alerts_keep_requeue_order(self) -> None:
        inbox = AlertInbox(maxsize=4, retry_after=0)
        first = IncomingAlert(text="first")
        second = IncomingAlert(text="second")
        inbox.requeue(first)
        inbox.requeue(second)

        assert inbox.iter_pending() == [first, second]

    def test_requeue_evicts_ready_alert_when_inbox_is_full(self) -> None:
        inbox = AlertInbox(maxsize=2, retry_after=60)
        retry = IncomingAlert(text="retry")
        inbox.put(IncomingAlert(text="ready"))
        inbox.requeue(retry)

        new = IncomingAlert(text="new")
        assert inbox.put(new) is False
        assert inbox.dropped == 1
        assert inbox.qsize == 2
        # The ready alert was evicted and the retried one kept: a retry already
        # failed once, so it is not the one to drop on pressure.
        assert inbox.iter_pending() == [new]
        assert inbox.qsize == 1
