"""Validated declarations for executable helpers bundled with a skill."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.agent_harness.prompts.skills.catalog.schema import parse_frontmatter


class SkillScriptTool(BaseModel):
    """One named tool executing a sibling script with a JSON argument and result."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
    script: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.py$")]
    description: Annotated[str, Field(min_length=1)]
    input_schema: dict[str, Any]

    @model_validator(mode="after")
    def validate_schema(self) -> Self:
        if self.input_schema.get("type") != "object":
            raise ValueError("script tool input_schema must describe an object")
        properties = self.input_schema.get("properties")
        required = self.input_schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or self.input_schema.get("additionalProperties") is not False
        ):
            raise ValueError("script tools require properties and additionalProperties: false")
        if not isinstance(required, list) or any(
            not isinstance(key, str) or key not in properties for key in required
        ):
            raise ValueError("script tool required arguments must name declared properties")
        for key, schema in properties.items():
            if (
                not isinstance(key, str)
                or not key.isidentifier()
                or key.startswith("_")
                or key == "context"
            ):
                raise ValueError("script tool argument names must be public Python identifiers")
            if not isinstance(schema, dict) or schema.get("type") not in (
                "string",
                "integer",
                "number",
                "boolean",
            ):
                raise ValueError("script tool arguments must have scalar JSON types")
        return self

    def resolve(self, skill_path: Path) -> Path:
        """Resolve a regular script strictly inside this bundled skill's directory."""
        directory = skill_path.parent / "scripts"
        path = directory / self.script
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError(f"script_tools: missing or linked script {self.script!r}")
        if path.resolve().parent != directory.resolve():
            raise ValueError("script_tools: script must stay inside its skill")
        return path.resolve()


class _ScriptToolReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    script_tools: list[SkillScriptTool]

    @model_validator(mode="after")
    def distinct_names(self) -> Self:
        if len({tool.name for tool in self.script_tools}) != len(self.script_tools):
            raise ValueError("script tool names must be distinct within a skill")
        return self


def load_script_tools(skill_path: Path, reference: str | None) -> tuple[SkillScriptTool, ...]:
    """Read helper declarations from the skill's own reference Markdown frontmatter."""
    if reference is None:
        return ()
    path = skill_path.parent / reference
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or not path.resolve().is_relative_to(skill_path.parent.resolve())
    ):
        raise ValueError("script_tools: reference must stay inside its skill")
    frontmatter, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
    declarations = _ScriptToolReference.model_validate(frontmatter).script_tools
    for declaration in declarations:
        declaration.resolve(skill_path)
    return tuple(declarations)
