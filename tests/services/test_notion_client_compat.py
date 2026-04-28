"""Test that the legacy import path for NotionClient still works.

See: https://github.com/Tracer-Cloud/opensre/issues/898
"""

from __future__ import annotations

import pytest


def test_legacy_import_path_still_works() -> None:
    """app.integrations.clients.notion.client must re-export NotionClient."""
    with pytest.warns(DeprecationWarning, match="app.services.notion.client"):
        from app.integrations.clients.notion.client import NotionClient, NotionConfig  # noqa: F401

    assert NotionClient is not None
    assert NotionConfig is not None


def test_canonical_import_path_works() -> None:
    """app.services.notion.client is the new canonical location."""
    from app.services.notion.client import NotionClient, NotionConfig  # noqa: F401

    assert NotionClient is not None
    assert NotionConfig is not None
