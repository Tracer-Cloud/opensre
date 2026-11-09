"""Shared guardrail application helpers for all LLM call sites."""

from __future__ import annotations

from typing import Any


def apply_guardrails_to_messages(
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Apply active guardrails to a standard message list and optional system prompt.

    Only applies to messages whose content is a non-empty string; non-string
    content (e.g. multimodal blocks) is passed through unchanged.
    Returns inputs unchanged when the engine is inactive.
    """
    from infrastructure.safety.guardrails.evaluator import get_guardrail_evaluator

    evaluator = get_guardrail_evaluator()
    if not evaluator.is_active:
        return messages, system

    guarded: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str) and content:
            msg = {**msg, "content": evaluator.apply(content)}
        guarded.append(msg)

    guarded_system = evaluator.apply(system) if system else system
    return guarded, guarded_system


def apply_guardrails_to_text(text: str) -> str:
    """Apply active guardrails to a plain string.

    Returns text unchanged when the evaluator is inactive.
    """
    from infrastructure.safety.guardrails.evaluator import get_guardrail_evaluator

    evaluator = get_guardrail_evaluator()
    if not evaluator.is_active:
        return text
    return evaluator.apply(text)


def apply_guardrails_to_converse_payload(
    *,
    messages: list[dict[str, Any]],
    system: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Apply active guardrails to Bedrock Converse format messages (string or text-block content).

    Returns inputs unchanged when the evaluator is inactive.
    """
    from infrastructure.safety.guardrails.evaluator import get_guardrail_evaluator

    evaluator = get_guardrail_evaluator()
    if not evaluator.is_active:
        return messages, system

    guarded_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        if isinstance(content, str):
            guarded_messages.append({"role": role, "content": [{"text": evaluator.apply(content)}]})
            continue
        if not isinstance(content, list):
            guarded_messages.append(message)
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                blocks.append(block)
                continue
            if "text" in block and isinstance(block["text"], str):
                blocks.append({"text": evaluator.apply(block["text"])})
            else:
                blocks.append(block)
        guarded_messages.append({"role": role, "content": blocks})

    guarded_system = evaluator.apply(system) if system is not None else None
    return guarded_messages, guarded_system
