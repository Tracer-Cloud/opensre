"""One demo call schedules the seeded pull request and finishes once."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.github.tools.ci_repair_run import tool as run_tool

_OWNER = "octocat"
_REQUESTED_REPO = "opensre-ci-repair-demo"
_SEEDED_REPO = "opensre-ci-repair-demo-ab12"
_PR_NUMBER = 7
_PR_URL = f"https://github.com/{_OWNER}/{_SEEDED_REPO}/pull/{_PR_NUMBER}"
_TASK_ID = "task-1"
_FAILED_RUN = 11
_FIX = "bbb222"
_PASSING_RUN = 99


class _Record:
    def __init__(self) -> None:
        self.seeds = 0
        self.schedules: list[dict[str, Any]] = []
        self.waits: list[tuple[str, bool]] = []
        self.views: list[list[str]] = []
        self.finishes: list[dict[str, Any]] = []


def _seed_ok(
    owner: str,
    repo: str,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    del owner, repo, github_token, _kwargs
    return {
        "ok": True,
        "owner": _OWNER,
        "repo": _SEEDED_REPO,
        "pr_number": _PR_NUMBER,
        "pr_url": _PR_URL,
        "head_sha": "aaa111",
        "failed_run_id": _FAILED_RUN,
    }


def _view(
    args: list[str],
    *,
    repo: str,
    github_token: str | None,
    timeout: int = 120,
) -> dict[str, Any]:
    del repo, github_token, timeout
    return {
        "headRefOid": _FIX,
        "commits": [{"oid": _FIX}],
        "statusCheckRollup": [
            {
                "conclusion": "SUCCESS",
                "detailsUrl": f"https://github.com/{_OWNER}/{_SEEDED_REPO}/actions/runs/{_PASSING_RUN}",
            }
        ],
        "_args": args,
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    record: _Record,
    *,
    seed_result: dict[str, Any] | None = None,
    schedule_result: dict[str, Any] | None = None,
    observe_result: dict[str, Any] | None = None,
) -> None:
    def _seed(
        owner: str,
        repo: str,
        github_token: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del github_token, _kwargs
        record.seeds += 1
        if seed_result is not None:
            return seed_result
        return _seed_ok(owner, repo)

    def _schedule(
        owner: str = "",
        repo: str = "",
        pr_number: int = 0,
        github_token: str | None = None,
        context: Any = None,
        fast_checks: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del github_token, context, _kwargs
        record.schedules.append(
            {
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "fast_checks": fast_checks,
            }
        )
        if schedule_result is not None:
            return schedule_result
        return {"ok": True, "task_id": _TASK_ID, "status": "queued", "pr_url": _PR_URL}

    def _get(
        task_id: str = "",
        wait_seconds: int = 0,
        wait_until_terminal: bool = False,
        github_token: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del wait_seconds, github_token, _kwargs
        record.waits.append((task_id, wait_until_terminal))
        if observe_result is not None:
            return observe_result
        return {
            "ok": True,
            "task_id": task_id,
            "status": "succeeded",
            "terminal": True,
        }

    def _finish(**kwargs: Any) -> dict[str, Any]:
        record.finishes.append(kwargs)
        return {
            "ok": True,
            "evidence": "/tmp/ci-repair-demo.md",
            "repository_retained": True,
            "loop_removed": True,
        }

    def _pull(
        args: list[str],
        *,
        repo: str,
        github_token: str | None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        record.views.append(args)
        return _view(args, repo=repo, github_token=github_token, timeout=timeout)

    monkeypatch.setattr(run_tool, "seed_ci_repair_demo", _seed)
    monkeypatch.setattr(run_tool, "schedule_ci_repair_loop", _schedule)
    monkeypatch.setattr(run_tool, "get_ci_repair_loop", _get)
    monkeypatch.setattr(run_tool, "finish_ci_repair_demo", _finish)
    monkeypatch.setattr(run_tool, "run_gh_json", _pull)


def test_run_schedules_the_seeded_pr_once_then_waits_and_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    _install(monkeypatch, record)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert record.seeds == 1
    assert record.schedules == [
        {
            "owner": _OWNER,
            "repo": _SEEDED_REPO,
            "pr_number": _PR_NUMBER,
            "fast_checks": True,
        }
    ]
    assert record.waits == [(_TASK_ID, True)]
    assert record.views == [
        ["pr", "view", str(_PR_NUMBER), "--json", "headRefOid,commits,statusCheckRollup"]
    ]
    assert len(record.finishes) == 1
    finished = record.finishes[0]
    assert finished["repo"] == f"{_OWNER}/{_SEEDED_REPO}"
    assert finished["pr_number"] == _PR_NUMBER
    assert finished["loop_id"] == _TASK_ID
    assert finished["outcome"] == "success"
    assert finished["failed_run_id"] == _FAILED_RUN
    assert finished["fix_commit"] == _FIX
    assert finished["passing_run_id"] == _PASSING_RUN
    assert result["ok"] is True
    assert result["owner"] == _OWNER
    assert result["repo"] == _SEEDED_REPO
    assert result["pr_number"] == _PR_NUMBER
    assert result["pr_url"] == _PR_URL
    assert result["task_id"] == _TASK_ID
    assert result["failed_run_id"] == _FAILED_RUN
    assert result["fix_commit"] == _FIX
    assert result["passing_run_id"] == _PASSING_RUN
    assert result["outcome"] == "success"
    assert result["evidence"] == "/tmp/ci-repair-demo.md"
    assert result["response_text"]


def test_seed_failure_does_not_schedule_or_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    failure = {"ok": False, "error": "Could not seed the CI repair demo: GitHubApiError."}
    _install(monkeypatch, record, seed_result=failure)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result == failure
    assert record.seeds == 1
    assert record.schedules == []
    assert record.waits == []
    assert record.finishes == []


def test_schedule_failure_does_not_schedule_again_or_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    failure = {"ok": False, "error": "Could not schedule CI repair: the pull request was refused."}
    _install(monkeypatch, record, schedule_result=failure)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result == failure
    assert record.schedules == [
        {
            "owner": _OWNER,
            "repo": _SEEDED_REPO,
            "pr_number": _PR_NUMBER,
            "fast_checks": True,
        }
    ]
    assert record.waits == []
    assert record.finishes == []


def test_a_running_report_leaves_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(
        monkeypatch,
        record,
        observe_result={"ok": True, "status": "running", "task_id": _TASK_ID},
    )

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result["ok"] is False
    assert result["task_id"] == _TASK_ID
    assert "left in place" in result["error"]
    assert record.finishes == []
    assert record.views == []


def test_a_failed_report_read_removes_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(monkeypatch, record, observe_result={"ok": False, "error": "missing"})

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result["ok"] is False
    assert result["task_id"] == _TASK_ID
    assert result["loop_removed"] is True
    assert record.finishes[0]["outcome"] == "blocked"
    assert record.finishes[0]["loop_id"] == _TASK_ID


def test_a_pull_read_failure_removes_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(monkeypatch, record)

    def _boom(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise OSError("timed out")

    monkeypatch.setattr(run_tool, "run_gh_json", _boom)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result["ok"] is False
    assert result["task_id"] == _TASK_ID
    assert record.finishes[0]["loop_id"] == _TASK_ID


def test_a_neutral_check_beside_a_success_still_counts_as_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _Record()
    _install(monkeypatch, record)

    def _pull(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "headRefOid": _FIX,
            "statusCheckRollup": [
                {
                    "conclusion": "SUCCESS",
                    "detailsUrl": (
                        f"https://github.com/{_OWNER}/{_SEEDED_REPO}/actions/runs/{_PASSING_RUN}"
                    ),
                },
                {"conclusion": "NEUTRAL"},
                {"conclusion": "SKIPPED"},
            ],
        }

    monkeypatch.setattr(run_tool, "run_gh_json", _pull)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result["outcome"] == "success"
    assert result["passing_run_id"] == _PASSING_RUN
