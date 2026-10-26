"""Shared recent-conversation context for interactive-shell prompt builders.

Single source of truth for rendering the recent CLI conversation so the action
planner and the conversational assistant see the same multi-turn history.
"""

from __future__ import annotations

import re

from core.agent_harness.session.pending_offer import (
    DispatchablePendingOffer,
    PendingScheduleOffer,
)
from core.agent_harness.session.want_me_to import offer_from_assistant_content
from core.state import MAX_CONVERSATION_TURNS
from core.state.transcript_window import SESSION_SUMMARY_PREFIX
from infrastructure.harness_providers import strip_message_context_prefix

NO_HISTORY_PLACEHOLDER = "(no prior messages in this CLI thread)"
_ACTION_FACT_MARKERS = (
    " input:",
    " result:",
    "tool:",
    "arguments:",
    "stdout",
    "response_text",
)
_VALUE_LINE_RE = re.compile(
    r"(?im)^[A-Z][A-Za-z0-9 ._/-]{1,64}:\s+.*(?:[-+]?\d+(?:\.\d+)?\s*°?\s*[CF]|sent|true|false|\{|\[)"
)
_AFFIRMATIVES = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "please",
        "go ahead",
        "do it",
        "do that",
    }
)


def expand_affirmative_follow_up(
    text: str,
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None,
    *,
    pending_schedule: PendingScheduleOffer | None = None,
    pending_offer: DispatchablePendingOffer | None = None,
) -> str:
    """Rewrite bare affirmatives into the prior actionable offer.

    Gateway/Slack turns often arrive as ``yes`` / ``sure`` after the assistant
    offered a next step. Without expansion, the action agent treats that as a
    new vague request.

    Structured :class:`~core.agent_harness.session.pending_offer.DispatchablePendingOffer`
    instances (schedule → ``/cron``) expand without scraping Want-me-to prose.
    Pass ``pending_offer`` (preferred) or the typed kwarg.

    Other Want-me-to closers still expand from the newest assistant turn only,
    so an older remediation offer cannot shadow a fresher one.
    """
    raw = text if isinstance(text, str) else ""
    if not raw.strip():
        return raw

    prefix, remainder = strip_message_context_prefix(raw)
    if not (_is_affirmative(remainder) or _is_restated_affirmative(remainder)):
        return raw

    structured = pending_offer or pending_schedule
    if structured is not None:
        # No vendor context prefix (would hide a leading /cron dispatch).
        return structured.to_dispatch_message()

    if not messages:
        return raw

    offer = _latest_actionable_offer(messages)
    if not offer:
        return raw
    return f"{prefix}Yes — please {_normalize_offer(offer)}."


def _is_affirmative(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".!?")
    if cleaned.endswith(" please"):
        cleaned = cleaned[: -len(" please")].rstrip()
    return cleaned in _AFFIRMATIVES


def _is_restated_affirmative(text: str) -> bool:
    """Slack users often restate the offer instead of a bare yes."""
    lowered = text.lower()
    if "i replied yes" in lowered or "as a yes" in lowered:
        return True
    quotes_an_offer = "want me to" in lowered or "you asked" in lowered
    return quotes_an_offer and "yes" in lowered


def _normalize_offer(offer: str) -> str:
    """Collapse dual ``A, or B`` Want-me-to offers into an actionable request."""
    lowered = offer.lower()
    for sep in (", or ", " or "):
        idx = lowered.find(sep)
        if idx < 0:
            continue
        left = offer[:idx].strip(" .?")
        right = offer[idx + len(sep) :].strip(" .?")
        if left and right:
            return f"do both — {left}; and {right}"
    return offer


