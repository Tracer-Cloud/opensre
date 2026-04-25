"""Compatibility shim for moved Notion client."""

import warnings

from app.services.notion import NotionClient, NotionConfig

warnings.warn(
    "app.integrations.clients.notion is deprecated. Use app.services.notion instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["NotionClient", "NotionConfig"]
