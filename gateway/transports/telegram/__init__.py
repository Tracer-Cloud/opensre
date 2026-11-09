"""Telegram long-poll transport for the gateway.

Inbound Telegram messaging: settings, poller, inbound authorization, the
edit-in-place turn output, and the background worker. The per-message handler
it drives is transport-agnostic and injected by the composition root
(:mod:`gateway.core.lifecycle.controller`). Mirrors :mod:`gateway.transports.slack`.

Transport entry: :mod:`gateway.transports.telegram.startup` (``start_telegram_worker``).
"""

from __future__ import annotations
