"""A Claude Code implementation run holds one heavy-work slot from spawn until its child is reaped."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable

import pytest

from core.agent_harness.tools import ActionToolScope
from infrastructure.process.turn_capacity import HEAVY_WORK_BUSY_MESSAGE, HeavyWorkGate
from infrastructure.process.turn_capacity import heavy_work as heavy_work_module
from infrastructure.scheduling.task_types import TaskRecord, TaskStatus
from integrations.llm_cli.base import CLIInvocation, CLIProbe
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.implementation import execute_implementation_tool
from tools.interactive_shell.implementation import claude_code_executor
from tools.interactive_shell.subprocess_presenter import HeadlessSubprocessPresenter


class _CountingGate(HeavyWorkGate):
    """A one-slot gate that counts releases; ``BoundedSemaphore`` also raises on an extra one."""

    def __init__(self) -> None:
        super().__init__(1)
        self.releases = 0

    def release(self) -> None:
        self.releases += 1
        super().release()


class _TurnConsole:
    """The turn's console; counts how often the tool asked whether the turn was cancelled."""

    def __init__(self, *, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.reads = 0

    @property
    def cancel_requested(self) -> bool:
        self.reads += 1
        return self.cancelled


class _ClaudeChild:
    """A running Claude Code child that ends when told to exit or when terminated."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self._ended = threading.Event()

    def exit(self, code: int) -> None:
        self.returncode = code
        self._ended.set()

    def terminate(self) -> None:
        self.exit(-signal.SIGTERM)

    def poll(self) -> int | None:
        return self.returncode

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        del input, timeout
        if not self._ended.wait(timeout=5):
            raise RuntimeError("the test never ended the child")
        return "", ""


class _ClaudeAdapter:
    def detect(self) -> CLIProbe:
        return CLIProbe(
            installed=True,
            version="1.0.0",
            logged_in=True,
            bin_path="/usr/local/bin/claude",
            detail="ok",
        )

    def build(
        self,
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> CLIInvocation:
        del model, reasoning_effort
        return CLIInvocation(
            argv=("/usr/local/bin/claude", "-p"),
            stdin=prompt,
            cwd=workspace,
            env=None,
            timeout_sec=60.0,
        )


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> _CountingGate:
    gate = _CountingGate()
    monkeypatch.setattr(heavy_work_module, "process_heavy_work_gate", lambda: gate)
    monkeypatch.setattr(claude_code_executor, "ClaudeCodeAdapter", _ClaudeAdapter)
    return gate


def _implement(session: Session, console: _TurnConsole) -> dict[str, object]:
    scope = ActionToolScope(
        session=session,
        console=console,
        subprocess_presenter=HeadlessSubprocessPresenter(session),
    )
    return execute_implementation_tool({"task": "add a --verbose flag to /tasks"}, scope)


def _no_spawn(_invocation: object) -> _ClaudeChild:
    pytest.fail("spawned Claude Code without a heavy-work slot")


@pytest.mark.parametrize(
    ("cancelled", "wait_seconds"),
    [
        pytest.param(False, 0.0, id="no-slot-frees"),
        pytest.param(True, 30.0, id="turn-cancelled-while-waiting"),
    ],
)
def test_a_full_gate_spawns_nothing_and_reports_busy(
    cancelled: bool, wait_seconds: float, gate: _CountingGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several Claude Code children at once OOM the gateway; a cancelled turn must not sit out the wait."""
    # Arrange: the only slot is held elsewhere.
    monkeypatch.setattr(heavy_work_module, "HEAVY_WORK_WAIT_SECONDS", wait_seconds)
    monkeypatch.setattr(claude_code_executor, "spawn_claude_code", _no_spawn)
    assert gate.try_acquire()
    session = Session()
    console = _TurnConsole(cancelled=cancelled)

    # Act
    result = _implement(session, console)

    # Assert: the tool's ordinary failure with fixed copy, the turn's cancel was consulted,
    # and the slot held elsewhere was not released on its behalf.
    assert result == {"ok": False, "error": HEAVY_WORK_BUSY_MESSAGE}
    assert console.reads > 0
    assert gate.releases == 0
    assert session.task_registry.list_recent(1)[0].status == TaskStatus.FAILED


def _child_exits(child: _ClaudeChild, _task: TaskRecord) -> None:
    child.exit(0)


def _user_cancels(_child: _ClaudeChild, task: TaskRecord) -> None:
    task.request_cancel()


@pytest.mark.parametrize(
    ("end_child", "final_status"),
    [
        pytest.param(_child_exits, TaskStatus.COMPLETED, id="exits"),
        pytest.param(_user_cancels, TaskStatus.CANCELLED, id="cancelled"),
    ],
)
def test_the_slot_is_held_until_the_watcher_reaps_the_child_then_freed_once(
    end_child: Callable[[_ClaudeChild, TaskRecord], None],
    final_status: TaskStatus,
    gate: _CountingGate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Freed at launch, the gate under-counts; freed twice, it over-counts (or raises)."""
    # Arrange
    child = _ClaudeChild()
    monkeypatch.setattr(claude_code_executor, "spawn_claude_code", lambda _invocation: child)
    session = Session()

    # Act: launch, then end the child once the launching call has returned.
    result = _implement(session, _TurnConsole())
    assert result["ok"] is True
    task = session.task_registry.get(str(result["task_id"]))
    assert task is not None
    watcher = next(t for t in threading.enumerate() if t.name == f"claude-code-{task.task_id}")
    held_while_running = not gate.try_acquire()
    end_child(child, task)
    watcher.join(timeout=5)

    # Assert
    assert held_while_running
    assert not watcher.is_alive()
    assert task.status == final_status
    assert gate.releases == 1
    assert gate.try_acquire()


def _spawn_fails(_invocation: object) -> _ClaudeChild:
    raise OSError("exec format error")


def test_a_failed_spawn_frees_its_slot(
    gate: _CountingGate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot leaked here would refuse every later coding agent for the life of the process."""
    monkeypatch.setattr(claude_code_executor, "spawn_claude_code", _spawn_fails)

    result = _implement(Session(), _TurnConsole())

    assert result == {"ok": False, "error": "Claude Code failed to start."}
    assert gate.releases == 1
    assert gate.try_acquire()
