"""Read-only git calls: no prompts, no optional index lock, bounded time."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from config.constants.git import GIT_OPTIONAL_LOCKS_ENV, GIT_TERMINAL_PROMPT_ENV


class ReadStopped(Exception):
    """The analysis stopped, so no further git call of this checkout starts."""


def never_stopped() -> bool:
    return False


def git_output(
    checkout: Path,
    *args: str,
    timeout: float,
    stopped: Callable[[], bool] = never_stopped,
) -> str | None:
    """Unstripped stdout of one git call, or None when it failed or ran past ``timeout`` seconds.

    Raises ``ReadStopped`` instead of starting the call once ``stopped`` is true.
    """
    if stopped():
        raise ReadStopped
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, GIT_TERMINAL_PROMPT_ENV: "0", GIT_OPTIONAL_LOCKS_ENV: "0"},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


__all__ = ["ReadStopped", "git_output", "never_stopped"]
