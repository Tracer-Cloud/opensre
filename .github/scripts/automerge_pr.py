#!/usr/bin/env python3
"""Merge a pull request when it is labeled automerge and CI checks are green."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

AUTOMERGE_LABEL = "automerge"
AUTOMERGE_WORKFLOW_NAME = "Auto-merge"
AUTOMERGE_JOB_CHECK_NAME = "Merge when CI is green"
# Greptile is advisory for this automation; all other checks must pass.
AUTOMERGE_SKIPPED_CHECK_SUBSTRINGS = ("greptile",)
CHECK_RUN_PENDING_STATUSES = frozenset({"IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED"})
CHECK_RUN_ALLOWED_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
STATUS_CONTEXT_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
STATUS_CONTEXT_ALLOWED_STATES = frozenset({"SUCCESS"})


def _check_display_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "unknown")


def _is_automerge_workflow_check(check: dict[str, Any]) -> bool:
    if check.get("workflowName") == AUTOMERGE_WORKFLOW_NAME:
        return True
    return check.get("name") == AUTOMERGE_JOB_CHECK_NAME


def _is_skipped_automerge_check(check: dict[str, Any]) -> bool:
    if _is_automerge_workflow_check(check):
        return True
    name = _check_display_name(check).casefold()
    return any(marker in name for marker in AUTOMERGE_SKIPPED_CHECK_SUBSTRINGS)


def _check_run_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    name = _check_display_name(check)
    status = check.get("status", "")
    conclusion = check.get("conclusion") or ""

    if status in CHECK_RUN_PENDING_STATUSES:
        return False, f"check still running: {name}"

    if status != "COMPLETED":
        return False, f"unexpected check status for {name}: {status or 'missing status'}"

    if conclusion not in CHECK_RUN_ALLOWED_CONCLUSIONS:
        return False, f"check not green: {name} ({conclusion or 'missing conclusion'})"

    return True, ""


def _status_context_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    name = _check_display_name(check)
    state = check.get("state") or ""

    if state in STATUS_CONTEXT_PENDING_STATES:
        return False, f"status still pending: {name}"

    if state not in STATUS_CONTEXT_ALLOWED_STATES:
        return False, f"status not green: {name} ({state or 'missing state'})"

    return True, ""


def _rollup_item_is_green(check: dict[str, Any]) -> tuple[bool, str]:
    typename = check.get("__typename", "")
    if typename == "StatusContext":
        return _status_context_is_green(check)
    if typename == "CheckRun":
        return _check_run_is_green(check)
    if "state" in check and "status" not in check:
        return _status_context_is_green(check)
    return _check_run_is_green(check)


def _squash_commit_subject(title: str, pr_number: str) -> str:
    suffix = f"(#{pr_number})"
    stripped = title.rstrip()
    if stripped.endswith(suffix):
        return stripped
    return f"{stripped} {suffix}"


def _checks_are_green(status_rollup: list[dict[str, Any]]) -> tuple[bool, str]:
    if not status_rollup:
        return False, "no status checks reported yet"

    gates = [check for check in status_rollup if check.get("name") == "CI Gate"]
    if not gates or any(
        gate.get("status") != "COMPLETED" or gate.get("conclusion") != "SUCCESS" for gate in gates
    ):
        return False, "CI Gate has not succeeded"

    for check in status_rollup:
        if _is_skipped_automerge_check(check):
            continue
        green, reason = _rollup_item_is_green(check)
        if not green:
            return False, reason

    return True, "all checks green"


def _run_gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]

    pr = _run_gh(
        [
            "pr",
            "view",
            pr_number,
            "--repo",
            repo,
            "--json",
            "baseRefName,headRefOid,isDraft,mergeable,mergeStateStatus,labels,state,statusCheckRollup,title",
        ]
    )

    if pr.get("baseRefName") != "main":
        print(f"PR #{pr_number} does not target main; skipping.")
        return 0

    if pr.get("state") != "OPEN":
        print(f"PR #{pr_number} is not open; skipping.")
        return 0

    if pr.get("isDraft"):
        print(f"PR #{pr_number} is a draft; skipping.")
        return 0

    label_names = {label["name"] for label in pr.get("labels", [])}
    if AUTOMERGE_LABEL not in label_names:
        print(f"PR #{pr_number} does not have the {AUTOMERGE_LABEL} label; skipping.")
        return 0

    if pr.get("mergeable") != "MERGEABLE":
        print(f"PR #{pr_number} is not mergeable ({pr.get('mergeStateStatus')}); skipping.")
        return 0

    green, reason = _checks_are_green(pr.get("statusCheckRollup") or [])
    if not green:
        print(f"PR #{pr_number} not ready to merge: {reason}")
        return 0

    head_sha = pr.get("headRefOid")
    if not head_sha:
        print(f"PR #{pr_number} has no head SHA; skipping.")
        return 0

    title = pr["title"]
    print(f"Merging PR #{pr_number}: {title}")
    subprocess.run(
        [
            "gh",
            "pr",
            "merge",
            pr_number,
            "--repo",
            repo,
            "--squash",
            "--match-head-commit",
            head_sha,
            "--delete-branch",
            "--subject",
            _squash_commit_subject(title, pr_number),
        ],
        check=True,
    )
    print(f"Merged PR #{pr_number}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
