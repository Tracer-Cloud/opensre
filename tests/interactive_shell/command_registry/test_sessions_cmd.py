"""Interactive /sessions selection and resume handoff."""

from __future__ import annotations

import importlib
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console


def _session() -> SimpleNamespace:
    records: list[tuple[str, str]] = []

    def _record(kind: str, text: str, **_kwargs: Any) -> None:
        records.append((kind, text))

    return SimpleNamespace(
        session_id="current-session",
        started_at=0.0,
        resumed_from_name=None,
        record=_record,
        records=records,
    )


def _repo(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"session_id": "current-session", "name": "Current work"},
        {"session_id": "target-session", "name": "Investigate latency"},
    ]

    def _load_recent(_limit: int) -> list[dict[str, str]]:
        return rows

    def _default_session_repo() -> SimpleNamespace:
        return SimpleNamespace(load_recent=_load_recent)

    monkeypatch.setattr(
        "core.agent_harness.spi.defaults.default_session_repo",
        _default_session_repo,
    )


def test_sessions_picker_resumes_highlighted_session(monkeypatch: pytest.MonkeyPatch) -> None:
    command = importlib.import_module(
        "surfaces.interactive_shell.command_registry.session_cmds.list"
    )
    _repo(monkeypatch)
    session = _session()
    events: list[str] = []
    resumed: dict[str, Any] = {}

    monkeypatch.setattr(command, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(command, "choose_recent_session", lambda _items: "target-session")
    monkeypatch.setattr(command, "prepare_repl_output_line", lambda: events.append("gap"))

    def _resume(prefix: str, _session: Any, _console: Console, *, slash_command: str) -> bool:
        events.append("resume")
        resumed.update(prefix=prefix, slash_command=slash_command)
        return True

    monkeypatch.setattr(command, "resume_session_by_prefix", _resume)

    assert command._cmd_sessions(session, Console(file=StringIO()), []) is True
    assert events == ["gap", "resume"]
    assert resumed == {"prefix": "target-session", "slash_command": "/sessions target-s"}
    assert session.records == []


def test_sessions_picker_does_not_resume_current_session(monkeypatch: pytest.MonkeyPatch) -> None:
    command = importlib.import_module(
        "surfaces.interactive_shell.command_registry.session_cmds.list"
    )
    _repo(monkeypatch)
    session = _session()
    output = StringIO()

    monkeypatch.setattr(command, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(command, "choose_recent_session", lambda _items: "current-session")
    monkeypatch.setattr(command, "prepare_repl_output_line", lambda: None)

    def _resume(*_args: Any, **_kwargs: Any) -> bool:
        pytest.fail("current session must not be rebound")

    monkeypatch.setattr(command, "resume_session_by_prefix", _resume)

    assert command._cmd_sessions(session, Console(file=output), []) is True
    assert "Already in this session" in output.getvalue()
    assert session.records == [("slash", "/sessions")]


def test_sessions_prints_list_when_not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    command = importlib.import_module(
        "surfaces.interactive_shell.command_registry.session_cmds.list"
    )
    _repo(monkeypatch)
    session = _session()
    output = StringIO()
    monkeypatch.setattr(command, "repl_tty_interactive", lambda: False)

    assert command._cmd_sessions(session, Console(file=output), []) is True
    assert "target-s" in output.getvalue()
    assert "/resume <id>" in output.getvalue()
    assert session.records == [("slash", "/sessions")]


def test_agent_observes_noninteractive_session_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.interactive_shell.command_registry import dispatch_slash
    from surfaces.interactive_shell.session import Session
    from tools.interactive_shell.actions.slash import _slash_observation

    _repo(monkeypatch)
    session = Session()
    console = Console(file=StringIO(), force_terminal=False)

    assert dispatch_slash("/sessions", session, console, is_tty=False)
    observation = _slash_observation(SimpleNamespace(session=session, history_start=0), "/sessions")
    assert isinstance(observation, dict)
    assert "target-s" in observation["output"]
    assert "/resume <id>" in observation["output"]


def test_sessions_with_arguments_never_opens_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.interactive_shell.command_registry import dispatch_slash
    from surfaces.interactive_shell.session import Session

    command = importlib.import_module(
        "surfaces.interactive_shell.command_registry.session_cmds.list"
    )

    def _unexpected_picker(_items: Any) -> None:
        pytest.fail("invalid arguments must not open the picker")

    monkeypatch.setattr(command, "choose_recent_session", _unexpected_picker)
    session = Session()
    output = StringIO()
    assert dispatch_slash("/sessions ignored", session, Console(file=output))
    assert "usage: /sessions" in output.getvalue()
    assert session.history[-1]["ok"] is False
