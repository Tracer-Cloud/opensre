"""Poll ``buzz feed get`` for mentions and yield normalized inbound messages."""

from __future__ import annotations

import logging
import threading
import time

from gateway.transports.buzz.poller.cursor import load_cursor, save_cursor
from gateway.transports.buzz.poller.parse_buzz_event import parse_feed_event
from gateway.transports.buzz.settings import BuzzInboundMessage
from integrations.buzz import BuzzClient

logger = logging.getLogger(__name__)

_WARNING_COOLDOWN_SECONDS = 60.0
_FEED_TYPES = "mentions"


class BuzzFeedPoller:
    """Poll the mention feed and yield normalized inbound messages.

    **The cursor tracks handled work, not fetched work.** ``poll_once`` marks
    what it returns as in flight; only :meth:`acknowledge` — called once a turn
    has actually run — records it as handled. A turn that dies mid-flight, or
    that shutdown cuts short, is therefore re-delivered on the next start
    instead of being silently skipped.

    Two rules define the whole thing:

    * ``_since`` may cover a second only once no in-flight turn sits at or
      below it, because ``feed get --since`` is inclusive (NIP-01: matches
      ``created_at >= since``) and has one-second resolution. Advancing onto
      an in-flight event's second would skip it on restart.
    * ``_handled`` holds every handled event at or above ``_since``, and is
      persisted whole on every acknowledgement. Turns finish out of order, so
      a newer completion routinely cannot be folded into the watermark yet —
      keeping it only in memory is what makes it replay after a restart.

    Everything strictly below ``_since`` is handled by definition, so pruning
    those IDs keeps both the map and the file bounded.
    """

    def __init__(self, client: BuzzClient) -> None:
        self._client = client
        self._lock = threading.Lock()
        state = load_cursor()
        self._since = state.since
        self._handled: dict[str, int] = dict(state.handled)
        # event_id -> created_at for events dispatched but not yet handled.
        self._inflight: dict[str, int] = {}
        self._last_warning_monotonic = 0.0

    def poll_once(self) -> list[BuzzInboundMessage]:
        """Fetch events not yet dispatched, and mark them in flight."""
        result = self._client.get_feed(since=self._since, types=_FEED_TYPES)
        if not result["success"]:
            self._log_transient("[buzz-gateway] feed get failed: %s", result["error"])
            return []

        fresh: list[BuzzInboundMessage] = []
        with self._lock:
            for raw in result["events"]:
                if not isinstance(raw, dict):
                    continue
                parsed = parse_feed_event(raw)
                if parsed is None:
                    continue
                if parsed.event_id in self._inflight or parsed.event_id in self._handled:
                    continue
                self._inflight[parsed.event_id] = parsed.created_at
                fresh.append(parsed)
        return fresh

    def acknowledge(self, event: BuzzInboundMessage) -> None:
        """Record one dispatched event as handled and persist the new state.

        Idempotent — only the first call for an event counts, so the turn's
        own acknowledgement and the dispatcher's fallback cannot double-count.
        """
        with self._lock:
            created_at = self._inflight.pop(event.event_id, None)
            if created_at is None:
                return
            self._handled[event.event_id] = created_at
            self._advance_watermark()
            save_cursor(self._since, self._handled)

    def inflight_count(self) -> int:
        """How many dispatched events have not been acknowledged yet."""
        with self._lock:
            return len(self._inflight)

    def _advance_watermark(self) -> None:
        """Move ``_since`` as far as in-flight work allows, then prune."""
        oldest_inflight = min(self._inflight.values(), default=None)
        if oldest_inflight is None:
            # Nothing running: every handled second is covered. Stop *at* the
            # newest rather than past it — another event may share that second.
            ceiling = max(self._handled.values(), default=self._since)
        else:
            # Cannot cover the second an unfinished turn sits on.
            ceiling = oldest_inflight
        self._since = max(self._since, ceiling)
        self._handled = {
            event_id: created_at
            for event_id, created_at in self._handled.items()
            if created_at >= self._since
        }

    def _log_transient(self, message: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_warning_monotonic < _WARNING_COOLDOWN_SECONDS:
            logger.debug(message, *args)
            return
        self._last_warning_monotonic = now
        logger.warning(message, *args)


__all__ = ["BuzzFeedPoller"]
