"""Placeholder validation for detecting drift and broken placeholders.

Provides utilities to validate that placeholders in model output are valid
and haven't drifted from the expected format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.utils.masking.placeholder import PlaceholderMap


class ValidationSeverity(Enum):
    """Severity levels for placeholder validation issues."""

    ERROR = auto()  # Broken/malformed placeholder - unmasking will fail
    WARNING = auto()  # Suspicious pattern but might be valid
    INFO = auto()  # Informational - unexpected but not broken


@dataclass(frozen=True)
class PlaceholderIssue:
    """A detected placeholder validation issue.

    Attributes:
        placeholder: The problematic placeholder text
        severity: ERROR, WARNING, or INFO
        message: Human-readable explanation of the issue
        position: Position in text where issue was found (or -1 if not applicable)
    """

    placeholder: str
    severity: ValidationSeverity
    message: str
    position: int = -1


# Valid placeholder patterns
_VALID_PLACEHOLDER_PATTERN = re.compile(
    r"<(?:HOSTNAME|ACCOUNT|CLUSTER|SERVICE|IP|EMAIL|CUSTOM|MASKED)_\d+>"
)

# Pattern to detect potentially broken/incomplete placeholders
_BROKEN_PLACEHOLDER_PATTERNS = [
    # Unclosed placeholders
    (
        re.compile(r"<(?:HOSTNAME|ACCOUNT|CLUSTER|SERVICE|IP|EMAIL|CUSTOM|MASKED)_?\d?[^>]*$"),
        "Unclosed placeholder",
    ),
    # Wrong format with underscore but missing type
    (re.compile(r"<_\d+>"), "Placeholder missing type prefix"),
    # Double angle brackets
    (
        re.compile(r"<<(?:HOSTNAME|ACCOUNT|CLUSTER|SERVICE|IP|EMAIL|CUSTOM|MASKED)_\d+>>"),
        "Double angle brackets",
    ),
    # Placeholder with letters after number
    (
        re.compile(r"<(?:HOSTNAME|ACCOUNT|CLUSTER|SERVICE|IP|EMAIL|CUSTOM|MASKED)_\d+[a-zA-Z]+>"),
        "Invalid suffix after number",
    ),
]

# Pattern to find all potential placeholders (valid or not)
_ALL_PLACEHOLDER_LIKE_PATTERN = re.compile(r"<[^>]+>")


def validate_placeholders(
    text: str, placeholder_map: PlaceholderMap | None = None
) -> list[PlaceholderIssue]:
    """Validate placeholders in text.

    Checks for:
    1. Broken/malformed placeholders that won't unmask correctly
    2. Placeholders that don't exist in the mapping (potential drift)
    3. Suspicious patterns that might indicate corruption

    Args:
        text: The text to validate (typically LLM output)
        placeholder_map: Optional map to check against known placeholders

    Returns:
        List of validation issues found (empty if all valid)
    """
    issues: list[PlaceholderIssue] = []
    reported_positions: set[tuple[int, int]] = set()

    # Check for broken patterns first
    for pattern, description in _BROKEN_PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            pos = (match.start(), match.end())
            if pos not in reported_positions:
                reported_positions.add(pos)
                issues.append(
                    PlaceholderIssue(
                        placeholder=match.group(0),
                        severity=ValidationSeverity.ERROR,
                        message=f"{description}: '{match.group(0)}'",
                        position=match.start(),
                    )
                )

    # Find all placeholder-like patterns
    for match in _ALL_PLACEHOLDER_LIKE_PATTERN.finditer(text):
        pos = (match.start(), match.end())
        # Skip if we already reported an issue at this position
        if pos in reported_positions:
            continue

        placeholder = match.group(0)

        # Skip if it's a valid placeholder pattern
        if _VALID_PLACEHOLDER_PATTERN.fullmatch(placeholder):
            # If we have a map, check if this placeholder is known
            if placeholder_map is not None:
                original = placeholder_map.get_original_value(placeholder)
                if original is None:
                    # This is a valid-format placeholder but not in our map
                    issues.append(
                        PlaceholderIssue(
                            placeholder=placeholder,
                            severity=ValidationSeverity.WARNING,
                            message=f"Unknown placeholder '{placeholder}' not in mapping - may be hallucinated by model",
                            position=match.start(),
                        )
                    )
            continue

        # Check if it looks like a placeholder attempt (starts with < and ends with >)
        # but doesn't match valid pattern
        if re.match(r"<[A-Z_]+\d*", placeholder) or re.match(r"<\w+_", placeholder):
            reported_positions.add(pos)
            issues.append(
                PlaceholderIssue(
                    placeholder=placeholder,
                    severity=ValidationSeverity.ERROR,
                    message=f"Malformed placeholder '{placeholder}' - does not match expected format <TYPE_N>",
                    position=match.start(),
                )
            )

    return issues


def has_valid_placeholders(text: str) -> bool:
    """Quick check if text contains only valid placeholders.

    Args:
        text: Text to check

    Returns:
        True if no placeholder issues found, False otherwise
    """
    return len(validate_placeholders(text)) == 0


def get_unknown_placeholders(text: str, placeholder_map: PlaceholderMap) -> list[str]:
    """Get list of placeholders in text that aren't in the mapping.

    Args:
        text: Text to check
        placeholder_map: The placeholder mapping to check against

    Returns:
        List of unknown placeholder strings
    """
    issues = validate_placeholders(text, placeholder_map)
    return [issue.placeholder for issue in issues if "not in mapping" in issue.message]
