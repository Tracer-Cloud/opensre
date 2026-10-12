"""/memory show collects a missing name through a searchable picker."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.constants import OPENSRE_MEMORY_DIR_ENV
from core.domain.memory import MemoryType, list_memories, save_memory
from surfaces.interactive_shell.command_registry import (
    dispatch_slash,
    input_collection,
    memory_cmds,
)
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.memory_input import build_memory_picker


@pytest.fixture(autouse=True)
def isolated_memory_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)


def run_keys(app: Any, keys: str) -> Any:
    """Drive the actual prompt-toolkit bindings with a deterministic input pipe."""
    with create_pipe_input() as pipe:
        app.input = pipe
        app.output = DummyOutput()
        return app.run(pre_run=lambda: pipe.send_text(keys))


def dispatch(command: str, *, tty: bool = True) -> str:
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output), is_tty=tty)
    return output.getvalue()


def store_memories() -> None:
    save_memory(
        slug="eks-prod-cluster",
        memory_type=MemoryType.INFRASTRUCTURE,
        description="Production EKS cluster details",
        body="prod body",
    )
    save_memory(
        slug="oncall-runbook",
        memory_type=MemoryType.INFRASTRUCTURE,
        description="How to page the on call rotation",
        body="runbook body",
    )


def test_picker_searches_names_and_descriptions_then_displays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_memories()

    def collect(app: Any) -> Any:
        # "page" matches only the on-call runbook's description, not its name.
        return run_keys(app, "page\r")

    monkeypatch.setattr(memory_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        out = dispatch("/memory show")
    assert "runbook body" in out
    assert "prod body" not in out

    def by_name(app: Any) -> Any:
        return run_keys(app, "eks\r")

    monkeypatch.setattr(memory_cmds, "run_command_input", by_name)
    with create_app_session(output=DummyOutput()):
        out = dispatch("/memory show")
    assert "prod body" in out
    assert "runbook body" not in out


def test_picker_enter_returns_the_selected_slug() -> None:
    store_memories()
    records = list_memories()
    with create_app_session(output=DummyOutput()):
        # "page" narrows the list to the on-call runbook only.
        assert run_keys(build_memory_picker(records), "page\r") == "oncall-runbook"
        # Arrow down moves the selection before Enter, whatever the store order is.
        assert run_keys(build_memory_picker(records), "\x1b[B\r") == records[1].slug


def test_empty_store_prints_hint_without_opening_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("must not open the picker for an empty store")

    monkeypatch.setattr(memory_cmds, "run_command_input", unexpected)
    assert "no memories stored yet" in dispatch("/memory show")


def test_cancellation_displays_nothing_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_memories()

    def collect(app: Any) -> Any:
        return run_keys(app, "eks\x1b")

    monkeypatch.setattr(memory_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        out = dispatch("/memory show")
    assert "prod body" not in out
    assert len(list_memories()) == 2


def test_no_match_keeps_the_picker_open_until_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_memories()

    def collect(app: Any) -> Any:
        # Enter with no candidate is inert; only Esc closes the picker.
        return run_keys(app, "zzz\r\x1b")

    monkeypatch.setattr(memory_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        out = dispatch("/memory show")
    assert "prod body" not in out
    assert "runbook body" not in out


def test_supplied_name_bypasses_the_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    store_memories()

    def unexpected(_app: Any) -> None:
        raise AssertionError("complete invocations must not open the picker")

    monkeypatch.setattr(memory_cmds, "run_command_input", unexpected)
    assert "prod body" in dispatch("/memory show eks-prod-cluster")


def test_headless_and_non_tty_calls_keep_the_usage_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_memories()

    def unexpected(_app: Any) -> None:
        raise AssertionError("must not open interactive input")

    monkeypatch.setattr(memory_cmds, "run_command_input", unexpected)
    assert "usage:" in dispatch("/memory show", tty=False)
    session = Session()
    output = io.StringIO()
    dispatch_slash("/memory show", session, Console(file=output), is_tty=True)
    assert "usage:" in output.getvalue()


@pytest.mark.parametrize("columns,rows", [(100, 30), (40, 12)])
def test_picker_stays_usable_on_narrow_terminals(columns: int, rows: int) -> None:
    store_memories()

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    captured: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_memory_picker(list_memories())
        app.input = pipe

        def capture_and_cancel(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            captured.extend(
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            )
            pipe.send_text("\x1b")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def start() -> None:
            app.create_background_task(stop_if_stalled())
            pipe.send_text("")

        app.after_render += capture_and_cancel
        app.run(pre_run=start)

    assert captured, "Picker did not render before the timeout"
    text = "\n".join(captured)
    assert "eks-prod-cluster" in text
    assert len(captured) <= rows
