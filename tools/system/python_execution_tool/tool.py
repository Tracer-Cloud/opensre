"""Agent-facing Python execution tool."""

from __future__ import annotations

import os
from typing import Any

from config.constants.tooling import OPENSRE_PYTHON_EXECUTION_ENABLED_ENV
from config.runtime_metadata import (
    BLOCKED_INTROSPECTION_COMMANDS,
    LIVE_FACT_KEYS,
    STATIC_FACT_KEYS,
)
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from infrastructure.observability.trace.spans import component_span
from infrastructure.safety.sandbox import python_interpreter_available
from tools.system.python_execution_tool._evidence import map_execute_python_code
from tools.system.python_execution_tool.credentials import execution_env, github_extract_params
from tools.system.python_execution_tool.runner import run_python_execution

_RUNTIME_FACT_KEYS = ", ".join((*STATIC_FACT_KEYS, *LIVE_FACT_KEYS))
_NEVER_RUN = ", ".join(f"`{command}`" for command in BLOCKED_INTROSPECTION_COMMANDS)


_RUNTIME_FACTS_ANTI_EXAMPLE = (
    "Calling this tool only to read inputs['opensre_runtime'] — runtime facts "
    "(version, PID, host, cloud, disk, memory, tools) are already in the environment block"
)


class PythonExecutionTool(BaseTool):
    """Run generated Python code with structured inputs and approved credentials."""

    name = "execute_python_code"
    display_name = "Python execution"
    source = "knowledge"
    evidence_mapper = map_execute_python_code
    # Generated code executes with the OpenSRE user's host privileges. The
    # Python API patches in the runner cannot reliably contain subprocesses or egress.
    side_effect_level = SideEffectLevel.MUTATING
    requires_approval = True
    approval_reason = (
        "Runs generated Python with this host's privileges. Review the code before approving; "
        "the Python restrictions are not a security isolation boundary."
    )
    surfaces = (ToolSurface.CHAT,)
    injected_params = ["github_token"]
    description = (
        "Execute generated Python code with host privileges after operator opt-in and per-call "
        "approval. Capture stdout, stderr, exceptions, and timeout state. The Python-level "
        "network, filesystem, and subprocess restrictions are not security isolation; "
        "opt in to network access only for approved API-backed analysis. Runtime facts "
        f"({_RUNTIME_FACT_KEYS}) are already stated in the conversation's environment block — "
        "answer them from there directly and never call this tool just to re-read them; code "
        "already running for another reason can reuse them via `inputs['opensre_runtime']` "
        "(injected automatically). For filesystem introspection, use "
        "`pathlib.Path(...).iterdir()` to list directories and "
        "`Path('/etc/hostname').read_text()` for the pod name. "
        f"Never run {_NEVER_RUN}, never probe cloud instance metadata over the network, "
        "and never use any `subprocess` call. When workflow guidance lists skills, "
        "read each skill description and follow the one that matches the user's request."
    )
    use_cases = [
        "Compute metrics or summaries from structured evidence already in context",
        "Run a small API-backed calculation with approved credentials",
        "Parse logs or JSON payloads when a direct tool result needs post-processing",
        "Reuse inputs['opensre_runtime'] inside code that is already computing something else",
        "List scratchpad files with pathlib; reuse injected runtime facts for disk/memory",
    ]
    anti_examples = [
        _RUNTIME_FACTS_ANTI_EXAMPLE,
        "Changing local files or shelling out to other processes",
        f"Calling {', '.join(BLOCKED_INTROSPECTION_COMMANDS)} instead of using runtime facts",
        "Probing cloud instance metadata over the network (use the injected cloud facts)",
        "Using allow_network for arbitrary host/port reachability probes or scanning (allow_network is unrestricted once enabled — only for approved API-backed analysis)",
        "Long-running jobs, crawlers, or broad external scans",
        "Accessing credentials not explicitly provided by configured integrations or env vars",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
            "inputs": {
                "type": "object",
                "description": (
                    "Optional JSON-serializable values injected into the script as the "
                    "`inputs` global. OpenSRE always merges `opensre_runtime` under this "
                    f"key with: {_RUNTIME_FACT_KEYS}. Skipped if you already set the "
                    "`opensre_runtime` key yourself."
                ),
                "nullable": True,
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum execution time in seconds. Defaults to 30 and caps at 60.",
                "nullable": True,
            },
            "allow_network": {
                "type": "boolean",
                "description": (
                    "Allow outbound network calls for approved API-backed analysis. Defaults to "
                    "false; Python-level restrictions are not a security boundary."
                ),
                "nullable": True,
            },
            "github_token": {
                "type": "string",
                "description": "Injected GitHub token. Hidden from the model-facing schema.",
            },
        },
        "required": ["code"],
    }
    outputs = {
        "success": "True when execution completed with exit code 0 and did not time out",
        "stdout": "Captured standard output",
        "stderr": "Captured standard error",
        "exit_code": "Process exit code, or -1 for timeout/runner failures",
        "timed_out": "True when execution exceeded the timeout",
        "error": "Human-readable runner error when available",
        "credentials_available": "Credential labels made available to the subprocess",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Expose host Python execution only after explicit operator opt-in."""
        return (
            os.environ.get(OPENSRE_PYTHON_EXECUTION_ENABLED_ENV) == "1"
            and python_interpreter_available()
        )

    def extract_params(self, sources: dict[str, dict]) -> dict[str, Any]:
        """Inject approved credentials from resolved integration sources."""
        return github_extract_params(sources)

    def run(
        self,
        code: str,
        inputs: dict[str, Any] | None = None,
        timeout: int | None = None,
        allow_network: bool | None = None,
        github_token: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute ``code`` in the sandbox with runtime metadata injected.

        ``runtime_metadata`` — when the caller has a session with a pre-built
        ``runtime_metadata`` dict (e.g. ``SessionCore.runtime_metadata``),
        passing it here avoids re-running ``build_runtime_metadata()`` per tool
        call. When ``None`` (default) the merge helper builds a fresh dict —
        preserving the previous behavior. Callers that hold a session can
        pass ``session.runtime_metadata`` directly.
        """
        if os.environ.get(OPENSRE_PYTHON_EXECUTION_ENABLED_ENV) != "1":
            raise PermissionError(
                f"Python execution is disabled. Set {OPENSRE_PYTHON_EXECUTION_ENABLED_ENV}=1 "
                "in the OpenSRE process to enable it."
            )
        from config.runtime_metadata import merge_runtime_into_inputs

        with component_span("runtime_metadata:sandbox"):
            env, credentials_available = execution_env(github_token=github_token)
            return run_python_execution(
                code=code,
                inputs=merge_runtime_into_inputs(inputs, metadata=runtime_metadata),
                timeout=timeout,
                env=env,
                allow_network=bool(allow_network),
                credentials_available=credentials_available,
            )


execute_python_code = PythonExecutionTool()


__all__ = ["PythonExecutionTool", "execute_python_code"]
