"""Recover a pushed repair using durable intent and the actual remote revision."""

from dataclasses import replace

from integrations.git import remote_branch_sha
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.ship import PushResult
from integrations.github.tools.ci_fix.storage.attempts import load_prepared_push, repair_key


def resumed_push(
    ctx: CiFixContext,
    workspace: str,
    *,
    github_token: str | None,
) -> tuple[CiFixContext, PushResult] | None:
    """Resume verification only when the remote still contains the recorded repair commit."""
    target = str(ctx.number) if not ctx.is_branch_target else ctx.target_branch
    prepared = load_prepared_push(repair_key(ctx.owner, ctx.repo, target))
    if prepared is None or prepared.checks_state in {"failed", "conflicted", "superseded"}:
        return None
    if ctx.is_branch_target and ctx.head_sha != prepared.source_head_sha:
        return None
    if not ctx.is_branch_target and ctx.head_sha != prepared.fix_head_sha:
        return None
    observed = remote_branch_sha(workspace, prepared.branch_name, token=github_token)
    if observed != prepared.fix_head_sha:
        return None
    restored = replace(ctx, head_sha=prepared.source_head_sha, head_branch=prepared.branch_name)
    return restored, PushResult(prepared.branch_name, prepared.fix_head_sha, prepared.changed_files)
