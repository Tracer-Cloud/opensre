"""Standalone messaging gateway for inbound chat platforms.

Subpackages:

* ``core/`` — process + leaf infrastructure (``host``, ``process``,
  ``runtime``, ``storage``, ``billing``, ``attachments``, ``session``,
  ``config``)
* ``transports/`` — chat peers (``slack``, ``discord``, ``telegram``, ``buzz``)
* ``web/`` — web surface (FastAPI health / alerts; not a chat transport)

Entry points:

* Production — ``opensre gateway start`` (CLI injects slash ports into ``GatewayController``)
* Package main — ``python -m gateway`` fails closed (guard in ``__main__.py``)
* Composition root — :mod:`gateway.core.lifecycle.controller`
* Daemon helpers — :mod:`gateway.core.process.supervision`
* HTTP app (``MODE=web``) — :mod:`gateway.web.webapp` (``app``)
* Telegram — :mod:`gateway.transports.telegram.startup`
* Slack — :mod:`gateway.transports.slack.startup`
* Discord — :mod:`gateway.transports.discord.startup`
"""

from __future__ import annotations

__all__: list[str] = []
