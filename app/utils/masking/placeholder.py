"""Placeholder mapping for masked identifiers.

Maps sensitive identifiers to stable placeholders within an investigation context.
Repeated identifiers map to the same placeholder within one investigation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.utils.masking.policies import DetectedIdentifier, IdentifierType

logger = logging.getLogger(__name__)

# Maximum number of placeholders to prevent unbounded memory growth
_DEFAULT_MAX_PLACEHOLDERS = 1000
# Threshold at which to warn about approaching limit (80%)
_WARNING_THRESHOLD = 0.8


# Placeholder templates by identifier type name
_PLACEHOLDER_TEMPLATES: dict[str, str] = {
    "HOSTNAME": "<HOSTNAME_{index}>",
    "ACCOUNT_ID": "<ACCOUNT_{index}>",
    "CLUSTER_NAME": "<CLUSTER_{index}>",
    "SERVICE_NAME": "<SERVICE_{index}>",
    "IP_ADDRESS": "<IP_{index}>",
    "EMAIL": "<EMAIL_{index}>",
    "CUSTOM": "<CUSTOM_{index}>",
}


def _get_placeholder_template(identifier_type: IdentifierType) -> str:
    """Get the placeholder template for an identifier type."""
    return _PLACEHOLDER_TEMPLATES.get(identifier_type.name, "<MASKED_{index}>")


@dataclass
class PlaceholderMap:
    """Maps original identifiers to stable placeholders.

    Within a single investigation context, the same identifier value
    always maps to the same placeholder, enabling round-trip masking.

    Example:
        "prod-cluster-01" -> "<CLUSTER_0>"
        "prod-cluster-01" -> "<CLUSTER_0>"  (same value, same placeholder)
        "prod-cluster-02" -> "<CLUSTER_1>"  (different value, new placeholder)

    Memory management:
        - Maximum 1000 placeholders by default (configurable via max_placeholders)
        - Warnings at 80% capacity
        - New identifiers are passed through unchanged when limit reached
    """

    value_to_placeholder: dict[str, str] = field(default_factory=dict)
    placeholder_to_value: dict[str, str] = field(default_factory=dict)
    type_counters: dict[str, int] = field(default_factory=dict)
    max_placeholders: int = field(default=_DEFAULT_MAX_PLACEHOLDERS)
    _warning_issued: bool = field(default=False, repr=False)

    def _is_at_capacity(self) -> bool:
        """Check if placeholder map has reached its size limit."""
        return len(self.value_to_placeholder) >= self.max_placeholders

    def _check_capacity_warning(self) -> None:
        """Issue warning if approaching capacity limit."""
        if self._warning_issued:
            return
        current = len(self.value_to_placeholder)
        threshold = int(self.max_placeholders * _WARNING_THRESHOLD)
        if current >= threshold:
            logger.warning(
                "[masking] Placeholder map at %d/%d (%d%%) - approaching limit",
                current,
                self.max_placeholders,
                int(current / self.max_placeholders * 100),
            )
            self._warning_issued = True

    def get_or_create_placeholder(self, identifier: DetectedIdentifier) -> str:
        """Get existing placeholder or create new one for identifier value.

        Args:
            identifier: The detected identifier

        Returns:
            The placeholder string (stable for same value within this context)
            If capacity is reached, returns the original value unchanged
        """
        value = identifier.value

        # Return existing placeholder if value was already seen
        if value in self.value_to_placeholder:
            return self.value_to_placeholder[value]

        # Check capacity before creating new placeholder
        self._check_capacity_warning()
        if self._is_at_capacity():
            logger.warning(
                "[masking] Placeholder map at capacity (%d) - passing through: %s...",
                self.max_placeholders,
                value[:20],
            )
            return value  # Pass through original when at capacity

        # Create new placeholder
        type_key = identifier.identifier_type.name
        index = self.type_counters.get(type_key, 0)
        self.type_counters[type_key] = index + 1

        template = _get_placeholder_template(identifier.identifier_type)
        placeholder = template.format(index=index)

        # Store bidirectional mapping
        self.value_to_placeholder[value] = placeholder
        self.placeholder_to_value[placeholder] = value

        return placeholder

    def get_original_value(self, placeholder: str) -> str | None:
        """Get the original value for a placeholder.

        Args:
            placeholder: The placeholder string

        Returns:
            The original value, or None if not found
        """
        return self.placeholder_to_value.get(placeholder)

    def unmask_text(self, text: str) -> str:
        """Restore all placeholder values in text to original values.

        Args:
            text: Text containing placeholders

        Returns:
            Text with placeholders replaced by original values
        """
        result = text
        # Replace in order of longest placeholder first to avoid partial matches
        for placeholder, value in sorted(
            self.placeholder_to_value.items(), key=lambda x: len(x[0]), reverse=True
        ):
            result = result.replace(placeholder, value)
        return result

    def get_size(self) -> int:
        """Get the number of stored placeholder mappings.

        Returns:
            Current number of unique identifier mappings
        """
        return len(self.value_to_placeholder)

    def get_stats(self) -> dict[str, int]:
        """Get statistics about the placeholder map.

        Returns:
            Dict with size, max_size, and per-type counters
        """
        return {
            "size": self.get_size(),
            "max_size": self.max_placeholders,
            "remaining": self.max_placeholders - self.get_size(),
            **self.type_counters,
        }

    def clear(self) -> None:
        """Clear all mappings. Useful for starting a new investigation context."""
        self.value_to_placeholder.clear()
        self.placeholder_to_value.clear()
        self.type_counters.clear()
        self._warning_issued = False

    def copy(self) -> PlaceholderMap:
        """Create a copy of the placeholder map."""
        return PlaceholderMap(
            value_to_placeholder=dict(self.value_to_placeholder),
            placeholder_to_value=dict(self.placeholder_to_value),
            type_counters=dict(self.type_counters),
            max_placeholders=self.max_placeholders,
        )
