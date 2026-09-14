"""Turn Claude Code's ``--output-format stream-json`` events into progress lines and the final text."""

from __future__ import annotations

import json
import os
from typing import Any

from integrations.coding_agent.models import Progress

_MAX_LINE_CHARS = 110
_TEXT_TOOLS = {"Read": "Reading", "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing"}
_SEARCH_TOOLS = {"Glob", "Grep"}


class ClaudeStreamReader:
    """Feed it stdout lines; it reports steps through *on_progress* and keeps the result text."""

    def __init__(self, on_progress: Progress, *, workspace: str) -> None:
        self._on_progress = on_progress
        self._workspace = workspace
        self._last = ""
        self.result_text = ""

    def line(self, raw: str) -> None:
        text = raw.strip()
        if not text.startswith("{"):
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return
        if event.get("type") == "result":
            self.result_text = str(event.get("result") or "").strip()
            return
        if event.get("type") != "assistant":
            return
        for block in event.get("message", {}).get("content", []) or []:
            described = self._describe(block)
            if described and described != self._last:
                self._last = described
                self._on_progress(described)

    def _describe(self, block: dict[str, Any]) -> str:
        kind = block.get("type")
        if kind == "text":
            first = str(block.get("text") or "").strip().splitlines()
            return _clip(first[0]) if first else ""
        if kind != "tool_use":
            return ""
        name = str(block.get("name") or "")
        params = block.get("input") or {}
        if name in _TEXT_TOOLS:
            return f"{_TEXT_TOOLS[name]} {self._relative(str(params.get('file_path') or ''))}"
        if name == "Bash":
            return _clip(f"Running: {' '.join(str(params.get('command') or '').split())}")
        if name in _SEARCH_TOOLS:
            return _clip(f"Searching {params.get('pattern') or ''}")
        return name

    def _relative(self, path: str) -> str:
        if path.startswith(self._workspace.rstrip(os.sep) + os.sep):
            return path[len(self._workspace.rstrip(os.sep)) + 1 :]
        return path


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_LINE_CHARS else text[: _MAX_LINE_CHARS - 1] + "…"


__all__ = ["ClaudeStreamReader"]
