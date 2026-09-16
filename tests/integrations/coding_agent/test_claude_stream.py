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

    # Assert: duplicates collapse, paths are relative, commands become intents.
    assert steps == [
        "I'll compare both sides.",
        "Reading a/b.py",
        "Editing a/b.py",
        "Running tests: tests",
        "Searching report",
    ]
    assert reader.result_text == "Combined both sides; 12 tests pass."


def test_command_intent_is_judged_by_the_program_not_by_words_in_its_text() -> None:
    # Arrange
    steps: list[str] = []
    reader = ClaudeStreamReader(steps.append, workspace="/repo")
    commands = [
        'rg "pytest" tests',
        "uv run pytest tests/scheduler -x -q 2>&1 | tail -20",
        "git status --short && grep -n marker file.py",
        "npx eslint src",
        "make test",
        "./scripts/deploy.sh --dry-run",
    ]

    # Act
    for command in commands:
        reader.line(_assistant({"type": "tool_use", "name": "Bash", "input": {"command": command}}))

    # Assert
    assert steps == [
        "Searching the code",
        "Running tests: tests/scheduler",
        "Searching the code",
        "Checking lint and types",
        "Running tests",
        "Running: ./scripts/deploy.sh --dry-run",
    ]
