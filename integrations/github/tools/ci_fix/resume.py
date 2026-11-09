"""Recover a pushed repair using durable intent and the actual remote revision."""

from dataclasses import replace

from integrations.git import remote_branch_sha
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.ship import PushResult
from integrations.github.tools.ci_fix.storage.attempts import load_prepared_push, repair_key
from integrations.github.tools.ci_fix.verification import CheckState

#: Verification never finished for these records; every settled state is final.
_RESUMABLE_STATES = frozenset({"", CheckState.TIMED_OUT.value})


def resumed_push(
    ctx: CiFixContext,
    workspace: str,
    *,
    github_token: str | None,
) -> tuple[CiFixContext, PushResult] | None:
    """Resume verification only when the remote still contains the recorded repair commit.

    A record whose checks already settled (passed, failed, conflicted,
    superseded) is never resumed; a later invocation must judge the PR afresh.
    """
    target = str(ctx.number) if not ctx.is_branch_target else ctx.target_branch
    prepared = load_prepared_push(repair_key(ctx.owner, ctx.repo, target))
    if prepared is None or prepared.checks_state not in _RESUMABLE_STATES:
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
