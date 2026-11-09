"""Start one run of a loop now, from a member's chat turn, as a scheduled tick would run it.

The host that runs the scheduler installs the starter: on a hosted gateway the
web app loop sync, which also serves the app's Run now, so both share one
in-flight guard and shutdown drain. The run starts in the background and acts
as a scheduled tick does (a private loop as its owner), never as the turn that
asked for it.
"""

from __future__ import annotations

from collections.abc import Callable

#: A loop id -> "" once its run has started, else a short reason it could not start.
LoopRunStarter = Callable[[str], str]

_NO_STARTER = "This agent can't run loops on demand here; use Run now on the loop's page."

_starter: LoopRunStarter | None = None


def install_loop_run_starter(starter: LoopRunStarter | None) -> None:
    """Set what starts an on-demand run; ``None`` when this process cannot."""
    global _starter
    _starter = starter


def start_loop_run(loop_id: str) -> str:
    """Start a run of ``loop_id`` in the background; "" when it started, else why it did not."""
    starter = _starter
    if starter is None:
        return _NO_STARTER
    return starter(loop_id)


__all__ = ["LoopRunStarter", "install_loop_run_starter", "start_loop_run"]
