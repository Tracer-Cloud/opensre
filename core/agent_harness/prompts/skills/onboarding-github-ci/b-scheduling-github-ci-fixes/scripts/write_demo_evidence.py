"""Save the observed demo outcome before removing its owned temporary checkout."""

from __future__ import annotations

import shutil
from datetime import date
from typing import Any

from _demo_state import (
    atomic_write,
    demo_key,
    owned_workspace,
    read_receipt,
    receipt_path,
    results_directory,
    run_json,
)


def write_demo_evidence(
    repo: str,
    pr_number: int,
    loop_id: str,
    outcome: str,
    failed_run_id: int | None = None,
    fix_commit: str | None = None,
    passing_run_id: int | None = None,
    blocker: str = "",
) -> dict[str, Any]:
    """Keep failed-demo evidence and preserve the checkout if writing fails."""
    key = demo_key(repo)
    if outcome not in {"success", "failed", "blocked"}:
        raise ValueError("outcome must be success, failed, or blocked")
    if outcome == "success" and not (failed_run_id and fix_commit and passing_run_id):
        raise ValueError(
            "Successful repair evidence requires failed run, fix commit, and passing run."
        )
    state = read_receipt(repo)
    workspace = owned_workspace(state) if state else None
    evidence = results_directory() / f"ci-repair-demo-{date.today().isoformat()}-{key}.md"
    content = (
        f"# CI repair demo\n\n"
        f"- Repository: {repo}\n"
        f"- PR: https://github.com/{repo}/pull/{pr_number}\n"
        f"- Outcome: {outcome}\n"
        f"- Failed run: {failed_run_id or 'none'}\n"
        f"- Loop: {loop_id}\n"
        f"- Fix commit: {fix_commit or 'none'}\n"
        f"- Passing run: {passing_run_id or 'none'}\n"
        f"- Blocker: {blocker or 'none'}\n"
        "- Repository retained.\n"
        "- Scheduler removal is verified separately by the workflow.\n"
    )
    atomic_write(evidence, content + "- Checkout cleanup: pending.\n")
    removed = False
    try:
        if workspace is not None:
            shutil.rmtree(workspace)
        removed = True
        receipt_path(repo).unlink(missing_ok=True)
        atomic_write(evidence, content + "- Checkout cleanup: complete.\n")
    except OSError as exc:
        return {
            "ok": False,
            "error": "Evidence saved, but cleanup or its final status could not be completed.",
            "diagnostics": str(exc),
            "evidence": str(evidence),
            "checkout_removed": removed,
        }
    return {
        "ok": True,
        "evidence": str(evidence),
        "checkout_removed": True,
        "summary": "Saved demo evidence and removed the temporary checkout.",
    }


if __name__ == "__main__":
    run_json(write_demo_evidence)
