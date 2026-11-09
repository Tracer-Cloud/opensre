"""Chat transports: Slack, Discord, Telegram, Buzz.

Each package owns its settings, inbound worker, security, turn output, and
``startup``. Peers do not import each other. Shared turn steps come from
``gateway.core``. The process starts every configured transport in one pass.
"""

from __future__ import annotations

__all__: list[str] = []
