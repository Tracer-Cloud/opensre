"""Runtime/session compaction helpers for long REPL conversations.

With structured history (the default) compaction is token-based: at the start of
a turn, when the replayed history (text plus each turn's tool evidence) passes
the budget, a model writes a handoff summary of the older turns and the newest
turns stay verbatim with their evidence. The compaction record keeps both, so a
resumed session restarts from exactly what the live one had. With structured
history switched off, the original character threshold and deterministic
summary apply.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from config.constants.conversation_history import (
    HISTORY_COMPACT_AFTER_TURNS,
    HISTORY_KEEP_MAX_TURNS,
    HISTORY_KEEP_RECENT_TOKENS,
    HISTORY_SUMMARY_MAX_CHARS,
)
from core.agent_harness.turns.structured_history import (
    CHARS_PER_TOKEN,
    history_chars,
)
from core.state import TurnEvidence, match_turn_evidence
from core.state.history_settings import (
    history_token_budget,
    llm_compaction_enabled,
    structured_history_enabled,
)
from core.state.transcript_window import (
    SESSION_SUMMARY_PREFIX,
    SUMMARY_MAX_CHARS,
    format_messages_for_summary,
    is_summary_message,
    merge_summary_texts,
    summary_text,
)
from core.state.turn_evidence import ITEM_ASSISTANT, ITEM_TOOL_RESULTS, cap_text

logger = logging.getLogger(__name__)

DEFAULT_AUTO_COMPACTION_CHARS = 48_000
_KEEP_RECENT_MESSAGES = 8
# Characters of one tool result shown to the summarizer, and of its whole input.
_SUMMARY_INPUT_RESULT_CHARS = 2_000
_SUMMARY_INPUT_MAX_CHARS = 200_000

COMPACTION_PROMPT = """\
You are performing a CONTEXT CHECKPOINT COMPACTION for OpenSRE, an AI site \
reliability engineering agent. Write a handoff summary for another instance of \
the agent that will continue this conversation without seeing the turns below.

Include:
- What the user is trying to achieve, and every constraint or preference they stated.
- The systems, repositories, services, runs and identifiers involved (names, ids, \
URLs, branches, PR numbers, commit SHAs), copied exactly.
- What was tried, what the tools returned, and what was concluded, including \
failures and their causes.
- Decisions made, and anything the user approved or declined.
- Open questions and the next steps that remain.

Be concise and structured: short sections, plain text. Do not invent facts or \
claim verification that did not happen.
"""

#: Asks a model for a handoff summary of the given prompt; returns the text.
Summarizer = Callable[[str], str]


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    before_chars: int
    after_chars: int
    first_kept_entry_id: str


def _message_chars(messages: list[tuple[str, str]]) -> int:
    return sum(len(role) + len(text) + 2 for role, text in messages)


def should_compact(
    session: Any,
    *,
    threshold_chars: int | None = None,
) -> bool:
    # Headless / in-memory sessions do not have a persisted ``session.agent``;
    # they can never grow past a threshold worth compacting, so treat missing
    # attributes as "no compaction needed" rather than raising.
    agent = getattr(session, "agent", None)
    messages = getattr(agent, "messages", None) if agent is not None else None
    if messages is None:
        return False
    if structured_history_enabled() and threshold_chars is None:
        if len(messages) > HISTORY_COMPACT_AFTER_TURNS * 2:
            return True
        evidence = list(getattr(agent, "turn_evidence", None) or ())
        tokens = history_chars(list(messages), evidence) // CHARS_PER_TOKEN
        return tokens > history_token_budget()
    threshold = threshold_chars or _auto_threshold()
    return _message_chars(list(messages)) > threshold


def compact_session_branch(
    session: Any,
    *,
    summary: str | None = None,
    first_kept_entry_id: str = "",
    summarizer: Summarizer | None = None,
    manual: bool = False,
) -> CompactionResult | None:
    """Compact the live session branch and persist a compaction entry.

    With structured history, older turns go to a model-written handoff summary
    (``summarizer``, default: the reasoning-tier model) and the newest turns stay
    verbatim with their evidence; when the model is unavailable the
    deterministic summary is used, so compaction never fails a turn. ``manual``
    (``/compact``) keeps only the newest turn verbatim; automatic compaction
    keeps as many recent turns as fit the keep budget. Without structured
    history the deterministic path below applies unchanged. A leading
    session-summary message is carried through whole and merged, never
    excerpted per line.
    """
    if structured_history_enabled():
        return _compact_structured(
            session,
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            summarizer=summarizer,
            keep_budget_tokens=0 if manual else HISTORY_KEEP_RECENT_TOKENS,
        )

    messages = list(session.agent.messages)
    if len(messages) <= _KEEP_RECENT_MESSAGES:
        return None

    before_chars = _message_chars(messages)
    kept = messages[-_KEEP_RECENT_MESSAGES:]
    compacted = messages[:-_KEEP_RECENT_MESSAGES]
    prior = ""
    if compacted and is_summary_message(compacted[0]):
        prior = summary_text(compacted[0])
        compacted = compacted[1:]
    final_summary = merge_summary_texts(prior, summary or deterministic_summary(compacted))
    session.agent.messages = [("assistant", f"{SESSION_SUMMARY_PREFIX}{final_summary}"), *kept]
    after_chars = _message_chars(list(session.agent.messages))
    session.store.append_compaction(
        session.session_id,
        summary=final_summary,
        first_kept_entry_id=first_kept_entry_id,
        before_chars=before_chars,
        after_chars=after_chars,
        before_tokens=_estimate_tokens(before_chars),
        after_tokens=_estimate_tokens(after_chars),
    )
    return CompactionResult(
        summary=final_summary,
        before_chars=before_chars,
        after_chars=after_chars,
        first_kept_entry_id=first_kept_entry_id,
    )


@dataclass(frozen=True)
class CompactionPreview:
    """What automatic compaction keeps verbatim; its summary exists only once it runs."""

    kept_messages: tuple[tuple[str, str], ...]
    kept_evidence: tuple[TurnEvidence, ...]
    #: Messages the summary replaces, an earlier session summary included.
    summarized_messages: int


def preview_compaction(session: Any) -> CompactionPreview | None:
    """What :func:`auto_compact_if_needed` would keep, without compacting; ``None`` if it would not run."""
    if not should_compact(session):
        return None
    messages = list(session.agent.messages)
    if not structured_history_enabled():
        if len(messages) <= _KEEP_RECENT_MESSAGES:
            return None
        kept = messages[-_KEEP_RECENT_MESSAGES:]
        return CompactionPreview(tuple(kept), (), len(messages) - len(kept))
    evidence = list(getattr(session.agent, "turn_evidence", None) or ())
    _prior, compacted, kept, kept_evidence = _structured_split(
        messages, evidence, keep_budget_tokens=HISTORY_KEEP_RECENT_TOKENS
    )
    if not compacted:
        return None
    return CompactionPreview(tuple(kept), tuple(kept_evidence), len(messages) - len(kept))


def _structured_split(
    messages: list[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
    *,
    keep_budget_tokens: int,
) -> tuple[str, list[tuple[str, str]], list[tuple[str, str]], list[TurnEvidence]]:
    """The earlier summary's text, the turns to summarize, the turns kept and their evidence."""
    prior = ""
    body = messages
    if body and is_summary_message(body[0]):
        prior = summary_text(body[0])
        body = body[1:]
    split = _keep_from(body, evidence, budget_tokens=keep_budget_tokens)
    kept = body[split:]
    matched = match_turn_evidence(kept, evidence)
    return prior, body[:split], kept, [matched[index] for index in sorted(matched)]


