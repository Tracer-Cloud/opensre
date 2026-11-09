"""Resolve or create persisted Session instances for gateway chats.

Session lifecycle (create / resolve / rotate / restore / flush) is owned by
:class:`core.agent_harness.session.SessionManager`. This resolver adds only the
gateway-specific concerns on top: the platform chat-id ↔ session-id binding
store (principal- and actor-scoped) and per-turn gateway chat metadata.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.spi.integrations import has_resolved_integrations
from gateway.core.session.gateway_chat_context import inject_gateway_chat_context

if TYPE_CHECKING:
    from config.principal import Actor, Principal
    from gateway.core.storage.session.binding_store import BindingStore

logger = logging.getLogger(__name__)

_PLATFORM_TELEGRAM = "telegram"


def _ensure_integrations(session: SessionCore) -> SessionCore:
    """Re-hydrate configured names and warm configs when the cache is empty.

    Gateway turns must see Slack (and other store/env integrations) even when a
    prior session left a metadata-only or empty resolved cache.
    """
    session.hydrate_configured_integrations()
    if not has_resolved_integrations(session.resolved_integrations_cache):
        session.warm_resolved_integrations()
    return session


def _inject_chat_context(session: SessionCore, *, chat_id: str, platform: str = "") -> SessionCore:
    """Attach per-turn gateway chat metadata.

    Capability policy is not applied here: it belongs to the host layer, which
    states it once when it builds the session's agent.
    """
    _ensure_integrations(session)
    session.resolved_integrations_cache = inject_gateway_chat_context(
        dict(session.resolved_integrations_cache or {}),
        chat_id,
        platform,
    )
    return session


class SessionResolver:
    """Bind platform conversations to sessions, delegating lifecycle to SessionManager."""

    def __init__(
        self,
        bindings: BindingStore,
        *,
        manager: SessionManager | None = None,
        platform: str = _PLATFORM_TELEGRAM,
    ) -> None:
        self._bindings = bindings
        self._manager = manager or SessionManager()
        self._platform = platform

    def _lookup_or_adopt_session_id(
        self,
        *,
        user_id: str,
        principal: Principal | None,
        actor: Actor | str | None,
    ) -> str | None:
        """Scoped binding, or adopt a same-document legacy empty-id row once.

        Adoption is single-use (legacy row removed) so a second actor in the
        same conversation cannot inherit the same persisted session.
        """
        existing = self._bindings.get_session_id(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
            actor=actor,
        )
        if existing:
            return existing
        if principal is None or actor is None:
            return None
        legacy = self._bindings.adopt_unscoped_binding(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
            actor=actor,
        )
        if legacy:
            logger.info(
                "[gateway] adopted legacy %s binding conversation=%s → principal=%s actor=%s",
                self._platform,
                user_id,
                principal.id,
                actor.id if hasattr(actor, "id") else actor,
            )
        return legacy

    def resolve(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: Principal | None,
        actor: Actor | str | None,
    ) -> SessionCore:
        """Return a hydrated session for the platform conversation key ``user_id``.

        Chat transports pass an org ``principal`` and platform ``actor``. Both are
        required arguments on purpose: they defaulted to ``None`` and a transport
        that forgot them shipped with scoped binding silently degraded to legacy
        empty-key rows. A caller that genuinely means the legacy key passes
        ``None`` explicitly.
        """
        existing = self._lookup_or_adopt_session_id(
            user_id=user_id,
            principal=principal,
            actor=actor,
        )
        if existing:
            session = self._manager.resolve(existing)
            return _inject_chat_context(session, chat_id=chat_id, platform=self._platform)

        session = self._manager.create(warm_integrations=True)
        _inject_chat_context(session, chat_id=chat_id, platform=self._platform)
        self._bindings.bind(
            platform=self._platform,
            chat_id=user_id,
            session_id=session.session_id,
            principal=principal,
            actor=actor,
        )
        logger.info(
            "[gateway] created session %s for %s conversation %s principal=%s actor=%s",
            session.session_id,
            self._platform,
            user_id,
            principal.id if principal is not None else "",
            actor.id if actor is not None and hasattr(actor, "id") else (actor or ""),
        )
        return session

    def has_conversation(
        self, *, conversation_key: str, principal: Principal | None = None
    ) -> bool:
        """Whether any member already has a session in this conversation."""
        return self._bindings.has_any_actor_binding(
            platform=self._platform,
            chat_id=conversation_key,
            principal=principal,
        )

    def rotate(
        self,
        *,
        user_id: str,
        chat_id: str,
        principal: Principal | None,
        actor: Actor | str | None,
    ) -> SessionCore:
        """Flush the current session file and start a new binding.

        ``principal``/``actor`` are required for the same reason as
        :meth:`resolve` — explicit ``None`` for legacy keys, never by omission.
        """
        from core.domain.memory import gateway_memory_enabled

        existing = self._lookup_or_adopt_session_id(
            user_id=user_id,
            principal=principal,
            actor=actor,
        )
        new_id = self._bindings.rotate(
            platform=self._platform,
            chat_id=user_id,
            principal=principal,
            actor=actor,
        )
        # Memory now follows the bound scope, so extraction is gated by the
        # same opt-in the shared gateway host used.
        session = self._manager.rotate(
            old_session_id=existing or None,
            new_session_id=new_id,
            extract_memory=gateway_memory_enabled(),
        )
        return _inject_chat_context(session, chat_id=chat_id, platform=self._platform)
