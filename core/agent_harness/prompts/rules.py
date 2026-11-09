"""Shared LLM prompt rules for interactive-shell assistants."""

from __future__ import annotations

import re

from infrastructure.text.markdown import tighten_markdown_emphasis

# Align copy across docs-aware and conversational CLI assistants so wording
# does not drift between modules.
INTERACTIVE_SHELL_TERMINOLOGY_RULE = (
    "Terminology: always call this surface the 'interactive shell' (the "
    "OpenSRE interactive terminal launched when you run `opensre` from an "
    "interactive terminal). Never use the word 'REPL' in user-facing answers "
    "- it is internal jargon."
)

CLI_ASSISTANT_MARKDOWN_RULE = (
    "Formatting: respond in concise Markdown. Markdown will be rendered "
    "in the user's terminal, so tables, **bold**, lists, and `code spans` "
    "will display correctly - do not wrap the whole answer in a code fence. "
    "Keep **bold** tight: write **I found:** and **Want me to:** with no "
    "spaces inside the asterisks (**this**, never ** this **). Do not use "
    "__underscore__ bold — it eats filenames like __init__.py."
)

SENIOR_ENGINEER_WORKING_STYLE = (
    "Work like a senior on-call engineer pairing with the user: name the "
    "goal of this turn, then take the shortest path that achieves it. Prefer "
    "evidence you already have (tool results, session context, references) "
    "over asking. When something is wrong, diagnose from the error and keep "
    "going — do not stop at the first obstacle or dump a menu of options. "
    "Explain the non-obvious why as you go, the way you would at 2am, not as "
    "a tutorial. Be direct. Do not flatter. Do not pad. The user's request is "
    "the finish line, not that a tool ran. Listing tools, schemas, or a "
    "drafted query is not completion when they asked to change something or "
    "fetch a number. Propose done with the evidence. If you cannot complete "
    "the request, say what blocked you and stop.\n"
)

AGENT_RESPONSE_THREE_TIER_RULE = (
    "Response shape: when you report findings (especially after tool results), "
    "use three parts when the answer is more than a one-line status:\n"
    "1. **I found:** — the fact or conclusion in plain language.\n"
    "2. **Here's what that looks like:** — a short structured view (list, table, "
    "or code block) when it helps the user scan the data; omit this part for "
    "trivial answers.\n"
    "3. **Want me to:** — one specific next step tied to the finding (not a "
    "generic 'let me know if you need anything'). After integration status "
    "questions, offer something concrete such as connecting another "
    "integration, verifying a failed one, or running setup for a missing "
    "service.\n"
    "Put a blank line between each part (two newlines in Markdown) so the "
    "sections render as separate paragraphs.\n"
    "For single-line confirmations, keep the main answer to one sentence, but "
    "still add **Want me to:** when a sensible follow-up exists."
)


def normalize_three_tier_spacing(text: str) -> str:
    """Ensure three-tier section headers are separated by a Markdown paragraph break."""
    normalized = tighten_markdown_emphasis(text.replace("\r\n", "\n").replace("\r", "\n"))
    for marker in ("**Here's what that looks like:**", "**Want me to:**"):
        normalized = re.sub(
            rf"\n(?!\n)(?={re.escape(marker)})",
            "\n\n",
            normalized,
        )
        normalized = re.sub(
            rf"(?<!\n)({re.escape(marker)})",
            r"\n\n\1",
            normalized,
        )
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def format_agent_response(
    found: str,
    display: str = "",
    next_action: str = "",
) -> str:
    """Format assistant findings as the standard three-tier Markdown block.

    ``found`` is required when ``display`` or ``next_action`` is supplied.
    """
    finding = found.strip()
    detail = display.strip()
    offer = next_action.strip()
    if not finding:
        if detail or offer:
            raise ValueError("found is required when display or next_action is set")
        return ""
    if not detail and not offer:
        return finding
    sections = [f"**I found:** {finding}"]
    if detail:
        sections.append(f"**Here's what that looks like:**\n{detail}")
    if offer:
        sections.append(f"**Want me to:** {offer}")
    return "\n\n".join(sections)


__all__ = [
    "AGENT_RESPONSE_THREE_TIER_RULE",
    "CLI_ASSISTANT_MARKDOWN_RULE",
    "INTERACTIVE_SHELL_TERMINOLOGY_RULE",
    "SENIOR_ENGINEER_WORKING_STYLE",
    "format_agent_response",
    "normalize_three_tier_spacing",
]
