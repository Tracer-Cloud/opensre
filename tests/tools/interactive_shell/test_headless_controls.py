"""Gateway control tools execute through the headless slash ports."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from config.constants.capabilities import SCHEDULER_HOST_CAPABILITY, SCHEDULER_HOST_IN_PROCESS
from core.agent_harness import SessionCore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.tool import AgentToolContext
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports
from tools.interactive_shell.subprocess_presenter import headless_subprocess_presenter_factory


def _run_control(session: SessionCore, name: str, **arguments: Any) -> dict[str, Any]:
    provider = DefaultToolProvider(
        session,
        Console(file=io.StringIO(), force_terminal=False),
        slash_ports_factory=headless_slash_ports,
        subprocess_presenter_factory=headless_subprocess_presenter_factory,
    )
    tools = provider.action_tools(confirm_fn=None, is_tty=False, resolved_integrations={})
    tool = next(tool for tool in tools if tool.name == name)
    context = AgentToolContext(resolved_integrations={}, resources=provider.tool_resources())
    return tool.run(**arguments, context=context)


def test_headless_provider_switch_reports_the_real_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_switch(provider: str, console: Console, **_kwargs: Any) -> bool:
        assert provider == "anthropic"
        console.print("The requested provider is not configured.")
        return False

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.model.command.switch_llm_provider",
        refuse_switch,
    )

    result = _run_control(SessionCore(), "llm_set_provider", target="anthropic")

    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_headless_cancel_signals_the_selected_running_task() -> None:
    session = SessionCore()
    task = session.task_registry.create(TaskKind.CLI_COMMAND)
    task.mark_running()

    result = _run_control(session, "task_cancel", target="task")

    assert result["ok"] is True
    assert task.cancel_requested.is_set()


def test_hosted_loop_start_uses_the_existing_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
    from infrastructure.scheduling.scheduler.reload_signal import consume_scheduler_reload_request
    from infrastructure.scheduling.scheduler.storage.task_store import add_task, get_task
    from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask
    from infrastructure.scheduling.scheduler.types import TaskKind as ScheduledTaskKind

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.OPENSRE_HOME_DIR", tmp_path
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.reload_signal.OPENSRE_HOME_DIR", tmp_path
    )
    started = []

    def start_extra_scheduler() -> int:
        started.append(True)
        return 1

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.loop_scheduler.reload_loop_scheduler",
        start_extra_scheduler,
    )
    task = ScheduledTask(
        id="remote-loop",
        name="remote loop",
        kind=ScheduledTaskKind.MANUAL_LOOP,
        cron="0 8 * * *",
        provider=Provider.INTERACTIVE_SHELL,
        enabled=False,
        params={LOOP_PROMPT_PARAM: "inspect CI"},
    )
    add_task(task)
    session = SessionCore()
    session.available_capabilities[SCHEDULER_HOST_CAPABILITY] = (SCHEDULER_HOST_IN_PROCESS,)

    result = _run_control(session, "slash_invoke", command="/loops", args=["start", task.id])

    assert result["ok"] is True
    enabled = get_task(task.id)
    assert enabled is not None and enabled.enabled
    assert not started
    assert consume_scheduler_reload_request()


def test_headless_cli_returns_real_output_and_exit_status() -> None:
    result = _run_control(SessionCore(), "cli_exec", payload="--version")

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "opensre" in result["stdout"].lower()


def test_headless_cli_runs_default_background_commands_and_reports_failure() -> None:
    result = _run_control(SessionCore(), "cli_exec", payload="missing-validation-command")

    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "No such command" in result["stderr"]
