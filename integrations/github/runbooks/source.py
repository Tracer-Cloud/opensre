"""GitHub MCP implementation of the runbook source contract."""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse

from config.constants.runbooks import RUNBOOK_CONTENT_MAX_CHARS
from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import (
    RunbookCatalog,
    RunbookDocument,
    RunbookReference,
)
from integrations.github.helpers import github_creds, github_source_available
from integrations.github.runbooks.manifest import ManifestError, parse_manifest
from integrations.github.tools.commits import list_github_commits
from integrations.github.tools.file_contents import get_github_file_contents

logger = logging.getLogger(__name__)

_RESOURCE_SHA_RE = re.compile(r"/sha/(?P<sha>[^/]+)/contents/")
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class RunbookRetrievalError(RuntimeError):
    """Raised when a trusted GitHub runbook cannot be safely retrieved."""


def _safe_markdown_path(value: str) -> str | None:
    candidate = unquote(value).strip().strip("/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
        or path.suffix.lower() != ".md"
    ):
        return None
    return candidate


def _blob_url(repository: str, revision: str, path: str) -> str:
    encoded_repository = quote(repository, safe="/")
    encoded_revision = quote(revision, safe="")
    encoded_path = quote(path, safe="/")
    return f"https://github.com/{encoded_repository}/blob/{encoded_revision}/{encoded_path}"


def _resource_file(payload: dict[str, Any]) -> tuple[str, str]:
    file_data = payload.get("file")
    if isinstance(file_data, dict):
        content = file_data.get("content")
        uri = file_data.get("uri")
        if isinstance(content, str):
            return content, str(uri or "")
    for item in payload.get("content", []):
        if isinstance(item, dict) and item.get("type") == "resource_text":
            return str(item.get("text") or ""), str(item.get("uri") or "")
    return "", ""


def _revision_from_payload(payload: dict[str, Any], uri: str, requested: str) -> str:
    match = _RESOURCE_SHA_RE.search(uri)
    if match:
        return match.group("sha")
    file_data = payload.get("file")
    if isinstance(file_data, dict) and file_data.get("commit_sha"):
        return str(file_data["commit_sha"])
    if _FULL_SHA_RE.fullmatch(requested):
        return requested
    raise RunbookRetrievalError("GitHub did not return an immutable revision for the runbook.")


