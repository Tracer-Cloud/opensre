"""Surface-agnostic session state and persistence — the session package facade.

- :class:`SessionCore` (``session_core``) — the surface-agnostic session domain object.
  The interactive shell's ``Session`` subclass with UI facets lives in
  ``surfaces/interactive_shell/session/``.
- :class:`SessionManager` (``lifecycle``) — create / resolve / rotate / restore / flush.
- :class:`SessionStore` / :class:`SessionRepo` protocols + backends (``persistence``).

``SessionCore`` delegates all persistence to an injected ``SessionStore`` so the on-disk
format is swappable and tests can run without touching the filesystem. The module-level
``DEFAULT_SESSION_STORE`` / ``DEFAULT_SESSION_REPO`` singletons provide the production
JSONL backends used by agent surfaces.
"""

from __future__ import annotations

from core.agent_harness.session.persistence import (
    InMemorySessionStore,
    JsonlSessionStore,
)
from core.agent_harness.session.persistence.contracts import (
    CHAT_KINDS,
    SessionPersistenceSource,
    SessionRepo,
    SessionStore,
)
from core.agent_harness.session.persistence.jsonl_repo import JsonlSessionRepo
from core.agent_harness.session.session_core import SessionCore

# Production singletons. Both backends are stateless, so sharing one instance
# across the process is safe and avoids re-instantiation on every session.
DEFAULT_SESSION_STORE: SessionStore = JsonlSessionStore()
DEFAULT_SESSION_REPO: SessionRepo = JsonlSessionRepo()


def default_session_store() -> SessionStore:
    """Return the shared production JSONL storage backend."""
    return DEFAULT_SESSION_STORE


def default_session_repo() -> SessionRepo:
    """Return the shared production JSONL cross-session repository."""
    return DEFAULT_SESSION_REPO


# Imported last: SessionManager reads the DEFAULT_* singletons above (lazily, in
# its constructor), so this import must follow their definition.
from core.agent_harness.session.lifecycle import SessionManager  # noqa: E402

__all__ = [
    "CHAT_KINDS",
    "DEFAULT_SESSION_REPO",
    "DEFAULT_SESSION_STORE",
    "InMemorySessionStore",
    "JsonlSessionRepo",
    "JsonlSessionStore",
    "SessionCore",
    "SessionManager",
    "SessionPersistenceSource",
    "SessionRepo",
    "SessionStore",
    "default_session_repo",
    "default_session_store",
]
