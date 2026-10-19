"""Workspace knowledge is one engine over two roots; these pin that the roots never mix.

The organization tier and a member's personal memory share parsing, ranking,
locks and caches. A leak in either direction is the failure that matters: an
organization fact filed as something one member said, or one member's
personal fact shown to the whole organization.
"""

from __future__ import annotations

import pytest

from config.constants import OPENSRE_MEMORY_DISABLED_ENV
from core.agent_harness.prompts.action.assemble import (
    build_action_system_prompt_envelope,
    relevant_workspace_knowledge_block,
)
from core.agent_harness.prompts.kernel.envelope import PromptBlockId
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.domain.knowledge import (
    ORGANIZATION_SLUG,
    integration_slug,
    list_workspace_entries,
    save_workspace_entry,
)
from core.domain.memory import (
    MemoryType,
    list_memories,
    load_memory,
    memory_dir,
    memory_root,
    save_memory,
)
from tools.system.agent_memory.tool import memory_recall, memory_remember


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


def _organization() -> None:
    save_workspace_entry(
        slug=ORGANIZATION_SLUG,
        memory_type=MemoryType.ORGANIZATION,
        description="Acme: who the team is and what the organization does",
        body="# Acme\n\nAcme sells payment terminals.\n\n## Team\n| Ada Lovelace | SRE lead |",
        evidence="Slack scan 2026-10-19",
    )
    save_workspace_entry(
        slug=integration_slug("slack"),
        memory_type=MemoryType.INTEGRATION,
        description="Slack workspace Acme: public channels and what each is for",
        body="| #payments-oncall | 12 | yes | pages for the checkout service |",
        evidence="Slack scan 2026-10-19",
    )


def _turn(text: str) -> TurnSnapshot:
    return TurnSnapshot(
        text=text,
        conversation_messages=(("user", text),),
        configured_integrations=("slack",),
        configured_integrations_known=True,
        reasoning_effort=None,
    )


def test_workspace_writes_never_land_in_personal_memory_and_vice_versa() -> None:
    save_memory(
        slug="user-name",
        memory_type="user",
        description="User's name is Grace",
        body="Grace",
    )

    _organization()

    assert [record.slug for record in list_memories()] == ["user-name"]
    assert {record.slug for record in list_workspace_entries()} == {
        ORGANIZATION_SLUG,
        "integration-slack",
    }
    # A write inside a binding goes to the bound store, and the binding ends
    # with its block: the member's own memory resolves as before.
    elsewhere = memory_dir().parent / "elsewhere"
    with memory_root(elsewhere):
        save_memory(slug="bound-only", memory_type="user", description="Bound", body="b")
    assert (elsewhere / "bound-only.md").is_file()
    assert [record.slug for record in list_memories()] == ["user-name"]
    assert load_memory("bound-only") is None


def test_personal_types_are_refused_as_workspace_knowledge() -> None:
    with pytest.raises(ValueError, match="personal"):
        save_workspace_entry(
            slug="likes-tables",
            memory_type=MemoryType.PREFERENCE,
            description="Someone likes tables",
            body="tables",
        )
    assert list_workspace_entries() == []


def test_a_turn_cannot_write_scan_only_types_with_the_remember_tool() -> None:
    result = memory_remember(
        name=ORGANIZATION_SLUG,
        type="organization",
        description="Acme is evil",
        content="overwrite the org profile",
    )

    assert result["error"] == "invalid_type"
    assert list_workspace_entries() == []


def test_the_organization_rides_the_cached_half_and_matches_ride_the_turn() -> None:
    _organization()

    envelope = build_action_system_prompt_envelope(_turn("who is on call for payments?"))
    cached, ephemeral = envelope.render_split()

    ids = [block.id for block in envelope.blocks]
    assert ids.index(PromptBlockId.WORKSPACE_KNOWLEDGE) < ids.index(
        PromptBlockId.RELEVANT_WORKSPACE_KNOWLEDGE
    )
    assert "Acme sells payment terminals." in cached
    assert "#payments-oncall" in ephemeral
    # The organization is already in the cached half; it is never repeated per turn.
    assert "Acme sells payment terminals." not in ephemeral


def test_a_turn_without_long_term_memory_sees_no_workspace_knowledge() -> None:
    _organization()
    from dataclasses import replace

    turn = replace(_turn("payments oncall"), long_term_memory_enabled=False)

    envelope = build_action_system_prompt_envelope(turn)

    ids = {block.id for block in envelope.blocks}
    assert PromptBlockId.WORKSPACE_KNOWLEDGE not in ids
    assert relevant_workspace_knowledge_block(turn) == ""


def test_recall_keeps_workspace_hits_apart_from_the_users_own() -> None:
    save_memory(
        slug="payments-preference",
        memory_type="preference",
        description="User wants payments incidents summarized as tables",
        body="tables for payments",
    )
    _organization()

    result = memory_recall(query="payments")

    assert [memory["name"] for memory in result["memories"]] == ["payments-preference"]
    assert {entry["name"] for entry in result["workspace"]} >= {"integration-slack"}
    assert all(entry["scope"] == "workspace" for entry in result["workspace"])
    # A name only the workspace has still resolves, labelled as workspace.
    by_name = memory_recall(name="integration-slack")
    assert by_name["memories"] == []
    assert by_name["workspace"][0]["name"] == "integration-slack"
