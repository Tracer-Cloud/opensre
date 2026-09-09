"""Action tool: read a structured file and report what one key contains."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from tools.system.structured_file.parse import (
    FORMAT_BY_SUFFIX,
    StructureError,
    StructureView,
    describe,
)

TOOL_NAME = "read_structured_file"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to a YAML, JSON or TOML file, relative to the working directory."
            ),
        },
        "key": {
            "type": "string",
            "description": (
                "Dotted path to the part you are asking about, such as `jobs` or "
                "`project.dependencies`. Omit for the whole document."
            ),
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _summary(view: StructureView) -> str:
    """One sentence a reader can use as the answer, not a dump of the structure."""
    where = f"under `{view.key}` in {view.path}" if view.key else f"at the top level of {view.path}"
    if view.kind == "mapping":
        listed = ", ".join(view.keys)
        tail = f": {listed}" if listed else ""
        entries = "entry" if view.count == 1 else "entries"
        return f"{view.count} {entries} {where}{tail}"
    if view.kind == "list":
        items = "item" if view.count == 1 else "items"
        return f"{view.count} {items} {where}"
    return f"`{view.key or view.path}` holds a single {view.kind} value"


@tool(
    name=TOOL_NAME,
    source="system",
    display_name="Read a structured file",
    description=(
        "Parse a YAML, JSON or TOML file and report what one key holds: its kind, "
        "how many entries it has, and their names. Use this whenever the answer is "
        "a count or a list taken from such a file — how many jobs a workflow "
        "defines, how many dependencies a project declares — instead of matching "
        "indentation in shell output, which also counts nested keys and gives a "
        "different number. Reports structure only — names and counts, never a "
        "value, so a config file's secrets are not echoed; read a value with "
        "shell_run. Read-only."
    ),
    use_cases=[
        "How many jobs does .github/workflows/ci.yml define?",
        "How many dependencies are declared in pyproject.toml?",
        "Which keys are under a section of a YAML or TOML file?",
    ],
    anti_examples=[
        "Reading prose or code (use shell_run)",
        "Counting files in a directory (use shell_run)",
        "Editing a file (this tool never writes)",
    ],
    outputs={
        "count": "Number of entries when the key holds a mapping or a list",
        "keys": "Entry names when the key holds a mapping",
        "response_text": "One sentence naming the count and the entry names",
    },
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=True,
    input_schema=_INPUT_SCHEMA,
    tags=("safe",),
)
def read_structured_file(path: str, key: str = "", **_kwargs: Any) -> dict[str, Any]:
    """Describe what ``key`` holds inside the structured file at ``path``."""
    target = (path or "").strip()
    if not target:
        return {"ok": False, "error": "path is required."}
    try:
        view = describe(Path(target), (key or "").strip())
    except StructureError as exc:
        return {"ok": False, "path": target, "error": str(exc)}
    summary = _summary(view)
    return {
        "ok": True,
        "path": view.path,
        "format": view.file_format,
        "key": view.key,
        "kind": view.kind,
        "count": view.count,
        "keys": list(view.keys),
        "summary": summary,
        "response_text": summary,
    }


__all__ = ["FORMAT_BY_SUFFIX", "TOOL_NAME", "read_structured_file"]
