"""Isolated scheduled worker: evidence-led repair of one pull request."""

from __future__ import annotations

import json
import logging
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, TypedDict

from config.constants.ci_repair import CI_REPAIR_FINISH_RESERVE_SECONDS, CI_REPAIR_MAX_ATTEMPTS
from infrastructure.analytics.provider import shutdown_analytics
from infrastructure.process.tree import start_watchdog
from integrations.coding_agent import verify_coding_agent
from integrations.git import clone_repository
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import (
    ERR_CHECKS_SUPERSEDED,
    ERR_NO_FAILING_CHECKS,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.gh import run_gh_json
from integrations.github.tools.ci_fix.ledger import record_ci_fix_outcome
from integrations.github.tools.ci_fix.runner import run_ci_fix
from integrations.github.tools.ci_fix.verification import (
    CheckState,
    all_checks_settled,
    check_failed,
    wait_for_pr_checks,
)
from integrations.github.tools.ci_repair_loop import telemetry
from integrations.github.tools.ci_repair_loop.credentials import account_id, configured_token
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.responses import object_response
from integrations.github.tools.ci_repair_loop.storage import RepairStore

logger = logging.getLogger(__name__)

#: Reason recorded on a stored run of the retired fixed-repository demo.
_RETIRED_DEMO_REASON = (
    "The fixed-repository demo was retired, so this run was stopped. Run the CI repair demo again."
)

_DEMO_REGISTRATION_SECONDS = 0
_DEMO_SETTLE_SECONDS = 0
_DEMO_POLL_INTERVAL_SECONDS = 2


class _CheckWait(TypedDict, total=False):
    """Optional check-wait overrides. Absent keys keep the real-PR defaults."""

    registration_seconds: int
    settle_seconds: int
    poll_interval_seconds: int


def _demo_repository(run: RepairRun) -> bool:
    """True for the seeded demo PR its tool just scheduled.

    ``run.fast_checks`` is set only by the seeded-demo tool, never from a repository
    name. The demo holds one known workflow and one file the repair may change.
    """
    return run.fast_checks


def _check_wait(run: RepairRun) -> _CheckWait:
    """Check-wait limits. A demo returns as soon as the head's run is terminal.

    The 60-second registration and 30-second settle windows are for a real
    repository's unknown checks.
    """
    if not _demo_repository(run):
        return {}
    return {
        "registration_seconds": _DEMO_REGISTRATION_SECONDS,
        "settle_seconds": _DEMO_SETTLE_SECONDS,
        "poll_interval_seconds": _DEMO_POLL_INTERVAL_SECONDS,
    }


def _read_pr(run: RepairRun, token: str) -> dict[str, Any]:
    return run_gh_json(
        ["pr", "view", str(run.pr_number), "--json", "state,headRefOid,statusCheckRollup"],
        repo=f"{run.owner}/{run.repo}",
        github_token=token,
        timeout=20,
    )


def _run_link(rows: list[dict[str, Any]], *, failed: bool) -> str:
    for row in rows:
        conclusion = str(row.get("conclusion") or row.get("state") or "").upper()
        matches = check_failed(row, expected_skips=set()) if failed else conclusion == "SUCCESS"
        if matches:
            link = str(row.get("detailsUrl") or row.get("targetUrl") or "")
            if link.startswith("https://github.com/"):
                return link
    return ""


def _verify_green(run: RepairRun, pr: dict[str, Any], token: str) -> bool:
    """Confirm the head's checks passed. A demo skips the extra wait once they are terminal."""
    sha = str(pr["headRefOid"])
    ctx = CiFixContext(
        owner=run.owner,
        repo=run.repo,
        number=run.pr_number,
        title="",
        url=run.pr_url,
        base_branch="",
        head_branch="",
        head_sha=sha,
        skipped_check_names=(),
        failing_checks=(),
        task="Verify the selected PR head.",
    )
    result = wait_for_pr_checks(ctx, github_token=token, expected_head_sha=sha, **_check_wait(run))
    if result.state is CheckState.FAILED:
        return False
    if result.state is CheckState.PASSED:
        current = _read_pr(run, token)
        if current.get("state") != "OPEN" or current.get("headRefOid") != sha:
            run.status, run.reason = (
                RepairStatus.CANCELLED,
                "The selected PR changed during verification.",
            )
        else:
            run.status, run.reason = (
                RepairStatus.SUCCEEDED,
                "The selected PR is already green; no repair was made.",
            )
            run.passed_run_url = _run_link(current.get("statusCheckRollup") or [], failed=False)
    else:
        run.status = (
            RepairStatus.TIMED_OUT if result.state is CheckState.TIMED_OUT else RepairStatus.FAILED
        )
        run.reason = f"The selected PR could not be verified: {result.state.value}."
    return True


def _repair(run: RepairRun, store: RepairStore, token: str) -> None:
    while time.time() < run.deadline - CI_REPAIR_FINISH_RESERVE_SECONDS:
        pr = _read_pr(run, token)
        if pr.get("state") != "OPEN":
            run.status, run.reason = RepairStatus.CANCELLED, "The PR was closed."
            return
        head = str(pr.get("headRefOid") or "")
        rows = pr.get("statusCheckRollup") or []
        failed = any(check_failed(row, expected_skips=set()) for row in rows)
        if not failed:
            # Empty or queued checks prove nothing; only a settled head is verified.
            if all_checks_settled(rows) and _verify_green(run, pr, token):
                return
            time.sleep(2)
            continue
        run.failed_run_url = run.failed_run_url or _run_link(rows, failed=True)
        run.initial_sha = run.initial_sha or str(pr["headRefOid"])
        run.attempts += 1
        run.reason = f"Repair attempt {run.attempts} is running."
        store.save(run)
        if run.attempts == 1:
            telemetry.failure_detected(run)
        output = run_ci_fix(
            owner=run.owner,
            repo=run.repo,
            pr_number=run.pr_number,
            workspace=run.workspace,
            github_token=token,
            allowed_paths=frozenset({"calculator.py"}) if _demo_repository(run) else None,
            expected_source_head_sha=head,
            **_check_wait(run),
        )
        diagnostic = store.directory(run.id) / f"attempt-{run.attempts}.json"
        diagnostic.write_text(json.dumps(output, indent=2), encoding="utf-8")
        record_ci_fix_outcome(output)
        pushed = str(output.get("fix_head_sha") or "")
        if pushed and pushed not in run.pushed_shas:
            run.pushed_shas.append(pushed)
        if output.get("success") and output.get("checks_state") == "passed":
            run.fixed_sha = str(output.get("fix_head_sha") or "")
            current = _read_pr(run, token)
            if not run.fixed_sha or current.get("headRefOid") != run.fixed_sha:
                run.status, run.reason = (
                    RepairStatus.FAILED,
                    "Another commit replaced the verified repair.",
                )
                return
            run.checks_passed = True
            run.passed_run_url = _run_link(current.get("statusCheckRollup") or [], failed=False)
            run.reason = "The repair commit passed CI."
            store.save(run)
            return
        error = str(output.get("error_kind") or "repair_failed")
        # Nothing left to fix on the current head: the earlier push may have done
        # the job while its checks were still being read as failing.
        nothing_left = error == ERR_NO_FAILING_CHECKS and bool(run.pushed_shas)
        if nothing_left and _green_after_repair(run, store, token, output):
            return
        run.attempt_errors.append(error)
        run.reason = f"Repair attempt {run.attempts}: {_reason_for(error, run)}"
        store.save(run)
        # A head that moved before any push left nothing to undo: read it again.
        moved_before_push = error == ERR_CHECKS_SUPERSEDED and not pushed
        retryable = {"checks_failed", "execution_error", "timeout", "no_changes"}
        if error not in retryable and not moved_before_push:
            run.status = RepairStatus.FAILED
            return
        if run.attempts >= CI_REPAIR_MAX_ATTEMPTS:
            run.status = RepairStatus.FAILED
            run.reason = f"Stopped after {CI_REPAIR_MAX_ATTEMPTS} failed repair attempts."
            return
        time.sleep(1)
    run.status, run.reason = RepairStatus.TIMED_OUT, "The repair reached its time budget."


#: What an attempt's error kind means for the person reading the run, in plain words.
_REASON_TEXT = {
    "unsupported_pr_branch": (
        "PR #{pr} comes from a fork; the loop only pushes to branches inside {repo}. "
        "Choose a pull request opened from a branch in this repository."
    ),
    "push_failed": "The fix was made but the push was refused; the attempt report says why.",
    "checks_failed": "The fix was pushed but CI still failed on it.",
    "no_changes": "The coding agent made no change to the checkout.",
    "timeout": "The coding agent ran out of time.",
    "execution_error": "The coding agent could not run.",
    "checks_superseded": "Another commit changed the PR head during the repair.",
}


def _reason_for(error: str, run: RepairRun) -> str:
    """The run's reason line for one attempt outcome; unknown kinds keep their code."""
    template = _REASON_TEXT.get(error)
    if template is None:
        return f"{error}."
    return template.format(pr=run.pr_number, repo=f"{run.owner}/{run.repo}")


def _green_after_repair(
    run: RepairRun, store: RepairStore, token: str, output: dict[str, Any]
) -> bool:
    """Confirm a head this run pushed passed CI once the PR reports nothing left to fix.

    A head pushed by someone else is never credited. True when the loop is
    finished (verified green, or the PR moved on); False when verification did
    not settle, so the attempt is recorded as usual.
    """
    current = _read_pr(run, token)
    head = str(current.get("headRefOid") or "")
    if head not in run.pushed_shas:
        return False
    if not _verify_green(run, current, token):
        return False
    if run.status is RepairStatus.SUCCEEDED:
        run.fixed_sha = head
        run.checks_passed = True
        run.reason = "The repair commit passed CI."
        # The ledger saw an attempt with no check state; record the verified pass
        # under the head the repair started from, as the normal success path does,
        # so one repair is one ledger entry.
        record_ci_fix_outcome(
            {
                **output,
                "success": True,
                "checks_state": CheckState.PASSED.value,
                "source_head_sha": run.initial_sha,
                "fix_head_sha": head,
            }
        )
    store.save(run)
    return True


def execute_repair(run: RepairRun, store: RepairStore) -> None:
    """Keep all remote writes inside the supervised worker and its pinned repository scope."""
    ready, _detail = verify_coding_agent()
    if not ready:
        raise ValueError("Configure and authenticate a coding agent before starting the repair.")
    token = configured_token()
    user = object_response(GitHubRestClient(token).request("GET", "user"))
    if not run.actor_id or account_id(user) != run.actor_id:
        raise ValueError("The background GitHub account changed; repair stopped.")
    directory = store.directory(run.id)
    directory.mkdir(parents=True, exist_ok=True)
    workspace = directory / "checkout"
    run.workspace = str(workspace)
    store.save(run)
    if not workspace.exists():
        clone_repository(run.repository_url + ".git", run.workspace, token=token)
    if not run.checks_passed:
        _repair(run, store, token)
    if run.checks_passed:
        run.status = RepairStatus.SUCCEEDED
        telemetry.repair_succeeded(run)


def run_ci_repair_worker(store_directory: Path, run_id: str) -> None:
    """Run a persisted repair in the supervised child process."""
    logging.basicConfig(level=logging.INFO)
    store = RepairStore(store_directory)
    run = store.get(run_id)
    work_deadline = run.deadline - CI_REPAIR_FINISH_RESERVE_SECONDS
    watchdog = start_watchdog(work_deadline)
    try:
        if time.time() >= work_deadline:
            run.status, run.reason = RepairStatus.TIMED_OUT, "The original deadline has expired."
        elif run.demo:
            # Its fixture, branch guard, and cleanup are gone; touch nothing on GitHub.
            run.status, run.reason = RepairStatus.FAILED, _RETIRED_DEMO_REASON
        else:
            run.status = RepairStatus.RUNNING
            store.save(run)
            try:
                execute_repair(run, store)
            except GitHubApiError as exc:
                logger.exception("GitHub request failed during CI repair")
                run.status = RepairStatus.FAILED
                run.reason = (
                    "GitHub authorization failed; repair stopped."
                    if exc.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
                    else "GitHub could not complete the repair; inspect retained diagnostics."
                )
            except (ValueError, GitHubCiFixError):
                logger.exception("CI repair could not continue")
                run.status, run.reason = (
                    RepairStatus.FAILED,
                    "The repair could not continue; inspect the local worker log.",
                )
            except Exception as exc:
                logger.exception("CI repair worker failed")
                run.status, run.reason = (
                    RepairStatus.FAILED,
                    f"Repair stopped: {type(exc).__name__}.",
                )
        if run.terminal:
            store.discard_checkout(run)
        run.finished_at = time.time()
        store.save(run)
    finally:
        watchdog.set()
        shutdown_analytics(flush=True, timeout=5)
