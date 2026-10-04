"""What a model call carries, measured: each prompt block, the replayed history, the tools.

One measurement serves two readers. The prompt log records it for every turn
that calls the model (``model_blocks``), and ``/context`` shows it for the next
turn without calling one. Sizes are characters plus an estimate at
:data:`~core.agent_harness.turns.structured_history.CHARS_PER_TOKEN` characters
per token, the estimate history compaction measures against its budget.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from core.agent_harness.prompts.kernel.envelope import PromptBlockId, PromptEnvelope, PromptTier
from core.agent_harness.turns.structured_history import (
    CHARS_PER_TOKEN,
    history_chars,
    history_messages,
)
from core.agent_harness.turns.transcript_compaction import preview_compaction
from core.state import TurnEvidence, match_turn_evidence
from core.state.history_settings import structured_history_enabled
from core.state.transcript_window import is_summary_message

if TYPE_CHECKING:
    from core.agent_harness.ports import SessionState
    from infrastructure.analytics.provider import JsonValue

logger = logging.getLogger(__name__)

#: Tiers in the order the envelope renders them (the enum's declaration order).
_TIER_RANK = {tier: rank for rank, tier in enumerate(PromptTier)}


def _tokens(chars: int) -> int:
    return chars // CHARS_PER_TOKEN


@dataclass(frozen=True)
class BlockSize:
    """One prompt block the model reads: its id, tier, and length."""

    id: str
    tier: str
    chars: int

    @property
    def tokens(self) -> int:
        return _tokens(self.chars)


@dataclass(frozen=True)
class HistorySize:
    """The earlier turns replayed as messages ahead of the new one."""

    messages: int = 0
    chars: int = 0
    #: Turns the replay covers: one per user message of the transcript.
    turns: int = 0
    #: Replayed turns that carry their tool calls and results.
    tool_turns: int = 0
    #: A compacted session summary opens the replay.
    summarized: bool = False
    #: Messages a compaction before the call folds into a summary that is not
    #: written yet; the summary's length is not counted.
    compacted_messages: int = 0

    @property
    def tokens(self) -> int:
        return _tokens(self.chars)


@dataclass(frozen=True)
class PromptSize:
    """A model call's prompt: its blocks in reading order, the history, the tools."""

    blocks: tuple[BlockSize, ...]
    history: HistorySize
    #: Tool schemas sent with the call; ``None`` when they could not be counted.
    tool_schema_count: int | None = None
    #: The user's own message, sent ahead of the ephemeral blocks; 0 in a preview.
    request_chars: int = 0

    @property
    def chars(self) -> int:
        """Characters of every block, the history and the request; tool schemas are not counted."""
        return sum(block.chars for block in self.blocks) + self.history.chars + self.request_chars

    @property
    def tokens(self) -> int:
        return _tokens(self.chars)

    @property
    def carries_repository_instructions(self) -> bool:
        """Whether an active repository's AGENTS.md block (or why it is absent) is in the call."""
        return any(block.id == PromptBlockId.REPOSITORY_INSTRUCTIONS for block in self.blocks)

    def as_record(self) -> dict[str, JsonValue]:
        """The prompt log's ``model_blocks`` field: sizes and block ids, never prompt text."""
        record: dict[str, JsonValue] = {
            "blocks": {
                block.id: {"tier": block.tier, "chars": block.chars, "tokens": block.tokens}
                for block in self.blocks
            },
            "history": {
                "messages": self.history.messages,
                "chars": self.history.chars,
                "tokens": self.history.tokens,
                "turns": self.history.turns,
                "tool_turns": self.history.tool_turns,
            },
            "request": {"chars": self.request_chars, "tokens": _tokens(self.request_chars)},
            "total": {"chars": self.chars, "tokens": self.tokens},
        }
        if self.tool_schema_count is not None:
            record["tool_schema_count"] = self.tool_schema_count
        return record


def measure_history(
    replayed: Sequence[Any],
    conversation_messages: Sequence[tuple[str, str]],
    turn_evidence: Sequence[TurnEvidence],
) -> HistorySize:
    """Size of ``replayed``, the messages built from this transcript and its evidence.

    Characters are counted the way compaction counts them
    (:func:`~core.agent_harness.turns.structured_history.history_chars`), so the
    estimate compares directly with the history token budget.
    """
    if not replayed:
        return HistorySize()
    matched = match_turn_evidence(conversation_messages, turn_evidence)
    return HistorySize(
        messages=len(replayed),
        chars=history_chars(conversation_messages, turn_evidence),
        turns=sum(1 for role, _text in conversation_messages if role == "user"),
        tool_turns=sum(1 for record in matched.values() if record.has_tool_activity),
        summarized=bool(conversation_messages) and is_summary_message(conversation_messages[0]),
    )


def measure_prompt(
    envelope: PromptEnvelope,
    *,
    history: HistorySize,
    tool_schema_count: int | None = None,
    request_chars: int = 0,
) -> PromptSize:
    """Size every block of ``envelope`` the model reads, in the order it reads them."""
    ordered = sorted(envelope.blocks, key=lambda block: _TIER_RANK[block.tier])
    blocks = tuple(
        BlockSize(id=str(block.id), tier=str(block.tier), chars=len(text))
        for block in ordered
        if (text := block.render())
    )
    return PromptSize(
        blocks=blocks,
        history=history,
        tool_schema_count=tool_schema_count,
        request_chars=request_chars,
    )


def measure_next_prompt(session: SessionState, *, surface: str) -> PromptSize:
    """What the next model call on ``session`` would carry, measured without making it.

    The snapshot and envelope are built as a turn builds them, for an empty
    message, and the session is left as it was: a pending ``/resume`` recovery
    note is read, not consumed. Repositories are found as the turn finds them,
    from cached integrations only; one the next message names is not known yet.
    When the next turn compacts first, the history is what compaction keeps;
    the summary it writes is not counted.
    """
    from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
    from core.agent_harness.turns.turn_plan import preview_repositories
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    snapshot = preview_repositories(
        TurnSnapshot.from_session("", session, surface=surface, consume_recovery_note=False),
        session,
    )
    messages: Sequence[tuple[str, str]] = snapshot.conversation_messages
    evidence: Sequence[TurnEvidence] = snapshot.turn_evidence
    preview = preview_compaction(session)
    if preview is not None:
        messages, evidence = preview.kept_messages, preview.kept_evidence
    replayed = history_messages(messages, evidence) if structured_history_enabled() else []
    history = measure_history(replayed, messages, evidence)
    if preview is not None:
        history = replace(history, compacted_messages=preview.summarized_messages)
    return measure_prompt(
        build_action_system_prompt_envelope(snapshot),
        history=history,
        tool_schema_count=_default_tool_count(session),
    )


def _default_tool_count(session: SessionState) -> int | None:
    """Tools the default provider would offer the next turn, or ``None`` when unknown.

    Counted over the session's cached integrations only: resolving them here
    could reach the network, and before the first resolve a count would guess.
    """
    resolved = session.resolved_integrations_cache
    if resolved is None:
        return None
    from core.agent_harness.tools.tool_provider import DefaultToolProvider

    try:
        tools = DefaultToolProvider(session, None).action_tools(
            confirm_fn=None, is_tty=None, resolved_integrations=dict(resolved)
        )
    except Exception:  # noqa: BLE001 - a preview must not fail on one count
        logger.debug("tool count unavailable for the prompt preview", exc_info=True)
        return None
    return len(tools)


__all__ = [
    "BlockSize",
    "HistorySize",
    "PromptSize",
    "measure_history",
    "measure_next_prompt",
    "measure_prompt",
]
