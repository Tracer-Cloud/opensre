"""A resent thinking block is never one the API's history-editing check would reject.

The API binds each thinking block to the system prompt, the tools, every earlier
message and the block before it, and answers a request that resends a block
whose conversation changed with a 400 on enforced accounts. The client drops
those blocks itself, mirroring the API's rules, and keeps the rest so an
append-only conversation keeps its cached prefix.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.llm import ANTHROPIC_THINKING_BINDING_KEY
from core.llm.transports.sdk.anthropic_thinking import bind_thinking

_SYSTEM = "You are OpenSRE."
_TOOLS = [{"name": "logs", "description": "Fetch logs", "input_schema": {"type": "object"}}]


def _reply(signature: str, call_id: str) -> list[dict[str, Any]]:
    return [
        {"type": "thinking", "thinking": "", "signature": signature},
        {"type": "tool_use", "id": call_id, "name": "logs", "input": {"service": "api"}},
    ]


def _result(call_id: str, output: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": call_id, "content": output}],
    }


def _answered(history: list[dict[str, Any]], reply: list[dict[str, Any]]) -> dict[str, Any]:
    """The assistant message the client records for ``reply`` to ``history``."""
    _sent, binding = bind_thinking(history, system=_SYSTEM, tools=_TOOLS)
    return {"role": "assistant", "content": reply, ANTHROPIC_THINKING_BINDING_KEY: binding}


def _conversation() -> list[dict[str, Any]]:
    """Two turns of one tool call each, every assistant message recorded as the client does."""
    history: list[dict[str, Any]] = [{"role": "user", "content": "Why is api slow?"}]
    history.append(_answered(history, _reply("sig-1", "toolu_1")))
    history.append(_result("toolu_1", "p99 1.2s"))
    history.append(_answered(history, _reply("sig-2", "toolu_2")))
    history.append(_result("toolu_2", "db pool exhausted"))
    history.append({"role": "assistant", "content": "The db pool is exhausted."})
    history.append({"role": "user", "content": "And now?"})
    history.append(_answered(history, _reply("sig-3", "toolu_3")))
    history.append(_result("toolu_3", "recovered"))
    return history


def _signatures(messages: list[dict[str, Any]]) -> list[str]:
    return [
        block["signature"]
        for message in messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "thinking"
    ]


def _without_thinking(message: dict[str, Any]) -> dict[str, Any]:
    """The message as replayed without its provider payload: same calls, no thinking."""
    content = message["content"]
    if isinstance(content, list):
        content = [block for block in content if block.get("type") != "thinking"]
    return {"role": message["role"], "content": content}


def test_an_append_only_conversation_resends_every_thinking_block_unchanged() -> None:
    history = _conversation()

    sent, _binding = bind_thinking(history, system=_SYSTEM, tools=_TOOLS)

    assert sent == history
    assert _signatures(sent) == ["sig-1", "sig-2", "sig-3"]


@pytest.mark.parametrize(
    ("edit", "kept"),
    [
        ("tool_result_trimmed", ["sig-1"]),
        ("tool_list_grew", []),
        ("system_prompt_changed", []),
    ],
)
def test_an_edit_drops_the_thinking_written_after_it(edit: str, kept: list[str]) -> None:
    history = _conversation()
    system, tools = _SYSTEM, _TOOLS
    if edit == "tool_result_trimmed":
        history[2] = _result("toolu_1", "[output removed to fit the context window]")
    elif edit == "tool_list_grew":
        tools = [*_TOOLS, {"name": "pods", "description": "List pods", "input_schema": {}}]
    else:
        system = f"{_SYSTEM} Be brief."

    sent, _binding = bind_thinking(history, system=system, tools=tools)

    assert _signatures(sent) == kept
    # Everything but the thinking still goes out, so every call keeps its result.
    assert [_without_thinking(m) for m in sent] == [_without_thinking(m) for m in history]


def test_dropping_the_oldest_blocks_keeps_the_later_ones() -> None:
    history = _conversation()
    history[1] = _without_thinking(history[1])

    sent, _binding = bind_thinking(history, system=_SYSTEM, tools=_TOOLS)

    assert _signatures(sent) == ["sig-2", "sig-3"]


def test_a_block_dropped_from_the_middle_drops_the_ones_written_after_it() -> None:
    history = _conversation()
    history[3] = _without_thinking(history[3])

    sent, _binding = bind_thinking(history, system=_SYSTEM, tools=_TOOLS)

    assert _signatures(sent) == ["sig-1"]
