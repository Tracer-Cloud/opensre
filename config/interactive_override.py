"""Context-local override that reports the interactive shell as switched off.

A slash command dispatched without a TTY (gateway and scripted turns) must run
as if ``OPENSRE_INTERACTIVE=0``. The gateway runs several turns in one process,
so the override is a ``ContextVar`` instead of a write to ``os.environ``: a
process-wide write races between overlapping commands and leaks into the CLI
shell. Readers call :func:`interactive_env_value` instead of reading the env
var directly.

The override does not follow work onto a new thread or into a child process.
Run a thread's target through ``contextvars.copy_context().run`` on the calling
thread, and merge :func:`interactive_override_env` into a child's environment.

Leaf module: only depends on :mod:`config.constants`, so any layer can import it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from config.constants.product import OPENSRE_INTERACTIVE_ENV

_DISABLED_VALUE = "0"

_FORCED_NON_INTERACTIVE: ContextVar[bool] = ContextVar(
    "opensre_forced_non_interactive", default=False
)


@contextmanager
def forced_non_interactive() -> Iterator[None]:
    """Report the shell as non-interactive to readers in this context for the block."""
    token = _FORCED_NON_INTERACTIVE.set(True)
    try:
        yield
    finally:
        _FORCED_NON_INTERACTIVE.reset(token)


def interactive_env_value() -> str | None:
    """Return the effective ``OPENSRE_INTERACTIVE`` value: the override first, then the env."""
    if _FORCED_NON_INTERACTIVE.get():
        return _DISABLED_VALUE
    return os.environ.get(OPENSRE_INTERACTIVE_ENV)


def interactive_override_env() -> dict[str, str]:
    """Return the env entries a child process needs to inherit the override; empty when unset."""
    if _FORCED_NON_INTERACTIVE.get():
        return {OPENSRE_INTERACTIVE_ENV: _DISABLED_VALUE}
    return {}


__all__ = [
    "forced_non_interactive",
    "interactive_env_value",
    "interactive_override_env",
]
