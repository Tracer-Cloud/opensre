"""Only full members of the organization read its workspace knowledge.

Review finding: an open workspace admits Slack guests and Slack Connect users,
and the workspace tier was gated on feature flags alone, so they received the
organization profile built from public channels they cannot see.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, cast

import pytest

from config.constants import OPENSRE_MEMORY_DISABLED_ENV, OPENSRE_MEMORY_GATEWAY_ENABLED_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.prompts.action.assemble import workspace_knowledge_block
from core.domain.knowledge import (
    ORGANIZATION_SLUG,
    knowledge_available_here,
    record_workspace_readers,
    save_workspace_entry,
)
from core.domain.memory import MemoryType
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from tools.system.agent_memory.tool import memory_recall


@pytest.fixture(autouse=True)
def _organization_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    monkeypatch.setenv(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV, "1")
    save_workspace_entry(
        slug=ORGANIZATION_SLUG,
        memory_type=MemoryType.ORGANIZATION,
        description="Acme: who the team is",
        body="# Acme\n\nAcme sells payment terminals.",
    )
    assert record_workspace_readers("slack", ["U_MEMBER"])


def _turn_as(actor: str) -> AbstractContextManager[StorageScope]:
    return bound_storage_scope(StorageScope(principal=Principal.org("org_A"), actor=Actor(actor)))


@pytest.mark.parametrize("actor", ["U_GUEST", "W_CONNECT_USER", "1234567890"])
def test_a_slack_user_the_scan_did_not_list_as_a_member_reads_nothing(actor: str) -> None:
    with bound_usage_context(surface=UsageSurface.SLACK), _turn_as(actor):
        assert not knowledge_available_here()
        assert workspace_knowledge_block() == ""
        assert "workspace" not in memory_recall(query="payment terminals")


@pytest.mark.parametrize("actor", ["U_MEMBER", "user_2abcdefghijklmn"])
def test_full_members_and_app_members_read_it(actor: str) -> None:
    with bound_usage_context(surface=UsageSurface.SLACK), _turn_as(actor):
        assert knowledge_available_here()
        assert "Acme sells payment terminals." in workspace_knowledge_block()


def test_a_member_removed_by_the_next_scan_loses_access() -> None:
    assert record_workspace_readers("slack", ["U_SOMEONE_ELSE"])

    with bound_usage_context(surface=UsageSurface.SLACK), _turn_as("U_MEMBER"):
        assert not knowledge_available_here()


def test_a_guest_cannot_read_workspace_knowledge_through_the_slash_command() -> None:
    # Review finding: gateways expose /knowledge, which checked only that the
    # feature was on, so a guest could print the organization profile.
    from io import StringIO

    from rich.console import Console

    from surfaces.interactive_shell.command_registry.knowledge_cmds import COMMANDS

    [command] = COMMANDS
    for actor, visible in (("U_GUEST", False), ("U_MEMBER", True)):
        out = StringIO()
        with bound_usage_context(surface=UsageSurface.SLACK), _turn_as(actor):
            command.handler(cast(Any, None), Console(file=out, width=200), ["show", "organization"])
        assert ("Acme sells payment terminals." in out.getvalue()) is visible


@pytest.mark.parametrize(("actor", "visible"), [("U_GUEST", False), ("U_MEMBER", True)])
def test_a_cli_child_a_guest_turn_started_reads_as_that_guest(
    monkeypatch: pytest.MonkeyPatch, actor: str, visible: bool
) -> None:
    # Review finding: `opensre knowledge show` run by a turn's shell saw no
    # bound scope and read as the deployment, so a guest's turn printed it.
    from config.constants.tenancy import TURN_ACTOR_ID_ENV, TURN_ORGANIZATION_ID_ENV
    from core.domain.knowledge import readable_workspace_entry

    monkeypatch.setenv(TURN_ORGANIZATION_ID_ENV, "org_A")
    monkeypatch.setenv(TURN_ACTOR_ID_ENV, actor)

    assert (readable_workspace_entry(ORGANIZATION_SLUG) is not None) is visible
