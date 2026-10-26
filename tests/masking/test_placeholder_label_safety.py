"""Placeholder labels must uphold the one-pass unmask token invariant.

The single-scan unmask matches ``<[^<>]+>``. That is only correct if every
generated placeholder is exactly one bracket pair — so a label taken from user
``extra_patterns`` config must be sanitized before it is interpolated. A raw
label like ``tok>x`` would mint ``<TOK>X_0>``, which the scan can never match:
the secret would stay masked in user-facing output.
"""

from __future__ import annotations

import re

from infrastructure.safety.masking.policy import MaskingPolicy
from infrastructure.safety.masking.rules import MaskingRules

_SINGLE_TOKEN = re.compile(r"^<[A-Z0-9_]+>$")

PLANTED_SECRET = "value-that-must-round-trip-7d1"


def _context_with_extra(label: str) -> MaskingRules:
    policy = MaskingPolicy(
        enabled=True,
        extra_patterns={label: re.escape(PLANTED_SECRET)},
    )
    return MaskingRules(policy=policy)


def test_hostile_label_still_mints_a_single_bracket_token() -> None:
    # Arrange
    context = _context_with_extra("tok>x")

    # Act
    masked = context.mask(f"before {PLANTED_SECRET} after")

    # Assert: whatever the placeholder is, it is one clean token.
    (token,) = re.findall(r"<[^ ]*>", masked)
    assert _SINGLE_TOKEN.match(token), f"label leaked into token: {token!r}"
    assert PLANTED_SECRET not in masked


def test_hostile_label_round_trips_through_unmask() -> None:
    """The user-facing path: mask then unmask must restore the original."""
    # Arrange
    context = _context_with_extra("tok>x")
    original = f"the value is {PLANTED_SECRET}."

    # Act
    restored = context.unmask(context.mask(original))

    # Assert
    assert restored == original


def test_angle_brackets_and_spaces_in_labels_are_neutralized() -> None:
    # Arrange / Act / Assert — a few shapes users actually type.
    for label in ("<already>", "two words", "a<b>c", "trailing>"):
        context = _context_with_extra(label)
        original = f"x {PLANTED_SECRET} y"
        assert context.unmask(context.mask(original)) == original, (
            f"label {label!r} broke the mask/unmask round trip"
        )


def test_restored_context_does_not_reuse_indices_for_sanitized_labels(
    monkeypatch,
) -> None:
    """Counters must key on the sanitized label, or a state round trip
    re-mints index 0 and silently overwrites the earlier secret."""
    # Arrange: a hyphenated label via env, because from_state re-reads the
    # policy from the environment.
    import json as _json

    monkeypatch.setenv("OPENSRE_MASK_ENABLED", "1")
    monkeypatch.setenv("OPENSRE_MASK_EXTRA_REGEX", _json.dumps({"jira-key": r"AAA-\d+"}))
    first = MaskingRules(policy=MaskingPolicy.from_env())
    masked_one = first.mask("see AAA-111")
    restored = MaskingRules.from_state({"masking_map": first.to_state()})

    # Act: the restored context masks a second, different value.
    masked_two = restored.mask("see AAA-222")

    # Assert: distinct values, distinct tokens, both restorable.
    (token_one,) = re.findall(r"<[^<>]+>", masked_one)
    (token_two,) = re.findall(r"<[^<>]+>", masked_two)
    assert token_one != token_two, "index reuse — second secret overwrote the first"
    assert restored.unmask(masked_one) == "see AAA-111"
    assert restored.unmask(masked_two) == "see AAA-222"


def test_kinds_that_sanitize_alike_do_not_clobber_each_other() -> None:
    # Arrange: two labels collapsing to the same sanitized form.
    policy = MaskingPolicy(
        enabled=True,
        extra_patterns={"a-b": r"S1VALUE", "a.b": r"S2VALUE"},
    )
    context = MaskingRules(policy=policy)

    # Act
    masked = context.mask("first S1VALUE then S2VALUE")

    # Assert: two distinct tokens, both round-trip.
    tokens = re.findall(r"<[^<>]+>", masked)
    assert len(set(tokens)) == 2, f"tokens collapsed: {tokens}"
    assert context.unmask(masked) == "first S1VALUE then S2VALUE"
