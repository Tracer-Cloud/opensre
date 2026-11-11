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
        inbox = AlertInbox()
        assert inbox.pop_nowait() is None
        assert not inbox.pending_event.is_set()

    def test_pop_nowait_clears_pending_event_when_empty(self) -> None:
        inbox = AlertInbox(maxsize=3)
        inbox.put(IncomingAlert(text="first"))
        assert inbox.pending_event.is_set()
        popped = inbox.pop_nowait()
        assert popped is not None and popped.text == "first"
        assert not inbox.pending_event.is_set()

    def test_peek_last_with_zero_or_negative_returns_empty(self) -> None:
        inbox = AlertInbox(maxsize=3)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        assert inbox.peek_last(0) == []
        assert inbox.peek_last(-1) == []
        assert [alert.text for alert in inbox.peek_last(1)] == ["b"]
        assert [alert.text for alert in inbox.peek_last(2)] == ["a", "b"]

    def test_rejects_non_positive_maxsize(self) -> None:
        with pytest.raises(ValueError, match="maxsize must be at least 1"):
            AlertInbox(maxsize=0)
        with pytest.raises(ValueError, match="maxsize must be at least 1"):
            AlertInbox(maxsize=-5)

    def test_drop_oldest_on_overflow(self) -> None:
        inbox = AlertInbox(maxsize=2)
        inbox.put(IncomingAlert(text="a"))
        inbox.put(IncomingAlert(text="b"))
        inbox.put(IncomingAlert(text="c"))
        assert inbox.qsize == 2
        assert inbox.dropped == 1
        assert [a.text for a in inbox.iter_pending()] == ["b", "c"]
