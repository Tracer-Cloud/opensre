"""Turn Claude Code's ``--output-format stream-json`` events into progress lines and the final text."""

from __future__ import annotations

import json
import os
import re
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
            return _describe_command(" ".join(str(params.get("command") or "").split()))
        if name in _SEARCH_TOOLS:
            return _clip(f"Searching {params.get('pattern') or ''}")
        return name

    def _relative(self, path: str) -> str:
        if path.startswith(self._workspace.rstrip(os.sep) + os.sep):
            return path[len(self._workspace.rstrip(os.sep)) + 1 :]
        return path


_TEST_RUNNERS = frozenset({"pytest", "jest", "vitest", "mocha", "phpunit", "rspec"})
_TEST_SUBCOMMANDS = {"go": "test", "cargo": "test", "make": "test", "npm": "test", "pnpm": "test"}
_LINTERS = frozenset({"ruff", "mypy", "eslint", "tsc", "flake8", "black", "prettier", "pyright"})
_LOOKUPS = frozenset({"grep", "rg", "sed", "cat", "wc", "ls", "find", "head", "tail", "awk"})
_WRAPPERS = frozenset({"uv", "run", "npx", "poetry", "env", "python", "python3", "-m", "sudo"})
_SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def _describe_command(command: str) -> str:
    """Say what a shell command is for, judged by the programs it runs, not by its text."""
    intents = [
        intent for segment in _SEGMENT_SPLIT.split(command) if (intent := _segment_intent(segment))
    ]
    for wanted in ("tests", "lint", "search", "git"):
        for label, kind in intents:
            if kind == wanted:
                return label
    return _clip(f"Running: {command}")


def _segment_intent(segment: str) -> tuple[str, str] | None:
    words = [word for word in segment.split() if word not in _WRAPPERS]
    if not words:
        return None
    program = words[0].rsplit("/", 1)[-1]
    if program in _LOOKUPS:
        return "Searching the code", "search"
    if program in _TEST_RUNNERS or (
        program in _TEST_SUBCOMMANDS and _TEST_SUBCOMMANDS[program] in words[1:2]
    ):
        paths = [word for word in words[1:] if word.startswith("tests")]
        return (f"Running tests: {' '.join(paths)}" if paths else "Running tests"), "tests"
    if program in _LINTERS:
        return "Checking lint and types", "lint"
    if program == "git":
        return "Inspecting the merge", "git"
    return None


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_LINE_CHARS else text[: _MAX_LINE_CHARS - 1] + "…"


__all__ = ["ClaudeStreamReader"]
