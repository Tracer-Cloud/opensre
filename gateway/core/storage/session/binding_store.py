"""Session binding store construction.

Bindings live in a JSON file beside the rest of a principal's context — no
database, and no SQLite: the context root is an NFS-backed mount where SQLite's
advisory locking is unreliable, while a write-temp-then-rename is atomic. One
writer at a time, which the Slack gateway already is (Socket Mode is
single-consumer).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from config.principal import Actor, Principal
from gateway.core.storage.session.file_bindings import FileBindingStore
from gateway.core.storage.session.legacy_sqlite import collect_legacy_bindings
from gateway.core.storage.session.paths import bindings_file_path, legacy_binding_dirs

logger = logging.getLogger(__name__)


class BindingStore(Protocol):
    """Contract the session resolver depends on, independent of the backend."""

    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str | None:
        """Return the session id for the binding, if present."""

    def bind(
        self,
        *,
        platform: str,
        chat_id: str,
        session_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> None:
        """Bind the chat to the provided session id."""

    def adopt_unscoped_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal,
        actor: Actor | str,
    ) -> str | None:
        """Move a legacy empty-id row onto ``principal``/``actor`` once.

        Atomic and single-use: the unscoped row is removed so a second actor in
        the same conversation cannot inherit the same session. Idempotent when
        the scoped binding already exists.
        """

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str:
        """Create and return a replacement session id for the chat."""

    def has_any_actor_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> bool:
        """Return whether the chat has any actor-specific binding."""

    def close(self) -> None:
        """Release any resources held by the binding store."""


def legacy_bindings_for(path: Path) -> list[dict[str, Any]]:
    """Rows to carry into ``path`` from the SQLite index that preceded it.

    Called the first time a document is reached rather than when the store is
    built: a gateway opens the store before any scope is bound, so at that point
    the path is the unbound host one, not the organization mount its turns use.
    """
    rows = collect_legacy_bindings(legacy_binding_dirs(path.parent))
    if rows:
        logger.info(
            "[gateway] carrying %d binding(s) from the previous SQLite index into %s",
            len(rows),
            path,
        )
    return rows


def open_file_binding_store(path: Path | None = None) -> FileBindingStore:
    """File-backed bindings, following the bound principal unless pinned."""
    return FileBindingStore(
        path if path is not None else bindings_file_path,
        adopter=legacy_bindings_for,
    )


def open_binding_store() -> BindingStore:
    """The binding store this process should use."""
    return open_file_binding_store()


__all__ = [
    "BindingStore",
    "legacy_bindings_for",
    "open_binding_store",
    "open_file_binding_store",
]
