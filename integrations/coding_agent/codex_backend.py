"""OpenAI Codex coding-agent backend — agentic ``codex exec`` with workspace write.

The ``integrations/llm_cli`` Codex adapter is the "brain" role and runs with a
read-only sandbox. This backend runs the same binary in its "hands" role:
``codex exec`` with the ``workspace-write`` sandbox so it can edit files in the
target checkout non-interactively (network stays disabled by Codex's sandbox
default). The guarded task prompt forbids commits/pushes; branch/commit/PR
mechanics stay with the caller.

Env vars: ``CODEX_BIN`` (optional explicit binary path). A signed-in OpenSRE
account supplies hosted OpenAI credentials through the child environment;
otherwise OpenAI Platform auth env keys are forwarded to the subprocess.
"""

from __future__ import annotations

from contextlib import ExitStack

from config.constants import CODING_AGENT_SANDBOX_HOST, OPENAI_API_KEY_ENV, OPENAI_BASE_URL_ENV
from integrations.coding_agent.backend_exec import (
    failure,
    resolve_workspace_dir,
    run_agentic_cli,
    workspace_error,
)
from integrations.coding_agent.config import coding_agent_sandbox
from integrations.coding_agent.hosted_credentials import hosted_openai_subprocess_env
from integrations.coding_agent.hosted_relay import HostedRouteRelay
from integrations.coding_agent.models import CodingResult, Progress
from integrations.llm_cli.agent_exec import build_guarded_task_prompt
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names,
    default_cli_fallback_paths,
    resolve_cli_binary,
)
from integrations.llm_cli.codex import CodexAdapter, hosted_provider_overrides
from integrations.llm_cli.env_overrides import OPENAI_PLATFORM_ENV_KEYS, nonempty_env_values
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

_INSTALL_HINT = "npm i -g @openai/codex"
_WRITE_SANDBOX = "workspace-write"
#: Codex's own sandbox needs user namespaces; an isolated host that lacks them is the boundary instead.
_HOST_SANDBOX = "danger-full-access"
#: Codex reads no AGENTS.md from the checkout when the checkout must not instruct it.
_NO_PROJECT_DOCS = ("-c", "project_doc_max_bytes=0")


def _sandbox_mode() -> str:
    if coding_agent_sandbox() == CODING_AGENT_SANDBOX_HOST:
        return _HOST_SANDBOX
    return _WRITE_SANDBOX


def _resolve_binary() -> str | None:
    return resolve_cli_binary(
        explicit_env_key="CODEX_BIN",
        binary_names=candidate_binary_names("codex"),
        fallback_paths=lambda: default_cli_fallback_paths("codex"),
    )


def _subprocess_env(hosted: dict[str, str] | None) -> dict[str, str]:
    env: dict[str, str] = {"NO_COLOR": "1"}
    if hosted is not None:
        # Hosted credentials replace local OpenAI keys so a signed-in session
        # cannot silently bill a different provider account.
        env.update(hosted)
    else:
        env.update(nonempty_env_values(OPENAI_PLATFORM_ENV_KEYS))
    return build_cli_subprocess_env(env)


def _hosted_route_via(relay: HostedRouteRelay | None) -> tuple[dict[str, str] | None, str | None]:
    """The hosted env and base URL Codex gets: the relay's when one runs, else the route's."""
    hosted = hosted_openai_subprocess_env()
    if hosted is None:
        return None, None
    if relay is not None:
        return {
            OPENAI_API_KEY_ENV: relay.run_token,
            OPENAI_BASE_URL_ENV: relay.base_url,
        }, relay.base_url
    return hosted, hosted[OPENAI_BASE_URL_ENV]


def _relay_for_host_sandbox() -> HostedRouteRelay | None:
    """Without its own sandbox, Codex must not hold the account token: it gets a relay instead."""
    if _sandbox_mode() != _HOST_SANDBOX:
        return None
    hosted = hosted_openai_subprocess_env()
    if hosted is None:
        return None
    return HostedRouteRelay(hosted[OPENAI_BASE_URL_ENV], hosted[OPENAI_API_KEY_ENV])


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

    relay = _relay_for_host_sandbox()
    with ExitStack() as stack:
        if relay is not None:
            stack.enter_context(relay)
        hosted, base_url = _hosted_route_via(relay)
        argv = _argv(binary, ws, model, task, hosted_base_url=base_url)
        return run_agentic_cli(
            argv,
            workspace=ws,
            env=_subprocess_env(hosted),
            timeout_sec=timeout_sec,
            agent_name="codex",
        )


def _argv(
    binary: str, ws: str, model: str | None, task: str, *, hosted_base_url: str | None
) -> list[str]:
    sandbox = _sandbox_mode()
    argv: list[str] = [binary, "exec", "--ephemeral", "-s", sandbox, "--color", "never", "-C", ws]
    if hosted_base_url is not None:
        argv.extend(hosted_provider_overrides(hosted_base_url))
    host_sandbox = sandbox == _HOST_SANDBOX
    if host_sandbox:
        # The checkout is not trusted to instruct an agent that has the whole process.
        argv.extend(_NO_PROJECT_DOCS)
    resolved_model = (model or "").strip()
    if resolved_model:
        argv.extend(["-m", resolved_model])
    prompt = build_guarded_task_prompt(
        task, agent_label="the Codex coding agent", trust_project_docs=not host_sandbox
    )
    argv.append(prompt)
    return argv


def verify() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the Codex coding backend.

    Mirrors the Pi verifier semantics: available when the binary is installed and
    auth is not explicitly missing (``logged_in`` True or unclear); the actual run
    surfaces a clear error if auth fails.
    """
    probe = CodexAdapter().detect()
    if not probe.installed:
        return False, probe.detail
    if hosted_openai_subprocess_env() is not None:
        return True, "OpenSRE hosted credentials"
    if probe.logged_in is False:
        return False, probe.detail
    return True, probe.detail


__all__ = ["run", "verify"]
