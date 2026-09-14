"""Tests for turning Claude Code stream-json events into progress lines."""

from __future__ import annotations

import json

from integrations.coding_agent.claude_stream import ClaudeStreamReader


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def test_tool_uses_and_narration_become_short_steps_and_the_result_is_kept() -> None:
    # Arrange
    steps: list[str] = []
    reader = ClaudeStreamReader(steps.append, workspace="/repo")
    lines = [
        '{"type":"system","subtype":"init"}',
        "not json at all",
        _assistant({"type": "text", "text": "I'll compare both sides.\nThen edit."}),
        _assistant({"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/a/b.py"}}),
        _assistant({"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/a/b.py"}}),
        _assistant({"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/a/b.py"}}),
        _assistant(
            {"type": "tool_use", "name": "Bash", "input": {"command": "uv run   pytest tests -q"}}
        ),
        _assistant({"type": "tool_use", "name": "Grep", "input": {"pattern": "report"}}),
        json.dumps({"type": "result", "result": "Combined both sides; 12 tests pass."}),
    ]

    # Act
    for line in lines:
        reader.line(line + "\n")

    # Assert: duplicates collapse, paths are relative, commands are whitespace-normalized.
    assert steps == [
        "I'll compare both sides.",
        "Reading a/b.py",
        "Editing a/b.py",
        "Running: uv run pytest tests -q",
        "Searching report",
    ]
    assert reader.result_text == "Combined both sides; 12 tests pass."
