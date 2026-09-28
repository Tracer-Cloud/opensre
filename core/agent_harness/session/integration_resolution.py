"""Per-session integration state, cache helpers, and turn-time resolution.

Owns everything session-scoped for integration discovery: configured service
names, the resolved-config cache, repository scopes, background warm tasks, and
``resolve_and_cache_integrations`` for the turn engine.

``SessionCore`` composes :class:`IntegrationState` as ``session.integrations`` and
re-exposes public fields via properties for API stability. Port-level fetch/classify
logic lives in :mod:`infrastructure.harness_providers` (wired at startup from
``integrations/harness_adapters``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.constants.paths import integrations_store_stamp
from infrastructure.harness_providers import (
    IntegrationResolutionResult,
    resolve_integrations,
)

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionState

__all__ = [
    "IntegrationResolutionResult",
    "IntegrationState",
    "has_only_underscore_prefixed_keys",
    "has_resolved_integrations",
    "merge_resolved_integrations",
    "resolve_and_cache_integrations",
    "resolve_integrations",
]


# ---------------------------------------------------------------------------
# Cache helpers (shared by IntegrationState and resolve_and_cache_integrations)
# ---------------------------------------------------------------------------


def has_resolved_integrations(cache: dict[str, Any] | None) -> bool:
    """Return True when the cache holds at least one integration config."""
    if not cache:
        return False
    return any(not str(key).startswith("_") for key in cache)


def has_only_underscore_prefixed_keys(cache: dict[str, Any] | None) -> bool:
    """True when every cache key starts with ``_`` (book-keeping only, no configs)."""
    if not cache:
        return False
    return all(str(key).startswith("_") for key in cache)


def merge_resolved_integrations(
    base: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge integration configs while preserving gateway/runtime metadata keys."""
    merged = dict(base or {})
    merged.update(updates)
    return merged


def _has_usable_cache(cache: dict[str, Any] | None) -> bool:
    """True when a cache holds a resolved view and need not be re-resolved.

    ``None`` means unresolved (resolve live). An explicit ``{}`` is a known
    empty view — oracle fixtures and callers that force a no-integration world
    rely on that distinction, so it must not trigger a live re-resolve.
    Metadata-only maps (underscore keys such as ``_gateway_chat_id``) are not
    a hit and still trigger resolve.
    """
    if cache is None:
        return False
    if not cache:
        # Explicit empty dict: known "no integrations", not a cache miss.
        return True
    return has_resolved_integrations(cache) or not has_only_underscore_prefixed_keys(cache)


def resolve_and_cache_integrations(session: SessionState) -> dict[str, Any]:
    """Resolve a session's integration configs, using and updating its cache.

    A cache built from an earlier version of the integrations store is dropped,
    so a credential that reached this process after the session started is
    picked up on the session's next turn. The store stamp lives on the session's
    :class:`IntegrationState`; a session without one keeps the plain cache rule.
    """
    stamp = integrations_store_stamp()
    state = getattr(session, "integrations", None)
    cached = session.resolved_integrations_cache
    if cached and _built_from_another_store(state, stamp):
        cached = None
        session.resolved_integrations_cache = None
    if _has_usable_cache(cached):
        return dict(cached or {})

    resolved = resolve_integrations()
    if resolved:
        session.resolved_integrations_cache = merge_resolved_integrations(cached, resolved)
        if isinstance(state, IntegrationState):
            state.store_stamp = stamp
    return dict(session.resolved_integrations_cache or {})


def _built_from_another_store(state: Any, stamp: int) -> bool:
    if not isinstance(state, IntegrationState) or state.store_stamp is None:
        return False
    return state.store_stamp != stamp


# ---------------------------------------------------------------------------
# Session integration facet
# ---------------------------------------------------------------------------


