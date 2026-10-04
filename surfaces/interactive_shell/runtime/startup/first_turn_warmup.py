"""Warm the first model turn while the onboarding menu is on screen."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-first-turn-warmup"


def warm_first_turn() -> threading.Thread:
    """Build the agent LLM client and read hosted credits in the background.

    The menu answer's turn otherwise pays for both before its first model call
    (1-5 s for the client and its SDK import). Failures are left for that turn
    to report, so nothing here is surfaced.
    """
    thread = threading.Thread(target=_warm, name=_THREAD_NAME, daemon=True)
    thread.start()
    return thread


def _warm() -> None:
    from core.agent_harness.runtime import default_llm_factory
    from core.llm.hosted_credits import prefetch_hosted_credits

    for step in (default_llm_factory, prefetch_hosted_credits):
        try:
            step()
        except Exception:
            logger.debug("First-turn warm-up step %s failed", step.__name__, exc_info=True)


__all__ = ["warm_first_turn"]
