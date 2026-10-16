"""Incremental transcript input for bounded deferred-output replay."""

from __future__ import annotations

import re

from rich.ansi import AnsiDecoder
from rich.color import ColorSystem

from config.constants.terminal_host import DEFERRED_OUTPUT_CHUNK_CHARS


class ReplayTranscript:
    """Carry terminal-control and SGR state without retaining complete lines."""

    def __init__(self) -> None:
        self._decoder = AnsiDecoder()
        self._state = "text"
        self._control = ""
        self._carriage_return = False

    def feed(self, text: str) -> str:
        """Return transcript-safe text; the terminal receives the original bytes."""
        if (
            self._state == "text"
            and not self._carriage_return
            and not any(char in text for char in ("\x1b", "\r"))
        ):
            return self._styled_lines(text)
        output: list[str] = []
        for char in text:
            if self._state == "osc":
                if char == "\x07":
                    self._state = "text"
                elif char == "\x1b":
                    self._state = "osc-escape"
            elif self._state == "osc-escape":
                self._state = "text" if char == "\\" else "osc"
            elif self._state == "escape":
                if char == "[":
                    self._control = "\x1b["
                    self._state = "csi"
                elif char == "]":
                    self._state = "osc"
                else:
                    self._state = "escape" if char == "\x1b" else "text"
            elif self._state in ("csi", "long-csi"):
                if self._state == "csi":
                    self._control += char
                    if len(self._control) > DEFERRED_OUTPUT_CHUNK_CHARS:
                        self._control = ""
                        self._state = "long-csi"
                if "@" <= char <= "~":
                    if self._control.endswith("m") or self._control in ("\x1b[2J", "\x1b[3J"):
                        output.append(self._control)
                    self._control = ""
                    self._state = "text"
            else:
                if self._carriage_return:
                    self._carriage_return = False
                    if char == "\n":
                        output.append("\n")
                        continue
                    output.append("\r")
                if char == "\x1b":
                    self._state = "escape"
                elif char == "\r":
                    self._carriage_return = True
                else:
                    output.append(char)
        ready = "".join(output)
        return self._styled_lines(ready)

    def finish(self) -> str:
        """Release a final bare carriage return and discard incomplete controls."""
        return self._style_prefix() + "\r" if self._carriage_return else ""

    def _style_prefix(self) -> str:
        rendered = self._decoder.style.render("\x00", color_system=ColorSystem.TRUECOLOR)
        return rendered.partition("\x00")[0]

    def _styled_lines(self, text: str) -> str:
        parts: list[str] = []
        for part in re.split(r"([\n\r])", text):
            if part in ("\n", "\r"):
                parts.append(part)
            elif part:
                # The transcript keeps a partial line separately from completed
                # lines, so both record and line starts need the active style.
                parts.append(self._style_prefix() + part)
                if "\x1b" in part:
                    self._decoder.decode_line(part)
        return "".join(parts)


__all__ = ["ReplayTranscript"]
