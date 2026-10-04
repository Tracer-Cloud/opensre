"""One demo call schedules the seeded pull request and finishes once."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness.turns.display_text import is_outcome_report
from infrastructure.text.data_blob import is_data_blob
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore
from integrations.github.tools.ci_repair_run import tool as run_tool
from integrations.github.tools.ci_repair_run.root_cause import (
    RepairEvidence,
    read_repair_evidence,
)

_OWNER = "octocat"
_REQUESTED_REPO = "opensre-ci-repair-demo"
_SEEDED_REPO = "opensre-ci-repair-demo-ab12"
_PR_NUMBER = 7
_PR_URL = f"https://github.com/{_OWNER}/{_SEEDED_REPO}/pull/{_PR_NUMBER}"
_TASK_ID = "f50fd5579ff9"
_SEED_HEAD = "aaa111"
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
        "head_sha": _SEED_HEAD,
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
    store: RepairStore | None = None,
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

    def _evidence(task_id: str) -> RepairEvidence:
        # Without a store the run reads as unrecorded; never the developer's own store.
        if store is None:
            return RepairEvidence()
        return read_repair_evidence(task_id, store=store)

    monkeypatch.setattr(run_tool, "seed_ci_repair_demo", _seed)
    monkeypatch.setattr(run_tool, "read_repair_evidence", _evidence)
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


def _repair_store(tmp_path: Path) -> RepairStore:
    """The finished run of ``_TASK_ID`` with the one attempt that pushed ``_FIX``."""
    store = RepairStore(tmp_path)
    now = time.time()
    store.save(
        RepairRun(
            id=_TASK_ID,
            owner=_OWNER,
            repo=_SEEDED_REPO,
            actor=_OWNER,
            actor_id=1,
            started_at=now,
            deadline=now + 600,
            pr_number=_PR_NUMBER,
            fast_checks=True,
            initial_sha=_SEED_HEAD,
            fixed_sha=_FIX,
            status=RepairStatus.SUCCEEDED,
            attempts=1,
        )
    )
    store.directory(_TASK_ID).mkdir(parents=True, exist_ok=True)
    attempt = {
        "failing_checks": ["test"],
        "fix_head_sha": _FIX,
        "changed_files": ["calculator.py"],
        "summary": "Updated `calculator.py` so `add()` performs addition.",
        "diff": "--- a/calculator.py\n+++ b/calculator.py\n-    return a - b\n+    return a + b\n",
        "diff_truncated": False,
    }
    store.attempt_path(_TASK_ID, 1).write_text(json.dumps(attempt), encoding="utf-8")
    return store


def test_a_repaired_demo_reports_full_github_urls_and_its_root_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: this seed committed the failing change, and the store holds the repair
    record = _Record()
    seeded = {**_seed_ok(_OWNER, _REQUESTED_REPO), "reused": False}
    _install(monkeypatch, record, seed_result=seeded, store=_repair_store(tmp_path))

    # Act
    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    # Assert: every URL is written out, since a terminal shows link text without its URL
    base = f"https://github.com/{_OWNER}/{_SEEDED_REPO}"
    assert result["links"] == {
        "pull_request": _PR_URL,
        "failing_commit": f"{base}/commit/{_SEED_HEAD}",
        "failed_run": f"{base}/actions/runs/{_FAILED_RUN}",
        "fix_commit": f"{base}/commit/{_FIX}",
        "passing_run": f"{base}/actions/runs/{_PASSING_RUN}",
    }
    text = result["response_text"]
    for url in result["links"].values():
        assert f": {url}\n" in text
    analysis = result["root_cause_analysis"]
    assert analysis["failure"] == f"Check `test` failed on commit {_SEED_HEAD} of PR #{_PR_NUMBER}."
    assert analysis["cause"].startswith(f"Commit {_SEED_HEAD} changed `add()` in `calculator.py`")
    assert analysis["fix"] == f"Commit {_FIX} changed `calculator.py`."
    assert "**Root cause analysis**" in text
    assert "-    return a - b\n+    return a + b" in text
    # The gateway appends a tool's text to its answer unless it reads as data or
    # as a second outcome report; either would drop the links and the analysis.
    assert not is_data_blob(text)
    assert not is_outcome_report(text)
    assert record.finishes[0]["analysis"] in text


def test_a_green_head_pushed_after_the_repair_is_not_reported_as_its_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: the run verified _FIX, then someone else pushed a green head
    record = _Record()
    seeded = {**_seed_ok(_OWNER, _REQUESTED_REPO), "reused": False}
    _install(monkeypatch, record, seed_result=seeded, store=_repair_store(tmp_path))
    other_head = "ccc333"

    def _pull(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "headRefOid": other_head,
            "statusCheckRollup": [
                {
                    "conclusion": "SUCCESS",
                    "detailsUrl": (
                        f"https://github.com/{_OWNER}/{_SEEDED_REPO}/actions/runs/{_PASSING_RUN}"
                    ),
                }
            ],
        }

    monkeypatch.setattr(run_tool, "run_gh_json", _pull)

    # Act
    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    # Assert: neither the other head nor its passing run is linked or credited
    assert set(result["links"]) == {"pull_request", "failing_commit", "failed_run"}
    analysis = result["root_cause_analysis"]
    assert analysis["fix"] == (
        f"Not attributed: the repair verified commit {_FIX}, "
        f"but the pull request head is now {other_head}."
    )
    assert "verification" not in analysis


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


def test_the_result_says_the_demo_loop_was_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(monkeypatch, record)

    result = run_tool.run_ci_repair_demo(_OWNER, _REQUESTED_REPO)

    assert result["loop_removed"] is True
    assert result["repository_retained"] is True
    assert "The demo loop was removed." in result["response_text"]
    assert "The repository remains." in result["response_text"]


def test_an_empty_owner_seeds_under_the_token_login(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(monkeypatch, record)
    seeded_owners: list[str] = []
    seed = run_tool.seed_ci_repair_demo

    def _seed(owner: str, repo: str, **kwargs: Any) -> dict[str, Any]:
        seeded_owners.append(owner)
        return seed(owner=owner, repo=repo, **kwargs)

    class _Client:
        def __init__(self, token: str) -> None:
            assert token == "ghp_demo"

        def request(self, method: str, path: str) -> dict[str, Any]:
            assert (method, path) == ("GET", "user")
            return {"login": _OWNER}

    monkeypatch.setattr(run_tool, "seed_ci_repair_demo", _seed)
    monkeypatch.setattr(run_tool, "GitHubRestClient", _Client)
    monkeypatch.setattr(run_tool, "configured_token", lambda _explicit=None: "ghp_demo")

    result = run_tool.run_ci_repair_demo(repo=_REQUESTED_REPO)

    assert seeded_owners == [_OWNER]
    assert result["ok"] is True
    assert result["owner"] == _OWNER


def test_a_token_without_a_login_does_not_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _Record()
    _install(monkeypatch, record)

    class _Client:
        def __init__(self, token: str) -> None:
            del token

        def request(self, method: str, path: str) -> dict[str, Any]:
            del method, path
            return {}

    monkeypatch.setattr(run_tool, "GitHubRestClient", _Client)
    monkeypatch.setattr(run_tool, "configured_token", lambda _explicit=None: "ghp_demo")

    result = run_tool.run_ci_repair_demo(owner="  ", repo=_REQUESTED_REPO)

    assert result["ok"] is False
    assert "pass owner" in result["error"]
    assert record.seeds == 0
