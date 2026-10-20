"""Scheduling a recurring prompt from a chat turn, and stopping it.

A Slack user asked for recurring repo checks and got loops that posted to a
local inbox file on the gateway, or an offer refused because no weather fetch
had run. These pin delivery back to the asking chat and organization isolation.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import infrastructure.scheduling.scheduler.loops as loops
import tools.interactive_shell.actions.propose_scheduled_delivery as propose
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.tools.tool_context import ActionToolScope
from core.agent_harness.turns.headless_adapters import InMemorySessionState
from infrastructure.scheduling.scheduler.delivery_plan import configured_target_keys
from infrastructure.scheduling.scheduler.run_now import install_loop_run_starter
from infrastructure.scheduling.scheduler.storage import get_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.scheduled_loops import manage


@pytest.fixture(autouse=True)
def _scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every scheduler module that holds the home directory at ``tmp_path``."""
    home = tmp_path / "opensre-home"
    for info in pkgutil.walk_packages(scheduler_pkg.__path__, f"{scheduler_pkg.__name__}."):
        module = importlib.import_module(info.name)
        if hasattr(module, "OPENSRE_HOME_DIR"):
            monkeypatch.setattr(module, "OPENSRE_HOME_DIR", home)


def _slack_bot_token(_params: dict[str, str]) -> dict[str, str]:
    return {"access_token": "xoxb-test"}


def _chat_session(platform: str, chat_id: str) -> Any:
    return SimpleNamespace(
        resolved_integrations_cache={"_gateway_platform": platform, "_gateway_chat_id": chat_id}
    )


def test_a_prompt_scheduled_from_slack_posts_back_to_that_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a Slack DM turn on a gateway whose bot token can post.
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_token)
    monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "D0915KH"))

    # Act
    result = manage.schedule_prompt_loop(
        prompt="Check Tracer-Cloud/opensre for branches with failed CI and list them.",
        time="09:00",
        weekdays=True,
        timezone="Europe/Warsaw",
    )

    # Assert: stored for Slack delivery to the asking channel, not the local inbox.
    assert result["ok"] is True, result
    task = get_task(result["loop_id"])
    assert task is not None
    assert task.enabled
    assert task.cron == "0 9 * * mon-fri"
    assert configured_target_keys(task) == ((Provider.SLACK, "D0915KH"),)
    assert result["delivers_to"] == "slack:D0915KH"


def test_an_unreachable_chat_is_refused_instead_of_firing_into_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: Discord prompt loops have no delivery path.
    monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("discord", "123"))

    # Act
    result = manage.schedule_prompt_loop(prompt="Summarize open PRs.", time="9am")

    # Assert
    assert result["ok"] is False
    assert list_tasks() == []


def test_another_organizations_loop_cannot_be_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: org A schedules a loop in the shared store.
    monkeypatch.setattr(manage, "_session", lambda _context: SimpleNamespace())
    with bound_storage_scope(StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u1"))):
        created = manage.schedule_prompt_loop(prompt="Report failed CI on main.", time="08:00")
    assert created["ok"] is True, created

    # Act: org B names it.
    with bound_storage_scope(StorageScope(principal=Principal.org("org_B"), actor=Actor(id="u2"))):
        refused = manage.stop_scheduled_loop(created["loop_id"], action="delete")

    # Assert: reported as missing, and still scheduled for org A.
    assert refused["ok"] is False
    assert "not found" in refused["error"]
    assert get_task(created["loop_id"]) is not None


def test_a_prompt_loop_offer_needs_no_briefing_and_targets_the_chat() -> None:
    # Arrange: a Slack turn that fetched no weather or news.
    session = InMemorySessionState()
    session.resolved_integrations_cache = _chat_session("slack", "C42").resolved_integrations_cache
    ctx = ActionToolScope(session=session, console=object())

    # Act
    result = propose.execute_propose_scheduled_delivery_tool(
        {
            "kind": "manual_loop",
            "cron": "0 9 * * mon-fri",
            "prompt": "Check my repos for failed branches.",
        },
        ctx,
    )

    # Assert
    assert result["ok"] is True, result
    offer = session.pending_schedule_offer
    assert offer is not None
    assert (offer.provider, offer.chat_id) == ("slack", "C42")


def test_a_loop_edited_from_slack_keeps_its_id_and_moves_its_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a morning report scheduled from Slack at 9:00 UTC.
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_token)
    monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "C42"))
    created = manage.schedule_prompt_loop(prompt="Morning report.", time="09:00", name="Morning")
    assert created["ok"] is True, created

    # Act: the member moves it to 8:30 on weekdays, in their own timezone.
    edited = manage.edit_scheduled_loop(
        "Morning", time="8:30", weekdays=True, timezone="Europe/Warsaw"
    )

    # Assert: same loop, new schedule, still delivering to the same channel.
    assert edited["ok"] is True, edited
    task = get_task(created["loop_id"])
    assert task is not None
    assert (task.cron, task.timezone) == ("30 8 * * mon-fri", "Europe/Warsaw")
    assert configured_target_keys(task) == ((Provider.SLACK, "C42"),)


