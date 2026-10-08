"""Polling after onboarding flow B creates the calculator demo.

The failing branch is pushed before its pull request exists, and the workflow
runs on both push and pull_request. These tests start with that checkout, then
drive the check poller, the repair loop, and the epoch observer through the
queued, split-event, and head-change sequences that follow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from config.constants.git import OPENSRE_COMMIT_COAUTHOR_EMAIL
from integrations.github.tools.actions import _workflow_verdicts
from integrations.github.tools.ci_fix.context import CiFixContext, FailingCheck
from integrations.github.tools.ci_fix.verification import (
    CheckState,
    check_failed,
    wait_for_pr_checks,
)
from integrations.github.tools.ci_fix.verification import (
    _check_is_terminal as check_is_terminal,
)
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore

_REPO = "tester/opensre-ci-repair-demo-poll"
_WORKFLOW = "Demo calculator CI"
_RUN_URL = f"https://github.com/{_REPO}/actions/runs/99"


class Clock:
    """Test clock. Sleep moves it forward; callers read the same value."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


_CALCULATOR = "def add(left: int, right: int) -> int:\n    return {}\n"
_CALCULATOR_TEST = """import unittest

from calculator import add


class CalculatorTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""


def _commit_all(checkout: Path, message: str) -> str:
    _git(checkout, "add", "-A")
    _git(
        checkout,
        "-c",
        "user.name=Demo User",
        "-c",
        "user.email=demo@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
    )
    return _git(checkout, "rev-parse", "HEAD")


@pytest.fixture
def seeded_demo(tmp_path: Path) -> dict[str, Any]:
    """The flow B demo checkout: green main, one red commit on demo/failing-ci."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    (checkout / "calculator.py").write_text(_CALCULATOR.format("left + right"))
    (checkout / "test_calculator.py").write_text(_CALCULATOR_TEST)
    seed_sha = _commit_all(checkout, "Seed demo calculator CI")
    _git(checkout, "checkout", "-b", "demo/failing-ci")
    (checkout / "calculator.py").write_text(_CALCULATOR.format("left - right"))
    head_sha = _commit_all(checkout, "Introduce demo calculator regression")
    return {"checkout": checkout, "seed_sha": seed_sha, "head_sha": head_sha}


def _demo_check(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "name": "test",
        "workflowName": _WORKFLOW,
        "status": "COMPLETED",
        "conclusion": "FAILURE",
        "detailsUrl": _RUN_URL,
    }
    row.update(overrides)
    return row


def _context(head_sha: str) -> CiFixContext:
    return CiFixContext(
        owner="tester",
        repo="opensre-ci-repair-demo-poll",
        number=1,
        title="Demo: repair failing calculator CI",
        url=f"https://github.com/{_REPO}/pull/1",
        base_branch="main",
        head_branch="demo/failing-ci",
        head_sha=head_sha,
        skipped_check_names=(),
        failing_checks=(
            FailingCheck(
                name="test",
                conclusion="failure",
                details_url=_RUN_URL,
                workflow_name=_WORKFLOW,
            ),
        ),
        task="Fix the seeded calculator regression.",
    )


def _run_row(
    identifier: int,
    sha: str,
    *,
    event: str,
    conclusion: str | None,
    status: str = "completed",
    pull_number: int | None = 1,
    updated_at: str = "2026-09-21T12:00:00Z",
    run_number: int = 1,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "workflow_id": 7,
        "name": _WORKFLOW,
        "event": event,
        "pull_requests": [] if pull_number is None else [{"number": pull_number}],
        "head_sha": sha,
        "head_branch": "demo/failing-ci",
        "status": status,
        "conclusion": conclusion,
        "updated_at": updated_at,
        "run_number": run_number,
        "run_attempt": 1,
    }


def _api_commit(checkout: Path, sha: str) -> dict[str, Any]:
    email = _git(checkout, "log", "-1", "--format=%ae", sha)
    name = _git(checkout, "log", "-1", "--format=%an", sha)
    message = _git(checkout, "log", "-1", "--format=%B", sha)
    return {
        "sha": sha,
        "author": {"login": "demo-user"},
        "commit": {
            "author": {"name": name, "email": email},
            "committer": {"name": name, "email": email},
            "message": message,
        },
    }


