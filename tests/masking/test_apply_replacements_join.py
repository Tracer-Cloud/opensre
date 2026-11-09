"""TDD: mask apply builds output via segment join (not reverse splice)."""

from __future__ import annotations

from infrastructure.safety.masking.detectors import DetectedIdentifier
from infrastructure.safety.masking.policy import MaskingPolicy
from infrastructure.safety.masking.rules import MaskingRules


def test_apply_replacements_preserves_gaps_and_order() -> None:
    # Arrange
    ctx = MaskingRules(policy=MaskingPolicy.model_validate({"enabled": True, "kinds": ()}))
    text = "aaa BBB ccc DDD eee"
    matches = [
        DetectedIdentifier(kind="x", start=4, end=7, value="BBB"),
        DetectedIdentifier(kind="y", start=12, end=15, value="DDD"),
    ]

    # Act
    masked = ctx._apply_replacements(text, matches)

    # Assert
    assert masked == "aaa <X_0> ccc <Y_0> eee"
    assert ctx.unmask(masked) == text


def test_apply_replacements_skips_overlapping_later_span() -> None:
    # Arrange — second span overlaps first; forward pass must keep the first.
    ctx = MaskingRules(policy=MaskingPolicy.model_validate({"enabled": True, "kinds": ()}))
    text = "abcdefghij"
    matches = [
        DetectedIdentifier(kind="long", start=0, end=6, value="abcdef"),
        DetectedIdentifier(kind="short", start=3, end=8, value="defgh"),
    ]

    # Act
    masked = ctx._apply_replacements(text, matches)

    # Assert
    assert masked == "<LONG_0>ghij"
    assert "<SHORT_" not in masked


def test_mask_end_to_end_with_many_non_overlapping_hits() -> None:
    """Public mask() path must survive a dense replacement set (join apply)."""
    policy = MaskingPolicy.model_validate(
        {
            "enabled": True,
            "kinds": (),
            "extra_patterns": {"tok": r"\b(T\d+)\b"},
        }
    )
    ctx = MaskingRules(policy=policy)
    original = "start T1 mid T2 end T3"
    masked = ctx.mask(original)
    assert masked.count("<TOK_") == 3
    assert "T1" not in masked
    assert ctx.unmask(masked) == original
