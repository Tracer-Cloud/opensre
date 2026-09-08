"""Acceptance tests for repository-scoped scheduled GitHub CI health."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness import pin_recurring_skill
from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.github.ci_health_runner import (
    MAX_CHECK_RUNS_PER_SHA,
    MAX_OPEN_PRS,
    run_github_ci_health,
)


class _FakeGitHubClient:
    def __init__(self, repositories: dict[str, dict[str, Any]]) -> None:
        self.repositories = repositories
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((method, path, params))
        parts = path.split("/")
        repo = parts[3]
        data = self.repositories[repo]
        if len(parts) == 4:
            return {"default_branch": data["default_branch"]}
        if parts[4] == "branches":
            branch = parts[5].replace("%2F", "/")
            return {"commit": {"sha": data["branches"][branch]}}
        if parts[4] == "pulls":
            number = int(parts[5])
            return next(pr for pr in data["pulls"] if pr["number"] == number)
        if parts[4] == "commits" and parts[6] == "status":
            return {"statuses": data.get("statuses", {}).get(parts[5], [])}
        raise AssertionError(f"Unexpected GitHub path: {path}")

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append(("GET", path, params))
        parts = path.split("/")
        data = self.repositories[parts[3]]
        if parts[4] == "pulls":
            return list(data["pulls"])
        if parts[4] == "commits" and parts[6] == "check-runs":
            return list(data["checks"].get(parts[5], []))
        raise AssertionError(f"Unexpected GitHub path: {path}")


def _check(name: str, url: str, conclusion: str = "failure") -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": conclusion,
        "html_url": url,
        "started_at": "2026-09-04T09:00:00Z",
        "completed_at": "2026-09-04T10:00:00Z",
    }


def _status(context: str, url: str, state: str = "failure") -> dict[str, Any]:
    return {
        "context": context,
        "state": state,
        "target_url": url,
        "updated_at": "2026-09-04T10:30:00Z",
    }


def _repository(
    *,
    branch: str,
    sha: str,
    checks: list[dict[str, Any]],
    statuses: list[dict[str, Any]] | None = None,
    pulls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "default_branch": branch,
        "branches": {branch: sha},
        "checks": {sha: checks},
        "statuses": {sha: statuses or []},
        "pulls": pulls or [],
    }


def _scheduled_task(repo: str) -> ScheduledTask:
    skill_name, skill_revision = pin_recurring_skill("github-ci-health")
    return ScheduledTask(
        kind=TaskKind.RECURRING_SKILL,
        cron="0 8 * * 1-5",
        provider=Provider.INTERACTIVE_SHELL,
        skill_name=skill_name,
        skill_revision=skill_revision,
        skill_inputs={"owner": "acme", "repo": repo},
    )


def test_two_repository_schedule_scopes_are_persisted_separately(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    for repo in ("api", "web"):
        add_task(_scheduled_task(repo), store_path=store)

    tasks = list_tasks(store)

    assert len(tasks) == 2
    assert [task.skill_name for task in tasks] == ["github-ci-health"] * 2
    assert [task.skill_inputs for task in tasks] == [
        {"owner": "acme", "repo": "api"},
        {"owner": "acme", "repo": "web"},
    ]
    assert all(task.skill_revision for task in tasks)


def test_two_repository_schedules_keep_results_isolated() -> None:
    client = _FakeGitHubClient(
        {
            "api": _repository(
                branch="main",
                sha="api-sha",
                checks=[_check("API tests", "https://github.test/acme/api/1")],
            ),
            "web": _repository(
                branch="release",
                sha="web-sha",
                checks=[_check("Web tests", "https://github.test/acme/web/2")],
            ),
        }
    )
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)

    api_report = run_github_ci_health({"owner": "acme", "repo": "api"}, client=client, now=now)
    web_report = run_github_ci_health({"owner": "acme", "repo": "web"}, client=client, now=now)

    assert "acme/api" in api_report and "API tests" in api_report
    assert "2h old" in api_report and "branch main" in api_report
    assert "Web tests" not in api_report
    assert "acme/web" in web_report and "Web tests" in web_report
    assert "API tests" not in web_report
    assert all(method == "GET" for method, _path, _params in client.calls)


def test_report_includes_check_runs_commit_statuses_and_repair_handoff() -> None:
    client = _FakeGitHubClient(
        {
            "api": _repository(
                branch="feature",
                sha="feature-sha",
                checks=[_check("quality\ninjected", "https://github.test/check/7")],
                statuses=[_status("jenkins", "https://ci.test/build/8")],
            )
        }
    )

    report = run_github_ci_health(
        {"owner": "acme", "repo": "api", "branch": "feature"},
        client=client,
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert "quality injected" in report
    assert "https://github.test/check/7" in report
    assert "jenkins" in report and "https://ci.test/build/8" in report
    assert "1h old" in report
    assert "requires your approval" in report
    assert "fix_github_pr_ci" in report
    assert all(method == "GET" for method, _path, _params in client.calls)


def test_pr_filter_reports_only_current_pull_request_head() -> None:
    pull = {"number": 42, "head": {"sha": "pr-sha", "ref": "feature/ci"}}
    client = _FakeGitHubClient(
        {
            "api": {
                "default_branch": "main",
                "branches": {"main": "main-sha"},
                "checks": {"pr-sha": [_check("unit tests", "https://github.test/check/42")]},
                "statuses": {"pr-sha": []},
                "pulls": [pull],
            }
        }
    )

    report = run_github_ci_health(
        {"owner": "acme", "repo": "api", "pr_number": "42"},
        client=client,
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert "unit tests" in report and "PR #42 (feature/ci)" in report
    assert [path for _method, path, _params in client.calls] == [
        "/repos/acme/api/pulls/42",
        "/repos/acme/api/commits/pr-sha/check-runs",
        "/repos/acme/api/commits/pr-sha/status",
    ]


def test_repository_report_uses_current_heads_not_historical_failures() -> None:
    client = _FakeGitHubClient(
        {
            "api": _repository(
                branch="main",
                sha="current-green-sha",
                checks=[_check("CI", "https://github.test/current", conclusion="success")],
                statuses=[_status("legacy", "https://ci.test/current", state="success")],
            )
        }
    )

    report = run_github_ci_health(
        {"owner": "acme", "repo": "api"},
        client=client,
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert "No failing checks found" in report
    assert any("current-green-sha/check-runs" in path for _, path, _ in client.calls)


def test_repository_report_includes_each_open_pr_current_head() -> None:
    pull = {"number": 9, "head": {"sha": "pr-9-sha", "ref": "feature/nine"}}
    data = _repository(branch="main", sha="main-sha", checks=[], pulls=[pull])
    data["checks"]["pr-9-sha"] = [_check("PR tests", "https://github.test/pr/9")]
    data["statuses"]["pr-9-sha"] = []
    client = _FakeGitHubClient({"api": data})

    report = run_github_ci_health(
        {"owner": "acme", "repo": "api"},
        client=client,
        now=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert "PR tests" in report and "PR #9 (feature/nine)" in report


def test_repository_report_caps_open_pr_scan_and_discloses_coverage() -> None:
    pulls = [
        {"number": number, "head": {"sha": f"sha-{number}", "ref": f"pr-{number}"}}
        for number in range(1, MAX_OPEN_PRS + 2)
    ]
    data = _repository(branch="main", sha="main-sha", checks=[], pulls=pulls)
    data["checks"].update({f"sha-{number}": [] for number in range(1, MAX_OPEN_PRS + 2)})
    data["statuses"].update({f"sha-{number}": [] for number in range(1, MAX_OPEN_PRS + 2)})
    client = _FakeGitHubClient({"api": data})

    report = run_github_ci_health({"owner": "acme", "repo": "api"}, client=client)

    assert f"limited to the first {MAX_OPEN_PRS} open PRs" in report
    checked_pr_shas = [path for _, path, _ in client.calls if "/check-runs" in path]
    assert len(checked_pr_shas) == MAX_OPEN_PRS + 1  # default branch plus bounded PRs


def test_check_run_scan_detects_and_discloses_truncation() -> None:
    checks = [
        _check(f"check-{number}", f"https://github.test/{number}", conclusion="success")
        for number in range(MAX_CHECK_RUNS_PER_SHA + 1)
    ]
    client = _FakeGitHubClient({"api": _repository(branch="main", sha="main-sha", checks=checks)})

    report = run_github_ci_health({"owner": "acme", "repo": "api", "branch": "main"}, client=client)

    assert f"limited to the first {MAX_CHECK_RUNS_PER_SHA} latest checks" in report
    assert "branch main" in report
    assert "No failing checks found" in report


def test_scope_path_segments_are_encoded() -> None:
    client = _FakeGitHubClient({})
    with pytest.raises(KeyError):
        run_github_ci_health(
            {"owner": "acme/other", "repo": "api/name", "branch": "feature/ci"}, client=client
        )
    assert client.calls[0][1] == "/repos/acme%2Fother/api%2Fname/branches/feature%2Fci"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"repo": "api"}, "requires owner"),
        ({"owner": "acme"}, "requires repo"),
        (
            {"owner": "acme", "repo": "api", "branch": "main", "pr_number": "2"},
            "either branch or pr_number",
        ),
        ({"owner": "acme", "repo": "api", "pr_number": "zero"}, "positive integer"),
    ],
)
def test_invalid_scheduled_scope_fails_before_github_reads(
    payload: dict[str, str], message: str
) -> None:
    client = _FakeGitHubClient({})

    with pytest.raises(RuntimeError, match=message):
        run_github_ci_health(payload, client=client)

    assert client.calls == []
