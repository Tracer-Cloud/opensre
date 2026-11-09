"""One row in the chat-transport registry: name, how to start, status when up."""

from __future__ import annotations

from dataclasses import dataclass

from gateway.transports.names import TransportName
from gateway.transports.worker import TransportStarter


@dataclass(frozen=True)
class TransportRegistration:
    name: TransportName
    start: TransportStarter
    running_status: str
