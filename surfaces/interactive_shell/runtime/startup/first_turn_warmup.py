"""Warm the first model turn while the onboarding menu is on screen."""

from __future__ import annotations

import importlib
import logging
import threading
import time

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-first-turn-warmup"
_SDK_THREAD_NAME = "opensre-sdk-import-warmup"
#: What the first turn imports before it can build a client: the SDK, the
#: endpoint resources it loads lazily, and the harness runtime that builds it.
_SDK_MODULES = (
    "openai",
    "openai.resources.chat",
    "openai.resources.responses",
    "core.agent_harness.runtime",
)
_SHUTDOWN_JOIN_SECONDS = 2.0
_threads: list[threading.Thread] = []


def warm_first_turn() -> threading.Thread:
    """Build and prewarm the agent LLM client and read hosted credits in the background.

    The menu answer's turn otherwise pays for both before its first model call
    (1-5 s for the client and its SDK import, then about 0.5-2 s more for the
    SDK's lazily loaded request and response path). Failures are left for that
    turn to report, so nothing here is surfaced.
    """
    thread = threading.Thread(target=_warm, name=_THREAD_NAME, daemon=True)
    _threads.append(thread)
    thread.start()
    return thread


def warm_sdk_imports() -> threading.Thread:
    """Import what the first turn needs while the user is away signing in.

    A fresh login has no client to build yet, but the SDK and runtime imports
    behind it need no credentials, and the browser round-trip is idle time to
    pay for them in. A missing module is left for the first turn to report.
    """
    thread = threading.Thread(target=_import_sdk, name=_SDK_THREAD_NAME, daemon=True)
    _threads.append(thread)
    thread.start()
    return thread


def _import_sdk() -> None:
    for name in _SDK_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            logger.debug("SDK warm-up import %s failed", name, exc_info=True)


def join_first_turn_warmup(timeout: float = _SHUTDOWN_JOIN_SECONDS) -> None:
    """Wait, at most ``timeout`` seconds in all, for warm-ups still running at exit.

    The thread is a daemon, so a client build or credit read still blocked on
    the network after that bound cannot hold the process open.
    """
    deadline = time.monotonic() + timeout
    while _threads:
        _threads.pop().join(max(0.0, deadline - time.monotonic()))


def _prepare_llm_client() -> None:
    """Build the agent client and load its SDK request path (no request is sent)."""
    from core.agent_harness.runtime import default_llm_factory

    prewarm = getattr(default_llm_factory(), "prewarm", None)
    if callable(prewarm):
        prewarm()


def _warm() -> None:
    from core.llm.hosted_credits import prefetch_hosted_credits

    for step in (_prepare_llm_client, prefetch_hosted_credits):
        try:
            step()
        except Exception:
            logger.debug("First-turn warm-up step %s failed", step.__name__, exc_info=True)


__all__ = ["join_first_turn_warmup", "warm_first_turn", "warm_sdk_imports"]
