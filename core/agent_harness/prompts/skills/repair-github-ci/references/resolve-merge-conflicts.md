# Resolving merge conflicts

A pull request that conflicts with its base branch gets no checks: GitHub
reports "This branch has conflicts that must be resolved" and the CI never
starts. Bringing the base branch into the PR branch is then the fix.

## Which tool

- Inside a CI repair, `fix_github_pr_ci` merges the base branch itself and
  resolves conflicts before it edits anything; do not add a separate step.
- When the user is in a checkout and asks to resolve, fix, or finish a merge,
  or to commit and push a resolved merge, call `resolve_merge_conflicts`. It
  works on the merge already in progress in the current directory, or pass
  `ref` (for example `origin/main`) to merge that branch first.
- Pass the user's decisions in `instructions` ("keep our version of
  config.py"); they reach the coding agent word for word.

Never run `git merge`, `git add`, `git commit`, or `git push` through
`shell_run` for this; the shell refuses them while a merge is in progress.
Never resolve conflict markers with `code_implement`.

## What the tool does

1. Shows each conflict hunk side by side in the shell at once: our side,
   their side, and "(to decide)". `rendered_in_shell` is true when this
   happened, so do not repeat the file contents.
2. Lets the coding agent resolve every file, the way Claude Code or Cursor
   would, without asking first. It asks through the shell menu only for a
   file the agent could not settle (keep ours, take theirs, combine, or free
   text), or when the user asked to decide file by file (pass
   `decisions={"*": "each"}`). When the result says `menu: queued`, end the
   turn; the answers arrive as the next user message and the next call with
   no arguments reads them itself. Pass per-file `decisions` only for choices
   the user already stated in words. Never ask a question of your own about
   how to resolve a file: the tool asks.
3. Applies "keep ours" and "take theirs" with git, and sends only the files
   to combine to the coding agent. Lockfiles are regenerated from the resolved
   manifest, never merged by hand. Then it verifies that no conflict marker or
   unmerged path remains and shows the result table.
4. Asks the shell's approval policy to commit the merge and push the branch to
   the one it tracks. At `/auto high` (the default) it proceeds; at lower
   levels the user is asked first; an unattended run proceeds. The remote
   branch is never a protected base branch.
5. Waits for the checks of the open pull request whose head is that branch
   and reports `checks_state` (`passed`, `failed`, `timed_out`, `superseded`,
   `conflicted`, or `not_watched`). Pass `wait_for_checks=false` only when the
   user asks not to wait.

## How to reply

- Repeat `outcome` as the first line. It says whether the merge was committed
  and pushed, and it outranks `coding_agent_summary`, which the agent wrote
  before the commit.
- List `resolutions` (one line per file: kept ours, took theirs, combined
  both) so the user sees what was decided.
- End with `next_step`.
- When `error_kind` is `conflicts_remain`, ask each entry of `questions` with
  `ask_user_choice`, using its `question` and `options` exactly as given (they
  show what each side contains), then call the tool again with the answers in
  `instructions`, naming the file and the chosen side. Never invent a
  different question or answer it yourself. The merge stays in progress; do
  not abort it.
- When `error_kind` is `cancelled`, the user pressed ESC: say what was and was
  not done and wait for their next instruction.
- When `error_kind` is `confirmation_denied`, say the resolved files are in the
  working tree uncommitted and ask what should change.
- When `error_kind` is `push_failed`, the merge is committed locally; give the
  push command from `next_step`.
- When `error_kind` starts with `checks_`, the merge is pushed; name
  `failing_checks` and offer to fix the pull request's CI.
- When `checks_state` is `not_watched`, the merge is complete: say why the
  checks were not watched (from `checks_detail`) and finish. Do not mark a
  plan step blocked or ask the user about verification.
