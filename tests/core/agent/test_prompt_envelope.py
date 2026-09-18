from __future__ import annotations

from dataclasses import replace

import pytest

from core.agent_harness.prompts import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
    build_action_system_prompt,
    build_action_system_prompt_envelope,
    build_action_user_message,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _ctx() -> TurnSnapshot:
    return TurnSnapshot(
        text="show connected integrations",
        conversation_messages=(("user", "hello"),),
        configured_integrations=("github",),
        configured_integrations_known=True,
        reasoning_effort=None,
    )


def test_prompt_envelope_renders_existing_string_prompt_without_changes() -> None:
    envelope = PromptEnvelope.from_text("line one\n\nline two")

    assert envelope.render() == "line one\n\nline two"
    assert envelope.require_block("prompt").content == "line one\n\nline two"


def test_prompt_envelope_renders_ordered_blocks_with_optional_titles() -> None:
    envelope = PromptEnvelope.from_blocks(
        (
            PromptBlock(id="rules", kind="rule", content="Follow the rules."),
            PromptBlock(
                id="cli-reference",
                kind="context",
                title="CLI reference",
                content="opensre --help",
                include_title=True,
            ),
        ),
        separator="\n\n",
    )

    assert envelope.render() == "Follow the rules.\n\n--- CLI reference ---\nopensre --help"
    assert envelope.block("cli-reference") is not None
    with pytest.raises(KeyError, match="missing"):
        envelope.require_block("missing")


def test_action_system_prompt_envelope_matches_legacy_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.constants import OPENSRE_MEMORY_DISABLED_ENV

    monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
    ctx = _ctx()
    envelope = build_action_system_prompt_envelope(ctx)

    # ACTION_VENDOR_FRAGMENTS carries integration-owned prompt recipes
    # (e.g. Slack/GitHub action routing) registered via
    # infrastructure.harness_providers.register_action_prompt_fragment — see
    # integrations/harness_adapters.py. It renders empty (and is absent from
    # this id list) when no fragments are registered.
    assert [block.id for block in envelope.blocks] == [
        PromptBlockId.ACTION_SYSTEM_BASE,
        PromptBlockId.ACTION_GOAL_KERNEL,
        PromptBlockId.ACTION_VENDOR_FRAGMENTS,
        PromptBlockId.ACTION_RUNTIME_FACTS,
        PromptBlockId.ACTION_SKILLS,
        PromptBlockId.CONNECTED_INTEGRATIONS,
        PromptBlockId.ACTION_GOAL_KERNEL_CLOSER,
        PromptBlockId.TURN_INTERACTION,
        PromptBlockId.RECENT_CONVERSATION,
    ]
    assert (
        envelope.require_block(PromptBlockId.ACTION_VENDOR_FRAGMENTS).kind == PromptBlockKind.RULE
    )
    # The action agent can answer the user, so it carries the same host facts
    # the assistant does rather than guessing them.
    assert (
        envelope.require_block(PromptBlockId.ACTION_RUNTIME_FACTS).kind == PromptBlockKind.CONTEXT
    )
    assert envelope.require_block(PromptBlockId.ACTION_SKILLS).kind == PromptBlockKind.RULE
    assert (
        envelope.require_block(PromptBlockId.CONNECTED_INTEGRATIONS).kind == PromptBlockKind.CONTEXT
    )
    assert (
        envelope.require_block(PromptBlockId.RECENT_CONVERSATION).kind
        == PromptBlockKind.CONVERSATION
    )
    assert envelope.render() == build_action_system_prompt(ctx)


def test_repository_context_is_a_named_context_block() -> None:
    ctx = replace(
        _ctx(),
        active_vcs_repositories={"github": "acme/payments"},
        known_vcs_repositories={"github": ("Tracer-Cloud/opensre", "acme/payments")},
    )

    block = build_action_system_prompt_envelope(ctx).require_block(PromptBlockId.REPOSITORY_CONTEXT)
    assert block.kind == PromptBlockKind.CONTEXT
    assert block.tier == PromptTier.CONTEXT
    assert "active=acme/payments" in block.content


