"""Context-window budgeting for shared agent tool loops."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from config.llm_models import DEFAULT_MAX_TOKENS

logger = logging.getLogger(__name__)

# Prompt windows are substring-matched against provider model ids. Unknown
# models use a conservative default so we trim early rather than overflow.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gemini": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4": 128_000,
    # Lookup is first-substring-match in insertion order, so a longer family
    # id must stay above any shorter prefix that it contains. GPT-6.1 Sol's
    # published window is 1,050,000 tokens with a 922,000 input cap and
    # 128,000 max output. The trimmer only reserves DEFAULT_MAX_TOKENS
    # (25,000), so the stored window is that input cap plus the reserve.
    # A full 1,050,000 window would admit prompts the API rejects.
    "gpt-6.1": 947_000,
    # gpt-5.4 / gpt-5.6 must stay above the gpt-5 catch-all or they are never reached.
    "gpt-5.6": 1_000_000,
    "gpt-5.4": 1_000_000,
    # gpt-5 window is conservatively pinned to 128k until confirmed for the
    # dated snapshot in use; raise once verified to reclaim headroom.
    "gpt-5": 128_000,
    "o1": 128_000,
    "o3": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 128_000

_TOKEN_BUDGET_CEILING = _DEFAULT_CONTEXT_WINDOW - DEFAULT_MAX_TOKENS

# Conservative char-to-token estimate for JSON-heavy tool payloads.
_TOKENS_PER_CHAR = 0.50

_TRUNCATION_MARKER = "…[truncated to fit context budget]"
_TRUNCATION_SAFETY_TOKENS = 2_000
_TRUNCATION_MIN_TOKENS = 1_000

_PINNED_MESSAGE_KEY = "_opensre_seed"
_DUPLICATE_RESULT_KEY = "_opensre_duplicate_result"
# Marks a tool result whose output was replaced by the eviction stub.
_EVICTED_RESULT_KEY = "_opensre_evicted"
EVICTED_TOOL_RESULT_TEXT = (
    "[This tool call's output was removed to fit the context window. "
    "Call the tool again if you still need it.]"
)


def strip_internal_message_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` without internal ``_opensre_*`` keys.

    Context-budget eviction tags seed and duplicate tool exchanges with these
    markers. They must remain on the in-memory transcript for trimming heuristics
    but are rejected by strict provider message schemas (e.g. Anthropic).
    """
    return [
        {key: value for key, value in message.items() if not key.startswith("_opensre_")}
        for message in messages
    ]


@dataclass(frozen=True)
class _ToolExchange:
    start: int
    end: int
    token_estimate: int
    duplicate_only: bool


def _is_pinned_message(message: dict[str, Any]) -> bool:
    """Whether whole-pair eviction must preserve this message."""
    return bool(message.get(_PINNED_MESSAGE_KEY))


def _is_duplicate_result_message(message: dict[str, Any]) -> bool:
    """Whether this message belongs to a duplicate-only tool exchange."""
    return bool(message.get(_DUPLICATE_RESULT_KEY))


def _is_evicted_message(message: dict[str, Any]) -> bool:
    """Whether this tool-result message already holds the eviction stub."""
    return bool(message.get(_EVICTED_RESULT_KEY))


def _has_tool_use_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and (block.get("type") == "tool_use" or "toolUse" in block)
        for block in content
    )


def _candidate_exchange(
    messages: list[dict[str, Any]],
    *,
    start: int,
    end: int,
    message_tokens: list[int] | None = None,
    evicted: bool = False,
) -> _ToolExchange | None:
    """The exchange at ``[start, end)`` when it is a candidate, else ``None``.

    With ``evicted=False`` only exchanges that still carry real output qualify
    (to be stubbed); with ``evicted=True`` only fully stubbed ones (to be dropped).
    """
    exchange_messages = messages[start:end]
    if any(_is_pinned_message(message) for message in exchange_messages):
        return None

    result_messages = exchange_messages[1:]
    if not result_messages:
        return None
    if all(_is_evicted_message(message) for message in result_messages) != evicted:
        return None
    duplicate_only = bool(result_messages) and all(
        _is_duplicate_result_message(message) for message in result_messages
    )
    if message_tokens is not None:
        token_estimate = sum(message_tokens[start:end])
    else:
        token_estimate = estimate_message_tokens(exchange_messages)
    return _ToolExchange(
        start=start,
        end=end,
        token_estimate=token_estimate,
        duplicate_only=duplicate_only,
    )


