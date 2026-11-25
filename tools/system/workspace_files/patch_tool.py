"""Action tool: create, edit, rename and delete files of the working directory.

The system prompt tells the model to edit files with ``apply_patch``; without
this tool that name led nowhere, and an edit became a shell heredoc or a
hand-off to a coding agent. A patch is checked whole before any file is
written, stays inside the working directory, cannot reach the files that hold
credentials, and follows the shell's ``/auto`` approval like a shell command.
"""

from __future__ import annotations

from typing import Any

from config.constants.capabilities import SHELL_COMMANDS_CAPABILITY
from config.constants.repl_autonomy import FILE_EDIT_TOOL_TYPE
from core.agent_harness.tools import (
    action_context_from_agent_context,
    capability_available_from_sources,
)
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, ToolExecutionResult
from core.tool_framework import tool
from tools.interactive_shell.shared import allow_tool
from tools.system.workspace_files.access import FileAccessError, resolve_writable
from tools.system.workspace_files.patch_format import PatchFormatError, parse_patch
from tools.system.workspace_files.patching import (
    PatchApplyError,
    PatchPlan,
    plan_patch,
    write_plan,
)
from tools.system.workspace_paths import workspace_relative

#: Lines of the patch the approval prompt shows under the list of files.
_APPROVAL_PATCH_LINES = 60
_CHANGE_WORDS = {"A": "add", "M": "edit", "D": "delete"}

_FORMAT = (
    "`*** Begin Patch`, then one section per file, then `*** End Patch`. A section is "
    "`*** Add File: path` followed by the file's lines, each starting with `+`; "
    "`*** Delete File: path`; or `*** Update File: path`, optionally followed by "
    "`*** Move to: new/path`, then hunks. A hunk starts with `@@` (optionally "
    "`@@ class Name` or `@@ def name():` to say where it is) and holds lines starting "
    "with a space (unchanged context), `-` (removed) or `+` (added)."
)


def _edits_local_files(sources: dict[str, dict[str, Any]]) -> bool:
    """A host that offers no commands on its machine offers no edits of it either."""
    return capability_available_from_sources(sources, SHELL_COMMANDS_CAPABILITY)


def _refused(message: str, **fields: Any) -> dict[str, Any]:
    return {"ok": False, **fields, "error": message}


def _totals(plan: PatchPlan) -> str:
    return f"+{plan.added} \N{MINUS SIGN}{plan.removed}"


def _approval_summary(plan: PatchPlan, patch: str) -> str:
    """What the user is asked to allow: the files, then the patch itself."""
    files = "\n".join(f"{_CHANGE_WORDS[change.kind]} {change.path}" for change in plan.changes)
    lines = patch.strip().splitlines()
    shown = lines[:_APPROVAL_PATCH_LINES]
    if len(lines) > len(shown):
        shown.append(f"… {len(lines) - len(shown)} more lines")
    return f"apply_patch ({_totals(plan)})\n{files}\n\n" + "\n".join(shown)


def _user_allows(context: Any, plan: PatchPlan, patch: str) -> bool:
    """Ask the shell's execution policy; a surface with no policy to consult allows.

    Such a surface gates the call before it runs: ``opensre ask`` denies a
    mutating tool it was not told to allow.
    """
    if context is None:
        return True
    try:
        scope = action_context_from_agent_context(context)
    except RuntimeError:
        return True
    presenter = getattr(scope, "subprocess_presenter", None)
    if presenter is None:
        return True
    return bool(
        presenter.execution_allowed(
            allow_tool(FILE_EDIT_TOOL_TYPE), action_summary=_approval_summary(plan, patch)
        )
    )


@tool(
    name="apply_patch",
    source="system",
    display_name="Edit files",
    description=(
        "Create, edit, rename or delete text files in the working directory with one "
        f"patch. The patch is {_FORMAT} Paths are relative to the working directory. Show "
        "three lines of context above and below each change, copied exactly from the "
        "file; a hunk applies at the first place its lines occur after the previous "
        "hunk, so add an `@@` line when the context alone is not unique. A hunk with "
        "only `+` lines appends to the file, or goes right after its `@@` line. One "
        "patch may change several files and is applied whole or not at all. Read a "
        "file with read_file before changing it. Files that hold credentials and "
        "anything under `.git` cannot be changed. Follows the /auto approval level. "
        "Use it instead of writing files through shell_run."
    ),
    compact_description=(
        "Create, edit, rename or delete text files in the working directory with one "
        f"patch: {_FORMAT} Paths are relative; copy context lines exactly from the file."
    ),
    use_cases=[
        "Change code, configuration or documentation in the repository",
        "Create a new file, such as a test or a script",
        "Rename or delete a file",
    ],
    anti_examples=[
        "Reading a file (use read_file)",
        "Changing a file outside the working directory (use shell_run)",
        "Running a formatter or a code generator (use shell_run)",
    ],
    outputs={
        "files": "Each changed path with A (added), M (modified) or D (deleted)",
        "added_lines": "Lines the patch added",
        "removed_lines": "Lines the patch removed",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    is_available=_edits_local_files,
    input_schema={
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": (
                    "The whole patch, from `*** Begin Patch` to `*** End Patch`. Example:\n"
                    "*** Begin Patch\n"
                    "*** Update File: src/app.py\n"
                    "@@ def greet():\n"
                    '-    print("Hi")\n'
                    '+    print("Hello")\n'
                    "*** End Patch"
                ),
            },
        },
        "required": ["input"],
        "additionalProperties": False,
    },
)
def apply_patch(
    input: str,  # The argument name models are trained to send.
    context: Any = None,
    **_kwargs: Any,
) -> ToolExecutionResult | dict[str, Any]:
    """Apply the patch in ``input`` to the working directory, whole or not at all."""
    patch = input or ""
    if not patch.strip():
        return _refused("input is required: the patch, from *** Begin Patch to *** End Patch.")
    try:
        plan = plan_patch(parse_patch(patch), resolve=resolve_writable, shown=workspace_relative)
    except (PatchFormatError, PatchApplyError, FileAccessError) as exc:
        return _refused(f"{exc} Nothing was changed.")
    if not plan.changes:
        return _refused("The patch leaves every file as it already is. Nothing was changed.")
    if not _user_allows(context, plan, patch):
        return _refused(
            "The user declined this patch. Nothing was changed; do not send it again, "
            "ask what they want instead.",
            declined=True,
        )
    try:
        write_plan(plan)
    except PatchApplyError as exc:
        return _refused(str(exc))
    files = [{"path": change.path, "change": change.kind} for change in plan.changes]
    paths = ", ".join(change.path for change in plan.changes)
    count = len(plan.changes)
    return ToolExecutionResult(
        # The wording models are trained to read back from this tool.
        content="Success. Updated the following files:\n"
        + "\n".join(f"{change.kind} {change.path}" for change in plan.changes),
        details={
            "ok": True,
            "files": files,
            "added_lines": plan.added,
            "removed_lines": plan.removed,
            "summary": (
                f"Changed {count} {'file' if count == 1 else 'files'} ({_totals(plan)}): {paths}"
            ),
        },
        provider_output_bounded=True,
    )


__all__ = ["apply_patch"]
