"""Tests for typed interactive-shell working-directory state."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from config.constants.repl_autonomy import AutoLevel
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.tools import ActionToolScope
from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import make_repl_presenter
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.shell import execute_set_working_directory_tool
from tools.interactive_shell.shared import ExecutionPolicyResult


def _session(working_directory: Path) -> Session:
    store = InMemorySessionStore()
    session = Session(store=store, working_directory=str(working_directory))
    store.open_session(session)
    return session


def _scope(
    session: Session,
    *,
    confirm_fn: object = None,
    is_tty: bool | None = True,
) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False)
    presenter = make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )
    return ActionToolScope(
        session=session,
        console=SimpleNamespace(),
        subprocess_presenter=presenter,
    )


def test_set_working_directory_resolves_relative_path(tmp_path: Path) -> None:
    child = tmp_path / "directory with spaces"
    child.mkdir()
    session = _session(tmp_path)

    result = execute_set_working_directory_tool(
        {"path": child.name},
        _scope(session),
    )

    assert result == {
        "ok": True,
        "working_directory": str(child.resolve()),
        "response_text": str(child.resolve()),
    }
    assert session.working_directory == str(child.resolve())


def test_set_working_directory_failure_preserves_current_directory(tmp_path: Path) -> None:
    session = _session(tmp_path)

    result = execute_set_working_directory_tool(
        {"path": "missing"},
        _scope(session),
    )

    assert result["ok"] is False
    assert result["working_directory"] == str(tmp_path)
    assert session.working_directory == str(tmp_path)
    assert session.history[-1]["ok"] is False


def test_set_working_directory_rejects_regular_file(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    session = _session(tmp_path)

    result = execute_set_working_directory_tool(
        {"path": str(file_path)},
        _scope(session),
    )

    assert result["ok"] is False
    assert "not a directory" in str(result["response_text"])
    assert session.working_directory == str(tmp_path)


def test_session_clear_preserves_working_directory(tmp_path: Path) -> None:
    session = _session(tmp_path)

    session.clear()

    assert session.working_directory == str(tmp_path)


def test_set_working_directory_denial_does_not_mutate_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    session = _session(tmp_path)
    monkeypatch.setattr(
        "tools.interactive_shell.actions.shell.allow_tool",
        lambda _tool_type: ExecutionPolicyResult(
            verdict="deny",
            tool_type="shell",
            reason="blocked by host policy",
        ),
    )

    result = execute_set_working_directory_tool(
        {"path": str(target)},
        _scope(session),
    )

    assert result["ok"] is False
    assert session.working_directory == str(tmp_path)
    assert session.history[-1]["ok"] is False


def test_set_working_directory_confirmation_can_allow_mutation(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    session = _session(tmp_path)
    session.terminal.auto_level = AutoLevel.MED
    prompts: list[str] = []

    def _confirm(prompt: str) -> str:
        prompts.append(prompt)
        return "y"

    result = execute_set_working_directory_tool(
        {"path": str(target)},
        _scope(session, confirm_fn=_confirm),
    )

    assert result["ok"] is True
    assert session.working_directory == str(target.resolve())
    assert prompts == ["Approve this action?"]
