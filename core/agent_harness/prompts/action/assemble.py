"""Prompt context for the shell action core.agent_harness."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from core.agent_harness.prompts.action.active_skill import active_skill_block
from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.action.turn_interaction import turn_interaction_facts_block
from core.agent_harness.prompts.getting_started import load_getting_started_block
from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.memory.conversation import (
    format_prior_action_facts,
    format_recent_conversation,
)
from core.agent_harness.prompts.runtime_facts import render_static_runtime_facts
from core.agent_harness.prompts.skills.loader import load_skills_index
from core.agent_harness.task_plan.prompt import (
    ask_user_answered_block,
    current_task_plan_block,
)
from infrastructure.harness_providers import action_prompt_vendor_fragments

if TYPE_CHECKING:
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

logger = logging.getLogger(__name__)

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"


def _runtime_facts_block() -> str:
    """The authoritative host/version facts, or ``""`` when they cannot be read.

    Same producer as the assistant block: two speakers may answer the user, and
    facts assembled twice drift. Never raises — a turn without facts is worse
    than one with them, but far better than a turn that does not run.
    """
    from config.runtime_metadata import capture_runtime_facts

    try:
        return render_static_runtime_facts(capture_runtime_facts())
    except Exception:  # noqa: BLE001 - prompt assembly must not fail a turn
        logger.debug("Runtime facts unavailable for the action prompt", exc_info=True)
        return ""


def build_action_system_prompt(turn_snapshot: TurnSnapshot) -> str:
    return build_action_system_prompt_envelope(turn_snapshot).render()


def _optional_block(
    *,
    id: str,  # noqa: A002 - mirrors PromptBlock's field name
    kind: PromptBlockKind,
    tier: PromptTier,
    content: str,
    provenance: str,
    suffix: str = "",
) -> list[PromptBlock]:
    """The block as a one-item list, or empty when there is nothing to say.

    Returning a list lets callers extend unconditionally: seven ``if`` guards
    around ``blocks.append`` is what pushed this module past its complexity
    budget, and each one only asked "is this content non-empty?".
    """
    if not content:
        return []
    return [
        PromptBlock(
            id=id,
            kind=kind,
            tier=tier,
            content="".join((content, suffix)) if suffix else content,
            provenance=provenance,
        )
    ]


def build_action_system_prompt_envelope(turn_snapshot: TurnSnapshot) -> PromptEnvelope:
    blocks = [
        PromptBlock(
            id=PromptBlockId.ACTION_SYSTEM_BASE,
            kind=PromptBlockKind.SYSTEM,
            tier=PromptTier.STABLE,
            # Trailing separators stay in the block; avoid ``base + "\n\n"`` which
            # copies the entire stable prompt body on every turn.
            content="".join((_SYSTEM_PROMPT_BASE, "\n\n")),
            provenance="core.agent_harness.prompts.opensre_system_prompt.md",
        ),
    ]
    vendor_fragments = action_prompt_vendor_fragments()
    blocks.extend(
        _optional_block(
            id=PromptBlockId.ACTION_VENDOR_FRAGMENTS,
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            content=vendor_fragments,
            provenance="infrastructure.harness_providers.action_prompt_vendor_fragments",
            suffix="\n\n",
        )
    )
    facts_block = _runtime_facts_block()
    blocks.extend(
        _optional_block(
            id=PromptBlockId.ACTION_RUNTIME_FACTS,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.STABLE,
            content=facts_block,
            provenance="config.runtime_metadata",
            suffix="\n\n",
        )
    )
    skills_index = "\n\n".join(filter(None, (load_skills_index(), load_getting_started_block())))
    blocks.extend(
        _optional_block(
            id=PromptBlockId.ACTION_SKILLS,
            kind=PromptBlockKind.RULE,
            tier=PromptTier.STABLE,
            content=skills_index,
            provenance="core.agent_harness.prompts.skills",
        )
    )
    if turn_snapshot.setup_state:
        blocks.append(
            PromptBlock(
                id=PromptBlockId.ACTION_SETUP_STATE,
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.CONTEXT,
                content=turn_snapshot.setup_state,
                provenance="infrastructure.setup_state",
            )
        )
    blocks.append(
        PromptBlock(
            id=PromptBlockId.CONNECTED_INTEGRATIONS,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            content=connected_integrations_block(turn_snapshot),
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    blocks.extend(
        _optional_block(
            id=PromptBlockId.REPOSITORY_CONTEXT,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.CONTEXT,
            content=repository_context_block(turn_snapshot),
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    # Volatile before ephemeral so render_cached + render_ephemeral reassemble
    # into render() and the cache breakpoint can sit after memory.
    memory_block = long_term_memory_block()
    blocks.extend(
        _optional_block(
            id=PromptBlockId.LONG_TERM_MEMORY,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.VOLATILE,
            content=memory_block,
            provenance="core.domain.memory",
        )
    )
    blocks.extend(
        _optional_block(
            id=PromptBlockId.ASK_USER_ANSWERED,
            kind=PromptBlockKind.RULE,
            tier=PromptTier.EPHEMERAL,
            content=ask_user_answered_block(
                turn_snapshot.text,
                plan_only=turn_snapshot.plan_only_until_authorized,
            ),
            provenance="core.agent_harness.task_plan.prompt",
            suffix="\n\n",
        )
    )
    blocks.extend(
        _optional_block(
            id=PromptBlockId.ACTIVE_SKILL,
            kind=PromptBlockKind.RULE,
            tier=PromptTier.EPHEMERAL,
            content=active_skill_block(turn_snapshot.active_skill, turn_snapshot.text),
            provenance="core.agent_harness.prompts.action.active_skill",
        )
    )
    blocks.append(
        PromptBlock(
            id=PromptBlockId.TURN_INTERACTION,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.EPHEMERAL,
            content=turn_interaction_facts_block(turn_snapshot),
            provenance="core.agent_harness.prompts.action.turn_interaction",
        )
    )
    blocks.append(
        PromptBlock(
            id=PromptBlockId.RECENT_CONVERSATION,
            kind=PromptBlockKind.CONVERSATION,
            tier=PromptTier.EPHEMERAL,
            content=recent_conversation_block(turn_snapshot),
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    action_facts = prior_action_facts_block(turn_snapshot)
    blocks.extend(
        _optional_block(
            id=PromptBlockId.PRIOR_ACTION_FACTS,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.EPHEMERAL,
            content=action_facts,
            provenance="core.agent_harness.turns.turn_snapshot",
        )
    )
    recovery = interrupted_turn_recovery_block(turn_snapshot)
    if recovery:
        # Ephemeral: the note rides exactly one turn (popped from the session
        # at snapshot time), so the cached prompt half stays byte-identical.
        blocks.append(
            PromptBlock(
                id=PromptBlockId.INTERRUPTED_TURN_RECOVERY,
                kind=PromptBlockKind.CONTEXT,
                tier=PromptTier.EPHEMERAL,
                content=recovery,
                provenance="core.agent_harness.session.persistence.wal_recovery",
            )
        )
    plan_block = current_task_plan_block(
        turn_snapshot.task_plan,
        plan_only=turn_snapshot.plan_only_until_authorized,
    )
    blocks.extend(
        _optional_block(
            id=PromptBlockId.CURRENT_TASK_PLAN,
            kind=PromptBlockKind.CONTEXT,
            tier=PromptTier.EPHEMERAL,
            content=plan_block,
            provenance="core.agent_harness.task_plan.prompt",
            suffix="\n",
        )
    )
    return PromptEnvelope.from_blocks(
        blocks,
        separator="",
        metadata={"prompt": "action_agent_system"},
    )


def connected_integrations_block(turn_snapshot: TurnSnapshot) -> str:
    """Render which integrations are connected for this shell action turn."""
    known = turn_snapshot.configured_integrations_known
    configured = turn_snapshot.configured_integrations
    if known and configured:
        listing = ", ".join(sorted(str(name) for name in configured))
    elif known:
        listing = "none"
    else:
        listing = "unknown"
    gate_note = (
        "Cause/why / figure-out questions → use available chat tools, then answer directly.\n"
    )
    return f"CONNECTED INTEGRATIONS (this install, right now): {listing}\n{gate_note}\n"


def repository_context_block(turn_snapshot: TurnSnapshot) -> str:
    """Render one active repo plus every repo retained in session memory."""
    active = turn_snapshot.active_vcs_repositories
    known = turn_snapshot.known_vcs_repositories
    vendors = sorted(set(active) | set(known))
    if not vendors:
        return ""

    lines: list[str] = []
    for vendor in vendors:
        active_repo = active.get(vendor, "none")
        remembered = known.get(vendor, ())
        remembered_text = ", ".join(remembered) if remembered else "none"
        lines.append(f"- {vendor}: active={active_repo}; remembered={remembered_text}")
    repository_lines = "\n".join(lines)
    return (
        "REPOSITORY CONTEXT (this session; one active target per vendor, many "
        "remembered repositories):\n"
        f"{repository_lines}\n"
        "Use the active target for an unqualified repository request. A user-named "
        "repository becomes active without deleting the others. Do not describe the "
        "active repository as the only repository OpenSRE remembers.\n\n"
    )


def recent_conversation_block(turn_snapshot: TurnSnapshot) -> str:
    # Newest-first: this block rides after the literal user message, and
    # context_budget shrinks with text[:keep]. Chronological (oldest-first)
    # order put the latest turns at the truncated tail — follow-ups then saw
    # only stale chatter. Newest-first drops the oldest turns under pressure.
    history = format_recent_conversation(
        list(turn_snapshot.conversation_messages),
        newest_first=True,
    )
    return (
        "RECENT CONVERSATION (context only, newest first; previous assistant messages "
        "may contain shell stdout, computed values, and prior tool inputs/results. Use "
        "these as facts when resolving follow-up references in the USER MESSAGE above "
        "and when composing later tool inputs. Do NOT re-run turns that already "
        f"completed):\n{history}\n\n"
    )


def prior_action_facts_block(turn_snapshot: TurnSnapshot) -> str:
    facts = format_prior_action_facts(list(turn_snapshot.conversation_messages))
    if not facts:
        return ""
    return (
        "PRIOR ACTION FACTS (newest first; extracted from earlier persisted "
        "assistant/tool outputs; use these values when the USER MESSAGE refers "
        "to previous results, sent messages, comparisons, or 'both/that/them'. "
        "Do NOT ask the user to paste values already listed here):\n"
        f"{facts}\n\n"
    )


def interrupted_turn_recovery_block(turn_snapshot: TurnSnapshot) -> str:
    """Render the WAL recovery note for the first action turn after ``/resume``."""
    note = turn_snapshot.recovery_note
    if not note:
        return ""
    return (
        "INTERRUPTED-TURN RECOVERY (write-ahead log of the resumed session; "
        "applies to THIS turn):\n"
        f"{note}\n\n"
    )


def long_term_memory_block() -> str:
    """Inject stored memory facts into every action-agent turn when available."""
    from core.domain.memory import (
        ensure_memory_store,
        memory_available_here,
        render_prompt_index,
    )

    if not memory_available_here():
        return ""
    ensure_memory_store()
    rendered = render_prompt_index()
    if not rendered:
        return ""
    return (
        "LONG-TERM MEMORY (durable facts from ~/.opensre/memory — injected into "
        "every turn). Use listed facts when planning; when the USER MESSAGE "
        "contains a new useful durable fact, call memory_remember in this turn "
        "even if they never said remember/save — do not wait for special phrasing. "
        "Prefer updating an existing name over near-duplicates. Repository memories "
        "are a collection: keep one stable memory per repository and never overwrite "
        "one repository's facts merely because another repository became active:\n"
        f"{rendered}\n\n"
    )


def build_action_user_message(text: str, *, prefix: str = "") -> str:
    """Wrap the user turn; optional ``prefix`` carries ephemeral system halves.

    ``prefix`` is where per-turn conversation / prior-action-facts land once the
    action driver opts into :meth:`PromptEnvelope.render_cached` — the same text
    the model used to see at the end of ``system``, moved here so the cached
    system prefix stays byte-stable across turns.

    Layout (required by head-preserving truncation ``text[:keep]``):

    1. Literal user message first — never drop the active request.
    2. Ephemeral history next, newest-first — when the combined turn is over
       budget, the tail cut removes the oldest chatter, not the latest turns
       the follow-up is referring to.
    """
    body = _USER_TEMPLATE.format(text=sanitize_action_text(text.strip()))
    if not prefix:
        return body
    # Ephemeral history can be large; join avoids an intermediate f-string copy.
    return "".join((body, "\n", prefix))


def sanitize_action_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]


__all__ = [
    "build_action_system_prompt_envelope",
    "build_action_system_prompt",
    "build_action_user_message",
    "connected_integrations_block",
    "interrupted_turn_recovery_block",
    "long_term_memory_block",
    "prior_action_facts_block",
    "recent_conversation_block",
    "repository_context_block",
    "sanitize_action_text",
]
