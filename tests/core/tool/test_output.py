"""Provider-visible output follows Codex's UTF-8 head/tail budget."""

from typing import Any

import pytest

from config.constants.tool_output import OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT_ENV
from core.tool import ToolExecutionResult, history_replay_byte_budget, tool_output_byte_budget


def test_default_history_replay_matches_codex_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT_ENV, raising=False)

    assert history_replay_byte_budget() == 48_000


def test_history_allowance_rounds_up_tokens_before_converting_to_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT_ENV, "10001")

    assert tool_output_byte_budget() == 40_004
    assert tool_output_byte_budget(history=True) == 48_008
    assert history_replay_byte_budget() == 48_008


def test_provider_output_matches_codex_unicode_truncation() -> None:
    raw = "HEADER" + "🙂" * 20_000 + "TAIL"
    result = ToolExecutionResult(content=raw)

    assert result.provider_content() == (
        "HEADER" + "🙂" * 4_998 + "…10003 tokens truncated…" + "🙂" * 4_999 + "TAIL"
    )
    assert result.content == raw


def test_text_blocks_share_one_budget_and_preserve_non_text_content() -> None:
    image = {"type": "image", "source": {"data": "image-data"}}
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "a" * 39_996},
        image,
        {"type": "text", "text": "abcdefgh"},
        {"type": "text", "text": "later evidence"},
    ]

    visible = ToolExecutionResult(content=blocks).provider_content()

    assert visible == [
        {
            "type": "text",
            "text": "a" * 20_000
            + "…5 tokens truncated…"
            + "a" * 19_976
            + "\nabcdefgh\nlater evidence",
        },
        image,
    ]
    assert blocks[2]["text"] == "abcdefgh"
