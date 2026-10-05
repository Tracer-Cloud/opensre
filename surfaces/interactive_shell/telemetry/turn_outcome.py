"""Format terminal-turn outcomes for prompt-log and PostHog analytics."""

from __future__ import annotations

_ANALYTICS_OUTPUT_MAX_CHARS = 8_000

# Slash commands whose handlers attach to the real TTY (wizards, pickers). Analytics
# should record structured success/failure, not full interactive transcripts.
_INTERACTIVE_WIZARD_SLASH_ROOTS: frozenset[str] = frozenset(
    {
        "/setup",
        "/onboard",
        "/auth",
        "/login",
    }
)
_INTERACTIVE_WIZARD_SLASH_PATHS: frozenset[str] = frozenset(
    {
        "/integrations list",
        "/integrations setup",
        "/integrations remove",
        "/mcp list",
        "/mcp connect",
        "/mcp disconnect",
        "/auth login",
        "/auth logout",
    }
)
_CONDITIONAL_BROWSER_SLASH_PATHS: frozenset[str] = frozenset(
    {
        "/integrations list",
        "/mcp list",
    }
)

# Slash commands where console capture is noisy or redundant.
_SUMMARY_ONLY_SLASH_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/help",
        "/?",
        # The user has already read the resume hint and the goodbye on screen,
        # and neither tells the model anything. Replaying the capture printed
        # the whole farewell a second time, with the spinner's frames
        # transcribed one after another — ``console.status`` animates in place
        # but ``export_text`` records every frame it wrote.
        "/exit",
        "/quit",
    }
)


def slash_command_is_interactive_wizard(command_line: str) -> bool:
    """True when ``command_line`` names a multi-step TTY wizard or picker."""
    stripped = command_line.strip()
    if not stripped.startswith("/"):
        return False
    parts = stripped.split()
    root = parts[0].lower()
    if root in _INTERACTIVE_WIZARD_SLASH_ROOTS and len(parts) == 1:
        return True
    if len(parts) >= 2:
        path = f"{root} {parts[1].lower()}"
        if path in _INTERACTIVE_WIZARD_SLASH_PATHS:
            return True
    return False


def slash_command_is_summary_only(command_line: str) -> bool:
    """True when analytics should omit captured console text for this slash command."""
    stripped = command_line.strip()
    if not stripped.startswith("/"):
        return False
    parts = stripped.split()
    root = parts[0].lower()
    if root in _SUMMARY_ONLY_SLASH_ROOTS:
        return True
    if len(parts) >= 2:
        path = f"{root} {parts[1].lower()}"
        if path in _CONDITIONAL_BROWSER_SLASH_PATHS:
            return False
    return slash_command_is_interactive_wizard(command_line)


def truncate_analytics_text(text: str, *, max_chars: int = _ANALYTICS_OUTPUT_MAX_CHARS) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 20].rstrip()}… [truncated]"


def format_wizard_cli_outcome(args: list[str], *, exit_code: int | None) -> str:
    """Structured outcome for delegated interactive CLI wizards (e.g. ``/onboard``)."""
    command = " ".join(["opensre", *args]).strip()
    if exit_code is None:
        return f"{command}: interactive wizard cancelled"
    if exit_code == 0:
        return f"{command}: interactive wizard completed successfully"
    return f"{command}: interactive wizard failed (exit {exit_code})"


def format_terminal_turn_outcome(
    command_line: str,
    *,
    kind: str,
    ok: bool,
    captured_output: str = "",
    outcome_hint: str | None = None,
    include_captured_on_summary_only: bool = False,
) -> str:
    """Build the analytics payload for one handled terminal turn."""
    if outcome_hint and outcome_hint.strip():
        return truncate_analytics_text(outcome_hint.strip())

    status = "succeeded" if ok else "failed"
    prefix = f"{kind} {command_line.strip()} ({status})"

    summary_only = kind == "slash" and slash_command_is_summary_only(command_line)
    if summary_only and not include_captured_on_summary_only:
        return prefix

    if captured_output:
        return truncate_analytics_text(f"{prefix}\n{captured_output}")
    return prefix
