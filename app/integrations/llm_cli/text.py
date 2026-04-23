"""Flatten chat-style messages into a single prompt string for CLI stdin."""

from __future__ import annotations

from typing import Any


def flatten_messages_to_prompt(prompt_or_messages: Any) -> str:
    """Turn LangChain-style messages or a plain string into one text block."""
    if isinstance(prompt_or_messages, str):
        return prompt_or_messages

    if not isinstance(prompt_or_messages, list):
        return str(prompt_or_messages)

    parts: list[str] = []
    for msg in prompt_or_messages:
        if not isinstance(msg, dict):
            parts.append(str(msg))
            continue
        role = str(msg.get("role", "user")).strip().lower() or "user"
        content = msg.get("content", "")
        if isinstance(content, list):
            text_bits: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_bits.append(str(block.get("text", "")))
                else:
                    text_bits.append(str(block))
            content = "\n".join(text_bits)
        else:
            content = str(content)
        label = role.upper()
        parts.append(f"=== {label} ===\n{content}")

    return "\n\n".join(parts).strip()
