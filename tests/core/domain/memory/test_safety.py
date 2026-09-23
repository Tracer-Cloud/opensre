"""Tests for durable-memory safety redaction."""

from __future__ import annotations

from core.domain.memory.safety import redact_memory_unsafe_text


def test_redact_preserves_separator_when_value_echoes_the_label() -> None:
    """A value that repeats a prefix of its own label must not eat the separator.

    `_LABELED_SECRET_RE` can capture a label like `pass1234_secret_key` whose
    own text contains the secret value (`pass1234`). Searching for the value
    anywhere in the full match used to find that first, in-label occurrence
    instead of the real one after the delimiter, collapsing the separator.
    """
    redacted = redact_memory_unsafe_text("pass1234_secret_key: pass1234")
    assert redacted == "pass1234_secret_key: [REDACTED]"


def test_redact_labeled_secret_keeps_delimiter_shape() -> None:
    redacted = redact_memory_unsafe_text("password=Sup3rSecret!")
    assert redacted == "password=[REDACTED]"


def test_redact_leaves_benign_values_untouched() -> None:
    redacted = redact_memory_unsafe_text("password: unset")
    assert redacted == "password: unset"
