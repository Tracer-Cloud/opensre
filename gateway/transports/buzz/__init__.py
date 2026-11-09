"""Buzz poll transport for the gateway.

Inbound Buzz messaging: settings, mention-feed poller, inbound authorization,
the edit-in-place turn output, approvals, and the background worker. The
per-message handler it drives is transport-agnostic and injected by the
composition root (:mod:`gateway.core.lifecycle.controller`). Mirrors
:mod:`gateway.transports.telegram` (poll-based, not a persistent socket).

Transport entry: :mod:`gateway.transports.buzz.startup` (``start_buzz_worker``).
"""

from __future__ import annotations
