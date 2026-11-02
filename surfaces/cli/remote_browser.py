"""Whether the user's browser runs on a different machine than this CLI.

A browser sign-in that redirects to ``127.0.0.1`` only works when the browser
and the CLI share a loopback interface. Over SSH, or on a Linux host with no
graphical session (a server or container), the browser is elsewhere and the
CLI asks for a pasted code instead.
"""

from __future__ import annotations

import os
import sys
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
    """Whether a browser the user opens cannot reach this machine's loopback."""
    env = os.environ if environ is None else environ
    if any(env.get(name, "").strip() for name in SSH_SESSION_ENVS):
        return True
    if not (sys.platform if platform is None else platform).startswith("linux"):
        return False
    if env.get(WSL_DISTRO_ENV, "").strip():
        return False
    return not any(env.get(name, "").strip() for name in GRAPHICAL_DISPLAY_ENVS)


def paste_code_by_default() -> bool:
    """Whether sign-in should ask for a pasted code without being told to.

    Only an interactive terminal can take the paste; a piped or scripted run
    keeps the loopback callback.
    """
    return sys.stdin.isatty() and browser_is_remote()


__all__ = ["browser_is_remote", "paste_code_by_default"]
