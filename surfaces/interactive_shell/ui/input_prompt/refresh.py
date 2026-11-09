"""Prompt redraw and pending-input prefill wiring."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from surfaces.interactive_shell.runtime import Session


def wire_prompt_refresh(
    session: Session,
    pt_app: Any,
    loop: asyncio.AbstractEventLoop,
) -> Callable[[], None]:
    """Register session hook to prefill pending text and redraw the active prompt."""

    def invalidate_prompt() -> None:
        loop.call_soon_threadsafe(pt_app.invalidate)

    def refresh_active_prompt() -> None:
        def _apply() -> None:
            pending = session.terminal.pending_prompt_default
            buffer = pt_app.current_buffer
            # Never clobber text the user is actively typing.
            if not pending or buffer.text:
                invalidate_prompt()
                return
            if session.terminal.pending_prompt_autosubmit:
                # Mid-turn (e.g. ``/goal set`` → ``set_auto_command``): keep the
                # queue and let the next ``read_prompt_text`` autosubmit. Calling
                # ``validate_and_handle`` here nests a second ``execute_shell_turn``
                # inside the slash turn and doubles the live PostHog answer.
                if session.terminal.dispatch_active:
                    invalidate_prompt()
                    return
                # Idle prompt: auto-submit so interactive setup/connect commands
                # take the exclusive-stdin path without waiting for Enter.
                # ``pt_app.is_running`` under-reports during some waits, so we do
                # not gate on it here — ``dispatch_active`` is the nest guard.
                session.terminal.pending_prompt_default = None
                session.terminal.pop_pending_autosubmit()
                plain = session.terminal.pop_pending_plain_turn()
                session.terminal.last_input_autosubmitted = not plain
                buffer.text = pending
                try:
                    buffer.validate_and_handle()
                except Exception:  # noqa: BLE001
                    session.terminal.pending_prompt_default = pending
                    session.terminal.pending_prompt_autosubmit = True
                    session.terminal.pending_prompt_plain_turn = plain
                    session.terminal.last_input_autosubmitted = False
            elif pt_app.is_running:
                session.terminal.pending_prompt_default = None
                buffer.text = pending
            invalidate_prompt()

        loop.call_soon_threadsafe(_apply)

    session.terminal.prompt_refresh_fn = refresh_active_prompt
    return invalidate_prompt
