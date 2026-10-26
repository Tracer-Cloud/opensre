"""Vendor-fragment and stripper registry mechanics in the harness port layer.

These pin the contract core prompt builders rely on: fragments join in
registration order with a fixed separator, an empty registry renders nothing
rather than raising, and the first matching context stripper wins.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import infrastructure.harness_providers as harness_providers


@pytest.fixture(autouse=True)
def _empty_registries() -> Iterator[None]:
    harness_providers.reset_harness_providers()
    yield
    harness_providers.reset_harness_providers()


def test_action_fragments_join_in_registration_order() -> None:
    # Arrange
    harness_providers.register_action_prompt_fragment(lambda: "FIRST")
    harness_providers.register_action_prompt_fragment(lambda: "SECOND")

    # Act
    joined = harness_providers.action_prompt_vendor_fragments()

    # Assert: order is registration order, separated by a blank line.
    assert joined == "FIRST\n\nSECOND"


def test_empty_fragment_registries_render_empty_string() -> None:
    # Assert: a surface with no integrations wired must not raise.
    assert harness_providers.action_prompt_vendor_fragments() == ""
    assert harness_providers.assistant_prompt_vendor_fragments() == ""
    assert harness_providers.gateway_persona_fragments() == ""


def test_first_matching_context_stripper_wins() -> None:
    # Arrange: two strippers match the same text; only the first may apply.
    harness_providers.register_message_context_prefix_stripper(
        lambda text: ("FIRST-PREFIX", "first-remainder") if text.startswith("[X") else None
    )
    harness_providers.register_message_context_prefix_stripper(
        lambda text: ("SECOND-PREFIX", "second-remainder") if text.startswith("[X") else None
    )

    # Act
    prefix, remainder = harness_providers.strip_message_context_prefix("[X] hello")

    # Assert
    assert prefix == "FIRST-PREFIX"
    assert remainder == "first-remainder"


def test_context_stripper_falls_back_to_untouched_text() -> None:
    # Arrange: a stripper that never matches must not consume the message.
    harness_providers.register_message_context_prefix_stripper(lambda _text: None)

    # Act
    prefix, remainder = harness_providers.strip_message_context_prefix("plain question")

    # Assert
    assert prefix == ""
    assert remainder == "plain question"


def test_repo_scope_enrichment_without_providers_returns_a_copy() -> None:
    # Arrange: no VCS providers registered.
    base = {"datadog": {"connection_verified": True}}

    # Act
    out = harness_providers.enrich_resolved_with_repo_scopes(
        resolved=base,
        message="",
        conversation_messages=[],
        env={},
        cwd=None,
        cached_scopes={},
    )

    # Assert: same content, but mutating the result must not touch the input.
    assert out == base
    out["injected_marker"] = {"leaked": True}
    assert "injected_marker" not in base
