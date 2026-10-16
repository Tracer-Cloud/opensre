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
