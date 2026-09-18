"""Ports used by the vendor-neutral proactive judgement service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from config.principal import StorageScope


class ProactiveThreadHistory(Protocol):
    def __call__(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        limit: int,
    ) -> Mapping[str, Any]:
        """Return bounded read-only context for the originating conversation."""


class ProactiveDelivery(Protocol):
    def __call__(self, *, channel_id: str, thread_ts: str, message: str) -> str | None:
        """Deliver one message to the fixed originating thread and return its identifier."""


class ProactiveMessageScheduler(Protocol):
    def capture_boundary(self, session_id: str) -> str | None:
        """Return the current durable session-record boundary."""

    def enqueue(
        self,
        *,
        scope: StorageScope,
        session_id: str,
        start_record_id: str | None,
        channel_id: str,
        thread_ts: str,
        user_id: str,
    ) -> bool:
        """Queue review of records appended after ``start_record_id`` without blocking."""


__all__ = ["ProactiveDelivery", "ProactiveMessageScheduler", "ProactiveThreadHistory"]
