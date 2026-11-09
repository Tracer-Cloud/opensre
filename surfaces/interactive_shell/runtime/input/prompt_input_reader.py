"""Convert prompt-toolkit terminal behavior into shell input events."""

from __future__ import annotations

from rich.console import Console

from infrastructure.terminal.prompt_support import (
    CTRL_C_DOUBLE_PRESS_WINDOW_S,
    print_session_resume_hint,
    repl_prompt_ctrl_c_should_exit,
    repl_reset_ctrl_c_gate,
)
from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.runtime.input.events import (
    InputCancelled,
    InputClosed,
    InputEvent,
    InputSubmitted,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import HIGHLIGHT
from surfaces.shared.terminal.components.cpr_stdin import (
    contains_cpr_sequence,
    strip_cpr_sequences,
)

# Stuck-key / paste-corruption spam (seen after CPR redraw glitches) — a long
# run dominated by one character is never a real ask. Re-prompt instead of
# sending it to the model.
_SPAM_MIN_LEN = 40
_SPAM_DOMINANT_RATIO = 0.9


def _looks_like_key_spam(text: str) -> bool:
    """True when *text* is almost entirely one repeated character."""
    body = "".join(text.split())
    if len(body) < _SPAM_MIN_LEN:
        return False
    dominant = max(body.count(ch) for ch in set(body))
    return dominant / len(body) >= _SPAM_DOMINANT_RATIO


class PromptInputReader:
    """Read prompt text and hide terminal-specific control flow from the loop."""

    def __init__(
        self,
        prompt: PromptBuilder,
        state: ReplState,
        session: Session,
        console: Console,
    ) -> None:
        self.prompt = prompt
        self.state = state
        self.session = session
        self.console = console

    async def read(self) -> InputEvent:
        while True:
            try:
                text = await self.prompt.read_prompt_text()
            except EOFError:
                # The prompt swallows Ctrl-D while a turn runs, so EOF here is
                # an idle Ctrl-D or a closed terminal: both end the shell.
                self._render_session_resume_hint()
                return InputClosed()
            except KeyboardInterrupt:
                if self.state.is_dispatch_running():
                    return InputCancelled()
                if repl_prompt_ctrl_c_should_exit():
                    self.state.clear_ctrl_c_exit_hint()
                    return InputClosed()
                self.state.arm_ctrl_c_exit_hint(CTRL_C_DOUBLE_PRESS_WINDOW_S)
                return InputCancelled()

            repl_reset_ctrl_c_gate()
            self.state.clear_ctrl_c_exit_hint()
            raw_text = text
            text = strip_cpr_sequences(text)
            if not text.strip() and contains_cpr_sequence(raw_text):
                continue
            if _looks_like_key_spam(text):
                # Accidental held-key / redraw garbage — do not spend a turn.
                continue
            return InputSubmitted(text)

    def _render_session_resume_hint(self) -> None:
        if not self.session.session_id:
            return
        self.console.print()
        print_session_resume_hint(self.console, self.session.session_id)
        self.console.print(f"[{HIGHLIGHT}]Goodbye![/]")


__all__ = ["PromptInputReader"]
