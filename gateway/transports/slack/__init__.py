"""Slack Socket Mode transport for the gateway.

Layout:
- ``inbound/`` — event parsing, authorization, attachments, dispatcher
- ``outbound/`` — thread-reply posting, approvals, feedback, streamed replies
- ``connection/`` — Socket Mode worker and heartbeat
- package root — ``settings``, ``client``, ``startup``

Inbound messages go to the injected turn callback. This package is inbound
chat; Slack webhooks and RCA delivery stay in the Slack integration.

Start with :func:`gateway.transports.slack.startup.start_slack_worker`.
"""

from __future__ import annotations
