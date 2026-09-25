"""Isolated scheduled worker: fixture, evidence-led repair, and outcome-specific cleanup."""

from __future__ import annotations

import json
import logging
import shutil
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from config.constants.ci_repair import CI_REPAIR_FINISH_RESERVE_SECONDS, CI_REPAIR_MAX_ATTEMPTS
from infrastructure.analytics.provider import shutdown_analytics
from infrastructure.process.tree import start_watchdog
from integrations.coding_agent import verify_coding_agent
from integrations.git import clone_repository
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import ERR_NO_FAILING_CHECKS, GitHubCiFixError
from integrations.github.tools.ci_fix.gh import run_gh_json
from integrations.github.tools.ci_fix.ledger import record_ci_fix_outcome
from integrations.github.tools.ci_fix.runner import run_ci_fix
from integrations.github.tools.ci_fix.verification import (
    CheckState,
    check_failed,
    wait_for_pr_checks,
)
from integrations.github.tools.ci_repair_loop.credentials import account_id, configured_token
from integrations.github.tools.ci_repair_loop.fixture import (
    DemoRepositoryMismatch,
    cleanup_demo,
    object_response,
    prepare_demo,
)
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore

logger = logging.getLogger(__name__)


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
    """Require the normal registration and settlement windows even when no edit is needed."""
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
    result = wait_for_pr_checks(ctx, github_token=token, expected_head_sha=sha)
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
        rows = pr.get("statusCheckRollup") or []
        failed = any(check_failed(row, expected_skips=set()) for row in rows)
        if not failed:
            # A new fixture must first be observed failing. Empty or queued checks prove nothing.
            if (
                not run.demo
                and rows
                and all(
                    str(row.get("conclusion") or row.get("state") or "").upper() == "SUCCESS"
                    for row in rows
                )
                and _verify_green(run, pr, token)
            ):
                return
            time.sleep(2)
            continue
        run.failed_run_url = run.failed_run_url or _run_link(rows, failed=True)
        run.initial_sha = run.initial_sha or str(pr["headRefOid"])
        run.attempts += 1
        run.reason = f"Repair attempt {run.attempts} is running."
        store.save(run)
        output = run_ci_fix(
            owner=run.owner,
            repo=run.repo,
            pr_number=run.pr_number,
            workspace=run.workspace,
            github_token=token,
            allowed_paths=frozenset({"calculator.py"}) if run.demo else None,
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
        if error not in {"checks_failed", "execution_error", "timeout", "no_changes"}:
            run.status = RepairStatus.FAILED
            return
        if run.attempts >= CI_REPAIR_MAX_ATTEMPTS:
            run.status = RepairStatus.FAILED
            run.reason = f"Stopped after {CI_REPAIR_MAX_ATTEMPTS} failed repair attempts."
            return
        time.sleep(1)
    run.status, run.reason = RepairStatus.TIMED_OUT, "The demo reached its time budget."


#: What an attempt's error kind means for the person reading the run, in plain words.
_REASON_TEXT = {
    "unsupported_pr_branch": (
        "PR #{pr} comes from a fork; the loop only pushes to branches inside {repo}. "
        "Choose a pull request opened from a branch in this repository."
    ),
    "push_failed": (
        "The fix was made but the push was refused; the attempt report names the token "
        "change to make."
    ),
    "checks_failed": "The fix was pushed but CI still failed on it.",
    "no_changes": "The coding agent made no change to the checkout.",
    "timeout": "The coding agent ran out of time.",
    "execution_error": "The coding agent could not run.",
}


def _reason_for(error: str, run: RepairRun) -> str:
    """The run's reason line for one attempt outcome; unknown kinds keep their code."""
    template = _REASON_TEXT.get(error)
    if template is None:
        return f"{error}."
    return template.format(pr=run.pr_number, repo=f"{run.owner}/{run.repo}")


def _discard_checkout(run: RepairRun) -> None:
    """Remove a finished run's checkout; the report and attempt records stay.

    A checkout of a real repository is hundreds of megabytes on the shared
    volume and nothing reads it once the run is terminal.
    """
    if not run.workspace:
        return
    shutil.rmtree(run.workspace, ignore_errors=True)
    if not run.demo:
        # A demo run already describes its own cleanup of the demo resources.
        run.cleanup = "Checkout removed; report and attempt records retained."


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
    """Keep all remote writes inside the supervised worker and its fixed repository scope."""
    ready, _detail = verify_coding_agent()
    if not ready:
        raise ValueError("Configure and authenticate a coding agent before starting the demo.")
    token = configured_token()
    client = GitHubRestClient(token)
    user = object_response(client.request("GET", "user"))
    if not run.actor_id or account_id(user) != run.actor_id:
        raise ValueError("The background GitHub account changed; repair stopped.")
    if run.demo:
        prepare_demo(client, run, store)
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
        cleanup_demo(client, run)
        run.status = RepairStatus.SUCCEEDED
    if run.terminal:
        _discard_checkout(run)


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
                    else "GitHub could not complete the demo; inspect retained diagnostics."
                )
            except DemoRepositoryMismatch:
                logger.exception("Demo repository ownership or baseline check failed")
                run.status, run.reason = (
                    RepairStatus.FAILED,
                    "The fixed repository is unrecognized or modified; existing files were preserved.",
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
        run.finished_at = time.time()
        store.save(run)
    finally:
        watchdog.set()
        shutdown_analytics(flush=True, timeout=5)
