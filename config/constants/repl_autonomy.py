"""Auto (Off|Low|Med|High) autonomy levels for the REPL.

Default is High: alpha still allows every action without a prompt. Lower
levels opt into the existing ``ask`` confirmation hook — they are not a
shell-command allowlist.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class AutoLevel(StrEnum):
    """Autonomy shown on the status line above the input."""

    OFF = "off"
    LOW = "low"
    MED = "med"
    HIGH = "high"


DEFAULT_AUTO_LEVEL: Final[AutoLevel] = AutoLevel.HIGH

AUTO_LEVEL_CAPTIONS: Final[dict[AutoLevel, str]] = {
    AutoLevel.OFF: "all actions require approval",
    AutoLevel.LOW: "approve shell and mutating tools",
    AutoLevel.MED: "approve shell and mutating tools",
    AutoLevel.HIGH: "all actions allowed",
}

# Idle status bar — short permission words. High is the default; hiding the
# caption made it look like "best / full power" instead of allow-all.
AUTO_LEVEL_BAR_CAPTIONS: Final[dict[AutoLevel, str]] = {
    AutoLevel.OFF: "Ask everything",
    AutoLevel.LOW: "Ask shell + edits",
    AutoLevel.MED: "Ask shell + edits",
    AutoLevel.HIGH: "Allow all",
}

# Display title inside ``Auto (Med)`` — Factory uses Med, not medium.
AUTO_LEVEL_TITLES: Final[dict[AutoLevel, str]] = {
    AutoLevel.OFF: "Off",
    AutoLevel.LOW: "Low",
    AutoLevel.MED: "Med",
    AutoLevel.HIGH: "High",
}

# tool_type values that still need confirmation at this level (High: none).
# Mutation-capable agent tools ask at Med+ — including synthetic_test (spawns a
# mutation-classified subprocess), slash/CLI, Sentry issue-fix (edits the
# working tree and can commit/push/open a PR), and the merge push (commits a
# resolved merge and pushes the branch) — otherwise those run without the
# confirmation promised by ``/auto med``.
_MUTATING_AGENT_TOOL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "shell",
        "code_agent",
        "slash",
        "cli_command",
        "opensre_cli",
        "switch_llm_provider",
        "synthetic_test",
        "sentry_issue_fix",
        "merge_push",
    }
)

AUTO_LEVEL_ASK_TOOL_TYPES: Final[dict[AutoLevel, frozenset[str] | None]] = {
    AutoLevel.HIGH: frozenset(),
    # Read-only tool types run; shell text is not treated as provably read-only.
    AutoLevel.MED: _MUTATING_AGENT_TOOL_TYPES,
    AutoLevel.LOW: _MUTATING_AGENT_TOOL_TYPES,
    AutoLevel.OFF: None,  # ask every tool type
}


def parse_auto_level(raw: str) -> AutoLevel | None:
    """Parse a user-facing auto level, or ``None`` when unknown."""
    token = raw.strip().lower()
    aliases = {"medium": AutoLevel.MED, "off": AutoLevel.OFF}
    if token in aliases:
        return aliases[token]
    try:
        return AutoLevel(token)
    except ValueError:
        return None


def format_auto_status_plain(level: AutoLevel) -> str:
    """Format the session's auto level for the ``/auto`` command."""
    return f"Auto ({AUTO_LEVEL_TITLES[level]}) · {AUTO_LEVEL_CAPTIONS[level]}"


def format_auto_status_bar(level: AutoLevel) -> str:
    """``Auto (High) · Allow all`` — live prompt chrome, no model slug."""
    return f"Auto ({AUTO_LEVEL_TITLES[level]}) · {AUTO_LEVEL_BAR_CAPTIONS[level]}"


__all__ = [
    "AUTO_LEVEL_ASK_TOOL_TYPES",
    "AUTO_LEVEL_BAR_CAPTIONS",
    "AUTO_LEVEL_CAPTIONS",
    "AUTO_LEVEL_TITLES",
    "AutoLevel",
    "DEFAULT_AUTO_LEVEL",
    "format_auto_status_bar",
    "format_auto_status_plain",
    "parse_auto_level",
]
