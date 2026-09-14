"""OpenAI Codex coding-agent backend — agentic ``codex exec`` with workspace write.

The ``integrations/llm_cli`` Codex adapter is the "brain" role and runs with a
read-only sandbox. This backend runs the same binary in its "hands" role:
``codex exec`` with the ``workspace-write`` sandbox so it can edit files in the
target checkout non-interactively (network stays disabled by Codex's sandbox
default). The guarded task prompt forbids commits/pushes; branch/commit/PR
mechanics stay with the caller.

Env vars: ``CODEX_BIN`` (optional explicit binary path); OpenAI Platform auth
env keys are forwarded to the subprocess.
"""

from __future__ import annotations

from integrations.coding_agent.backend_exec import (
    failure,
    resolve_workspace_dir,
    run_agentic_cli,
    workspace_error,
)
from integrations.coding_agent.models import CodingResult, Progress
from integrations.llm_cli.agent_exec import build_guarded_task_prompt
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names,
    default_cli_fallback_paths,
    resolve_cli_binary,
)
from integrations.llm_cli.codex import CodexAdapter
from integrations.llm_cli.env_overrides import OPENAI_PLATFORM_ENV_KEYS, nonempty_env_values
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

_INSTALL_HINT = "npm i -g @openai/codex"
_WRITE_SANDBOX = "workspace-write"


def _resolve_binary() -> str | None:
    return resolve_cli_binary(
        explicit_env_key="CODEX_BIN",
        binary_names=candidate_binary_names("codex"),
        fallback_paths=lambda: default_cli_fallback_paths("codex"),
    )


def _subprocess_env() -> dict[str, str]:
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(OPENAI_PLATFORM_ENV_KEYS))
    return build_cli_subprocess_env(env)


def run(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
    on_progress: Progress | None = None,
) -> CodingResult:
    _ = on_progress  # this backend does not stream its steps
    binary = _resolve_binary()
    if not binary:
        return failure(
            "Codex CLI not found on PATH or known locations. "
            f"Install with: {_INSTALL_HINT} or set CODEX_BIN."
        )

    ws = resolve_workspace_dir(workspace)
    ws_error = workspace_error(ws)
    if ws_error:
        return failure(ws_error)

    argv: list[str] = [
        binary,
        "exec",
        "--ephemeral",
        "-s",
        _WRITE_SANDBOX,
        "--color",
        "never",
        "-C",
        ws,
    ]
    resolved_model = (model or "").strip()
    if resolved_model:
        argv.extend(["-m", resolved_model])
    argv.append(build_guarded_task_prompt(task, agent_label="the Codex coding agent"))

    return run_agentic_cli(
        argv,
        workspace=ws,
        env=_subprocess_env(),
        timeout_sec=timeout_sec,
        agent_name="codex",
    )


def verify() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the Codex coding backend.

    Mirrors the Pi verifier semantics: available when the binary is installed and
    auth is not explicitly missing (``logged_in`` True or unclear); the actual run
    surfaces a clear error if auth fails.
    """
    probe = CodexAdapter().detect()
    if not probe.installed:
        return False, probe.detail
    if probe.logged_in is False:
        return False, probe.detail
    return True, probe.detail


__all__ = ["run", "verify"]
