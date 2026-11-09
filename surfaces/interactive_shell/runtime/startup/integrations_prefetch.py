"""Read the configured integrations while the sign-in check waits on the app."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from infrastructure.analytics.source import is_test_run
from infrastructure.harness_providers import configured_integration_services

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-integrations-prefetch"
# The account read is bounded by its own HTTP timeout; this only bounds a
# pathological stall so the shell still starts (the boot then reads it itself).
_WAIT_SECONDS = 30.0


def start_integrations_prefetch() -> Callable[[], None] | None:
    """Start the read for an interactive launch; return a wait that joins it.

    The sign-in check and the account-integrations read are separate requests to
    the same app; made in turn they put two round trips in front of the banner.
    The runtime boot then hydrates from the snapshot this leaves in the process
    cache. Callers must wait before booting: that cache is not single-flight, so
    a boot racing this read would send it again.

    Test processes get ``None`` (same reason as the tool-registry prewarm): a
    shell-entry test must not reach the account app from a thread.
    """
    if is_test_run():
        return None
    thread = threading.Thread(target=_load, name=_THREAD_NAME, daemon=True)
    thread.start()
    return lambda: thread.join(_WAIT_SECONDS)


def _load() -> None:
    try:
        configured_integration_services()
    except Exception:
        # The boot hydrates the integrations itself and reports what failed.
        logger.debug("Integrations prefetch failed", exc_info=True)


__all__ = ["start_integrations_prefetch"]