def _compact_structured(
    session: Any,
    *,
    summary: str | None,
    first_kept_entry_id: str,
    summarizer: Summarizer | None,
    keep_budget_tokens: int,
) -> CompactionResult | None:
    agent = session.agent
    messages = list(agent.messages)
    evidence = list(getattr(agent, "turn_evidence", None) or ())
    prior, compacted, kept, kept_evidence = _structured_split(
        messages, evidence, keep_budget_tokens=keep_budget_tokens
    )
    if not compacted:
        return None

    before_chars = history_chars(messages, evidence)
    if summary:
        final_summary = merge_summary_texts(
            prior, summary.strip(), max_chars=HISTORY_SUMMARY_MAX_CHARS
        )
    else:
        written = _model_summary(prior, compacted, evidence, summarizer)
        final_summary = (
            cap_text(written, HISTORY_SUMMARY_MAX_CHARS)
            if written
            else merge_summary_texts(
                prior, deterministic_summary(compacted), max_chars=SUMMARY_MAX_CHARS
            )
        )
    agent.messages = [("assistant", f"{SESSION_SUMMARY_PREFIX}{final_summary}"), *kept]
    agent.turn_evidence = kept_evidence
    after_chars = history_chars(list(agent.messages), kept_evidence)
    _persist_compaction(
        session,
        summary=final_summary,
        first_kept_entry_id=first_kept_entry_id,
        before_chars=before_chars,
        after_chars=after_chars,
        replacement_messages=[[role, text] for role, text in kept],
        replacement_evidence=[record.to_json() for record in kept_evidence],
    )
    return CompactionResult(
        summary=final_summary,
        before_chars=before_chars,
        after_chars=after_chars,
        first_kept_entry_id=first_kept_entry_id,
    )


def _persist_compaction(
    session: Any,
    *,
    summary: str,
    first_kept_entry_id: str,
    before_chars: int,
    after_chars: int,
    replacement_messages: list[list[str]],
    replacement_evidence: list[dict[str, Any]],
) -> None:
    """Append the compaction record, with what it kept when the backend stores that."""
    record: dict[str, Any] = {
        "summary": summary,
        "first_kept_entry_id": first_kept_entry_id,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "before_tokens": _estimate_tokens(before_chars),
        "after_tokens": _estimate_tokens(after_chars),
    }
    append = session.store.append_compaction
    try:
        append(
            session.session_id,
            **record,
            replacement_messages=replacement_messages,
            replacement_evidence=replacement_evidence,
        )
    except TypeError:
        # A backend written before replacements existed still gets the summary.
        append(session.session_id, **record)


