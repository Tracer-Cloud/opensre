"""Pi coding-task tool: hand a coding task to the Pi agent so it implements the change.

Package layout (separation of concerns):

- ``errors.py``      — :class:`PiCodingError` + stable ``error_kind`` constants.
- ``validation.py``  — argument validators + :class:`ResolvedRequest` resolution.
- ``runner.py``      — lifecycle/execution stages (enable gate, CLI readiness,
  run the polled Pi process, shape the result dict).
- ``__init__.py``    — this file: the agent-facing :class:`BaseTool` contract whose
  ``run`` orchestrates those stages. The class lives here (rather than a sister
  module) because the tool registry discovers instances by ``__class__.__module__``
  and does not recurse into sub-modules.

This is the first **mutating** agent-callable tool, so it is deliberately gated,
mirroring how ``execute_python_code`` is gated by availability:

- ``side_effect_level = SideEffectLevel.MUTATING``.
- ``is_available`` returns True only when ``PI_CODING_ENABLED`` is set, so it is
  never offered to the agent unless the operator opts in.
- ``surfaces=(ToolSurface.ACTION,)`` — the surface the REPL action agent consumes.
  Reachability is gated by ``PI_CODING_ENABLED``, not by the surface.

Expected failures return a structured ``{"success": False, "error_kind": ...}`` dict;
any *unexpected* exception propagates to ``BaseTool.__call__``, which reports it to
Sentry. It edits the working tree and returns a summary + git diff; it never commits,
pushes, or opens a PR (see ``integrations/pi``).
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from integrations.pi import is_pi_coding_enabled
from integrations.pi.tools.pi_coding_tool.errors import PiCodingError
from integrations.pi.tools.pi_coding_tool.runner import (
    SOURCE,
    ensure_cli_ready,
    ensure_enabled,
    error_output,
    execute,
    to_output,
)
from integrations.pi.tools.pi_coding_tool.validation import resolve_request


class PiCodingTool(BaseTool):
    """Submit a coding task to the Pi agent; it edits the workspace and returns a diff."""

    name = "pi_coding_task"
    display_name = "Pi coding task"
    source = SOURCE
    side_effect_level = SideEffectLevel.MUTATING
    surfaces = (ToolSurface.ACTION,)
    description = (
        "Submit a coding task to the Pi agent (pi.dev). Pi edits files in the workspace to "
        "implement the change and returns a summary plus the git diff. It does not commit, "
        "push, or open a PR. Disabled unless PI_CODING_ENABLED=1 and the Pi CLI is installed "
        "and authenticated."
    )
    use_cases = [
        "Apply a small, well-scoped fix identified during an investigation",
        "Make a targeted code change in the current repository and return the diff for review",
    ]
    anti_examples = [
        "Reading logs, metrics, or traces (use a read-only evidence tool instead)",
        "Large multi-file refactors that should be reviewed interactively",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Natural-language description of the coding change Pi should make.",
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to the repository to edit. "
                    "Defaults to PI_CODING_WORKSPACE or the current directory."
                ),
                "nullable": True,
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional Pi model override in provider/model form "
                    "(e.g. anthropic/claude-haiku-4-5). Defaults to PI_CODING_MODEL."
                ),
                "nullable": True,
            },
        },
        "required": ["task"],
    }
    outputs = {
        "success": "True when Pi completed and exited cleanly",
        "error_kind": "Stable failure category (disabled, invalid_input, cli_unavailable, "
        "timeout, execution_error) or None on success",
        "summary": "Pi's final message summarizing what it changed",
        "changed_files": "Files modified in the working tree (status porcelain)",
        "diff": "git diff of the changes vs HEAD (truncated if large)",
        "diff_truncated": "True when the diff was truncated",
        "error": "Human-readable error detail when the task failed",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Only available when explicitly opted in (cheap flag check)."""
        return is_pi_coding_enabled()

    def run(
        self,
        task: str,
        workspace: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            ensure_enabled()
            request = resolve_request(task, workspace, model)
            ensure_cli_ready()
        except PiCodingError as exc:
            return error_output(exc.kind, exc.message)

        # Expected execution failures (timeout, provider limit, no-op) come back as a
        # populated PiCodingResult; any *unexpected* exception propagates to
        # BaseTool.__call__, which reports it to Sentry (the global tool wrapper).
        return to_output(execute(request))


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
pi_coding_task = PiCodingTool()