def _append_candidate(
    candidates: list[_ToolExchange],
    messages: list[dict[str, Any]],
    *,
    start: int,
    end: int,
    message_tokens: list[int] | None = None,
    evicted: bool = False,
) -> None:
    candidate = _candidate_exchange(
        messages, start=start, end=end, message_tokens=message_tokens, evicted=evicted
    )
    if candidate is not None:
        candidates.append(candidate)


def _tool_exchange_candidates(
    messages: list[dict[str, Any]],
    *,
    message_tokens: list[int] | None = None,
    evicted: bool = False,
) -> list[_ToolExchange]:
    candidates: list[_ToolExchange] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue

        if _has_tool_use_block(message.get("content")):
            _append_candidate(
                candidates,
                messages,
                start=index,
                end=min(index + 2, len(messages)),
                message_tokens=message_tokens,
                evicted=evicted,
            )
            continue

        tool_calls = message.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            call_ids = {tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")}
            end = index + 1
            while end < len(messages):
                follower = messages[end]
                if follower.get("role") == "tool" and follower.get("tool_call_id") in call_ids:
                    end += 1
                else:
                    break
            _append_candidate(
                candidates,
                messages,
                start=index,
                end=end,
                message_tokens=message_tokens,
                evicted=evicted,
            )
    return candidates


def _eviction_priority(exchange: _ToolExchange) -> tuple[int, int, int]:
    """Lower priority tuple is evicted first."""
    duplicate_rank = 0 if exchange.duplicate_only else 1
    return (duplicate_rank, -exchange.token_estimate, exchange.start)


def context_budget_ceiling_for_model(
    model: str | None, *, max_output_tokens: int = DEFAULT_MAX_TOKENS
) -> int:
    """Trim ceiling after reserving at least the configured output budget.

    Substring match (case-insensitive) so dated snapshots and provider prefixes
    resolve to the right family. Unknown → conservative default, which only ever
    trims slightly early; it never risks an overflow.
    """
    window = _DEFAULT_CONTEXT_WINDOW
    if model:
        key = model.lower()
        for family, family_window in _MODEL_CONTEXT_WINDOWS.items():
            if family in key:
                window = family_window
                break
    response_headroom = max(DEFAULT_MAX_TOKENS, max_output_tokens)
    return max(window - response_headroom, 0)


def _message_token_estimate(message: dict[str, Any]) -> int:
    total = 0
    content = message.get("content", "")
    if isinstance(content, str):
        total += int(len(content) * _TOKENS_PER_CHAR)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                total += int(len(json.dumps(block, default=str)) * _TOKENS_PER_CHAR)
            elif isinstance(block, str):
                total += int(len(block) * _TOKENS_PER_CHAR)
    return total


def _message_token_estimates(messages: list[dict[str, Any]]) -> tuple[list[int], int]:
    tokens = [_message_token_estimate(message) for message in messages]
    return tokens, sum(tokens)


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(_message_token_estimate(message) for message in messages)


def _system_tokens(system: str | None) -> int:
    return int(len(system) * _TOKENS_PER_CHAR) if system else 0


def system_and_tools_overhead(
    system: str | None,
    tools: list[dict[str, Any]] | None,
) -> int:
    """Fixed token overhead for the system prompt and tool schemas.

    Callers on a hot path (e.g. the agent ReAct loop) should call this
    once and pass the result to ``enforce_context_budget`` via
    ``fixed_overhead_tokens`` so tool schemas are not re-serialized every
    iteration.
    """
    total = _system_tokens(system)
    if tools:
        for schema in tools:
            total += int(len(json.dumps(schema, default=str)) * _TOKENS_PER_CHAR)
    return total


def estimate_message_tokens(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Cheap upper-bound token estimate covering everything Anthropic sees.

    Anthropic counts ``messages`` + ``system`` + ``tools`` toward the 200k
    prompt limit. Earlier versions counted only ``messages`` and trimmed
    aggressively while system + tools (tens of thousands of tokens for
    opensre's 100+ tool registry) silently pushed us over the line.
    """
    return _estimate_messages_tokens(messages) + system_and_tools_overhead(system, tools)


def _stub_tool_result_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """A copy of ``message`` with every tool result replaced by the eviction stub.

    Builds new containers rather than editing in place: provider messages share
    nested blocks with the run's transcript, which must keep the real output.
    Returns ``None`` when the message carries no tool result.
    """
    if message.get("role") == "tool":
        return {**message, "content": EVICTED_TOOL_RESULT_TEXT, _EVICTED_RESULT_KEY: True}
    content = message.get("content")
    if not isinstance(content, list):
        return None
    stubbed: list[Any] = []
    changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            stubbed.append({**block, "content": EVICTED_TOOL_RESULT_TEXT})
            changed = True
        elif isinstance(block, dict) and isinstance(block.get("toolResult"), dict):
            result = {**block["toolResult"], "content": [{"text": EVICTED_TOOL_RESULT_TEXT}]}
            stubbed.append({**block, "toolResult": result})
            changed = True
        else:
            stubbed.append(block)
    if not changed:
        return None
    return {**message, "content": stubbed, _EVICTED_RESULT_KEY: True}


def _trim_lowest_value_tool_pair(
    messages: list[dict[str, Any]],
    *,
    message_tokens: list[int] | None = None,
) -> tuple[int, int] | None:
    """Stub the results of one non-pinned tool exchange; return its ``[start, end)``.

    The call stays and its output is replaced by a short note, so the model
    still knows the call happened and can repeat it instead of losing the
    exchange without a trace. ``message_tokens`` is updated in place.
    """
    candidates = _tool_exchange_candidates(messages, message_tokens=message_tokens)
    if not candidates:
        return None

    selected = min(candidates, key=_eviction_priority)
    for index in range(selected.start + 1, selected.end):
        stubbed = _stub_tool_result_message(messages[index])
        if stubbed is None:
            continue
        messages[index] = stubbed
        if message_tokens is not None:
            message_tokens[index] = _message_token_estimate(stubbed)
    return selected.start, selected.end


def _drop_oldest_stubbed_exchange(
    messages: list[dict[str, Any]],
    *,
    message_tokens: list[int],
) -> bool:
    """Remove the oldest exchange whose output is already a stub (last resort)."""
    candidates = _tool_exchange_candidates(messages, message_tokens=message_tokens, evicted=True)
    if not candidates:
        return False
    oldest = min(candidates, key=lambda exchange: exchange.start)
    del messages[oldest.start : oldest.end]
    del message_tokens[oldest.start : oldest.end]
    return True


def trim_lowest_value_tool_pair(messages: list[dict[str, Any]]) -> bool:
    """Stub one non-pinned tool exchange's results using the eviction heuristic."""
    return _trim_lowest_value_tool_pair(messages) is not None


def _shrink_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_chars`` (inclusive of the marker). No-op if it fits."""
    if len(text) <= max_chars:
        return text, False
    keep = max(max_chars - len(_TRUNCATION_MARKER), 0)
    return text[:keep] + _TRUNCATION_MARKER, True


def _sum_text_chars(node: Any) -> int:
    """Total char length of every truncatable string in a content tree.

    Targets the bulky payload fields opensre actually emits: a dict's ``content``
    / ``text`` (Anthropic tool_result + text blocks) and bare strings inside
    lists, recursing through nested dicts/lists.
    """
    total = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and key in ("content", "text"):
                total += len(value)
            elif isinstance(value, (list, dict)):
                total += _sum_text_chars(value)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                total += len(value)
            elif isinstance(value, (list, dict)):
                total += _sum_text_chars(value)
    return total


def _apply_text_factor(node: Any, factor: float) -> bool:
    """Shrink every truncatable string in a content tree to ~``factor`` of its
    length, mutating in place. Returns whether anything changed."""
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and key in ("content", "text"):
                new_value, slot_changed = _shrink_text(value, max(int(len(value) * factor), 0))
                if slot_changed:
                    node[key] = new_value
                    changed = True
            elif isinstance(value, (list, dict)):
                changed = _apply_text_factor(value, factor) or changed
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            if isinstance(value, str):
                new_value, slot_changed = _shrink_text(value, max(int(len(value) * factor), 0))
                if slot_changed:
                    node[idx] = new_value
                    changed = True
            elif isinstance(value, (list, dict)):
                changed = _apply_text_factor(value, factor) or changed
    return changed


def truncate_content(content: Any, max_chars: int) -> tuple[Any, bool]:
    """Shrink a message's ``content`` so its char length is ~``max_chars``.

    String content is cut directly. List content (Anthropic block lists) is
    truncated proportionally across its text slots so the whole message lands
    near the budget rather than zeroing the first slot. Returns the (possibly
    same, mutated-in-place) content object and whether anything changed.
    """
    if isinstance(content, str):
        return _shrink_text(content, max_chars)
    if isinstance(content, list):
        total = _sum_text_chars(content)
        if total <= max_chars:
            return content, False
        factor = max_chars / total if total else 0.0
        return content, _apply_text_factor(content, factor)
    return content, False


def _truncate_largest_message(
    messages: list[dict[str, Any]],
    *,
    message_tokens: list[int],
    total_message_tokens: int,
    fixed_overhead_tokens: int,
    ceiling: int,
) -> tuple[bool, int]:
    """Truncate the biggest still-shrinkable message so the prompt fits.

    Tries messages largest-first (so an untruncatable assistant ``tool_calls``
    turn doesn't block a truncatable tool-result behind it) and stops at the
    first one that actually shrinks. Each successful call strictly reduces the
    total, guaranteeing the caller's loop terminates. Returns False when no
    message can be shrunk further — the caller then lets the API surface the
    error rather than spinning.
    """
    order = sorted(
        range(len(messages)),
        key=message_tokens.__getitem__,
        reverse=True,
    )
    for idx in order:
        overhead = (total_message_tokens - message_tokens[idx]) + fixed_overhead_tokens
        budget_tokens = max(ceiling - overhead - _TRUNCATION_SAFETY_TOKENS, _TRUNCATION_MIN_TOKENS)
        max_chars = int(budget_tokens / _TOKENS_PER_CHAR)
        new_content, changed = truncate_content(messages[idx].get("content"), max_chars)
        if changed:
            messages[idx]["content"] = new_content
            updated_tokens = _message_token_estimate(messages[idx])
            total_message_tokens += updated_tokens - message_tokens[idx]
            message_tokens[idx] = updated_tokens
            return True, total_message_tokens
    return False, total_message_tokens


def enforce_context_budget(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    fixed_overhead_tokens: int | None = None,
    ceiling: int = _TOKEN_BUDGET_CEILING,
) -> int:
    """Trim low-value tool exchanges until the prompt fits under ``ceiling``.

    Pass ``fixed_overhead_tokens`` (from :func:`system_and_tools_overhead`) to
    skip re-serializing tool schemas on every call.  When omitted the overhead
    is computed from ``system`` and ``tools``. Returns the estimated tokens of
    the request as sent, so a caller can compare it with the provider's count.
    """
    if fixed_overhead_tokens is None:
        fixed_overhead_tokens = system_and_tools_overhead(system, tools)
    # Per-message ledger: estimate once, then adjust only on stub / truncate.
    message_tokens, total_message_tokens = _message_token_estimates(messages)
    while (total_message_tokens + fixed_overhead_tokens) > ceiling:
        stubbed = _trim_lowest_value_tool_pair(messages, message_tokens=message_tokens)
        if stubbed is None and _drop_oldest_stubbed_exchange(
            messages, message_tokens=message_tokens
        ):
            # Every exchange is already a stub; drop whole ones, oldest first.
            total_message_tokens = sum(message_tokens)
            continue
        if stubbed is None:
            changed, total_message_tokens = _truncate_largest_message(
                messages,
                message_tokens=message_tokens,
                total_message_tokens=total_message_tokens,
                fixed_overhead_tokens=fixed_overhead_tokens,
                ceiling=ceiling,
            )
            if not changed:
                logger.warning(
                    "[agent] context still over budget after trimming + truncation "
                    "(ceiling=%d); letting the request proceed",
                    ceiling,
                )
                break
            logger.warning(
                "[agent] truncated oversized message to fit context budget (ceiling=%d)", ceiling
            )
            continue
        total_message_tokens = sum(message_tokens)
        logger.warning(
            "[agent] replaced a tool output with a stub to fit context budget (ceiling=%d)",
            ceiling,
        )
    return total_message_tokens + fixed_overhead_tokens
