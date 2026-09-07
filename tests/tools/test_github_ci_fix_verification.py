"""Tests for post-push GitHub PR check verification."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from integrations.github.tools.ci_fix.context import CiFixContext, FailingCheck
from integrations.github.tools.ci_fix.verification import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CheckState,
    _workflow_runs_state,
    wait_for_branch_checks,
    wait_for_pr_checks,
)

_CONTEXT = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=4597,
    title="feat: add fixer",
    url="https://github.com/Tracer-Cloud/opensre/pull/4597",
    base_branch="main",
    head_branch="feat/fix-ci",
    head_sha="old-sha",
    skipped_check_names=(),
    failing_checks=(
        FailingCheck(
            name="quality",
            conclusion="failure",
            details_url="",
            workflow_name="CI",
        ),
    ),
    task="Fix CI.",
)


@pytest.fixture(autouse=True)
def _completed_workflow_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.github.tools.ci_fix.verification._workflow_runs_state",
        lambda **_kwargs: (True, ("1:completed",)),
    )


def _rollup(
    *,
    sha: str,
    name: str,
    conclusion: str,
    status: str,
    workflow_name: str = "CI",
) -> dict:
    return {
        "headRefOid": sha,
        "statusCheckRollup": [
            {
                "name": name,
                "conclusion": conclusion,
                "status": status,
                "workflowName": workflow_name,
            }
        ],
    }


_BRANCH_CONTEXT = replace(
    _CONTEXT,
    number=None,
    title="",
    url="https://github.com/Tracer-Cloud/opensre/tree/main",
    head_branch="main",
)


def test_wait_for_branch_checks_waits_for_commit_runs_then_passes() -> None:
    responses = [
        {"runs": [{"databaseId": 1, "name": "CI", "status": "in_progress", "conclusion": ""}]},
        {"runs": [{"databaseId": 1, "name": "CI", "status": "completed", "conclusion": "success"}]},
        {"check_runs": [{"name": "CI job", "status": "completed", "conclusion": "success"}]},
        {"state": "success", "statuses": []},
    ]
    sleeps: list[float] = []

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ) as run_gh:
        result = wait_for_branch_checks(
            _BRANCH_CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            timeout_seconds=30,
            poll_interval_seconds=1,
            settle_seconds=0,
            sleep=sleeps.append,
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("CI",)
    assert sleeps == [1]
    assert run_gh.call_args_list[0].args[0][:4] == ["run", "list", "--commit", "new-sha"]


def test_wait_for_branch_checks_reports_failing_run() -> None:
    payload = {
        "runs": [
            {"databaseId": 1, "name": "CI", "status": "completed", "conclusion": "failure"},
            {"databaseId": 2, "name": "CodeQL", "status": "completed", "conclusion": "success"},
        ]
    }

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_branch_checks(
            _BRANCH_CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("CI",)


def test_wait_for_branch_checks_fails_on_external_commit_status() -> None:
    # Actions runs alone are not the whole picture: a failing non-Actions
    # check app or commit status must block the "all checks passed" verdict.
    responses = [
        {"runs": [{"databaseId": 1, "name": "CI", "status": "completed", "conclusion": "success"}]},
        {"check_runs": [{"name": "CI job", "status": "completed", "conclusion": "success"}]},
        {"state": "failure", "statuses": [{"context": "greptile/review", "state": "failure"}]},
    ]

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_branch_checks(
            _BRANCH_CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("greptile/review",)


def test_wait_for_branch_checks_defers_verdict_while_legacy_status_pending() -> None:
    # A pending commit status is external CI still running: the verdict must
    # wait for it rather than reporting PASSED from completed workflows alone.
    completed_runs = {
        "runs": [{"databaseId": 1, "name": "CI", "status": "completed", "conclusion": "success"}]
    }
    completed_check_runs = {
        "check_runs": [{"name": "CI job", "status": "completed", "conclusion": "success"}]
    }
    responses = [
        completed_runs,
        completed_check_runs,
        {"state": "pending", "statuses": [{"context": "external/ci", "state": "pending"}]},
        completed_runs,
        completed_check_runs,
        {"state": "success", "statuses": [{"context": "external/ci", "state": "success"}]},
    ]
    sleeps: list[float] = []

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_branch_checks(
            _BRANCH_CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            timeout_seconds=30,
            poll_interval_seconds=1,
            settle_seconds=0,
            sleep=sleeps.append,
        )

    assert result.state is CheckState.PASSED
    assert sleeps == [1]


def test_wait_for_branch_checks_times_out_when_no_runs_register() -> None:
    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value={"runs": []},
    ):
        result = wait_for_branch_checks(
            _BRANCH_CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            timeout_seconds=0,
        )

    assert result.state is CheckState.TIMED_OUT
    assert result.check_names == ()


def test_workflow_runs_state_uses_exact_commit() -> None:
    responses = [
        {"runs": []},
        {
            "runs": [
                {"databaseId": 1, "status": "completed"},
                {"databaseId": 2, "status": "in_progress"},
            ]
        },
        {
            "runs": [
                {"databaseId": 1, "status": "completed"},
                {"databaseId": 2, "status": "completed"},
            ]
        },
    ]

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ) as run_gh:
        assert _workflow_runs_state(
            repo="Tracer-Cloud/opensre",
            github_token="tok",
            expected_head_sha="new-sha",
            require_run=True,
        ) == (False, ())
        assert _workflow_runs_state(
            repo="Tracer-Cloud/opensre",
            github_token="tok",
            expected_head_sha="new-sha",
            require_run=True,
        ) == (False, ("1:completed", "2:in_progress"))
        assert _workflow_runs_state(
            repo="Tracer-Cloud/opensre",
            github_token="tok",
            expected_head_sha="new-sha",
            require_run=True,
        ) == (True, ("1:completed", "2:completed"))

    assert run_gh.call_args_list[0].args[0][:4] == ["run", "list", "--commit", "new-sha"]


def test_wait_for_pr_checks_ignores_stale_checks_then_waits_for_success() -> None:
    responses = [
        _rollup(sha="old-sha", name="quality", conclusion="FAILURE", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="", status="IN_PROGRESS"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
    ]
    sleeps: list[float] = []

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            timeout_seconds=30,
            poll_interval_seconds=1,
            settle_seconds=0,
            sleep=sleeps.append,
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("quality",)
    assert sleeps == [1, 1]


def test_wait_for_pr_checks_reports_terminal_failure() -> None:
    payload = _rollup(
        sha="new-sha",
        name="quality",
        conclusion="FAILURE",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("quality",)


def test_wait_for_pr_checks_waits_for_terminal_rollup_to_settle() -> None:
    responses = [
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        {
            "headRefOid": "new-sha",
            "statusCheckRollup": [
                {
                    "name": "quality",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "workflowName": "CI",
                },
                {
                    "name": "tests",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "workflowName": "CI",
                },
            ],
        },
        {
            "headRefOid": "new-sha",
            "statusCheckRollup": [
                {
                    "name": "quality",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "workflowName": "CI",
                },
                {
                    "name": "tests",
                    "conclusion": "SUCCESS",
                    "status": "COMPLETED",
                    "workflowName": "CI",
                },
            ],
        },
    ]
    sleeps: list[float] = []
    elapsed = iter((0.0, 0.0, 1.0, 3.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=2,
            sleep=sleeps.append,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.PASSED
    assert result.check_names == ("quality", "tests")
    assert sleeps == [DEFAULT_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS]


def test_wait_for_pr_checks_accepts_check_that_was_already_skipped() -> None:
    context = replace(
        _CONTEXT,
        skipped_check_names=("windows test",),
    )
    payload = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
                "workflowName": "CI",
            },
            {
                "name": "windows test",
                "conclusion": "SKIPPED",
                "status": "COMPLETED",
                "workflowName": "CI",
            },
        ],
    }

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            context,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.PASSED
    assert result.failing_checks == ()


def test_wait_for_pr_checks_rejects_newly_skipped_check() -> None:
    payload = _rollup(
        sha="new-sha",
        name="quality",
        conclusion="SKIPPED",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("quality",)


def test_wait_for_pr_checks_times_out_when_new_checks_never_appear() -> None:
    payload = {"headRefOid": "new-sha", "statusCheckRollup": []}
    elapsed = iter((0.0, 0.0, 10.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            timeout_seconds=10,
            poll_interval_seconds=1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.TIMED_OUT
    assert result.check_names == ()


def test_wait_for_pr_checks_waits_for_failing_external_check_to_register() -> None:
    context = replace(
        _CONTEXT,
        failing_checks=(
            FailingCheck(
                name="external security scan",
                conclusion="failure",
                details_url="https://ci.example.test/run/1",
                workflow_name="",
            ),
        ),
    )
    payload = _rollup(
        sha="new-sha",
        name="quality",
        conclusion="SUCCESS",
        status="COMPLETED",
    )
    elapsed = iter((0.0, 0.0, 10.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            context,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            timeout_seconds=10,
            poll_interval_seconds=1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.TIMED_OUT


def test_wait_for_pr_checks_waits_for_late_check_in_running_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late_failure = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
                "workflowName": "CI",
            },
            {
                "name": "tests",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
                "workflowName": "CI",
            },
        ],
    }
    responses = [
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        late_failure,
        late_failure,
    ]
    elapsed = iter((0.0, 0.0, 31.0, 32.0, 62.0))
    workflow_states = iter(
        (
            (False, ("1:in_progress",)),
            (False, ("1:in_progress",)),
            (True, ("1:completed",)),
            (True, ("1:completed",)),
        )
    )
    monkeypatch.setattr(
        "integrations.github.tools.ci_fix.verification._workflow_runs_state",
        lambda **_kwargs: next(workflow_states),
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=30,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("tests",)


def test_wait_for_pr_checks_resets_settle_for_late_second_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late_failure = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
            },
            {
                "name": "security",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
            },
        ],
    }
    responses = [
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        late_failure,
        late_failure,
    ]
    workflow_states = iter(
        (
            (True, ("1:completed",)),
            (True, ("1:completed",)),
            (True, ("1:completed", "2:completed")),
            (True, ("1:completed", "2:completed")),
            (True, ("1:completed", "2:completed")),
            (True, ("1:completed", "2:completed")),
        )
    )
    elapsed = iter((0.0, 60.0, 89.0, 91.0, 120.0, 122.0, 152.0))
    monkeypatch.setattr(
        "integrations.github.tools.ci_fix.verification._workflow_runs_state",
        lambda **_kwargs: next(workflow_states),
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=60,
            settle_seconds=30,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("security",)


def test_wait_for_pr_checks_registration_grace_catches_late_external_check() -> None:
    late_pending = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
            },
            {
                "name": "external security scan",
                "conclusion": "",
                "status": "IN_PROGRESS",
            },
        ],
    }
    late_failure = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
            },
            {
                "name": "external security scan",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
            },
        ],
    }
    responses = [
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        late_pending,
        late_failure,
        late_failure,
        late_failure,
    ]
    elapsed = iter((0.0, 0.0, 31.0, 40.0, 70.0, 100.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=60,
            settle_seconds=30,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("external security scan",)


def test_wait_for_pr_checks_starts_registration_after_exact_head_is_visible() -> None:
    late_failure = {
        "headRefOid": "new-sha",
        "statusCheckRollup": [
            {
                "name": "quality",
                "conclusion": "SUCCESS",
                "status": "COMPLETED",
            },
            {
                "name": "external security scan",
                "conclusion": "FAILURE",
                "status": "COMPLETED",
            },
        ],
    }
    responses = [
        _rollup(sha="old-sha", name="quality", conclusion="FAILURE", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        _rollup(sha="new-sha", name="quality", conclusion="SUCCESS", status="COMPLETED"),
        late_failure,
    ]
    elapsed = iter((0.0, 0.0, 59.0, 90.0, 120.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=60,
            settle_seconds=0,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.FAILED
    assert result.failing_checks == ("external security scan",)


def test_wait_for_pr_checks_rejects_unrelated_newer_head() -> None:
    payload = _rollup(
        sha="other-users-sha",
        name="quality",
        conclusion="SUCCESS",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="our-pushed-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state.value == "superseded"
    assert result.observed_head_sha == "other-users-sha"


def test_wait_for_pr_checks_rejects_original_head_restored_after_fix() -> None:
    responses = [
        _rollup(
            sha="our-pushed-sha",
            name="quality",
            conclusion="",
            status="IN_PROGRESS",
        ),
        _rollup(
            sha="old-sha",
            name="quality",
            conclusion="FAILURE",
            status="COMPLETED",
        ),
    ]

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="our-pushed-sha",
            registration_seconds=0,
            settle_seconds=0,
            sleep=lambda _seconds: None,
        )

    assert result.state is CheckState.SUPERSEDED
    assert result.observed_head_sha == "old-sha"


def test_wait_for_pr_checks_rejects_original_head_after_propagation_grace() -> None:
    payload = _rollup(
        sha="old-sha",
        name="quality",
        conclusion="FAILURE",
        status="COMPLETED",
    )
    elapsed = iter((0.0, 0.0, 31.0))

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="our-pushed-sha",
            registration_seconds=0,
            settle_seconds=0,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(elapsed),
        )

    assert result.state is CheckState.SUPERSEDED
    assert result.observed_head_sha == "old-sha"


def test_wait_for_pr_checks_ignores_absent_conditional_sibling() -> None:
    payload = _rollup(
        sha="new-sha",
        name="quality",
        conclusion="SUCCESS",
        status="COMPLETED",
    )

    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        return_value=payload,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            registration_seconds=0,
            settle_seconds=0,
        )

    assert result.state is CheckState.PASSED


def test_wait_for_pr_checks_stops_at_once_when_pushed_head_is_conflicted() -> None:
    # Arrange: GitHub computed the merge state for the pushed head and it is DIRTY.
    responses = [
        {"headRefOid": "new-sha", "mergeStateStatus": "UNKNOWN", "statusCheckRollup": []},
        {"headRefOid": "new-sha", "mergeStateStatus": "DIRTY", "statusCheckRollup": []},
    ]
    sleeps: list[float] = []

    # Act
    with patch(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        side_effect=responses,
    ):
        result = wait_for_pr_checks(
            _CONTEXT,
            github_token="tok",
            expected_head_sha="new-sha",
            timeout_seconds=900,
            poll_interval_seconds=1,
            sleep=sleeps.append,
        )

    # Assert
    assert result.state is CheckState.CONFLICTED
    assert sleeps == [1]
