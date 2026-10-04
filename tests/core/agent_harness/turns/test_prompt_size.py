"""The model's context is measured once: blocks in reading order, the history as compaction sees it."""

from __future__ import annotations

import json

from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.turns.prompt_size import HistorySize, measure_history, measure_prompt
from core.agent_harness.turns.structured_history import (
    build_turn_evidence,
    estimate_history_tokens,
    history_chars,
    history_messages,
)
from core.state.transcript_window import SESSION_SUMMARY_PREFIX

_SECRET_PROMPT_TEXT = "the operator's private runbook"


def test_blocks_are_measured_in_reading_order_and_the_record_carries_no_text() -> None:
    envelope = PromptEnvelope.from_blocks(
        [
            PromptBlock(id="turn-interaction", content="T" * 10, tier=PromptTier.EPHEMERAL),
            PromptBlock(id="empty", content="", tier=PromptTier.CONTEXT),
            PromptBlock(id="base", content=_SECRET_PROMPT_TEXT, tier=PromptTier.STABLE),
            PromptBlock(id="memory", content="M" * 9, tier=PromptTier.VOLATILE),
        ],
        separator="",
    )

    size = measure_prompt(envelope, history=HistorySize(), tool_schema_count=7)

    # Same order render() emits; a block that renders nothing is not part of the call.
    assert [(block.id, block.tier) for block in size.blocks] == [
        ("base", "stable"),
        ("memory", "volatile"),
        ("turn-interaction", "ephemeral"),
    ]
    assert size.chars == len(envelope.render())
    assert [block.tokens for block in size.blocks] == [len(_SECRET_PROMPT_TEXT) // 4, 2, 2]
    record = size.as_record()
    assert record["tool_schema_count"] == 7
    assert record["blocks"]["base"] == {
        "tier": "stable",
        "chars": len(_SECRET_PROMPT_TEXT),
        "tokens": len(_SECRET_PROMPT_TEXT) // 4,
    }
    assert _SECRET_PROMPT_TEXT not in json.dumps(record)


def test_history_size_matches_the_compaction_measure() -> None:
    tool_turn = build_turn_evidence(
        "rerun the failed job",
        "Rerun started.",
        [
            {
                "kind": "assistant",
                "text": "",
                "tool_calls": [{"id": "c1", "name": "shell_run", "input": {"command": "gh"}}],
            },
            {
                "kind": "tool_results",
                "results": [{"id": "c1", "name": "shell_run", "content": "ok"}],
            },
        ],
    )
    plain_turn = build_turn_evidence("thanks", "You're welcome.")
    conversation = [
        ("assistant", f"{SESSION_SUMMARY_PREFIX}CI failed twice on main."),
        ("user", "rerun the failed job"),
        ("assistant", "Rerun started."),
        ("user", "thanks"),
        ("assistant", "You're welcome."),
    ]
    evidence = [tool_turn, plain_turn]
    replayed = history_messages(conversation, evidence)

    size = measure_history(replayed, conversation, evidence)

    assert size.messages == len(replayed)
    assert size.turns == 2
    assert size.tool_turns == 1
    assert size.summarized is True
    # /context compares this estimate with the compaction budget, so it must be
    # the one compaction itself measures.
    assert size.chars == history_chars(conversation, evidence)
    assert size.tokens == estimate_history_tokens(conversation, evidence)
    # Nothing replayed (text-history kill switch): no history in the call.
    assert measure_history([], conversation, evidence) == HistorySize()
