"""Open a page in the user's browser, or leave the printed link to them.

Over SSH, or on a Linux host with no graphical session (a server or
container), the user's browser runs on another machine. Nothing is launched
there (it would at best start a text browser inside this terminal), and a
redirect to this machine's ``127.0.0.1`` would never arrive.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Mapping

from config.constants.terminal_host import (
    GRAPHICAL_DISPLAY_ENVS,
    SSH_SESSION_ENVS,
    WSL_DISTRO_ENV,
)


def browser_is_remote(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """Whether the user's browser runs on a different machine than this process."""
    env = os.environ if environ is None else environ
    if any(env.get(name, "").strip() for name in SSH_SESSION_ENVS):
        return True
    if not (sys.platform if platform is None else platform).startswith("linux"):
        return False
    # WSL forwards localhost to Windows, where the user's browser is.
    if env.get(WSL_DISTRO_ENV, "").strip():
        return False
    return not any(env.get(name, "").strip() for name in GRAPHICAL_DISPLAY_ENVS)


def open_in_browser(url: str, *, target: str, cli_auth_attempt_id: str | None = None) -> bool:
    """Open ``url`` in a local browser and record the request; ``False`` if nothing opened."""
    opened = False
    try:
        opened = not browser_is_remote() and bool(webbrowser.open(url))
    except (webbrowser.Error, OSError):
        opened = False
    finally:
        # Imported here: analytics costs ~0.2s to import on the CLI's startup path.
        from infrastructure.analytics.capture import capture_browser_open_requested

        capture_browser_open_requested(
            target=target, opened=opened, cli_auth_attempt_id=cli_auth_attempt_id
        )
    return opened


__all__ = ["browser_is_remote", "open_in_browser"]
