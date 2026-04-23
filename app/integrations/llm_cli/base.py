"""Shared types for LLM CLI adapters (non-interactive subprocess execution)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

PromptDelivery = Literal["stdin", "arg", "file"]


@dataclass(frozen=True)
class CLIProbe:
    """Result of probing whether a CLI binary is usable (install + auth + version)."""

    installed: bool
    version: str | None
    logged_in: bool | None
    bin_path: str | None
    detail: str


@dataclass(frozen=True)
class CLIInvocation:
    """A single non-interactive subprocess call (no TTY)."""

    argv: tuple[str, ...]
    stdin: str | None
    cwd: str
    env: dict[str, str] | None
    timeout_sec: float


@runtime_checkable
class LLMCLIAdapter(Protocol):
    """Contract for one-shot, non-interactive LLM CLI execution."""

    name: str
    #: Env var for explicit binary path when not on PATH (e.g. ``CODEX_BIN``).
    binary_env_key: str
    install_hint: str
    auth_hint: str
    min_version: str | None
    default_exec_timeout_sec: float
    prompt_delivery: PromptDelivery

    def detect(self) -> CLIProbe:
        """Resolve binary, version, and auth. Never raises; returns a structured probe."""
        pass

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        """Build argv for a non-interactive run (no approval prompts, no TTY)."""
        pass

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        """Extract the model answer from a successful run."""
        pass

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        """Human-readable failure when returncode != 0 or output is unusable."""
        pass
