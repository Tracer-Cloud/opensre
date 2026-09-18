"""Declarative model guidance that can be attached to registered tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SkillDiagnosticCode = Literal[
    "read_failed",
    "parse_failed",
    "invalid_metadata",
    "unknown_tool",
]


@dataclass(frozen=True)
class SkillDiagnostic:
    """Warning produced while loading declarative tool guidance."""

    type: Literal["warning"]
    code: SkillDiagnosticCode
    message: str
    path: str


@dataclass(frozen=True)
class SkillGuidance:
    """Markdown guidance that applies to a known set of registered tools."""

    name: str
    description: str
    content: str
    file_path: str
    tool_names: tuple[str, ...]
    disable_model_invocation: bool = False


@dataclass(frozen=True)
class SkillGuidanceLoadResult:
    """Result of loading one explicit SKILL.md file."""

    skill: SkillGuidance | None
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


def load_tool_skill_guidance(
    file_path: str | Path,
    *,
    known_tool_names: set[str] | frozenset[str] | None = None,
) -> SkillGuidanceLoadResult:
    """Load one explicit ``SKILL.md`` file.

    Missing files are skipped without diagnostics so optional skill guidance can be
    introduced per tool family without becoming a registry hard dependency.
    """

    path = Path(file_path)
    if not path.exists():
        return SkillGuidanceLoadResult(skill=None)
    if not path.is_file():
        return SkillGuidanceLoadResult(
            skill=None,
            diagnostics=[
                _diagnostic("invalid_metadata", "skill path is not a file", path),
            ],
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return SkillGuidanceLoadResult(
            skill=None,
            diagnostics=[_diagnostic("read_failed", str(exc), path)],
        )

    parsed = _parse_frontmatter(raw, path)
    if parsed.diagnostics:
        return SkillGuidanceLoadResult(skill=None, diagnostics=parsed.diagnostics)

    frontmatter = parsed.frontmatter
    diagnostics = _validate_frontmatter(path, frontmatter, known_tool_names)

    name = _string_value(frontmatter.get("name"))
    description = _string_value(frontmatter.get("description"))
    tool_names = _tool_names(frontmatter.get("tools"))
    if not name or not description or not tool_names:
        return SkillGuidanceLoadResult(skill=None, diagnostics=diagnostics)

    return SkillGuidanceLoadResult(
        skill=SkillGuidance(
            name=name,
            description=description,
            content=parsed.body,
            file_path=str(path),
            tool_names=tool_names,
            disable_model_invocation=_yaml_flag(frontmatter.get("disable-model-invocation")),
        ),
        diagnostics=diagnostics,
    )


def _xml_attr(value: str) -> str:
    """Escape a string for safe use inside an XML double-quoted attribute value."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", "&#13;")
        .replace("\n", "&#10;")
    )


def format_tool_skill_guidance(skill: SkillGuidance) -> str:
    """Format skill guidance for inclusion in model-facing tool descriptions."""

    skill_dir = str(Path(skill.file_path).parent)
    return (
        f'<tool_guidance name="{_xml_attr(skill.name)}" '
        f'description="{_xml_attr(skill.description)}" '
        f'location="{_xml_attr(skill.file_path)}">\n'
        "Already loaded: call the tool, not skill_view.\n"
        f"References are relative to {skill_dir}.\n\n"
        f"{skill.content.strip()}\n"
        "</tool_guidance>"
    )


@dataclass(frozen=True)
class _ParsedFrontmatter:
    frontmatter: dict[str, Any]
    body: str
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


def _parse_frontmatter(content: str, path: Path) -> _ParsedFrontmatter:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return _ParsedFrontmatter(frontmatter={}, body=normalized.strip())

    end_index = normalized.find("\n---", 3)
    if end_index == -1:
        return _ParsedFrontmatter(
            frontmatter={},
            body="",
            diagnostics=[
                _diagnostic("parse_failed", "frontmatter is not closed with ---", path),
            ],
        )

    yaml_content = normalized[4:end_index]
    body = normalized[end_index + 4 :].strip()
    try:
        loaded = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as exc:
        return _ParsedFrontmatter(
            frontmatter={},
            body="",
            diagnostics=[_diagnostic("parse_failed", str(exc), path)],
        )

    if not isinstance(loaded, dict):
        return _ParsedFrontmatter(
            frontmatter={},
            body="",
            diagnostics=[
                _diagnostic("invalid_metadata", "frontmatter must be a mapping", path),
            ],
        )
    return _ParsedFrontmatter(frontmatter=loaded, body=body)


def _validate_frontmatter(
    path: Path,
    frontmatter: dict[str, Any],
    known_tool_names: set[str] | frozenset[str] | None,
) -> list[SkillDiagnostic]:
    diagnostics: list[SkillDiagnostic] = []
    name = _string_value(frontmatter.get("name"))
    description = _string_value(frontmatter.get("description"))
    tool_names = _tool_names(frontmatter.get("tools"))

    if not name:
        diagnostics.append(_diagnostic("invalid_metadata", "name is required", path))
    else:
        if len(name) > MAX_SKILL_NAME_LENGTH:
            diagnostics.append(
                _diagnostic(
                    "invalid_metadata",
                    f"name exceeds {MAX_SKILL_NAME_LENGTH} characters ({len(name)})",
                    path,
                )
            )
        if not _SKILL_NAME_RE.match(name):
            diagnostics.append(
                _diagnostic(
                    "invalid_metadata",
                    "name must be lowercase kebab-case using a-z, 0-9, and hyphens",
                    path,
                )
            )

    if not description:
        diagnostics.append(_diagnostic("invalid_metadata", "description is required", path))
    elif len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        diagnostics.append(
            _diagnostic(
                "invalid_metadata",
                (
                    f"description exceeds {MAX_SKILL_DESCRIPTION_LENGTH} "
                    f"characters ({len(description)})"
                ),
                path,
            )
        )

    if not tool_names:
        diagnostics.append(
            _diagnostic("invalid_metadata", "tools must be a non-empty list of names", path)
        )
    elif known_tool_names is not None:
        for tool_name in tool_names:
            if tool_name not in known_tool_names:
                diagnostics.append(
                    _diagnostic(
                        "unknown_tool",
                        f"tool {tool_name!r} is not registered",
                        path,
                    )
                )

    return diagnostics


def _tool_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return ()
        name = item.strip()
        if not name:
            return ()
        if name not in names:
            names.append(name)
    return tuple(names)


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _yaml_flag(value: Any) -> bool:
    """Treat YAML booleans and the common string spellings as true."""
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return False


def _diagnostic(code: SkillDiagnosticCode, message: str, path: Path) -> SkillDiagnostic:
    return SkillDiagnostic(type="warning", code=code, message=message, path=str(path))


__all__ = [
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_NAME_LENGTH",
    "SkillDiagnostic",
    "SkillDiagnosticCode",
    "SkillGuidance",
    "SkillGuidanceLoadResult",
    "format_tool_skill_guidance",
    "load_tool_skill_guidance",
]
