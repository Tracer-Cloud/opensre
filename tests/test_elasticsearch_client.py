"""Tests for the Elasticsearch client and tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.state import EvidenceSource


def test_evidence_source_includes_elasticsearch() -> None:
    assert "elasticsearch" in EvidenceSource.__args__  # type: ignore[attr-defined]
