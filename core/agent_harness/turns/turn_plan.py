"""Turn-wide assembly: the decisions one turn runs on.

Assembled once at the top of ``run_turn`` and read by the action, gather, and
answer phases so they cannot disagree about what this turn knows. It composes the
frozen :class:`TurnSnapshot` (the read view of session state at turn start) with
the turn's resolved-integration decision.

The snapshot answers "what did the session look like at turn start?"; the plan
answers "what is this turn running on?". ``build_turn_plan`` owns the assembly:
it resolves integrations once and composes them into the snapshot. Tool lists and
prompts stay built by their phases (action tools need surface context; gather
tools depend on message-time GitHub scope), each reading ``resolved_integrations``
here so there is one source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.ports import SessionState
from core.agent_harness.session.integration_resolution import (
    has_resolved_integrations,
    resolve_and_cache_integrations,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from infrastructure.harness_providers import enrich_resolved_with_repo_scopes

_MAX_KNOWN_REPOSITORIES_PER_VENDOR = 20


@dataclass(frozen=True)
class TurnPlan:
    """Everything one turn runs on, assembled once at ``run_turn``."""

    snapshot: TurnSnapshot

    @property
    def text(self) -> str:
        """Raw user input text for this turn."""
        return self.snapshot.text

    @property
    def resolved_integrations(self) -> dict[str, Any]:
        """The turn's single resolved-integration view (frozen on the snapshot)."""
        return self.snapshot.resolved_integrations


def build_turn_plan(snapshot: TurnSnapshot, session: SessionState) -> TurnPlan:
    """Assemble the turn plan: resolve integrations once, then compose the snapshot.

    Resolution runs only when the snapshot has not already been populated (a
    runtime-request source can pre-fill it), so the plan is the single place that
    decides what this turn knows about connected integrations.

    An empty result (``{}`` — no integrations configured) is a valid resolved
    view; downstream phases read it from the plan rather than re-checking, so the
    resolve-once contract holds even in that case (``resolve_and_cache`` also
    caches, so a repeat call would be a no-op regardless).

    Metadata-only maps (underscore keys such as ``_gateway_chat_id``) are not a
    resolved view — they must still trigger a real resolve.
    """
    if not has_resolved_integrations(snapshot.resolved_integrations):
        snapshot = replace(snapshot, resolved_integrations=resolve_and_cache_integrations(session))

    repositories = _resolve_repositories(snapshot, session, snapshot.resolved_integrations)
    session.vcs_repo_scopes = repositories.scopes
    session.active_vcs_repositories = repositories.active
    session.known_vcs_repo_scopes = repositories.known
    snapshot = replace(
        snapshot,
        resolved_integrations=repositories.resolved,
        active_vcs_repositories=dict(repositories.active),
        known_vcs_repositories={
            vendor: tuple(scopes) for vendor, scopes in repositories.known.items()
        },
    )
    return TurnPlan(snapshot=snapshot)


@dataclass(frozen=True)
class _RepositoryResolution:
    """The integrations with repository scopes applied, and the session state they imply."""

    resolved: dict[str, Any]
    scopes: dict[str, tuple[str, ...]]
    active: dict[str, str]
    known: dict[str, dict[str, tuple[str, ...]]]


def _resolve_repositories(
    snapshot: TurnSnapshot, session: SessionState, resolved: dict[str, Any]
) -> _RepositoryResolution:
    """Which repository each vendor targets for ``snapshot``, without changing ``session``."""
    scopes = dict(session.vcs_repo_scopes)
    active = dict(session.active_vcs_repositories)
    known = {name: dict(remembered) for name, remembered in session.known_vcs_repo_scopes.items()}
    repository_keys: dict[tuple[str, tuple[str, ...]], str] = {}

    def _set_active_scope(vendor: str, scope: tuple[str, ...] | None) -> None:
        if scope is None:
            scopes.pop(vendor, None)
        else:
            scopes[vendor] = scope
        repository = repository_keys.get((vendor, scope)) if scope is not None else None
        if repository is None:
            active.pop(vendor, None)
        else:
            active[vendor] = repository

    def _remember_scope(vendor: str, repository: str, scope: tuple[str, ...]) -> None:
        repository_keys[(vendor, scope)] = repository
        remembered = known.setdefault(vendor, {})
        # Re-inserting moves a reused repository to the recent end without
        # creating a duplicate. Bound the collection for long-running gateways.
        remembered.pop(repository, None)
        remembered[repository] = scope
        while len(remembered) > _MAX_KNOWN_REPOSITORIES_PER_VENDOR:
            remembered.pop(next(iter(remembered)))

    enriched = enrich_resolved_with_repo_scopes(
        resolved=resolved,
        message=snapshot.text,
        conversation_messages=snapshot.conversation_messages,
        env=None,
        cwd=snapshot.working_directory,
        cached_scopes=dict(session.vcs_repo_scopes),
        set_cached_scope=_set_active_scope,
        remember_scope=_remember_scope,
    )
    return _RepositoryResolution(resolved=enriched, scopes=scopes, active=active, known=known)


def preview_repositories(snapshot: TurnSnapshot, session: SessionState) -> TurnSnapshot:
    """``snapshot`` with the repositories a turn on it would target, the session left as it was.

    Uses the session's cached integrations only: before the first resolve the
    snapshot keeps the repositories already active, since resolving could reach
    the network.
    """
    cached = session.resolved_integrations_cache
    if not has_resolved_integrations(cached):
        return snapshot
    repositories = _resolve_repositories(snapshot, session, dict(cached or {}))
    return replace(
        snapshot,
        resolved_integrations=repositories.resolved,
        active_vcs_repositories=dict(repositories.active),
        known_vcs_repositories={
            vendor: tuple(scopes) for vendor, scopes in repositories.known.items()
        },
    )


__all__ = ["TurnPlan", "build_turn_plan", "preview_repositories"]