@dataclass
class IntegrationState:
    """A session's integration-resolution state and the logic that warms it."""

    configured: tuple[str, ...] = ()
    """Session-scoped configured integration names for planning-time capability checks."""
    configured_known: bool = False
    """Whether ``configured`` reflects known state (vs default unknown)."""
    resolved_cache: dict[str, Any] | None = None
    """Resolved integration configs (env/store) shared across turns.

    Populated silently at REPL boot and again after integration mutations so the
    conversational assistant can call registered tools without
    waiting for the first user message to trigger a visible "Loading integrations"
    pass. Cleared by :meth:`refresh` when integrations change."""
    store_stamp: int | None = None
    """Stamp of the integrations store the resolved cache was built from."""
    vcs_repo_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Active per-vendor repo scopes used for unqualified VCS tool calls."""
    active_vcs_repositories: dict[str, str] = field(default_factory=dict)
    """Stable repository identity for each active per-vendor scope."""
    known_vcs_repo_scopes: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    """All repository scopes encountered this session, keyed by vendor and repo.

    Switching the active scope updates :attr:`vcs_repo_scopes` without
    discarding scopes for repositories used earlier in the session.
    """

    _warm_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _warm_generation: int = field(default=0, repr=False, compare=False)
    _warm_task: Any = field(default=None, repr=False, compare=False)

    def hydrate(self) -> None:
        """Load configured integration names (env + local store) — metadata only.

        Run at REPL boot and again whenever an integration is added or removed so
        capability checks and the tool-gathering pass reflect the current store
        state instead of a stale boot-time snapshot. Must not resolve keyring-backed
        secrets; full configs are resolved on demand via :meth:`warm`/:meth:`get`.
        """
        try:
            from infrastructure.harness_providers import configured_integration_services

            self.configured = tuple(sorted(configured_integration_services()))
            self.configured_known = True
        except Exception:
            # Best-effort: keep whatever state we already had (default unknown).
            pass

    def warm(self, *, generation: int | None = None) -> None:
        """Resolve full integration configs once, without progress UI.

        Empty resolves are not cached so a later turn can retry if boot-time
        resolution raced store/env hydration. Failures leave the cache unset.
        """
        cached = self.resolved_cache
        if cached is not None and not has_only_underscore_prefixed_keys(cached):
            return
        if generation is None:
            with self._warm_lock:
                generation = self._warm_generation
        # The stamp is read before resolving so it names the store these
        # credentials came from; a store rewritten mid-resolution keeps the
        # older stamp and is re-resolved on the next turn.
        stamp = integrations_store_stamp()
        try:
            resolved = resolve_integrations()
        except Exception:
            # Best-effort warmup: leave cache unset so later turns can retry.
            return
        self._store(resolved, generation=generation, stamp=stamp)

    def _store(self, resolved: dict[str, Any], *, generation: int, stamp: int) -> None:
        if not resolved:
            return
        with self._warm_lock:
            if generation != self._warm_generation:
                return
            if self.resolved_cache is not None and not has_only_underscore_prefixed_keys(
                self.resolved_cache
            ):
                return
            self.resolved_cache = merge_resolved_integrations(self.resolved_cache, resolved)
            self.store_stamp = stamp

    def get(self) -> IntegrationResolutionResult:
        """Return the session's integration configs as a typed snapshot (cache-aware).

        An explicit empty cache is treated as known state; metadata-only caches
        trigger one quiet warmup, merged through the same generation guard as startup.
        """
        cached = self.resolved_cache
        if _has_usable_cache(cached):
            return IntegrationResolutionResult(resolved_integrations=dict(cached or {}))
        self.warm()
        return IntegrationResolutionResult(resolved_integrations=dict(self.resolved_cache or {}))

    def refresh(self) -> None:
        """Re-resolve after the local store changes: drop cache, re-hydrate, re-warm."""
        self._cancel_warm(drop_cache=True)
        self.hydrate()
        self.warm()

    def reset(self) -> None:
        """Reset all resolution state for /new (cancels any in-flight warm task)."""
        self._cancel_warm(drop_cache=True)
        self.configured = ()
        self.configured_known = False

    def release(self) -> None:
        """Cancel the in-flight warm task for teardown (keeps cached data)."""
        self._cancel_warm(drop_cache=False)

    def _cancel_warm(self, *, drop_cache: bool) -> None:
        with self._warm_lock:
            self._warm_generation += 1
            pending = self._warm_task
            self._warm_task = None
            if drop_cache:
                self.resolved_cache = None
                self.vcs_repo_scopes = {}
                self.active_vcs_repositories = {}
                self.known_vcs_repo_scopes = {}
        if pending is not None and not pending.done():
            pending.cancel()
