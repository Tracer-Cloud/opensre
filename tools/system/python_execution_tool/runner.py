"""Execution helpers for the Python execution tool."""

from __future__ import annotations

from typing import Any

from infrastructure.safety.sandbox.runner import DEFAULT_TIMEOUT, MAX_TIMEOUT, run_python_sandbox


def run_python_execution(
    *,
    code: str,
    inputs: dict[str, Any] | None,
    timeout: int | None,
    env: dict[str, str],
    allow_network: bool,
    credentials_available: list[str],
) -> dict[str, Any]:
    """Run generated Python and return a stable, planner-friendly result."""
    effective_timeout = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    secret_values = tuple(value for value in env.values() if value)
    result = run_python_sandbox(
        code=code,
        inputs=inputs,
        timeout=effective_timeout,
        env=env,
        allow_network=allow_network,
    )

    output: dict[str, Any] = {
        "source": "knowledge",
        "code": result.code,
        "inputs": result.inputs,
        "stdout": _redact(result.stdout, secret_values),
        "stderr": _redact(result.stderr, secret_values),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "success": result.success,
        "credentials_available": credentials_available,
        "network_allowed": allow_network,
    }
    if result.error:
        output["error"] = _redact(result.error, secret_values)
    return output


def _redact(text: str, secret_values: tuple[str, ...]) -> str:
    redacted = text
    for secret in secret_values:
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


__all__ = ["run_python_execution"]
