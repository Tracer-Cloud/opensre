"""Output that stays on the session agent and is rebound each turn.

A gateway session reuses one :class:`HeadlessAgent` across inbound messages.
Each turn has its own transport destination, so this object stays on the agent
and :meth:`bind` points it at that turn's :class:`TurnOutput` before
dispatch.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from typing import Any

from infrastructure.turn_host.turn_output import TurnOutput


class BindableOutput:
    """Forwards :class:`~core.agent_harness.ports.OutputSink` calls to the bound destination.

    Explicit methods (not ``__getattr__``) keep the port structurally typed so
    construction sites do not need ``type: ignore``.
    """

    def __init__(self) -> None:
        self._inner: TurnOutput | None = None

    def bind(self, output: TurnOutput) -> None:
        """Point subsequent writes at ``output`` for the current turn."""
        self._inner = output

    @property
    def bound(self) -> TurnOutput | None:
        """Currently bound transport destination, if any."""
        return self._inner

    @property
    def turn_cancel(self) -> threading.Event | None:
        """Same cancel Event as the bound transport output (one signal, many readers).

        Explicit (not only ``__getattr__``) so
        :func:`~core.agent_harness.turns.host_cancel.host_cancel_requested` and
        the stream guard see cancel without depending on attribute forwarding.
        """
        inner = self._inner
        if inner is None:
            return None
        cancel = getattr(inner, "turn_cancel", None)
        return cancel if isinstance(cancel, threading.Event) else None

    def _require(self) -> TurnOutput:
        inner = self._inner
        if inner is None:
            raise RuntimeError("gateway turn output not bound")
        return inner

    def print(self, message: str = "") -> None:
        print_fn = getattr(self._require(), "print", None)
        if callable(print_fn):
            print_fn(message)

    def render_response_header(self, label: str) -> None:
        render = getattr(self._require(), "render_response_header", None)
        if callable(render):
            render(label)

    def render_error(self, message: str) -> None:
        self._require().render_error(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        return self._require().stream(
            label=label,
            chunks=self._stop_chunks_on_cancel(chunks),
            suppress_if_starts_with=suppress_if_starts_with,
            defer_want_me_to_closer=defer_want_me_to_closer,
        )

    def _stop_chunks_on_cancel(self, chunks: Iterable[str]) -> Iterator[str]:
        """Stop draining the LLM stream when the host soft-timeout Event fires."""
        for chunk in chunks:
            cancel = self.turn_cancel
            if isinstance(cancel, threading.Event) and cancel.is_set():
                return
            yield chunk

    def finalize(self, answer: str) -> None:
        self._require().finalize(answer)

    def finish_streamed_response(self, answer: str) -> None:
        finish = getattr(self._require(), "finish_streamed_response", None)
        if callable(finish):
            finish(answer)
            return
        self.finalize(answer)

    def set_tool_status(self, status: str) -> None:
        set_status = getattr(self._require(), "set_tool_status", None)
        if callable(set_status):
            set_status(status)

    def note_activity(self, text: str, *, kind: str) -> None:
        """Forward compact hosted-prompt activity when the bound sink records it."""
        note = getattr(self._require(), "note_activity", None)
        if callable(note):
            note(text, kind=kind)

    def __getattr__(self, name: str) -> Any:
        # Forward optional transport-specific attributes (e.g. tool_hooks readers).
        return getattr(self._require(), name)


__all__ = ["BindableOutput"]
