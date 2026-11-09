"""Unit tests for github_pr_sweep task kind routing."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.tasks import build_message
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import runners_with_agent


def test_github_pr_sweep_kind_invokes_agent_runner() -> None:
    calls: list[dict] = []

    def fake_runner(payload: dict) -> str:
        calls.append(payload)
        return "PR sweep ok"

    task = ScheduledTask(
        kind=TaskKind.GITHUB_PR_SWEEP,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
        chat_id="C01234567",
    )
    assert build_message(task, runners_with_agent(fake_runner)) == "PR sweep ok"
    assert calls[0]["source"] == "scheduled_github_pr_sweep"


def test_github_ci_health_recurring_skill_preserves_repository_scope() -> None:
    calls: list[dict] = []

    def fake_runner(payload: dict) -> str:
        calls.append(payload)
        return "CI health ok"

    from core.agent_harness import pin_recurring_skill

    skill_name, skill_revision = pin_recurring_skill("reporting-github-ci-failures")
    task = ScheduledTask(
        kind=TaskKind.RECURRING_SKILL,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
        skill_name=skill_name,
        skill_revision=skill_revision,
        skill_inputs={"owner": "acme", "repo": "api", "branch": "main"},
    )

    assert build_message(task, runners_with_agent(fake_runner)) == "CI health ok"
    assert calls == [
        {
            "source": "scheduled_recurring_skill",
            "task_id": task.id,
            "skill_name": "reporting-github-ci-failures",
            "skill_revision": skill_revision,
            "skill_inputs": {"owner": "acme", "repo": "api", "branch": "main"},
        }
    ]


def test_scheduled_agent_runs_the_runner_registered_for_the_source(monkeypatch) -> None:
    from infrastructure.scheduling.scheduler.sources import (
        SCHEDULED_GITHUB_PR_SWEEP,
        SCHEDULED_RECURRING_SKILL,
        SCHEDULED_SENTRY_MORNING_DIGEST,
        SCHEDULED_SENTRY_UPTIME_WATCH,
    )
    from integrations.scheduled_agent_bootstrap import (
        RUNNERS,
        ScheduledRunner,
        run_scheduled_agent_digest,
    )

    # Arrange
    for source, reply in (
        (SCHEDULED_GITHUB_PR_SWEEP, "gh"),
        (SCHEDULED_RECURRING_SKILL, "ci-health"),
        (SCHEDULED_SENTRY_MORNING_DIGEST, "sentry"),
    ):
        monkeypatch.setitem(RUNNERS, source, ScheduledRunner(lambda _p, reply=reply: reply))
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_uptime_watch_tick",
        lambda **_kwargs: "uptime",
    )

    # Act / Assert
    assert run_scheduled_agent_digest({"source": SCHEDULED_GITHUB_PR_SWEEP}) == "gh"
    assert run_scheduled_agent_digest({"source": SCHEDULED_RECURRING_SKILL}) == "ci-health"
    assert run_scheduled_agent_digest({"source": SCHEDULED_SENTRY_MORNING_DIGEST}) == "sentry"
    assert (
        run_scheduled_agent_digest({"source": SCHEDULED_SENTRY_UPTIME_WATCH, "task_id": "t1"})
        == "uptime"
    )


def test_an_unregistered_source_is_refused_not_guessed() -> None:
    from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest

    with pytest.raises(RuntimeError, match="No scheduled runner"):
        run_scheduled_agent_digest({"source": "something_new"})