def test_an_ambiguous_name_never_lists_another_organizations_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: both organizations have a loop named "Morning report".
    monkeypatch.setattr(manage, "_session", lambda _context: SimpleNamespace())
    ids = {}
    for org in ("org_A", "org_B"):
        scope = StorageScope(principal=Principal.org(org), actor=Actor(id="u"))
        with bound_storage_scope(scope):
            ids[org] = manage.schedule_prompt_loop(
                prompt=f"{org} report.", time="09:00", name="Morning report"
            )["loop_id"]

    # Act: org A edits it by name.
    with bound_storage_scope(StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u"))):
        edited = manage.edit_scheduled_loop("Morning report", time="07:00")

    # Assert: org A's loop alone matched; org B's is untouched and never named.
    assert edited["ok"] is True, edited
    assert edited["loop_id"] == ids["org_A"]
    other = get_task(ids["org_B"])
    assert other is not None and other.cron == "0 9 * * *"


def _member(actor: str) -> StorageScope:
    return StorageScope(principal=Principal.org("org_A"), actor=Actor(id=actor))


def test_a_loop_asked_for_in_a_dm_is_private_and_one_in_a_channel_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_token)

    # Act: the same member asks in their DM and in #ops.
    with bound_storage_scope(_member("U0OWNER")):
        monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "D0915KH"))
        in_dm = manage.schedule_prompt_loop(prompt="Summarize my inboxes.", time="08:00")
        monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "C42"))
        in_channel = manage.schedule_prompt_loop(prompt="Summarize open PRs.", time="09:00")

    # Assert: the DM loop is theirs alone; the channel loop is the workspace's.
    assert (in_dm["ok"], in_dm["private"]) == (True, True), in_dm
    assert (in_channel["ok"], in_channel["private"]) == (True, False), in_channel


def test_only_the_creator_can_make_a_shared_loop_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anyone may rewrite a shared loop; making it private lets it run as its creator."""
    # Arrange: a shared #ops loop U0OWNER created.
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_token)
    monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "C42"))
    with bound_storage_scope(_member("U0OWNER")):
        created = manage.schedule_prompt_loop(prompt="Summarize open PRs.", time="09:00")

    # Act
    with bound_storage_scope(_member("U0OTHER")):
        refused = manage.edit_scheduled_loop(created["loop_id"], private=True)
    with bound_storage_scope(_member("U0OWNER")):
        accepted = manage.edit_scheduled_loop(created["loop_id"], private=True)

    # Assert
    assert refused["ok"] is False
    assert "Only the member who created" in refused["error"]
    assert (accepted["ok"], accepted["private"]) == (True, True), accepted


def test_a_member_runs_their_private_loop_now_and_nobody_else_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a private loop from U0OWNER's DM, and a host that starts runs.
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_token)
    monkeypatch.setattr(manage, "_session", lambda _context: _chat_session("slack", "D0915KH"))
    started: list[str] = []

    def start(loop_id: str) -> str:
        started.append(loop_id)
        return ""

    install_loop_run_starter(start)
    try:
        with bound_storage_scope(_member("U0OWNER")):
            created = manage.schedule_prompt_loop(prompt="Summarize my inboxes.", time="08:00")

        # Act
        with bound_storage_scope(_member("U0OTHER")):
            refused = manage.run_scheduled_loop(created["loop_id"])
        with bound_storage_scope(_member("U0OWNER")):
            ran = manage.run_scheduled_loop(created["loop_id"])
    finally:
        install_loop_run_starter(None)

    # Assert: another member reads it as missing; the owner's run started once.
    assert refused["ok"] is False
    assert "not found" in refused["error"]
    assert ran["ok"] is True, ran
    assert started == [created["loop_id"]]
