"""Unit tests for github_pr_sweep task kind routing."""

from __future__ import annotations

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


def test_scheduled_agent_routes_github(monkeypatch) -> None:
    from integrations.scheduled_agent_bootstrap import run_scheduled_agent_digest

    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_github_pr_sweep",
        lambda _payload: "gh",
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_scheduled_recurring_skill",
        lambda _payload: "ci-health",
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_sentry_morning_digest",
        lambda _payload: "sentry",
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.run_uptime_watch_tick",
        lambda **_kwargs: "uptime",
    )
    assert run_scheduled_agent_digest({"source": "scheduled_github_pr_sweep"}) == "gh"
    assert run_scheduled_agent_digest({"source": "scheduled_recurring_skill"}) == "ci-health"
    assert run_scheduled_agent_digest({"source": "scheduled_sentry_morning_digest"}) == "sentry"
    assert (
        run_scheduled_agent_digest({"source": "scheduled_sentry_uptime_watch", "task_id": "t1"})
        == "uptime"
    )
