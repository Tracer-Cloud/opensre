"""Web surface — FastAPI app and API routes.

Primary entry: :mod:`gateway.web.webapp` (``app``) — used by
``uvicorn gateway.web.webapp:app`` when ``MODE=web``, and by the gateway
daemon / interactive shell via :mod:`gateway.web.web_server`.

Not a chat transport: does not bind a turn runner or turn output. May import
``gateway.core``; must not import ``gateway.transports``.
"""

from __future__ import annotations