def _turn(messages: list[tuple[str, str]]) -> TurnSnapshot:
    """A snapshot differing from another only in conversation history."""
    return TurnSnapshot(
        text="show connected integrations",
        conversation_messages=tuple(messages),
        configured_integrations=("github",),
        configured_integrations_known=True,
        reasoning_effort=None,
    )


def test_the_cached_prefix_is_byte_identical_across_turns() -> None:
    """Two turns in one session must share a byte-identical cacheable prefix.

    Anthropic caches the request prefix up to the ``cache_control`` marker. A
    system prompt whose tail changes every turn invalidates everything before
    it, so the whole base+skills prefix is re-sent at full price on every turn
    rather than read from cache. This is the property that makes caching work.
    """
    # Arrange
    first = _turn([("user", "hello")])
    second = _turn([("user", "hello"), ("assistant", "hi"), ("user", "and again")])

    # Act
    first_cached = build_action_system_prompt_envelope(first).render_cached()
    second_cached = build_action_system_prompt_envelope(second).render_cached()

    # Assert
    assert first_cached == second_cached


def test_per_turn_conversation_never_reaches_the_cached_prefix() -> None:
    """A distinctive utterance must not appear in the cached half.

    Anything that leaks here silently costs a full-price re-send of the entire
    prefix, which no other test would catch.
    """
    # Arrange
    marker = "zzmarker-utterance-that-must-not-be-cached"
    envelope = build_action_system_prompt_envelope(_turn([("user", marker)]))

    # Act
    cached = envelope.render_cached()
    ephemeral = envelope.render_ephemeral()

    # Assert
    assert marker not in cached
    assert marker in ephemeral


def test_the_split_halves_reassemble_into_the_unchanged_render() -> None:
    """``render()`` output must not move while the halves are introduced.

    Stated as the general contract — join the non-empty halves with the
    envelope's own separator — so the assertion holds for any envelope, not
    only one that happens to separate with the empty string. Requires every
    ephemeral block to follow every cached block in declaration order.
    """
    # Arrange
    envelope = build_action_system_prompt_envelope(_turn([("user", "hello")]))

    # Act
    cached, ephemeral = envelope.render_split()
    rejoined = envelope.separator.join(half for half in (cached, ephemeral) if half)

    # Assert
    assert rejoined == envelope.render()