def _repair(*, deadline: float = 500) -> RepairRun:
    return RepairRun(
        id="a" * 12,
        owner="tester",
        actor="tester",
        actor_id=123,
        repo="opensre-ci-repair-demo-poll",
        fast_checks=True,
        started_at=0,
        deadline=deadline,
        pr_number=1,
        workspace="/tmp/demo",
        branch="demo/failing-ci",
        initial_sha="",
        status=RepairStatus.RUNNING,
    )


def test_seeded_check_shapes_stay_unfinished_until_a_real_failure() -> None:
    failure = _demo_check()
    queued = _demo_check(status="QUEUED", conclusion="")
    running = _demo_check(status="IN_PROGRESS", conclusion="")
    success = _demo_check(conclusion="SUCCESS")

    assert check_failed(failure, expected_skips=set())
    assert check_is_terminal(failure)
    assert not check_failed(queued, expected_skips=set())
    assert not check_is_terminal(queued)
    assert not check_failed(running, expected_skips=set())
    assert not check_is_terminal(running)
    assert not check_failed(success, expected_skips=set())
    assert check_is_terminal(success)
    skipped = _demo_check(conclusion="SKIPPED")
    assert not check_failed(skipped, expected_skips=set())
    assert not check_failed(skipped, expected_skips={"test"})
    assert check_failed(skipped, expected_skips=set(), targeted_checks=frozenset({"test"}))
    assert check_failed(skipped, expected_skips=set(), failed_run_ids=frozenset({"99"}))


def test_branch_poll_keeps_waiting_while_the_newer_pull_request_run_is_open(
    seeded_demo: dict[str, Any],
) -> None:
    """Step 5 lists the branch. The push failure is older than the PR run."""
    head = seeded_demo["head_sha"]
    hidden = _workflow_verdicts(
        [
            _run_row(
                1,
                head,
                event="push",
                conclusion="failure",
                updated_at="2026-09-21T12:00:00Z",
                run_number=1,
                pull_number=None,
            ),
            _run_row(
                2,
                head,
                event="pull_request",
                conclusion=None,
                status="in_progress",
                updated_at="2026-09-21T12:01:00Z",
                run_number=2,
            ),
        ]
    )
    assert hidden[0]["workflow"] == _WORKFLOW
    assert hidden[0]["latest_conclusion"] == "in_progress"
    assert hidden[0]["re_run_to_green"] is False

    failed = _workflow_verdicts(
        [
            _run_row(1, head, event="push", conclusion="failure", run_number=1, pull_number=None),
            _run_row(
                2,
                head,
                event="pull_request",
                conclusion="failure",
                updated_at="2026-09-21T12:02:00Z",
                run_number=2,
            ),
        ]
    )
    assert failed[0]["latest_conclusion"] == "failure"
    assert "failure" in failed[0]["summary"]

    # A later green commit is a different SHA, so the seeded head stays failed.
    still_failed = _workflow_verdicts(
        [_run_row(2, head, event="pull_request", conclusion="failure", run_number=2)]
    )
    assert still_failed[0]["latest_conclusion"] == "failure"


