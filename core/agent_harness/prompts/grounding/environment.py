"""Session environment facts for prompt grounding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.prompts.runtime_facts import render_runtime_facts


def build_environment_block(
    *,
    integrations: tuple[str, ...],
    known: bool,
    llm_provider: str | None = None,
    reasoning_model: str | None = None,
    toolcall_model: str | None = None,
    llm_settings_available: bool | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> str:
    """Render current shell-state facts."""
    facts: list[str] = []
    if integrations:
        connected = ", ".join(integrations)
        facts.append(
            f"Configured integrations in this session: {connected}. "
            "Any integration not in that list is NOT configured. When the user asks "
            "whether a specific integration is installed/configured/connected, answer "
            "directly and definitively from this list instead of telling them to run "
            "a command."
        )
    elif known:
        facts.append(
            "No integrations are configured in this session. If the user asks whether "
            "a specific integration is installed/configured, answer that none are "
            "configured rather than deflecting."
        )

    if llm_settings_available is True:
        provider = (llm_provider or "unknown").strip() or "unknown"
        reasoning = (reasoning_model or "default").strip() or "default"
        toolcall = (toolcall_model or reasoning).strip() or reasoning
        facts.append(
            "Active LLM settings in this session: "
            f"provider {provider}; reasoning model {reasoning}; tool-call model {toolcall}. "
            "When the user asks which model/provider is being used, answer directly "
            "from these values instead of telling them to run `/model`, `/status`, "
            "or `opensre config show`."
        )
    elif llm_settings_available is False:
        facts.append(
            "Active LLM settings are unavailable in this session. If the user asks "
            "which model/provider is being used, say the settings could not be read "
            "instead of guessing or telling them to run another command."
        )

    runtime_fact = render_runtime_facts(runtime or {})
    if runtime_fact:
        facts.append(runtime_fact)
    credits_fact = _hosted_credits_fact()
    if credits_fact:
        facts.append(credits_fact)
    if not facts:
        return ""
    return "".join(("--- Environment (current shell state) ---\n", "\n".join(facts), "\n\n"))


def _hosted_credits_fact() -> str:
    from core.llm.hosted_credits import hosted_credits_prompt_lines

    lines = hosted_credits_prompt_lines()
    if not lines:
        return ""
    remaining, *rest = lines
    extra = ""
    if rest:
        admission = rest[0].rstrip(".")
        extra = f" {admission[0].upper()}{admission[1:]}."
    return (
        f"{remaining} — quote that number when the user asks how many OpenSRE "
        f"hosted credits they have left; do not invent a different balance.{extra}"
    )


__all__ = ["build_environment_block"]