def test_every_block_declares_which_tier_it_belongs_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A block with no tier would silently land in the cached prefix."""
    from config.constants import OPENSRE_MEMORY_DISABLED_ENV

    monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
    # Arrange
    envelope = build_action_system_prompt_envelope(_turn([("user", "hello")]))

    # Act
    tiers = {block.id: block.tier for block in envelope.blocks}

    # Assert
    assert tiers == {
        PromptBlockId.ACTION_SYSTEM_BASE: PromptTier.STABLE,
        PromptBlockId.ACTION_GOAL_KERNEL: PromptTier.STABLE,
        PromptBlockId.ACTION_VENDOR_FRAGMENTS: PromptTier.STABLE,
        PromptBlockId.ACTION_RUNTIME_FACTS: PromptTier.STABLE,
        PromptBlockId.ACTION_SKILLS: PromptTier.STABLE,
        PromptBlockId.CONNECTED_INTEGRATIONS: PromptTier.CONTEXT,
        PromptBlockId.ACTION_GOAL_KERNEL_CLOSER: PromptTier.EPHEMERAL,
        PromptBlockId.TURN_INTERACTION: PromptTier.EPHEMERAL,
        PromptBlockId.RECENT_CONVERSATION: PromptTier.EPHEMERAL,
    }


def test_the_action_envelope_exposes_a_stable_half_the_provider_can_cache() -> None:
    """The split is what a provider needs to place a cache breakpoint.

    Pins the half the action driver sends as ``system`` (with
    ``cache_control`` at the provider boundary): it is the overwhelming
    majority of the prompt and identical between turns.
    """
    # Arrange
    first = _turn([("user", "hello")])
    second = _turn([("user", "hello"), ("assistant", "hi"), ("user", "again")])

    # Act
    first_cached, first_ephemeral = build_action_system_prompt_envelope(first).render_split()
    second_cached, _ = build_action_system_prompt_envelope(second).render_split()

    # Assert
    assert first_cached == second_cached
    assert len(first_cached) > 20 * len(first_ephemeral)


def test_the_rendered_prompt_is_unchanged_by_the_split() -> None:
    """``render()`` must stay the join of the two halves.

    Callers that have not opted into the split still see one string; the
    driver's opt-in only relocates the ephemeral half into the user turn.
    """
    # Arrange
    snapshot = _turn([("user", "hello")])

    # Act
    rendered = build_action_system_prompt_envelope(snapshot).render()

    # Assert
    assert rendered == build_action_system_prompt(snapshot)


def test_driver_puts_conversation_on_the_user_turn_not_system() -> None:
    """Phase D: per-turn text must leave ``system`` so the cache can stick.

    Same words the model used to read at the end of the system prompt now
    prefix the user message. A distinctive utterance must appear only there.
    """
    # Arrange
    marker = "zzmarker-utterance-that-must-leave-system"
    snapshot = _turn([("user", marker)])
    envelope = build_action_system_prompt_envelope(snapshot)

    # Act — same split the action driver uses
    system = envelope.render_cached()
    user_message = build_action_user_message("follow up", prefix=envelope.render_ephemeral())

    # Assert
    assert marker not in system
    assert marker in user_message
    assert "USER MESSAGE (literal): <<<follow up>>>" in user_message


def test_driver_system_prefix_is_byte_identical_across_turns() -> None:
    """Phase D proof: the defect from phase A is gone at the driver seam."""
    # Arrange
    first = _turn([("user", "hello")])
    second = _turn([("user", "hello"), ("assistant", "hi"), ("user", "and again")])

    # Act
    first_system = build_action_system_prompt_envelope(first).render_cached()
    second_system = build_action_system_prompt_envelope(second).render_cached()

    # Assert
    assert first_system == second_system
    assert first_system  # non-empty cached prefix is what we mark for cache


def test_the_literal_user_message_leads_and_ephemeral_history_follows() -> None:
    """Order here decides what an over-budget turn loses.

    ``core.context_budget._shrink_text`` keeps the head and drops the tail, so
    the literal instruction has to come first. With history leading, a turn over
    budget kept stale chatter and cut the request that started it — the planner
    then acted on the wrong thing entirely.
    """
    # Arrange
    envelope = build_action_system_prompt_envelope(_turn([("user", "zzmarker-prefix-envelope")]))

    # Act
    user_message = build_action_user_message("run /health", prefix=envelope.render_ephemeral())

    # Assert
    assert user_message.startswith("USER MESSAGE (literal): <<<run /health>>>")
    assert "zzmarker-prefix-envelope" in user_message
    assert user_message.index("run /health") < user_message.index("RECENT CONVERSATION")
    assert user_message.count("USER MESSAGE (literal):") == 1


def test_newest_conversation_survives_head_preserving_truncation() -> None:
    """Ephemeral history is newest-first so the tail cut drops stale turns."""
    from core.context_budget import _shrink_text

    envelope = build_action_system_prompt_envelope(
        _turn(
            [
                ("user", "zzmarker-oldest-turn"),
                ("assistant", "old reply " + ("x" * 2000)),
                ("user", "zzmarker-newest-turn"),
                ("assistant", "fresh reply"),
            ]
        )
    )
    message = build_action_user_message(
        "follow up on that",
        prefix=envelope.render_ephemeral(),
    )
    # Cut just after the newest turn pair; head-preserving shrink must keep
    # that pair and drop the older turn that now sits at the tail.
    end_newest = message.index("Assistant: fresh reply") + len("Assistant: fresh reply")
    shrunk, truncated = _shrink_text(message, end_newest + 40)

    assert truncated
    assert "follow up on that" in shrunk
    assert "zzmarker-newest-turn" in shrunk
    assert "zzmarker-oldest-turn" not in shrunk


def test_split_reassembles_when_long_term_memory_is_present(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Volatile memory must sit before ephemeral so join(cached, eph) == render()."""
    from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
    from core.domain.memory import save_memory

    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    save_memory(
        slug="smoke-profile",
        memory_type="user",
        description="Name is Smoke",
        body="The user's name is Smoke for prompt-tier ordering.",
    )

    marker = "zzmarker-memory-order-utterance"
    envelope = build_action_system_prompt_envelope(_turn([("user", marker)]))
    ids = [block.id for block in envelope.blocks]
    assert PromptBlockId.LONG_TERM_MEMORY in ids
    assert ids.index(PromptBlockId.LONG_TERM_MEMORY) < ids.index(PromptBlockId.RECENT_CONVERSATION)

    cached, ephemeral = envelope.render_split()
    rejoined = envelope.separator.join(half for half in (cached, ephemeral) if half)
    assert rejoined == envelope.render()
    assert "LONG-TERM MEMORY" in cached
    assert "RECENT CONVERSATION" in ephemeral
    assert marker in ephemeral
    assert marker not in cached


