"""Message context prefix strippers.

Gateway surfaces (Slack, etc.) may prepend a channel/context marker to the raw
message text (e.g. ``[Slack channel_id=…]``) before it reaches core prompt/turn
helpers. Core needs to strip that marker to evaluate the underlying text (for
example, matching a bare affirmative like "yes") without hardcoding any vendor's
marker format.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

#: Heads the facts a host appends to a remote prompt (:func:`with_caller_facts`).
CALLER_FACTS_HEADER = "Known context:"

MessageContextPrefixStripper = Callable[[str], "tuple[str, str] | None"]

_message_context_prefix_strippers: list[MessageContextPrefixStripper] = []


def register_message_context_prefix_stripper(fn: MessageContextPrefixStripper) -> None:
    _message_context_prefix_strippers.append(fn)


def clear_message_context_prefix_strippers() -> None:
    _message_context_prefix_strippers.clear()


def strip_message_context_prefix(text: str) -> tuple[str, str]:
    """Return ``(prefix, remainder)``, trying registered strippers in order.

    The first stripper to return a non-``None`` result wins. Falls back to
    ``("", text)`` when no registered stripper matches.
    """
    for stripper in _message_context_prefix_strippers:
        result = stripper(text)
        if result is not None:
            return result
    return "", text


def with_caller_facts(prompt: str, facts: Mapping[str, str]) -> str:
    """``prompt`` followed by the facts its caller resolved up front, one per line."""
    lines = "\n".join(f"- {key}: {value}" for key, value in sorted(facts.items()))
    return f"{prompt}\n\n{CALLER_FACTS_HEADER}\n{lines}"


def split_caller_facts(text: str) -> tuple[str, str]:
    """Return ``(message, facts)``: the person's own words, and the block a host appended.

    The block steers the turn, but it is not what the person said. Read as
    theirs, a fact such as "OpenSRE web app session" names a repository called
    ``opensre``, and a bare "yes" stops being one. ``facts`` keeps its leading
    blank line, so ``message + facts`` is ``text``; it is empty when ``text``
    carries no block.
    """
    marker = f"\n\n{CALLER_FACTS_HEADER}\n"
    start = text.rfind(marker)
    if start < 0:
        return text, ""
    lines = text[start + len(marker) :].split("\n")
    if not all(line.startswith("- ") for line in lines):
        return text, ""
    return text[:start], text[start:]


def reset() -> None:
    """Clear registered strippers (tests)."""
    clear_message_context_prefix_strippers()


__all__ = [
    "CALLER_FACTS_HEADER",
    "MessageContextPrefixStripper",
    "clear_message_context_prefix_strippers",
    "register_message_context_prefix_stripper",
    "split_caller_facts",
    "strip_message_context_prefix",
    "with_caller_facts",
]