class GitHubRunbookSource:
    """Retrieve one configured GitHub runbook source through the existing MCP integration."""

    provider = "github"

    def __init__(self, source: RunbookSourceConfig, github: dict[str, Any]) -> None:
        self._source = source
        self._github = github
        self._owner, self._repo = source.repository.split("/", 1)

    def _resolve_revision(self, revision: str, *, verify_access: bool = False) -> str:
        if _FULL_SHA_RE.fullmatch(revision) and not verify_access:
            return revision.lower()
        result = list_github_commits(
            owner=self._owner,
            repo=self._repo,
            sha=revision,
            per_page=1,
            **github_creds(self._github),
        )
        commits = result.get("commits")
        if not result.get("available") or not isinstance(commits, list) or not commits:
            logger.warning(
                "GitHub runbook revision resolution failed for %s: %s",
                self._source.name,
                result.get("error", "no commits returned"),
            )
            raise RunbookRetrievalError("GitHub could not resolve the configured revision.")
        first = commits[0]
        resolved = first.get("sha") if isinstance(first, dict) else None
        if not isinstance(resolved, str) or not _FULL_SHA_RE.fullmatch(resolved):
            raise RunbookRetrievalError("GitHub did not return an immutable commit revision.")
        if _FULL_SHA_RE.fullmatch(revision) and resolved.lower() != revision.lower():
            raise RunbookRetrievalError("GitHub returned an unexpected commit revision.")
        return resolved.lower()

    def _fetch_file(self, path: str, revision: str) -> tuple[str, str, str]:
        kwargs = github_creds(self._github)
        resolved_revision = self._resolve_revision(revision)
        payload = get_github_file_contents(
            owner=self._owner,
            repo=self._repo,
            path=path,
            ref="",
            sha=resolved_revision,
            **kwargs,
        )
        if not payload.get("available"):
            logger.warning(
                "GitHub runbook retrieval failed for %s/%s: %s",
                self._source.name,
                path,
                payload.get("error", "unknown error"),
            )
            raise RunbookRetrievalError("GitHub could not retrieve the configured runbook.")
        content, uri = _resource_file(payload)
        if not content:
            raise RunbookRetrievalError("GitHub returned an empty or non-text runbook document.")
        returned_revision = _revision_from_payload(payload, uri, resolved_revision)
        if returned_revision.lower() != resolved_revision:
            raise RunbookRetrievalError("GitHub returned content from an unexpected revision.")
        return content, uri, resolved_revision

    def verify(self) -> tuple[bool, str]:
        """Verify repository access and the manifest when one is configured."""
        if self._source.manifest:
            try:
                catalog = self.fetch_catalog()
            except RunbookRetrievalError as exc:
                return False, str(exc)
            return True, (
                f"Loaded {len(catalog.entries)} runbook(s) from "
                f"{self._source.repository}@{catalog.resolved_revision}."
            )

        try:
            revision = self._resolve_revision(self._source.ref, verify_access=True)
        except RunbookRetrievalError:
            return False, "GitHub could not verify access to the configured repository."
        return True, f"Verified access to {self._source.repository}@{revision}."

    def resolve_reference(self, url: str) -> RunbookReference | None:
        """Resolve a GitHub blob URL only for this configured repository and ref."""
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            return None
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] != "blob":
            return None
        if f"{parts[0]}/{parts[1]}".lower() != self._source.repository.lower():
            return None

        configured_ref_parts = self._source.ref.split("/")
        candidate_parts = parts[3:]
        if candidate_parts[: len(configured_ref_parts)] == configured_ref_parts:
            revision = self._source.ref
            path_parts = candidate_parts[len(configured_ref_parts) :]
        elif candidate_parts and _FULL_SHA_RE.fullmatch(candidate_parts[0]):
            revision = candidate_parts[0]
            path_parts = candidate_parts[1:]
        else:
            return None
        path = _safe_markdown_path("/".join(path_parts))
        if path is None:
            return None
        return RunbookReference(
            source_name=self._source.name,
            document_id=PurePosixPath(path).stem,
            path=path,
            requested_revision=revision,
            canonical_url=url.strip(),
        )

    def fetch_catalog(self) -> RunbookCatalog:
        """Retrieve and validate the configured manifest."""
        if not self._source.manifest:
            raise RunbookRetrievalError("Runbook source has no manifest configured.")
        content, _resource_uri, revision = self._fetch_file(
            self._source.manifest,
            self._source.ref,
        )
        try:
            entries = parse_manifest(content)
        except ManifestError as exc:
            raise RunbookRetrievalError("The configured runbook manifest is invalid.") from exc
        source_uri = _blob_url(self._source.repository, revision, self._source.manifest)
        return RunbookCatalog(
            source_name=self._source.name,
            entries=entries,
            resolved_revision=revision,
            source_uri=source_uri,
        )

    def fetch_document(self, reference: RunbookReference) -> RunbookDocument:
        """Retrieve one trusted Markdown document with immutable provenance."""
        if reference.source_name != self._source.name:
            raise RunbookRetrievalError("Runbook reference belongs to another source.")
        path = _safe_markdown_path(reference.path)
        if path is None:
            raise RunbookRetrievalError("Runbook reference contains an unsafe document path.")
        revision = reference.requested_revision or self._source.ref
        content, _resource_uri, resolved_revision = self._fetch_file(path, revision)
        truncated = len(content) > RUNBOOK_CONTENT_MAX_CHARS
        bounded = content[:RUNBOOK_CONTENT_MAX_CHARS]
        source_uri = _blob_url(self._source.repository, resolved_revision, path)
        return RunbookDocument(
            reference=reference,
            content=bounded,
            resolved_revision=resolved_revision,
            source_uri=source_uri,
            title=reference.document_id,
            truncated=truncated,
        )


def build_github_runbook_source(
    source: RunbookSourceConfig,
    resolved_integrations: dict[str, Any],
) -> GitHubRunbookSource | None:
    """Build the GitHub source when verified GitHub credentials are available."""
    if source.provider != "github" or not github_source_available(resolved_integrations):
        return None
    github = resolved_integrations.get("github")
    if not isinstance(github, dict):
        return None
    return GitHubRunbookSource(source, github)


__all__ = [
    "GitHubRunbookSource",
    "RunbookRetrievalError",
    "build_github_runbook_source",
]
