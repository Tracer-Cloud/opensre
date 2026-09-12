"""Slack proactive-message composition."""

from __future__ import annotations

from gateway.transports.slack.proactive.assembly import build_proactive_message_service

__all__ = ["build_proactive_message_service"]
