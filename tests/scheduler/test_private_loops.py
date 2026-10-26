"""A private loop is its owner's own: unattended runs act as them, and only they see it.

A member asked in a Slack DM for a weekday digest of their four Gmail inboxes.
Every run failed: the scheduler ran it as nobody, so the turn never saw the
inboxes connected to their account. These pin the owner acting on an
unattended run, and the isolation that makes that safe.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import infrastructure.scheduling.scheduler.tasks as tasks_mod
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CREATED_BY_PARAM,
    LOOP_MODE_PARAM,
    LOOP_PRIVATE_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.loop_owner import install_member_resolver
from infrastructure.scheduling.scheduler.loops import (
    create_manual_loop,
    list_loop_summaries,
    resolve_loop_summary,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import runners_with_agent

_ORG = "org_1"
_PROMPT = "Summarize what came into my inboxes overnight."


@pytest.fixture(autouse=True)
def _scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every scheduler module that holds the home directory at ``tmp_path``."""
    home = tmp_path / "opensre-home"
    for info in pkgutil.walk_packages(scheduler_pkg.__path__, f"{scheduler_pkg.__name__}."):
        module = importlib.import_module(info.name)
        if hasattr(module, "OPENSRE_HOME_DIR"):
            monkeypatch.setattr(module, "OPENSRE_HOME_DIR", home)


@pytest.fixture(autouse=True)
def _exact_ids_only() -> Iterator[None]:
    install_member_resolver(None)
    yield
    install_member_resolver(None)


def _member(actor: str) -> StorageScope:
    return StorageScope(principal=Principal.org(_ORG), actor=Actor(id=actor))


def _loop(*, private: bool | None) -> ScheduledTask:
    params = {
        LOOP_PROMPT_PARAM: _PROMPT,
        LOOP_MODE_PARAM: "agent",
        LOOP_CREATED_BY_PARAM: "U0OWNER",
    }
    if private is not None:
        params[LOOP_PRIVATE_PARAM] = "true" if private else "false"
    return ScheduledTask(
        id="loop-1",
        name="Inbox digest",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * 1-5",
        provider=Provider.INTERACTIVE_SHELL,
        organization=_ORG,
        params=params,
    )


def _run_scope(task: ScheduledTask) -> StorageScope | None:
    """The scope the tick's turn ran in."""
    seen: list[StorageScope | None] = []

    def run(_payload: dict[str, object]) -> str:
        seen.append(current_scope())
        return "report"

    tasks_mod.build_message(task, runners_with_agent(run))
    return seen[0]


class TestUnattendedRun:
    def test_a_private_loop_runs_as_its_owner(self) -> None:
        # Act
        scope = _run_scope(_loop(private=True))

        # Assert: the owner's member view (their own Gmail) is what the turn resolves.
        assert scope == _member("U0OWNER")
        assert current_scope() is None

    def test_a_shared_loop_runs_as_nobody(self) -> None:
        # Anyone may rewrite a shared loop, so it never runs with a member's accounts.
        assert _run_scope(_loop(private=False)) is None
        assert _run_scope(_loop(private=None)) is None

    def test_a_run_inside_a_turn_keeps_that_turn(self) -> None:
        with bound_storage_scope(_member("user_owner")):
            assert _run_scope(_loop(private=True)) == _member("user_owner")


class TestPrivateLoopVisibility:
    def test_only_the_owner_sees_a_private_loop_but_the_app_mirror_sees_all(self) -> None:
        # Arrange
        with bound_storage_scope(_member("U0OWNER")):
            created = create_manual_loop(
                name="Inbox digest", prompt=_PROMPT, cron="0 9 * * 1-5", private=True
            )

        # Act / Assert: another member can neither list nor name it.
        with bound_storage_scope(_member("U0OTHER")):
            assert list_loop_summaries() == []
            missing, error = resolve_loop_summary(created.task.id)
            assert missing is None
            assert "not found" in error
            mirrored = list_loop_summaries(include_private=True)
        with bound_storage_scope(_member("U0OWNER")):
            [own] = list_loop_summaries()

        assert [loop.id for loop in mirrored] == [created.task.id]
        assert own.private
        assert own.owner == "U0OWNER"

    def test_the_owner_is_recognized_under_their_web_app_id(self) -> None:
        # Arrange: one person is U0OWNER in Slack and user_owner in the app.
        members = {
            "U0OWNER": "user_owner",
            "user_owner": "user_owner",
            "user_other": "user_other",
        }
        install_member_resolver(members.get)
        with bound_storage_scope(_member("U0OWNER")):
            created = create_manual_loop(
                name="Inbox digest", prompt=_PROMPT, cron="0 9 * * 1-5", private=True
            )

        # Act
        with bound_storage_scope(_member("user_owner")):
            seen_by_owner = [loop.id for loop in list_loop_summaries()]
        with bound_storage_scope(_member("user_other")):
            seen_by_other = list_loop_summaries()

        # Assert
        assert seen_by_owner == [created.task.id]
        assert seen_by_other == []

    def test_two_members_identical_private_loops_stay_two(self) -> None:
        # Merging them would run one member's loop as the other.
        with bound_storage_scope(_member("U0OWNER")):
            first = create_manual_loop(
                name="Digest", prompt=_PROMPT, cron="0 9 * * *", private=True
            )
        with bound_storage_scope(_member("U0OTHER")):
            second = create_manual_loop(
                name="Digest", prompt=_PROMPT, cron="0 9 * * *", private=True
            )

        assert first.task.id != second.task.id
