"""Vendor prompt fragments and gateway persona wording.

Vendor-specific prompt paragraphs (tool usage recipes for a particular
integration) do not belong in core's prompt builders. Integrations register a
zero-arg fragment factory here from ``integrations/harness_adapters.py``; core
prompt builders append the joined fragments without naming any vendor.

Gateway persona fragments are distinct: Slack/gateway teammate-persona wording
that only applies when the turn's surface is ``"gateway"``, whereas the
action/assistant fragments join into the shared prompt regardless of
surface.
"""

from __future__ import annotations

from collections.abc import Callable

PromptFragmentFn = Callable[[], str]

_action_prompt_fragments: list[PromptFragmentFn] = []
_assistant_prompt_fragments: list[PromptFragmentFn] = []
_gateway_persona_fragments: list[PromptFragmentFn] = []


def register_action_prompt_fragment(fn: PromptFragmentFn) -> None:
    _action_prompt_fragments.append(fn)


def action_prompt_vendor_fragments() -> str:
    return "\n\n".join(fn() for fn in _action_prompt_fragments)


def clear_action_prompt_fragments() -> None:
    _action_prompt_fragments.clear()


def register_assistant_prompt_fragment(fn: PromptFragmentFn) -> None:
    _assistant_prompt_fragments.append(fn)


def assistant_prompt_vendor_fragments() -> str:
    return "\n\n".join(fn() for fn in _assistant_prompt_fragments)


def clear_assistant_prompt_fragments() -> None:
    _assistant_prompt_fragments.clear()


def register_gateway_persona_fragment(fn: PromptFragmentFn) -> None:
    _gateway_persona_fragments.append(fn)


def gateway_persona_fragments() -> str:
    return "\n\n".join(fn() for fn in _gateway_persona_fragments)


def clear_gateway_persona_fragments() -> None:
    _gateway_persona_fragments.clear()


def reset() -> None:
    """Clear all registered prompt/persona fragments (tests)."""
    clear_action_prompt_fragments()
    clear_assistant_prompt_fragments()
    clear_gateway_persona_fragments()


__all__ = [
    "PromptFragmentFn",
    "action_prompt_vendor_fragments",
    "assistant_prompt_vendor_fragments",
    "clear_action_prompt_fragments",
    "clear_assistant_prompt_fragments",
    "clear_gateway_persona_fragments",
    "gateway_persona_fragments",
    "register_action_prompt_fragment",
    "register_assistant_prompt_fragment",
    "register_gateway_persona_fragment",
]
