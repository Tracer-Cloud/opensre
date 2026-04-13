"""Tool for running one-off diagnostic Python snippets in a sandbox."""

from __future__ import annotations

from typing import Any

from app.sandbox.runner import DEFAULT_TIMEOUT, MAX_TIMEOUT, run_python_sandbox
from app.tools.tool_decorator import tool


@tool(
    name="run_diagnostic_code",
    source="knowledge",
    description=(
        "Execute a Python snippet in a restricted sandbox for targeted diagnostics. "
        "Network access and filesystem writes outside /tmp are blocked. "
        f"Execution is capped at {MAX_TIMEOUT} seconds. "
        "Use this to compute metrics, parse collected evidence, or run targeted "
        "analysis during investigations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
            "inputs": {
                "type": "object",
                "description": (
                    "Optional key-value pairs injected into the script's global "
                    "scope as the 'inputs' variable."
                ),
                "nullable": True,
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Maximum execution time in seconds "
                    f"(default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})."
                ),
                "nullable": True,
            },
        },
        "required": ["code"],
    },
    use_cases=[
        "parse or transform evidence already collected",
        "compute statistics over collected metrics",
        "run targeted analysis on log patterns",
        "verify a hypothesis with lightweight calculation",
    ],
    outputs={
        "stdout": "Captured standard output from the script",
        "stderr": "Captured standard error output",
        "exit_code": "Process exit code (0 = success)",
        "timed_out": "True if execution exceeded the timeout",
        "success": "True when exit_code is 0 and execution did not time out",
    },
)
def run_diagnostic_code(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Execute a Python snippet in a restricted sandbox for targeted diagnostics."""
    effective_timeout = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)

    result = run_python_sandbox(code=code, inputs=inputs, timeout=effective_timeout)

    output: dict[str, Any] = {
        "source": "knowledge",
        "code": result.code,
        "inputs": result.inputs,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "success": result.success,
    }
    if result.error:
        output["error"] = result.error
    return output