def _keep_from(
    messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
    *,
    budget_tokens: int,
) -> int:
    """Index where the verbatim tail starts: whole turns, newest first, within budget.

    A turn is a user message with its reply (a lone message counts on its own),
    so no reply is separated from the message it answers. The newest turn
    always stays: it is the exchange the next message most likely refers to.
    At most ``HISTORY_KEEP_MAX_TURNS`` stay, so a long run of short turns still
    gets summarized.
    """
    matched = match_turn_evidence(messages, evidence)
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    used = 0
    kept_turns = 0
    start = len(messages)
    position = len(messages) - 1
    while position >= 0 and kept_turns < HISTORY_KEEP_MAX_TURNS:
        paired = (
            position >= 1
            and messages[position - 1][0] == "user"
            and messages[position][0] == "assistant"
        )
        first = position - 1 if paired else position
        record = matched.get(first) if paired else None
        size = (
            record.char_count()
            if record is not None
            else sum(len(text) for _, text in messages[first : position + 1])
        )
        if start < len(messages) and used + size > budget_chars:
            break
        used += size
        kept_turns += 1
        start = first
        position = first - 1
    return start


def _model_summary(
    prior: str,
    compacted: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
    summarizer: Summarizer | None,
) -> str:
    """A model-written handoff summary, or ``""`` when no model can write one."""
    if summarizer is None:
        if not llm_compaction_enabled():
            return ""
        summarizer = _default_summarizer
    prompt = "\n\n".join(
        part
        for part in (
            COMPACTION_PROMPT,
            f"--- Summary of turns before these ---\n{prior}" if prior else "",
            "--- Turns to summarize (oldest first) ---\n" + _render_turns(compacted, evidence),
        )
        if part
    )
    try:
        return str(summarizer(prompt) or "").strip()
    except Exception:  # noqa: BLE001 - compaction falls back instead of failing the turn
        logger.debug(
            "[compaction] model summary failed; using the deterministic one", exc_info=True
        )
        return ""


def _default_summarizer(prompt: str) -> str:
    from core.llm.factory import LLMRole, get_llm

    result = get_llm(LLMRole.REASONING).invoke(prompt)
    content = getattr(result, "content", result)
    return content if isinstance(content, str) else str(content)


def _render_turns(
    messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> str:
    """The compacted turns as text, tool results bounded, newest kept when over budget."""
    matched = match_turn_evidence(messages, evidence)
    blocks: list[str] = []
    index = 0
    while index < len(messages):
        record = matched.get(index)
        if record is None:
            role, text = messages[index]
            blocks.append(f"{'User' if role == 'user' else 'Assistant'}: {text}")
            index += 1
            continue
        lines = [f"User: {record.user_text}"]
        for item in record.items:
            if item.get("kind") == ITEM_ASSISTANT:
                for call in item.get("tool_calls") or ():
                    lines.append(f"Tool call: {call.get('name')} {call.get('input')}")
            elif item.get("kind") == ITEM_TOOL_RESULTS:
                for result in item.get("results") or ():
                    content = cap_text(
                        str(result.get("content") or ""), _SUMMARY_INPUT_RESULT_CHARS
                    )
                    lines.append(f"Tool result ({result.get('name')}): {content}")
        lines.append(f"Assistant: {record.assistant_text}")
        blocks.append("\n".join(lines))
        index += 2
    rendered = "\n\n".join(blocks)
    if len(rendered) > _SUMMARY_INPUT_MAX_CHARS:
        rendered = rendered[len(rendered) - _SUMMARY_INPUT_MAX_CHARS :]
    return rendered


def auto_compact_if_needed(
    session: Any,
    *,
    threshold_chars: int | None = None,
) -> CompactionResult | None:
    if not should_compact(session, threshold_chars=threshold_chars):
        return None
    return compact_session_branch(session)


def deterministic_summary(messages: list[tuple[str, str]]) -> str:
    if not messages:
        return ""
    first = format_messages_for_summary(messages[:4])
    recent = format_messages_for_summary(messages[-4:]) if len(messages) > 4 else ""
    parts = [
        f"Compacted {len(messages)} earlier conversation messages.",
        "Earlier context:",
        first,
    ]
    if recent:
        parts.extend(["Most recent compacted context:", recent])
    return "\n".join(part for part in parts if part).strip()[:SUMMARY_MAX_CHARS]


def _estimate_tokens(chars: int) -> int:
    return max(1, chars // 4) if chars else 0


def _auto_threshold() -> int:
    raw = os.getenv("OPENSRE_SESSION_COMPACTION_CHARS", "").strip()
    if raw.isdigit():
        return max(1_000, int(raw))
    return DEFAULT_AUTO_COMPACTION_CHARS


__all__ = [
    "COMPACTION_PROMPT",
    "CompactionPreview",
    "CompactionResult",
    "DEFAULT_AUTO_COMPACTION_CHARS",
    "auto_compact_if_needed",
    "compact_session_branch",
    "deterministic_summary",
    "preview_compaction",
    "should_compact",
]