def test_seeded_head_is_failed_only_after_both_trigger_events_finish(
    seeded_demo: dict[str, Any],
) -> None:
    head = seeded_demo["head_sha"]
    snapshots = iter(
        [
            {
                "headRefOid": head,
                "statusCheckRollup": [_demo_check(status="IN_PROGRESS", conclusion="")],
            },
            {
                "headRefOid": head,
                "statusCheckRollup": [_demo_check()],
            },
            {
                "headRefOid": head,
                "statusCheckRollup": [_demo_check()],
            },
        ]
    )
    run_lists = iter(
        [
            {"runs": [{"databaseId": 1, "status": "in_progress"}]},
            {
                "runs": [
                    {"databaseId": 1, "status": "completed"},
                    {"databaseId": 2, "status": "queued"},
                ]
            },
            {
                "runs": [
                    {"databaseId": 1, "status": "completed"},
                    {"databaseId": 2, "status": "completed"},
                ]
            },
        ]
    )
    sleeps: list[float] = []

    def gh(args: list[str], **_kwargs: object) -> dict[str, Any]:
        return next(snapshots) if args[0] == "pr" else next(run_lists)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "integrations.github.tools.ci_fix.verification.run_gh_json",
            gh,
        )
        result = wait_for_pr_checks(
            _context(head),
            github_token="tok",
            expected_head_sha=head,
            registration_seconds=0,
            settle_seconds=0,
            poll_interval_seconds=10,
            sleep=sleeps.append,
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("test",)
    assert sleeps == [10, 10]


def test_fix_poll_holds_the_seeded_head_then_resets_settlement(
    seeded_demo: dict[str, Any],
) -> None:
    """The repair SHA must not inherit the seeded failure, and a late check restarts settle."""
    failing = seeded_demo["head_sha"]
    fix = "f" * 40
    clock = Clock()
    views: list[float] = []

    def gh(args: list[str], **_kwargs: object) -> dict[str, Any]:
        now = clock.now
        if args[0] == "pr":
            views.append(now)
            if now < 20:
                return {"headRefOid": failing, "statusCheckRollup": [_demo_check()]}
            checks = [_demo_check(conclusion="SUCCESS")]
            if now >= 100:
                checks.append(_demo_check(name="lint", conclusion="SUCCESS"))
            if now < 60:
                checks = [_demo_check(status="IN_PROGRESS", conclusion="")]
            return {"headRefOid": fix, "statusCheckRollup": checks}
        if now < 60:
            return {
                "runs": [
                    {"databaseId": 1, "status": "completed"},
                    {"databaseId": 2, "status": "queued"},
                ]
            }
        return {
            "runs": [
                {"databaseId": 1, "status": "completed"},
                {"databaseId": 2, "status": "completed"},
            ]
        }

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("integrations.github.tools.ci_fix.verification.run_gh_json", gh)
        result = wait_for_pr_checks(
            _context(failing),
            github_token="tok",
            expected_head_sha=fix,
            timeout_seconds=300,
            poll_interval_seconds=10,
            registration_seconds=60,
            settle_seconds=30,
            head_propagation_seconds=30,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("test", "lint")
    assert result.failing_checks == ()
    assert views[0] == 0 and views[1] == 10
    assert views[-1] == 130
    assert 110 in views


def test_seeded_failure_does_not_count_as_the_repair_result(seeded_demo: dict[str, Any]) -> None:
    failing = seeded_demo["head_sha"]
    clock = Clock()

    def gh(args: list[str], **_kwargs: object) -> dict[str, Any]:
        assert args[0] == "pr"
        return {"headRefOid": failing, "statusCheckRollup": [_demo_check()]}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("integrations.github.tools.ci_fix.verification.run_gh_json", gh)
        result = wait_for_pr_checks(
            _context(failing),
            github_token="tok",
            expected_head_sha="f" * 40,
            timeout_seconds=300,
            poll_interval_seconds=10,
            head_propagation_seconds=30,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert result.state is CheckState.SUPERSEDED
    assert result.observed_head_sha == failing
    assert clock.now == 30


def _drive_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshots: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    *,
    deadline: float = 500,
) -> tuple[RepairRun, list[dict[str, Any]]]:
    from integrations.github.tools.ci_repair_loop import worker

    clock = Clock()
    rows = iter(snapshots)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(worker.time, "time", clock.time)
    monkeypatch.setattr(worker.time, "sleep", clock.sleep)
    monkeypatch.setattr(worker, "_read_pr", lambda *_args: next(rows))
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)

    def repair(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    run = _repair(deadline=deadline)
    store = RepairStore(tmp_path)
    store.directory(run.id).mkdir(parents=True, exist_ok=True)
    worker._repair(run, store, "test-token")
    return run, calls


def test_demo_loop_ignores_empty_and_queued_then_repairs_the_seeded_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_demo: dict[str, Any]
) -> None:
    failing = seeded_demo["head_sha"]
    fix = "a" * 40
    run, calls = _drive_repair(
        monkeypatch,
        tmp_path,
        [
            {"state": "OPEN", "headRefOid": failing, "statusCheckRollup": []},
            {
                "state": "OPEN",
                "headRefOid": failing,
                "statusCheckRollup": [_demo_check(status="QUEUED", conclusion="")],
            },
            {
                "state": "OPEN",
                "headRefOid": failing,
                "statusCheckRollup": [_demo_check()],
            },
            {
                "state": "OPEN",
                "headRefOid": fix,
                "statusCheckRollup": [_demo_check(conclusion="SUCCESS")],
            },
        ],
        [
            {
                "success": True,
                "checks_state": "passed",
                "fix_head_sha": fix,
            }
        ],
    )
    assert len(calls) == 1
    assert calls[0]["allowed_paths"] == frozenset({"calculator.py"})
    assert calls[0]["owner"] == "tester"
    assert calls[0]["pr_number"] == 1
    assert run.attempts == 1
    assert run.initial_sha == failing
    assert run.fixed_sha == fix
    assert run.checks_passed is True
    assert run.failed_run_url == _RUN_URL
    assert run.reason == "The repair commit passed CI."


def test_demo_loop_retries_one_failed_attempt_then_rejects_a_replaced_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_demo: dict[str, Any]
) -> None:
    failing = seeded_demo["head_sha"]
    fix = "b" * 40
    failure = {"state": "OPEN", "headRefOid": failing, "statusCheckRollup": [_demo_check()]}
    run, calls = _drive_repair(
        monkeypatch,
        tmp_path,
        [
            failure,
            failure,
            {"state": "OPEN", "headRefOid": "c" * 40, "statusCheckRollup": [_demo_check()]},
        ],
        [
            {"success": False, "error_kind": "checks_failed", "checks_state": "failed"},
            {"success": True, "checks_state": "passed", "fix_head_sha": fix},
        ],
    )
    assert [call["allowed_paths"] for call in calls] == [
        frozenset({"calculator.py"}),
        frozenset({"calculator.py"}),
    ]
    assert run.attempts == 2
    assert run.attempt_errors == ["checks_failed"]
    assert run.status is RepairStatus.FAILED
    assert run.reason == "Another commit replaced the verified repair."
    assert run.checks_passed is False


