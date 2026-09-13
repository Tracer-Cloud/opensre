"""Translate GitHub repair evidence into scheduler work outcomes."""

from typing import Any

from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus


def attach_ci_scan_outcome(output: dict[str, Any], *, fully_inspected: bool) -> dict[str, Any]:
    """Record no repair needed only for a complete scan of mergeable open PRs."""
    prs = output["pull_requests"]
    if not fully_inspected or any(pr["status"] != "mergeable" for pr in prs):
        return output
    owner, repo = output["owner"], output["repo"]
    outcome = WorkOutcome(
        status=WorkStatus.NOOP,
        operation=f"ci-scan:{owner.casefold()}/{repo.casefold()}",
        evidence={
            "owner": owner,
            "repo": repo,
            "checked_heads": {str(pr["number"]): pr["head_sha"] for pr in prs},
        },
    )
    return {**output, "work_outcome": outcome.model_dump(mode="json")}


def attach_repair_outcome(output: dict[str, Any], *, operation: str) -> dict[str, Any]:
    """Attach a terminal outcome derived from the repair tool's verification result."""
    owner, repo = output.get("owner"), output.get("repo")
    if owner and repo:
        prefix = operation.partition(":")[0]
        if prefix == "ci":
            number = output.get("pr_number")
            target = (
                f"pr:{number}" if number is not None else f"branch:{output.get('target_branch')}"
            )
        else:
            target = f"{output.get('alert_type')}:{output.get('alert_number')}"
        operation = f"{prefix}:{str(owner).casefold()}/{str(repo).casefold()}:{target}"
    kind = str(output.get("error_kind") or "")
    if kind == "no_failing_checks":
        status = WorkStatus.NOOP
    elif kind in {"checks_timeout", "checks_superseded", "timeout"}:
        status = WorkStatus.INCOMPLETE
    elif kind in {
        "repo_mismatch",
        "repo_scope_unresolved",
        "workspace_busy",
        "invalid_input",
        "github_token_missing",
        "confirmation_denied",
        "unsupported_pr_branch",
        "cli_unavailable",
        "pr_not_open",
        "alert_not_found",
    }:
        status = WorkStatus.BLOCKED
    elif output.get("success") is True:
        status = WorkStatus.SUCCEEDED
    else:
        status = WorkStatus.FAILED
    evidence: dict[str, Any] = {
        key: output[key]
        for key in (
            "owner",
            "repo",
            "pr_number",
            "pr_url",
            "source_head_sha",
            "fix_head_sha",
            "branch_name",
            "checks_state",
            "check_names",
            "alert_number",
            "alert_type",
        )
        if key in output
    }
    outcome = WorkOutcome(status=status, error_kind=kind, operation=operation, evidence=evidence)
    return {**output, "work_outcome": outcome.model_dump(mode="json")}
