"""Recognize JSON/record data blobs so callers can keep them out of prose.

One detection mechanism; each caller passes its own policy. A tool-result
payload is hidden aggressively (any short JSON is data), while a paragraph in a
model reply is collapsed conservatively (only a large, dense block) so real
prose is never mistaken for a dump. Keeping the mechanism here stops the two
policies from drifting apart as they did when each hand-rolled its own check.
"""

from __future__ import annotations

_KEY_SEPARATOR = '":'
_STRUCTURAL_CHARS = frozenset('{}[]":,')


def looks_like_data_blob(
    text: str,
    *,
    min_json_keys: int,
    min_chars: int = 0,
    honor_open_bracket: bool = False,
    structural_ratio: float | None = None,
    truncation_marker: str | None = None,
) -> bool:
    """Whether *text* reads as a JSON/record blob under the caller's policy.

    ``min_json_keys`` is the count of ``":`` key separators that marks it as
    data; these survive long URL values that would dilute a raw character
    ratio. ``honor_open_bracket`` treats text opening with ``{``/``[`` as data
    outright. ``min_chars`` sets a size floor below which nothing is a blob.
    ``structural_ratio`` catches non-JSON dense data by structural-char density.
    ``truncation_marker`` matches the substring a tool output cap leaves behind.
    """
    stripped = text.strip()
    if not stripped or len(stripped) < min_chars:
        return False
    if honor_open_bracket and stripped[0] in "{[":
        return True
    if truncation_marker and truncation_marker in stripped:
        return True
    if stripped.count(_KEY_SEPARATOR) >= min_json_keys:
        return True
    if structural_ratio is not None:
        structural = sum(1 for ch in stripped if ch in _STRUCTURAL_CHARS)
        return structural / len(stripped) >= structural_ratio
    return False


def is_data_blob(text: str) -> bool:
    """Whether a tool-result payload is a JSON/record blob to keep out of view.

    The aggressive policy shared by the transcript hider and the inline result
    preview: any payload opening with ``{``/``[``, or carrying two ``":`` key
    separators, is data the reply already summarizes — valid, truncated, or a
    mid-object fragment. The reply-prose collapse uses ``looks_like_data_blob``
    directly with a larger floor so it never mistakes real prose for a dump.
    """
    return looks_like_data_blob(text, min_json_keys=2, honor_open_bracket=True)


__all__ = ["is_data_blob", "looks_like_data_blob"]
