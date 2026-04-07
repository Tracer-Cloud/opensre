"""Core masking and unmasking utilities.

Provides functions to mask sensitive infrastructure identifiers in text
before sending to external LLM models, and to unmask responses.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.utils.masking.placeholder import PlaceholderMap
from app.utils.masking.policies import CompiledPolicy, MaskingPolicy, find_identifiers

if TYPE_CHECKING:
    pass


@dataclass
class MaskingContext:
    """Context for masking operations within an investigation.

    Maintains stable placeholder mappings so repeated identifiers
    map to the same placeholder throughout the investigation.
    """

    policy: CompiledPolicy
    placeholder_map: PlaceholderMap

    @classmethod
    def create(cls, policy: MaskingPolicy | None = None) -> MaskingContext:
        """Create a new masking context.

        Args:
            policy: Masking policy (loads from env if not provided)

        Returns:
            New masking context
        """
        if policy is None:
            policy = MaskingPolicy.from_env()
        compiled = CompiledPolicy.from_policy(policy)
        return cls(policy=compiled, placeholder_map=PlaceholderMap())

    def mask_text(self, text: str) -> str:
        """Mask sensitive identifiers in text.

        Args:
            text: Original text containing sensitive identifiers

        Returns:
            Text with sensitive values replaced by placeholders
        """
        if not self.policy.policy.is_any_enabled():
            return text

        # Find all identifiers
        identifiers = find_identifiers(
            text,
            hostname_pattern=self.policy.hostname_pattern,
            account_id_pattern=self.policy.account_id_pattern,
            cluster_name_pattern=self.policy.cluster_name_pattern,
            service_name_pattern=self.policy.service_name_pattern,
            ip_address_pattern=self.policy.ip_address_pattern,
            email_pattern=self.policy.email_pattern,
            custom_patterns=self.policy.custom_patterns
            if self.policy.policy.custom_patterns
            else None,
        )

        if not identifiers:
            return text

        # Replace from end to start to maintain positions
        result = text
        for identifier in reversed(identifiers):
            placeholder = self.placeholder_map.get_or_create_placeholder(identifier)
            result = result[: identifier.start] + placeholder + result[identifier.end :]

        return result

    def unmask_text(self, text: str) -> str:
        """Restore placeholders in text to original values.

        Args:
            text: Masked text with placeholders

        Returns:
            Text with placeholders replaced by original values
        """
        return self.placeholder_map.unmask_text(text)

    def get_stats(self) -> dict[str, int]:
        """Get statistics about masked identifiers.

        Returns:
            Dict mapping identifier type names to count (matches IdentifierType enum names)
        """
        # Use type_counters from PlaceholderMap which tracks by full IdentifierType.name
        # (e.g., "ACCOUNT_ID", "CLUSTER_NAME" instead of abbreviated placeholder labels)
        return dict(self.placeholder_map.type_counters)


# Global context for convenience (per-investigation contexts are preferred)
_global_context: MaskingContext | None = None
_global_context_lock = threading.Lock()


def get_global_context() -> MaskingContext:
    """Get or create the global masking context.

    Returns:
        Global masking context (creates if needed)
    """
    global _global_context
    if _global_context is None:
        with _global_context_lock:
            if _global_context is None:
                _global_context = MaskingContext.create()
    return _global_context


def reset_global_context() -> None:
    """Reset the global masking context."""
    global _global_context
    _global_context = None


def mask_text(text: str, context: MaskingContext | None = None) -> str:
    """Mask sensitive identifiers in text.

    Convenience function that uses global context if none provided.

    Args:
        text: Original text
        context: Optional masking context (uses global if not provided)

    Returns:
        Masked text
    """
    ctx = context or get_global_context()
    return ctx.mask_text(text)


def unmask_text(text: str, context: MaskingContext | None = None) -> str:
    """Restore placeholders to original values.

    Convenience function that uses global context if none provided.

    Args:
        text: Masked text with placeholders
        context: Optional masking context (uses global if not provided)

    Returns:
        Unmasked text with original values restored
    """
    ctx = context or get_global_context()
    return ctx.unmask_text(text)


def mask_dict(data: dict[str, object], context: MaskingContext | None = None) -> dict[str, object]:
    """Mask sensitive identifiers in dictionary values.

    Recursively masks string values in the dictionary.

    Args:
        data: Dictionary containing potentially sensitive strings
        context: Optional masking context

    Returns:
        Dictionary with masked values
    """
    ctx = context or get_global_context()
    result: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = ctx.mask_text(value)
        elif isinstance(value, dict):
            result[key] = mask_dict(value, ctx)
        elif isinstance(value, list):
            result[key] = mask_list(value, ctx)
        else:
            result[key] = value
    return result


def mask_list(data: list[object], context: MaskingContext | None = None) -> list[object]:
    """Mask sensitive identifiers in list items.

    Recursively masks string items in the list.

    Args:
        data: List containing potentially sensitive strings
        context: Optional masking context

    Returns:
        List with masked items
    """
    ctx = context or get_global_context()
    result: list[object] = []
    for item in data:
        if isinstance(item, str):
            result.append(ctx.mask_text(item))
        elif isinstance(item, dict):
            result.append(mask_dict(item, ctx))
        elif isinstance(item, list):
            result.append(mask_list(item, ctx))
        else:
            result.append(item)
    return result


def unmask_dict(
    data: dict[str, object], context: MaskingContext | None = None
) -> dict[str, object]:
    """Restore placeholders in dictionary values.

    Args:
        data: Dictionary with potentially masked values
        context: Optional masking context

    Returns:
        Dictionary with placeholders restored
    """
    ctx = context or get_global_context()
    result: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = ctx.unmask_text(value)
        elif isinstance(value, dict):
            result[key] = unmask_dict(value, ctx)
        elif isinstance(value, list):
            result[key] = unmask_list(value, ctx)
        else:
            result[key] = value
    return result


def unmask_list(data: list[object], context: MaskingContext | None = None) -> list[object]:
    """Restore placeholders in list items.

    Args:
        data: List with potentially masked items
        context: Optional masking context

    Returns:
        List with placeholders restored
    """
    ctx = context or get_global_context()
    result: list[object] = []
    for item in data:
        if isinstance(item, str):
            result.append(ctx.unmask_text(item))
        elif isinstance(item, dict):
            result.append(unmask_dict(item, ctx))
        elif isinstance(item, list):
            result.append(unmask_list(item, ctx))
        else:
            result.append(item)
    return result
