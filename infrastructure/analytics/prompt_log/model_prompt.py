"""Record what the model received on an action turn: prompt text and its block sizes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from infrastructure.analytics.provider import JsonValue


def skill_prompt_from_tool_results(tool_results: Sequence[tuple[Any, Any]]) -> str:
    """The skill text ``skill_view`` returned to the model during this turn."""
    parts: list[str] = []
    for call, execution in tool_results:
        if str(getattr(call, "name", "") or "") != "skill_view":
            continue
        text = _skill_body(execution)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def record_action_model_prompt(result: Any, *, skill: str, context: str) -> None:
    """Store the system prompt, skill body, and ephemeral context on the open recorder."""
    recorder = PromptRecorder.current()
    if recorder is None:
        return
    loaded = skill_prompt_from_tool_results(getattr(result, "tool_results", ()) or ())
    skill_text = "\n\n".join(part for part in (skill.strip(), loaded) if part)
    recorder.set_model_prompt(
        system=str(getattr(result, "final_system_prompt", "") or ""),
        skill=skill_text,
        context=context,
    )


def record_model_blocks(blocks: dict[str, JsonValue]) -> None:
    """Store the size of each part of the turn's prompt on the open recorder."""
    recorder = PromptRecorder.current()
    if recorder is None:
        return
    recorder.set_model_blocks(blocks)


def _skill_body(execution: Any) -> str:
    details = getattr(execution, "details", None)
    if isinstance(details, dict):
        content = details.get("content")
        if isinstance(content, str) and content.strip():
            name = str(details.get("name") or "").strip()
            reference = str(details.get("reference") or "").strip()
            label = " / ".join(part for part in (name, reference) if part)
            body = content.strip()
            return f"{label}\n{body}" if label else body
    content = getattr(execution, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


__all__ = ["record_action_model_prompt", "record_model_blocks", "skill_prompt_from_tool_results"]
