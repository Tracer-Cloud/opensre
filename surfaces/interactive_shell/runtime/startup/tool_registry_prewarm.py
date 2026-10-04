"""Load the action-tool registry while the launch waits on the network."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from core.agent_harness.tools import registered_single_turn_tool_names
from infrastructure.analytics.source import is_test_run

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-tool-registry-prewarm"
# Loading the registry is imports and local file reads; this only bounds a
# pathological stall so the shell still starts (the first turn then loads it).
_WAIT_SECONDS = 30.0


class ToolRegistryPrewarm:
    """Loads the single-turn tool registry on a daemon thread; :meth:`wait` joins it.

    The sign-in check and the account-integrations read leave the interpreter
    idle for most of a second, and the first turn (the onboarding menu's
    ``/choose``) needs the registry before it can draw anything. Callers must
    :meth:`wait` before the first turn starts: the registry caches are not
    single-flight, so a turn racing the load would repeat it.
    """

    def __init__(self) -> None:
        self._thread = threading.Thread(target=_load_registry, name=_THREAD_NAME, daemon=True)

    def start(self) -> ToolRegistryPrewarm:
        """Begin loading in the background; return ``self``."""
        self._thread.start()
        return self

    def wait(self, timeout: float = _WAIT_SECONDS) -> None:
        """Block until the load finished, at most ``timeout`` seconds."""
        self._thread.join(timeout)


def start_tool_registry_prewarm() -> Callable[[], None] | None:
    """Start the load for an interactive launch; return its ``wait``.

    Test processes get ``None`` (same reason as the sign-in gate and the demo
    menu): a shell-entry test must not import every tool module on a thread.
    """
    if is_test_run():
        return None
    return ToolRegistryPrewarm().start().wait


def _load_registry() -> None:
    try:
        registered_single_turn_tool_names()
    except Exception:
        # The first turn loads the registry itself and reports what failed.
        logger.debug("Tool registry prewarm failed", exc_info=True)


__all__ = ["ToolRegistryPrewarm", "start_tool_registry_prewarm"]
