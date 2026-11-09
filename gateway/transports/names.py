"""Chat transports the gateway can serve.

``TransportName`` is the key in :attr:`gateway.startup.StartedGateway.transports`
and in the component status map, so status keys and lookups cannot drift.
Web is not a member: it is a channel but not a chat transport.
"""

from __future__ import annotations

from enum import StrEnum


class TransportName(StrEnum):
    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    BUZZ = "buzz"
