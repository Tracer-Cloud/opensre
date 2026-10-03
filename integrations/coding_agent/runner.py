"""Provider-agnostic entry point for running a coding agent over a workspace.

Resolves the configured backend (``CODING_AGENT``, default ``auto``) and
dispatches to it. ``auto`` picks the first *ready* backend in ``_AUTO_ORDER``
(installed and not explicitly unauthenticated), so a machine with Claude Code,
Codex, or Cursor — but not Pi — still gets a working coding agent without
configuration. New backends register in ``_BACKENDS`` and every caller keeps using
:func:`run_coding_task` / :func:`verify_coding_agent` unchanged. Inside
:func:`reuse_coding_agent_choice` the readiness probes run once and the ready
backend they found is reused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from config.account import account_llm_route
from integrations.coding_agent.claude_code_backend import run as _claude_code_run
from integrations.coding_agent.claude_code_backend import verify as _claude_code_verify
from integrations.coding_agent.codex_backend import run as _codex_run
from integrations.coding_agent.codex_backend import verify as _codex_verify
from integrations.coding_agent.config import coding_agent_provider
from integrations.coding_agent.cursor_backend import run as _cursor_run
from integrations.coding_agent.cursor_backend import verify as _cursor_verify
from integrations.coding_agent.hosted_credentials import hosted_openai_subprocess_env
from integrations.coding_agent.models import CodingResult, Progress
from integrations.coding_agent.pi_backend import run as _pi_run
from integrations.coding_agent.pi_backend import verify as _pi_verify

_RunFn = Callable[..., CodingResult]
_VerifyFn = Callable[[], tuple[bool, str]]
_Backend = tuple[_RunFn, _VerifyFn]

AUTO_PROVIDER = "auto"

# provider name -> (run, verify)
_BACKENDS: dict[str, _Backend] = {
    "pi": (_pi_run, _pi_verify),
    "claude-code": (_claude_code_run, _claude_code_verify),
    "codex": (_codex_run, _codex_verify),
    "cursor": (_cursor_run, _cursor_verify),
}
# Detection order for CODING_AGENT=auto: Pi first for backward compatibility with
# existing Pi setups, then the most widely installed general coding CLIs.
_AUTO_ORDER: tuple[str, ...] = ("pi", "claude-code", "codex", "cursor")
# Hosted OpenSRE credentials are OpenAI-compatible. Claude Code / Pi keep using
# a local Anthropic key and would surface that provider's quota as a fake
# OpenSRE credit wall, so auto never falls through to them while signed in.
_HOSTED_AUTO_ORDER: tuple[str, ...] = ("codex",)
_HOSTED_CODEX_HINT = (
    "OpenSRE hosted credits require the Codex CLI for coding-agent work. "
    "Install with: npm i -g @openai/codex"
)


_ALIASES: dict[str, str] = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "claudecode": "claude-code",
}


def _normalize(provider: str | None) -> str:
    name = (provider or coding_agent_provider()).strip().lower()
    return _ALIASES.get(name, name)


def _auto_order() -> tuple[str, ...]:
    """Prefer Codex when a signed-in account can bill the hosted OpenSRE ledger."""
    if hosted_openai_subprocess_env() is None:
        return _AUTO_ORDER
    return _HOSTED_AUTO_ORDER


def _hosted_codex_model() -> str | None:
    """The model the hosted route serves; Codex's own default is not among them."""
    route = account_llm_route()
    if route is None:
        return None
    return route.model


def _codex_model(requested: str | None) -> str | None:
    """Drop Anthropic model names so a hosted Codex run cannot keep that quota."""
    name = (requested or "").strip()
    if not name:
        return None
    lowered = name.lower()
    if "claude" in lowered or "anthropic" in lowered:
        return None
    return name


