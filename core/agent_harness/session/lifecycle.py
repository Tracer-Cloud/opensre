"""Centralized session lifecycle owner for every surface.

``SessionManager`` is the single component that creates, resolves, rotates,
restores, and flushes :class:`SessionCore` objects (surfaces subclass it).
Surfaces (interactive
shell, gateway, headless) delegate session lifecycle to it instead of each
re-implementing bootstrap + persistence wiring:

- **create** — a fresh session: construct, run the core bootstrap (persistent
  tasks + integration hydration/warm), and open its storage stream.
- **resolve** — load a persisted session by id: construct with that id, run the
  core bootstrap, restore its saved conversation context, and reopen storage.
- **rotate** — close the outgoing session and create a fresh replacement (new
  handle; used by the gateway).
- **open_store** — open the JSONL stream for an already-bootstrapped handle
  (interactive REPL entry after ``SessionBootstrapSpec``).
- **rotate_in_place** / **rebind_for_resume** — flush + reset the *live* handle
  the REPL already holds (``/new`` / ``/resume``), preserving loop-owned UI
  state instead of releasing it.
- **restore_context** — rehydrate messages / accumulated context / history from
  a persisted session dict.
- **close** — terminal teardown of a discarded handle: flush + release
  resources (cancel warm task, drop background references).

Surface-specific concerns stay with the surface: the shell layers terminal UI
state (theme, grounding providers, prompt history) on top of a manager-created
session; the gateway injects per-chat metadata. Neither re-implements the core
bootstrap, and neither reaches across surfaces to do it.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from core.agent_harness.session.persistence.contracts import (
    RestoreContextKey,
    SessionRepo,
    SessionStore,
)

# Import from submodules (not the package __init__) so the session package can
# re-export SessionManager without a circular import.
from core.agent_harness.session.session_core import SessionCore
from infrastructure.observability.trace.spans import component_span
from infrastructure.scheduling.task_registry import TaskRegistry

logger = logging.getLogger(__name__)

# In-place lifecycle methods return the caller's own session type (a surface's
# ``Session`` subclass or a plain ``SessionCore``), so they preserve it.
_S = TypeVar("_S", bound=SessionCore)


class SessionManager:
    """Owns the create / resolve / rotate / restore / flush session lifecycle.

    Storage and repo backends are injectable so tests can run against in-memory
    persistence; production surfaces use the shared JSONL singletons (resolved
    lazily to avoid importing the package ``__init__`` from within it).
    """

    def __init__(
        self,
        *,
        store: SessionStore | None = None,
        repo: SessionRepo | None = None,
    ) -> None:
        if store is None or repo is None:
            from core.agent_harness.session import (
                DEFAULT_SESSION_REPO,
                DEFAULT_SESSION_STORE,
            )

            store = store or DEFAULT_SESSION_STORE
            repo = repo or DEFAULT_SESSION_REPO
        self._store = store
        self._repo = repo

    @classmethod
    def for_session(cls, session: SessionCore) -> SessionManager:
        """Build a manager bound to a live session's own store.

        The single named construction point for the in-place lifecycle calls
        (``/new`` / ``/resume``) so they all bind to ``session.store``
        consistently instead of re-passing it at each call site.
        """
        return cls(store=session.store)

    # ─── Core bootstrap ──────────────────────────────────────────────────

    def bootstrap(
        self,
        session: _S,
        *,
        hydrate_integrations: bool = True,
        warm_integrations: bool = False,
        persistent_tasks: bool = True,
    ) -> _S:
        """Apply the surface-agnostic startup mutations to ``session``.

        This is the single definition of "a booted session": a persistent task
        registry and hydrated (optionally warmed) integration state. Surface UI
        wiring is layered by the surface after this returns.
        """
        if persistent_tasks:
            session.task_registry = TaskRegistry.persistent()
        # Safe read-only facts (version/env) so agents never need subprocess introspection.
        with component_span("runtime_metadata:bootstrap", session_id=session.session_id):
            session.refresh_runtime_metadata()
        if hydrate_integrations:
            session.hydrate_configured_integrations()
        if warm_integrations:
            session.warm_resolved_integrations()
        return session

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def create(
        self,
        *,
        session_id: str | None = None,
        hydrate_integrations: bool = True,
        warm_integrations: bool = False,
        persistent_tasks: bool = True,
        open_store: bool = True,
    ) -> SessionCore:
        """Build a fresh session, bootstrap it, and open its storage stream."""
        session = SessionCore(session_id=session_id) if session_id else SessionCore()
        # Align the session's own persistence backend with the manager's, so
        # session.record()/append go through the same storage the manager opens
        # and flushes. Otherwise an injected backend is bypassed by the default
        # JSONL field on SessionCore.
        session.store = self._store
        self.bootstrap(
            session,
            hydrate_integrations=hydrate_integrations,
            warm_integrations=warm_integrations,
            persistent_tasks=persistent_tasks,
        )
        if open_store:
            self.open_store(session)
        return session

    def open_store(self, session: _S) -> _S:
        """Open the JSONL stream for an already-bootstrapped session handle.

        The interactive shell bootstraps via ``SessionBootstrapSpec`` first, then
        calls this once it knows the run is an interactive REPL (not a one-shot
        ``initial_input`` path).
        """
        session.store = self._store
        self._store.open_session(session)
        return session

    def resolve(
        self,
        session_id: str,
        *,
        hydrate_integrations: bool = True,
        warm_integrations: bool = True,
        persistent_tasks: bool = True,
    ) -> SessionCore:
        """Load a persisted session by id: bootstrap, restore context, reopen storage."""
        session = self.create(
            session_id=session_id,
            hydrate_integrations=hydrate_integrations,
            warm_integrations=warm_integrations,
            persistent_tasks=persistent_tasks,
            open_store=False,
        )
        data = self._repo.load_session(session_id)
        self.restore_context(session, data)
        self._store.reopen_session(session.session_id)
        return session

    def rotate(
        self,
        *,
        old_session_id: str | None = None,
        new_session_id: str | None = None,
        warm_integrations: bool = True,
        extract_memory: bool = True,
    ) -> SessionCore:
        """Close the outgoing session (if any) and create its replacement."""
        if old_session_id:
            outgoing = SessionCore(session_id=old_session_id)
            # Reconstructed handle: align its backend with the manager's so the
            # close flush lands on the same storage the manager owns.
            outgoing.store = self._store
            # Restore the persisted transcript so session-end memory extraction
            # sees the outgoing conversation (not an empty reconstructed handle).
            self.restore_context(outgoing, self._repo.load_session(old_session_id))
            # Do not wait for extraction: gateway /new must stay responsive.
            self.close(
                outgoing,
                wait_for_memory_extraction=False,
                extract_memory=extract_memory,
            )
        return self.create(session_id=new_session_id, warm_integrations=warm_integrations)

    def rotate_in_place(self, session: _S) -> _S:
        """Flush the outgoing session file, reset state, and open a new session id.

        Mutates the live ``session`` handle the REPL already holds (``/new``).
        The caller restores any conversation context to carry forward
        (``agent.messages``, ``accumulated_context``, ``resumed_from_name``)
        after this returns.

        Flushes but does not release resources: ``clear()`` resets in-memory
        state (and cancels the warm task) while the loop-owned
        ``prompt_refresh_fn`` is preserved for the continuing REPL. Schedules
        memory extraction from the outgoing transcript before clearing it.
        """
        session.store = self._store
        self._flush(session)
        self._schedule_memory_extraction(session)
        session.clear()
        self._store.open_session(session)
        return session

    def rebind_for_resume(
        self,
        session: _S,
        *,
        session_id: str,
        started_at: Any | None = None,
    ) -> _S:
        """Point the live session handle at a persisted id before :meth:`restore_context`.

        Used by the interactive shell ``/resume`` command on the in-process
        session object. When ``session_id`` differs from the current id the
        outgoing file is flushed; otherwise only in-memory state is cleared
        without rotating identity. Either way the live handle is reused, so
        loop-owned ``prompt_refresh_fn`` is preserved (flush, not close).
        """
        session.store = self._store
        if session.session_id != session_id:
            self._flush(session)
            self._schedule_memory_extraction(session)
            session.clear(rotate_identity=False)
            session.session_id = session_id
            if isinstance(started_at, str) and started_at:
                with contextlib.suppress(Exception):
                    session.started_at = datetime.fromisoformat(started_at).timestamp()
            self._store.reopen_session(session_id)
        else:
            session.clear(rotate_identity=False)
            session.session_id = session_id
        return session

    def restore_context(self, session: _S, data: dict[str, Any] | None) -> _S:
        """Rehydrate conversation messages, accumulated context, and history.

        ``data`` is the persisted session dict from ``SessionRepo.load_session``;
        a ``None`` or empty dict leaves the session untouched.
        """
        if not data:
            return session
        messages = data.get(RestoreContextKey.CLI_AGENT_MESSAGES)
        if isinstance(messages, list):
            restored: list[tuple[str, str]] = []
            for item in messages:
                try:
                    role, content = item
                except (TypeError, ValueError):
                    continue
                if role in {"user", "assistant"} and isinstance(content, str) and content:
                    restored.append((role, content))
            session.cli_agent_messages = restored
        context = data.get(RestoreContextKey.ACCUMULATED_CONTEXT)
        if isinstance(context, dict):
            session.accumulated_context = dict(context)
        goal_state = data.get(RestoreContextKey.SESSION_GOAL_STATE)
        if isinstance(goal_state, dict):
            from core.agent_harness.session_goal.persist import (
                apply_session_goal_state,
            )

            apply_session_goal_state(session, goal_state)
        plan_state = data.get(RestoreContextKey.TASK_PLAN_STATE)
        if plan_state is not None:
            from core.agent_harness.task_plan.persist import apply_task_plan_state

            apply_task_plan_state(session, plan_state)
        history = data.get(RestoreContextKey.HISTORY)
        if isinstance(history, list):
            session.history = [dict(item) for item in history if isinstance(item, dict)]
        working_directory = data.get(RestoreContextKey.WORKING_DIRECTORY)
        if isinstance(working_directory, str) and working_directory.strip():
            with contextlib.suppress(OSError, RuntimeError):
                restored_directory = Path(working_directory).expanduser().resolve(strict=True)
                if restored_directory.is_dir():
                    session.working_directory = str(restored_directory)
        return session

    def close(
        self,
        session: SessionCore,
        *,
        wait_for_memory_extraction: bool = True,
        extract_memory: bool = True,
    ) -> None:
        """Finalize a session for good: persist buffered state and release resources.

        This is the terminal teardown hook — the session handle is being
        discarded (end of a REPL run, or ``rotate``'s outgoing session). It is
        NOT for the in-place swaps (``/new`` / ``/resume``) which reuse the live
        handle; those call :meth:`rotate_in_place` / :meth:`rebind_for_resume`,
        which flush without releasing loop-owned UI state.

        Persisting is best-effort (a failed flush must not crash teardown);
        the session releases its own resources (:meth:`SessionCore.release_resources`)
        to prevent per-session leaks. Long-term memory extraction also runs after
        every recorded turn; this close path runs it again after release when
        ``extract_memory`` is true. Process exit (default) waits for extraction
        to finish so durable facts are not dropped; gateway rotation passes
        ``wait_for_memory_extraction=False`` so inbound handling stays
        responsive, and skips extraction entirely unless gateway memory is opted in.
        """
        self._flush(session)
        # Snapshot messages before release/clear; the background extractor must
        # not retain a reference to the live session object.
        messages = list(getattr(session, "cli_agent_messages", []) or [])
        from infrastructure.observability.trace.spans import emit_thread_boundary

        emit_thread_boundary(session.session_id, name="session_end", phase="session_end")
        session.release_resources()
        if extract_memory:
            self._schedule_memory_extraction_from_messages(
                messages,
                wait_for_completion=wait_for_memory_extraction,
            )

    def flush(self, session: SessionCore) -> None:
        """Best-effort persist buffered session state (including session goals).

        Gateway ``resolve`` rebuilds a fresh :class:`SessionCore` from disk each
        inbound turn, so mutations such as ``/goal pause`` must flush or the
        next message restores the pre-pause snapshot and continuation resumes.
        Safe mid-session even after a trailing ``leaf``: stores append a new
        ``session_goal_state`` when it changed and do not write a second leaf.
        Failures are logged, never raised.
        """
        self._flush(session)

    @staticmethod
    def _flush(session: SessionCore) -> None:
        """Best-effort persist through the session's own backend.

        Flushes through ``session.store`` — the backend it recorded turns
        through — so the end-of-session marker lands with the data. A failed
        flush is logged, never raised, so teardown/rotation cannot crash.
        """
        try:
            session.store.flush(session)
        except OSError:
            logger.debug("[session] flush failed", exc_info=True)

    @staticmethod
    def _schedule_memory_extraction(session: SessionCore) -> None:
        messages = list(getattr(session, "cli_agent_messages", []) or [])
        SessionManager._schedule_memory_extraction_from_messages(messages)

    @staticmethod
    def _schedule_memory_extraction_from_messages(
        messages: list[tuple[str, str]],
        *,
        wait_for_completion: bool = False,
    ) -> None:
        from core.agent_harness.session.memory_extraction import schedule_memory_extraction

        schedule_memory_extraction(
            messages,
            wait_for_completion=wait_for_completion,
        )


__all__ = ["SessionManager"]
