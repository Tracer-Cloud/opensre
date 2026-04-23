"""Shared subprocess executor for `LLMCLIAdapter` implementations."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Any

from pydantic import BaseModel

from app.integrations.llm_cli.base import CLIProbe, LLMCLIAdapter
from app.integrations.llm_cli.text import flatten_messages_to_prompt
from app.services.llm_client import LLMResponse

logger = logging.getLogger(__name__)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# Avoid re-running `detect()` (two subprocess probes) on every invoke during long investigations.
_PROBE_CACHE_TTL_SEC = 45.0


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


class CLIBackedLLMClient:
    """Drives any `LLMCLIAdapter` with a single non-interactive subprocess call per invoke."""

    def __init__(
        self,
        adapter: LLMCLIAdapter,
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        model_type: str = "reasoning",
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._max_tokens = max_tokens
        self._model_type = model_type
        self._cached_probe: CLIProbe | None = None
        self._probe_cached_at: float = 0.0

    def _probe(self) -> CLIProbe:
        now = time.monotonic()
        if self._cached_probe is not None and (now - self._probe_cached_at) < _PROBE_CACHE_TTL_SEC:
            return self._cached_probe
        probe = self._adapter.detect()
        self._cached_probe = probe
        self._probe_cached_at = now
        return probe

    def with_config(self, **_kwargs: Any) -> CLIBackedLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> Any:
        """JSON-schema prompt + parse; same contract as API `StructuredOutputClient`."""
        from app.services.llm_client import StructuredOutputClient

        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list[Any]) -> CLIBackedLLMClient:
        return self

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        # max_tokens / model_type are stored for API parity but ignored here:
        # CLI adapters (e.g. codex exec) do not expose a scriptable token limit.
        _ = self._max_tokens
        _ = self._model_type

        from app.guardrails.engine import GuardrailBlockedError, get_guardrail_engine

        flat = flatten_messages_to_prompt(prompt_or_messages)
        engine = get_guardrail_engine()
        try:
            if engine.is_active:
                flat = engine.apply(flat)
        except GuardrailBlockedError:
            raise

        probe = self._probe()
        if not probe.installed or not probe.bin_path:
            raise RuntimeError(
                f"{self._adapter.name} CLI not found. {self._adapter.install_hint} "
                f"or set {self._adapter.binary_env_key} to the full binary path. "
                f"({probe.detail})"
            )
        if probe.logged_in is False:
            raise RuntimeError(
                f"{self._adapter.name} is not authenticated. {self._adapter.auth_hint} "
                f"({probe.detail})"
            )

        invocation = self._adapter.build(prompt=flat, model=self._model, workspace="")
        merged_env = os.environ.copy()
        if invocation.env:
            merged_env.update(invocation.env)

        try:
            proc = subprocess.run(
                invocation.argv,
                input=invocation.stdin,
                capture_output=True,
                text=True,
                cwd=invocation.cwd,
                env=merged_env,
                timeout=invocation.timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{self._adapter.name} CLI timed out after {invocation.timeout_sec:.0f}s."
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to spawn {self._adapter.name} CLI: {exc}") from exc

        out = _strip_ansi(proc.stdout or "")
        err = _strip_ansi(proc.stderr or "")

        if proc.returncode != 0:
            raise RuntimeError(
                self._adapter.explain_failure(stdout=out, stderr=err, returncode=proc.returncode)
            )

        content = self._adapter.parse(stdout=out, stderr=err, returncode=proc.returncode)
        content = _strip_ansi(content).strip()
        logger.debug(
            "cli_llm_invoke",
            extra={"provider": self._adapter.name, "cli_cost_unknown": True},
        )
        return LLMResponse(content=content)
