"""Own one prompt recorder and its correlation context until the turn exits."""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from typing import Any

from infrastructure.analytics.prompt_log.recorder import PromptRecorder, current_recorder
from infrastructure.analytics.repl_context import (
    bind_prompt_turn_id,
    bound_repl_turn_context,
    reset_prompt_turn_id,
)

log = logging.getLogger(__name__)


@contextlib.contextmanager
def record_prompt_turn(text: str, session: Any, *, surface: str) -> Iterator[PromptRecorder | None]:
    """Capture each turn independently, restoring the parent context after nested turns."""
    recorder = None
    try:
        recorder = PromptRecorder.start(
            session=session, text=text, turn_kind="agent", surface=surface
        )
    except Exception:
        log.debug("prompt recorder could not start", exc_info=True)
    recorder_token = current_recorder.set(recorder)
    turn_token = bind_prompt_turn_id(recorder.turn_id if recorder is not None else None)
    try:
        with bound_repl_turn_context(
            session_id=getattr(session, "session_id", None), turn_kind="agent"
        ):
            completed = False
            try:
                yield recorder
                completed = True
            finally:
                if recorder is not None:
                    # Signals may use BaseException subclasses owned by a host.
                    # Inspect unwinding without catching or swallowing them.
                    error = None if completed else sys.exception()
                    if error is not None:
                        if isinstance(error, Exception):
                            recorder.set_error("turn_error", str(error))
                        else:
                            recorder.set_error("cancelled", "Agent execution cancelled.")
                    with contextlib.suppress(Exception):
                        recorder.flush()
    finally:
        reset_prompt_turn_id(turn_token)
        current_recorder.reset(recorder_token)
