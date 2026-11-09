"""Cursor coding-agent backend — agentic ``agent --print`` with workspace writes."""

from __future__ import annotations

from integrations.coding_agent.backend_exec import (
    failure,
    resolve_workspace_dir,
    run_agentic_cli,
    workspace_error,
)
from integrations.coding_agent.models import CodingResult, Progress
from integrations.llm_cli.agent_exec import build_guarded_task_prompt
from integrations.llm_cli.cursor import CursorAdapter
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env


def run(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
    on_progress: Progress | None = None,
) -> CodingResult:
    """Run Cursor Agent over *workspace* and capture the resulting diff."""
    _ = on_progress  # this backend does not stream its steps
    ws = resolve_workspace_dir(workspace)
    ws_error = workspace_error(ws)
    if ws_error:
        return failure(ws_error)

    prompt = build_guarded_task_prompt(task, agent_label="the Cursor coding agent")
    try:
        invocation = CursorAdapter().build(prompt=prompt, model=model, workspace=ws)
    except RuntimeError as exc:
        return failure(str(exc))

    return run_agentic_cli(
        list(invocation.argv),
        workspace=ws,
        env=build_cli_subprocess_env(invocation.env or {}),
        timeout_sec=timeout_sec,
        agent_name="cursor",
        stdin=invocation.stdin,
    )


def verify() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the Cursor coding backend."""
    probe = CursorAdapter().detect()
    if not probe.installed:
        return False, probe.detail
    if probe.logged_in is False:
        return False, probe.detail
    return True, probe.detail


__all__ = ["run", "verify"]
