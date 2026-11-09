"""Shared gateway turn-output helpers."""

from __future__ import annotations

from collections.abc import Iterator

from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports


class RecordingTurnOutput:
    """Minimal sink recording streamed and finalized output."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.streamed: list[str] = []
        self.finalized: str | None = None

    def print(self, message: str = "") -> None:
        if message:
            self.lines.append(message)

    def render_response_header(self, label: str) -> None:
        self.lines.append(f"[{label}]")

    def render_error(self, message: str) -> None:
        self.lines.append(f"ERROR: {message}")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterator[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        text = "".join(str(chunk) for chunk in chunks)
        self.streamed.append(text)
        return text

    def finish_streamed_response(self, answer: str) -> None:
        self.finalize(answer)

    def finalize(self, answer: str) -> None:
        self.finalized = answer


__all__ = ["RecordingTurnOutput", "headless_slash_ports"]
