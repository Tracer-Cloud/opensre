"""Per-run masking rules.

``MaskingRules`` holds a ``MaskingPolicy`` and a stable placeholder map
for the lifetime of a single run. Mask and unmask operations run
over strings, lists, and dicts. The placeholder map is serialized to
``AgentState["masking_map"]`` so it survives node-to-node transitions.
"""

from __future__ import annotations

import re
from typing import Any

from infrastructure.safety.masking.detectors import DetectedIdentifier, find_identifiers
from infrastructure.safety.masking.policy import MaskingPolicy, compile_extra_patterns

# Placeholders are always ``<KIND_N>`` (no nested ``<>``). One scan finds every
# token; dict lookup restores known ones and leaves unknown angle-brackets alone.
_PLACEHOLDER_TOKEN_RE = re.compile(r"<[^<>]+>")


class MaskingRules:
    """Stable masking state for one run."""

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
    def from_state(cls, state: dict[str, Any]) -> MaskingRules:
        """Reconstruct a context from a persisted state dict.

        Policy is re-read from the environment so env changes are honoured.
        ``placeholder_map`` carries the mappings accumulated by earlier nodes
        in the same run.
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

    @staticmethod
    def _canonical_label(kind: str) -> str:
        """Token label for ``kind``, upholding the single-bracket invariant.

        Labels come from user extra_patterns config; the one-pass unmask
        matches ``<[^<>]+>``, so brackets and spaces must not survive into
        the token.
        """
        return re.sub(r"[^A-Za-z0-9_]", "_", kind.upper()).strip("_") or "EXTRA"

    def _new_placeholder(self, kind: str) -> str:
        # Counters key on the canonical label, matching what
        # ``_derive_counters`` reads back out of restored placeholders —
        # a raw-kind key would reset to index 0 after a state round trip
        # and overwrite the earlier secret.
        label = self._canonical_label(kind)
        key = label.lower()
        index = self._counters.get(key, 0)
        self._counters[key] = index + 1
        return f"<{label}_{index}>"

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
        # One forward pass + join: O(L + N_m). Prefer start order (find_identifiers
        # already returns it); sort defensively if a caller passes unsorted spans.
        parts: list[str] = []
        cursor = 0
        for m in sorted(matches, key=lambda x: x.start):
            if m.start < cursor:
                continue
            parts.append(text[cursor : m.start])
            parts.append(self._ensure_placeholder(m.kind, m.value))
            cursor = m.end
        parts.append(text[cursor:])
        return "".join(parts)

    def unmask(self, text: str) -> str:
        """Restore known placeholders in ``text`` (single left-to-right scan).

        Token boundaries are ``<…>``, so ``<NAMESPACE_10>`` is never partially
        rewritten by a shorter key like ``<NAMESPACE_1>``. Replacement text is
        not re-scanned — originals that happen to contain angle-brackets stay
        literal (identifiers from detectors do not look like placeholders).
        """
        if not text or not self._placeholder_map:
            return text
        if "<" not in text:
            return text
        mapping = self._placeholder_map
        return _PLACEHOLDER_TOKEN_RE.sub(
            lambda match: mapping.get(match.group(0), match.group(0)),
            text,
        )

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


__all__ = ["MaskingRules"]