def test_demo_loop_times_out_while_the_seeded_check_never_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_demo: dict[str, Any]
) -> None:
    failing = seeded_demo["head_sha"]
    empty = {"state": "OPEN", "headRefOid": failing, "statusCheckRollup": []}
    run, calls = _drive_repair(
        monkeypatch,
        tmp_path,
        [empty, empty, empty, empty],
        [],
        deadline=20,
    )
    assert calls == []
    assert run.status is RepairStatus.TIMED_OUT
    assert run.attempts == 0


class _SeededGitHub:
    def __init__(self, history: list[dict[str, Any]], runs: list[dict[str, Any]]) -> None:
        self.history = history
        self.runs = runs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "GET"
        self.calls.append((path, params))
        if path.endswith("/pulls/1"):
            return {
                "base": {"sha": "base"},
                "head": {"sha": self.history[-1]["sha"], "ref": "demo/failing-ci"},
                "commits": len(self.history),
                "state": "open",
                "merged": False,
            }
        start = (params["page"] - 1) * params["per_page"]
        end = start + params["per_page"]
        if path.endswith("/commits"):
            return self.history[start:end]
        assert path.endswith("/actions/runs")
        return {"total_count": len(self.runs), "workflow_runs": self.runs[start:end]}


def _observer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: _SeededGitHub) -> Any:
    import integrations.github.ci_epochs as ci_epochs

    monkeypatch.setattr(ci_epochs, "_github_token", lambda: "unused")
    monkeypatch.setattr(ci_epochs, "GitHubRestClient", lambda _token: github)
    return ci_epochs.Observer("tester", "opensre-ci-repair-demo-poll", 1, tmp_path)


def _commit_fix(checkout: Path) -> str:
    calculator = checkout / "calculator.py"
    calculator.write_text("def add(left: int, right: int) -> int:\n    return left + right\n")
    _git(checkout, "add", "calculator.py")
    _git(
        checkout,
        "-c",
        "user.name=OpenSRE Agent",
        "-c",
        f"user.email={OPENSRE_COMMIT_COAUTHOR_EMAIL}",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Repair the seeded calculator regression",
    )
    sha = _git(checkout, "rev-parse", "HEAD")
    tested = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-v"], cwd=checkout, capture_output=True
    )
    assert tested.returncode == 0
    return sha