def test_every_tier_lands_in_exactly_one_half() -> None:
    """The halves must partition the blocks whatever tiers exist.

    Selecting the cached half by set membership and the ephemeral half by
    equality partitions only while the enum has exactly today's members. A tier
    added later would fall in neither half and vanish from the prompt with
    nothing failing, so the invariant is asserted over every enum member rather
    than over the four blocks the action prompt happens to build.
    """
    # Arrange
    from core.agent_harness.prompts import PromptTier

    envelope = PromptEnvelope.from_blocks(
        [
            PromptBlock(id=tier.value, content=f"marker-{tier.value}", tier=tier)
            for tier in PromptTier
        ],
        separator="\n",
    )

    # Act
    cached, ephemeral = envelope.render_split()

    # Assert
    for tier in PromptTier:
        marker = f"marker-{tier.value}"
        assert (marker in cached) ^ (marker in ephemeral), f"{tier} landed in neither or both"


def test_layers_are_emitted_in_tier_order_however_blocks_were_added() -> None:
    """The three layers must hold by construction, not by authoring discipline.

    Prompt blocks join the prompt ``stable → context → volatile``. Today that
    order survives only because the builder happens to append in that sequence —
    a volatile block added after an ephemeral one would silently break the layer
    contract and land inside the churn. Rendering sorts by tier so the layout is a
    property of the envelope, not of the order someone typed.
    """
    # Arrange — deliberately scrambled
    from core.agent_harness.prompts import PromptTier

    scrambled = PromptEnvelope.from_blocks(
        [
            PromptBlock(id="e", content="EPH", tier=PromptTier.EPHEMERAL),
            PromptBlock(id="v", content="VOL", tier=PromptTier.VOLATILE),
            PromptBlock(id="s", content="STA", tier=PromptTier.STABLE),
            PromptBlock(id="c", content="CTX", tier=PromptTier.CONTEXT),
        ],
        separator="|",
    )

    # Act
    rendered = scrambled.render()

    # Assert
    assert rendered == "STA|CTX|VOL|EPH"


def test_blocks_sharing_a_tier_keep_the_order_they_were_added() -> None:
    """Sorting must be stable, or skills could outrank the base prompt."""
    # Arrange
    from core.agent_harness.prompts import PromptTier

    envelope = PromptEnvelope.from_blocks(
        [
            PromptBlock(id="base", content="BASE", tier=PromptTier.STABLE),
            PromptBlock(id="skills", content="SKILLS", tier=PromptTier.STABLE),
        ],
        separator="|",
    )

    # Act / Assert
    assert envelope.render() == "BASE|SKILLS"
