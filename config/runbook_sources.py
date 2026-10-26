"""Validated runbook-source settings stored in ``~/.opensre/config.yml``."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError, field_validator

from config.local_settings import load_local_settings, save_local_settings
from config.strict_config import StrictConfigModel

_SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SECTION_NAME = "runbooks"
_SOURCES_KEY = "sources"


class RunbookSourceConfigError(RuntimeError):
    """Raised when runbook-source settings cannot be safely used."""


def _safe_relative_path(value: str, *, field_name: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
    ):
        raise ValueError(f"{field_name} must be a safe repository-relative path")
    return candidate


class RunbookSourceConfig(StrictConfigModel):
    """One trusted source of organization-owned runbooks."""

    name: str
    provider: str
    repository: str
    ref: str = "main"
    manifest: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _SOURCE_NAME_RE.fullmatch(value):
            raise ValueError("name must contain only letters, numbers, underscores, and hyphens")
        return value

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        normalized = value.lower()
        if not normalized:
            raise ValueError("provider must not be empty")
        return normalized

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        parts = value.strip("/").split("/")
        if (
            len(parts) != 2
            or any(not part or part in {".", ".."} for part in parts)
            or any(char.isspace() for char in value)
        ):
            raise ValueError("repository must use the owner/repository form")
        return "/".join(parts)

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("ref must not be empty or contain control characters")
        return value

    @field_validator("manifest")
    @classmethod
    def _validate_manifest(cls, value: str) -> str:
        return _safe_relative_path(value, field_name="manifest")


def _source_payloads(data: dict[str, Any]) -> list[dict[str, Any]]:
    section = data.get(_SECTION_NAME, {})
    if not isinstance(section, dict):
        raise RunbookSourceConfigError("Invalid runbooks config: expected a mapping")
    raw_sources = section.get(_SOURCES_KEY, [])
    if not isinstance(raw_sources, list):
        raise RunbookSourceConfigError("Invalid runbooks config: sources must be a list")
    if not all(isinstance(item, dict) for item in raw_sources):
        raise RunbookSourceConfigError("Invalid runbooks config: every source must be a mapping")
    return raw_sources


def load_runbook_sources() -> tuple[RunbookSourceConfig, ...]:
    """Load and validate configured runbook sources."""
    try:
        return tuple(
            RunbookSourceConfig.model_validate(item)
            for item in _source_payloads(load_local_settings())
        )
    except ValidationError as exc:
        raise RunbookSourceConfigError(f"Invalid runbook source: {exc}") from exc


def _save_sources(data: dict[str, Any], sources: list[RunbookSourceConfig]) -> None:
    section = data.get(_SECTION_NAME)
    updated = dict(section) if isinstance(section, dict) else {}
    updated[_SOURCES_KEY] = [source.model_dump() for source in sources]
    data[_SECTION_NAME] = updated
    save_local_settings(data)


def add_runbook_source(source: RunbookSourceConfig) -> None:
    """Persist a new source, rejecting duplicate names."""
    data = load_local_settings()
    sources = [RunbookSourceConfig.model_validate(item) for item in _source_payloads(data)]
    if any(existing.name == source.name for existing in sources):
        raise RunbookSourceConfigError(f"Runbook source {source.name!r} already exists")
    sources.append(source)
    _save_sources(data, sources)


def remove_runbook_source(name: str) -> bool:
    """Remove a source by name and return whether it existed."""
    data = load_local_settings()
    sources = [RunbookSourceConfig.model_validate(item) for item in _source_payloads(data)]
    kept = [source for source in sources if source.name != name]
    if len(kept) == len(sources):
        return False
    _save_sources(data, kept)
    return True


def get_runbook_source(name: str) -> RunbookSourceConfig | None:
    """Return one configured source by name."""
    return next((source for source in load_runbook_sources() if source.name == name), None)


__all__ = [
    "RunbookSourceConfig",
    "RunbookSourceConfigError",
    "add_runbook_source",
    "get_runbook_source",
    "load_runbook_sources",
    "remove_runbook_source",
]
