"""Where the REPOSITORY INSTRUCTIONS block sits in the action prompt, and who gets it."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness.prompts import (
    PromptBlockId,
    PromptBlockKind,
    PromptTier,
    build_action_system_prompt_envelope,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from infrastructure.harness_providers import (
    RemoteInstructions,
    RemoteInstructionsStatus,
    register_repository_instructions_source,
    reset_harness_providers,
)


class _RemoteOnlySource:
    """Serves one default-branch AGENTS.md for every repository; no local checkout."""

    vendor = "github"
    label = "GitHub"

    def checkout_matches(self, repository: str, root: Path) -> bool:
        _ = (repository, root)
        return False

    def credential_scope(self, resolved_integrations: Mapping[str, Any]) -> str | None:
        _ = resolved_integrations
        return "scope"

    def fetch(
        self, repository: str, resolved_integrations: Mapping[str, Any]
    ) -> RemoteInstructions:
        _ = (repository, resolved_integrations)
        return RemoteInstructions(
            RemoteInstructionsStatus.FOUND,
            content=b"Run make check before pushing.",
            origin="GitHub default branch",
        )


@pytest.fixture(autouse=True)
def _remote_only_source() -> Iterator[None]:
    reset_harness_providers()
    register_repository_instructions_source(_RemoteOnlySource())
    yield
    reset_harness_providers()


def _snapshot(messages: tuple[tuple[str, str], ...] = (("user", "hello"),)) -> TurnSnapshot:
    return TurnSnapshot(
        text="fix the failing check",
        conversation_messages=messages,
        configured_integrations=("github",),
        configured_integrations_known=True,
        reasoning_effort=None,
        active_vcs_repositories={"github": "acme/payments"},
        known_vcs_repositories={"github": ("acme/payments",)},
    )


def test_the_block_follows_repository_context_in_the_cached_context_tier() -> None:
    # Arrange
    first = _snapshot()
    second = _snapshot((("user", "hello"), ("assistant", "hi"), ("user", "and again")))

    # Act
    envelope = build_action_system_prompt_envelope(first)
    ids = [block.id for block in envelope.blocks]
    block = envelope.require_block(PromptBlockId.REPOSITORY_INSTRUCTIONS)

    # Assert: right after the repository it describes, and part of the cached
    # prefix, which stays byte-identical while only the conversation changes.
    assert ids.index(PromptBlockId.REPOSITORY_INSTRUCTIONS) == (
        ids.index(PromptBlockId.REPOSITORY_CONTEXT) + 1
    )
    assert block.kind is PromptBlockKind.CONTEXT
    assert block.tier is PromptTier.CONTEXT
    assert "Run make check before pushing." in envelope.render_cached()
    assert "Run make check before pushing." not in envelope.render_ephemeral()
    assert envelope.render_cached() == build_action_system_prompt_envelope(second).render_cached()


@pytest.mark.parametrize("surface", ["gateway", "headless_cli", "interactive_shell", None])
def test_every_surface_gets_the_block_even_without_skill_discovery(surface: str | None) -> None:
    # Arrange: scheduled ticks turn skill discovery off; shared chats hide
    # setup state. Neither hides repository content.
    snapshot = replace(_snapshot(), prompt_surface=surface, skill_discovery_enabled=False)

    # Act
    block = build_action_system_prompt_envelope(snapshot).block(
        PromptBlockId.REPOSITORY_INSTRUCTIONS
    )

    # Assert
    assert block is not None
    assert "Run make check before pushing." in block.content


def test_no_active_repository_adds_no_block() -> None:
    # Arrange
    snapshot = replace(_snapshot(), active_vcs_repositories={})

    # Act
    envelope = build_action_system_prompt_envelope(snapshot)

    # Assert
    assert envelope.block(PromptBlockId.REPOSITORY_INSTRUCTIONS) is None
