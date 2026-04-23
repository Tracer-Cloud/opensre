"""Per-investigation masking context.

``MaskingContext`` holds a ``MaskingPolicy`` and a stable placeholder map
for the lifetime of a single investigation. Mask and unmask operations run
over strings, lists, and dicts. The placeholder map is serialized to
``AgentState["masking_map"]`` so it survives node-to-node transitions.
"""

from __future__ import annotations

import re
from typing import Any

from app.masking.detectors import DetectedIdentifier, find_identifiers
from app.masking.policy import MaskingPolicy, compile_extra_patterns


class MaskingContext:
    """Stable masking state for one investigation."""

    def __init__(
        self,
        policy: MaskingPolicy,
        placeholder_map: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy
        # placeholder -> original value
        self._placeholder_map: dict[str, str] = dict(placeholder_map or {})
        # original value -> placeholder (reverse for reuse/stability)
        self._reverse_map: dict[str, str] = {
            original: placeholder for placeholder, original in self._placeholder_map.items()
        }
        # running counter per kind so placeholder numbers stay stable within a run
        self._counters: dict[str, int] = self._derive_counters()
        # Compile extra regex patterns once per context to avoid per-call work
        self._compiled_extras: dict[str, re.Pattern[str]] = compile_extra_patterns(policy)

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> MaskingContext:
        """Reconstruct a context from an investigation state dict.

        Policy is re-read from the environment so env changes are honoured.
        ``placeholder_map`` carries the mappings accumulated by earlier nodes
        in the same investigation.
        """
        policy = MaskingPolicy.from_env()
        existing = state.get("masking_map") or {}
        if not isinstance(existing, dict):
            existing = {}
        return cls(policy=policy, placeholder_map=dict(existing))

    @property
    def placeholder_map(self) -> dict[str, str]:
        return dict(self._placeholder_map)

    def _derive_counters(self) -> dict[str, int]:
        # Accumulate the maximum index per kind across the whole map first,
        # then add 1 once at the end. Doing "+1" inside the loop would
        # over-count when the map is iterated out of ascending order
        # (e.g. <NS_2>, <NS_0> would yield 4 instead of 3).
        max_index: dict[str, int] = {}
        for placeholder in self._placeholder_map:
            kind, _, index = placeholder.strip("<>").rpartition("_")
            if not kind or not index.isdigit():
                continue
            key = kind.lower()
            max_index[key] = max(max_index.get(key, -1), int(index))
        return {key: value + 1 for key, value in max_index.items()}

    def _new_placeholder(self, kind: str) -> str:
        index = self._counters.get(kind, 0)
        self._counters[kind] = index + 1
        return f"<{kind.upper()}_{index}>"

    def _ensure_placeholder(self, kind: str, value: str) -> str:
        if value in self._reverse_map:
            return self._reverse_map[value]
        placeholder = self._new_placeholder(kind)
        self._placeholder_map[placeholder] = value
        self._reverse_map[value] = placeholder
        return placeholder

    def mask(self, text: str) -> str:
        """Return ``text`` with sensitive identifiers replaced by placeholders.

        Pass-through (identity) when the policy is disabled.
        """
        if not self.policy.enabled or not text:
            return text
        matches = find_identifiers(text, self.policy, self._compiled_extras)
        if not matches:
            return text
        return self._apply_replacements(text, matches)

    def _apply_replacements(self, text: str, matches: list[DetectedIdentifier]) -> str:
        # Replace in reverse order so earlier positions remain valid.
        result = text
        for m in sorted(matches, key=lambda x: x.start, reverse=True):
            placeholder = self._ensure_placeholder(m.kind, m.value)
            result = result[: m.start] + placeholder + result[m.end :]
        return result

    def unmask(self, text: str) -> str:
        """ "Restore any known placeholders in ``text`` to their original values."""
        if not text or not self._placeholder_map:
            return text
        result = text
        # Sort longest-first to avoid prefix collisions (e.g. <NS_10> before <NS_1>)
        for placeholder, original in sorted(
            self._placeholder_map.items(), key=lambda x: len(x[0]), reverse=True
        ):
            if placeholder in result:
                result = result.replace(placeholder, original)
        return result

    def mask_value(self, value: Any) -> Any:
        """Recursively mask strings inside dicts/lists/tuples."""
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, dict):
            return {k: self.mask_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.mask_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.mask_value(v) for v in value)
        return value

    def unmask_value(self, value: Any) -> Any:
        """Recursively unmask strings inside dicts/lists/tuples."""
        if isinstance(value, str):
            return self.unmask(value)
        if isinstance(value, dict):
            return {k: self.unmask_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.unmask_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.unmask_value(v) for v in value)
        return value

    def to_state(self) -> dict[str, str]:
        """Return the placeholder map in a form suitable for state storage."""
        return dict(self._placeholder_map)


__all__ = ["MaskingContext"]
