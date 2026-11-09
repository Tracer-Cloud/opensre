"""Validation and normalization for GitHub-hosted runbook manifests."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from config.constants.runbooks import RUNBOOK_MANIFEST_MAX_CHARS
from config.strict_config import StrictConfigModel
from core.domain.runbooks import RunbookCatalogEntry, RunbookMatch


class ManifestError(ValueError):
    """Raised when a runbook manifest is malformed or unsafe."""


def _safe_markdown_path(value: str) -> str:
    candidate = value.strip()
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
        or path.suffix.lower() != ".md"
    ):
        raise ValueError("document must be a safe repository-relative Markdown path")
    return candidate


class _ManifestMatch(StrictConfigModel):
    alertname: str = ""
    service: str = ""
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_primary_match(self) -> _ManifestMatch:
        if not self.alertname and not self.service:
            raise ValueError("match requires alertname or service")
        return self


class _ManifestEntry(StrictConfigModel):
    id: str
    document: str
    title: str = ""
    match: _ManifestMatch

    @model_validator(mode="after")
    def _validate_values(self) -> _ManifestEntry:
        if not self.id:
            raise ValueError("id must not be empty")
        self.document = _safe_markdown_path(self.document)
        return self


class _Manifest(StrictConfigModel):
    version: Literal[1]
    runbooks: list[_ManifestEntry]

    @model_validator(mode="after")
    def _require_unique_ids(self) -> _Manifest:
        ids = [entry.id for entry in self.runbooks]
        if len(ids) != len(set(ids)):
            raise ValueError("runbook ids must be unique")
        return self


def parse_manifest(content: str) -> tuple[RunbookCatalogEntry, ...]:
    """Parse V1 YAML into provider-neutral catalog entries."""
    if len(content) > RUNBOOK_MANIFEST_MAX_CHARS:
        raise ManifestError("Runbook manifest exceeds the supported size.")
    try:
        raw: Any = yaml.safe_load(content)
        manifest = _Manifest.model_validate(raw)
    except Exception as exc:
        raise ManifestError("Runbook manifest is invalid.") from exc

    return tuple(
        RunbookCatalogEntry(
            document_id=entry.id,
            path=entry.document,
            title=entry.title,
            match=RunbookMatch(
                alertname=entry.match.alertname,
                service=entry.match.service,
                labels=tuple(sorted(entry.match.labels.items())),
            ),
        )
        for entry in manifest.runbooks
    )


__all__ = ["ManifestError", "parse_manifest"]
