"""Turn output that keeps the answer in memory for a caller who polls for it."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from config.constants.gateway import PROMPT_PROGRESS_KIND_NOTE, PROMPT_PROGRESS_KIND_PLAN_DONE
from core.tool import ToolExecutionHooks
from infrastructure.turn_host.status_messages import EMPTY_RESPONSE_MESSAGE

logger = logging.getLogger("gateway")


class CollectingTurnOutput:
    """The ``TurnOutput`` surface with no chat behind it: text is collected, not sent.

    ``records_hosted_activity`` opts this sink into compact shell activity
    (``note_activity``) instead of the chat status line.
    """

    records_hosted_activity = True

    def __init__(self, on_status: Callable[..., None] | None = None) -> None:
        self.tool_hooks: ToolExecutionHooks | None = None
        self.turn_cancel: threading.Event | None = None
        self.answer = ""
        self.failed = False
        self.status = ""
        self._on_status = on_status

    def print(self, message: str = "") -> None:
        if message:
            self._note(message)

    def render_response_header(self, label: str) -> None:
        self._note(label)

    def render_error(self, message: str) -> None:
        # Detail stays in the server log; the caller gets a stable code from the worker.
        logger.warning("remote prompt turn error: %s", message)
        self.failed = True

    def set_tool_status(self, status: str) -> None:
        self._note(status)

    def note_activity(self, text: str, *, kind: str) -> None:
        """Record one compact activity line (tool, plan) for the shell feed."""
        self._note(text, kind=kind)

    def render_plan_breakdown(self, breakdown: str) -> None:
        """Record the settled checklist so the shell themes it once."""
        self._note(breakdown, kind=PROMPT_PROGRESS_KIND_PLAN_DONE)

    def _note(self, status: str, *, kind: str = PROMPT_PROGRESS_KIND_NOTE) -> None:
        """Keep the latest status and hand it to whoever records progress."""
        self.status = status
        if self._on_status is not None:
            self._on_status(status, kind=kind)

    def finish_streamed_response(self, answer: str) -> None:
        self.answer = answer or EMPTY_RESPONSE_MESSAGE

    def finalize(self, answer: str) -> None:
        self.answer = answer

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(str(chunk) for chunk in chunks)
        if not defer_want_me_to_closer:
            self.answer = text or EMPTY_RESPONSE_MESSAGE
        return text


__all__ = ["CollectingTurnOutput"]