def _latest_actionable_offer(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> str | None:
    for entry in reversed(messages):
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if role != "assistant" or not isinstance(content, str):
            continue
        # Only the newest assistant turn. Searching further back is what turned
        # a "yes" to a scheduling offer into an unrelated `nvm install 22` from
        # two turns earlier: not expanding is harmless, since the model still
        # reads the conversation, but expanding to a stale offer runs something
        # nobody agreed to.
        if content.startswith(SESSION_SUMMARY_PREFIX):
            return None
        return offer_from_assistant_content(content)
    return None


def format_recent_conversation(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    max_turns: int = MAX_CONVERSATION_TURNS,
    newest_first: bool = False,
) -> str:
    """Render recent CLI-agent turns as ``User:``/``Assistant:`` lines.

    Accepts a list or tuple of ``(role, content)`` pairs (oldest first on input).
    Returns at most ``max_turns`` turns. Default order is chronological (oldest
    first). Pass ``newest_first=True`` for action-agent ephemeral context: the
    user message leads and head-preserving truncation (``text[:keep]``) must
    drop stale turns at the tail, not the latest ones.

    Returns :data:`NO_HISTORY_PLACEHOLDER` when empty so prompt builders
    always have a stable, non-empty block. Never raises.
    """
    cap = max(max_turns, 0) * 2
    if not cap:
        return NO_HISTORY_PLACEHOLDER

    window: list[tuple[str, str]] = []
    for entry in messages[-cap:]:
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if not isinstance(content, str):
            continue
        window.append((str(role), content))
    if not window:
        return NO_HISTORY_PLACEHOLDER

    if newest_first:
        window = _newest_turn_first(window)

    return "\n".join(
        f"{'User' if role == 'user' else 'Assistant'}: {content}" for role, content in window
    )


def _newest_turn_first(
    window: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Reverse by user/assistant turn pairs; keep order inside each turn."""
    pairs: list[list[tuple[str, str]]] = []
    idx = 0
    while idx < len(window):
        role, _content = window[idx]
        if role == "user" and idx + 1 < len(window) and window[idx + 1][0] == "assistant":
            pairs.append([window[idx], window[idx + 1]])
            idx += 2
            continue
        pairs.append([window[idx]])
        idx += 1
    pairs.reverse()
    ordered: list[tuple[str, str]] = []
    for pair in pairs:
        ordered.extend(pair)
    return ordered


def format_prior_action_facts(
    messages: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    max_entries: int = 6,
    max_chars: int = 4_000,
) -> str:
    """Render a compact fact block from earlier assistant/tool outputs.

    The persisted conversation is the source of truth. This view only makes the
    actionable parts easier for the next prompt to use: tool inputs/results,
    command stdout, and value-shaped lines such as weather readings.
    """
    facts: list[str] = []
    limit = max_entries if max_entries > 0 else None
    # Walk newest-first so we can stop as soon as `limit` facts are found,
    # instead of scanning the full history and trimming afterward.
    for entry in reversed(messages):
        try:
            role, content = entry
        except (TypeError, ValueError):
            continue
        if role != "assistant" or not isinstance(content, str):
            continue
        # A compacted session summary quotes old tool output wholesale; letting
        # it in as one giant "fact" can spend the whole budget and starve
        # newer live results. The summary reaches the model via the history
        # block instead.
        if content.startswith(SESSION_SUMMARY_PREFIX):
            continue
        text = content.strip()
        if not text:
            continue
        lower = text.lower()
        if not any(
            marker in lower for marker in _ACTION_FACT_MARKERS
        ) and not _VALUE_LINE_RE.search(text):
            continue
        facts.append(text)
        if limit is not None and len(facts) >= limit:
            break

    if not facts:
        return ""

    rendered: list[str] = []
    remaining = max(max_chars, 0)
    # Newest-first: this block sits after the user message in the action
    # ephemeral half, and context_budget keeps the head. Chronological order
    # would put the freshest facts at the truncated tail.
    for idx, fact in enumerate(facts, start=1):
        if remaining <= 0:
            break
        chunk = f"- Prior assistant/tool output {idx}:\n{fact.strip()}"
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip() + "\n...[truncated]"
        rendered.append(chunk)
        remaining -= len(chunk) + 2
    return "\n\n".join(rendered)
