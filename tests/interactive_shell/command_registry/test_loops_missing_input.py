"""Missing-argument collection for ``/loops run`` through real dispatch."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from infrastructure.scheduling.scheduler.loops import list_loop_summaries
from infrastructure.scheduling.scheduler.storage import add_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.interactive_shell.command_registry import dispatch_slash, loops_cmds
from surfaces.interactive_shell.command_registry import help as help_cmd
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session


@pytest.fixture(autouse=True)
def isolated_loop_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(loops_cmds, "repl_tty_interactive", lambda: True)


def _store_loop(task_id: str, name: str) -> ScheduledTask:
    task = ScheduledTask(
        id=task_id,
        name=name,
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * *",
        provider=Provider.SLACK,
        chat_id=task_id,
    )
    add_task(task)
    return task


def _record_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    runs: list[tuple[str, ...]] = []

    def _run_now(console: Console, task_ids: tuple[str, ...]) -> bool:  # noqa: ARG001
        runs.append(task_ids)
        return True

    monkeypatch.setattr(loops_cmds, "_run_loop_task_ids_once", _run_now)
    return runs


def _refuse_picker(**_kwargs: Any) -> str:
    raise AssertionError("the picker must not open in this path")


def dispatch(command: str) -> str:
    """Dispatch like the REPL does: reserve stdin from the typed command line."""
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(
        command, session, Console(file=output, width=100, color_system=None), is_tty=True
    )
    return output.getvalue()


def test_missing_id_opens_picker_and_runs_the_selected_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    seen: dict[str, Any] = {}

    def _choose(**kwargs: Any) -> str:
        seen.update(kwargs)
        return str(kwargs["choices"][0][0])

    monkeypatch.setattr(loops_cmds, "repl_choose_one", _choose)

    text = dispatch("/loops run")

    assert seen["breadcrumb"] == "/loops run"
    assert [choice[0] for choice in seen["choices"]] == ["morning-ops"]
    assert "Morning ops" in seen["choices"][0][1]
    assert runs == [("morning-ops",)]
    assert "usage:" not in text


def test_supplied_id_runs_directly_without_opening_the_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", _refuse_picker)

    assert "usage:" not in dispatch("/loops run morning-ops")
    assert runs == [("morning-ops",)]


def test_deferred_blank_id_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``slash_invoke(args=["run", ""])`` re-submits ``/loops run ''``.

    The loop reserved stdin for that text before dispatch saw it, so dispatch
    must read the quoted blank as a missing id rather than a loop named ``''``.
    """
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", lambda **_kwargs: "morning-ops")

    text = dispatch("/loops run ''")

    assert runs == [("morning-ops",)]
    assert "not found" not in text
    assert "usage:" not in text


def test_cancelling_the_picker_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", lambda **_kwargs: None)

    dispatch("/loops run")

    assert runs == []
    assert len(list_loop_summaries()) == 1


def test_non_tty_prints_usage_without_opening_the_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_tty_interactive", lambda: False)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", _refuse_picker)

    assert "usage: /loops run LOOP_ID" in dispatch("/loops run")
    assert runs == []


def test_without_exclusive_stdin_the_handler_falls_back_to_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", _refuse_picker)
    output = io.StringIO()

    dispatch_slash(
        "/loops run",
        Session(),
        Console(file=output, width=100, color_system=None),
        is_tty=True,
    )

    assert "usage: /loops run LOOP_ID" in output.getvalue()
    assert runs == []


def test_empty_store_hints_instead_of_opening_a_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", _refuse_picker)

    text = dispatch("/loops run")

    assert "no loops configured yet" in text
    assert "usage:" not in text
    assert runs == []


def test_unknown_id_still_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(loops_cmds, "repl_choose_one", _refuse_picker)

    text = dispatch("/loops run absent")

    assert "not found" in text
    assert runs == []


def test_help_selection_dispatch_opens_the_same_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_loop("morning-ops", "Morning ops")
    runs = _record_runs(monkeypatch)
    monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(help_cmd, "browse_help_commands", lambda _sections: "/loops")
    monkeypatch.setattr(help_cmd, "repl_choose_subcommand", lambda **_kwargs: "run")
    monkeypatch.setattr(loops_cmds, "repl_choose_one", lambda **_kwargs: "morning-ops")

    dispatch("/help")

    assert runs == [("morning-ops",)]