def _select_auto_backend() -> tuple[str, _Backend, str] | tuple[None, None, str]:
    """First ready backend in ``_AUTO_ORDER`` as ``(name, backend, detail)``.

    When none is ready, ``(None, None, message)`` where the message lists every
    backend's failure detail so the user knows exactly what to install or log into.
    """
    details: list[str] = []
    hosted = hosted_openai_subprocess_env() is not None
    for name in _auto_order():
        backend = _BACKENDS[name]
        _run, verify = backend
        ready, detail = verify()
        if ready:
            return name, backend, detail
        details.append(f"{name}: {detail}")
    if hosted:
        return None, None, _HOSTED_CODEX_HINT
    supported = ", ".join(_AUTO_ORDER)
    return None, None, f"No coding agent is ready (checked {supported}). {'; '.join(details)}"


@dataclass
class _ReadyChoice:
    """The ready backend one :func:`reuse_coding_agent_choice` block resolved."""

    requested: str = ""
    name: str = ""
    detail: str = ""


_CHOICE: ContextVar[_ReadyChoice | None] = ContextVar("coding_agent_choice", default=None)


@contextmanager
def reuse_coding_agent_choice() -> Iterator[None]:
    """Probe for a ready backend at most once inside this block, then reuse it.

    Each readiness check runs the agent CLIs (``pi --version`` alone takes
    seconds) and one CI repair attempt asks several times. Only a ready backend
    is kept, so a check that found none probes again. A nested block shares the
    outer block's choice. A backend that breaks mid-block is not swapped out;
    its run fails and reports why.
    """
    if _CHOICE.get() is not None:
        yield
        return
    token = _CHOICE.set(_ReadyChoice())
    try:
        yield
    finally:
        _CHOICE.reset(token)


def select_coding_agent(provider: str | None = None) -> tuple[str | None, str]:
    """The ready backend a run would use (``auto`` resolved) and its detail (never raises).

    ``(None, detail)`` when it is not ready; the detail says what to install or log into.
    """
    name = _normalize(provider)
    choice = _CHOICE.get()
    if choice is not None and choice.name and choice.requested == name:
        return choice.name, choice.detail
    selected, detail = _probe(name)
    if choice is not None and selected is not None:
        choice.requested, choice.name, choice.detail = name, selected, detail
    return selected, detail


def _probe(name: str) -> tuple[str | None, str]:
    """Run the readiness probes for ``name`` (``auto`` sweeps in order)."""
    if name == AUTO_PROVIDER:
        selected, _backend, detail = _select_auto_backend()
        return selected, detail
    backend = _BACKENDS.get(name)
    if backend is None:
        supported = ", ".join((*sorted(_BACKENDS), AUTO_PROVIDER))
        return None, f"Unsupported coding agent '{name}'. Set CODING_AGENT to one of: {supported}."
    _run, verify = backend
    ready, detail = verify()
    return (name if ready else None), detail


def verify_coding_agent(provider: str | None = None) -> tuple[bool, str]:
    """Whether the configured coding agent is installed/ready (never raises)."""
    auto = _normalize(provider) == AUTO_PROVIDER
    selected, detail = select_coding_agent(provider)
    if selected is None:
        return False, detail
    return True, f"{selected}: {detail}" if auto else detail


def run_coding_task(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
    provider: str | None = None,
    on_progress: Progress | None = None,
) -> CodingResult:
    """Run the configured coding agent on *task* in *workspace*.

    *on_progress* receives one short line per step for backends that can stream
    their activity; others ignore it.
    """
    name = _normalize(provider)
    selected_name = name
    if name == AUTO_PROVIDER:
        selected, detail = select_coding_agent(name)
        if selected is None:
            return CodingResult(success=False, summary="", error=detail)
        selected_name = selected
    backend = _BACKENDS.get(selected_name)
    if backend is None:
        return CodingResult(success=False, summary="", error=f"Unsupported coding agent '{name}'.")
    run, _verify = backend
    resolved_model = model
    if selected_name == "codex" and hosted_openai_subprocess_env() is not None:
        resolved_model = _codex_model(model) or _hosted_codex_model()
    return run(
        task,
        workspace=workspace,
        model=resolved_model,
        timeout_sec=timeout_sec,
        on_progress=on_progress,
    )
