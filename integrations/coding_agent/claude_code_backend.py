"""Claude Code coding-agent backend — agentic ``claude -p`` with edit permissions.

The ``integrations/llm_cli`` Claude Code adapter is the "brain" role (prompt in,
text out). This backend runs the same binary in its "hands" role: print mode
with ``--permission-mode acceptEdits`` and an explicit tool allowlist so the CLI
can read, edit, and verify code in the workspace non-interactively. The guarded
task prompt forbids commits/pushes; branch/commit/PR mechanics stay with the
caller (e.g. the GitHub security-fix ship step).

Env vars: ``CLAUDE_CODE_BIN`` (optional explicit binary path) and the shared
``CODING_MODEL`` override are honored via the caller; Anthropic auth env keys
are forwarded to the subprocess.
"""

from __future__ import annotations

from dataclasses import replace

from integrations.coding_agent.backend_exec import (
    failure,
    resolve_workspace_dir,
    run_agentic_cli,
    workspace_error,
)
from integrations.coding_agent.claude_stream import ClaudeStreamReader
from integrations.coding_agent.models import CodingResult, Progress
from integrations.llm_cli.agent_exec import build_guarded_task_prompt
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names,
    default_cli_fallback_paths,
    resolve_cli_binary,
)
from integrations.llm_cli.claude_code import ClaudeCodeAdapter
from integrations.llm_cli.env_overrides import ANTHROPIC_CLI_ENV_KEYS, nonempty_env_values
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

_INSTALL_HINT = "npm i -g @anthropic-ai/claude-code"
# Tools the headless run may use without prompting. Deliberately no web access;
# Bash is needed for dependency updates (lockfiles) and focused test runs.
_ALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep"


def _resolve_binary() -> str | None:
    return resolve_cli_binary(
        explicit_env_key="CLAUDE_CODE_BIN",
        binary_names=candidate_binary_names("claude"),
        fallback_paths=lambda: default_cli_fallback_paths("claude"),
    )


def _subprocess_env() -> dict[str, str]:
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(ANTHROPIC_CLI_ENV_KEYS))
    return build_cli_subprocess_env(env)


def run(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
    on_progress: Progress | None = None,
) -> CodingResult:
    """Run Claude Code on *task*; with *on_progress* its steps are streamed as they happen."""
    binary = _resolve_binary()
    if not binary:
        return failure(
            "Claude Code CLI not found on PATH or known locations. "
            f"Install with: {_INSTALL_HINT} or set CLAUDE_CODE_BIN."
        )

    ws = resolve_workspace_dir(workspace)
    ws_error = workspace_error(ws)
    if ws_error:
        return failure(ws_error)

    prompt = build_guarded_task_prompt(task, agent_label="the Claude Code coding agent")
    reader = ClaudeStreamReader(on_progress, workspace=ws) if on_progress else None
    argv: list[str] = [
        binary,
        "-p",
        prompt,
        "--output-format",
        "stream-json" if reader else "text",
        *(("--verbose",) if reader else ()),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        _ALLOWED_TOOLS,
    ]
    resolved_model = (model or "").strip()
    if resolved_model:
        argv.extend(["--model", resolved_model])

    result = run_agentic_cli(
        argv,
        workspace=ws,
        env=_subprocess_env(),
        timeout_sec=timeout_sec,
        agent_name="claude-code",
        on_stdout_line=reader.line if reader else None,
    )
    if reader is not None and reader.result_text:
        # Streamed stdout is JSON events; the agent's own account is the result event.
        return replace(result, summary=reader.result_text)
    return result


def verify() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the Claude Code coding backend.

    Mirrors the Pi verifier semantics: available when the binary is installed and
    auth is not explicitly missing (``logged_in`` True or unclear); the actual run
    surfaces a clear error if auth fails.
    """
    probe = ClaudeCodeAdapter().detect()
    if not probe.installed:
        return False, probe.detail
    if probe.logged_in is False:
        return False, probe.detail
    return True, probe.detail


__all__ = ["run", "verify"]
