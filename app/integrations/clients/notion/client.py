"""Backwards-compatibility shim for the Notion client.

The canonical location is now ``app.services.notion.client``.
This module re-exports everything so existing imports continue to work.

See: https://github.com/Tracer-Cloud/opensre/issues/898
"""

import warnings as _warnings

from app.services.notion.client import NotionClient, NotionConfig  # noqa: F401

_warnings.warn(
    "Import from app.integrations.clients.notion.client is deprecated. "
    "Use app.services.notion.client instead.",
    DeprecationWarning,
    stacklevel=2,
)
