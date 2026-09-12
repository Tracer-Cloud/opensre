"""Isolated scheduled worker: fixture, evidence-led repair, and outcome-specific cleanup."""

from __future__ import annotations

import json
import logging
import shutil
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from config.constants.ci_repair import CI_REPAIR_FINISH_RESERVE_SECONDS
from infrastructure.process.tree import start_watchdog
from integrations.coding_agent import verify_coding_agent
from integrations.git import clone_repository
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import GitHubCiFixError
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
        if output.get("success") and output.get("checks_state") == "passed":
            run.fixed_sha = str(output.get("pushed_head_sha") or "")
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
        run.attempt_errors.append(error)
        run.reason = f"Repair attempt {run.attempts}: {error}."
        store.save(run)
        if error not in {"checks_failed", "execution_error", "timeout", "no_changes"}:
            run.status = RepairStatus.FAILED
            return
        time.sleep(1)
    run.status, run.reason = RepairStatus.TIMED_OUT, "The demo reached its time budget."


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
        if run.demo:
            shutil.rmtree(workspace, ignore_errors=True)
        run.status = RepairStatus.SUCCEEDED


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
