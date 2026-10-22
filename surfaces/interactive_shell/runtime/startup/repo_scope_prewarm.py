"""Load what repository-scope inference reads remotely while the shell waits on the user."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from infrastructure.analytics.source import is_test_run
from infrastructure.harness_providers import prewarm_repo_scopes

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-repo-scope-prewarm"


def warm_repo_scopes(session: Session) -> threading.Thread | None:
    """Start the load on a daemon thread and return it; ``None`` in test processes.

    A turn whose message could name a repository by its short name otherwise
    waits for the connection's repository listing (seconds on GitHub) before it
    can start. Runs on its own thread so the held startup jobs queued behind it
    do not wait on the network. Failures are left for that turn to meet.
    """
    if is_test_run():
        return None
    thread = threading.Thread(target=_warm, args=(session,), name=_THREAD_NAME, daemon=True)
    thread.start()
    return thread


def _warm(session: Session) -> None:
    try:
        prewarm_repo_scopes(session.get_integrations().resolved_integrations)
    except Exception:
        logger.debug("Repository-scope prewarm failed", exc_info=True)


__all__ = ["warm_repo_scopes"]
