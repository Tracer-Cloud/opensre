"""Slack message-context prefix stripper.

The gateway prepends a ``[Slack channel_id=… thread_ts=…]`` context line to
raw message text before core turn/prompt helpers see it. Registered with
:func:`infrastructure.harness_providers.register_message_context_prefix_stripper` from
``integrations/harness_adapters.py`` so core can strip it without importing
this module directly.
"""

from __future__ import annotations

import re

_SLACK_CONTEXT_PREFIX_RE = re.compile(r"(?is)^\s*\[Slack[^\]]*\]\s*")


def strip_slack_context_prefix(text: str) -> tuple[str, str] | None:
    """Return ``(prefix, remainder)`` when ``text`` starts with a Slack context
    marker, else ``None``."""
    match = _SLACK_CONTEXT_PREFIX_RE.match(text)
    if not match:
        return None
    return match.group(0), text[match.end() :]


__all__ = ["strip_slack_context_prefix"]
