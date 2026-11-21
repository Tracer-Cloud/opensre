"""Infer GitHub repository scope (owner/repo) for repo-scoped tool calls."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from config.constants.runtime_metadata import WORKSPACE_REPO_ENV_KEYS
from core.domain.repositories import (
    RepositoryPreferences,
    load_repository_preferences,
    preferred_repository_for_name,
)
from integrations.github.accessible_repos import (
    accessible_repositories,
    confirmed_repository,
    could_name_repository,
    repository_named_in,
)
from integrations.github.identity import workspace_public_repository_source
from integrations.github.rest_token import resolved_github_rest_token

# Names as GitHub allows them: an owner is letters, digits, ``-`` and ``_``; a
# repository also takes ``.``. A trailing ``.`` is read as a full stop, and a
# quote or bracket after a name is not part of it.
_OWNER = r"(?P<owner>[A-Za-z0-9][A-Za-z0-9_-]*)"
_REPO = r"(?P<repo>[A-Za-z0-9_.-]*[A-Za-z0-9_-])"

_GITHUB_HOST_RE = re.compile(
    rf"(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:){_OWNER}/{_REPO}",
    re.IGNORECASE,
)
_REPO_QUALIFIER_RE = re.compile(rf"\brepo:{_OWNER}/{_REPO}", re.IGNORECASE)
# A bare ``owner/repo`` stands alone: never part of a longer path (``a/b/c``,
# ``src/.env``), though a full stop may follow it.
_BARE_REPO_RE = re.compile(
    rf"(?<![/\w.-]){_OWNER}/(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?)(?!\.?[/\w-])",
)
_EXPLICIT_REFERENCE_PATTERNS = (_GITHUB_HOST_RE, _REPO_QUALIFIER_RE)
_ALL_REFERENCE_PATTERNS = (*_EXPLICIT_REFERENCE_PATTERNS, _BARE_REPO_RE)

_PUBLIC_WORKSPACE_SCOPE_MARKER = "public_workspace"
# Template text such as "provide `owner/opensre`" is a question, not a repository:
# reading it back from the history sent the next turn to a repository that is not there.
_PLACEHOLDER_OWNERS = frozenset({"owner", "<owner>", "your-org", "your-username", "username"})
_PLACEHOLDER_REPOS = frozenset({"repo", "<repo>", "repo-name", "your-repo"})


def _is_placeholder(owner: str, repo: str) -> bool:
    return owner.lower() in _PLACEHOLDER_OWNERS or repo.lower() in _PLACEHOLDER_REPOS


def split_repo_full_name(value: str) -> tuple[str, str]:
    """Split ``owner/repo`` (optionally with trailing ``.git``) into its parts."""
    cleaned = value.strip().strip("/")
    if cleaned.count("/") < 1:
        return "", ""
    owner, repo = cleaned.split("/", 1)
    return owner.strip(), repo.strip().removesuffix(".git")


def _reference_matches(text: str, *, include_bare: bool) -> list[tuple[tuple[str, str], bool]]:
    """Each owner/repo written in ``text``, in order, with whether it was written explicitly.

    Explicit is a github.com URL, git remote or ``repo:`` qualifier; the rest
    are bare ``owner/repo``. A repository written both ways is explicit.
    """
    if not text.strip():
        return []
    found: list[tuple[int, tuple[str, str], bool]] = []
    patterns = _ALL_REFERENCE_PATTERNS if include_bare else _EXPLICIT_REFERENCE_PATTERNS
    for pattern in patterns:
        explicit = pattern is not _BARE_REPO_RE
        for match in pattern.finditer(text):
            owner = match.group("owner").strip()
            repo = match.group("repo").strip().removesuffix(".git")
            if owner and repo and not _is_placeholder(owner, repo):
                found.append((match.start(), (owner, repo), explicit))
    explicit_scopes = {scope for _position, scope, explicit in found if explicit}
    return [(scope, scope in explicit_scopes) for _position, scope, _explicit in sorted(found)]


def _by_recency(scopes: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """``scopes`` without repeats, each kept at its last position."""
    references: list[tuple[str, str]] = []
    for scope in scopes:
        if scope in references:
            references.remove(scope)
        references.append(scope)
    return tuple(references)


def find_github_repository_references(
    text: str, *, include_bare: bool = True
) -> tuple[tuple[str, str], ...]:
    """Return every distinct owner/repo in textual recency order.

    A github.com URL, git remote or ``repo:`` qualifier always counts; a bare
    ``owner/repo`` only with ``include_bare``. Leave it off for text that carries
    tool output, where file paths and words like ``AI/Agent`` look the same.
    """
    return _by_recency(
        [scope for scope, _explicit in _reference_matches(text, include_bare=include_bare)]
    )


def _named_repositories(
    text: str, *, include_bare: bool, token: str
) -> tuple[tuple[str, str], ...]:
    """Every repository ``text`` names, in textual recency order.

    An explicit reference names one as written. A bare ``owner/repo`` names one
    only when it is a real repository (:func:`confirmed_repository`): "why is
    my CI/CD failing" is not about a repository called ``CI/CD``.
    """
    named: list[tuple[str, str]] = []
    for scope, explicit in _reference_matches(text, include_bare=include_bare):
        if explicit:
            named.append(scope)
            continue
        confirmed = confirmed_repository(f"{scope[0]}/{scope[1]}", token)
        if confirmed:
            named.append(split_repo_full_name(confirmed))
    return _by_recency(named)


def parse_github_repository_reference(
    text: str, *, include_bare: bool = True
) -> tuple[str, str] | None:
    """Return the last owner/repo pair found in *text*, or ``None``."""
    references = find_github_repository_references(text, include_bare=include_bare)
    return references[-1] if references else None


def _repository_in_user_text(
    text: str, *, token: str, preferences: RepositoryPreferences
) -> tuple[str, str] | None:
    """The repository one message from the user names, by full name or by short name.

    "fix the opensre repo" names the organization's own repository, never one
    of its forks: a short name resolves to the matching primary. With no
    preference saved it is the one reachable original of that name, which the
    connection knows, so nobody is asked.
    """
    named = _named_repositories(text, include_bare=True, token=token)
    if named:
        return named[-1]
    preferred = preferred_repository_for_name(text, preferences)
    if preferred:
        return split_repo_full_name(preferred)
    if could_name_repository(text):
        reachable = repository_named_in(text, accessible_repositories(token))
        if reachable:
            return split_repo_full_name(reachable)
    return None


def _scope_from_history(
    conversation_messages: Sequence[tuple[str, str]] | None,
    *,
    token: str = "",
    preferences: RepositoryPreferences | None = None,
) -> tuple[str, str] | None:
    """The repository named by the newest message that names one.

    An assistant message carries the output of the tools it ran, so only an
    explicit reference counts there. With ``preferences``, a message from the
    user counts as it did when it was the turn's own message, short names
    included: "now the opensre repo" two messages ago still decides what "its
    open issues" means.
    """
    for role, content in reversed(conversation_messages or ()):
        if role == "user" and preferences is not None:
            scope = _repository_in_user_text(content, token=token, preferences=preferences)
        else:
            named = _named_repositories(content, include_bare=role == "user", token=token)
            scope = named[-1] if named else None
        if scope:
            return scope
    return None


def _parse_git_remote_url(url: str) -> tuple[str, str] | None:
    source = workspace_public_repository_source({"workspace_repo": url})
    github = source.get("github", {})
    owner = str(github.get("owner") or "")
    repo = str(github.get("repo") or "")
    return (owner, repo) if owner and repo else None


def detect_git_remote_repo_scope(cwd: str | Path | None = None) -> tuple[str, str] | None:
    """Best-effort ``owner/repo`` from the checkout's upstream or origin remote.

    In a fork checkout ``origin`` is the fork and ``upstream`` the repository
    it came from, so ``upstream`` wins: work targets the original.
    """
    work_dir = Path(cwd or os.getcwd())
    for remote in ("upstream", "origin"):
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0:
            scope = _parse_git_remote_url(result.stdout)
            if scope is not None:
                return scope
    return None


def infer_github_repo_scope(
    *,
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    cached: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    """Resolve GitHub owner/repo using message, history, session cache, env, and git."""
    from_message = parse_github_repository_reference(message)
    if from_message:
        return from_message

    from_history = _scope_from_history(conversation_messages)
    if from_history:
        return from_history

    if cached:
        return cached

    env_map = env if env is not None else os.environ
    env_repo = str(env_map.get("GITHUB_REPOSITORY", "")).strip()
    if env_repo:
        owner, repo = split_repo_full_name(env_repo)
        if owner and repo:
            return owner, repo

    return detect_git_remote_repo_scope(cwd)


def _infer_workspace_repo_scope(
    *,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
) -> tuple[str, str] | None:
    """Resolve only the process workspace repository, not prompt-provided scope."""
    env_map = env if env is not None else os.environ
    for key in WORKSPACE_REPO_ENV_KEYS:
        source = workspace_public_repository_source({"workspace_repo": str(env_map.get(key, ""))})
        github = source.get("github", {})
        owner = str(github.get("owner") or "")
        repo = str(github.get("repo") or "")
        if owner and repo:
            return owner, repo
    return detect_git_remote_repo_scope(cwd)


def apply_github_repo_scope(
    resolved: dict[str, Any],
    owner: str,
    repo: str,
) -> dict[str, Any]:
    """Return a copy of *resolved* with ``github.owner`` and ``github.repo`` set."""
    gh = resolved.get("github")
    if not gh:
        return dict(resolved)

    if isinstance(gh, BaseModel):
        gh_dict = gh.model_dump(exclude_none=True)
    elif isinstance(gh, dict):
        gh_dict = dict(gh)
    else:
        return dict(resolved)

    merged = dict(resolved)
    gh_dict["owner"] = owner
    gh_dict["repo"] = repo
    merged["github"] = gh_dict
    return merged


class _GithubVcsRepoScopeProvider:
    """Adapts GitHub's owner/repo scope to :class:`infrastructure.harness_providers.VcsRepoScopeProvider`."""

    vendor = "github"

    def infer(
        self,
        *,
        message: str,
        conversation_messages: Sequence[tuple[str, str]] | None,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        cached: tuple[str, ...] | None,
        resolved: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...] | None:
        token = resolved_github_rest_token(resolved or {})
        preferences = load_repository_preferences()
        in_message = _repository_in_user_text(message, token=token, preferences=preferences)
        if in_message:
            return in_message
        from_history = _scope_from_history(
            conversation_messages, token=token, preferences=preferences
        )
        if from_history:
            return from_history
        if cached:
            return cached
        workspace_scope = _infer_workspace_repo_scope(env=env, cwd=cwd)
        if workspace_scope is None and preferences.primary:
            workspace_scope = split_repo_full_name(preferences.primary[0])
        if workspace_scope:
            return (*workspace_scope, _PUBLIC_WORKSPACE_SCOPE_MARKER)
        # A connection that reaches one repository of its own leaves nothing to
        # ask: "why is CI failing" is about that one. A text that could not name
        # a repository (a bare ``/command``) never waits on the listing for it.
        if not could_name_repository(message):
            return None
        originals = [found.full_name for found in accessible_repositories(token) if not found.fork]
        return split_repo_full_name(originals[0]) if len(originals) == 1 else None

    def apply(self, resolved: dict[str, Any], scope: tuple[str, ...]) -> dict[str, Any]:
        owner, repo = scope[0], scope[1]
        if (
            len(scope) >= 3
            and scope[2] == _PUBLIC_WORKSPACE_SCOPE_MARKER
            and "github" not in resolved
        ):
            return {
                **resolved,
                **workspace_public_repository_source({"workspace_repo": f"{owner}/{repo}"}),
            }
        return apply_github_repo_scope(resolved, owner, repo)

    def repository_key(self, scope: tuple[str, ...]) -> str:
        """Return ``owner/repo`` without internal workspace-scope markers."""
        return f"{scope[0]}/{scope[1]}"

    def referenced_scopes(
        self,
        *,
        text: str,
        role: str,
        env: Mapping[str, str] | None,
        resolved: Mapping[str, Any] | None = None,
    ) -> tuple[tuple[str, ...], ...]:
        """Return all GitHub repositories named in ``text``; a bare one only from the user."""
        _ = env
        return _named_repositories(
            text,
            include_bare=role == "user",
            token=resolved_github_rest_token(resolved or {}),
        )

    def prewarm(self, resolved: Mapping[str, Any]) -> None:
        """List the repositories the connection reaches, which short names resolve against."""
        accessible_repositories(resolved_github_rest_token(resolved))


GITHUB_VCS_REPO_SCOPE_PROVIDER = _GithubVcsRepoScopeProvider()


__all__ = [
    "GITHUB_VCS_REPO_SCOPE_PROVIDER",
    "apply_github_repo_scope",
    "detect_git_remote_repo_scope",
    "find_github_repository_references",
    "infer_github_repo_scope",
    "parse_github_repository_reference",
    "split_repo_full_name",
]
