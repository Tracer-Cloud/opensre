"""When structured input owns the keyboard, hide the free-text typing box.

Ask User menus and execution confirmation need arrow keys / y-n, not the
``[N] ❯`` composer. The live prompt region still redraws under
``patch_stdout``, so this module decides when to omit the typing chrome and
clears any leftover prompt-toolkit paint before raw-stdin menus draw.

Lives beside ``input_prompt/`` (not inside it) to avoid circular imports:
``ask_user`` → visibility must not load ``input_prompt.__init__`` → slash catalog.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime.core.state import ReplState
    from surfaces.interactive_shell.session import Session

# ``_prompt_message`` is a single ``> `` row (no embedded newline). Keep the
# same newline count when the box is hidden so confirmation redraws do not
# shift the region height (see test_prompt_region_height_stable_*).
_HIDDEN_TYPING_BOX_PAD = ""


def typing_box_hidden(session: Session, state: ReplState) -> bool:
    """True when free-text chrome must not compete with structured input.

    Hide the ``[N] ❯`` composer while confirmation, exclusive-stdin menus, or a
    queued Ask User / options payload owns the decision — otherwise users type
    into a box that is about to be replaced by arrow-key options.
    """
    if state.is_awaiting_confirmation():
        return True
    terminal = getattr(session, "terminal", None)
    if terminal is not None and bool(getattr(terminal, "exclusive_stdin_active", False)):
        return True
    return getattr(session, "pending_user_choice", None) is not None


def hidden_typing_box_pad() -> str:
    """Blank stand-in for the rule + ``[N] ❯`` rows (same newline count)."""
    return _HIDDEN_TYPING_BOX_PAD


def clear_live_prompt_paint(session: Session | None = None) -> None:
    """Erase any prompt-toolkit frame still painted before a raw-stdin menu.

    Exclusive-stdin turns normally wait until ``prompt_async`` has exited, but
    ``patch_stdout`` can leave a stale frame. Clearing here matches the Droid
    Ask User UX: menu only, no typing box underneath.
    """
    app: Any = None
    if session is not None:
        terminal = getattr(session, "terminal", None)
        app = getattr(terminal, "prompt_app", None) if terminal is not None else None
    if app is None:
        from prompt_toolkit.application.current import get_app_or_none

        app = get_app_or_none()
    if app is None:
        return
    renderer = getattr(app, "renderer", None)
    if renderer is not None:
        # ``erase`` wipes only the app's own reserved rows (``ESC[J``), leaving
        # the transcript above untouched, so the menu draws inline like Droid.
        # ``clear`` would emit a full-screen ``ESC[2J`` and read as a new window.
        with suppress(Exception):
            renderer.erase()
    if getattr(app, "is_running", False):
        with suppress(Exception):
            app.invalidate()


__all__ = [
    "clear_live_prompt_paint",
    "hidden_typing_box_pad",
    "typing_box_hidden",
]
