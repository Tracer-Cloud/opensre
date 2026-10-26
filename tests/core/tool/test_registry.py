"""Unit tests for core.tool.registry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.domain.types.tools import ToolRole
from core.tool.registry import (
    BaseToolRegistryMetadata,
    normalize_surfaces,
    normalize_tags,
)


def test_registry_metadata_defaults() -> None:
    meta = BaseToolRegistryMetadata.model_validate({})
    assert meta.surfaces == ("chat",)
    assert meta.tags == ()
    assert meta.role is ToolRole.ACTION


def test_registry_metadata_normalizes_surfaces() -> None:
    meta = BaseToolRegistryMetadata.model_validate({"surfaces": ("chat", "action")})
    assert meta.surfaces == ("chat", "action")


def test_registry_metadata_rejects_invalid_surface() -> None:
    with pytest.raises(ValidationError):
        BaseToolRegistryMetadata.model_validate({"surfaces": ("bogus",)})


def test_registry_metadata_normalizes_tags() -> None:
    meta = BaseToolRegistryMetadata.model_validate({"tags": (" fast ", "safe", "fast")})
    assert meta.tags == ("fast", "safe")


def test_normalize_surfaces_none_returns_default() -> None:
    assert normalize_surfaces(None) == ("chat",)


def test_normalize_tags_deduplicates_and_strips() -> None:
    assert normalize_tags([" logs ", "metrics", "logs"]) == ("logs", "metrics")
