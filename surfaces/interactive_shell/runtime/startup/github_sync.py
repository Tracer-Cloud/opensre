"""Pull the workspace GitHub connection into the local store as the shell starts."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from infrastructure.analytics.source import is_test_run

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime import Session

logger = logging.getLogger(__name__)

_STOP_JOIN_SECONDS = 1.0


class WorkspaceGitHubSync:
    """Handle for the startup sync; :meth:`stop` before the session closes."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="opensre-github-workspace-sync", daemon=True
        )

    def start(self) -> WorkspaceGitHubSync:
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the sync: it writes no credentials and refreshes no session after this."""
        with self._lock:
            self._stopped.set()
        self._thread.join(timeout=_STOP_JOIN_SECONDS)

    def _run(self) -> None:
        from integrations.github import sync_workspace_github

        try:
            result = sync_workspace_github(still_wanted=lambda: not self._stopped.is_set())
        except Exception:
            logger.debug("[github-sync] startup sync failed", exc_info=True)
            return
        if not result.changed:
            return
        # Held against stop() so a closed session is never refreshed.
        with self._lock:
            if not self._stopped.is_set():
                self._session.refresh_integration_state()


def start_workspace_github_sync(session: Session) -> WorkspaceGitHubSync | None:
    """Sync in the background so a slow webapp never delays the prompt.

    A change lands in the store; the session re-resolves integrations so the
    next turn sees GitHub connected (or gone) without a restart.
    """
    if is_test_run():
        return None
    return WorkspaceGitHubSync(session).start()


__all__ = ["WorkspaceGitHubSync", "start_workspace_github_sync"]
