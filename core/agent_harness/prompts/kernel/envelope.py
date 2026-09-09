"""Structured prompt blocks rendered at the provider boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PromptBlockId(StrEnum):
    """Identity of every block an envelope can carry.

    A block id is looked up by callers (``require_block``) and asserted on by
    contract tests, so a literal typo silently becomes a missing block rather
    than an error. Naming them here keeps producer and consumer on one spelling.
    """

    # Action envelope.
    ACTION_SYSTEM_BASE = "action-agent-system-base"
    ACTION_VENDOR_FRAGMENTS = "action-agent-vendor-fragments"
    ACTION_RUNTIME_FACTS = "action-agent-runtime-facts"
    ACTION_SKILLS = "action-agent-skills"
    ACTIVE_SKILL = "active-skill"
    ACTION_SETUP_STATE = "action-agent-setup-state"
    ASK_USER_ANSWERED = "ask-user-answered"
    CURRENT_TASK_PLAN = "current-task-plan"
    TURN_INTERACTION = "turn-interaction"

    # Shared across envelopes.
    CONNECTED_INTEGRATIONS = "connected-integrations"
    REPOSITORY_CONTEXT = "repository-context"
    LONG_TERM_MEMORY = "long-term-memory"
    RECENT_CONVERSATION = "recent-conversation"
    PRIOR_ACTION_FACTS = "prior-action-facts"
    INTERRUPTED_TURN_RECOVERY = "interrupted-turn-recovery"


class PromptBlockKind(StrEnum):
    """What a block is, independent of how often it changes.

    Orthogonal to :class:`PromptTier`: a ``CONVERSATION`` block is ephemeral, a
    ``RULE`` block is usually stable.
    """

    SYSTEM = "system"
    RULE = "rule"
    CONTEXT = "context"
    CONVERSATION = "conversation"
    TOOL = "tool"
    USER = "user"


class PromptTier(StrEnum):
    """How often a block's text changes, which is what decides cacheability.

    The first three form the cached prefix, joined in declaration order;
    :attr:`EPHEMERAL` must stay outside it or it invalidates everything before
    it on every turn.
    """

    #: Changes on release or config.
    STABLE = "stable"
    #: Changes per project or session.
    CONTEXT = "context"
    #: Changes on an explicit rebuild, such as a memory write.
    VOLATILE = "volatile"
    #: Changes every turn.
    EPHEMERAL = "ephemeral"


_CACHED_TIERS: frozenset[PromptTier] = frozenset(
    {PromptTier.STABLE, PromptTier.CONTEXT, PromptTier.VOLATILE}
)

#: Layer order the prompt is emitted in, so the layout is a property of the
#: envelope rather than of the order a builder happens to append in.
_TIER_ORDER: dict[PromptTier, int] = {tier: index for index, tier in enumerate(PromptTier)}


@dataclass(frozen=True)
class PromptBlock:
    """One model-visible prompt block with optional provenance metadata."""

    id: PromptBlockId | str
    content: str
    kind: PromptBlockKind = PromptBlockKind.CONTEXT
    #: Orthogonal to ``kind``: ``kind`` says what the block is, ``tier`` says
    #: when it changes. A ``conversation`` block is ``ephemeral``; a ``rule`` is
    #: usually ``stable``.
    tier: PromptTier = PromptTier.CONTEXT
    title: str | None = None
    priority: int = 0
    provenance: str | None = None
    token_estimate: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    include_title: bool = False

    def render(self) -> str:
        """Render this block without changing its body text."""
        if not self.content:
            return ""
        if self.include_title and self.title:
            return f"--- {self.title} ---\n{self.content}"
        return self.content


@dataclass(frozen=True)
class PromptEnvelope:
    """Ordered collection of structured prompt blocks.

    The envelope keeps block identity, kind, priority, provenance, and token
    estimates available to tests and future trimming policy while still
    rendering to the same prompt strings current providers accept.
    """

    blocks: tuple[PromptBlock, ...] = ()
    separator: str = "\n\n"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        content: str,
        *,
        block_id: str = "prompt",
        kind: PromptBlockKind = PromptBlockKind.SYSTEM,
        metadata: Mapping[str, Any] | None = None,
    ) -> PromptEnvelope:
        """Wrap an existing string prompt in a single structured block."""
        return cls(
            blocks=(PromptBlock(id=block_id, content=content, kind=kind),),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_blocks(
        cls,
        blocks: Iterable[PromptBlock],
        *,
        separator: str = "\n\n",
        metadata: Mapping[str, Any] | None = None,
    ) -> PromptEnvelope:
        return cls(
            blocks=tuple(blocks),
            separator=separator,
            metadata=dict(metadata or {}),
        )

    def block(self, block_id: str) -> PromptBlock | None:
        """Return the block with ``block_id`` if present."""
        return next((block for block in self.blocks if block.id == block_id), None)

    def require_block(self, block_id: str) -> PromptBlock:
        """Return a block by id, raising a clear error when it is absent."""
        block = self.block(block_id)
        if block is None:
            raise KeyError(f"PromptEnvelope block not found: {block_id}")
        return block

    def _render(self, blocks: Iterable[PromptBlock]) -> str:
        """Render ``blocks`` grouped into layers, stable within each layer."""
        ordered = sorted(blocks, key=lambda block: _TIER_ORDER[block.tier])
        rendered = [block.render() for block in ordered]
        return self.separator.join(text for text in rendered if text)

    def render(self) -> str:
        """Render every non-empty block, layer by layer."""
        return self._render(self.blocks)

    def _partition(self) -> tuple[list[PromptBlock], list[PromptBlock]]:
        """Split the blocks into ``(cached, ephemeral)``, losing none.

        Anything not in :data:`_CACHED_TIERS` is ephemeral, so a tier added
        later stays out of the cached prefix instead of vanishing from both
        halves.
        """
        cached: list[PromptBlock] = []
        ephemeral: list[PromptBlock] = []
        for block in self.blocks:
            target = cached if block.tier in _CACHED_TIERS else ephemeral
            target.append(block)
        return cached, ephemeral

    def render_split(self) -> tuple[str, str]:
        """Return ``(cached, ephemeral)``.

        Joining the two non-empty halves with ``separator`` reproduces
        :meth:`render`, so splitting never changes what the model reads — only
        where it sits. The cached half is what a provider marks as a cache
        breakpoint; the ephemeral half belongs with the user turn.
        """
        cached, ephemeral = self._partition()
        return self._render(cached), self._render(ephemeral)

    def render_cached(self) -> str:
        """Render the blocks that are stable enough to sit behind a cache marker."""
        return self._render(self._partition()[0])

    def render_ephemeral(self) -> str:
        """Render the per-turn blocks that must stay out of the cached prefix."""
        return self._render(self._partition()[1])


__all__ = ["PromptBlock", "PromptBlockId", "PromptBlockKind", "PromptEnvelope", "PromptTier"]
