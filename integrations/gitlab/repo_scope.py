"""Infer GitLab project and file scope for repository-scoped tool calls."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from pydantic import BaseModel

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_SSH_REMOTE_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<path>.+)$", re.IGNORECASE)
_PROJECT_QUALIFIER_RE = re.compile(
    r"\bgitlab:(?P<project>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)",
    re.IGNORECASE,
)


class GitlabRepoScope(NamedTuple):
    """Resolved GitLab scope: ``project_id`` plus optional ``ref`` and ``file_path``."""

    project_id: str
    ref: str = ""
    file_path: str = ""


def _clean_project_path(value: str) -> str:
    return value.strip().strip("/").removesuffix(".git")


def _is_gitlab_host(host: str, allowed_hosts: frozenset[str]) -> bool:
    lowered = host.lower()
    return "gitlab" in lowered or lowered in allowed_hosts


def _scope_from_url(value: str, allowed_hosts: frozenset[str]) -> GitlabRepoScope | None:
    parsed = urlsplit(value.rstrip(".,);]"))
    if not _is_gitlab_host(parsed.hostname or "", allowed_hosts):
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    blob_marker = "/-/blob/"
    if blob_marker in path:
        project, remainder = path.split(blob_marker, 1)
        ref, separator, file_path = remainder.partition("/")
        project = _clean_project_path(project)
        if project and ref and separator and file_path:
            return GitlabRepoScope(project_id=project, ref=ref, file_path=file_path)
        return None

    project = _clean_project_path(path.split("/-/", 1)[0])
    return GitlabRepoScope(project_id=project) if "/" in project else None


def find_gitlab_repository_references(
    text: str, *, allowed_hosts: frozenset[str] = frozenset()
) -> tuple[GitlabRepoScope, ...]:
    """Return all distinct GitLab project/file scopes in textual recency order.

    A host is accepted when it contains ``gitlab`` or matches *allowed_hosts*
    (typically the configured self-hosted GitLab base URL host).
    """
    if not text.strip():
        return ()

    matches: list[tuple[int, GitlabRepoScope]] = []
    for match in _PROJECT_QUALIFIER_RE.finditer(text):
        project = _clean_project_path(match.group("project"))
        if project:
            matches.append((match.start(), GitlabRepoScope(project_id=project)))
    for match in _URL_RE.finditer(text):
        scope = _scope_from_url(match.group(0), allowed_hosts)
        if scope:
            matches.append((match.start(), scope))
    references: list[GitlabRepoScope] = []
    for _position, scope in sorted(matches):
        if scope in references:
            references.remove(scope)
        references.append(scope)
    return tuple(references)


def parse_gitlab_repository_reference(
    text: str, *, allowed_hosts: frozenset[str] = frozenset()
) -> GitlabRepoScope | None:
    """Return the last GitLab project/file scope found in *text*."""
    references = find_gitlab_repository_references(text, allowed_hosts=allowed_hosts)
    return references[-1] if references else None


def _parse_git_remote_scope(url: str, allowed_hosts: frozenset[str]) -> GitlabRepoScope | None:
    cleaned = url.strip()
    ssh_match = _SSH_REMOTE_RE.match(cleaned)
    if ssh_match:
        if not _is_gitlab_host(ssh_match.group("host"), allowed_hosts):
            return None
        project = _clean_project_path(ssh_match.group("path"))
        return GitlabRepoScope(project_id=project) if "/" in project else None
    return _scope_from_url(cleaned, allowed_hosts)


def detect_git_remote_repo_scope(
    cwd: str | Path | None = None, *, allowed_hosts: frozenset[str] = frozenset()
) -> GitlabRepoScope | None:
    """Best-effort GitLab project scope from ``git remote get-url origin``."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=Path(cwd or os.getcwd()),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_git_remote_scope(result.stdout, allowed_hosts)


def _configured_hosts(env_map: Mapping[str, str]) -> frozenset[str]:
    """Hosts trusted beyond the ``gitlab`` substring check (self-hosted base URLs)."""
    configured_url = str(env_map.get("GITLAB_BASE_URL", "")).strip()
    host = (urlsplit(configured_url).hostname or "").lower()
    return frozenset({host}) if host else frozenset()


