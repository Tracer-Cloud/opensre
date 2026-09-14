"""Lifecycle for resolving the conflicts of a git merge with a coding agent.

Snapshot the stopped merge, hand the conflicted files to the configured coding
agent (via the neutral ``integrations/coding_agent`` seam), verify that no
marker or unmerged path remains, show each hunk side by side, then, once the
shell's approval policy allows it, stage and commit the merge and push the
branch to the one it tracks. Files the agent could not settle are reported and
the merge is left in progress, so the user decides them; nothing is aborted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from rich.markup import escape

from integrations.coding_agent import (
    CodingResult,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
    run_coding_task,
    verify_coding_agent,
)
from integrations.git import (
    GitCommandError,
    MergeConflicts,
    changed_paths,
    compare_hunks,
    conclude_merge,
    conflict_resolution_task,
    current_branch,
    describe_resolutions,
    ensure_git_repo,
    file_fingerprints,
    head_sha,
    is_git_repo,
    merge_committed_by_resolver,
    merge_conflicts,
    merge_head_name,
    merge_head_sha,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    push_head_to_upstream,
    take_side,
    unresolved_conflicts,
    upstream_branch,
)
from integrations.github import CHECKS_NOT_WATCHED, ChecksOutcome, watch_pull_request_checks
from tools.cross_vendor.resolve_merge_conflicts.comparison import PENDING, render_comparison
from tools.cross_vendor.resolve_merge_conflicts.errors import (
    ERR_AWAITING_DECISIONS,
    ERR_CANCELLED,
    ERR_CLI_UNAVAILABLE,
    ERR_CONFIRMATION_DENIED,
    ERR_CONFLICTS_REMAIN,
    ERR_EXECUTION,
    ERR_MERGE_ABANDONED,
    ERR_NO_MERGE_IN_PROGRESS,
    ERR_TIMEOUT,
    ResolveMergeError,
)

SOURCE: Final = "git"

# Asked to allow one action; True means the shell's policy (or the user) approved it.
Approve = Callable[[str], bool]
# True once the user pressed ESC; checked before every step that changes the branch.
Cancelled = Callable[[], bool]

KEEP_OURS: Final = "ours"
TAKE_THEIRS: Final = "theirs"
COMBINE: Final = "combine"
_SIDES_AND_COMBINE: Final = (KEEP_OURS, TAKE_THEIRS, COMBINE)


@dataclass(frozen=True)
class FileChoice:
    """The question the tool asks for one conflicted file, with what each side holds."""

    path: str
    ours: str
    theirs: str
    ours_summary: str
    theirs_summary: str

    @property
    def title(self) -> str:
        return f"Resolve {self.path}"

    @property
    def options(self) -> tuple[str, str, str]:
        return (
            f"Keep ours ({self.ours}): {self.ours_summary}",
            f"Take theirs ({self.theirs}): {self.theirs_summary}",
            "Combine both with the coding agent",
        )


# Shows the per-file menu; True when it was queued and the turn must end to await the answer.
Ask = Callable[[list[FileChoice]], bool]

ALL_FILES_TITLE: Final = "Resolve all conflicted files"
ALL_FILES_OPTIONS: Final = (
    "Combine all with the coding agent",
    "Keep ours for all",
    "Take theirs for all",
    "Decide file by file",
)
DECIDE_EACH: Final = "each"

AWAITING_INSTRUCTION: Final = (
    "The per-file menu opens after this turn ends. End the turn now without a user-facing "
    "sentence; do NOT repeat the options as text. The user's choices arrive as the next "
    "user message; then call resolve_merge_conflicts again with no arguments (the answers "
    "are read automatically)."
)


def resolve_merge(
    workspace: str | None,
    *,
    ref: str | None,
    model: str | None,
    instructions: str | None,
    decisions: Mapping[str, str] | None = None,
    console: Any = None,
    approve: Approve | None = None,
    wait_for_checks: bool = True,
    cancelled: Cancelled | None = None,
    ask: Ask | None = None,
) -> dict[str, Any]:
    """Resolve the merge in *workspace*, then commit and push it once *approve* allows.

    The conflicts are shown side by side first, then the coding agent resolves
    them, the way Claude Code or Cursor would. *decisions* maps a conflicted
    path to ``ours``, ``theirs``, ``combine`` or free text for the coding
    agent, and ``"*": "each"`` asks the user per file through *ask* instead.
    Files the agent cannot settle are asked through *ask* as well. Without
    *approve* (no shell policy to consult, as in an unattended run) the commit
    and push proceed. After the push, *wait_for_checks* waits for the pull
    request's checks.
    """
    ws = workspace or coding_workspace()
    try:
        return _resolve(
            ws,
            ref=ref,
            model=model,
            instructions=instructions,
            decisions=decisions or {},
            console=console,
            approve=approve,
            wait_for_checks=wait_for_checks,
            cancelled=cancelled,
            ask=ask,
        )
    except ResolveMergeError as exc:
        rendered = _paint(console, ws, exc.conflicts)
        return _output(
            ws,
            success=False,
            error_kind=exc.kind,
            error=exc.message,
            unresolved=exc.unresolved,
            summary=exc.summary,
            rendered=rendered or exc.rendered,
            questions=_questions(ws, exc.conflicts, exc.unresolved),
            awaiting=exc.kind == ERR_AWAITING_DECISIONS,
        )


def _resolve(
    ws: str,
    *,
    ref: str | None,
    model: str | None,
    instructions: str | None,
    decisions: Mapping[str, str],
    console: Any,
    approve: Approve | None,
    wait_for_checks: bool,
    cancelled: Cancelled | None,
    ask: Ask | None,
) -> dict[str, Any]:
    finish = _Finish(
        console=console,
        approve=approve,
        wait_for_checks=wait_for_checks,
        cancelled=cancelled or _never,
    )
    try:
        ensure_git_repo(ws)
        branch = current_branch(ws) or "HEAD"
        already_merging = merge_in_progress(ws)
        if not already_merging:
            if not ref:
                raise ResolveMergeError(
                    ERR_NO_MERGE_IN_PROGRESS,
                    f"No merge is in progress in {ws}. Name the branch or commit to merge "
                    f"into {branch} and it will be merged first.",
                )
            if finish.cancelled():
                raise ResolveMergeError(
                    ERR_CANCELLED,
                    f"Stopped before the merge of {ref} into {branch}. "
                    "Nothing was committed or pushed.",
                )
            clean = merge_ref(ws, ref, message=f"Merge {ref} into {branch}", commit=False)
            if clean and not merge_in_progress(ws):
                return _output(
                    ws,
                    branch=branch,
                    merged=str(ref),
                    commit_sha=head_sha(ws),
                    summary=f"{branch} already contains {ref}; nothing to merge or push.",
                )
        theirs = merge_head_name(ws) if already_merging else str(ref)
        merging = merge_head_sha(ws)
        conflicts = merge_conflicts(ws, ours=branch, theirs=theirs)
        baseline = file_fingerprints(ws, changed_paths(ws))
        open_paths = set(paths_with_conflict_markers(ws, conflicts.names)) | {
            c.path for c in unresolved_conflicts(ws, conflicts)
        }
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message) from exc

    if not open_paths:
        summary = (
            "The conflicted files were already edited in the working tree."
            if conflicts.paths
            else ""
        )
        if instructions and not conflicts.paths:
            summary = (
                "Nothing was left to resolve, so the instructions were not applied; "
                "to change how a file was resolved earlier, ask for that edit."
            )
        return _commit(ws, conflicts, baseline, summary=summary, finish=finish)

    shown = _paint(console, ws, conflicts, pending_label=PENDING)
    plan = _decision_plan(conflicts, decisions)
    undecided = [path for path in conflicts.names if path in open_paths and path not in plan]
    per_file = normalize_decision(decisions.get("*", "")) == DECIDE_EACH
    if undecided and per_file and ask is not None and ask(_choices(ws, conflicts, undecided)):
        raise ResolveMergeError(
            ERR_AWAITING_DECISIONS,
            f"Waiting for the user's choice on {len(undecided)} file(s): "
            f"{', '.join(undecided)}. The merge stays in progress in {ws}.",
            unresolved=tuple(undecided),
            rendered=shown,
        )
    for path in undecided:
        plan[path] = COMBINE
    plan = {
        path: choice
        for path, choice in plan.items()
        if path in open_paths or choice in (KEEP_OURS, TAKE_THEIRS)
    }
    try:
        for path, choice in plan.items():
            if choice in (KEEP_OURS, TAKE_THEIRS):
                take_side(ws, path, choice)
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message, rendered=shown) from exc
    to_agent = [path for path, choice in plan.items() if choice not in (KEEP_OURS, TAKE_THEIRS)]
    notes = [
        f"{path}: {choice}" for path, choice in plan.items() if choice not in _SIDES_AND_COMBINE
    ]
    if to_agent:
        agent_conflicts = replace(
            conflicts, paths=tuple(c for c in conflicts.paths if c.path in to_agent)
        )
        result = _run_agent(
            agent_conflicts,
            ws,
            model=model,
            merged_ref=theirs,
            instructions="\n".join(part for part in (instructions or "", *notes) if part),
            console=console,
        )
    else:
        result = CodingResult(success=True, summary=_choices_summary(plan))
    try:
        if not merge_in_progress(ws):
            return _merge_finished_by_agent(ws, conflicts, merging, result, finish=finish)
        remaining = _unresolved(ws, conflicts)
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message, conflicts=conflicts) from exc
    if remaining and result.success and ask is not None:
        left_paths = [path for path, _why in remaining]
        if ask(_choices(ws, conflicts, left_paths)):
            raise ResolveMergeError(
                ERR_AWAITING_DECISIONS,
                f"The coding agent could not settle {', '.join(left_paths)}; waiting for the "
                f"user's choice. The merge stays in progress in {ws}.",
                unresolved=tuple(left_paths),
                summary=result.summary,
                rendered=_paint(console, ws, conflicts, pending_label=PENDING),
            )
    if not result.success:
        left = tuple(path for path, _why in remaining) or conflicts.names
        raise ResolveMergeError(
            ERR_TIMEOUT if result.timed_out else ERR_EXECUTION,
            f"The coding agent did not finish: {result.error or 'no detail'}. "
            f"Conflicts remain in {', '.join(left)}; the merge stays in progress in {ws}.",
            unresolved=left,
            summary=result.summary,
            conflicts=conflicts,
        )
    if remaining:
        raise ResolveMergeError(
            ERR_CONFLICTS_REMAIN,
            f"{len(remaining)} file(s) still need a person's decision: "
            f"{'; '.join(f'{path} ({why})' for path, why in remaining)}. "
            f"The merge stays in progress in {ws}; ask the user how to settle them "
            "and run the resolution again with their decisions.",
            unresolved=tuple(path for path, _why in remaining),
            summary=result.summary,
            conflicts=conflicts,
        )
    return _commit(ws, conflicts, baseline, summary=result.summary, finish=finish)


def _decision_plan(conflicts: MergeConflicts, decisions: Mapping[str, str]) -> dict[str, str]:
    """Normalize the given decisions to ``ours``/``theirs``/``combine``/free text per path.

    A decision under ``*`` (the answer to the all-files question) applies to
    every file that has no decision of its own; "decide file by file" leaves
    them undecided so the per-file menu follows.
    """
    plan = {
        path: normalize_decision(raw)
        for path, raw in decisions.items()
        if path in conflicts.names and raw.strip()
    }
    for_all = normalize_decision(decisions.get("*", ""))
    if for_all and for_all != DECIDE_EACH:
        for path in conflicts.names:
            plan.setdefault(path, for_all)
    return plan


def normalize_decision(raw: str) -> str:
    """Map a menu label or a word to a side; anything else is free text for the agent."""
    text = raw.strip()
    lowered = text.casefold()
    if lowered in (KEEP_OURS, "keep", "mine") or lowered.startswith("keep ours"):
        return KEEP_OURS
    if lowered in (TAKE_THEIRS, "take", "main") or lowered.startswith("take theirs"):
        return TAKE_THEIRS
    if lowered in (COMBINE, "agent", "both") or lowered.startswith("combine"):
        return COMBINE
    if lowered.startswith("decide file by file"):
        return DECIDE_EACH
    return text


def _choices(ws: str, conflicts: MergeConflicts, paths: list[str]) -> list[FileChoice]:
    comparisons = compare_hunks(ws, conflicts)
    choices: list[FileChoice] = []
    for path in paths:
        hunks = [c for c in comparisons if c.path == path]
        choices.append(
            FileChoice(
                path=path,
                ours=conflicts.ours,
                theirs=conflicts.theirs,
                ours_summary=_hunk_summary([line for h in hunks for line in h.ours]),
                theirs_summary=_hunk_summary([line for h in hunks for line in h.theirs]),
            )
        )
    return choices


def _choices_summary(plan: Mapping[str, str]) -> str:
    words = {KEEP_OURS: "kept ours", TAKE_THEIRS: "took theirs"}
    return "; ".join(f"{path}: {words.get(choice, choice)}" for path, choice in plan.items())


def _run_agent(
    conflicts: MergeConflicts,
    ws: str,
    *,
    model: str | None,
    merged_ref: str,
    instructions: str | None,
    console: Any = None,
) -> CodingResult:
    ready, detail = verify_coding_agent()
    if not ready:
        raise ResolveMergeError(
            ERR_CLI_UNAVAILABLE,
            f"Conflicts in {', '.join(conflicts.names)} need a coding agent, but none is "
            f"ready: {detail}. The merge stays in progress in {ws}.",
            unresolved=conflicts.names,
        )
    context = (f"Instructions from the user: {instructions.strip()}",) if instructions else ()
    task = conflict_resolution_task(conflicts, merged_ref=merged_ref, context_lines=context)
    return run_coding_task(
        task,
        workspace=ws,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
        on_progress=_progress_printer(console),
    )


def _progress_printer(console: Any) -> Callable[[str], None] | None:
    """Print each step the coding agent takes as a dim line under the running tool."""
    if console is None:
        return None

    def show(step: str) -> None:
        console.print(f"[dim]  {escape(step)}[/]")

    return show


def _unresolved(ws: str, conflicts: MergeConflicts) -> list[tuple[str, str]]:
    return [(c.path, c.description) for c in unresolved_conflicts(ws, conflicts)]


def _merge_finished_by_agent(
    ws: str, conflicts: MergeConflicts, merging: str, result: CodingResult, *, finish: _Finish
) -> dict[str, Any]:
    """A merge the coding agent committed itself is still approved, pushed and watched."""
    if not (merging and merge_committed_by_resolver(ws, conflicts, merging)):
        raise ResolveMergeError(
            ERR_MERGE_ABANDONED,
            f"The coding agent abandoned the merge of {conflicts.theirs} into {conflicts.ours}; "
            "no merge is in progress any more. Start the merge again.",
            summary=result.summary,
        )
    sha = head_sha(ws)
    rendered = _paint(finish.console, ws, conflicts)

    def done(**extra: Any) -> dict[str, Any]:
        return _output(
            ws,
            branch=conflicts.ours,
            merged=conflicts.theirs,
            commit_sha=sha,
            resolved=conflicts.names,
            resolutions=_resolutions(ws, sha, conflicts),
            summary=result.summary,
            rendered=rendered,
            **extra,
        )

    if finish.cancelled():
        return done(error_kind=ERR_CANCELLED, error="Stopped before the push.")
    target = _push_target(ws, conflicts.ours)
    action = (
        f"push the merge of {conflicts.theirs} into {conflicts.ours}, committed by the coding "
        f"agent as {sha[:12]}, to {target}"
        f"{' and wait for the pull request checks' if finish.wait_for_checks else ''}"
    )
    if finish.approve is not None and not finish.approve(action):
        return done(error_kind=ERR_CONFIRMATION_DENIED, error=f"Not approved: {action}.")
    if finish.cancelled():
        return done(error_kind=ERR_CANCELLED, error="Stopped before the push.")
    pushed_to, push_error, checks = _push_and_watch(ws, sha, finish)
    return done(
        pushed_to=pushed_to,
        checks=checks,
        error_kind=_error_kind(push_error, checks),
        error=push_error.message if push_error else _checks_error(checks),
    )


@dataclass(frozen=True)
class _Finish:
    """How the run ends once the files are resolved: show, approve, commit, push, watch."""

    console: Any
    approve: Approve | None
    wait_for_checks: bool
    cancelled: Cancelled


def _never() -> bool:
    return False


def _stop_if_cancelled(
    finish: _Finish, ws: str, *, before: str, summary: str, rendered: bool
) -> None:
    if finish.cancelled():
        raise ResolveMergeError(
            ERR_CANCELLED,
            f"Stopped before the {before}. The resolved files are in the working tree, "
            f"unstaged; the merge stays in progress in {ws} and nothing was committed or pushed.",
            summary=summary,
            rendered=rendered,
        )


def _commit(
    ws: str,
    conflicts: MergeConflicts,
    baseline: dict[str, str],
    *,
    summary: str,
    finish: _Finish,
) -> dict[str, Any]:
    """Show the resolution, ask to commit and push, then do both and watch the checks."""
    rendered = _paint(finish.console, ws, conflicts)
    _stop_if_cancelled(finish, ws, before="commit", summary=summary, rendered=rendered)
    target = _push_target(ws, conflicts.ours)
    action = (
        f"commit the merge of {conflicts.theirs} into {conflicts.ours}, push it to {target}"
        f"{' and wait for the pull request checks' if finish.wait_for_checks else ''}"
    )
    if finish.approve is not None and not finish.approve(action):
        raise ResolveMergeError(
            ERR_CONFIRMATION_DENIED,
            f"Not approved: {action}. The resolved files are in the working tree, unstaged; "
            f"the merge stays in progress in {ws} and nothing was committed or pushed.",
            summary=summary,
            rendered=rendered,
        )
    _stop_if_cancelled(finish, ws, before="commit", summary=summary, rendered=rendered)
    try:
        sha = conclude_merge(ws, conflicts, baseline=baseline)
    except GitCommandError as exc:
        raise ResolveMergeError(
            exc.kind,
            f"{exc.message} The merge stays in progress in {ws}.",
            summary=summary,
            rendered=rendered,
        ) from exc
    if finish.cancelled():
        return _cancelled_before_push(
            ws,
            conflicts.ours,
            conflicts.theirs,
            sha,
            resolved=conflicts.names,
            resolutions=_resolutions(ws, sha, conflicts),
            summary=summary,
            rendered=rendered,
        )
    pushed_to, push_error, checks = _push_and_watch(ws, sha, finish)
    return _output(
        ws,
        branch=conflicts.ours,
        merged=conflicts.theirs,
        commit_sha=sha,
        resolved=conflicts.names,
        resolutions=_resolutions(ws, sha, conflicts),
        summary=summary,
        rendered=rendered,
        pushed_to=pushed_to,
        checks=checks,
        error_kind=_error_kind(push_error, checks),
        error=push_error.message if push_error else _checks_error(checks),
    )


def _cancelled_before_push(
    ws: str,
    branch: str,
    merged: str,
    sha: str,
    *,
    resolved: tuple[str, ...] = (),
    resolutions: tuple[str, ...] = (),
    summary: str = "",
    rendered: bool = False,
) -> dict[str, Any]:
    """The merge commit exists locally; ESC arrived before the remote was updated."""
    return _output(
        ws,
        branch=branch,
        merged=merged,
        commit_sha=sha,
        resolved=resolved,
        resolutions=resolutions,
        summary=summary,
        rendered=rendered,
        error_kind=ERR_CANCELLED,
        error="Stopped before the push.",
    )


def _push_and_watch(
    ws: str, sha: str, finish: _Finish
) -> tuple[str, GitCommandError | None, ChecksOutcome | None]:
    pushed_to, push_error = _push(ws)
    if not pushed_to or not finish.wait_for_checks:
        return pushed_to, push_error, None
    return pushed_to, None, watch_pull_request_checks(ws, pushed_to=pushed_to, commit_sha=sha)


def _error_kind(push_error: GitCommandError | None, checks: ChecksOutcome | None) -> str | None:
    if push_error is not None:
        return push_error.kind
    if checks is None or checks.passed or checks.state == CHECKS_NOT_WATCHED:
        return None
    return f"checks_{checks.state}"


def _checks_error(checks: ChecksOutcome | None) -> str | None:
    if checks is None or checks.passed or checks.state == CHECKS_NOT_WATCHED:
        return None
    return f"The pull request checks did not pass: {checks.detail}."


def _push(ws: str) -> tuple[str, GitCommandError | None]:
    try:
        return push_head_to_upstream(ws), None
    except GitCommandError as exc:
        return "", exc


def _push_target(ws: str, branch: str) -> str:
    try:
        return upstream_branch(ws) or f"origin/{branch}"
    except GitCommandError:
        return f"origin/{branch}"


def _resolutions(ws: str, sha: str, conflicts: MergeConflicts) -> tuple[str, ...]:
    return tuple(str(r) for r in describe_resolutions(ws, sha, conflicts))


_HUNK_SUMMARY_CHARS = 90


def _questions(
    ws: str, conflicts: MergeConflicts | None, unresolved: tuple[str, ...]
) -> list[dict[str, Any]]:
    """One ready-to-ask question per unresolved file, showing what each side holds."""
    if conflicts is None or not unresolved:
        return []
    questions: list[dict[str, Any]] = []
    for path in unresolved:
        hunks = [c for c in compare_hunks(ws, conflicts) if c.path == path and c.result is None]
        if not hunks:
            continue
        ours = _hunk_summary([line for hunk in hunks for line in hunk.ours])
        theirs = _hunk_summary([line for hunk in hunks for line in hunk.theirs])
        questions.append(
            {
                "file": path,
                "question": f"How should {path} be resolved?",
                "options": [
                    f"Keep {conflicts.ours}: {ours}",
                    f"Take {conflicts.theirs}: {theirs}",
                    "Combine both sides (say how)",
                ],
                "hunks": [{"ours": list(hunk.ours), "theirs": list(hunk.theirs)} for hunk in hunks],
            }
        )
    return questions


def _hunk_summary(lines: list[str]) -> str:
    text = " | ".join(line.strip() for line in lines if line.strip()) or "(empty)"
    if len(text) > _HUNK_SUMMARY_CHARS:
        return text[: _HUNK_SUMMARY_CHARS - 1] + "…"
    return text


def _paint(
    console: Any, ws: str, conflicts: MergeConflicts | None, *, pending_label: str | None = None
) -> bool:
    """Draw the side-by-side hunk comparison when a terminal console is available."""
    if console is None or conflicts is None or not conflicts.paths:
        return False
    comparisons = compare_hunks(ws, conflicts)
    if pending_label is not None:
        render_comparison(
            console,
            comparisons,
            ours=conflicts.ours,
            theirs=conflicts.theirs,
            pending_label=pending_label,
        )
    else:
        render_comparison(console, comparisons, ours=conflicts.ours, theirs=conflicts.theirs)
    return True


def _output(
    ws: str,
    *,
    success: bool = True,
    branch: str = "",
    merged: str = "",
    commit_sha: str | None = None,
    resolved: tuple[str, ...] = (),
    resolutions: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
    summary: str = "",
    error_kind: str | None = None,
    error: str | None = None,
    rendered: bool = False,
    pushed_to: str = "",
    checks: ChecksOutcome | None = None,
    questions: list[dict[str, Any]] | None = None,
    awaiting: bool = False,
) -> dict[str, Any]:
    committed = success and bool(commit_sha)
    # A set error kind means the requested operation did not complete, even when the
    # merge commit exists (cancelled, not approved, push failed, checks failed).
    success = success and error_kind is None
    return {
        "outcome": _outcome(
            committed,
            branch=branch,
            merged=merged,
            commit_sha=commit_sha,
            pushed_to=pushed_to,
            checks=checks,
            error=error,
        ),
        "resolutions": list(resolutions),
        "next_step": _next_step(
            committed,
            branch=branch,
            commit_sha=commit_sha,
            pushed_to=pushed_to,
            checks=checks,
            unresolved=unresolved,
            awaiting=awaiting,
        ),
        "success": success,
        "error_kind": error_kind,
        "error": error,
        "workspace": ws,
        "branch": branch,
        "merged": merged,
        "commit_sha": commit_sha,
        "pushed": bool(pushed_to),
        "pushed_to": pushed_to,
        "pull_request_url": checks.pr_url if checks else "",
        "checks_state": checks.state if checks else "",
        "checks_detail": checks.detail if checks else "",
        "failing_checks": list(checks.failing_checks) if checks else [],
        "resolved_files": list(resolved),
        "unresolved_files": list(unresolved),
        "questions": [] if awaiting else (questions or []),
        "menu": "queued" if awaiting else "",
        "instruction": AWAITING_INSTRUCTION if awaiting else "",
        "coding_agent_summary": summary,
        "merge_in_progress": _merge_still_in_progress(ws),
        "rendered_in_shell": rendered,
    }


def _outcome(
    committed: bool,
    *,
    branch: str,
    merged: str,
    commit_sha: str | None,
    pushed_to: str,
    checks: ChecksOutcome | None,
    error: str | None,
) -> str:
    """One sentence the caller can repeat verbatim; it outranks the coding agent's own account."""
    if committed and commit_sha and pushed_to:
        head = (
            f"OpenSRE committed the merge of {merged} into {branch} as {commit_sha[:12]} and "
            f"pushed it to {pushed_to}"
        )
        if checks is None:
            return f"{head}; the pull request is updated."
        if checks.passed:
            return f"{head}; {checks.detail} ({checks.pr_url})."
        if checks.state == CHECKS_NOT_WATCHED:
            return f"{head}; the checks were not watched because {checks.detail}."
        return f"{head}, but {checks.detail} ({checks.pr_url})."
    if committed and commit_sha:
        detail = f": {error}" if error else ""
        return (
            f"OpenSRE committed the merge of {merged} into {branch} locally as {commit_sha[:12]} "
            f"but did not push it{detail}. The remote and any pull request still show the "
            "conflict."
        )
    return error or "The merge was not committed."


