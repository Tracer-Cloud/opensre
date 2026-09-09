"""Action tool: count the project files under a directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from tools.system.file_count.count import FileCountError, FileTally, count_matching_files

TOOL_NAME = "count_files"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to count in, relative to the working directory.",
        },
        "pattern": {
            "type": "string",
            "description": (
                "File-name glob such as `test_*.py` or `*.yml`. Narrow it to what was "
                "asked; the default `*` counts every file."
            ),
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _summary(tally: FileTally) -> str:
    """One sentence naming the count and the glob that produced it.

    The glob is fenced: a pattern like ``*test*`` is otherwise read as markdown
    emphasis and reaches the reader as ``test``, so the sentence would name a
    filter that was never used.
    """
    described = "files" if tally.pattern == "*" else f"files matching `{tally.pattern}`"
    if tally.count == 1:
        described = described.replace("files", "file", 1)
    return f"{tally.count} {described} under {tally.root}, not counting generated directories"


@tool(
    name=TOOL_NAME,
    source="system",
    display_name="Count files",
    description=(
        "Count the files under a directory whose names match a glob, skipping "
        "generated directories (`__pycache__`, `.venv`, `node_modules`, `build`, "
        "`dist`, caches, `.git`). Use this for 'how many … files' questions instead "
        "of `find`, which counts compiled caches as project files. The count covers "
        "the whole tree, never a sample, and the path must be inside the working "
        "directory. Read-only."
    ),
    use_cases=[
        "How many test files are under tests/?",
        "How many Python files does this package have?",
        "How many workflow files are in .github/workflows?",
    ],
    anti_examples=[
        "Counting entries inside a YAML, JSON or TOML file (use read_structured_file)",
        "Searching file contents (use shell_run with rg)",
        "Listing every file name (this returns a count and a few examples)",
    ],
    outputs={
        "count": "How many files matched, across the whole tree",
        "names": "Up to twenty matching paths, as examples",
        "response_text": "One sentence naming the count, the glob and the directory",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=True,
    input_schema=_INPUT_SCHEMA,
    tags=("safe",),
)
def count_files(path: str, pattern: str = "*", **_kwargs: Any) -> dict[str, Any]:
    """Count files under ``path`` matching ``pattern``, skipping generated output."""
    target = (path or "").strip()
    if not target:
        return {"ok": False, "error": "path is required."}
    try:
        tally = count_matching_files(Path(target), pattern or "*")
    except FileCountError as exc:
        return {"ok": False, "path": target, "error": str(exc)}
    summary = _summary(tally)
    return {
        "ok": True,
        "path": tally.root,
        "pattern": tally.pattern,
        "count": tally.count,
        "names": list(tally.names),
        "skipped_directories": tally.skipped_directories,
        "summary": summary,
        "response_text": summary,
    }


__all__ = ["TOOL_NAME", "count_files"]