def test_epoch_poll_follows_the_seeded_push_then_the_linked_pull_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_demo: dict[str, Any]
) -> None:
    import integrations.github.ci_epochs as ci_epochs

    checkout = seeded_demo["checkout"]
    _git(checkout, "checkout", "demo/failing-ci")
    failing = seeded_demo["head_sha"]
    fix = _commit_fix(checkout)
    failing_row = _api_commit(checkout, failing)
    fix_row = _api_commit(checkout, fix)
    assert ci_epochs.commit_author(failing_row) != "opensre"
    assert ci_epochs.commit_author(fix_row) == "opensre"

    github = _SeededGitHub(
        [failing_row],
        [
            _run_row(1, failing, event="push", conclusion="failure", pull_number=None),
            _run_row(
                2,
                seeded_demo["seed_sha"],
                event="push",
                conclusion="success",
                pull_number=None,
            ),
        ],
    )
    observer = _observer(monkeypatch, tmp_path, github)
    observer.tick()
    assert observer.commits[0].sha == failing
    assert observer.commits[0].result == "unknown"
    assert observer.epochs == []

    github.runs.append(
        _run_row(3, failing, event="pull_request", conclusion=None, status="in_progress")
    )
    observer.tick()
    assert observer.commits[0].result == "pending"
    assert observer.epochs == []

    github.runs[-1].update(status="completed", conclusion="failure")
    observer.tick()
    assert observer.commits[0].result == "red"
    assert observer.epochs[0].outcome == "unresolved"
    assert observer.epochs[0].commits[0].sha == failing

    github.history.append(fix_row)
    github.runs.extend(
        [
            _run_row(
                4,
                fix,
                event="pull_request",
                conclusion="success",
                updated_at="2026-09-21T12:05:00Z",
            ),
            _run_row(
                5,
                fix,
                event="push",
                conclusion="failure",
                pull_number=None,
                updated_at="2026-09-21T12:04:00Z",
            ),
        ]
    )
    observer.tick()
    assert observer.epochs[0].outcome == "agent_fixed"
    assert observer.epochs[0].fixing_commit is not None
    assert observer.epochs[0].fixing_commit.sha == fix
    assert observer.epochs[0].commits[0].sha == failing
    assert all(
        params.get("event") == "pull_request"
        for path, params in github.calls
        if path.endswith("/actions/runs")
    )

    github.runs = [
        run
        for run in github.runs
        if not (run["head_sha"] == failing and run["event"] == "pull_request")
    ]
    github.runs.append(
        _run_row(6, failing, event="pull_request", conclusion="failure", pull_number=None)
    )
    observer.tick()
    assert [commit.result for commit in observer.commits] == ["unknown", "green"]
    assert observer.epochs == []


def test_epoch_publish_credits_only_the_seeded_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_demo: dict[str, Any]
) -> None:
    import integrations.github.ci_epochs as ci_epochs

    checkout = seeded_demo["checkout"]
    _git(checkout, "checkout", "demo/failing-ci")
    failing = seeded_demo["head_sha"]
    fix = _commit_fix(checkout)
    github = _SeededGitHub(
        [_api_commit(checkout, failing), _api_commit(checkout, fix)],
        [
            _run_row(1, failing, event="pull_request", conclusion="failure"),
            _run_row(
                2,
                fix,
                event="pull_request",
                conclusion="success",
                updated_at="2026-09-21T12:05:00Z",
            ),
            _run_row(3, fix, event="push", conclusion="failure", pull_number=None),
        ],
    )
    observer = _observer(monkeypatch, tmp_path, github)
    observer.tick()
    captured: list[tuple[Any, dict[str, Any]]] = []

    class Sink:
        def capture(self, event: Any, properties: dict[str, Any]) -> None:
            captured.append((event, properties))

    monkeypatch.setattr(ci_epochs, "get_analytics", lambda: Sink())
    assert observer.publish(fixing_sha="d" * 40) == 0
    assert observer.publish(fixing_sha=fix) == 1
    assert captured[0][1]["first_red_sha"] == failing
    assert captured[0][1]["fixing_sha"] == fix
    assert captured[0][1]["outcome"] == "agent_fixed"
    assert captured[0][1]["repository"] == _REPO
