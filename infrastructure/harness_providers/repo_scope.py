"""VCS repository-scope enrichment.

Repository-scope inference (owner/repo, project/ref/file, …) is vendor behavior.
Core must not name any specific VCS vendor — instead, each vendor's
``integrations`` package registers a ``VcsRepoScopeProvider`` that wraps its own
infer/apply helpers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VcsRepoScopeProvider(Protocol):
    """Adapter that infers and applies one vendor's repository scope.

    ``vendor`` names the cache slot in the session's scope bag (e.g.
    ``"github"``, ``"gitlab"``); ``infer``/``apply`` mirror the vendor's own
    scope helpers but speak in vendor-neutral ``tuple[str, ...]`` scopes.
    """

    vendor: str

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
        """Resolve scope, optionally preferring the cwd over ambient repo identity."""

    def apply(self, resolved: dict[str, Any], scope: tuple[str, ...]) -> dict[str, Any]:
        """Return a copy of *resolved* enriched with this vendor's scope."""
        raise NotImplementedError

    def repository_key(self, scope: tuple[str, ...]) -> str:
        """Return the stable repository identity represented by ``scope``."""

    def referenced_scopes(
        self,
        *,
        text: str,
        env: Mapping[str, str] | None,
    ) -> tuple[tuple[str, ...], ...]:
        """Return every repository scope explicitly referenced in ``text``."""


_vcs_repo_scope_providers: list[VcsRepoScopeProvider] = []


def register_vcs_repo_scope_provider(provider: VcsRepoScopeProvider) -> None:
    _vcs_repo_scope_providers.append(provider)


def clear_vcs_repo_scope_providers() -> None:
    _vcs_repo_scope_providers.clear()


def enrich_resolved_with_repo_scopes(
    *,
    resolved: dict[str, Any],
    message: str,
    conversation_messages: Sequence[tuple[str, str]] | None,
    env: Mapping[str, str] | None,
    cwd: str | Path | None,
    cached_scopes: Mapping[str, tuple[str, ...]],
    prefer_cwd_over_environment: bool = False,
    set_cached_scope: Callable[[str, tuple[str, ...] | None], None] | None = None,
    remember_scope: Callable[[str, str, tuple[str, ...]], None] | None = None,
) -> dict[str, Any]:
    """Apply all registered VCS repo-scope providers to ``resolved``.

    Core callers must use this entrypoint instead of naming individual vendors.
    """
    out = dict(resolved)
    for provider in _vcs_repo_scope_providers:
        if remember_scope is not None:
            reference_texts = [
                *(content for _role, content in conversation_messages or ()),
                message,
            ]
            for text in reference_texts:
                for referenced_scope in provider.referenced_scopes(text=text, env=env):
                    remember_scope(
                        provider.vendor,
                        provider.repository_key(referenced_scope),
                        referenced_scope,
                    )
        scope = provider.infer(
            message=message,
            conversation_messages=conversation_messages,
            env=env,
            cwd=cwd,
            cached=cached_scopes.get(provider.vendor),
            prefer_cwd_over_environment=prefer_cwd_over_environment,
        )
        if not scope:
            continue
        if remember_scope is not None:
            remember_scope(provider.vendor, provider.repository_key(scope), scope)
        if set_cached_scope is not None:
            set_cached_scope(provider.vendor, scope)
        out = provider.apply(out, scope)
    return out


def reset() -> None:
    """Clear registered repo-scope providers (tests)."""
    clear_vcs_repo_scope_providers()


__all__ = [
    "VcsRepoScopeProvider",
    "clear_vcs_repo_scope_providers",
    "enrich_resolved_with_repo_scopes",
    "register_vcs_repo_scope_provider",
]