def infer_gitlab_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: tuple[str, str, str] | None = None,
    prefer_cwd_over_environment: bool = False,
) -> GitlabRepoScope | None:
    """Resolve GitLab scope from message, history, cache, environment, or git."""
    env_map = env if env is not None else os.environ
    allowed_hosts = _configured_hosts(env_map)

    from_message = parse_gitlab_repository_reference(message, allowed_hosts=allowed_hosts)
    if from_message:
        return from_message

    if conversation_messages:
        for _role, content in reversed(conversation_messages):
            from_history = parse_gitlab_repository_reference(content, allowed_hosts=allowed_hosts)
            if from_history:
                return from_history

    if cached:
        return GitlabRepoScope(*cached)

    if prefer_cwd_over_environment:
        cwd_scope = detect_git_remote_repo_scope(cwd, allowed_hosts=allowed_hosts)
        if cwd_scope is not None:
            return cwd_scope

    project = _clean_project_path(str(env_map.get("GITLAB_PROJECT_ID", "")))
    if project:
        return GitlabRepoScope(
            project_id=project,
            ref=str(env_map.get("GITLAB_REF", "")).strip(),
            file_path=str(env_map.get("GITLAB_FILE_PATH", "")).strip().strip("/"),
        )

    return detect_git_remote_repo_scope(cwd, allowed_hosts=allowed_hosts)


def apply_gitlab_repo_scope(
    resolved: dict[str, Any],
    project_id: str,
    ref_name: str,
    file_path: str,
) -> dict[str, Any]:
    """Return a copy of *resolved* enriched with GitLab repository scope."""
    gitlab = resolved.get("gitlab")
    if not gitlab:
        return dict(resolved)

    if isinstance(gitlab, BaseModel):
        gitlab_dict = gitlab.model_dump(exclude_none=True)
    elif isinstance(gitlab, dict):
        gitlab_dict = dict(gitlab)
    else:
        return dict(resolved)

    gitlab_dict["project_id"] = project_id
    if ref_name:
        gitlab_dict["ref_name"] = ref_name
    if file_path:
        gitlab_dict["file_path"] = file_path

    merged = dict(resolved)
    merged["gitlab"] = gitlab_dict
    return merged


class _GitlabVcsRepoScopeProvider:
    """Adapts GitLab's project/ref/file scope to :class:`infrastructure.harness_providers.VcsRepoScopeProvider`."""

    vendor = "gitlab"

    def infer(
        self,
        *,
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, ...] | None,
        prefer_cwd_over_environment: bool = False,
    ) -> tuple[str, ...] | None:
        cached_scope = GitlabRepoScope(*cached) if cached else None
        scope = infer_gitlab_repo_scope(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached_scope,
            prefer_cwd_over_environment=prefer_cwd_over_environment,
        )
        return tuple(scope) if scope else None

    def apply(self, resolved: dict[str, Any], scope: tuple[str, ...]) -> dict[str, Any]:
        project_id = scope[0]
        ref_name = scope[1] if len(scope) > 1 else ""
        file_path = scope[2] if len(scope) > 2 else ""
        return apply_gitlab_repo_scope(resolved, project_id, ref_name, file_path)

    def repository_key(self, scope: tuple[str, ...]) -> str:
        """Return the project identity without its optional ref or file path."""
        return scope[0]

    def referenced_scopes(
        self,
        *,
        text: str,
        env: Mapping[str, str] | None,
    ) -> tuple[tuple[str, ...], ...]:
        """Return all GitLab repositories explicitly named in ``text``."""
        env_map = env if env is not None else os.environ
        return tuple(
            tuple(scope)
            for scope in find_gitlab_repository_references(
                text,
                allowed_hosts=_configured_hosts(env_map),
            )
        )


GITLAB_VCS_REPO_SCOPE_PROVIDER = _GitlabVcsRepoScopeProvider()


__all__ = [
    "GITLAB_VCS_REPO_SCOPE_PROVIDER",
    "GitlabRepoScope",
    "apply_gitlab_repo_scope",
    "detect_git_remote_repo_scope",
    "find_gitlab_repository_references",
    "infer_gitlab_repo_scope",
    "parse_gitlab_repository_reference",
]
