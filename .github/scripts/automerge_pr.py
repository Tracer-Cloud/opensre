#!/usr/bin/env python3
"""Merge a pull request once a maintainer approved its head commit and CI is green.

The ``automerge`` label stays on a pull request while its author keeps pushing,
so the label alone says nothing about the commit being merged. The approval is
therefore recorded against one commit, as a status only this repository's
Actions token writes, and a merge needs that status on the exact head it merges.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

AUTOMERGE_LABEL = "automerge"
# Written on the commit a maintainer approved; a later push has no such status.
APPROVAL_STATUS_CONTEXT = "automerge/approved"
# Pull request code never runs with a token that can write statuses here, so a
# status from this account can only come from the trusted base-branch workflow.
APPROVAL_STATUS_CREATOR = "github-actions[bot]"
APPROVAL_EVENT_ACTIONS = frozenset({"labeled", "synchronize"})
# The legacy ``permission`` field reports maintain as write and triage as read.
WRITE_PERMISSIONS = frozenset({"admin", "write"})
RECORD_APPROVAL_COMMAND = "record-approval"
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
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


def _actor_can_write(repo: str, login: str) -> bool:
    """Whether ``login`` may push to ``repo``; an unknown account may not."""
    if not login:
        return False
    try:
        permission = _run_gh(["api", f"repos/{repo}/collaborators/{login}/permission"])
    except subprocess.CalledProcessError:
        return False
    return isinstance(permission, dict) and permission.get("permission") in WRITE_PERMISSIONS


def _head_is_approved(repo: str, head_sha: str) -> bool:
    """Whether the newest approval status on ``head_sha`` is a trusted success."""
    pages = _run_gh(["api", "--paginate", "--slurp", f"repos/{repo}/commits/{head_sha}/statuses"])
    for page in pages:
        for status in page:
            if status.get("context") != APPROVAL_STATUS_CONTEXT:
                continue
            # Newest first: the first match is the current state of the approval.
            creator = (status.get("creator") or {}).get("login")
            return status.get("state") == "success" and creator == APPROVAL_STATUS_CREATOR
    return False


def record_approval() -> int:
    """Approve the head a writer just labeled or pushed; otherwise withdraw the label.

    A push from someone without write access leaves the label on a commit no
    maintainer has seen, so the label comes off and has to be added again.
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    action = os.environ.get("EVENT_ACTION", "")
    if action not in APPROVAL_EVENT_ACTIONS:
        return 0
    if action == "synchronize" and os.environ.get("HAS_AUTOMERGE_LABEL") != "true":
        return 0

    head_sha = os.environ.get("PR_HEAD_SHA", "")
    actor = os.environ.get("EVENT_ACTOR", "")
    if _COMMIT_SHA.fullmatch(head_sha) and _actor_can_write(repo, actor):
        _run_gh(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repo}/statuses/{head_sha}",
                "-f",
                "state=success",
                "-f",
                f"context={APPROVAL_STATUS_CONTEXT}",
                "-f",
                f"description=Approved for auto-merge by {actor}"[:140],
            ]
        )
        print(f"PR #{pr_number}: {actor} approved {head_sha} for auto-merge.")
        return 0

    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            f"repos/{repo}/issues/{pr_number}/labels/{AUTOMERGE_LABEL}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    print(
        f"PR #{pr_number}: {actor or 'unknown actor'} cannot approve {head_sha or 'this head'}; "
        f"removed the {AUTOMERGE_LABEL} label. A maintainer must add it again."
    )
    return 0


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

    if not _head_is_approved(repo, head_sha):
        print(
            f"PR #{pr_number} head {head_sha} was not approved by a maintainer; "
            f"re-add the {AUTOMERGE_LABEL} label to approve it."
        )
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
        if sys.argv[1:] == [RECORD_APPROVAL_COMMAND]:
            raise SystemExit(record_approval())
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