def _next_step(
    committed: bool,
    *,
    branch: str,
    commit_sha: str | None,
    pushed_to: str,
    checks: ChecksOutcome | None,
    unresolved: tuple[str, ...],
    awaiting: bool = False,
) -> str:
    """What the user does now; the caller should suggest it."""
    if awaiting:
        return AWAITING_INSTRUCTION
    if committed and commit_sha and pushed_to:
        if checks is None or checks.state == CHECKS_NOT_WATCHED:
            return "Watch the pull request's checks on the pushed commit."
        if checks.passed:
            return "The pull request is green; it is ready for review or merge."
        if checks.state == "failed":
            return "Open the failing checks on the pull request, or ask OpenSRE to fix its CI."
        return "Open the pull request and check its latest commit and checks."
    if committed and commit_sha:
        return (
            f"Review the merge with `git show {commit_sha[:12]}`, then push {branch} to update "
            "the pull request."
        )
    if unresolved:
        return (
            "Ask the user each entry of `questions` with ask_user_choice, exactly as given, "
            "then run the resolution again with their answers in `instructions`."
        )
    return (
        "Review the resolved files; to finish, ask again to commit and push the merge, or say "
        "what should change."
    )


def _merge_still_in_progress(ws: str) -> bool:
    try:
        return is_git_repo(ws) and merge_in_progress(ws)
    except GitCommandError:
        return False


__all__ = ["SOURCE", "resolve_merge"]
