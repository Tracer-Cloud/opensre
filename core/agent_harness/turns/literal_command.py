"""Messages a turn runs verbatim as a command instead of asking the model.

An explicit ``!command`` shell escape, or a literal ``/command`` after any
registered vendor context prefix. A host that adds text to a message (facts a
caller resolved up front) must leave these alone: the command would read the
added text as arguments.
"""

from __future__ import annotations

from infrastructure.harness_providers import strip_message_context_prefix


def bang_shell_command(message: str) -> str | None:
    """The normalized ``!command`` the user typed as an explicit shell escape, or ``None``."""
    # Explicit `!cmd` shell escape: a deterministic bypass for input the user
    # typed verbatim as a shell command. This is NOT natural-language intent
    # inference — do NOT copy this pattern for bare aliases, regex/keyword
    # matches, or "obvious" natural-language intents. Those must go through the
    # action-agent LLM selecting first-class AgentTools. Engineers have been
    # fired before for reintroducing regex/keyword intent shortcuts here.
    stripped = message.strip()
    if not stripped.startswith("!") or len(stripped) <= 1:
        return None
    cmd = " ".join(stripped[1:].split())
    return f"!{cmd}" if cmd else None


def literal_slash_text(message: str) -> str | None:
    """The ``/command …`` text of a message typed as a literal slash command, or ``None``."""
    _, remainder = strip_message_context_prefix(message)
    stripped = remainder.strip()
    return stripped if stripped.startswith("/") else None


def is_literal_command(message: str) -> bool:
    """True when the turn runs ``message`` verbatim instead of asking the model."""
    return bang_shell_command(message) is not None or literal_slash_text(message) is not None


__all__ = ["bang_shell_command", "is_literal_command", "literal_slash_text"]
